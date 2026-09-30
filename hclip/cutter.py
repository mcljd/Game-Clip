"""Cut a checked clip list (hclip/clipplan.py) into reels.

- Every clip is RE-ENCODED (libx264 veryfast CRF 20, AAC 192k, +faststart)
  with the seek before -i, so cuts are frame-accurate. Stream copy would
  snap each start to a keyframe.
- Every piece (clips and text screens) is encoded to ONE shared format (the
  first video's picture size and frame rate, 48 kHz stereo), so the pieces
  can then be joined with the concat demuxer and -c copy. The join still
  checks size, frame rate and audio rate first, and re-encodes the reel if
  they ever differ.
- Work happens in a local temp folder (the output folder may be in
  OneDrive, where writing is very slow), under temporary names renamed only
  when ffmpeg succeeds. Finished files are then moved to the output folder,
  again via a temporary name.
- Each source is checked before each clip, so a moved file fails with its
  name instead of a confusing ffmpeg error.
- After each reel, ffprobe measures every clip and the reel: a clip must be
  its planned length (within 0.1 s) and the reel the sum of its pieces.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

from hclip import clipplan, ffmpeg, timecode

CLIP_TOLERANCE = 0.1       # seconds a clip may differ from its planned length
REEL_TOLERANCE = 0.1       # seconds a reel may differ from the sum of its pieces
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@dataclass
class ReelJob:
    name: str                       # "reel 1", for progress and the results
    file_stem: str                  # "bc clip 1": the reel is <stem>.mp4, clips <stem> - 01.mp4
    cuts: list[clipplan.Cut]


@dataclass
class ClipResult:
    position: int
    video: str                      # the video's label
    range_text: str
    file: str
    expected: float
    measured: float | None = None

    @property
    def ok(self) -> bool:
        return self.measured is not None and abs(self.measured - self.expected) <= CLIP_TOLERANCE


@dataclass
class ReelResult:
    name: str
    file: str
    status: str = "waiting"         # "made" | "failed" | "stopped"
    error: str = ""
    clips: list[ClipResult] = field(default_factory=list)
    expected: float | None = None   # sum of the measured pieces (clips + text screens)
    measured: float | None = None

    @property
    def ok(self) -> bool:
        return (self.status == "made" and all(c.ok for c in self.clips)
                and self.measured is not None and self.expected is not None
                and abs(self.measured - self.expected) <= REEL_TOLERANCE)


@dataclass(frozen=True)
class Progress:
    text: str                       # "reel 1, clip 6/9, 2nd half 3:10-3:34"
    overall: float                  # 0..1
    elapsed: float
    remaining: float | None


class MissingSource(Exception):
    pass


def output_names(stem: str, count: int) -> tuple[str, list[str]]:
    return f"{stem}.mp4", [f"{stem} - {i:02d}.mp4" for i in range(1, count + 1)]


def _measure(path: Path) -> float:
    return ffmpeg.probe(path).duration


def _publish(src: Path, dest: Path) -> None:
    """Move a finished file into the output folder without ever leaving a
    half-copied file under the real name."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        shutil.copyfile(src, part)
        os.replace(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    src.unlink(missing_ok=True)


def run(
    reels: Sequence[ReelJob],
    videos: Sequence[Path],
    labels: Sequence[str],
    infos: Sequence[ffmpeg.MediaInfo],
    out_dir: Path,
    *,
    cards: Mapping[str, Path] | None = None,     # clip key -> PNG of its text screen
    card_seconds: float = 3.0,
    work_root: Path | None = None,
    preset: str = "veryfast",
    on_progress: Callable[[Progress], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[ReelResult]:
    ff = ffmpeg.find_binary("ffmpeg")
    cards = cards or {}
    used = [c.row.video for r in reels for c in r.cuts]
    first = infos[used[0]] if used else infos[0]
    look = ffmpeg.Look.for_source(first, preset=preset)
    work = (work_root or Path(tempfile.gettempdir())) / f"gaa-clips-{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True)

    def card_for(cut: clipplan.Cut) -> Path | None:
        img = cards.get(cut.row.key) if cut.row.text.strip() else None
        return img if img and img.read_bytes()[:8] == _PNG_MAGIC else None

    # Progress is measured in seconds of video to make.
    total = sum(c.length + (card_seconds if card_for(c) else 0) for r in reels for c in r.cuts) or 1.0
    done = 0.0
    started = time.monotonic()

    def report(text: str, extra: float = 0.0) -> None:
        if not on_progress:
            return
        overall = min(1.0, (done + extra) / total)
        elapsed = time.monotonic() - started
        remaining = elapsed * (1 - overall) / overall if overall > 0.02 else None
        on_progress(Progress(text, overall, elapsed, remaining))

    def encode(cmd: list[str], out: Path, seconds: float, text: str) -> None:
        part = out.with_name(out.name + ".part")
        cmd = [*cmd[:-1], str(part)]
        try:
            ffmpeg.run(cmd, lambda s: report(text, min(s, seconds)), should_stop)
            os.replace(part, out)
        except BaseException:
            part.unlink(missing_ok=True)
            raise

    results: list[ReelResult] = []
    stopped = False
    try:
        for r_i, reel in enumerate(reels):
            reel_file, clip_files = output_names(reel.file_stem, len(reel.cuts))
            res = ReelResult(reel.name, reel_file)
            results.append(res)
            if stopped:
                res.status = "stopped"
                continue
            pieces: list[Path] = []
            made: list[tuple[Path, str]] = []     # (work file, output name) for each clip
            try:
                for i, cut in enumerate(reel.cuts, start=1):
                    video = cut.row.video
                    assert video is not None
                    label = labels[video]
                    text = f"{reel.name}, clip {i}/{len(reel.cuts)}, {label} {cut.row.range_text}"
                    if should_stop and should_stop():
                        raise ffmpeg.Cancelled()
                    src = Path(videos[video])
                    if not src.exists():
                        raise MissingSource(
                            f"The video “{src.name}” ({label}) can't be found. Was it moved or "
                            f"renamed? It should be in {src.parent}.")
                    card = card_for(cut)
                    if card is not None:
                        card_out = work / f"r{r_i}_c{i:03d}_screen.mp4"
                        encode(ffmpeg.build_card_cmd(ff, card, card_seconds, card_out, look),
                               card_out, card_seconds, f"{text} (text screen)")
                        done += card_seconds
                        pieces.append(card_out)
                    clip_out = work / f"r{r_i}_c{i:03d}.mp4"
                    encode(ffmpeg.build_encode_clip_cmd(
                        ff, src, cut.start, cut.length, clip_out, look,
                        has_audio=bool(infos[video].audio_codec)), clip_out, cut.length, text)
                    done += cut.length
                    pieces.append(clip_out)
                    made.append((clip_out, clip_files[i - 1]))
                    res.clips.append(ClipResult(i, label, cut.row.range_text, clip_files[i - 1],
                                                round(cut.length, 3), _measure(clip_out)))
                    report(text)

                # Join. Copy is safe only if every piece has the same format.
                formats = {(p.width, p.height, ffmpeg.standard_fps(p.fps_str or p.fps), p.sample_rate)
                           for p in map(ffmpeg.probe, pieces)}
                listing = work / f"r{r_i}.ffconcat"
                ffmpeg.write_concat_list(pieces, listing)
                reel_out = work / f"r{r_i}_reel.mp4"
                join = ffmpeg.build_join_cmd(ff, listing, reel_out)
                if len(formats) > 1:
                    join = [*join[:join.index("-c")], *ffmpeg._encode_args(look),
                            "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
                            "-f", "mp4", str(reel_out)]
                encode(join, reel_out, 0.0, f"{reel.name}: joining {len(pieces)} pieces")
                res.expected = round(sum(_measure(p) for p in pieces), 3)
                res.measured = _measure(reel_out)

                # Move the finished files to the output folder.
                report(f"{reel.name}: saving to {out_dir}")
                for work_file, name in made:
                    _publish(work_file, out_dir / name)
                _publish(reel_out, out_dir / reel_file)
                res.status = "made"
            except ffmpeg.Cancelled:
                res.status, res.error = "stopped", "Stopped."
                stopped = True
            except MissingSource as e:
                res.status, res.error = "failed", str(e)
            except ffmpeg.FFmpegError as e:
                res.status, res.error = "failed", str(e)
            except OSError as e:
                res.status = "failed"
                res.error = (f"Couldn't save to {out_dir}: {e.strerror or e}. If a file there is open "
                             f"in a video player, close it and try again.")
            finally:
                for p in pieces:
                    p.unlink(missing_ok=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return results


def summary(results: Sequence[ReelResult]) -> list[dict]:
    """The results table, as plain data for the window."""
    out = []
    for r in results:
        out.append({
            "name": r.name, "file": r.file, "status": r.status, "error": r.error, "ok": r.ok,
            "expected": r.expected, "measured": r.measured,
            "length": timecode.fmt(r.measured) if r.measured is not None else "",
            "clips": [{"position": c.position, "video": c.video, "range": c.range_text,
                       "file": c.file, "expected": c.expected, "measured": c.measured, "ok": c.ok}
                      for c in r.clips],
        })
    return out
