"""Highlight reels: one video per half, and/or one for the whole match.

Two ways to build a reel, chosen automatically:

- **Copy** (fast, no quality loss): each clip is cut by stream copy, then the
  clips are joined by stream copy. Only possible when every clip in the reel
  comes from footage with the same encoding (same camera settings).
- **Encode**: needed for text screens (a black screen with white text before
  a clip is new video), or when halves were filmed with different settings.
  Every piece (text screen or clip) is encoded with one shared ``Look``, so
  the pieces then join by stream copy without glitches.

Pieces are made once and shared: the whole-match video reuses the halves'
pieces. They live in a work folder inside the output folder so an
interrupted run resumes, and the folder is removed when everything succeeds.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

from hclip import ffmpeg, naming
from hclip.cut import HalfVideo, Job
from hclip.notes import Note

WORK_DIR = ".gaa-clips-work"
DEFAULT_CARD_SECONDS = 3.0


@dataclass
class Reel:
    name: str                  # "1st Half", "Whole match"
    jobs: list[Job]
    out: Path

    @property
    def duration(self) -> float:
        return sum(j.duration for j in self.jobs)


def reel_filename(title: str, name: str) -> str:
    return safe_name(f"{title.strip() or 'Match'} - {name}") + ".mp4"


def safe_name(name: str) -> str:
    """Keep the coach's wording but drop characters Windows forbids."""
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in bad or ord(ch) < 32 else ch for ch in name).strip(" .")
    return cleaned or naming.folder_name(name)


def plan_reels(note: Note, jobs: Sequence[Job], out_dir: Path, title: str, *,
               halves: bool = True, whole: bool = False) -> list[Reel]:
    reels: list[Reel] = []
    if halves:
        used: dict[str, int] = {}
        for s in note.sections:
            mine = [j for j in jobs if j.section_tag == s.tag]
            if not mine:
                continue
            # Two sections called "2nd half" mustn't overwrite each other.
            n = used[s.name.lower()] = used.get(s.name.lower(), 0) + 1
            name = s.name if n == 1 else f"{s.name} ({n})"
            reels.append(Reel(name, mine, out_dir / reel_filename(title, name)))
    if whole and jobs:
        reels.append(Reel("Whole match", list(jobs), out_dir / reel_filename(title, "Whole match")))
    return reels


def can_copy(jobs: Sequence[Job]) -> bool:
    """True if these clips can be joined without re-encoding."""
    keys = {j.half.info.join_key() if j.half.info else None for j in jobs}
    return len(keys) == 1 and None not in keys


def look_for(jobs: Sequence[Job], preset: str = "veryfast",
             max_height: int | None = None) -> ffmpeg.Look:
    """Encode to the first clip's picture size and frame rate."""
    info = jobs[0].half.info
    if info is None:
        return ffmpeg.Look(1280, 720, "25", preset)
    return ffmpeg.Look.for_source(info, max_height=max_height, preset=preset)


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ReelProgress:
    stage: str              # what's happening, for the coach
    overall: float          # 0..1
    elapsed: float
    remaining: float | None


@dataclass(frozen=True)
class ReelResult:
    reel: Reel
    status: str             # "made" | "skipped" | "failed"
    error: str = ""
    missing: tuple[int, ...] = ()          # note lines of clips left out (failed)
    missing_screens: tuple[int, ...] = ()  # lines whose text screen failed (clip kept)


@dataclass
class _Piece:
    path: Path
    cmd: list[str]
    seconds: float
    kind: str                  # "clip" | "card"
    job: Job
    done: bool = False
    error: str = ""


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _locked_message(path: Path, e: OSError) -> str:
    return (f"Couldn't write {path.name}. If it's open in a video player or another "
            f"program, close it and try again. ({e.strerror or e})")


@dataclass
class _Build:
    reel: Reel
    pieces: list[_Piece] = field(default_factory=list)


def _sig(*parts: object) -> str:
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:10]


def run_reels(
    reels: Sequence[Reel],
    *,
    card_images: Mapping[int, Path] | None = None,
    card_seconds: float = DEFAULT_CARD_SECONDS,
    precise: bool = False,
    keep_clips_dir: Path | None = None,
    force: bool = False,
    preset: str = "veryfast",
    on_progress: Callable[[ReelProgress], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[ReelResult]:
    """Build every reel. ``card_images`` maps a clip's note line to the PNG
    of the text screen shown before it. Reels that already exist are skipped
    unless ``force``; finished pieces from an interrupted run are always
    reused (they're named by their content)."""
    ff = ffmpeg.find_binary("ffmpeg")
    card_images = card_images or {}
    results: list[ReelResult] = []
    todo = [r for r in reels if force or not r.out.exists()]
    results += [ReelResult(r, "skipped") for r in reels if r not in todo]
    if not todo:
        return results

    work = todo[0].out.parent / WORK_DIR
    work.mkdir(parents=True, exist_ok=True)
    all_jobs = list({id(j): j for r in todo for j in r.jobs}.values())
    # Text screens need every reel re-encoded to one look. Without them each
    # reel is copied if its own clips allow it (a half filmed differently
    # from the other half doesn't slow down that half's reel).
    shared_look = look_for(all_jobs, preset=preset) if card_images else None

    def reel_look(r: Reel) -> ffmpeg.Look | None:
        if shared_look is not None:
            return shared_look
        return None if can_copy(r.jobs) else look_for(r.jobs, preset=preset)

    # One piece per clip (and per text screen), shared between reels. Pieces
    # live in the work folder named by their content, so reuse is always safe.
    pieces: dict[str, _Piece] = {}

    def clip_piece(j: Job, look: ffmpeg.Look | None) -> _Piece:
        if look is not None:
            key = _sig("enc", str(j.half.files), j.start, j.end, look)
            path = work / f"clip_{key}.mp4"
            cmd = ffmpeg.build_encode_clip_cmd(
                ff, j.half.source(), j.start, j.duration, path, look,
                is_concat=j.half.is_concat,
                has_audio=bool(j.half.info and j.half.info.audio_codec))
        else:
            key = _sig("copy", str(j.half.files), j.start, j.end, precise)
            path = work / f"clip_{key}.mp4"
            cmd = ffmpeg.build_cut_cmd(
                ff, j.half.source(), j.start, j.duration, path, precise=precise,
                reencode_audio=j.half.reencode_audio, is_concat=j.half.is_concat,
                hevc=j.half.hevc)
        return pieces.setdefault(key, _Piece(path, cmd, j.duration, "clip", j))

    def card_piece(j: Job, look: ffmpeg.Look | None) -> _Piece | None:
        img = card_images.get(j.clip.line)
        if img is None or look is None:
            return None
        data = img.read_bytes()
        key = _sig("card", data, card_seconds, look)
        path = work / f"card_{key}.mp4"
        cmd = ffmpeg.build_card_cmd(ff, img, card_seconds, path, look)
        piece = pieces.setdefault(key, _Piece(path, cmd, card_seconds, "card", j))
        if not data.startswith(_PNG_MAGIC):
            piece.error = f"The text screen for line {j.clip.line} isn't a readable image."
        return piece

    builds: list[_Build] = []
    for r in todo:
        b = _Build(r)
        look = reel_look(r)
        for j in r.jobs:
            c = card_piece(j, look)
            if c:
                b.pieces.append(c)
            b.pieces.append(clip_piece(j, look))
        builds.append(b)

    # Work out the total so progress and time-left are honest.
    join_weight = 0.03
    total = sum(p.seconds for p in pieces.values() if not p.path.exists()) + \
        sum(r.duration * join_weight for r in todo) or 1.0
    done = 0.0
    started = time.monotonic()

    def report(stage: str, extra: float = 0.0) -> None:
        if not on_progress:
            return
        overall = min(1.0, (done + extra) / total)
        elapsed = time.monotonic() - started
        remaining = elapsed * (1 - overall) / overall if overall > 0.02 else None
        on_progress(ReelProgress(stage, overall, elapsed, remaining))

    def run_step(cmd: list[str], out: Path, seconds: float, weight: float, stage: str) -> None:
        part = out.with_name(out.name + ".part")
        cmd = [*cmd[:-1], str(part)]
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            ffmpeg.run(cmd, lambda s: report(stage, min(s, seconds) * weight), should_stop)
            os.replace(part, out)
        except BaseException:
            part.unlink(missing_ok=True)
            raise

    ordered = list(pieces.values())
    stopped = False
    for n, p in enumerate(ordered, start=1):
        if should_stop and should_stop():
            stopped = True
            break
        if p.path.exists():
            p.done = True
            continue
        if p.error:              # known bad before starting (e.g. unreadable image)
            continue
        stage = f"Making piece {n} of {len(ordered)}"
        try:
            run_step(p.cmd, p.path, p.seconds, 1.0, stage)
            p.done = True
        except ffmpeg.Cancelled:
            stopped = True
            break
        except ffmpeg.FFmpegError as e:
            p.error = str(e)
        except OSError as e:
            p.error = _locked_message(p.path, e)
        done += p.seconds
        report(stage)

    ok = True
    for b in builds:
        if stopped:
            results.append(ReelResult(b.reel, "failed", "Stopped."))
            ok = False
            continue
        usable = [p for p in b.pieces if p.done]
        missing = tuple(sorted({p.job.clip.line for p in b.pieces if p.kind == "clip" and not p.done}))
        screens = tuple(sorted({p.job.clip.line for p in b.pieces if p.kind == "card" and not p.done}))
        why = next((p.error for p in b.pieces if p.error), "")
        if not any(p.kind == "clip" for p in usable):
            results.append(ReelResult(b.reel, "failed", why or "None of its clips could be made.",
                                      missing, screens))
            ok = False
            continue
        listing = work / f"join_{_sig(b.reel.out.name)}.ffconcat"
        ffmpeg.write_concat_list([p.path for p in usable], listing)
        stage = f"Joining {b.reel.name}"
        try:
            run_step(ffmpeg.build_join_cmd(ff, listing, b.reel.out), b.reel.out,
                     b.reel.duration, join_weight, stage)
            results.append(ReelResult(b.reel, "made", why, missing, screens))
            ok &= not (missing or screens)
        except ffmpeg.Cancelled:
            results.append(ReelResult(b.reel, "failed", "Stopped."))
            ok = False
            stopped = True
        except ffmpeg.FFmpegError as e:
            results.append(ReelResult(b.reel, "failed", str(e), missing, screens))
            ok = False
        except OSError as e:
            results.append(ReelResult(b.reel, "failed", _locked_message(b.reel.out, e), missing, screens))
            ok = False
        finally:
            listing.unlink(missing_ok=True)
        done += b.reel.duration * join_weight
        report(stage)

    if keep_clips_dir is not None and not stopped:
        # Also keep each clip (without its text screen), written fresh every
        # run so a changed video or setting never leaves a stale clip.
        keep_clips_dir.mkdir(parents=True, exist_ok=True)
        for p in pieces.values():
            if p.done and p.kind == "clip":
                try:
                    shutil.copy2(p.path, keep_clips_dir / p.job.out.name)
                except OSError:
                    ok = False

    if ok:
        shutil.rmtree(work, ignore_errors=True)
    return results


def piece_errors(results: Sequence[ReelResult]) -> list[int]:
    """Note lines whose clips were left out of any reel."""
    return sorted({line for r in results for line in r.missing})
