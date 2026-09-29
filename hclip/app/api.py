"""What the coach's window can ask Python to do.

pywebview exposes every public method of ``Api`` to the page as
``window.pywebview.api.<name>(...)``, returning a Promise. Everything returned
is plain JSON-able data. Nothing here depends on pywebview, so it's tested
directly; the window's native dialogs are passed in as functions.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from hclip import cut, ffmpeg, naming, notes, reel, timecode

VIDEO_EXTS = ("mp4", "mov", "m4v", "mts", "m2ts", "avi", "mkv", "lrv")

PickFiles = Callable[[], Sequence[str] | None]
PickFolder = Callable[[], str | None]


def _problem(p: notes.Problem) -> dict:
    return {"line": p.line, "message": p.message, "fatal": p.fatal}


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


@dataclass
class _Slot:
    half: cut.HalfVideo
    size_bytes: int


@dataclass
class _RunState:
    running: bool = False
    total: int = 0
    index: int = 0
    current: str = ""
    fraction: float = 0.0
    overall: float = 0.0
    remaining: float | None = None
    made: int = 0
    skipped: int = 0
    failed: list[dict] = field(default_factory=list)
    finished: bool = False
    cancelled: bool = False
    error: str = ""
    out_dir: str = ""
    reels: list[dict] = field(default_factory=list)


class Api:
    def __init__(self, pick_files: PickFiles | None = None,
                 pick_folder: PickFolder | None = None) -> None:
        self._pick_files = pick_files
        self._pick_folder = pick_folder
        self._slots: dict[int, _Slot] = {}
        self._lock = threading.Lock()
        self._state = _RunState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- note

    def read_note(self, text: str) -> dict:
        note = notes.parse(text)
        return {
            "title": note.title,
            "clip_count": note.clip_count,
            "sections": [
                {
                    "name": s.name,
                    "tag": s.tag,
                    "clips": [
                        {"start": timecode.fmt(c.start), "end": timecode.fmt(c.end),
                         "length": timecode.fmt(c.duration), "label": c.label,
                         "line": c.line}
                        for c in s.clips
                    ],
                }
                for s in note.sections
            ],
            "problems": [_problem(p) for p in note.problems],
        }

    # -------------------------------------------------------------- videos

    def choose_videos(self, index: int) -> dict | None:
        """Open the file picker for one section. Several files = one half
        recorded in parts (GoPro chapters); they're joined in order."""
        if self._pick_files is None:
            return {"error": "file picker unavailable"}
        picked = self._pick_files()
        if not picked:
            return None
        return self.set_videos(index, list(picked))

    def set_videos(self, index: int, paths: list[str]) -> dict:
        try:
            half = cut.HalfVideo.open(paths)
        except (ffmpeg.FFmpegError, ValueError) as e:
            return {"error": f"Couldn't open that video. {str(e).splitlines()[0]}"}
        if half.duration <= 0:
            return {"error": "That file doesn't look like a video."}
        size = sum(Path(f).stat().st_size for f in half.files)
        old = self._slots.pop(index, None)
        if old:
            old.half.close()
        self._slots[index] = _Slot(half, size)
        return self._slot_info(index)

    def clear_videos(self, index: int) -> None:
        slot = self._slots.pop(index, None)
        if slot:
            slot.half.close()

    def _slot_info(self, index: int) -> dict:
        s = self._slots[index]
        return {
            "files": [f.name for f in s.half.files],
            "folder": str(s.half.files[0].parent),
            "duration": timecode.fmt(s.half.duration),
            "size": _gb(s.size_bytes),
        }

    # -------------------------------------------------------------- output

    def default_folder(self, title: str) -> str:
        """'<title> highlights' next to the first video."""
        if 0 in self._slots:
            base = self._slots[0].half.files[0].parent
        elif self._slots:
            base = next(iter(self._slots.values())).half.files[0].parent
        else:
            base = Path.home() / "Videos"
        name = (title.strip() or "Match") + " highlights"
        return str(base / _safe_folder(name))

    def choose_folder(self) -> str | None:
        return self._pick_folder() if self._pick_folder else None

    # --------------------------------------------------------------- check

    def _plan(self, text: str, out_dir: str):
        note = notes.parse(text)
        halves = [self._slots[i].half for i in range(len(note.sections))]
        return note, cut.plan(note, halves, Path(out_dir or "."))

    def check(self, text: str, out_dir: str, title: str = "", cards: bool = False) -> dict:
        """Everything the Make buttons need to know."""
        note = notes.parse(text)
        missing = [s.name for i, s in enumerate(note.sections) if i not in self._slots]
        result = {"ready": False, "message": "", "problems": [], "clips": 0,
                  "reels": [], "whole": None, "labelled": 0, "encode": False,
                  "look": None, "existing": []}
        if note.clip_count == 0:
            result["message"] = "Paste the clip timings to begin."
            return result
        if missing:
            result["message"] = "Choose the video for: " + ", ".join(missing)
            return result
        note, p = self._plan(text, out_dir)
        result["problems"] = self._shared_video_warnings(note) + [_problem(x) for x in p.problems]
        result["clips"] = len(p.jobs)
        if not p.jobs:
            result["message"] = "None of the clips fit inside the videos."
            return result
        out = Path(out_dir or ".")
        halves_r = reel.plan_reels(note, p.jobs, out, title, halves=True)
        [whole_r] = reel.plan_reels(note, p.jobs, out, title, halves=False, whole=True)
        info = lambda r: {"name": r.name, "file": r.out.name, "clips": len(r.jobs),
                          "length": timecode.fmt(r.duration), "exists": r.out.exists()}
        result["reels"] = [info(r) for r in halves_r]
        result["whole"] = info(whole_r)
        result["labelled"] = sum(1 for j in p.jobs if j.clip.label.strip())
        use_cards = cards and result["labelled"] > 0
        result["encode"] = use_cards or not reel.can_copy(p.jobs)
        look = reel.look_for(p.jobs)
        result["look"] = {"width": look.width, "height": look.height}
        if not out_dir:
            result["message"] = "Choose where to save the videos."
            return result
        result["ready"] = True
        return result

    def _shared_video_warnings(self, note: notes.Note) -> list[dict]:
        """The same file chosen for two sections is almost always a mistake
        (e.g. both halves' files picked together in every slot): each
        section's timings count from the start of its own video."""
        users: dict[Path, list[str]] = {}
        for i, s in enumerate(note.sections):
            slot = self._slots.get(i)
            for f in slot.half.files if slot else []:
                users.setdefault(Path(f).resolve(), []).append(s.name)
        warnings = []
        for f, names in users.items():
            if len(names) > 1:
                warnings.append({"line": None, "fatal": False, "message": (
                    f"“{f.name}” is chosen for {' and '.join(names)}. Each part of the "
                    f"note is timed from the start of its own video, so each usually "
                    f"needs a different video. Press Change… to fix it.")})
        return warnings

    # ----------------------------------------------------------------- run

    def start(self, text: str, out_dir: str, title: str = "", what: str = "halves",
              cards: dict | None = None, card_seconds: float = reel.DEFAULT_CARD_SECONDS,
              keep_clips: bool = False, precise: bool = False) -> dict:
        """``what`` is "halves" (one reel per half) or "whole" (one video).
        ``cards`` maps a note line (as a string, from JS) to a PNG as base64."""
        # Claim the run under the lock before doing anything else, so a
        # double-click (each JS call runs on its own thread) can't start two.
        with self._lock:
            if self._state.running:
                return {"error": "Already making videos."}
            self._state = _RunState(running=True, out_dir=out_dir)
        try:
            chk = self.check(text, out_dir, title, bool(cards))
            if not chk["ready"]:
                raise ValueError(chk["message"])
            note, p = self._plan(text, out_dir)
            out = Path(out_dir)
            reels = reel.plan_reels(note, p.jobs, out, title,
                                    halves=what == "halves", whole=what == "whole")
            card_images = {}
            if cards:
                card_dir = Path(tempfile.mkdtemp(prefix="gaa-cards-"))
                for line, b64 in cards.items():
                    path = card_dir / f"card_{int(line)}.png"
                    path.write_bytes(base64.b64decode(b64.split(",")[-1]))
                    card_images[int(line)] = path
        except Exception as e:
            with self._lock:
                self._state = _RunState()
            return {"error": str(e) or "Couldn't start."}
        self._stop.clear()
        with self._lock:
            self._state.total = len(reels)
        self._thread = threading.Thread(
            target=self._work,
            args=(reels, card_images, float(card_seconds),
                  out / "Clips" if keep_clips else None, precise),
            daemon=True)
        self._thread.start()
        return {"ok": True}

    def _work(self, reels, card_images, card_seconds, keep_dir, precise) -> None:
        def on_progress(pr: reel.ReelProgress) -> None:
            with self._lock:
                st = self._state
                st.current, st.overall, st.remaining = pr.stage, pr.overall, pr.remaining
        try:
            results = reel.run_reels(
                reels, card_images=card_images, card_seconds=card_seconds,
                # Always rebuild: the coach may have changed the text or clips.
                precise=precise, keep_clips_dir=keep_dir, force=True,
                on_progress=on_progress, should_stop=self._stop.is_set)
            with self._lock:
                self._state.reels = [
                    {"name": r.reel.name, "file": r.reel.out.name, "status": r.status,
                     "error": r.error, "missing": list(r.missing),
                     "missing_screens": list(r.missing_screens),
                     "length": timecode.fmt(r.reel.duration)}
                    for r in results]
                self._state.made = sum(r.status == "made" for r in results)
                self._state.skipped = sum(r.status == "skipped" for r in results)
                self._state.failed = [x for x in self._state.reels if x["status"] == "failed"]
        except Exception as e:  # show anything unexpected rather than hang
            with self._lock:
                self._state.error = str(e) or type(e).__name__
        finally:
            for path in card_images.values():
                path.unlink(missing_ok=True)
            if card_images:
                shutil.rmtree(next(iter(card_images.values())).parent, ignore_errors=True)
            with self._lock:
                self._state.running = False
                self._state.finished = True
                self._state.cancelled = self._stop.is_set()

    def status(self) -> dict:
        with self._lock:
            st = self._state
            return {
                "running": st.running, "finished": st.finished,
                "cancelled": st.cancelled, "error": st.error,
                "total": st.total, "stage": st.current, "overall": st.overall,
                "remaining": None if st.remaining is None else round(st.remaining),
                "made": st.made, "skipped": st.skipped, "failed": list(st.failed),
                "reels": list(st.reels), "out_dir": st.out_dir,
            }

    def cancel(self) -> None:
        self._stop.set()

    def wait(self, timeout: float | None = None) -> None:
        """For tests: block until the current run finishes."""
        if self._thread:
            self._thread.join(timeout)

    # ---------------------------------------------------------------- misc

    def open_folder(self, path: str) -> None:
        if not path or not Path(path).is_dir():
            return
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def ffmpeg_ok(self) -> dict:
        try:
            ffmpeg.find_binary("ffmpeg")
            ffmpeg.find_binary("ffprobe")
            return {"ok": True}
        except ffmpeg.FFmpegError as e:
            return {"ok": False, "error": str(e)}


def _safe_folder(name: str) -> str:
    """Keep the coach's wording (spaces, capitals) but drop characters Windows
    forbids in folder names."""
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in bad or ord(ch) < 32 else ch for ch in name)
    cleaned = cleaned.strip(" .")
    return cleaned or naming.folder_name(name)
