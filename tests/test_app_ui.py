"""The Windows window (hclip/app/ui.html) in headless Chromium, wired to the
real Python Api the way pywebview wires it. Skipped without Playwright."""

import os

import pytest

from hclip.app.api import Api
from tests.conftest import needs_ffmpeg

UI = os.path.join(os.path.dirname(__file__), "..", "hclip", "app", "ui.html")
playwright = pytest.importorskip("playwright.sync_api")
pytestmark = needs_ffmpeg

# pywebview exposes each Api method as a promise-returning function.
BRIDGE = """
window.pywebview = { api: new Proxy({}, { get: (_, name) => (...args) => window.__py(name, args) }) };
"""


@pytest.fixture()
def window(videos, tmp_path):
    picks = []
    api = Api(pick_files=lambda: picks.pop(0), pick_folder=lambda: str(tmp_path))
    with playwright.sync_playwright() as p:
        exe = os.environ.get("HCLIP_CHROMIUM")
        try:
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        except playwright.Error:
            if os.environ.get("CI"):
                raise
            pytest.skip("no Chromium for Playwright; set HCLIP_CHROMIUM")
        page = browser.new_page(viewport={"width": 1300, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.expose_function("__py", lambda name, args: getattr(api, name)(*args))
        page.add_init_script(BRIDGE)
        page.goto("file://" + os.path.abspath(UI))
        yield page, picks
        browser.close()
        assert errors == []


def test_note_with_two_videos_from_two_halves(window, videos):
    page, picks = window
    page.fill("#note", "Video Bally\n0:03-0:05\n0:40-0:44\n2nd Half\n0:10-0:13\n"
                       "2nd Video\n0:20-0:22\n2nd half\n0:30-0:33\n")
    page.wait_for_function("document.getElementById('summary').textContent.includes('videos')")
    assert "5 clips for 2 videos" in page.inner_text("#summary")
    assert page.inner_text("#goHalves") == "Make 2 videos"
    assert page.locator("#textList .tgroup").all_inner_texts() == ["Video Bally", "2nd Video"]
    assert page.locator(".slot").count() == 2                 # only the two halves' videos
    picks += [[str(videos["h1"])], [str(videos["h2"])]]
    page.click("[data-pick='0']")
    page.wait_for_selector(".slot:nth-child(1).done")
    page.click("[data-pick='1']")
    page.wait_for_selector(".slot:nth-child(2).done")
    page.wait_for_function("!document.getElementById('goHalves').disabled")
    assert page.locator("#reelList li").all_inner_texts()[:2] == [
        "Video Bally\n3 clips · 0:09", "2nd Video\n2 clips · 0:05"]


def test_one_video_per_half_is_unchanged(window):
    page, _ = window
    page.fill("#note", "Dunloy clips\n1:52-2:10\n2nd Half Video\n3:04-3:37\n")
    page.wait_for_function("document.getElementById('summary').textContent.includes('clips')")
    assert page.inner_text("#goHalves") == "Make half reels"
    assert page.locator("#textList .tgroup").count() == 0
    assert page.locator(".slot").count() == 2
