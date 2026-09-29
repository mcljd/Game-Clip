/* Browser cutting engine: ffmpeg compiled to WebAssembly, running in a worker.
 *
 * Videos are never read into memory: they're mounted with WORKERFS, which
 * reads only the bytes ffmpeg asks for, straight from the File the user
 * picked. Tested cutting past the 4 GB mark of a 4.3 GB file with its index
 * at the end (like iPhone recordings) in under 3 seconds.
 *
 * The cut command mirrors hclip/ffmpeg.py build_cut_cmd.
 */
(function (root) {
  "use strict";

  const MP4_OK_AUDIO = new Set(["aac", "mp3", "ac3", "eac3", "alac", "opus", "flac"]);
  const GOPRO_RE = /^(G[HXL])(\d{2})(\d{4})$/i;

  function sortParts(files) {
    const key = (f) => {
      const stem = f.name.replace(/\.[^.]+$/, "");
      const m = GOPRO_RE.exec(stem);
      return m ? [0, m[3], +m[2], f.name.toLowerCase()] : [1, "", 0, f.name.toLowerCase()];
    };
    return [...files].sort((a, b) => {
      const ka = key(a), kb = key(b);
      for (let i = 0; i < ka.length; i++) {
        if (ka[i] < kb[i]) return -1;
        if (ka[i] > kb[i]) return 1;
      }
      return 0;
    });
  }

  // A clip that ends this little past the end of one file still counts as inside it.
  const PART_SLACK = 0.1;

  /** Which of a half's files a clip is read from (same rule as the Python
   *  HalfVideo.clip_source): {first, last, localStart}. first === last means
   *  one file, read with a fast -ss seek; otherwise a joined list starting at
   *  the clip (inpoint), because -ss can't seek in a joined input and would
   *  copy everything from the start of the half. */
  function clipSource(durations, start, end) {
    let offset = 0;
    for (let i = 0; i < durations.length; i++) {
      const d = durations[i], lastFile = i === durations.length - 1;
      if (start < offset + d || lastFile) {
        const localStart = Math.max(0, start - offset);
        let last = i, covered = offset + d;
        if (!(end <= covered + PART_SLACK)) {
          while (last < durations.length - 1 && end > covered) covered += durations[++last];
        }
        return { first: i, last, localStart };
      }
      offset += d;
    }
    return { first: 0, last: 0, localStart: start };
  }

  // Input options that start reading at `start`. A joined input already starts
  // at the clip (inpoint); when re-encoding, -copyts keeps its keyframe lead-in
  // at negative times so preRollTrim can drop it.
  const seekArgs = (start, isConcat, reencode) =>
    isConcat ? (reencode ? ["-copyts"] : []) : ["-ss", start.toFixed(3)];
  const preRollTrim = (duration) => [
    `trim=start=0:duration=${duration.toFixed(3)},setpts=PTS-STARTPTS`,
    `atrim=start=0:duration=${duration.toFixed(3)},asetpts=PTS-STARTPTS`];

  function buildCutArgs(input, start, duration, out, opts = {}) {
    const args = ["-hide_banner", "-nostdin", "-y", ...seekArgs(start, opts.isConcat, opts.precise)];
    if (opts.isConcat) args.push("-f", "concat", "-safe", "0");
    args.push("-i", input, "-t", duration.toFixed(3), "-map", "0:v:0", "-map", "0:a:0?");
    if (opts.precise) {
      if (opts.isConcat) { const [vf, af] = preRollTrim(duration); args.push("-vf", vf, "-af", af); }
      args.push("-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k");
    } else {
      args.push("-c:v", "copy");
      if (opts.hevc) args.push("-tag:v", "hvc1");
      if (opts.reencodeAudio) args.push("-c:a", "aac", "-b:a", "192k");
      else args.push("-c:a", "copy");
      args.push("-avoid_negative_ts", "make_zero");
    }
    args.push("-movflags", "+faststart", "-f", "mp4", out);
    return args;
  }

  // ffmpeg prints rounded rates; map the usual NTSC ones back to exact.
  const EXACT_FPS = { "23.98": "24000/1001", "29.97": "30000/1001", "59.94": "60000/1001", "47.95": "48000/1001" };

  function parseProbeLog(text) {
    const d = /Duration: (\d+):(\d+):(\d+(?:\.\d+)?)/.exec(text);
    const vLine = (/Stream #0:\d+[^\n]*: Video: [^\n]*/.exec(text) || [""])[0];
    const aLine = (/Stream #0:\d+[^\n]*: Audio: [^\n]*/.exec(text) || [""])[0];
    const v = /Video: (\w+)/.exec(vLine);
    const a = /Audio: (\w+)/.exec(aLine);
    const size = /, (\d{2,5})x(\d{2,5})[, \[]/.exec(vLine);
    const fps = /, ([\d.]+) fps/.exec(vLine);
    const pix = /Video: \w+[^,]*, (\w+)/.exec(vLine);
    const profile = /Video: \w+ \(([^)]+)\)/.exec(vLine);
    const rate = /, (\d+) Hz/.exec(aLine);
    const ch = /Hz, ([\w.()]+)/.exec(aLine);
    return {
      duration: d ? +d[1] * 3600 + +d[2] * 60 + +d[3] : 0,
      video: v ? v[1] : null,
      audio: a ? a[1] : null,
      width: size ? +size[1] : null,
      height: size ? +size[2] : null,
      fps: fps ? (EXACT_FPS[fps[1]] || fps[1]) : null,
      pixFmt: pix ? pix[1] : null,
      profile: profile ? profile[1] : null,
      sampleRate: rate ? +rate[1] : null,
      channels: ch ? ch[1] : null,
    };
  }

  /** Clips from footage with the same key can be joined without re-encoding. */
  // Same idea as hclip/ffmpeg.py join_key: compare the camera's standard rate
  // (ffmpeg's log shows a rounded average that wobbles on phone footage).
  const joinKey = (i) => [i.video, i.profile, i.width, i.height, standardFps(i.fps), i.pixFmt,
    i.audio, i.sampleRate, i.channels].join("|");

  // Same as hclip/ffmpeg.py standard_fps: snap to the rate cameras really use.
  const STANDARD_FPS = [[23.976, "24000/1001"], [24, "24"], [25, "25"], [29.97, "30000/1001"], [30, "30"],
    [48, "48"], [50, "50"], [59.94, "60000/1001"], [60, "60"]];
  function standardFps(rate) {
    let v = typeof rate === "string" && rate.includes("/") ? (+rate.split("/")[0]) / (+rate.split("/")[1] || 1) : +rate;
    if (!v || v <= 0 || !isFinite(v)) return "25";
    v = Math.min(v, 60);
    let best = STANDARD_FPS[0];
    for (const f of STANDARD_FPS) if (Math.abs(f[0] - v) < Math.abs(best[0] - v)) best = f;
    return Math.abs(best[0] - v) / best[0] <= 0.015 ? best[1] : String(Math.round(v));
  }

  /** The one encode every piece of a re-made reel shares (see hclip/ffmpeg.py Look). */
  function lookFor(info, maxHeight) {
    let w = info.width || 1280, h = info.height || 720;
    if (maxHeight && h > maxHeight) { w = Math.round(w * maxHeight / h); h = maxHeight; }
    const even = (n) => Math.max(2, Math.floor(n / 2) * 2);
    return { width: even(w), height: even(h), fps: standardFps(info.fps), preset: "ultrafast", crf: 23 };
  }

  const encodeArgs = (look) => [
    "-c:v", "libx264", "-preset", look.preset, "-crf", String(look.crf),
    "-pix_fmt", "yuv420p", "-profile:v", "high",
    "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
    "-video_track_timescale", "90000",
  ];
  const fitFilter = (l) => `scale=${l.width}:${l.height}:force_original_aspect_ratio=decrease,` +
    `pad=${l.width}:${l.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=${l.fps},format=yuv420p`;
  const SILENCE = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"];

  function buildEncodeClipArgs(input, start, duration, out, look, { isConcat = false, hasAudio = true } = {}) {
    const args = ["-hide_banner", "-nostdin", "-y", ...seekArgs(start, isConcat, true)];
    if (isConcat) args.push("-f", "concat", "-safe", "0");
    args.push("-i", input);
    if (!hasAudio) args.push(...SILENCE);
    let vf = fitFilter(look);
    if (isConcat) {
      const [trimV, trimA] = preRollTrim(duration);
      vf = `${trimV},${vf}`;
      if (hasAudio) args.push("-af", trimA);
    }
    args.push("-t", duration.toFixed(3), "-map", "0:v:0", "-map", hasAudio ? "0:a:0" : "1:a:0",
      "-vf", vf, ...encodeArgs(look), "-f", "mp4", out);
    return args;
  }

  function buildCardArgs(image, seconds, out, look) {
    return ["-hide_banner", "-nostdin", "-y", "-loop", "1", "-framerate", look.fps,
      "-t", seconds.toFixed(3), "-i", image,   // input -t: a bad image errors instead of looping for ever
      "-t", seconds.toFixed(3), ...SILENCE, "-t", seconds.toFixed(3), "-map", "0:v:0", "-map", "1:a:0",
      "-vf", fitFilter(look), ...encodeArgs(look), "-f", "mp4", out];
  }

  // No +faststart here: in the browser it rewrites the whole file in memory.
  const buildJoinArgs = (list, out) => ["-hide_banner", "-nostdin", "-y", "-f", "concat", "-safe", "0",
    "-i", list, "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-f", "mp4", out];

  const TIME_LOG_RE = /time=\s*(\d+):(\d+):(\d+(?:\.\d+)?)/;

  class Engine {
    constructor(base) {
      this.base = base;           // URL of the vendor folder
      this.ff = null;
      this.loading = null;
      this.halves = new Map();    // slot index -> {files, dir, duration, ...}
      this.mountCounter = 0;
      this.logSink = null;
    }

    async ready(onStatus) {
      if (this.ff) return;
      if (!this.loading) {
        this.loading = (async () => {
          onStatus && onStatus("Loading the video tools (about 30 MB, first time only)…");
          const ff = new root.FFmpegWASM.FFmpeg();
          ff.on("log", ({ message }) => this.logSink && this.logSink(message));
          await ff.load({
            coreURL: new URL("ffmpeg-core.js", this.base).href,
            wasmURL: new URL("ffmpeg-core.wasm", this.base).href,
          });
          this.ff = ff;
          // Remount any videos chosen before a restart (after Stop).
          for (const [, h] of this.halves) await this._mount(h);
        })().finally(() => { this.loading = null; });
      }
      await this.loading;
    }

    async _mount(h) {
      h.dir = `/v${++this.mountCounter}`;
      await this.ff.createDir(h.dir);
      await this.ff.mount("WORKERFS", { files: h.files }, h.dir);
      h.input = `${h.dir}/${h.files[0].name}`;
    }

    /** Where to read one clip from: {input, start, isConcat, list}. A joined
     *  list (only for a clip running across two files) must be deleted after. */
    async _clipSource(h, start, duration) {
      const { first, last, localStart } = clipSource(h.durations, start, start + duration);
      const path = (f) => `${h.dir}/${f.name}`;
      if (first === last) return { input: path(h.files[first]), start: localStart, isConcat: false };
      const q = (f) => `file '${path(f).replace(/'/g, "'\\''")}'`;
      const files = h.files.slice(first, last + 1);
      const lines = ["ffconcat version 1.0", q(files[0]), `inpoint ${localStart.toFixed(3)}`, ...files.slice(1).map(q)];
      const list = `${h.dir}.clip.ffconcat`;
      await this.ff.writeFile(list, new TextEncoder().encode(lines.join("\n") + "\n"));
      return { input: list, start: 0, isConcat: true, list };
    }

    async _dropList(src) {
      if (src.list) { try { await this.ff.deleteFile(src.list); } catch (_) { /* gone */ } }
    }

    async _logOf(args) {
      const lines = [];
      this.logSink = (m) => lines.push(m);
      try { await this.ff.exec(args); } finally { this.logSink = null; }
      return lines.join("\n");
    }

    /** Choose the video(s) for one section. Returns {duration, ...} or throws. */
    async setHalf(index, fileList, onStatus) {
      await this.ready(onStatus);
      const files = sortParts(fileList);
      const names = new Set(files.map((f) => f.name));
      if (names.size !== files.length) throw new Error("Two of those files have the same name.");
      let duration = 0, video = null, audio = null, reencodeAudio = false, first = null;
      const durations = [];
      for (const f of files) {
        const tmp = { files: [f] };
        await this._mount(tmp);
        const info = parseProbeLog(await this._logOf(["-hide_banner", "-i", tmp.input]));
        await this.ff.unmount(tmp.dir);
        if (!info.duration || !info.video) throw new Error(`“${f.name}” doesn't look like a video this can read.`);
        duration += info.duration;
        durations.push(info.duration);
        first = first || info;
        video = video || info.video;
        audio = audio || info.audio;
        if (info.audio && !MP4_OK_AUDIO.has(info.audio)) reencodeAudio = true;
      }
      await this.clearHalf(index);
      const h = { files, duration, durations, video, audio, reencodeAudio, hevc: video === "hevc", info: first };
      await this._mount(h);
      this.halves.set(index, h);
      return {
        files: files.map((f) => f.name), duration,
        size: files.reduce((n, f) => n + f.size, 0), video,
        info: first, joinKey: joinKey(first),
      };
    }

    async clearHalf(index) {
      const h = this.halves.get(index);
      if (!h) return;
      this.halves.delete(index);
      if (this.ff) { try { await this.ff.unmount(h.dir); } catch (_) { /* already gone */ } }
    }

    /** Cut one clip; resolves to a Blob. onProgress(seconds written). */
    async cut(index, start, duration, { precise = false, onProgress } = {}) {
      await this.ready();
      const h = this.halves.get(index);
      const out = "/out.mp4";
      const src = await this._clipSource(h, start, duration);
      const args = buildCutArgs(src.input, src.start, duration, out, {
        precise, isConcat: src.isConcat, hevc: h.hevc, reencodeAudio: h.reencodeAudio,
      });
      const log = [];
      this.logSink = (m) => {
        log.push(m);
        if (log.length > 200) log.shift();
        const t = TIME_LOG_RE.exec(m);
        if (t && onProgress) onProgress(+t[1] * 3600 + +t[2] * 60 + +t[3]);
      };
      let code;
      try { code = await this.ff.exec(args); } finally { this.logSink = null; await this._dropList(src); }
      if (code !== 0) {
        try { await this.ff.deleteFile(out); } catch (_) { /* nothing written */ }
        const err = new Error("The video tools couldn't make this clip.");
        err.details = log.slice(-15).join("\n");
        throw err;
      }
      const data = await this.ff.readFile(out);
      await this.ff.deleteFile(out);
      return new Blob([data], { type: "video/mp4" });
    }

    async _run(args, out, onProgress) {
      const log = [];
      this.logSink = (m) => {
        log.push(m);
        if (log.length > 200) log.shift();
        const t = TIME_LOG_RE.exec(m);
        if (t && onProgress) onProgress(+t[1] * 3600 + +t[2] * 60 + +t[3]);
      };
      let code;
      try { code = await this.ff.exec(args); } finally { this.logSink = null; }
      if (code !== 0) {
        try { await this.ff.deleteFile(out); } catch (_) { /* nothing written */ }
        const err = new Error("The video tools couldn't make this part.");
        err.details = log.slice(-15).join("\n");
        throw err;
      }
      const data = await this.ff.readFile(out);
      await this.ff.deleteFile(out);
      return new Blob([data], { type: "video/mp4" });
    }

    /** One clip re-encoded to ``look`` (for reels with text screens). */
    async encodeClip(index, start, duration, look, { onProgress } = {}) {
      await this.ready();
      const h = this.halves.get(index);
      const src = await this._clipSource(h, start, duration);
      const args = buildEncodeClipArgs(src.input, src.start, duration, "/piece.mp4", look,
        { isConcat: src.isConcat, hasAudio: !!h.audio });
      try { return await this._run(args, "/piece.mp4", onProgress); } finally { await this._dropList(src); }
    }

    /** A text screen: ``png`` (Blob) held for ``seconds``. */
    async encodeCard(png, seconds, look, { onProgress } = {}) {
      await this.ready();
      await this.ff.writeFile("/card.png", new Uint8Array(await png.arrayBuffer()));
      try {
        return await this._run(buildCardArgs("/card.png", seconds, "/piece.mp4", look), "/piece.mp4", onProgress);
      } finally {
        try { await this.ff.deleteFile("/card.png"); } catch (_) { /* gone */ }
      }
    }

    /** Join pieces (Blobs sharing one encoding) by stream copy. The pieces are
     *  read from storage on demand, not loaded into memory first. */
    async join(blobs, { onProgress } = {}) {
      await this.ready();
      const dir = `/j${++this.mountCounter}`;
      await this.ff.createDir(dir);
      const named = blobs.map((b, i) => ({ name: `p${String(i).padStart(4, "0")}.mp4`, data: b }));
      await this.ff.mount("WORKERFS", { blobs: named }, dir);
      const list = `${dir}.ffconcat`;
      await this.ff.writeFile(list, new TextEncoder().encode(
        ["ffconcat version 1.0", ...named.map((n) => `file '${dir}/${n.name}'`)].join("\n") + "\n"));
      try {
        return await this._run(buildJoinArgs(list, "/reel.mp4"), "/reel.mp4", onProgress);
      } finally {
        try { await this.ff.unmount(dir); } catch (_) { /* gone */ }
        try { await this.ff.deleteFile(list); } catch (_) { /* gone */ }
      }
    }

    /** Stop immediately. The worker is killed; ready() reloads it. */
    stop() {
      if (this.ff) { this.ff.terminate(); this.ff = null; }
    }
  }

  const api = { Engine, buildCutArgs, buildEncodeClipArgs, buildCardArgs, buildJoinArgs,
    parseProbeLog, sortParts, joinKey, lookFor, standardFps, clipSource };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ClipEngine = api;
})(typeof self !== "undefined" ? self : this);
