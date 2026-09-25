import time

import pytest

from hclip.app.api import Api, _safe_folder
from tests.conftest import needs_ffmpeg

NOTE = "Test match\n0:10-0:16\n0:31-0:40\n2nd half\n0:05-0:12\n"


def test_read_note_is_json_friendly():
    r = Api().read_note(NOTE)
    assert r["title"] == "Test match" and r["clip_count"] == 3
    assert r["sections"][0]["clips"][0] == {
        "start": "0:10", "end": "0:16", "length": "0:06", "label": "", "line": 2}


def test_check_asks_for_missing_videos():
    api = Api()
    assert api.check("", "x")["message"].startswith("Paste")
    msg = api.check(NOTE, "x")["message"]
    assert "1st Half" in msg and "2nd half" in msg


def test_safe_folder_keeps_coach_wording():
    assert _safe_folder("Dunloy v Loughgiel clips") == "Dunloy v Loughgiel clips"
    assert _safe_folder('A/B: "final"?') == "A_B_ _final__"


def test_bad_file_gives_friendly_error(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello")
    r = Api().set_videos(0, [str(f)])
    assert r["error"].startswith("Couldn't open")


@needs_ffmpeg
def test_full_flow_like_the_window(videos, tmp_path):
    picks = iter([[str(videos["h1"])], [str(videos["h2"])]])
    api = Api(pick_files=lambda: next(picks))
    assert api.choose_videos(0)["duration"] == "1:00"
    assert api.choose_videos(1)["files"] == ["second_half.mp4"]
    out = api.default_folder("Test match")
    assert out.endswith("Test match clips")
    out = str(tmp_path / "Test match clips")
    chk = api.check(NOTE, out)
    assert chk["ready"] and chk["clips"] == 3 and chk["existing"] == 0

    assert api.start(NOTE, out)["ok"]
    api.wait(60)
    st = api.status()
    assert st["finished"] and not st["running"]
    assert (st["made"], st["skipped"], st["failed"]) == (3, 0, [])

    assert api.check(NOTE, out)["existing"] == 3
    api.start(NOTE, out)
    api.wait(60)
    assert api.status()["skipped"] == 3


@needs_ffmpeg
def test_cancel_stops_mid_clip_and_leaves_no_part(videos, tmp_path):
    api = Api()
    api.set_videos(0, [str(videos["h1"])])
    note = "0:00-0:59"  # one long precise clip so there's time to cancel
    api.start(note, str(tmp_path), precise=True)
    deadline = time.time() + 10
    while api.status()["overall"] == 0 and time.time() < deadline:
        time.sleep(0.05)
    api.cancel()
    api.wait(30)
    st = api.status()
    assert st["cancelled"] and st["made"] == 0
    assert list(tmp_path.iterdir()) == []
