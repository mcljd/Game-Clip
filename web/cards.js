/* Text screens: white text on black, shown before a clip in a reel.
 * Drawn on a canvas at the reel's exact picture size, then encoded by ffmpeg.
 * The Windows app (hclip/app/ui.html) carries a copy of drawCard; keep them
 * in step.
 */
(function (root) {
  "use strict";
  const FONT = '"Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, Arial, sans-serif';
  const MAROON = "#8a1a2c";

  function wrap(ctx, text, maxWidth) {
    const words = text.split(/\s+/).filter(Boolean);
    const lines = [];
    let line = "";
    for (const w of words) {
      const test = line ? `${line} ${w}` : w;
      if (ctx.measureText(test).width <= maxWidth || !line) line = test;
      else { lines.push(line); line = w; }
    }
    if (line) lines.push(line);
    return lines;
  }

  function drawCard(canvas, text) {
    const ctx = canvas.getContext("2d");
    const w = canvas.width, h = canvas.height;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, w, h);
    const maxWidth = w * 0.84;
    let size = Math.round(h * 0.09), lines;
    // Shrink until it fits in 4 lines and every line fits across.
    for (;;) {
      ctx.font = `700 ${size}px ${FONT}`;
      lines = wrap(ctx, text.trim(), maxWidth);
      const widest = Math.max(...lines.map((l) => ctx.measureText(l).width));
      if ((lines.length <= 4 && widest <= maxWidth) || size <= h * 0.035) break;
      size = Math.round(size * 0.9);
    }
    const lineHeight = size * 1.2;
    const bar = Math.max(3, Math.round(h * 0.008));
    const gap = size * 0.55;
    const block = lines.length * lineHeight + gap + bar;
    let y = (h - block) / 2 + lineHeight / 2;
    ctx.fillStyle = "#fff";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const l of lines) { ctx.fillText(l, w / 2, y); y += lineHeight; }
    const barWidth = Math.round(w * 0.08);
    ctx.fillStyle = MAROON;
    ctx.fillRect(Math.round((w - barWidth) / 2), Math.round(y - lineHeight / 2 + gap), barWidth, bar);
  }

  function cardCanvas(text, width, height) {
    const c = document.createElement("canvas");
    c.width = width; c.height = height;
    drawCard(c, text);
    return c;
  }

  function cardBlob(text, width, height) {
    const c = cardCanvas(text, width, height);
    return new Promise((resolve) => c.toBlob(resolve, "image/png"));
  }

  root.Cards = { drawCard, cardCanvas, cardBlob };
})(self);
