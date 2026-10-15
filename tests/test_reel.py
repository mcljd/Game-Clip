import subprocess
from pathlib import Path

import pytest

from hclip import cut, ffmpeg, notes, reel
from tests.conftest import make_video, needs_ffmpeg

NOTE = "Test match\n0:10-0:16\n0:31-0:40 Goal - Cúl báire\n2nd half\n0:05-0:12 Point\n"


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
    assert reel.reel_filename("Home v Away", "1st Half") == "Home v Away - 1st Half.mp4"
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
    assert ffmpeg.Look.for_source(info, max_height=720) == ffmpeg.Look(1278, 720, "50")


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
    # Stopped part-way: some pieces kept (3 in total), nothing half-written.
    assert 1 <= len(made) < 3 and not list(work.glob("*.part"))

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


@pytest.mark.parametrize("rate,expected", [
    ("115200000/4608001", "25"), ("30000/1001", "30000/1001"), ("143640/4789", "30"), ("2997/100", "30000/1001"),
    ("50/1", "50"), ("240/1", "60"), ("0/0", "25"), (None, "25"), ("12/1", "12"), (59.94, "60000/1001"),
])
def test_standard_fps(rate, expected):
    assert ffmpeg.standard_fps(rate) == expected


def test_nominal_frame_rate_ignores_timebase_guesses():
    v = {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
         "r_frame_rate": "90000/1", "avg_frame_rate": "30000/1001", "profile": "High", "level": 42}
    info = ffmpeg.parse_probe("x", {"format": {"duration": "10"}, "streams": [v]})
    assert info.fps_str == "30000/1001"
    v2 = dict(v, r_frame_rate="30/1", avg_frame_rate="143640/4789")   # VFR phone
    assert ffmpeg.parse_probe("x", {"format": {}, "streams": [v2]}).fps_str == "30/1"


def test_join_key_compares_standard_rate_and_profile():
    base = dict(path="x", duration=1, container="mp4", video_codec="h264", width=1920, height=1080,
                fps=29.98, audio_codec="aac", size_bytes=0, pix_fmt="yuv420p", sample_rate=48000,
                channels=2, profile="High", level=42)
    a = ffmpeg.MediaInfo(**base, fps_str="2998/100")
    b = ffmpeg.MediaInfo(**base, fps_str="30000/1001")
    assert a.join_key() == b.join_key()                     # both really 29.97
    c = ffmpeg.MediaInfo(**{**base, "profile": "Main"}, fps_str="30000/1001")
    assert a.join_key() != c.join_key()


def test_duplicate_video_names_get_their_own_reels(videos, tmp_path):
    note, jobs = _jobs("Video T\n0:01-0:03\n2nd Video\n0:04-0:06\n2nd Video\n0:07-0:09",
                       [[videos["h1"]]], tmp_path)
    reels = reel.plan_reels(note, jobs, tmp_path, "Video T", halves=True)
    assert [r.out.name for r in reels] == [
        "Video T.mp4", "Video T - 2nd Video.mp4", "Video T - 2nd Video (2).mp4"]


MIXED_HALVES = """Video Final
0:03-0:05
0:40-0:44
2nd Half
0:10-0:13
2nd Video
0:20-0:22
2nd half
0:30-0:33 Goal
"""


@needs_ffmpeg
def test_videos_that_mix_both_halves(videos, tmp_path):
    # Real note shape: two recordings (the halves), two videos to make, each
    # with clips from both halves in the order they're written.
    note, jobs = _jobs(MIXED_HALVES, [[videos["h1"]], [videos["h2"]]], tmp_path)
    assert [s.tag for s in note.sections] == ["h1", "h2"]
    reels = reel.plan_reels(note, jobs, tmp_path, "Video Final", halves=True)
    assert [r.out.name for r in reels] == ["Video Final.mp4", "Video Final - 2nd Video.mp4"]
    assert [[(j.section_tag, j.clip.line) for j in r.jobs] for r in reels] == [
        [("h1", 2), ("h1", 3), ("h2", 5)], [("h1", 7), ("h2", 9)]]
    [whole] = reel.plan_reels(note, jobs, tmp_path, "Video Final", halves=False, whole=True)
    assert [j.clip.line for j in whole.jobs] == [2, 3, 5, 7, 9]
    results = reel.run_reels(reels, card_images={}, force=True, preset="ultrafast")
    assert [r.status for r in results] == ["made", "made"], [r.error for r in results]
    first, second = (_dur(r.reel.out) for r in results)
    assert 9 - 0.3 <= first <= 9 + 3 * 2.1     # 2 + 4 + 3 s, plus keyframe lead-ins
    assert 5 - 0.3 <= second <= 5 + 2 * 2.1


@needs_ffmpeg
def test_kept_clips_are_never_stale(videos, tmp_path):
    note, jobs = _jobs("0:11-0:16", [[videos["h1"]]], tmp_path)
    reels = reel.plan_reels(note, jobs, tmp_path, "T", halves=True)
    keep = tmp_path / "Clips"
    reel.run_reels(reels, keep_clips_dir=keep, force=True)
    [clip] = keep.iterdir()
    assert _dur(clip) > 5.5                  # copy starts on the keyframe at 0:10
    reel.run_reels(reels, keep_clips_dir=keep, force=True, precise=True)
    assert abs(_dur(clip) - 5) < 0.15        # re-made exactly, not the old file


@needs_ffmpeg
def test_locked_reel_file_is_reported_and_others_still_made(videos, tmp_path, monkeypatch):
    note, jobs = _jobs(NOTE, [[videos["h1"]], [videos["h2"]]], tmp_path)
    reels = reel.plan_reels(note, jobs, tmp_path, "T", halves=True)
    real = reel.os.replace

    def replace(src, dst):
        if str(dst).endswith("T - 1st Half.mp4"):
            raise PermissionError(13, "The process cannot access the file")
        return real(src, dst)
    monkeypatch.setattr(reel.os, "replace", replace)
    first, second = reel.run_reels(reels)
    assert first.status == "failed" and "close it and try again" in first.error
    assert second.status == "made"


@needs_ffmpeg
def test_half_reels_copy_each_half_even_if_cameras_differ(videos, tmp_path):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(videos["h2"]), "-vf", "scale=640:360",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "50", "-c:a", "aac",
                    str(tmp_path / "phone640.mp4")], check=True)
    note, jobs = _jobs("0:10-0:14\n2nd half\n0:02-0:05", [[videos["h1"]], [tmp_path / "phone640.mp4"]], tmp_path / "o")
    h1, h2 = reel.plan_reels(note, jobs, tmp_path / "o", "M", halves=True)
    res = reel.run_reels([h1, h2])
    assert [r.status for r in res] == ["made", "made"]
    assert ffmpeg.probe(h2.out).width == 640      # copied as filmed, not re-encoded to 320


@needs_ffmpeg
def test_failed_text_screen_keeps_the_clip(videos, tmp_path):
    note, jobs = _jobs("0:10-0:16 A\n0:31-0:40 B", [[videos["h1"]]], tmp_path)
    reels = reel.plan_reels(note, jobs, tmp_path, "T", halves=True)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png")
    [res] = reel.run_reels(reels, card_images={1: bad, 2: black_card(tmp_path / "ok.png")}, card_seconds=2)
    assert res.status == "made"
    assert res.missing == () and res.missing_screens == (1,)
    assert abs(_dur(reels[0].out) - (6 + 9 + 2)) < 0.5
