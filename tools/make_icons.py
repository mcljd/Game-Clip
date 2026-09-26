"""Render packaging/icon.svg into every icon the apps use.

    python tools/make_icons.py      (needs playwright + Chromium; set
                                     HCLIP_CHROMIUM to use a specific browser)

Writes packaging/icon.ico (Windows exe, installer, shortcuts) and the web
icons (browser tab, iPhone home screen, install manifest). The outputs are
committed, so building the apps never needs this script.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SVG = (ROOT / "packaging" / "icon.svg").read_text()
# At 16-24 px the seams turn to mush: drop them and fatten the play button.
SIMPLE = (SVG.replace('<path d="M58 90', '<path data-seams d="M58 90')
          .replace('stroke-width="9"', 'stroke-width="0" opacity="0"')
          .replace("M110 98 L158 128 L110 158 Z", "M104 88 L172 128 L104 168 Z"))


def render(page, svg: str, size: int, pad: float = 0.0, bg: str | None = None) -> bytes:
    inner = size * (1 - 2 * pad)
    sized = svg.replace("<svg ", '<svg width="100%" height="100%" ')
    page.set_viewport_size({"width": size, "height": size})
    page.set_content(
        f'<html><body style="margin:0;background:{bg or "transparent"}">'
        f'<div style="width:{size}px;height:{size}px;display:grid;place-items:center">'
        f'<div style="width:{inner}px;height:{inner}px">{sized}</div>'
        f"</div></body></html>")
    return page.screenshot(omit_background=bg is None)


def write_ico(pngs: dict[int, bytes], dest: Path) -> None:
    """ICO with PNG-compressed images (supported since Windows Vista)."""
    sizes = sorted(pngs)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for s in sizes:
        data = pngs[s]
        dim = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    dest.write_bytes(header + entries + blobs)


def main() -> None:
    exe = os.environ.get("HCLIP_CHROMIUM")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        page = browser.new_page()
        ico = {s: render(page, SIMPLE if s <= 24 else SVG, s) for s in (16, 24, 32, 48, 64, 128, 256)}
        write_ico(ico, ROOT / "packaging" / "icon.ico")
        web = ROOT / "web"
        (web / "icon-192.png").write_bytes(render(page, SVG, 192))
        (web / "icon-512.png").write_bytes(render(page, SVG, 512))
        (web / "favicon-32.png").write_bytes(ico[32])
        # iPhone masks its own rounded corners and dislikes transparency:
        # full-bleed maroon square, sliotar inset.
        full = SVG.replace('rx="56"', 'rx="0"')
        (web / "apple-touch-icon.png").write_bytes(render(page, full, 180))
        # Android/Chrome "maskable": keep the artwork inside the safe zone.
        (web / "icon-maskable-512.png").write_bytes(render(page, full, 512, pad=0.1, bg="#6e1423"))
        browser.close()
    print("icons written")


if __name__ == "__main__":
    main()
