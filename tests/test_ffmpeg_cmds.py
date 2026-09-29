import json
import subprocess
from pathlib import Path

import pytest

from hclip import ffmpeg


def test_stream_copy_cut_cmd():
    cmd = ffmpeg.build_cut_cmd("ffmpeg", "in.mp4", 112, 18, "out.mp4.part")
    assert cmd == [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-ss", "112.000", "-i", "in.mp4",
        "-t", "18.000", "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "copy", "-c:a", "copy", "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
        "-f", "mp4", "out.mp4.part",
    ]


def test_pcm_audio_gets_aac_but_video_still_copied():
    cmd = ffmpeg.build_cut_cmd("ffmpeg", "in.mov", 0, 5, "o", reencode_audio=True)
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "aac"


def test_precise_reencodes():
    cmd = ffmpeg.build_cut_cmd("ffmpeg", "in.mp4", 0, 5, "o", precise=True)
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert "-avoid_negative_ts" not in cmd


def test_single_file_seeks_before_input():
    cmd = ffmpeg.build_cut_cmd("ffmpeg", "in.mp4", 90, 5, "o")
    assert cmd.index("-ss") < cmd.index("-i")   # input seek: near-instant on huge files


def test_concat_input_starts_at_inpoint_not_ss():
    # -ss can't seek in a joined input: it would copy everything from the
    # start of the half. The list itself starts at the clip (inpoint).
    cmd = ffmpeg.build_cut_cmd("ffmpeg", "list.ffconcat", 0, 5, "o", is_concat=True)
    i = cmd.index("-i")
    assert cmd[i - 4:i + 2] == ["-f", "concat", "-safe", "0", "-i", "list.ffconcat"]
    assert "-ss" not in cmd


def test_concat_reencode_trims_lead_in():
    cmd = ffmpeg.build_cut_cmd("ffmpeg", "l.ffconcat", 0, 5, "o", is_concat=True, precise=True)
    assert "-copyts" in cmd and "trim=start=0:duration=5.000" in cmd[cmd.index("-vf") + 1]
    look = ffmpeg.Look(1280, 720)
    enc = ffmpeg.build_encode_clip_cmd("ffmpeg", "l.ffconcat", 0, 5, "o", look, is_concat=True)
    assert "-copyts" in enc and "-ss" not in enc
    assert enc[enc.index("-vf") + 1].startswith("trim=start=0")
    assert enc[enc.index("-af") + 1].startswith("atrim=start=0")


def test_concat_list_inpoint(tmp_path):
    text = ffmpeg.write_concat_list([tmp_path / "a.mp4", tmp_path / "b.mp4"],
                                    tmp_path / "l.txt", inpoint=95.5).read_text()
    lines = text.splitlines()
    assert lines[1].endswith("a.mp4'") and lines[2] == "inpoint 95.500"
    assert not any(line.startswith("inpoint") for line in lines[3:])


def test_gopro_chapter_order():
    files = ["GX020123.MP4", "GX010124.MP4", "GX010123.MP4", "other.mp4"]
    assert [p.name for p in ffmpeg.sort_parts(files)] == [
        "GX010123.MP4", "GX020123.MP4", "GX010124.MP4", "other.mp4"]


def test_concat_list_escapes_quotes(tmp_path):
    f = tmp_path / "it's.mp4"
    out = ffmpeg.write_concat_list([f], tmp_path / "l.txt")
    assert r"it'\''s.mp4'" in out.read_text()


def test_parse_probe_flags_pcm_audio():
    data = {"format": {"duration": "2700.5", "format_name": "mov,mp4", "size": "9000000000"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920,
                 "height": 1080, "avg_frame_rate": "50/1"},
                {"codec_type": "audio", "codec_name": "pcm_s16le"},
                {"codec_type": "data", "codec_name": "bin_data"}]}
    info = ffmpeg.parse_probe("x.mov", data)
    assert info.duration == 2700.5 and info.fps == 50.0
    assert info.audio_needs_reencode


def test_run_surfaces_stderr(monkeypatch):
    class FakeProc:
        stdout = iter([b"out_time_us=500000\n"])
        def __init__(self, cmd, stdout, stderr, stdin, **kw):
            stderr.write(b"Invalid data found when processing input\n")
        def wait(self):
            return 1
    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    seen = []
    with pytest.raises(ffmpeg.FFmpegError) as e:
        ffmpeg.run(["ffmpeg"], seen.append)
    assert seen == [0.5]
    assert "Invalid data found" in str(e.value)


def test_probe_uses_arg_list_not_shell(monkeypatch, tmp_path):
    video = tmp_path / "a b.mp4"
    video.write_bytes(b"")
    calls = []
    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"format": {"duration": "10"}}), "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("HCLIP_FFPROBE", "ffprobe")
    assert ffmpeg.probe(video).duration == 10
    cmd, kw = calls[0]
    assert isinstance(cmd, list) and cmd[-1] == str(video) and not kw.get("shell")


def test_find_binary_env_override(monkeypatch):
    monkeypatch.setenv("HCLIP_FFMPEG", "/opt/ff/ffmpeg")
    assert ffmpeg.find_binary("ffmpeg") == "/opt/ff/ffmpeg"


def test_find_binary_prefers_bundled_copy(monkeypatch, tmp_path):
    monkeypatch.delenv("HCLIP_FFPROBE", raising=False)
    exe = tmp_path / "ffmpeg" / ffmpeg._exe("ffprobe")
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    assert ffmpeg.find_binary("ffprobe") == str(exe)


def test_hevc_gets_apple_tag_in_copy_mode_only():
    cmd = ffmpeg.build_cut_cmd("ffmpeg", "iphone.mov", 0, 5, "o", hevc=True)
    assert cmd[cmd.index("-tag:v") + 1] == "hvc1"
    assert "-tag:v" not in ffmpeg.build_cut_cmd("ffmpeg", "i.mov", 0, 5, "o", hevc=True, precise=True)


def test_stalled_ffmpeg_is_killed_even_when_silent(tmp_path):
    """A process that hangs without printing anything is still stopped."""
    import sys, time as _t
    t = _t.monotonic()
    with pytest.raises(ffmpeg.FFmpegError, match="stopped making progress"):
        ffmpeg.run([sys.executable, "-c", "import time; time.sleep(30)"], stall_seconds=1)
    assert _t.monotonic() - t < 5


def test_stop_is_prompt_even_when_silent():
    import sys, time as _t
    t = _t.monotonic()
    with pytest.raises(ffmpeg.Cancelled):
        ffmpeg.run([sys.executable, "-c", "import time; time.sleep(30)"],
                   should_stop=lambda: _t.monotonic() - t > 0.5)
    assert _t.monotonic() - t < 3


def test_card_input_is_time_limited():
    cmd = ffmpeg.build_card_cmd("ffmpeg", "c.png", 3, "o.mp4", ffmpeg.Look(320, 180))
    i = cmd.index("-i")
    assert cmd[i - 2:i] == ["-t", "3.000"]
    j = cmd.index("anullsrc=r=48000:cl=stereo")
    assert cmd[j - 5:j - 3] == ["-t", "3.000"]   # the silence ends too
