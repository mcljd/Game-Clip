# PyInstaller build for the Windows app.  Run from the repo root:
#     pyinstaller packaging/gaa-clips.spec
# Expects ffmpeg.exe and ffprobe.exe in vendor/ffmpeg/ (the CI workflow
# downloads them).  Produces dist/GAA Clips/GAA Clips.exe (one folder, not
# one-file: starts faster and trips antivirus far less often).
import os
import re

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
AUTHOR = "Joseph McLaughlin"
with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
    VERSION = re.search(r'^version = "(.+)"', f.read(), re.M).group(1)
nums = tuple(int(n) for n in (VERSION.split(".") + ["0"] * 4)[:4])


def windows_version_info():
    """File details shown in Properties > Details (Windows builds only)."""
    from PyInstaller.utils.win32 import versioninfo as vi
    return vi.VSVersionInfo(
        ffi=vi.FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[
            vi.StringFileInfo([vi.StringTable("080904B0", [
                vi.StringStruct("CompanyName", AUTHOR),
                vi.StringStruct("FileDescription", "GAA Clips"),
                vi.StringStruct("FileVersion", VERSION),
                vi.StringStruct("InternalName", "GAA Clips"),
                vi.StringStruct("LegalCopyright", f"© 2026 {AUTHOR}"),
                vi.StringStruct("OriginalFilename", "GAA Clips.exe"),
                vi.StringStruct("ProductName", "GAA Clips"),
                vi.StringStruct("ProductVersion", VERSION),
            ])]),
            vi.VarFileInfo([vi.VarStruct("Translation", [0x0809, 1200])]),
        ],
    )
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
    icon=os.path.join(SPECPATH, "icon.ico"),
    version=windows_version_info() if os.name == "nt" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="GAA Clips", upx=False)
