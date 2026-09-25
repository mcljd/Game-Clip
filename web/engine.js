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

  function buildCutArgs(input, start, duration, out, opts = {}) {
    const args = ["-hide_banner", "-nostdin", "-y", "-ss", start.toFixed(3)];
    if (opts.isConcat) args.push("-f", "concat", "-safe", "0");
    args.push("-i", input, "-t", duration.toFixed(3), "-map", "0:v:0", "-map", "0:a:0?");
    if (opts.precise) {
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

  function parseProbeLog(text) {
    const d = /Duration: (\d+):(\d+):(\d+(?:\.\d+)?)/.exec(text);
    const v = /Stream #0:\d+[^:]*: Video: (\w+)/.exec(text);
    const a = /Stream #0:\d+[^:]*: Audio: (\w+)/.exec(text);
    return {
      duration: d ? +d[1] * 3600 + +d[2] * 60 + +d[3] : 0,
      video: v ? v[1] : null,
      audio: a ? a[1] : null,
    };
  }

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
      if (h.files.length > 1) {
        const list = ["ffconcat version 1.0",
          ...h.files.map((f) => `file '${h.dir}/${f.name.replace(/'/g, "'\\''")}'`)].join("\n");
        h.input = `${h.dir}.ffconcat`;
        await this.ff.writeFile(h.input, new TextEncoder().encode(list + "\n"));
      } else {
        h.input = `${h.dir}/${h.files[0].name}`;
      }
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
      let duration = 0, video = null, audio = null, reencodeAudio = false;
      for (const f of files) {
        const tmp = { files: [f] };
        await this._mount(tmp);
        const info = parseProbeLog(await this._logOf(["-hide_banner", "-i", tmp.input]));
        await this.ff.unmount(tmp.dir);
        if (!info.duration || !info.video) throw new Error(`“${f.name}” doesn't look like a video this can read.`);
        duration += info.duration;
        video = video || info.video;
        audio = audio || info.audio;
        if (info.audio && !MP4_OK_AUDIO.has(info.audio)) reencodeAudio = true;
      }
      await this.clearHalf(index);
      const h = { files, duration, video, audio, reencodeAudio, hevc: video === "hevc" };
      await this._mount(h);
      this.halves.set(index, h);
      return {
        files: files.map((f) => f.name), duration,
        size: files.reduce((n, f) => n + f.size, 0), video,
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
      const args = buildCutArgs(h.input, start, duration, out, {
        precise, isConcat: h.files.length > 1, hevc: h.hevc, reencodeAudio: h.reencodeAudio,
      });
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
        const err = new Error("The video tools couldn't make this clip.");
        err.details = log.slice(-15).join("\n");
        throw err;
      }
      const data = await this.ff.readFile(out);
      await this.ff.deleteFile(out);
      return new Blob([data], { type: "video/mp4" });
    }

    /** Stop immediately. The worker is killed; ready() reloads it. */
    stop() {
      if (this.ff) { this.ff.terminate(); this.ff = null; }
    }
  }

  const api = { Engine, buildCutArgs, parseProbeLog, sortParts };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ClipEngine = api;
})(typeof self !== "undefined" ? self : this);
