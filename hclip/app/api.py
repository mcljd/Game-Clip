"""What the coach's window can ask Python to do.

pywebview exposes every public method of ``Api`` to the page as
``window.pywebview.api.<name>(...)``, returning a Promise. Everything returned
is plain JSON-able data. Nothing here depends on pywebview, so it's tested
directly; the window's native dialogs are passed in as functions.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from hclip import cut, ffmpeg, naming, notes, timecode

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
        """'<title> clips' next to the first video, e.g. D:\\Dunloy\\Dunloy clips."""
        if 0 in self._slots:
            base = self._slots[0].half.files[0].parent
        elif self._slots:
            base = next(iter(self._slots.values())).half.files[0].parent
        else:
            base = Path.home() / "Videos"
        name = (title.strip() or "Match") + " clips"
        return str(base / _safe_folder(name))

    def choose_folder(self) -> str | None:
        return self._pick_folder() if self._pick_folder else None

    # --------------------------------------------------------------- check

    def check(self, text: str, out_dir: str) -> dict:
        """Everything the Make clips button needs to know."""
        note = notes.parse(text)
        n = len(note.sections)
        missing = [s.name for i, s in enumerate(note.sections) if i not in self._slots]
        result = {"ready": False, "message": "", "problems": [], "clips": 0,
                  "existing": 0}
        if note.clip_count == 0:
            result["message"] = "Paste the clip timings to begin."
            return result
        if missing:
            result["message"] = "Choose the video for: " + ", ".join(missing)
            return result
        halves = [self._slots[i].half for i in range(n)]
        p = cut.plan(note, halves, Path(out_dir or "."))
        result["problems"] = [_problem(x) for x in p.problems]
        result["clips"] = len(p.jobs)
        result["existing"] = sum(j.out.exists() for j in p.jobs)
        if not p.jobs:
            result["message"] = "None of the clips fit inside the videos."
            return result
        if not out_dir:
            result["message"] = "Choose where to save the clips."
            return result
        result["ready"] = True
        return result

    # ----------------------------------------------------------------- run

    def start(self, text: str, out_dir: str, precise: bool = False,
              redo: bool = False) -> dict:
        with self._lock:
            if self._state.running:
                return {"error": "Already making clips."}
        chk = self.check(text, out_dir)
        if not chk["ready"]:
            return {"error": chk["message"]}
        note = notes.parse(text)
        halves = [self._slots[i].half for i in range(len(note.sections))]
        p = cut.plan(note, halves, Path(out_dir))
        self._stop.clear()
        with self._lock:
            self._state = _RunState(running=True, total=len(p.jobs), out_dir=out_dir)
        self._thread = threading.Thread(
            target=self._work, args=(p.jobs, precise, redo), daemon=True)
        self._thread.start()
        return {"ok": True}

    def _work(self, jobs: list[cut.Job], precise: bool, redo: bool) -> None:
        def on_progress(pr: cut.Progress) -> None:
            with self._lock:
                st = self._state
                st.index, st.current = pr.index, pr.job.out.name
                st.fraction, st.overall, st.remaining = pr.fraction, pr.overall, pr.remaining

        def on_result(r: cut.Result) -> None:
            with self._lock:
                if r.status == "made":
                    self._state.made += 1
                elif r.status == "skipped":
                    self._state.skipped += 1
                else:
                    self._state.failed.append({"name": r.job.out.name,
                                               "line": r.job.clip.line,
                                               "error": r.error})
        try:
            cut.run(jobs, precise=precise, force=redo, on_progress=on_progress,
                    on_result=on_result, should_stop=self._stop.is_set)
        except Exception as e:  # show anything unexpected rather than hang
            with self._lock:
                self._state.error = str(e) or type(e).__name__
        finally:
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
                "total": st.total, "number": min(st.index + 1, st.total) if st.total else 0,
                "current": st.current, "fraction": st.fraction,
                "overall": st.overall,
                "remaining": None if st.remaining is None else round(st.remaining),
                "made": st.made, "skipped": st.skipped, "failed": list(st.failed),
                "out_dir": st.out_dir,
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
