/* GAA Clips in the browser: phone or laptop, nothing uploaded.
 * Makes one reel per half, or one video for the whole match, the same way
 * as hclip/reel.py: pieces (clips, text screens) first, then joined.
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const { parse, fmt, clipFilename, setLabel } = window.Notes;
  const { joinKey, lookFor } = window.ClipEngine;

  const END_SLACK_SECONDS = 15;                      // same as hclip/cut.py
  const JOIN_WEIGHT = 0.03;
  const isPhone = matchMedia("(pointer: coarse)").matches;
  const canPickFolder = typeof window.showDirectoryPicker === "function" && !isPhone;
  const shareProbe = typeof File === "function" ? [new File([""], "a.mp4", { type: "video/mp4" })] : null;
  const canShareFiles = !!(isPhone && navigator.canShare && shareProbe && navigator.canShare({ files: shareProbe }));

  const engine = new window.ClipEngine.Engine(new URL("vendor/", location.href));
  const tray = new window.ClipTray();

  let note = parse("");
  const slots = {};               // index -> {files, duration, size, info, joinKey} | {error} | {busy}
  let titleEdited = false;
  let running = false, stopping = false;
  let trayRows = [], trayFiles = [];
  let wakeLock = null;
  let lastFailures = [];
  let textSig = "", previewText = "";

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const size = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(b / 1e6))} MB`);
  const hash = (s) => { let h = 2166136261; for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619); return (h >>> 0).toString(36); };
  const safeName = (s) => s.replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").replace(/^[\s.]+|[\s.]+$/g, "") || "Match";
  const store = {
    get(k) { try { return localStorage.getItem(k) || ""; } catch (_) { return ""; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } },
  };

  function matchName() {
    return $("title").value.trim() || note.title.replace(/\s+clips?$/i, "") || "Match";
  }

  function sectionsForSlots() {
    return note.sections.length ? note.sections : [{ name: "1st Half", clips: [] }, { name: "2nd Half", clips: [] }];
  }

  // ---------------------------------------------------------------- plan

  function plan() {
    const problems = [];
    const kept = [];
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
        kept.push({ index: i, section: s, clip: c, start: c.start, end });
      }
    });
    kept.sort((a, b) => a.clip.line - b.clip.line);   // the note's order (a half can be named more than once)
    problems.sort((a, b) => a.line - b.line);
    const match = matchName();
    const jobs = kept.map((k, j) => ({
      ...k, number: j + 1, duration: k.end - k.start, match,
      name: clipFilename(j + 1, kept.length, k.section.tag, k.clip.start, k.clip.end, k.clip.label),
    }));
    if (!jobs.length) return { ready: false, message: "None of the clips fit inside the videos.", jobs, problems };
    const labelled = jobs.filter((j) => j.clip.label.trim()).length;
    const cards = $("cards").checked && labelled > 0;
    const maxH = isPhone ? 720 : 1080;
    // Like hclip/reel.py: text screens re-make every reel with one shared look;
    // otherwise each reel is copied if its own clips allow it.
    const sharedLook = cards ? lookFor(slots[jobs[0].index].info, maxH) : null;
    const used = {};
    const reel = (baseName, js) => {
      const n = used[baseName.toLowerCase()] = (used[baseName.toLowerCase()] || 0) + 1;
      const name = n === 1 ? baseName : `${baseName} (${n})`;   // two videos with one name mustn't clash
      const copy = !cards && new Set(js.map((j) => slots[j.index].joinKey)).size === 1;
      const look = sharedLook || (copy ? null : lookFor(slots[js[0].index].info, maxH));
      const duration = js.reduce((t, j) => t + j.duration, 0);
      // Rough output size, to stop a reel too big for the browser to join.
      const bytes = look
        ? duration * (look.height >= 1080 ? 1.2e6 : 0.6e6)
        : js.reduce((t, j) => t + slots[j.index].size / slots[j.index].duration * j.duration, 0);
      // A video named by the title line is saved under the title alone (as reel.py does).
      const file = name.trim().toLowerCase() === match.toLowerCase() ? safeName(match) : safeName(`${match} - ${name}`);
      return { name, jobs: js, file: `${file}.mp4`,
        key: `reel:${match}/${name}`, duration, look, bytes };
    };
    // One reel per video the note names ("2nd Video" lines), each mixing both
    // halves; otherwise one per half.
    const halves = (note.groups.length
      ? note.groups.map((g, gi) => reel(g, jobs.filter((j) => j.clip.group === gi)))
      : note.sections.map((s) => reel(s.name, jobs.filter((j) => j.section === s)))).filter((r) => r.jobs.length);
    const whole = reel("Whole match", jobs);
    halves.forEach((r, i) => { r.order = i; });   // saved videos are listed in the note's order
    whole.order = halves.length;
    const encode = cards || halves.some((r) => r.look) || !!whole.look;
    return { ready: true, message: "", jobs, problems, halves, whole, labelled, cards, encode };
  }

  // -------------------------------------------------------------- render

  function renderProblems(el, list) {
    el.innerHTML = list.slice().sort((a, b) => (a.line || 0) - (b.line || 0))
      .map((p) => `<li class="${p.fatal ? "fatal" : "warn"}">${p.line ? `Line ${p.line}: ` : ""}${esc(p.message)}</li>`).join("");
  }

  // The same video chosen for two sections is almost always a mistake (e.g.
  // both halves' files picked together in every slot): each section's timings
  // count from the start of its own video. Same rule as the Windows app.
  function sharedVideoWarnings() {
    const users = new Map();
    note.sections.forEach((s, i) => {
      for (const f of (slots[i] && slots[i].files) || []) users.set(f, [...(users.get(f) || []), s.name]);
    });
    return [...users].filter(([, names]) => names.length > 1).map(([f, names]) => ({
      line: null, fatal: false,
      message: `“${f}” is chosen for ${names.join(" and ")}. Each part of the note is timed from ` +
        "the start of its own video, so each usually needs a different video. Tap Change to fix it.",
    }));
  }

  function renderNote() {
    const s = note.sections, g = note.groups;
    const inGroup = (i) => note.clips.filter((c) => c.group === i).length;
    $("summary").innerHTML = !note.clipCount ? "" : g.length
      ? `<strong>${plural(note.clipCount, "clip")}</strong> for ${g.length} videos · ` + g.map((x, i) => `${esc(x)}: ${inGroup(i)}`).join(" · ")
      : `<strong>${plural(note.clipCount, "clip")}</strong> · ` + s.map((x) => `${esc(x.name)}: ${x.clips.length}`).join(" · ");
    // Several videos in the note: one per video instead of one per half.
    $("goHalves").textContent = g.length ? `Make ${g.length} videos` : "Half reels";
    renderProblems($("noteProblems"), note.problems);
    if (!titleEdited && note.title) $("title").value = note.title.replace(/\s+clips?$/i, "");
  }

  function renderPreview(text) {
    previewText = text;
    window.Cards.drawCard($("preview"), text || "Point from play - Conor");
  }

  function renderTextList() {
    const groups = note.groups;
    const rows = note.sections.flatMap((s) => s.clips.map((c) => ({ ...c, half: s.name })))
      .sort((a, b) => a.line - b.line);
    $("textCard").hidden = !rows.length;
    const sig = rows.map((r) => `${r.line}|${r.start}|${r.end}|${r.group}`).join(",") + groups.join("|");
    if (sig !== textSig) {
      textSig = sig;
      // With several videos in the note, a heading above each video's clips.
      const heading = (r, i) => groups.length && (i === 0 || rows[i - 1].group !== r.group)
        ? `<div class="tgroup">${esc(groups[r.group])}</div>` : "";
      $("textList").innerHTML = rows.map((r, i) => `${heading(r, i)}<div class="trow">
          <span class="half">${esc(r.half.replace(/\s*video$/i, "").replace(/\s*half$/i, ""))}</span>
          <span class="time">${fmt(r.start)}–${fmt(r.end)}</span>
          <input type="text" data-line="${r.line}" value="${esc(r.label)}" placeholder="No text screen" aria-label="Text before ${fmt(r.start)}">
        </div>`).join("");
    } else {
      for (const r of rows) {
        const el = $("textList").querySelector(`[data-line="${r.line}"]`);
        if (el && el !== document.activeElement) el.value = r.label;
      }
    }
    if (!previewText) {
      const first = rows.find((r) => r.label);
      renderPreview(first ? first.label : "");
    }
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

  function renderPlan() {
    const p = plan();
    const noteKeys = new Set(note.problems.map((x) => `${x.line}|${x.message}`));
    renderProblems($("planProblems"), [
      ...sharedVideoWarnings(),
      ...p.problems.filter((x) => !noteKeys.has(`${x.line}|${x.message}`)),
      ...lastFailures.map((f) => ({ line: f.line, fatal: true, message: f.message })),
    ]);
    const details = lastFailures.filter((f) => f.details).map((f) => f.details).join("\n\n");
    if (details) {
      $("planProblems").insertAdjacentHTML("beforeend",
        `<li class="fatal"><details><summary>Details</summary><pre>${esc(details)}</pre></details></li>`);
    }
    const labelled = p.labelled || 0;
    $("cardsHint").textContent = !p.ready ? "" : labelled
      ? `${labelled} of ${p.jobs.length} clips have text. Text screens mean the video is re-made` +
        (isPhone ? ": on a phone this can take 10 minutes or more for a half." : ", which takes a few minutes.")
      : "No clips have text yet. Add it in the list above.";
    $("preciseWrap").hidden = !!p.encode;
    const li = (r, cls = "") => `<li class="${cls}"><span>${esc(r.name)}</span><span class="meta">${plural(r.jobs.length, "clip")} · ${fmt(r.duration)}</span></li>`;
    $("reelList").innerHTML = p.ready ? p.halves.map((r) => li(r)).join("") + li(p.whole, "whole") : "";
    if (!running) {
      $("goHalves").disabled = $("goWhole").disabled = !p.ready;
      $("status").textContent = p.ready ? "" : p.message;
    }
    return p;
  }

  async function renderTray() {
    const rank = (r) => (r.kind === "reel" ? 0 : 1);   // reels first, then kept clips
    trayRows = (await tray.all()).filter((r) => r.kind !== "piece")
      .sort((a, b) => rank(a) - rank(b) || (a.order || 0) - (b.order || 0) || a.key.localeCompare(b.key));
    trayFiles = trayRows.map((r) => new File([r.blob], r.name, { type: "video/mp4" }));
    $("trayCard").hidden = !trayRows.length;
    const total = trayRows.reduce((n, r) => n + r.size, 0);
    $("saveAll").textContent = canShareFiles
      ? (trayRows.length === 1 ? "Save to Photos or Files" : `Save all ${trayRows.length} to Photos or Files`)
      : (trayRows.length === 1 ? "Download" : `Download all ${trayRows.length}`);
    $("trayHint").textContent = `${plural(trayRows.length, "video")} · ${size(total)}. ` +
      "They stay here until you Clear them.";
    $("clipList").innerHTML = trayRows.map((r, i) => `<li>
        <div><div class="name">${esc(r.name)}</div><div class="meta">${esc(r.match)} · ${fmt(r.duration)} · ${size(r.size)}</div></div>
        <div class="acts"><button class="small" data-play="${i}" type="button">Play</button>
          <button class="small" data-save="${i}" type="button">Save</button></div>
      </li>`).join("");
  }

  const refresh = async () => {
    note = parse($("note").value);
    renderNote();
    renderTextList();
    renderSlots();
    renderPlan();
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

  function setRunning(on) {
    running = on;
    $("running").hidden = !on;
    $("goButtons").hidden = on;
    $("stop").hidden = !on;
    renderSlots();
  }

  /** The pieces a set of reels needs, each made once and shared. */
  function piecesFor(p, reels) {
    const secs = Number($("cardSecs").value);
    const precise = $("precise").checked;
    const pieces = new Map();
    const reelPieces = reels.map((r) => r.jobs.flatMap((j) => {
      const src = slots[j.index].files.join("/") + "|" + slots[j.index].duration;
      const out = [];
      const text = j.clip.label.trim();
      if (p.cards && text) {
        const sig = hash(JSON.stringify(["card", text, secs, r.look]));
        pieces.set(sig, pieces.get(sig) || { sig, kind: "card", job: j, text, seconds: secs, look: r.look });
        out.push(sig);
      }
      const sig = r.look ? hash(JSON.stringify(["enc", src, j.start, j.end, r.look]))
        : hash(JSON.stringify(["copy", src, j.start, j.end, precise]));
      pieces.set(sig, pieces.get(sig) || { sig, kind: r.look ? "enc" : "copy", job: j, seconds: j.duration, look: r.look });
      out.push(sig);
      return out;
    }));
    return { pieces: [...pieces.values()], reelPieces };
  }

  async function writeToFolder(dir, name, blob) {
    const fh = await dir.getFileHandle(name, { create: true });
    const w = await fh.createWritable();
    await w.write(blob);
    await w.close();
  }

  async function start(what) {
    // The folder picker must open straight from the tap, before any await.
    let dir = null;
    if ($("useFolder").checked) {
      try { dir = await window.showDirectoryPicker({ mode: "readwrite", id: "gaa-clips" }); }
      catch (_) { return; }
    }
    const p = renderPlan();
    if (!p.ready) return;
    const reels = what === "whole" ? [p.whole] : p.halves;
    // The browser has to hold a finished video in memory twice while saving
    // it; past this it fails or the phone closes the page.
    const limit = isPhone ? 1.2e9 : 2e9;
    const tooBig = reels.find((r) => r.bytes > limit);
    if (tooBig) {
      $("status").textContent = `${tooBig.name} would be about ${size(tooBig.bytes)}, too big for ` +
        `${isPhone ? "the phone" : "the browser"} to put together. ` +
        (what === "whole" ? "Make half reels instead, or " : "") +
        (p.cards ? "use fewer or shorter clips." : "tick the text screens option (it makes a smaller 720p video)" +
          (isPhone ? "" : ", or use the Windows app") + ".");
      return;
    }
    const precise = $("precise").checked;
    const keepClips = $("keepClips").checked;
    const { pieces, reelPieces } = piecesFor(p, reels);
    const kindOf = new Map(pieces.map((pc) => [pc.sig, pc.kind]));

    stopping = false; lastFailures = [];
    setRunning(true);
    $("barFill").style.width = "0";
    await keepAwake();
    $("status").textContent = isPhone ? "Keep this screen open until it's finished." : "";

    // Throw away parts left from an earlier, different run (e.g. stopped,
    // then the timings changed) so they don't fill the phone's storage.
    const needed = new Set(pieces.map((pc) => `piece:${pc.sig}`));
    for (const rec of await tray.all()) {
      if (rec.kind === "piece" && !needed.has(rec.key)) await tray.delete(rec.key);
    }
    const todo = [];
    for (const pc of pieces) if (!(await tray.has(`piece:${pc.sig}`))) todo.push(pc);
    const total = todo.reduce((n, pc) => n + pc.seconds, 0) + reels.reduce((n, r) => n + r.duration * JOIN_WEIGHT, 0) || 1;
    let done = 0;
    const t0 = performance.now();
    const show = (stage, extra = 0) => {
      const overall = Math.min(1, (done + extra) / total);
      $("barFill").style.width = `${Math.round(overall * 100)}%`;
      $("progText").textContent = stage;
      const el = (performance.now() - t0) / 1000;
      $("progEta").textContent = overall > 0.03 ? fmtEta(el * (1 - overall) / overall) : "";
    };

    const failedLines = new Set();
    for (let i = 0; i < todo.length && !stopping; i++) {
      const pc = todo[i], j = pc.job;
      const stage = `Making part ${i + 1} of ${todo.length}`;
      show(stage);
      const onProgress = (s) => show(stage, Math.min(s, pc.seconds));
      try {
        let blob;
        if (pc.kind === "card") {
          const png = await window.Cards.cardBlob(pc.text, pc.look.width, pc.look.height);
          blob = await engine.encodeCard(png, pc.seconds, pc.look, { onProgress });
        } else if (pc.kind === "enc") {
          blob = await engine.encodeClip(j.index, j.start, j.duration, pc.look, { onProgress });
        } else {
          blob = await engine.cut(j.index, j.start, j.duration, { precise, onProgress });
        }
        await tray.put({ key: `piece:${pc.sig}`, kind: "piece", name: j.name, match: j.match,
          blob, size: blob.size, duration: pc.seconds });
        blob = null;
      } catch (e) {
        if (stopping) break;
        if (pc.kind !== "card") failedLines.add(j.clip.line);   // a failed screen leaves the clip in
        lastFailures.push({ line: j.clip.line, message: `the ${pc.kind === "card" ? "text screen" : "clip"} couldn't be made. ${e.message || e}`, details: e.details || "" });
      }
      done += pc.seconds;
      show(stage);
    }

    let made = 0;
    if (!stopping) {
      for (let r = 0; r < reels.length && !stopping; r++) {
        const reel = reels[r];
        const stage = `Joining ${reel.name}`;
        show(stage);
        try {
          const blobs = [];
          let clips = 0;
          for (const sig of reelPieces[r]) {
            const rec = await tray.get(`piece:${sig}`);
            if (!rec) continue;
            blobs.push(rec.blob);
            if (kindOf.get(sig) !== "card") clips++;
          }
          if (!clips) throw new Error("none of its clips could be made.");
          let blob = await engine.join(blobs, { onProgress: (s) => show(stage, Math.min(s, reel.duration) * JOIN_WEIGHT) });
          if (dir) await writeToFolder(dir, reel.file, blob);
          else await tray.put({ key: reel.key, kind: "reel", order: reel.order, name: reel.file, match: reel.jobs[0].match,
            blob, size: blob.size, duration: reel.duration + (p.cards ? reel.jobs.filter((j) => j.clip.label.trim()).length * Number($("cardSecs").value) : 0) });
          blob = null;
          made++;
        } catch (e) {
          if (stopping) break;
          lastFailures.push({ line: reel.jobs[0].clip.line, message: `${reel.name} couldn't be made: ${e.message || e}`, details: e.details || "" });
        }
        done += reel.duration * JOIN_WEIGHT;
        show(stage);
      }
    }

    // Keep each clip too, if asked (copy-mode pieces are the clips themselves).
    if (!stopping && keepClips) {
      for (const pc of pieces.filter((x) => x.kind !== "card")) {
        const rec = await tray.get(`piece:${pc.sig}`);
        if (!rec) continue;
        if (dir) await writeToFolder(dir, pc.job.name, rec.blob);
        else await tray.put({ key: `clip:${pc.job.match}/${pc.job.name}`, kind: "clip", name: pc.job.name,
          match: pc.job.match, blob: rec.blob, size: rec.size, duration: pc.seconds });
      }
    }
    // Tidy up the parts once everything worked; keep them after a Stop so it resumes.
    if (!stopping && !lastFailures.length) for (const pc of pieces) await tray.delete(`piece:${pc.sig}`);

    setRunning(false);
    if (wakeLock) { try { await wakeLock.release(); } catch (_) { /* already released */ } wakeLock = null; }
    await renderTray();
    await refresh();
    const bits = [];
    if (stopping) bits.push("Stopped. Press a Make button again to carry on from where it stopped.");
    else if (lastFailures.length) bits.push(`${plural(made, "video")} made; see the problems above.`);
    else bits.push(`Done: ${plural(made, "video")} made.`);
    if (failedLines.size && !stopping) bits.push(`Left out line ${[...failedLines].join(", ")}.`);
    if (dir && made) bits.push("Saved in the folder you chose.");
    $("status").textContent = bits.join(" ");
    if (made && !dir) $("trayCard").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function stop() {
    stopping = true;
    $("progEta").textContent = "Stopping…";
    engine.stop();                 // kills ffmpeg mid-part; it reloads next time
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
        ? "Couldn't share them all at once. Save them one at a time with each video's Save button."
        : "Couldn't open Save for that video.";
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
    $("note").placeholder = "Paste the timings from your phone's Notes, e.g.\n\nDunloy clips\n1:52-2:10\n3:05-3:33 Point from play - Conor\n\n2nd Half Video\n3:04-3:37";
    $("note").value = store.get("gaa-clips-note");
    $("title").value = store.get("gaa-clips-title");
    titleEdited = !!$("title").value;
    $("folderOpt").hidden = !canPickFolder;
    $("dest").textContent = isPhone
      ? "Videos are kept here until you save them to Photos or Files."
      : "Videos download to your Downloads folder" + (canPickFolder ? ", or tick below to pick a folder." : ".");

    $("note").addEventListener("input", () => { store.set("gaa-clips-note", $("note").value); refreshSoon(); });
    $("title").addEventListener("input", () => { titleEdited = true; store.set("gaa-clips-title", $("title").value); refreshSoon(); });
    for (const id of ["cards", "useFolder", "precise", "cardSecs"]) $(id).addEventListener("change", refresh);
    $("textList").addEventListener("input", (e) => {
      const el = e.target.closest("[data-line]");
      if (!el) return;
      $("note").value = setLabel($("note").value, Number(el.dataset.line), el.value);
      store.set("gaa-clips-note", $("note").value);
      renderPreview(el.value);
      refreshSoon();
    });
    $("textList").addEventListener("focusin", (e) => {
      const el = e.target.closest("[data-line]");
      if (el) renderPreview(el.value);
    });
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
    $("goHalves").addEventListener("click", () => start("halves"));
    $("goWhole").addEventListener("click", () => start("whole"));
    $("stop").addEventListener("click", stop);

    await tray.init();
    renderPreview("");
    await renderTray();
    await refresh();
  }

  init();
})();
