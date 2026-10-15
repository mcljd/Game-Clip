"""The window's Python side (hclip/app/api.py), without a window."""

import time

import pytest

from hclip.app.api import Api, _safe_folder, default_label, in_onedrive
from tests.conftest import needs_ffmpeg
from tests.test_clipplan import ACCEPTANCE, NO_HEADERS

NOTE = "reel 1\n1st half\n0:10-0:16\n2nd half\n0:05-0:12\nreel 2\n1st half\n0:31-0:40\n"


def test_default_labels():
    assert [default_label(i) for i in range(4)] == ["1st half", "2nd half", "Video 3", "Video 4"]


def test_safe_folder_keeps_coach_wording():
    assert _safe_folder("Home v Away clips") == "Home v Away clips"
    assert _safe_folder('A/B: "final"?') == "A_B_ _final__"


def test_onedrive_output_is_flagged():
    api = Api()
    assert in_onedrive(r"C:\Users\Coach\OneDrive\Hurling Clips")
    assert "OneDrive" in api.folder_note(r"C:\Users\Coach\OneDrive\Hurling Clips")
    assert api.folder_note(r"C:\Clips") == ""
    assert "onedrive" not in api.default_folder().lower()


def test_plan_asks_for_videos_first():
    api = Api()
    p = api.plan(ACCEPTANCE)
    assert not p["can_cut"] and p["message"] == "Add the videos."
    assert all(r["needs_video"] for reel in p["reels"] for r in reel["rows"])


def test_bad_file_gives_friendly_error(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello")
    r = Api().add_video_paths([str(f)])
    assert r["videos"] == [] and r["errors"][0].startswith("Couldn't open “notes.txt”")


@needs_ffmpeg
def test_videos_are_labelled_in_name_order(videos):
    api = Api()
    r = api.add_video_paths([str(videos["h2"]), str(videos["h1"])])   # "second_half", "first half"
    assert [(v["label"], v["file"]) for v in r["videos"]] == [
        ("1st half", "first half.mp4"), ("2nd half", "second_half.mp4")]
    assert r["videos"][0]["duration"] == "1:00"
    assert api.set_label(1, "Second half (wide)")[1]["label"] == "Second half (wide)"
    assert [v["file"] for v in api.move_video(0, 1)] == ["second_half.mp4", "first half.mp4"]
    assert [v["file"] for v in api.remove_video(0)] == ["first half.mp4"]


@needs_ffmpeg
def test_acceptance_plan_through_the_api(videos):
    api = Api()
    api.add_video_paths([str(videos["h1"]), str(videos["h2"])])
    # Lengths against 60 s / 40 s test videos: most ranges are past their end.
    p = api.plan(ACCEPTANCE, {}, 0, 1)
    assert [len(r["rows"]) for r in p["reels"]] == [9, 8]
    assert [r["video"] for r in p["reels"][0]["rows"]] == [0] * 5 + [1] * 4
    assert not p["can_cut"] and "marked in red" in p["message"]


@needs_ffmpeg
def test_without_headers_cut_is_blocked_until_every_clip_has_a_video(videos):
    api = Api()
    api.add_video_paths([str(videos["h1"]), str(videos["h2"])])
    text = "0:10-0:16\n0:20-0:25\n0:05-0:08"
    p = api.plan(text)
    assert not p["can_cut"] and p["message"] == "Choose a video for 3 clips (highlighted)."
    keys = [r["key"] for r in p["reels"][0]["rows"]]
    p = api.plan(text, {keys[0]: 0, keys[1]: 0})
    assert p["message"] == "Choose a video for 1 clip (highlighted)."
    p = api.plan(text, {keys[0]: 0, keys[1]: 0, keys[2]: 1})
    assert p["can_cut"] and p["reels"][0]["rows"][2]["chosen_by"] == "you"
    assert api.start(text, {keys[0]: 0}, 0, 1, "x")["error"].startswith("Choose a video")


def _wait(api, timeout=120):
    end = time.time() + timeout
    while api.status()["running"] and time.time() < end:
        time.sleep(0.1)
    return api.status()


@needs_ffmpeg
def test_cut_like_the_window(videos, tmp_path):
    api = Api()
    api.add_video_paths([str(videos["h1"]), str(videos["h2"])])
    p = api.plan(NOTE, {}, 0, 1)
    assert p["can_cut"], p["message"]
    res = api.start(NOTE, {}, 0, 1, str(tmp_path), ["game clip 1", "game clip 2"])
    assert res == {"ok": True}
    st = _wait(api)
    assert not st["error"] and [r["status"] for r in st["results"]] == ["made", "made"]
    assert all(r["ok"] for r in st["results"])
    assert [c["expected"] for c in st["results"][0]["clips"]] == [7, 8]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "game clip 1 - 01.mp4", "game clip 1 - 02.mp4", "game clip 1.mp4", "game clip 2 - 01.mp4", "game clip 2.mp4"]


@needs_ffmpeg
def test_double_click_starts_only_one_run(videos, tmp_path):
    api = Api()
    api.add_video_paths([str(videos["h1"]), str(videos["h2"])])
    first = api.start(NOTE, {}, 0, 1, str(tmp_path))
    second = api.start(NOTE, {}, 0, 1, str(tmp_path))
    assert first == {"ok": True} and second["error"] == "Already cutting."
    api.cancel()
    _wait(api)


@needs_ffmpeg
def test_stop(videos, tmp_path):
    # Stopping mid-clip is covered in test_cutter; this checks the window's wiring.
    api = Api()
    api.add_video_paths([str(videos["h1"]), str(videos["h2"])])
    api.start("1st half\n0:00-0:55", {}, 0, 1, str(tmp_path / "out"))
    api.cancel()
    st = _wait(api)
    assert st["cancelled"] and st["results"][0]["status"] == "stopped"
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())
