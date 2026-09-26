import subprocess
from pathlib import Path

import pytest

from hclip import cut, ffmpeg, notes, reel
from tests.conftest import make_video, needs_ffmpeg

NOTE = "Test match\n0:10-0:16\n0:31-0:40 Goal - Seán\n2nd half\n0:05-0:12 Point\n"


def _jobs(note_text, halves, out):
    note = notes.parse(note_text)
    return note, cut.plan(note, [cut.HalfVideo.open(h) for h in halves], out).jobs


def _dur(p: Path) -> float:
    return ffmpeg.probe(p).duration


def _decodes_cleanly(p: Path) -> str:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-f", "null", "-"],
                       capture_output=True, text=True)
    return r.stderr.strip()


def black_card(path: Path, size="320x180") -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=black:s={size}",
                    "-frames:v", "1", str(path)], check=True)
    return path


def test_reel_names_are_windows_safe():
    assert reel.reel_filename("Dunloy v Loughgiel", "1st Half") == "Dunloy v Loughgiel - 1st Half.mp4"
    assert reel.reel_filename('A/B: "final"', "2nd Half?") == "A_B_ _final_ - 2nd Half_.mp4"
    assert reel.reel_filename("", "Whole match") == "Match - Whole match.mp4"


def test_encode_commands_share_one_look():
    look = ffmpeg.Look(1280, 720, "30000/1001")
    clip = ffmpeg.build_encode_clip_cmd("ffmpeg", "in.mp4", 10, 5, "o.mp4", look)
    card = ffmpeg.build_card_cmd("ffmpeg", "card.png", 3, "c.mp4", look)
    tail = lambda cmd: cmd[cmd.index("-c:v"):cmd.index("-progress")]
    assert tail(clip) == tail(card)  # identical encode settings -> clean joins
    assert "fps=30000/1001" in clip[clip.index("-vf") + 1]
    silent = ffmpeg.build_encode_clip_cmd("ffmpeg", "in.mp4", 0, 5, "o", look, has_audio=False)
    assert "anullsrc=r=48000:cl=stereo" in silent and silent[silent.index("-map", silent.index("-map") + 1) + 1] == "1:a:0"


def test_look_caps_height_and_keeps_even_sizes():
    info = ffmpeg.MediaInfo("x", 1, "mp4", "h264", 1918, 1080, 50.0, "aac", 0, fps_str="50/1")
    assert ffmpeg.Look.for_source(info, max_height=720) == ffmpeg.Look(1278, 720, "50/1")


@needs_ffmpeg
def test_half_reels_by_copy(videos, tmp_path):
    note, jobs = _jobs(NOTE, [[videos["h1"]], [videos["h2"]]], tmp_path)
    reels = reel.plan_reels(note, jobs, tmp_path, "Test match", halves=True)
    assert [r.out.name for r in reels] == ["Test match - 1st Half.mp4", "Test match - 2nd half.mp4"]
    results = reel.run_reels(reels)
    assert [r.status for r in results] == ["made", "made"]
    # clips 6s + 9s; stream copy can start each up to one 2s GOP early
    assert 15 - 0.3 <= _dur(reels[0].out) <= 15 + 4.5
    assert 7 - 0.3 <= _dur(reels[1].out) <= 7 + 2.3
    assert _decodes_cleanly(reels[0].out) == ""
    assert not (tmp_path / reel.WORK_DIR).exists()      # tidied up
    assert sorted(p.name for p in tmp_path.iterdir()) == [r.out.name for r in reels]

    again = reel.run_reels(reels)
    assert [r.status for r in again] == ["skipped", "skipped"]


@needs_ffmpeg
def test_whole_match_with_text_screens(videos, tmp_path):
    note, jobs = _jobs(NOTE, [[videos["h1"]], [videos["h2"]]], tmp_path)
    [whole] = reel.plan_reels(note, jobs, tmp_path, "Test match", halves=False, whole=True)
    cards = {3: black_card(tmp_path / "c3.png"), 5: black_card(tmp_path / "c5.png")}
    [res] = reel.run_reels([whole], card_images=cards, card_seconds=2)
    assert res.status == "made", res.error
    # Encoded, so frame-accurate: 6 + 9 + 7 seconds of clips + two 2s screens
    assert abs(_dur(whole.out) - 26) < 0.5
    info = ffmpeg.probe(whole.out)
    assert (info.video_codec, info.audio_codec, info.width) == ("h264", "aac", 320)
    assert _decodes_cleanly(whole.out) == ""


@needs_ffmpeg
def test_different_cameras_are_re_encoded_to_one_look(videos, tmp_path):
    other = make_video(tmp_path / "phone.mp4", 20)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(other), "-vf", "scale=640:360",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(tmp_path / "phone640.mp4")], check=True)
    note, jobs = _jobs("0:10-0:14\n2nd half\n0:02-0:05", [[videos["h1"]], [tmp_path / "phone640.mp4"]], tmp_path / "out")
    assert not reel.can_copy(jobs)
    [whole] = reel.plan_reels(note, jobs, tmp_path / "out", "Mixed", halves=False, whole=True)
    [res] = reel.run_reels([whole])
    assert res.status == "made", res.error
    assert ffmpeg.probe(whole.out).width == 320           # first half's look
    assert abs(_dur(whole.out) - 7) < 0.4
    assert _decodes_cleanly(whole.out) == ""


@needs_ffmpeg
def test_stop_keeps_finished_pieces_and_resume_reuses_them(videos, tmp_path):
    note, jobs = _jobs(NOTE, [[videos["h1"]], [videos["h2"]]], tmp_path)
    reels = reel.plan_reels(note, jobs, tmp_path, "T", halves=True)
    stages = []
    stop = lambda: len([s for s in stages if s.startswith("Making piece 2")]) > 0

    res = reel.run_reels(reels, on_progress=lambda p: stages.append(p.stage), should_stop=stop)
    assert all(r.status == "failed" for r in res)
    work = tmp_path / reel.WORK_DIR
    made = [p for p in work.glob("clip_*.mp4")]
    assert len(made) == 1 and not list(work.glob("*.part"))

    stages.clear()
    res = reel.run_reels(reels, on_progress=lambda p: stages.append(p.stage))
    assert [r.status for r in res] == ["made", "made"]
    assert not any(s.startswith("Making piece 1 ") for s in stages)  # reused


@needs_ffmpeg
def test_keep_clips_too(videos, tmp_path):
    note, jobs = _jobs(NOTE, [[videos["h1"]], [videos["h2"]]], tmp_path)
    reels = reel.plan_reels(note, jobs, tmp_path, "T", halves=True)
    reel.run_reels(reels, keep_clips_dir=tmp_path / "clips")
    assert sorted(p.name for p in (tmp_path / "clips").iterdir()) == [j.out.name for j in jobs]
