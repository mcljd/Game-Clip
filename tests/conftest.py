"""Fixture videos generated with ffmpeg's testsrc, so tests need no real footage."""

import shutil
import subprocess
from pathlib import Path

import pytest

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")


def make_video(path: Path, seconds: int, *, gop_frames: int = 50,
               audio: str = "aac") -> Path:
    """Small 25 fps test video with a 2 s keyframe interval and a tone."""
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=25:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", str(gop_frames),
        "-keyint_min", str(gop_frames), "-sc_threshold", "0", "-pix_fmt", "yuv420p",
        "-c:a", audio, str(path),
    ], check=True)
    return path


@pytest.fixture(scope="session")
def videos(tmp_path_factory):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not installed")
    d = tmp_path_factory.mktemp("videos")
    return {
        "h1": make_video(d / "first half.mp4", 60),
        "h2": make_video(d / "second_half.mp4", 40),
        "part1": make_video(d / "GX010001.MP4", 20),
        "part2": make_video(d / "GX020001.MP4", 20),
        "pcm": make_video(d / "camcorder.mov", 20, audio="pcm_s16le"),
    }
