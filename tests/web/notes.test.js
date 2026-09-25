// Run with: node --test tests/web
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const Notes = require("../../web/notes.js");

const cases = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "data", "note_cases.json"), "utf8"));

for (const c of cases) {
  test(c.name, () => {
    const note = Notes.parse(c.text);
    assert.equal(note.title, c.title);
    assert.deepEqual(note.sections.map((s) => [s.tag, s.clips.length]), c.sections);
    assert.deepEqual(note.problems.map((p) => [p.line, p.fatal]), c.problems);
    if (c.clips) {
      assert.deepEqual(note.sections.flatMap((s) => s.clips.map((x) => [x.start, x.end, x.label])), c.clips);
    }
  });
}

test("messages match the Python wording", () => {
  const n = Notes.parse("1:75-2:00");
  assert.equal(n.problems[0].message, "“1:75” isn't a time - seconds only go up to 59. This line is skipped.");
});

test("filenames match the Python app", () => {
  assert.equal(Notes.clipFilename(1, 15, "h1", 112, 130), "01_h1_01m52-02m10.mp4");
  assert.equal(Notes.clipFilename(3, 120, "h2", 185, 213, "Goal!"), "003_h2_03m05-03m33_goal.mp4");
  assert.equal(Notes.slugify("Seán Ó Dónaill!"), "sean_o_donaill");
  assert.equal(Notes.fmt(3723), "1:02:03");
});
