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
import threading
import time
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
    fps_str: str | None = None      # exact rate, e.g. "30000/1001"
    pix_fmt: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    profile: str | None = None
    level: int | None = None

    def join_key(self) -> tuple:
        """Clips from files with the same key can be joined without
        re-encoding: same codec, profile/level, picture size, standard
        frame rate and audio."""
        return (self.video_codec, self.profile, self.level, self.width, self.height,
                standard_fps(self.fps_str or self.fps), self.pix_fmt,
                self.audio_codec, self.sample_rate, self.channels)

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


def _nominal_rate(v: dict) -> str | None:
    """The camera's frame rate. Prefer the stated rate (r_frame_rate): phones
    record variable frame rate, whose average is an odd fraction. But some
    phones state a timebase-like guess (90000/1); then use the average."""
    r, avg = v.get("r_frame_rate"), v.get("avg_frame_rate")
    rv = _fps(r)
    if rv and 1 <= rv <= 120:
        return r
    return avg if _fps(avg) else r


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
        fps_str=_nominal_rate(v) if v else None,
        pix_fmt=v.get("pix_fmt") if v else None,
        profile=v.get("profile") if v else None,
        level=v.get("level") if v else None,
        sample_rate=int(a["sample_rate"]) if a and a.get("sample_rate") else None,
        channels=a.get("channels") if a else None,
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


def write_concat_list(paths: Sequence[str | Path], dest: Path,
                      inpoint: float | None = None) -> Path:
    """Write an ffmpeg concat-demuxer list. Quotes are escaped per ffmpeg rules.
    ``inpoint`` starts the first file part-way in (the demuxer seeks there;
    ``-ss`` on a joined input can't seek and reads from the very start)."""
    lines = ["ffconcat version 1.0"]
    for n, p in enumerate(paths):
        escaped = str(Path(p).resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
        if n == 0 and inpoint:
            lines.append(f"inpoint {inpoint:.3f}")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def concat_list_tempfile(paths: Sequence[str | Path], inpoint: float | None = None) -> Path:
    fd, name = tempfile.mkstemp(prefix="hclip_", suffix=".ffconcat")
    os.close(fd)
    return write_concat_list(paths, Path(name), inpoint)


# --------------------------------------------------------------------------
# Cutting
# --------------------------------------------------------------------------

def input_args(source: str | Path, is_concat: bool) -> list[str]:
    if is_concat:
        return ["-f", "concat", "-safe", "0", "-i", str(source)]
    return ["-i", str(source)]


def seek_args(start: float, is_concat: bool, reencode: bool) -> list[str]:
    """Input options that start reading at ``start``.

    A joined input (``is_concat``) is a list that already starts at the clip
    (see ``HalfVideo.clip_source``): ``-ss`` can't seek in it and would copy
    everything from the start of the video. When re-encoding, ``-copyts``
    keeps the lead-in before the inpoint at negative times so
    ``_preroll_trim`` can drop it."""
    if is_concat:
        return ["-copyts"] if reencode else []
    return ["-ss", f"{start:.3f}"]


def _preroll_trim(duration: float) -> tuple[str, str]:
    """Filters that drop frames before the clip's first frame (negative
    timestamps under -copyts) and restart the clock at zero."""
    return (f"trim=start=0:duration={duration:.3f},setpts=PTS-STARTPTS",
            f"atrim=start=0:duration={duration:.3f},asetpts=PTS-STARTPTS")


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
    hevc: bool = False,
) -> list[str]:
    """One clip. ``-ss`` before ``-i`` makes the seek near-instant on huge
    files. With stream copy the clip starts on the keyframe at or before
    ``start``; with ``precise`` the video is re-encoded and frame-accurate."""
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        *seek_args(start, is_concat, reencode=precise),
        *input_args(source, is_concat),
        "-t", f"{duration:.3f}",
        # Only first video + first audio: GoPro telemetry/timecode data
        # streams break stream-copy into MP4.
        "-map", "0:v:0", "-map", "0:a:0?",
    ]
    if precise:
        if is_concat:
            vf, af = _preroll_trim(duration)
            cmd += ["-vf", vf, "-af", af]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-c:v", "copy"]
        if hevc:
            # iPhone HEVC: Apple players (Photos, QuickTime) need the hvc1 tag.
            cmd += ["-tag:v", "hvc1"]
        cmd += ["-c:a", "aac", "-b:a", "192k"] if reencode_audio else ["-c:a", "copy"]
        cmd += ["-avoid_negative_ts", "make_zero"]
    cmd += [
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        "-f", "mp4", str(out),
    ]
    return cmd


# --------------------------------------------------------------------------
# Reels: re-encoded pieces that join cleanly
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Look:
    """The one encode every piece of a re-encoded reel shares, so the pieces
    can then be joined by stream copy without glitches at the joins."""
    width: int
    height: int
    fps: str = "25"          # exact rate string, e.g. "30000/1001"
    preset: str = "veryfast"
    crf: int = 20

    @classmethod
    def for_source(cls, info: "MediaInfo", max_height: int | None = None,
                   preset: str = "veryfast") -> "Look":
        w, h = info.width or 1280, info.height or 720
        if max_height and h > max_height:
            w, h = round(w * max_height / h), max_height
        even = lambda n: max(2, int(n) // 2 * 2)
        return cls(even(w), even(h), standard_fps(info.fps_str or info.fps), preset)


# Frame rates cameras actually use, as exact strings for ffmpeg.
_STANDARD_FPS = {23.976: "24000/1001", 24.0: "24", 25.0: "25", 29.97: "30000/1001", 30.0: "30",
                 48.0: "48", 50.0: "50", 59.94: "60000/1001", 60.0: "60"}


def standard_fps(rate: str | float | None) -> str:
    """Snap a probed rate to the nearest standard one (within 1.5%), so reels
    come out at 25 or 29.97, never 115200000/4608001. Very high rates (slow
    motion) are capped at 60; anything else unusual is rounded."""
    value = _fps(rate) if isinstance(rate, str) else rate
    if not value or value <= 0:
        return "25"
    value = min(value, 60.0)
    nearest = min(_STANDARD_FPS, key=lambda r: abs(r - value))
    if abs(nearest - value) / nearest <= 0.015:
        return _STANDARD_FPS[nearest]
    return str(round(value))


def _encode_args(look: Look) -> list[str]:
    return [
        "-c:v", "libx264", "-preset", look.preset, "-crf", str(look.crf),
        "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-video_track_timescale", "90000",
    ]


def _fit_filter(look: Look) -> str:
    w, h = look.width, look.height
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={look.fps},format=yuv420p")


_SILENCE = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]


def build_encode_clip_cmd(
    ffmpeg: str, source: str | Path, start: float, duration: float, out: str | Path,
    look: Look, *, is_concat: bool = False, has_audio: bool = True,
) -> list[str]:
    """One clip re-encoded to ``look`` (frame-accurate, since it's decoded)."""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y",
           *seek_args(start, is_concat, reencode=True), *input_args(source, is_concat)]
    if not has_audio:
        cmd += _SILENCE
    vf = _fit_filter(look)
    if is_concat:
        trim_v, trim_a = _preroll_trim(duration)
        vf = f"{trim_v},{vf}"
        if has_audio:
            cmd += ["-af", trim_a]
    cmd += ["-t", f"{duration:.3f}", "-map", "0:v:0",
            "-map", "0:a:0" if has_audio else "1:a:0",
            "-vf", vf, *_encode_args(look),
            "-progress", "pipe:1", "-nostats", "-f", "mp4", str(out)]
    return cmd


def build_card_cmd(
    ffmpeg: str, image: str | Path, seconds: float, out: str | Path, look: Look,
) -> list[str]:
    """A still image (the black text screen) held for ``seconds``, with silence."""
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        # -t on the input too: an unreadable image then ends in an error
        # instead of looping for ever without producing a frame.
        "-loop", "1", "-framerate", look.fps, "-t", f"{seconds:.3f}", "-i", str(image),
        "-t", f"{seconds:.3f}", *_SILENCE,
        "-t", f"{seconds:.3f}", "-map", "0:v:0", "-map", "1:a:0",
        "-vf", _fit_filter(look), *_encode_args(look),
        "-progress", "pipe:1", "-nostats", "-f", "mp4", str(out),
    ]


def build_join_cmd(ffmpeg: str, concat_list: str | Path, out: str | Path) -> list[str]:
    """Join pieces that share one encoding, by stream copy (no quality loss)."""
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
        "-f", "mp4", str(out),
    ]


ProgressFn = Callable[[float], None]  # seconds of output written so far


class Cancelled(Exception):
    pass


STALL_SECONDS = 120.0


def run(
    cmd: Sequence[str],
    on_progress: ProgressFn | None = None,
    should_stop: Callable[[], bool] | None = None,
    stall_seconds: float = STALL_SECONDS,
) -> None:
    """Run ffmpeg, streaming ``-progress`` output to ``on_progress``.

    stderr goes to a temp file (not a pipe) so a chatty ffmpeg can never
    deadlock us, and so it can be shown in full if the command fails.
    A watcher thread checks ``should_stop`` four times a second; when it
    returns True ffmpeg is killed and ``Cancelled`` raised. If the output
    position doesn't move for ``stall_seconds``, ffmpeg is assumed stuck,
    killed, and an FFmpegError raised, so the app can never hang.
    """
    with tempfile.TemporaryFile(mode="w+b") as err:
        proc = subprocess.Popen(
            list(cmd), stdout=subprocess.PIPE, stderr=err,
            stdin=subprocess.DEVNULL, **_popen_kwargs(),
        )
        assert proc.stdout is not None
        state = {"pos": -1.0, "moved": time.monotonic(), "why": None}
        lock = threading.Lock()
        finished = threading.Event()

        def watch() -> None:
            # Runs on its own thread: ffmpeg can hang without printing
            # anything, so this can't depend on reading its output.
            while not finished.wait(0.25):
                with lock:
                    stalled = time.monotonic() - state["moved"] > stall_seconds
                if should_stop and should_stop():
                    state["why"] = "stop"
                elif stalled:
                    state["why"] = "stall"
                else:
                    continue
                proc.kill()
                return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("out_time_us="):
                    continue
                try:
                    pos = max(0, int(line.split("=", 1)[1])) / 1_000_000
                except ValueError:       # "N/A": nothing written yet
                    continue
                with lock:
                    if pos != state["pos"]:
                        state["pos"], state["moved"] = pos, time.monotonic()
                if on_progress:
                    on_progress(pos)
            code = proc.wait()
        finally:
            finished.set()
            watcher.join()
        if state["why"] == "stop":
            raise Cancelled()
        if state["why"] == "stall":
            err.seek(0)
            raise FFmpegError(
                f"ffmpeg stopped making progress for {stall_seconds:.0f}s and was stopped",
                err.read().decode("utf-8", "replace"))
        if code != 0:
            err.seek(0)
            stderr = err.read().decode("utf-8", "replace")
            raise FFmpegError(f"ffmpeg failed (exit {code})", stderr)
