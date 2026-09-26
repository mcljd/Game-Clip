from pathlib import Path

from hclip import notes

DUNLOY = (Path(__file__).parent / "data" / "dunloy.txt").read_text()


def test_real_coach_note():
    note = notes.parse(DUNLOY)
    assert note.title == "Dunloy clips"
    assert [s.tag for s in note.sections] == ["h1", "h2"]
    assert [len(s.clips) for s in note.sections] == [11, 4]
    assert note.problems == []
    first, last = note.sections[0].clips[0], note.sections[1].clips[-1]
    assert (first.start, first.end) == (112, 130)
    assert (last.start, last.end) == (907, 939)
    assert note.sections[1].name == "2nd Half Video"


def test_separators_spacing_and_labels():
    note = notes.parse("1:52 - 2:10\n3:05–3:33 Goal Conor\n4:00 to 4:20: point\n5:00—5:10")
    clips = note.sections[0].clips
    assert [(c.start, c.end) for c in clips] == [(112, 130), (185, 213), (240, 260), (300, 310)]
    assert [c.label for c in clips] == ["", "Goal Conor", "point", ""]


def test_explicit_first_half_header_and_extra_time():
    note = notes.parse(
        "Cushendall v St Endas\nFirst half\n1:00-1:10\nSecond Half\n2:00-2:10\n"
        "Extra time 1\n0:30-0:40\nET2\n1:00-1:20\nPenalties\n0:10-0:20"
    )
    assert note.title == "Cushendall v St Endas"
    assert [s.tag for s in note.sections] == ["h1", "h2", "et1", "et2", "pens"]


def test_non_period_text_after_clips_starts_new_section():
    note = notes.parse("Dunloy\n1:00-1:10\nSecond video\n2:00-2:10")
    assert len(note.sections) == 2
    assert note.sections[1].tag == "part2"


def test_errors_carry_line_numbers_and_dont_stop_parsing():
    note = notes.parse("Title\n1:52-1:40\n1:75-2:00\n3:05-\n4:00-4:10")
    assert [(p.line, p.fatal) for p in note.problems] == [(2, True), (3, True), (4, True)]
    assert note.clip_count == 1


def test_warnings_for_long_and_overlapping_clips():
    note = notes.parse("1:00-3:00\n2:30-2:50")
    msgs = {(p.line, p.fatal) for p in note.problems}
    assert (1, False) in msgs  # long
    assert (2, False) in msgs  # overlaps line 1
    assert note.clip_count == 2


def test_empty_note():
    note = notes.parse("\n\n")
    assert note.sections == [] and note.clip_count == 0
