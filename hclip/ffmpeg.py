"""All ffmpeg/ffprobe interaction lives here.

Command *builders* are pure functions returning argument lists, so they can be
tested exactly. ``run`` and ``probe`` are the only places a process is started.
Nothing ever loads video into Python: ffmpeg streams from disk to disk.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

# Camcorder MOVs often carry PCM audio, which the MP4 container can't hold.
# Those get a cheap audio-only re-encode; video is still copied untouched.
_MP4_OK_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac", "opus", "flac"}


class FFmpegError(RuntimeError):
    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr

    def __str__(self) -> str:
        tail = "\n".join(self.stderr.strip().splitlines()[-15:])
        return f"{self.args[0]}\n{tail}" if tail else self.args[0]


# --------------------------------------------------------------------------
# Locating the binaries
# --------------------------------------------------------------------------

def _exe(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def find_binary(name: str) -> str:
    """Find ffmpeg/ffprobe: env override, then bundled copy, then PATH."""
    env = os.environ.get(f"HCLIP_{name.upper()}")
    if env:
        return env
    candidates: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", None)  # PyInstaller one-file/one-dir
    if bundle:
        candidates += [Path(bundle) / "ffmpeg" / _exe(name), Path(bundle) / _exe(name)]
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).parent
        candidates += [here / "ffmpeg" / _exe(name), here / _exe(name)]
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which(name)
    if found:
        return found
    raise FFmpegError(
        f"{name} not found. Install ffmpeg or set HCLIP_{name.upper()} to its path."
    )


def _popen_kwargs() -> dict:
    # Stop a console window flashing up for every clip in the Windows app.
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


# --------------------------------------------------------------------------
# Probing
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class MediaInfo:
    path: str
    duration: float
    container: str
    video_codec: str | None
    width: int | None
    height: int | None
    fps: float | None
    audio_codec: str | None
    size_bytes: int

    @property
    def audio_needs_reencode(self) -> bool:
        return self.audio_codec is not None and self.audio_codec not in _MP4_OK_AUDIO


def build_probe_cmd(ffprobe: str, path: str) -> list[str]:
    return [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]


def build_keyframe_cmd(ffprobe: str, path: str, seconds: float = 60.0) -> list[str]:
    return [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-skip_frame", "nokey", "-read_intervals", f"%+{seconds:g}",
        "-show_entries", "frame=pts_time,best_effort_timestamp_time",
        "-of", "csv=p=0", path,
    ]


def _fps(rate: str | None) -> float | None:
    if not rate or rate == "0/0":
        return None
    num, _, den = rate.partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None


def parse_probe(path: str, data: dict) -> MediaInfo:
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(fmt.get("duration") or (v or {}).get("duration") or 0.0)
    return MediaInfo(
        path=path,
        duration=duration,
        container=fmt.get("format_name", ""),
        video_codec=v.get("codec_name") if v else None,
        width=v.get("width") if v else None,
        height=v.get("height") if v else None,
        fps=_fps(v.get("avg_frame_rate") or v.get("r_frame_rate")) if v else None,
        audio_codec=a.get("codec_name") if a else None,
        size_bytes=int(fmt.get("size") or 0),
    )


def _run_capture(cmd: Sequence[str]) -> str:
    proc = subprocess.run(
        list(cmd), capture_output=True, text=True, errors="replace", **_popen_kwargs()
    )
    if proc.returncode != 0:
        raise FFmpegError(f"{Path(cmd[0]).name} failed (exit {proc.returncode})",
                          proc.stderr)
    return proc.stdout


def probe(path: str | Path) -> MediaInfo:
    path = str(path)
    if not Path(path).is_file():
        raise FFmpegError(f"video not found: {path}")
    out = _run_capture(build_probe_cmd(find_binary("ffprobe"), path))
    return parse_probe(path, json.loads(out))


def keyframe_interval(path: str | Path, seconds: float = 60.0) -> float | None:
    """Average gap between keyframes over the first ``seconds`` of video."""
    out = _run_capture(build_keyframe_cmd(find_binary("ffprobe"), str(path), seconds))
    times: list[float] = []
    for line in out.splitlines():
        for part in line.split(","):
            try:
                times.append(float(part))
                break
            except ValueError:
                continue
    times = sorted(set(times))
    if len(times) < 2:
        return None
    return (times[-1] - times[0]) / (len(times) - 1)


# --------------------------------------------------------------------------
# Multi-file halves (GoPro chapters etc.)
# --------------------------------------------------------------------------

_GOPRO_RE = re.compile(r"^(G[HXL])(\d{2})(\d{4})$", re.IGNORECASE)


def sort_parts(paths: Sequence[str | Path]) -> list[Path]:
    """Order files for one half. GoPro chapters (GX010123, GX020123) sort by
    recording then chapter; everything else sorts by name."""
    def key(p: Path) -> tuple:
        m = _GOPRO_RE.match(p.stem)
        if m:
            return (0, m.group(3), int(m.group(2)), p.name.lower())
        return (1, "", 0, p.name.lower())
    return sorted((Path(p) for p in paths), key=key)


def write_concat_list(paths: Sequence[str | Path], dest: Path) -> Path:
    """Write an ffmpeg concat-demuxer list. Quotes are escaped per ffmpeg rules."""
    lines = ["ffconcat version 1.0"]
    for p in paths:
        escaped = str(Path(p).resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def concat_list_tempfile(paths: Sequence[str | Path]) -> Path:
    fd, name = tempfile.mkstemp(prefix="hclip_", suffix=".ffconcat")
    os.close(fd)
    return write_concat_list(paths, Path(name))


# --------------------------------------------------------------------------
# Cutting
# --------------------------------------------------------------------------

def input_args(source: str | Path, is_concat: bool) -> list[str]:
    if is_concat:
        return ["-f", "concat", "-safe", "0", "-i", str(source)]
    return ["-i", str(source)]


def build_cut_cmd(
    ffmpeg: str,
    source: str | Path,
    start: float,
    duration: float,
    out: str | Path,
    *,
    precise: bool = False,
    reencode_audio: bool = False,
    is_concat: bool = False,
) -> list[str]:
    """One clip. ``-ss`` before ``-i`` makes the seek near-instant on huge
    files. With stream copy the clip starts on the keyframe at or before
    ``start``; with ``precise`` the video is re-encoded and frame-accurate."""
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-ss", f"{start:.3f}",
        *input_args(source, is_concat),
        "-t", f"{duration:.3f}",
        # Only first video + first audio: GoPro telemetry/timecode data
        # streams break stream-copy into MP4.
        "-map", "0:v:0", "-map", "0:a:0?",
    ]
    if precise:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-c:v", "copy"]
        cmd += ["-c:a", "aac", "-b:a", "192k"] if reencode_audio else ["-c:a", "copy"]
        cmd += ["-avoid_negative_ts", "make_zero"]
    cmd += [
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        "-f", "mp4", str(out),
    ]
    return cmd


ProgressFn = Callable[[float], None]  # seconds of output written so far


def run(cmd: Sequence[str], on_progress: ProgressFn | None = None) -> None:
    """Run ffmpeg, streaming ``-progress`` output to ``on_progress``.

    stderr goes to a temp file (not a pipe) so a chatty ffmpeg can never
    deadlock us, and so it can be shown in full if the command fails.
    """
    with tempfile.TemporaryFile(mode="w+b") as err:
        proc = subprocess.Popen(
            list(cmd), stdout=subprocess.PIPE, stderr=err,
            stdin=subprocess.DEVNULL, **_popen_kwargs(),
        )
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if on_progress and line.startswith("out_time_us="):
                try:
                    on_progress(max(0, int(line.split("=", 1)[1])) / 1_000_000)
                except ValueError:
                    pass
        code = proc.wait()
        if code != 0:
            err.seek(0)
            stderr = err.read().decode("utf-8", "replace")
            raise FFmpegError(f"ffmpeg failed (exit {code})", stderr)
