/* Coach's clip note parser - the browser twin of hclip/notes.py.
 * Both are checked against tests/data/note_cases.json so they can't drift.
 * Works as a browser global (window.Notes) and as a Node module (tests).
 */
(function (root) {
  "use strict";

  const TIME = String.raw`\d{1,3}[:.]\d{2}(?:[:.]\d{2})?`;
  const RANGE_RE = new RegExp(
    String.raw`^\s*(${TIME})\s*(?:-+|–|—|to)\s*(${TIME})\s*[-–—:,]?\s*(.*?)\s*$`, "i");
  const TIMEISH_RE = /^\s*\d{1,3}[:.]\d/;
  const TIME_RE = /^(\d{1,3})[:.](\d{2})(?:[:.](\d{2}))?$/;
  const LONG_CLIP_SECONDS = 90;
  // Same set as hclip/notes.py, including iPhone Notes' U+2028 soft breaks.
  const LINE_BREAK = /\r\n|[\n\r\u2028\u2029\x85]/;

  class TimecodeError extends Error {}

  function parseTime(text) {
    const t = text.trim();
    const m = TIME_RE.exec(t);
    if (!m) throw new TimecodeError(`“${t}” isn't a time`);
    let h = 0, mi, s;
    if (m[3] === undefined) { mi = +m[1]; s = +m[2]; }
    else {
      h = +m[1]; mi = +m[2]; s = +m[3];
      if (mi >= 60) throw new TimecodeError(`“${t}” isn't a time - minutes only go up to 59`);
    }
    if (s >= 60) throw new TimecodeError(`“${t}” isn't a time - seconds only go up to 59`);
    return h * 3600 + mi * 60 + s;
  }

  function fmt(seconds) {
    const total = Math.round(seconds);
    const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
    const ss = String(s).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
  }

  function fmtFilename(seconds) {
    const total = Math.round(seconds);
    const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
    const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
    return h ? `${h}h${mm}m${ss}` : `${mm}m${ss}`;
  }

  function sectionTag(name, position) {
    const n = name.toLowerCase();
    if (n.includes("penalt") || /\bpens\b/.test(n)) return "pens";
    const extra = n.includes("extra") || /\bet\s*\d?\b/.test(n);
    let which = null;
    if (/\b(1st|first|one)\b/.test(n) || /\b(et|time)\s*1\b/.test(n)) which = "1";
    else if (/\b(2nd|second|two)\b/.test(n) || /\b(et|time)\s*2\b/.test(n)) which = "2";
    if (extra) return which ? `et${which}` : "et";
    if (n.includes("half")) return `h${which || position}`;
    return null;
  }

  function parse(text) {
    let title = "";
    const sections = [];
    const problems = [];
    let current = null;

    const newSection = (name) => {
      const pos = sections.length + 1;
      const s = { name, tag: sectionTag(name, pos) || `part${pos}`, clips: [] };
      sections.push(s);
      return s;
    };

    const lines = text.split(LINE_BREAK);
    lines.forEach((raw, i) => {
      const lineno = i + 1;
      const line = raw.trim();
      if (!line) return;

      const m = RANGE_RE.exec(line);
      if (m) {
        const [, startS, endS, label] = m;
        let start, end;
        try { start = parseTime(startS); end = parseTime(endS); }
        catch (e) { problems.push({ line: lineno, message: `${e.message}. This line is skipped.`, fatal: true }); return; }
        if (end <= start) {
          problems.push({ line: lineno, message: `ends (${endS}) before it starts (${startS}). This line is skipped.`, fatal: true });
          return;
        }
        if (!current) current = newSection("1st Half");
        const clip = { start, end, label, line: lineno };
        if (end - start > LONG_CLIP_SECONDS) {
          problems.push({ line: lineno, message: `this clip is ${fmt(end - start)} long - is that a typo?`, fatal: false });
        }
        for (const o of current.clips) {
          if (clip.start < o.end && o.start < clip.end) {
            problems.push({ line: lineno, message: `overlaps the clip on line ${o.line}`, fatal: false });
          }
        }
        current.clips.push(clip);
        return;
      }

      if (TIMEISH_RE.test(line)) {
        problems.push({ line: lineno, message: `couldn't read “${line}”. Write it like 1:52-2:10. This line is skipped.`, fatal: true });
        return;
      }

      const isPeriod = sectionTag(line, sections.length + 1) !== null;
      if (isPeriod && (!current || current.clips.length)) current = newSection(line);
      else if (isPeriod) { current.name = line; current.tag = sectionTag(line, sections.length) || current.tag; }
      else if (!current && !title) title = line;
      else if (current && current.clips.length) current = newSection(line);
    });

    const kept = sections.filter((s) => s.clips.length);
    const seen = {};
    for (const s of kept) {
      if (seen[s.tag]) { seen[s.tag] += 1; s.tag = `${s.tag}_${seen[s.tag]}`; }
      else seen[s.tag] = 1;
    }
    return {
      title, sections: kept, problems,
      clipCount: kept.reduce((n, s) => n + s.clips.length, 0),
    };
  }

  function slugify(text, maxLen = 40) {
    let t = text.normalize("NFKD").replace(/[^\x00-\x7f]/g, "").toLowerCase();
    t = t.replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
    return t.slice(0, maxLen).replace(/_+$/, "");
  }

  function clipFilename(number, total, tag, start, end, label = "") {
    const width = Math.max(2, String(total).length);
    const parts = [String(number).padStart(width, "0"), tag, `${fmtFilename(start)}-${fmtFilename(end)}`];
    const slug = slugify(label);
    if (slug) parts.push(slug);
    return parts.join("_") + ".mp4";
  }

  const LEAD_RE = new RegExp(String.raw`^(\s*${TIME}\s*(?:-+|–|—|to)\s*${TIME})`, "i");

  /** Set the text shown before the clip on ``lineNo`` (1-based), keeping the
   *  coach's own times and every other line exactly as typed. */
  function setLabel(text, lineNo, label) {
    const nl = text.includes("\r\n") ? "\r\n" : "\n";
    const lines = text.split(LINE_BREAK);
    const m = LEAD_RE.exec(lines[lineNo - 1] || "");
    if (!m) return text;
    const clean = String(label).replace(/[\r\n]+/g, " ").trim();
    lines[lineNo - 1] = clean ? `${m[1]} ${clean}` : m[1];
    return lines.join(nl);
  }

  const api = { parse, setLabel, parseTime, fmt, fmtFilename, sectionTag, slugify, clipFilename, TimecodeError };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Notes = api;
})(typeof self !== "undefined" ? self : this);
