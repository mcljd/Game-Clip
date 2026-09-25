# PyInstaller build for the Windows app.  Run from the repo root:
#     pyinstaller packaging/gaa-clips.spec
# Expects ffmpeg.exe and ffprobe.exe in vendor/ffmpeg/ (the CI workflow
# downloads them).  Produces dist/GAA Clips/GAA Clips.exe (one folder, not
# one-file: starts faster and trips antivirus far less often).
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
FF = os.path.join(ROOT, "vendor", "ffmpeg")

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[ROOT],
    binaries=[
        (os.path.join(FF, "ffmpeg.exe"), "ffmpeg"),
        (os.path.join(FF, "ffprobe.exe"), "ffmpeg"),
    ],
    datas=[
        (os.path.join(ROOT, "hclip", "app", "ui.html"), os.path.join("hclip", "app")),
        (os.path.join(FF, "LICENSE.txt"), "ffmpeg"),
    ],
    hiddenimports=["webview.platforms.edgechromium", "webview.platforms.winforms"],
    excludes=["tkinter", "numpy", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="GAA Clips",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="GAA Clips", upx=False)
