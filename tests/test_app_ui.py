"""The Windows window (hclip/app/ui.html) in headless Chromium, wired to the
real Python Api the way pywebview wires it. Skipped without Playwright."""

import os

import pytest

from hclip.app.api import Api
from tests.conftest import needs_ffmpeg
from tests.test_clipplan import ACCEPTANCE, NO_HEADERS

UI = os.path.join(os.path.dirname(__file__), "..", "hclip", "app", "ui.html")
playwright = pytest.importorskip("playwright.sync_api")
pytestmark = needs_ffmpeg

# pywebview exposes each Api method as a promise-returning function.
BRIDGE = """
window.pywebview = { api: new Proxy({}, { get: (_, name) => (...args) => window.__py(name, args) }) };
"""


@pytest.fixture()
def window(videos, tmp_path):
    picks = [[str(videos["h2"]), str(videos["h1"])]]
    api = Api(pick_files=lambda: picks.pop(0), pick_folder=lambda: str(tmp_path / "out"))
    with playwright.sync_playwright() as p:
        exe = os.environ.get("HCLIP_CHROMIUM")
        try:
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        except playwright.Error:
            if os.environ.get("CI"):
                raise
            pytest.skip("no Chromium for Playwright; set HCLIP_CHROMIUM")
        page = browser.new_page(viewport={"width": 1200, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.expose_function("__py", lambda name, args: getattr(api, name)(*args))
        page.add_init_script(BRIDGE)
        page.goto("file://" + os.path.abspath(UI))
        page.wait_for_function("document.getElementById('outDir').value !== ''")   # started
        page.click("#addVideos")
        page.wait_for_selector("#videos .video:nth-child(2)")
        yield page, tmp_path
        api.cancel()
        api.wait(10)
        browser.close()
        assert errors == []


def paste(page, text):
    page.fill("#note", text)
    page.wait_for_timeout(500)


def checked_videos(page):
    """For each clip row, in order: the checked radio's label, or None."""
    return page.evaluate("""() => [...document.querySelectorAll('#reels tr[data-row]')].map((tr) => {
        const r = tr.querySelector('input[type=radio]:checked');
        return r ? r.parentElement.textContent.trim() : null; })""")


def test_acceptance_prefill_in_the_window(window):
    page, _ = window
    labels = page.eval_on_selector_all("#videos [data-label]", "els => els.map(e => e.value)")
    assert labels == ["1st half", "2nd half"]
    paste(page, ACCEPTANCE)
    assert page.locator(".reel-head strong").all_inner_texts() == ["reel 1", "reel 2"]
    assert checked_videos(page) == ["1st half"] * 5 + ["2nd half"] * 4 + ["1st half"] * 2 + ["2nd half"] * 6
    assert page.locator("#reels tr.need").count() == 0
    # The test videos are 60 s and 40 s, so most of these ranges are past the end: red, and no Cut.
    assert page.locator("#reels tr.bad").count() > 0 and page.is_disabled("#cut")


def test_no_headers_means_every_row_highlighted_and_cut_blocked(window):
    page, _ = window
    paste(page, NO_HEADERS)
    assert page.locator("#reels tr[data-row]").count() == 17
    assert page.locator("#reels tr.need").count() == 17
    assert checked_videos(page) == [None] * 17
    assert page.is_disabled("#cut")
    assert page.inner_text("#status") == "Choose a video for 17 clips (highlighted)."


def test_set_all_then_cut_and_see_checked_results(window):
    page, tmp = window
    paste(page, "0:10-0:16\n0:20-0:25 Point - No 11\n0:05-0:08")
    assert page.locator("#reels tr.need").count() == 3 and page.is_disabled("#cut")
    page.select_option("[data-setall='0']", "0")
    page.wait_for_function("document.querySelectorAll('#reels tr.need').length === 0")
    assert checked_videos(page) == ["1st half"] * 3
    assert page.locator("#reels .yours").count() == 3
    page.fill("#outDir", str(tmp / "out"))
    page.fill("[data-name='0']", "game clip 1")
    page.wait_for_function("!document.getElementById('cut').disabled")
    page.click("#cut")
    page.wait_for_selector("#done:not([hidden])", timeout=120_000)
    assert page.inner_text("#doneTitle") == "1 reel made and checked"
    assert page.locator("#results .off").count() == 0
    assert page.locator("#results .ok").count() == 4          # 3 clips + the reel
    assert sorted(p.name for p in (tmp / "out").iterdir()) == [
        "game clip 1 - 01.mp4", "game clip 1 - 02.mp4", "game clip 1 - 03.mp4", "game clip 1.mp4"]


def test_select_several_rows_and_set_their_video(window):
    page, _ = window
    paste(page, "1st half\n0:10-0:16\n0:20-0:25\n0:05-0:08")
    page.check("#reels tr[data-row]:nth-child(1) [data-sel]")
    page.check("#reels tr[data-row]:nth-child(3) [data-sel]")
    assert page.inner_text("#bulkCount") == "2 clips selected"
    page.select_option("#bulkVideo", "1")
    page.click("#bulkApply")
    page.wait_for_function("document.getElementById('bulk').hidden")
    assert checked_videos(page) == ["2nd half", "1st half", "2nd half"]


def test_hand_choice_survives_editing_the_timings(window):
    page, _ = window
    text = "reel 1\n1st half\n0:10-0:16\n0:20-0:25\n"
    paste(page, text)
    page.check("#reels tr[data-row]:nth-child(2) input[type=radio][value='1']")   # 0:20-0:25 -> 2nd half
    page.wait_for_function("document.querySelectorAll('#reels .yours').length === 1")
    paste(page, "reel 1\n1st half\n0:02-0:04\n0:10-0:16\n0:20-0:25\n")         # a clip added above it
    assert checked_videos(page) == ["1st half", "1st half", "2nd half"]
    assert "0:20-0:25 is now clip 3 (was 2)" in page.inner_text("#stale")
    paste(page, "reel 1\n1st half\n0:02-0:04\n0:10-0:16\n")                    # and now removed
    assert "no longer in the timings" in page.inner_text("#stale")
    page.click("#dropStale")
    page.wait_for_function("!document.getElementById('stale').textContent.trim()")


def test_ignored_lines_are_listed_and_text_screens_write_into_the_note(window):
    page, _ = window
    paste(page, "Final clips\n1st half\n0:10-0:16\nnice score\n")
    assert "Line 1: “Final clips”" in page.inner_text("#noteNotes")
    assert "Line 4: “nice score”" in page.inner_text("#noteNotes")
    page.fill("#reels [data-line='3']", "Point - No 11")
    page.wait_for_timeout(400)
    assert page.input_value("#note").splitlines()[2] == "0:10-0:16 Point - No 11"


def test_many_videos_use_a_dropdown(window, videos):
    page, _ = window
    page.evaluate("async () => { await window.__py('add_video_paths', [[%r, %r]]); await window.onVideosDropped([]); }"
                  % (str(videos["part1"]), str(videos["part2"])))
    page.wait_for_selector("#videos .video:nth-child(4)")
    paste(page, "0:01-0:03")
    assert page.locator("#reels select[data-key]").count() == 1
    assert page.locator("#reels input[type=radio]").count() == 0


def test_messy_note_in_the_window(window):
    page, tmp = window
    paste(page, "Home v Away\nreel3\nH1\n- 0:10-0:12\nGoal 0:20-0:22\n"
                "2nd half 0:05-0:07 wide\n0:30-0:32, 0:35-0:37\n0:5-0:07\n")
    assert page.locator(".reel-head strong").all_inner_texts() == ["reel 3"]
    assert checked_videos(page) == ["1st half", "1st half", "2nd half", "2nd half", "2nd half", "2nd half"]
    assert page.locator("#reels tr.bad").count() == 1                  # "0:5-0:07"
    assert "Line 1: “Home v Away”" in page.inner_text("#noteNotes")
    # Text can be edited in place only for a clip alone on a plain line; a list marker is kept.
    editable = page.eval_on_selector_all("#reels input.text", "els => els.map(e => !e.disabled)")
    assert editable == [True, False, False, False, False, True]
    page.fill("#reels input.text >> nth=0", "Point")
    page.wait_for_timeout(400)
    assert page.input_value("#note").splitlines()[3] == "- 0:10-0:12 Point"


def test_two_clips_on_one_line_get_their_own_text_screens(window, tmp_path):
    page, tmp = window
    paste(page, "1st half\n0:10-0:12 goal, 0:20-0:22 point\n")
    page.fill("#outDir", str(tmp / "out"))
    page.wait_for_function("!document.getElementById('cut').disabled")
    page.click("#cut")
    page.wait_for_selector("#done:not([hidden])", timeout=120_000)
    assert page.inner_text("#doneTitle") == "1 reel made and checked"
    # 2 clips of 3 s (1 s pad) + two 3 s text screens.
    reel_len = page.eval_on_selector("#results tr.reelrow td:nth-child(3)", "e => e.textContent")
    assert reel_len.startswith("12.0")
