"""End-to-end test of the browser/iPhone version (web/) in headless Chromium.

Runs the real page with the real ffmpeg.wasm on generated videos. Skipped
unless Playwright is installed and web/vendor/ has been fetched
(web/fetch-vendor.sh). Set HCLIP_CHROMIUM to use a specific browser binary.
"""

import base64
import functools
import http.server
import os
import threading
from pathlib import Path

import pytest

from hclip import ffmpeg
from tests.conftest import needs_ffmpeg

WEB = Path(__file__).resolve().parent.parent / "web"
playwright = pytest.importorskip("playwright.sync_api")
pytestmark = [
    needs_ffmpeg,
    pytest.mark.skipif(not (WEB / "vendor" / "ffmpeg-core.wasm").exists(),
                       reason="run web/fetch-vendor.sh first"),
]

NOTE = "Test match\n0:10-0:16\n0:31-0:40 Goal\n1:75-2:00\n2nd half\n0:05-0:12\n"


@pytest.fixture(scope="module")
def site():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture()
def phone(site):
    with playwright.sync_playwright() as p:
        exe = os.environ.get("HCLIP_CHROMIUM")
        try:
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        except playwright.Error:
            if os.environ.get("CI"):
                raise  # never silently skip in CI
            pytest.skip("no Chromium for Playwright; set HCLIP_CHROMIUM")
        ctx = browser.new_context(viewport={"width": 390, "height": 844},
                                  is_mobile=True, has_touch=True)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(site)
        yield page
        browser.close()
        assert errors == []


def pick(page, index, path):
    with page.expect_file_chooser() as fc:
        page.click(f"[data-pick='{index}']")
    fc.value.set_files(str(path))
    page.wait_for_selector(f".slot:nth-child({index + 1}).done", timeout=60_000)


def wait_status(page, pattern):
    page.wait_for_function(
        f"/{pattern}/.test(document.getElementById('status').textContent)", timeout=120_000)


def test_phone_flow_makes_valid_clips(phone, videos, tmp_path):
    page = phone
    page.fill("#note", NOTE)
    page.wait_for_timeout(400)
    assert "1:75" in page.inner_text("#noteProblems")
    assert not page.is_visible("#folderOpt")  # laptop-only option
    pick(page, 0, videos["h1"])
    pick(page, 1, videos["h2"])
    page.wait_for_function("document.getElementById('go').textContent === 'Make 3 clips'")

    page.click("#go")
    wait_status(page, "Done: 3 clips made")
    assert page.locator("#clipList li").count() == 3
    assert page.inner_text("#go") == "All clips made"

    clip = page.evaluate("""async () => {
      const t = new ClipTray(); await t.init(); const r = (await t.all())[1];
      const b = new Uint8Array(await r.blob.arrayBuffer()); let s = '';
      for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
      return {name: r.name, b64: btoa(s)};
    }""")
    assert clip["name"] == "02_h1_00m31-00m40_goal.mp4"
    out = tmp_path / clip["name"]
    out.write_bytes(base64.b64decode(clip["b64"]))
    info = ffmpeg.probe(out)
    assert info.video_codec == "h264" and info.audio_codec == "aac"
    assert 9 - 0.2 <= info.duration <= 9 + 2.2

    # Clips survive a reload and aren't made again.
    page.reload()
    page.wait_for_function("document.querySelectorAll('#clipList li').length === 3")


def test_stop_mid_clip_then_restart(phone, videos):
    page = phone
    page.fill("#note", "0:00-0:55\n")
    pick(page, 0, videos["h1"])
    page.check("#precise")
    page.wait_for_timeout(400)
    page.click("#go")
    page.wait_for_selector("#running:not([hidden])")
    page.wait_for_timeout(1500)
    page.click("#go")  # Stop
    wait_status(page, "Stopped. 0 clips made")
    assert page.locator("#clipList li").count() == 0

    page.uncheck("#precise")
    page.wait_for_timeout(400)
    page.click("#go")
    wait_status(page, "Done: 1 clip made")
    assert page.locator("#clipList li").count() == 1
