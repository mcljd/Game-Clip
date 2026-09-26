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


def _png_b64(tmp_path):
    import base64, subprocess
    png = tmp_path / "card.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=320x180",
                    "-frames:v", "1", str(png)], check=True)
    return "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()


@needs_ffmpeg
def test_half_reels_like_the_window(videos, tmp_path):
    picks = iter([[str(videos["h1"])], [str(videos["h2"])]])
    api = Api(pick_files=lambda: next(picks))
    assert api.choose_videos(0)["duration"] == "1:00"
    assert api.choose_videos(1)["files"] == ["second_half.mp4"]
    assert api.default_folder("Test match").endswith("Test match highlights")
    out = str(tmp_path / "Test match highlights")
    chk = api.check(NOTE, out, "Test match")
    assert chk["ready"] and chk["clips"] == 3 and not chk["encode"]
    assert [r["name"] for r in chk["reels"]] == ["1st Half", "2nd half"]
    assert chk["whole"]["clips"] == 3 and chk["look"] == {"width": 320, "height": 180}

    assert api.start(NOTE, out, "Test match", "halves")["ok"]
    api.wait(120)
    st = api.status()
    assert st["finished"] and not st["error"], st
    assert [(r["file"], r["status"]) for r in st["reels"]] == [
        ("Test match - 1st Half.mp4", "made"), ("Test match - 2nd half.mp4", "made")]

    # Pressing Make again rebuilds (the text or clips may have changed).
    api.start(NOTE, out, "Test match", "halves")
    api.wait(60)
    assert api.status()["made"] == 2


@needs_ffmpeg
def test_one_video_with_text_screens(videos, tmp_path):
    from hclip import ffmpeg
    api = Api()
    api.set_videos(0, [str(videos["h1"])])
    api.set_videos(1, [str(videos["h2"])])
    note = "Test match\n0:10-0:16 Point - Conor\n0:31-0:40\n2nd half\n0:05-0:12 Goal\n"
    chk = api.check(note, str(tmp_path), "Test match", cards=True)
    assert chk["labelled"] == 2 and chk["encode"]
    card = _png_b64(tmp_path)
    api.start(note, str(tmp_path), "Test match", "whole", cards={"2": card, "5": card},
              card_seconds=2, keep_clips=True)
    api.wait(120)
    st = api.status()
    assert [r["status"] for r in st["reels"]] == ["made"], st
    whole = tmp_path / "Test match - Whole match.mp4"
    assert abs(ffmpeg.probe(whole).duration - (6 + 9 + 7 + 2 + 2)) < 0.5
    assert len(list((tmp_path / "Clips").glob("*.mp4"))) == 3


@needs_ffmpeg
def test_cancel_stops_mid_clip_and_leaves_no_part(videos, tmp_path):
    api = Api()
    api.set_videos(0, [str(videos["h1"])])
    api.start("0:00-0:59", str(tmp_path), "T", "halves", precise=True)
    deadline = time.time() + 10
    while api.status()["overall"] == 0 and time.time() < deadline:
        time.sleep(0.05)
    api.cancel()
    api.wait(30)
    st = api.status()
    assert st["cancelled"] and st["made"] == 0
    assert not list(tmp_path.glob("*.mp4")) and not list(tmp_path.rglob("*.part"))


@needs_ffmpeg
def test_double_click_starts_only_one_run(videos, tmp_path):
    import threading
    api = Api()
    api.set_videos(0, [str(videos["h1"])])
    out = []
    barrier = threading.Barrier(2)
    def go():
        barrier.wait()
        out.append(api.start("0:10-0:16\n0:31-0:40", str(tmp_path), "T", "halves"))
    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    api.wait(60)
    assert sorted("ok" in r for r in out) == [False, True]
    assert [r["status"] for r in api.status()["reels"]] == ["made"]
