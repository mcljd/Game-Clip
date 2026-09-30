"""Cutting a checked clip list into reels, with real ffmpeg (hclip/cutter.py)."""

import subprocess
from pathlib import Path

import pytest

from hclip import clipplan, cutter, ffmpeg
from tests.conftest import make_video, needs_ffmpeg

pytestmark = needs_ffmpeg
LABELS = ["1st half", "2nd half"]

NOTE = """reel 1
1st half
0:03-0:05
0:40-0:44 Point - Conor
2nd Half
0:10-0:13

reel 2 Video
1st half
0:20-0:22
2nd half
0:30-0:33
"""


def _jobs(note, videos, pad_end=1.0, choices=None, names=("bc clip 1", "bc clip 2")):
    plan = clipplan.parse(note, LABELS, choices)
    chk = clipplan.check(plan, [ffmpeg.probe(v).duration for v in videos], pad_end=pad_end)
    assert chk.can_cut, chk.errors
    return [cutter.ReelJob(r.name, names[i], [c for c in chk.cuts if c.row.reel == i])
            for i, r in enumerate(plan.reels)]


def _decodes(path: Path) -> bool:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                       capture_output=True, text=True)
    return r.stderr.strip() == ""


def test_reels_mix_both_videos_in_order_and_verify(videos, tmp_path):
    srcs = [videos["h1"], videos["h2"]]
    jobs = _jobs(NOTE, srcs)
    progress = []
    results = cutter.run(jobs, srcs, LABELS, [ffmpeg.probe(v) for v in srcs], tmp_path / "out",
                         work_root=tmp_path / "work", preset="ultrafast",
                         on_progress=lambda p: progress.append(p.text))
    assert [r.status for r in results] == ["made", "made"], [r.error for r in results]
    assert all(r.ok for r in results), cutter.summary(results)
    # Clip lengths are the range + 1 s pad; the reel is the sum of its pieces.
    assert [c.expected for c in results[0].clips] == [3, 5, 4]
    assert [c.expected for c in results[1].clips] == [3, 4]
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert names == ["bc clip 1 - 01.mp4", "bc clip 1 - 02.mp4", "bc clip 1 - 03.mp4", "bc clip 1.mp4",
                     "bc clip 2 - 01.mp4", "bc clip 2 - 02.mp4", "bc clip 2.mp4"]
    assert all(_decodes(tmp_path / "out" / n) for n in names)
    reel = ffmpeg.probe(tmp_path / "out" / "bc clip 1.mp4")
    assert (reel.video_codec, reel.audio_codec, reel.sample_rate) == ("h264", "aac", 48000)
    assert "reel 1, clip 3/3, 2nd half 0:10-0:13" in progress
    assert not list((tmp_path / "work").iterdir())            # temp work cleared


def _frame(path, at: float) -> bytes:
    """One frame as small greyscale pixels."""
    return subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
                           "-vf", "scale=80:45,format=gray", "-f", "rawvideo", "-"],
                          capture_output=True, check=True).stdout


def _diff(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def test_clips_start_exactly_on_the_asked_time(videos, tmp_path):
    # Keyframes are every 2 s (0:40, 0:42); a stream copy of 0:41 would start
    # at 0:40. testsrc draws the running second, so frames a second apart differ.
    srcs = [videos["h1"], videos["h2"]]
    jobs = _jobs("1st half\n0:41-0:43", srcs, pad_end=0, names=("x",))
    cutter.run(jobs, srcs, LABELS, [ffmpeg.probe(v) for v in srcs], tmp_path, preset="ultrafast")
    clip = tmp_path / "x - 01.mp4"
    assert abs(ffmpeg.probe(clip).duration - 2) < cutter.CLIP_TOLERANCE
    first = _frame(clip, 0)
    assert _diff(first, _frame(srcs[0], 41)) < _diff(first, _frame(srcs[0], 40)) / 4


def test_text_screens_go_before_their_clip(videos, tmp_path):
    png = tmp_path / "card.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=320x180",
                    "-frames:v", "1", str(png)], check=True)
    srcs = [videos["h1"], videos["h2"]]
    jobs = _jobs(NOTE, srcs)
    key = next(c.row.key for c in jobs[0].cuts if c.row.text)
    [first, _] = cutter.run(jobs, srcs, LABELS, [ffmpeg.probe(v) for v in srcs], tmp_path / "out",
                            cards={key: png}, card_seconds=2, preset="ultrafast")
    assert first.ok, cutter.summary([first])
    assert abs(first.measured - (3 + 5 + 4 + 2)) < 0.2       # three clips plus one 2 s screen
    assert abs(ffmpeg.probe(tmp_path / "out" / "bc clip 1 - 02.mp4").duration - 5) < 0.1  # clip file has no screen


def test_missing_source_is_named_and_other_reels_still_made(videos, tmp_path):
    gone = tmp_path / "2nd half moved.mp4"
    gone.write_bytes(videos["h2"].read_bytes())
    srcs = [videos["h1"], gone]
    infos = [ffmpeg.probe(v) for v in srcs]
    jobs = _jobs("reel 1\n2nd half\n0:10-0:12\nreel 2\n1st half\n0:03-0:05", srcs)
    gone.unlink()                                             # moved while the app was open
    r1, r2 = cutter.run(jobs, srcs, LABELS, infos, tmp_path / "out", preset="ultrafast")
    assert r1.status == "failed" and "“2nd half moved.mp4”" in r1.error and "2nd half" in r1.error
    assert r2.status == "made" and r2.ok
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["bc clip 2 - 01.mp4", "bc clip 2.mp4"]


def test_different_sources_are_encoded_to_one_format(videos, tmp_path):
    other = make_video(tmp_path / "phone.mp4", 20)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(other), "-vf", "scale=640:360,fps=30",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-ar", "44100",
                    str(tmp_path / "phone2.mp4")], check=True)
    srcs = [videos["h1"], tmp_path / "phone2.mp4"]
    jobs = _jobs("1st half\n0:03-0:05\n2nd half\n0:05-0:08", srcs, names=("mixed",))
    [r] = cutter.run(jobs, srcs, LABELS, [ffmpeg.probe(v) for v in srcs], tmp_path / "out",
                     preset="ultrafast")
    assert r.ok, cutter.summary([r])
    reel = ffmpeg.probe(tmp_path / "out" / "mixed.mp4")
    assert (reel.width, reel.height, reel.sample_rate) == (320, 180, 48000)   # the first video's size
    assert _decodes(tmp_path / "out" / "mixed.mp4")


def test_stop_leaves_no_finished_looking_files(videos, tmp_path):
    srcs = [videos["h1"], videos["h2"]]
    jobs = _jobs("1st half\n0:00-0:50", srcs, names=("long",))
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 3

    [r] = cutter.run(jobs, srcs, LABELS, [ffmpeg.probe(v) for v in srcs], tmp_path / "out",
                     work_root=tmp_path / "work", should_stop=stop)
    assert r.status == "stopped"
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())
    assert not list((tmp_path / "work").iterdir())
