"""What the coach's window can ask Python to do.

pywebview exposes every public method of ``Api`` to the page as
``window.pywebview.api.<name>(...)``, returning a Promise. Everything returned
is plain JSON-able data. Nothing here depends on pywebview, so it's tested
directly; the window's native dialogs are passed in as functions.

The model (see hclip/clipplan.py): any number of labelled source videos; the
pasted text describes reels; each clip comes from one chosen video.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from hclip import clipplan, cutter, ffmpeg, naming, timecode

VIDEO_EXTS = ("mp4", "mov", "m4v", "mts", "m2ts", "avi", "mkv", "lrv")

PickFiles = Callable[[], Sequence[str] | None]
PickFolder = Callable[[], str | None]


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def default_label(index: int) -> str:
    return ["1st half", "2nd half"][index] if index < 2 else f"Video {index + 1}"


def cloud_only(path: Path) -> bool:
    """True for a OneDrive file that's "online only": it exists, but reading
    it means downloading several GB in the middle of cutting."""
    attrs = getattr(os.stat(path), "st_file_attributes", 0)
    offline = getattr(stat, "FILE_ATTRIBUTE_OFFLINE", 0x1000)
    recall = 0x400000   # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
    return bool(attrs & (offline | recall))


def in_onedrive(path: str) -> bool:
    return "onedrive" in path.lower()


def _natural(name: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


@dataclass
class _Video:
    path: Path
    label: str
    info: ffmpeg.MediaInfo


@dataclass
class _RunState:
    running: bool = False
    text: str = ""
    overall: float = 0.0
    remaining: float | None = None
    finished: bool = False
    cancelled: bool = False
    error: str = ""
    out_dir: str = ""
    results: list[dict] = field(default_factory=list)


class Api:
    def __init__(self, pick_files: PickFiles | None = None,
                 pick_folder: PickFolder | None = None) -> None:
        self._pick_files = pick_files
        self._pick_folder = pick_folder
        self._videos: list[_Video] = []
        self._lock = threading.Lock()
        self._state = _RunState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -------------------------------------------------------------- videos

    def videos(self) -> list[dict]:
        out = []
        for v in self._videos:
            try:
                online_only = v.path.exists() and cloud_only(v.path)
            except OSError:
                online_only = False
            out.append({
                "label": v.label, "file": v.path.name, "folder": str(v.path.parent),
                "duration": timecode.fmt(v.info.duration), "seconds": v.info.duration,
                "size": _gb(v.info.size_bytes), "missing": not v.path.exists(),
                "online_only": online_only,
            })
        return out

    def add_videos(self) -> dict:
        """Open the file picker; several files can be chosen at once."""
        if self._pick_files is None:
            return {"error": "file picker unavailable"}
        picked = self._pick_files()
        return self.add_video_paths(list(picked or []))

    def add_video_paths(self, paths: list[str]) -> dict:
        """Add videos (from the picker or dropped on the window), in name
        order, so "1st half …" comes before "2nd half …"."""
        errors = []
        for p in sorted(paths, key=lambda p: _natural(Path(p).name)):
            path = Path(p)
            if any(v.path == path for v in self._videos):
                continue
            try:
                info = ffmpeg.probe(path)
                if info.duration <= 0 or not info.video_codec:
                    raise ValueError("no video in it")
            except (ffmpeg.FFmpegError, ValueError, OSError) as e:
                errors.append(f"Couldn't open “{path.name}”: {str(e).splitlines()[0]}")
                continue
            self._videos.append(_Video(path, default_label(len(self._videos)), info))
        return {"videos": self.videos(), "errors": errors}

    def remove_video(self, index: int) -> list[dict]:
        if 0 <= index < len(self._videos):
            del self._videos[index]
        return self.videos()

    def move_video(self, index: int, step: int) -> list[dict]:
        j = index + step
        if 0 <= index < len(self._videos) and 0 <= j < len(self._videos):
            self._videos[index], self._videos[j] = self._videos[j], self._videos[index]
        return self.videos()

    def set_label(self, index: int, label: str) -> list[dict]:
        if 0 <= index < len(self._videos):
            self._videos[index].label = label.strip() or default_label(index)
        return self.videos()

    # ---------------------------------------------------------------- plan

    def _labels(self) -> list[str]:
        return [v.label for v in self._videos]

    def _check(self, text: str, choices: dict | None, pad_start: float, pad_end: float):
        plan = clipplan.parse(text, self._labels(), {k: int(v) for k, v in (choices or {}).items()})
        chk = clipplan.check(plan, [v.info.duration for v in self._videos],
                             max(0.0, float(pad_start)), max(0.0, float(pad_end)))
        return plan, chk

    def plan(self, text: str, choices: dict | None = None, pad_start: float = 0.0,
             pad_end: float = 1.0) -> dict:
        """The clip list for the window: reels of rows, each with its video
        (pre-filled or chosen), length after padding and any problem."""
        plan, chk = self._check(text, choices, pad_start, pad_end)
        cut_of = {id(c.row): c for c in chk.cuts}
        err_of = {id(r): e for r, e in chk.errors}
        reels = []
        for i, reel in enumerate(plan.reels):
            rows = []
            for r in reel.rows:
                c = cut_of.get(id(r))
                rows.append({
                    "key": r.key, "position": r.position, "line": r.line,
                    "range": r.range_text, "text": r.text, "header": r.header,
                    "video": r.video, "chosen_by": r.chosen_by, "why": r.why,
                    "error": err_of.get(id(r), "") if r.video is not None or r.error else "",
                    "needs_video": r.video is None and not r.error,
                    "warning": c.warning if c else "",
                    "length": timecode.fmt(c.length) if c else
                              (timecode.fmt(r.length) if r.length and r.length > 0 else ""),
                })
            seconds = sum(c.length for c in chk.cuts if c.row.reel == i)
            reels.append({"name": reel.name, "rows": rows, "length": timecode.fmt(seconds),
                          "ready": all(not x["error"] and not x["needs_video"] for x in rows)})
        needs = len(plan.unselected) - sum(1 for r in plan.unselected if r.error)
        bad = sum(1 for r, _ in chk.errors if r.video is not None or r.error)
        if not plan.rows:
            message = "Paste the clip timings to begin." if self._videos else "Add the videos, then paste the timings."
        elif not self._videos:
            message = "Add the videos."
        elif needs:
            message = f"Choose a video for {needs} clip{'s' if needs != 1 else ''} (highlighted)."
        elif bad:
            message = f"Fix {bad} clip{'s' if bad != 1 else ''} marked in red."
        else:
            message = ""
        used = [v for v in (c.row.video for c in chk.cuts) if v is not None]
        look = ffmpeg.Look.for_source(self._videos[used[0]].info) if used else None
        return {
            "reels": reels,
            "ignored": [{"line": n, "text": t} for n, t in plan.ignored],
            "stale": [clipplan.describe_key(k) for k in plan.stale_choices],
            "can_cut": chk.can_cut and bool(self._videos),
            "message": message,
            "look": {"width": look.width, "height": look.height} if look else None,
        }

    # -------------------------------------------------------------- output

    def default_folder(self) -> str:
        """Somewhere on this laptop, not OneDrive: writing big files into
        OneDrive can crawl at under 1 MB/s."""
        videos_dir = Path.home() / "Videos"
        base = videos_dir if videos_dir.is_dir() and not in_onedrive(str(videos_dir)) else Path.home()
        return str(base / "GAA Clips")

    def choose_folder(self) -> str | None:
        return self._pick_folder() if self._pick_folder else None

    def folder_note(self, path: str) -> str:
        if in_onedrive(path):
            return ("This folder is in OneDrive. Videos are made on this laptop first and "
                    "moved there at the end, but OneDrive may take a while to upload them.")
        return ""

    # ----------------------------------------------------------------- run

    def start(self, text: str, choices: dict | None, pad_start: float, pad_end: float,
              out_dir: str, names: list[str] | None = None, cards: dict | None = None,
              card_seconds: float = 3.0) -> dict:
        """Cut every reel. ``names`` are the reels' file names (without
        .mp4); ``cards`` maps a note line (a string, from JS) to the PNG of
        its text screen as base64."""
        # Claim the run under the lock first, so a double-click can't start two.
        with self._lock:
            if self._state.running:
                return {"error": "Already cutting."}
            self._state = _RunState(running=True, out_dir=out_dir)
        try:
            plan, chk = self._check(text, choices, pad_start, pad_end)
            if not chk.can_cut:
                raise ValueError(self.plan(text, choices, pad_start, pad_end)["message"]
                                 or "Nothing to cut.")
            if not out_dir.strip():
                raise ValueError("Choose where to save the videos.")
            stems = _unique([naming_safe((names or [])[i] if i < len(names or []) else r.name)
                             or r.name for i, r in enumerate(plan.reels)])
            jobs = [cutter.ReelJob(r.name, stems[i], [c for c in chk.cuts if c.row.reel == i])
                    for i, r in enumerate(plan.reels)]
            card_images: dict[int, Path] = {}
            if cards:
                card_dir = Path(tempfile.mkdtemp(prefix="gaa-cards-"))
                for line, b64 in cards.items():
                    path = card_dir / f"card_{int(line)}.png"
                    path.write_bytes(base64.b64decode(b64.split(",")[-1]))
                    card_images[int(line)] = path
            videos = list(self._videos)
        except Exception as e:
            with self._lock:
                self._state = _RunState()
            return {"error": str(e) or "Couldn't start."}
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._work, args=(jobs, videos, Path(out_dir), card_images, float(card_seconds)),
            daemon=True)
        self._thread.start()
        return {"ok": True}

    def _work(self, jobs, videos: list[_Video], out_dir: Path, card_images, card_seconds) -> None:
        def on_progress(p: cutter.Progress) -> None:
            with self._lock:
                self._state.text, self._state.overall, self._state.remaining = p.text, p.overall, p.remaining
        try:
            results = cutter.run(
                jobs, [v.path for v in videos], [v.label for v in videos], [v.info for v in videos],
                out_dir, cards=card_images, card_seconds=card_seconds,
                on_progress=on_progress, should_stop=self._stop.is_set)
            with self._lock:
                self._state.results = cutter.summary(results)
        except Exception as e:  # show anything unexpected rather than hang
            with self._lock:
                self._state.error = str(e) or type(e).__name__
        finally:
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
                "running": st.running, "finished": st.finished, "cancelled": st.cancelled,
                "error": st.error, "text": st.text, "overall": st.overall,
                "remaining": None if st.remaining is None else round(st.remaining),
                "results": list(st.results), "out_dir": st.out_dir,
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


def naming_safe(name: str) -> str:
    """Keep the coach's wording ("bc clip 1") but drop what Windows forbids."""
    bad = '<>:"/\\|?*'
    return "".join("_" if ch in bad or ord(ch) < 32 else ch for ch in name).strip(" .")


def _unique(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        k = n.lower()
        seen[k] = seen.get(k, 0) + 1
        out.append(n if seen[k] == 1 else f"{n} ({seen[k]})")
    return out


def _safe_folder(name: str) -> str:
    cleaned = naming_safe(name)
    return cleaned or naming.folder_name(name)
