# Game-Clip (`hclip`)

Cut GAA match clips (hurling, football, camogie) from full-match videos using
the list of timings a coach types into their phone.

The coach writes something like this in Notes:

```
Match clips
1:52-2:10
3:05-3:33 point from play
...
2nd Half Video
3:04-3:37
5:55-6:30
```

and gets highlight reels:

```
Final - 1st Half.mp4        (Make half reels)
Final - 2nd Half Video.mp4
Final - Whole match.mp4     (Make one video)
```

**Text screens:** text after a clip's time (or typed into the *Text before
each clip* list) is shown on a black screen with white text before that
clip, e.g. `3:05-3:33 Point from play - No 11`. Clips without text have no
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
2. **Videos:** press **Add videos…** (or drag them onto the window) and add
   the match recordings, usually one per half. Each gets a label you can
   edit ("1st half", "2nd half"); its length is shown next to it. Any number
   of videos works. Times are counted from the start of each video.
3. **Timings:** paste your notes, e.g.

   ```
   reel 1
   1st half
   3:43-4:01
   11:26-11:40 Point - No 11
   2nd Half
   3:10-3:34

   reel 2
   1st half
   6:56-7:07
   ```

   `reel N` starts another finished video. A half line picks the video for
   the clips under it by matching the videos' labels. Text after a time is
   shown on a black screen before that clip.
4. **Clip list:** every clip is listed under its reel with its video ticked.
   A clip with no half line above it (or one that matches no label) is
   highlighted and has no video until you choose one; Cut stays off until
   every clip has one. "Set all to" does a whole reel; tick several clips
   to set them together. Your own choices stay with their clip when you
   edit the timings.
5. **Cut:** each clip gets 1 second extra at the end by default (both ends
   can be set). Every clip is re-encoded so it starts exactly on time, the
   reel is joined in the order written, and every clip and reel is measured
   afterwards: the results table shows a tick for each one that's the right
   length. You get each reel (named as you like, e.g. `game clip 1.mp4`) and
   its clips (`game clip 1 - 01.mp4` …) in the folder you chose.

**What the timings box understands** (all tested in `tests/test_clipplan_inputs.py`):

| You type | It means |
|---|---|
| `reel 2`, `Reel 2:`, `reel2`, `Reel 6 - forwards`, `game clip 2`, `2nd Video`, `Video 3` | start another reel |
| `1st half`, `First Half`, `1sthalf`, `H1`, `1H`, `1st`, `half one`, `1st hlaf` | the next clips come from the 1st half's video (same for 2nd) |
| `half time`, `2md half`, no header at all | not guessed: the clips are highlighted for you to choose |
| `3:43-4:01`, `3.43-4.01`, `3:43 – 4:01`, `3:43 to 4:01`, `1:02:03-1:02:30` | a clip |
| `- 3:43-4:01`, `• …`, `1. …`, `2) …`, `☐ …` | a clip (list markers are fine) |
| `3:43-4:01 Point - No 11`, `Goal 3:43-4:01`, `2nd half 3:10-3:34 wide` | a clip with a text screen (and a half header on the same line) |
| `3:43-4:01 goal, 5:00-5:10 point` | two clips |
| `3:43-4:1`, `3:43-`, `1:75-2:00`, `4:01-3:43`, `8:05-8:21 19:03` | a red row that stops Cut until you fix it |
| anything else (titles, notes) | listed under the box as "not understood" |

Save to a folder on the laptop rather than in OneDrive if you can: writing
big files into OneDrive can be very slow. (The videos are made on the laptop
first either way.) A video that's "online only" in OneDrive is flagged: set it
to *Always keep on this device* first.

If the window won't open on an older PC, install Microsoft's free
[Edge WebView2 Runtime](https://go.microsoft.com/fwlink/p/?LinkId=2124703).

The iPhone/browser version and the `hclip` command line still use the
earlier one-video-per-half layout described below.

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
| `3:05-3:33 Goal No 11` | a clip labelled "Goal No 11" |
| `2nd Half`, `Second half video`, `Extra time 1`, `ET2`, `Penalties` | the next clips come from that half's video |
| `2nd Video`, `Video 2` (a line with "video" but no half) | starts another video to make (see below) |
| first text line before any clip | the match title |

Timings are measured from the start of **that half's video**, not the
match clock.

**Several videos from one match.** A coach can list more than one video
(say, one for the forwards and one for the backs), each with clips from
both halves:

```
Video Final
3:43-4:01          <- 1st half
2nd Half
3:10-3:34          <- 2nd half
2nd Video
6:56-7:07          <- each video starts on the 1st half again
2nd half
5:05-5:15
```

Choose one video per half as usual (here just two), and the first Make
button makes `Video Final.mp4` and `Video Final - 2nd Video.mp4`,
each with its clips in the order they're written.

## Command line

Needs Python 3.11+ and ffmpeg/ffprobe on the PATH (the Windows app will bundle
them).

```bash
pip install -e ".[dev]"

# Check the note first: lists every clip and any problems
hclip check sample_note.txt
pbpaste | hclip check -          # straight from the clipboard on a Mac

# Cut: one --half per section, in the note's order
hclip cut sample_note.txt \
  --half "Final 1st half.mp4" \
  --half "Final 2nd half.mp4" \
  --out "Match clips"

# A half recorded as several GoPro files
hclip cut sample_note.txt --half GX010123.MP4 GX020123.MP4 --half GX010124.MP4

# Frame-accurate (slow)
hclip cut sample_note.txt --half h1.mp4 --half h2.mp4 --precise

# Reels: one per half, or --whole for one video (text screens are app-only)
hclip reel sample_note.txt --half h1.mp4 --half h2.mp4 --title "Final" --out highlights

# Duration, codecs, keyframe spacing
hclip info "Final 1st half.mp4"
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
