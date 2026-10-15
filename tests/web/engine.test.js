// Command builders and file-picking of the browser engine (web/engine.js).
// clipSource must agree with the Python HalfVideo.clip_source.
const test = require("node:test");
const assert = require("node:assert/strict");
const { clipSource, buildCutArgs, buildEncodeClipArgs } = require("../../web/engine.js");

test("a clip inside one file is read from that file", () => {
  const d = [100, 100, 100];
  assert.deepEqual(clipSource(d, 10, 20), { first: 0, last: 0, localStart: 10 });
  assert.deepEqual(clipSource(d, 150, 160), { first: 1, last: 1, localStart: 50 });
  assert.deepEqual(clipSource(d, 100, 110), { first: 1, last: 1, localStart: 0 });   // on the join
  assert.deepEqual(clipSource(d, 190, 200.05), { first: 1, last: 1, localStart: 90 }); // rounding slack
});

test("a clip across a join spans only the files it needs", () => {
  const d = [100, 100, 100];
  assert.deepEqual(clipSource(d, 195, 210), { first: 1, last: 2, localStart: 95 });
  assert.deepEqual(clipSource(d, 95, 250), { first: 0, last: 2, localStart: 95 });
});

test("a single file seeks with -ss before the input", () => {
  const args = buildCutArgs("/v1/a.mp4", 90, 5, "/o.mp4");
  assert.ok(args.indexOf("-ss") < args.indexOf("-i"));
});

test("a joined input never uses -ss (it can't seek and would copy the whole half)", () => {
  const args = buildCutArgs("/v1.clip.ffconcat", 0, 5, "/o.mp4", { isConcat: true });
  assert.ok(!args.includes("-ss"));
  const enc = buildEncodeClipArgs("/v1.clip.ffconcat", 0, 5, "/o.mp4",
    { width: 1280, height: 720, fps: "25", preset: "ultrafast", crf: 23 }, { isConcat: true });
  assert.ok(enc.includes("-copyts") && !enc.includes("-ss"));
  assert.match(enc[enc.indexOf("-vf") + 1], /^trim=start=0:duration=5\.000/);
  assert.match(enc[enc.indexOf("-af") + 1], /^atrim=start=0/);
});
