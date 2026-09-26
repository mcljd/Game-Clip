"""Open the GAA Clips window."""

from __future__ import annotations

import os
import subprocess
import sys
from importlib import resources

from hclip import ffmpeg
from hclip.app.api import VIDEO_EXTS, Api

WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


def _load_ui() -> str:
    return resources.files("hclip.app").joinpath("ui.html").read_text(encoding="utf-8")


def selftest(report_path: str | None) -> int:
    """Check a built app can find everything, without opening a window.
    Used by CI on the packaged .exe (which has no console to print to)."""
    lines: list[str] = []
    ok = True
    bundle = getattr(sys, "_MEIPASS", None)
    try:
        for name in ("ffmpeg", "ffprobe"):
            exe = ffmpeg.find_binary(name)
            if bundle and not os.path.abspath(exe).startswith(os.path.abspath(bundle)):
                # A packaged app must use its own copy, not one that happens
                # to be on this machine's PATH (coaches won't have one).
                lines.append(f"{name}: NOT BUNDLED (found {exe})")
                ok = False
                continue
            out = subprocess.run([exe, "-version"], capture_output=True, text=True,
                                 **ffmpeg._popen_kwargs())
            first = (out.stdout.splitlines() or ["?"])[0]
            lines.append(f"{name}: {exe} -> {first}")
            ok &= out.returncode == 0
        lines.append(f"ui.html: {len(_load_ui())} chars")
        import webview  # noqa: F401
        lines.append(f"webview: {webview.__file__}")
    except Exception as e:  # report everything, never crash silently
        lines.append(f"FAILED: {type(e).__name__}: {e}")
        ok = False
    lines.append("OK" if ok else "FAILED")
    text = "\n".join(lines) + "\n"
    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(text)
    elif sys.stdout:
        sys.stdout.write(text)
    return 0 if ok else 1


def _fatal_box(message: str) -> None:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "GAA Clips", 0x10)
    elif sys.stderr:
        print(message, file=sys.stderr)


def main() -> None:
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        report = sys.argv[i + 1] if len(sys.argv) > i + 1 else None
        raise SystemExit(selftest(report))

    import webview  # imported here so the engine and tests don't need it

    window: list = []

    def pick_files():
        pattern = ";".join(f"*.{e};*.{e.upper()}" for e in VIDEO_EXTS)
        return window[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=(f"Video files ({pattern})", "All files (*.*)"),
        )

    def pick_folder():
        picked = window[0].create_file_dialog(webview.FOLDER_DIALOG)
        return picked[0] if picked else None

    api = Api(pick_files=pick_files, pick_folder=pick_folder)
    window.append(webview.create_window(
        "GAA Clips", html=_load_ui(), js_api=api,
        width=1040, height=800, min_size=(720, 600),
    ))
    try:
        # On Windows insist on Edge WebView2; the old IE engine can't run the page.
        webview.start(gui="edgechromium" if sys.platform == "win32" else None)
    except Exception as e:
        _fatal_box(
            "GAA Clips couldn't open its window.\n\n"
            "It needs the Microsoft Edge WebView2 Runtime, which comes with "
            "Windows 10 and 11 but may be missing on older or locked-down PCs. "
            f"Install it from:\n{WEBVIEW2_URL}\n\n(Details: {e})"
        )
        raise SystemExit(1)
    finally:
        api.cancel()
        api.wait(5)


if __name__ == "__main__":
    main()
