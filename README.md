# Game-Clip (`hclip`)

Cut GAA match clips (hurling, football, camogie) from full-match videos using
the list of timings a coach types into their phone.

The coach writes something like this in Notes:

```
Dunloy clips
1:52-2:10
3:05-3:33 point from play
...
2nd Half Video
3:04-3:37
5:55-6:30
```

and gets highlight reels:

```
Dunloy - 1st Half.mp4        (Make half reels)
Dunloy - 2nd Half Video.mp4
Dunloy - Whole match.mp4     (Make one video)
```

**Text screens:** text after a clip's time (or typed into the *Text before
each clip* list) is shown on a black screen with white text before that
clip, e.g. `3:05-3:33 Point from play - Conor`. Clips without text have no
screen. Tick *Also keep each clip separately* to get the single clips too.

**How long it takes:** without text screens, reels are made by copying the
video (seconds, no quality loss). Text screens mean the reel is re-made
(encoded): on a laptop about a quarter of the reel's length (a 5-minute reel
in about a minute and a half); in a phone's browser about real time or
slower, so a 5-minute half can take 5-10 minutes. Phone reels are made at
720p to keep that bearable.

## For coaches: the GAA Clips app (Windows)

1. **Install:** run `GAA-Clips-Setup.exe`. No admin password needed.
   Windows may show *"Windows protected your PC"* because the app isn't
   code-signed yet: click **More info → Run anyway**.
2. **Paste the timings** from your phone into box 1. Each clip appears
   straight away; typos are shown in red with the line number.
3. **Choose the videos:** one per half (box 2 shows a slot for every half
   in your note). If a half was recorded as several files, select them all
   at once and they're joined in order. Don't select both halves' videos
   together: each box takes only that half's video. The app warns if the
   same video is chosen for two boxes. Videos aren't copied or uploaded, so
   big files are fine, straight off an SD card or USB drive.
4. **Make half reels** or **Make one video.** They're saved in a
   "*Match name* highlights" folder next to the videos. Press **Open folder**
   when it's done.

Pressing a Make button again rebuilds the videos (so changed text or timings
are picked up). If you press Stop, the parts already made are kept and the
next Make carries on from there.

If the window won't open on an older PC, install Microsoft's free
[Edge WebView2 Runtime](https://go.microsoft.com/fwlink/p/?LinkId=2124703).

## On iPhone (or any browser): the web version

The same tool runs in Safari on iPhone, and in Chrome or Edge on a computer,
with nothing to install. It lives in `web/` and is published to GitHub Pages.

- **Nothing is uploaded.** ffmpeg runs inside the browser (WebAssembly) and
  reads only the parts of the video it needs, so a 4 GB+ half is fine.
  (Tested: a clip cut from past the 4 GB mark of a 4.3 GB file in under 3 s.)
- **Paste** the timings (the Paste button reads the clipboard), choose a
  video per half, tap **Half reels** or **One video**.
- Videos are kept on the phone until you tap **Save to Photos or Files**
  (the iPhone share sheet). They survive the page reloading, and after Stop
  the next Make carries on where it stopped.
- On iPhone, tap *Share → Add to Home Screen* to get an app icon.
- Keep the screen open while clips are made: iPhone pauses pages in the
  background. The page asks the phone not to sleep.

Things to know on iPhone:
- Choose videos from the **Files** app where you can. Picking a huge video
  from **Photos** makes iOS prepare a copy first, which can take minutes and
  needs free space.
- The first visit downloads the video tools (about 30 MB); after that they're
  cached.
- iPhone HEVC video is copied with the tag Apple's players need, so clips
  play in Photos.

## How it handles big match videos

- **Nothing is uploaded or loaded into memory.** ffmpeg reads the video
  straight off the disk or SD card and writes each clip directly, so a 15 GB
  half uses the same memory as a 1 GB one.
- **Fast by default.** Clips are stream-copied: no re-encoding, no quality
  loss, a few seconds per clip. The catch is that a clip starts on the nearest
  keyframe *at or before* the coach's start time, so it may begin a second or
  two early. It never starts late. Use `--precise` for frame-accurate cuts
  (re-encodes, much slower).
- **One video per half, or several.** Each section of the note (1st half,
  2nd half, extra time, penalties) is matched to its own video in order. A half
  split across several camera files (GoPro `GX01xxxx`, `GX02xxxx` chapters) is
  joined on the fly, in the right order, without writing a joined copy first.
- **Safe to re-run.** Existing clips are skipped (`--force` redoes them).
  Clips are written as `.part` files and renamed only when finished, so a
  crash or cancel never leaves a broken clip that looks complete.
- **Problems are listed, not fatal.** Typos (`1:75`, end before start), clips
  past the end of the video, overlaps and suspiciously long clips are each
  reported with the note's line number. Bad lines are skipped; the rest still
  get cut.

## What the note can contain

| Written as | Understood as |
|---|---|
| `1:52-2:10`, `1:52 - 2:10`, `1:52–2:10`, `1:52 to 2:10`, `1.52-2.10` | a clip |
| `1:02:03-1:02:30` | a clip past the hour (whole-match file) |
| `3:05-3:33 Goal Conor` | a clip labelled "Goal Conor" |
| `2nd Half`, `Second half video`, `Extra time 1`, `ET2`, `Penalties` | the next clips come from that half's video |
| `2nd Video`, `Video 2` (a line with "video" but no half) | starts another video to make (see below) |
| first text line before any clip | the match title |

Timings are measured from the start of **that half's video**, not the
match clock.

**Several videos from one match.** A coach can list more than one video
(say, one for the forwards and one for the backs), each with clips from
both halves:

```
Video Ballycastle
3:43-4:01          <- 1st half
2nd Half
3:10-3:34          <- 2nd half
2nd Video
6:56-7:07          <- each video starts on the 1st half again
2nd half
5:05-5:15
```

Choose one video per half as usual (here just two), and the first Make
button makes `Video Ballycastle.mp4` and `Video Ballycastle - 2nd Video.mp4`,
each with its clips in the order they're written.

## Command line

Needs Python 3.11+ and ffmpeg/ffprobe on the PATH (the Windows app will bundle
them).

```bash
pip install -e ".[dev]"

# Check the note first: lists every clip and any problems
hclip check dunloy.txt
pbpaste | hclip check -          # straight from the clipboard on a Mac

# Cut: one --half per section, in the note's order
hclip cut dunloy.txt \
  --half "Dunloy 1st half.mp4" \
  --half "Dunloy 2nd half.mp4" \
  --out "Dunloy clips"

# A half recorded as several GoPro files
hclip cut dunloy.txt --half GX010123.MP4 GX020123.MP4 --half GX010124.MP4

# Frame-accurate (slow)
hclip cut dunloy.txt --half h1.mp4 --half h2.mp4 --precise

# Reels: one per half, or --whole for one video (text screens are app-only)
hclip reel dunloy.txt --half h1.mp4 --half h2.mp4 --title "Dunloy" --out highlights

# Duration, codecs, keyframe spacing
hclip info "Dunloy 1st half.mp4"
```

## Development

```bash
pytest                       # unit + integration tests
pip install -e ".[app]"      # then: gaa-clips   (opens the window; Mac/Windows)
```

Integration tests generate small test videos with ffmpeg's `testsrc`, so no
real footage is needed. They are skipped if ffmpeg isn't installed.

### The web version

```bash
web/fetch-vendor.sh                  # pinned ffmpeg.wasm into web/vendor/ (~32 MB, not committed)
python3 -m http.server -d web 8000   # then open http://localhost:8000
node --test tests/web/*.test.js      # parser tests (same cases as the Python parser)
pytest tests/test_web_e2e.py         # full flow in headless Chromium (needs playwright)
```

`web/notes.js` is a port of `hclip/notes.py`; both are checked against
`tests/data/note_cases.json`, so a fix to one that isn't made to the other
fails CI.

**Publishing:** `.github/workflows/pages.yml` publishes `web/` whenever
`main` changes. One-time setup: *Settings → Pages → Source: GitHub Actions*.
GitHub Pages on a **private** repository needs a paid GitHub plan; otherwise
make the repository public or host `web/` anywhere that serves static files.

### Building the Windows app

GitHub Actions (`.github/workflows/build.yml`) does this on every push: it
downloads a GPL ffmpeg build, runs the whole test suite on Windows, packages
the app with PyInstaller (ffmpeg bundled inside), runs the packaged exe's
`--selftest` (which fails if it isn't using its *own* ffmpeg), and builds
`GAA-Clips-Setup.exe` with Inno Setup. Download it from the run's
**Artifacts**; tagging `v0.1.0` attaches it to a GitHub release.

The bundled ffmpeg is GPL-licensed; its licence ships in the app folder.

```
hclip/
  notes.py      parse the coach's note (sections, clips, problems + line numbers)
  timecode.py   1:52 / 1:02:03 / 1.52 <-> seconds
  ffmpeg.py     the only module that runs ffmpeg/ffprobe (pure command builders + runner)
  cut.py        match sections to videos, check ranges, cut resumably with progress
  reel.py       half / whole-match reels, text screens, copy-or-encode choice
  naming.py     Windows-safe filenames
  cli.py        command line
  app/api.py    what the window can ask Python to do (tested without a window)
  app/ui.html   the coach's window
  app/main.py   opens it with pywebview; --selftest for packaged builds
packaging/      PyInstaller spec + Inno Setup installer script
web/
  index.html    the phone/browser page
  notes.js      note parser (twin of notes.py)
  engine.js     ffmpeg.wasm cutting (same command as ffmpeg.py)
  store.js      keeps finished videos (and parts) on the device (IndexedDB)
  cards.js      draws the black text screens
  app.js        page logic
```
