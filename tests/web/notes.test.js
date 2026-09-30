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
    if (c.groups) assert.deepEqual(note.groups, c.groups);
    if (c.clip_groups) assert.deepEqual(note.clips.map((x) => x.group), c.clip_groups);
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

test("setLabel writes the text into that line only", () => {
  const note = "Dunloy clips\n1:52 - 2:10\n3:05–3:33 old text\n2nd Half Video\n3:04-3:37";
  let t = Notes.setLabel(note, 2, "Point - Conor");
  assert.equal(t.split("\n")[1], "1:52 - 2:10 Point - Conor");
  t = Notes.setLabel(t, 3, "Goal");
  assert.equal(t.split("\n")[2], "3:05–3:33 Goal");
  t = Notes.setLabel(t, 3, "  ");
  assert.equal(t.split("\n")[2], "3:05–3:33");
  assert.equal(Notes.setLabel(t, 4, "x"), t);             // not a clip line
  const n = Notes.parse(Notes.setLabel(note, 2, "Point - Conor"));
  assert.equal(n.sections[0].clips[0].label, "Point - Conor");
  assert.equal(Notes.setLabel("1:00-1:10\r\n2:00-2:10", 2, "a"), "1:00-1:10\r\n2:00-2:10 a");
});

test("standardFps matches the Python snapping", () => {
  const { standardFps } = require("../../web/engine.js");
  const cases = [["115200000/4608001", "25"], ["30000/1001", "30000/1001"], ["143640/4789", "30"], ["2997/100", "30000/1001"],
    ["50", "50"], ["240", "60"], [null, "25"], ["12", "12"], ["59.94", "60000/1001"], ["29.97", "30000/1001"]];
  for (const [rate, want] of cases) assert.equal(standardFps(rate), want, String(rate));
});
