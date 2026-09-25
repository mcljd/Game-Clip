/* GAA Clips in the browser: phone or laptop, nothing uploaded. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const { parse, fmt, clipFilename } = window.Notes;

  const END_SLACK_SECONDS = 15;                      // same as hclip/cut.py
  const isPhone = matchMedia("(pointer: coarse)").matches;
  const canPickFolder = typeof window.showDirectoryPicker === "function" && !isPhone;
  const shareProbe = typeof File === "function" ? [new File([""], "a.mp4", { type: "video/mp4" })] : null;
  const canShareFiles = !!(isPhone && navigator.canShare && shareProbe && navigator.canShare({ files: shareProbe }));

  const engine = new window.ClipEngine.Engine(new URL("vendor/", location.href));
  const tray = new window.ClipTray();

  let note = parse("");
  const slots = {};               // index -> {files, duration, size, video} | {error} | {busy}
  let titleEdited = false;
  let running = false, stopping = false;
  let trayRows = [], trayFiles = [];
  let wakeLock = null;
  let lastFailures = [];          // clips that failed in the last run, shown with the plan's problems

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const size = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(b / 1e6))} MB`);
  const store = {
    get(k) { try { return localStorage.getItem(k) || ""; } catch (_) { return ""; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } },
  };

  function matchName() {
    const t = $("title").value.trim() || note.title.replace(/\s+clips?$/i, "") || "Match";
    return t;
  }

  function sectionsForSlots() {
    return note.sections.length ? note.sections : [{ name: "1st Half", clips: [] }, { name: "2nd Half", clips: [] }];
  }

  // ---------------------------------------------------------------- plan

  function plan() {
    const problems = [];
    const kept = [];
    const n = note.sections.length;
    const missing = note.sections.filter((s, i) => !(slots[i] && slots[i].duration)).map((s) => s.name);
    if (!note.clipCount) return { ready: false, message: "Paste the clip timings to begin.", jobs: [], problems };
    if (missing.length) return { ready: false, message: `Choose the video for: ${missing.join(", ")}`, jobs: [], problems };
    note.sections.forEach((s, i) => {
      const dur = slots[i].duration;
      for (const c of s.clips) {
        if (c.start >= dur) {
          problems.push({ line: c.line, fatal: true, message: `starts at ${fmt(c.start)} but the ${s.name} video is only ${fmt(dur)} long, so it's skipped` });
          continue;
        }
        let end = c.end;
        if (end > dur) {
          if (end - dur > END_SLACK_SECONDS) {
            problems.push({ line: c.line, fatal: true, message: `ends at ${fmt(c.end)} but the ${s.name} video is only ${fmt(dur)} long, so it's skipped` });
            continue;
          }
          end = dur;
          problems.push({ line: c.line, fatal: false, message: `trimmed to end of video (${fmt(end)})` });
        }
        kept.push({ index: i, tag: s.tag, clip: c, start: c.start, end });
      }
    });
    const match = matchName();
    const jobs = kept.map((k, j) => {
      const name = clipFilename(j + 1, kept.length, k.tag, k.clip.start, k.clip.end, k.clip.label);
      return { ...k, number: j + 1, name, key: `${match}/${name}`, match, duration: k.end - k.start };
    });
    if (!jobs.length) return { ready: false, message: "None of the clips fit inside the videos.", jobs, problems };
    return { ready: n > 0, message: "", jobs, problems };
  }

  // -------------------------------------------------------------- render

  function renderProblems(el, list) {
    el.innerHTML = list.slice().sort((a, b) => a.line - b.line)
      .map((p) => `<li class="${p.fatal ? "fatal" : "warn"}">Line ${p.line}: ${esc(p.message)}</li>`).join("");
  }

  function renderNote() {
    const s = note.sections;
    $("summary").innerHTML = note.clipCount
      ? `<strong>${plural(note.clipCount, "clip")}</strong> · ` + s.map((x) => `${esc(x.name)}: ${x.clips.length}`).join(" · ")
      : "";
    renderProblems($("noteProblems"), note.problems);
    if (!titleEdited && note.title) $("title").value = note.title.replace(/\s+clips?$/i, "");
  }

  function renderSlots() {
    $("slots").innerHTML = sectionsForSlots().map((s, i) => {
      const info = slots[i];
      const ok = info && info.duration;
      const chips = s.clips.map((c) => `<span class="chip">${fmt(c.start)}–${fmt(c.end)}</span>`).join("");
      let body = "";
      if (info && info.busy) body = `<div class="meta">Reading the video…</div>`;
      else if (info && info.error) body = `<div class="err">${esc(info.error)}</div>`;
      else if (ok) body = info.files.map((f) => `<div class="file">${esc(f)}</div>`).join("") +
        `<div class="meta">${fmt(info.duration)} long · ${size(info.size)}${info.files.length > 1 ? " · joined in order" : ""}</div>`;
      return `<div class="slot ${ok ? "done" : ""}">
        <div class="slot-head"><strong>${esc(s.name)}</strong>
          <span class="slot-actions">${ok ? `<button class="link" data-clear="${i}" type="button">Remove</button>` : ""}
          <button class="small" data-pick="${i}" type="button" ${running || (info && info.busy) ? "disabled" : ""}>${ok ? "Change" : "Choose video"}</button></span></div>
        ${body}${chips ? `<div class="chips">${chips}</div>` : ""}
      </div>`;
    }).join("");
  }

  async function renderPlan() {
    const p = plan();
    const noteKeys = new Set(note.problems.map((x) => `${x.line}|${x.message}`));
    renderProblems($("planProblems"), [
      ...p.problems.filter((x) => !noteKeys.has(`${x.line}|${x.message}`)),
      ...lastFailures.map((f) => ({ line: f.job.clip.line, fatal: true,
        message: `${f.job.name} couldn't be made. ${f.message}` })),
    ]);
    const details = lastFailures.filter((f) => f.details).map((f) => f.details).join("\n\n");
    if (details) {
      $("planProblems").insertAdjacentHTML("beforeend",
        `<li class="fatal"><details><summary>Details</summary><pre>${esc(details)}</pre></details></li>`);
    }
    let existing = 0;
    if (p.ready && !$("useFolder").checked) {
      for (const j of p.jobs) if (await tray.has(j.key)) existing++;
    }
    const toMake = p.jobs.length - existing;
    if (!running) {
      $("go").disabled = !p.ready || toMake === 0;
      $("go").textContent = !p.ready ? "Make clips"
        : toMake === 0 ? "All clips made"
        : existing ? `Make ${plural(toMake, "more clip")}` : `Make ${plural(p.jobs.length, "clip")}`;
      $("status").textContent = p.ready
        ? (toMake === 0 ? "All clips are made. Save them below, or Clear to make them again."
          : existing ? `${existing} already made; the rest will be added.` : "")
        : p.message;
    }
    return p;
  }

  async function renderTray() {
    trayRows = await tray.all();
    trayFiles = trayRows.map((r) => new File([r.blob], r.name, { type: "video/mp4" }));
    $("trayCard").hidden = !trayRows.length;
    const total = trayRows.reduce((n, r) => n + r.size, 0);
    $("saveAll").textContent = canShareFiles ? `Save all ${trayRows.length} to Photos or Files` : `Download all ${trayRows.length}`;
    $("trayHint").textContent = `${plural(trayRows.length, "clip")} · ${size(total)}. ` +
      (canShareFiles ? "Tap a clip to watch it. Clips stay here until you Clear them."
        : "Clips stay in this browser until you Clear them.");
    $("clipList").innerHTML = trayRows.map((r, i) => `<li>
        <div><div class="name">${esc(r.name)}</div><div class="meta">${esc(r.match)} · ${fmt(r.duration)} · ${size(r.size)}</div></div>
        <div class="acts"><button class="small" data-play="${i}" type="button">Play</button>
          <button class="small" data-save="${i}" type="button">Save</button></div>
      </li>`).join("");
  }

  const refresh = async () => {
    note = parse($("note").value);
    renderNote();
    renderSlots();
    await renderPlan();
  };
  let refreshTimer = null;
  const refreshSoon = () => { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 250); };

  // -------------------------------------------------------------- videos

  let pickIndex = 0;
  function pick(i) {
    pickIndex = i;
    $("picker").value = "";
    $("picker").click();
  }

  $("picker").addEventListener("change", async () => {
    const files = [...$("picker").files];
    if (!files.length) return;
    const i = pickIndex;
    slots[i] = { busy: true };
    renderSlots();
    try {
      slots[i] = await engine.setHalf(i, files, (msg) => { $("status").textContent = msg; });
    } catch (e) {
      slots[i] = { error: `Couldn't open that video. ${e.message || e}` };
    }
    $("status").textContent = "";
    await refresh();
  });

  // ----------------------------------------------------------------- run

  function fmtEta(s) {
    if (s == null || !isFinite(s)) return "";
    return s < 60 ? `about ${Math.max(1, Math.round(s))} s left` : `about ${Math.round(s / 60)} min left`;
  }

  async function keepAwake() {
    try { if (navigator.wakeLock) wakeLock = await navigator.wakeLock.request("screen"); } catch (_) { wakeLock = null; }
  }
  document.addEventListener("visibilitychange", () => {
    if (running && document.visibilityState === "visible") keepAwake();
  });
  window.addEventListener("beforeunload", (e) => { if (running) { e.preventDefault(); e.returnValue = ""; } });

  async function fileExists(dir, name) {
    try { await dir.getFileHandle(name); return true; } catch (_) { return false; }
  }

  async function start() {
    if (running) { stop(); return; }
    // The folder picker must open straight from the tap, before any await.
    let dir = null;
    if ($("useFolder").checked) {
      try { dir = await window.showDirectoryPicker({ mode: "readwrite", id: "gaa-clips" }); }
      catch (_) { return; }
    }
    const p = await renderPlan();
    if (!p.ready) return;
    running = true; stopping = false; lastFailures = [];
    $("running").hidden = false;
    $("go").textContent = "Stop";
    $("go").disabled = false;
    $("barFill").style.width = "0";
    renderSlots();
    await keepAwake();

    const precise = $("precise").checked;
    const todo = [];
    let skipped = 0;
    for (const j of p.jobs) {
      const exists = dir ? await fileExists(dir, j.name) : await tray.has(j.key);
      if (exists) skipped++; else todo.push(j);
    }
    const totalSecs = todo.reduce((n, j) => n + j.duration, 0) || 1;
    let doneSecs = 0, made = 0;
    const failed = [];
    const t0 = performance.now();

    const show = (j, i, secs) => {
      const overall = Math.min(1, (doneSecs + Math.min(secs, j.duration)) / totalSecs);
      $("barFill").style.width = `${Math.round(overall * 100)}%`;
      $("progText").textContent = `Clip ${i + 1} of ${todo.length}`;
      const el = (performance.now() - t0) / 1000;
      $("progEta").textContent = overall > 0.03 ? fmtEta(el * (1 - overall) / overall) : "";
    };

    $("status").textContent = isPhone ? "Keep this screen open until it's finished." : "";
    for (let i = 0; i < todo.length && !stopping; i++) {
      const j = todo[i];
      show(j, i, 0);
      try {
        let blob = await engine.cut(j.index, j.start, j.duration, {
          precise, onProgress: (secs) => show(j, i, secs),
        });
        if (dir) {
          const fh = await dir.getFileHandle(j.name, { create: true });
          const w = await fh.createWritable();
          await w.write(blob);
          await w.close();
        } else {
          await tray.put({ key: j.key, match: j.match, name: j.name, blob, size: blob.size,
            duration: j.duration, line: j.clip.line, made: Date.now() });
        }
        blob = null;
        made++;
      } catch (e) {
        if (stopping) break;
        failed.push({ job: j, message: e.message || String(e), details: e.details || "" });
      }
      doneSecs += j.duration;
      show(j, i, 0);
    }

    running = false;
    lastFailures = failed;
    $("running").hidden = true;
    if (wakeLock) { try { await wakeLock.release(); } catch (_) { /* already released */ } wakeLock = null; }
    await renderTray();
    await refresh();
    const bits = [];
    if (stopping) bits.push(`Stopped. ${plural(made, "clip")} made; press Make clips to finish the rest.`);
    else bits.push(failed.length ? `${made} made, ${failed.length} failed.` : `Done: ${plural(made, "clip")} made.`);
    if (skipped) bits.push(`${skipped} were already there.`);
    if (dir && !stopping) bits.push(`Saved in the folder you chose.`);
    $("status").textContent = bits.join(" ");
    if (made && !dir) $("trayCard").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function stop() {
    stopping = true;
    $("progEta").textContent = "Stopping…";
    engine.stop();                 // kills ffmpeg mid-clip; it reloads next time
  }

  // ---------------------------------------------------------------- save

  function download(file) {
    const url = URL.createObjectURL(file);
    const a = document.createElement("a");
    a.href = url; a.download = file.name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }

  async function share(files) {
    try { await navigator.share({ files, title: matchName() }); }
    catch (e) {
      if (e && e.name === "AbortError") return;     // closed the share sheet
      $("status").textContent = files.length > 1
        ? "Couldn't share them all at once. Save them one at a time with each clip's Save button."
        : "Couldn't open Save for that clip.";
    }
  }

  $("saveAll").addEventListener("click", () => {
    if (!trayFiles.length) return;
    if (canShareFiles) share(trayFiles);
    else trayFiles.forEach((f, i) => setTimeout(() => download(f), i * 400));
  });

  $("clipList").addEventListener("click", (e) => {
    const s = e.target.closest("[data-save]"), p = e.target.closest("[data-play]");
    if (s) {
      const f = trayFiles[+s.dataset.save];
      if (canShareFiles) share([f]); else download(f);
    }
    if (p) {
      const v = $("player");
      if (v.src) URL.revokeObjectURL(v.src);
      v.src = URL.createObjectURL(trayFiles[+p.dataset.play]);
      v.hidden = false;
      v.play().catch(() => {});
      v.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  });

  let clearArmed = false;
  $("clearTray").addEventListener("click", async () => {
    if (!clearArmed) {
      clearArmed = true;
      $("clearTray").textContent = "Tap again to delete";
      setTimeout(() => { clearArmed = false; $("clearTray").textContent = "Clear"; }, 4000);
      return;
    }
    clearArmed = false;
    $("clearTray").textContent = "Clear";
    $("player").hidden = true;
    $("player").removeAttribute("src");
    await tray.clear();
    await renderTray();
    await refresh();
  });

  // ---------------------------------------------------------------- init

  async function init() {
    if (typeof WebAssembly !== "object" || typeof Worker !== "function") {
      $("banner").hidden = false;
      $("banner").textContent = "This browser can't run the video tools. Use Safari on iPhone, or Chrome or Edge on a computer.";
    }
    $("note").placeholder = "Paste the timings from your phone's Notes, e.g.\n\nDunloy clips\n1:52-2:10\n3:05-3:33 point from play\n\n2nd Half Video\n3:04-3:37";
    $("note").value = store.get("gaa-clips-note");
    $("title").value = store.get("gaa-clips-title");
    titleEdited = !!$("title").value;
    $("folderOpt").hidden = !canPickFolder;
    $("dest").textContent = isPhone
      ? "Clips are kept here until you save them to Photos or Files."
      : canPickFolder ? "Clips download to your Downloads folder, or tick below to pick a folder." : "Clips download to your Downloads folder.";

    $("note").addEventListener("input", () => { store.set("gaa-clips-note", $("note").value); refreshSoon(); });
    $("title").addEventListener("input", () => { titleEdited = true; store.set("gaa-clips-title", $("title").value); refreshSoon(); });
    $("useFolder").addEventListener("change", refresh);
    $("pasteBtn").addEventListener("click", async () => {
      try {
        const text = await navigator.clipboard.readText();
        if (text) { $("note").value = text; store.set("gaa-clips-note", text); await refresh(); }
      } catch (_) {
        $("note").focus();
        $("status").textContent = "Press and hold in the box, then tap Paste.";
      }
    });
    $("slots").addEventListener("click", async (e) => {
      const p = e.target.closest("[data-pick]"), c = e.target.closest("[data-clear]");
      if (p) pick(+p.dataset.pick);
      if (c) { await engine.clearHalf(+c.dataset.clear); delete slots[c.dataset.clear]; await refresh(); }
    });
    $("go").addEventListener("click", start);

    await tray.init();
    await renderTray();
    await refresh();
  }

  init();
})();
