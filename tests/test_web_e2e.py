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
        f"/{pattern}/.test(document.getElementById('status').textContent)", timeout=180_000)


def saved_video(page, index, tmp_path):
    """Pull a finished video out of the page's storage and write it to disk."""
    got = page.evaluate("""async (i) => {
      const t = new ClipTray(); await t.init();
      const rank = (x) => (x.kind === 'reel' ? 0 : 1);
      const r = (await t.all()).filter((x) => x.kind !== 'piece')
        .sort((a, b) => rank(a) - rank(b) || (a.order || 0) - (b.order || 0) || a.key.localeCompare(b.key))[i];
      const b = new Uint8Array(await r.blob.arrayBuffer()); let s = '';
      for (let k = 0; k < b.length; k += 0x8000) s += String.fromCharCode.apply(null, b.subarray(k, k + 0x8000));
      return {name: r.name, b64: btoa(s)};
    }""", index)
    out = tmp_path / got["name"]
    out.write_bytes(base64.b64decode(got["b64"]))
    return out


def decodes_cleanly(path):
    import subprocess
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                       capture_output=True, text=True)
    return r.stderr.strip() == ""


def test_half_reels_with_text_screens_then_one_video(phone, videos, tmp_path):
    page = phone
    page.fill("#title", "Test match")
    page.fill("#note", NOTE)
    page.wait_for_timeout(400)
    assert "1:75" in page.inner_text("#noteProblems")
    assert not page.is_visible("#folderOpt")  # laptop-only option
    # Type a text screen for the first clip in the list; it's written into the note.
    page.fill("[data-line='2']", "Point - No 11")
    page.wait_for_timeout(400)
    assert page.input_value("#note").splitlines()[1] == "0:10-0:16 Point - No 11"
    pick(page, 0, videos["h1"])
    pick(page, 1, videos["h2"])
    page.wait_for_function("!document.getElementById('goHalves').disabled")
    assert "2 of 3 clips have text" in page.inner_text("#cardsHint")

    page.click("#goHalves")
    wait_status(page, "Done: 2 videos made")
    names = page.locator("#clipList .name").all_inner_texts()
    assert names == ["Test match - 1st Half.mp4", "Test match - 2nd half.mp4"]
    first = saved_video(page, 0, tmp_path)
    info = ffmpeg.probe(first)
    assert (info.video_codec, info.audio_codec) == ("h264", "aac")
    # Re-made, so exact: 6s + 9s clips, plus two 3s text screens
    assert abs(info.duration - (6 + 9 + 3 + 3)) < 0.5
    assert decodes_cleanly(first)

    page.click("#goWhole")
    wait_status(page, "Done: 1 video made")
    assert page.locator("#clipList li").count() == 3
    # Parts are tidied away once everything worked.
    assert page.evaluate("async () => { const t = new ClipTray(); await t.init(); return (await t.all()).filter(x => x.kind === 'piece').length; }") == 0


def test_copy_mode_without_text_screens(phone, videos, tmp_path):
    page = phone
    page.fill("#note", "Test match\n0:10-0:16\n0:31-0:40\n")
    pick(page, 0, videos["h1"])
    page.uncheck("#cards")
    page.check("#keepClips")
    page.wait_for_timeout(400)
    page.click("#goHalves")
    wait_status(page, "Done: 1 video made")
    assert page.locator("#clipList li").count() == 3   # the reel + 2 kept clips
    reel = saved_video(page, 0, tmp_path)
    assert reel.name == "Test match - 1st Half.mp4"
    assert 15 - 0.3 <= ffmpeg.probe(reel).duration <= 15 + 4.5  # keyframe starts
    assert decodes_cleanly(reel)


def test_stop_then_carry_on(phone, videos):
    page = phone
    page.fill("#note", "0:00-0:55 Long one\n")
    pick(page, 0, videos["h1"])
    page.wait_for_timeout(400)
    page.click("#goHalves")
    # Stop as soon as it starts: a fixed wait raced the run on a fast machine.
    page.wait_for_selector("#running:not([hidden])")
    page.click("#stop")
    wait_status(page, "Stopped")
    assert page.locator("#clipList li").count() == 0
    page.click("#goHalves")
    wait_status(page, "Done: 1 video made")
    assert page.locator("#clipList li").count() == 1


def piece_count(page):
    return page.evaluate("async () => { const t = new ClipTray(); await t.init(); "
                         "return (await t.all()).filter(x => x.kind === 'piece').length; }")


def test_videos_that_mix_both_halves(phone, videos, tmp_path):
    # Real note shape: two recordings (the halves), two videos, each with
    # clips from both halves in the order they're written.
    page = phone
    page.fill("#title", "")
    page.fill("#note", "Video Bally\n0:03-0:05\n0:40-0:44\n2nd Half\n0:10-0:13\n"
                       "2nd Video\n0:20-0:22\n2nd half\n0:30-0:33\n")
    page.wait_for_timeout(300)
    assert "for 2 videos" in page.inner_text("#summary")
    assert page.inner_text("#goHalves") == "Make 2 videos"
    assert page.locator("#textList .tgroup").all_inner_texts() == ["Video Bally", "2nd Video"]
    pick(page, 0, videos["h1"])
    pick(page, 1, videos["h2"])
    assert page.locator(".slot").count() == 2        # just the two halves' recordings
    page.uncheck("#cards")
    page.wait_for_timeout(300)
    page.click("#goHalves")
    wait_status(page, "Done: 2 videos made")
    assert page.locator("#clipList .name").all_inner_texts() == ["Video Bally.mp4", "Video Bally - 2nd Video.mp4"]
    first = saved_video(page, 0, tmp_path)
    assert 9 - 0.3 <= ffmpeg.probe(first).duration <= 9 + 3 * 2.1
    assert decodes_cleanly(first)


def test_leftover_parts_are_cleared_when_timings_change(phone, videos):
    # A part left by an earlier, stopped run whose timings have since changed
    # (put in storage directly: stopping a real run mid-way raced the run).
    page = phone
    page.evaluate("""async () => { const t = new ClipTray(); await t.init();
        await t.put({ key: 'piece:old-timings', kind: 'piece', name: 'old.mp4', match: 'x',
                      blob: new Blob([new Uint8Array(10)], { type: 'video/mp4' }), size: 10 }); }""")
    assert piece_count(page) == 1
    page.fill("#note", "0:10-0:15 Different\n")
    pick(page, 0, videos["h1"])
    page.wait_for_timeout(400)
    page.click("#goHalves")
    wait_status(page, "Done: 1 video made")
    assert piece_count(page) == 0                 # the old part is gone, the new ones tidied


def test_joined_half_clips_are_their_own_length(phone, videos, tmp_path):
    # Regression: -ss can't seek in a joined input, so each clip used to carry
    # all the footage from the start of the half.
    page = phone
    page.fill("#note", "0:25-0:30\n0:17-0:23\n")   # inside the 2nd file, and across the join
    with page.expect_file_chooser() as fc:
        page.click("[data-pick='0']")
    fc.value.set_files([str(videos["part1"]), str(videos["part2"])])
    page.wait_for_selector(".slot:nth-child(1).done", timeout=60_000)
    page.uncheck("#cards")
    page.check("#keepClips")
    page.wait_for_timeout(400)
    page.click("#goHalves")
    wait_status(page, "Done: 1 video made")
    reel = saved_video(page, 0, tmp_path)
    assert 11 - 0.3 <= ffmpeg.probe(reel).duration <= 11 + 4.5   # two keyframe lead-ins at most
    for i in (1, 2):
        clip = saved_video(page, i, tmp_path)
        assert ffmpeg.probe(clip).duration <= 6 + 2.1, clip.name
        assert decodes_cleanly(clip)


def test_same_video_for_two_sections_is_flagged(phone, videos):
    page = phone
    page.fill("#note", "0:01-0:03\n2nd Half\n0:04-0:06\n")
    pick(page, 0, videos["h1"])
    pick(page, 1, videos["h1"])
    page.wait_for_function("document.getElementById('planProblems').textContent.includes('chosen for')")
    assert "1st Half and 2nd Half" in page.inner_text("#planProblems")
