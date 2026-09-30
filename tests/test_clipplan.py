"""Parser + pre-fill (hclip/clipplan.py): the coach's acceptance cases first."""

import pytest

from hclip import clipplan

LABELS = ["1st half", "2nd half"]

ACCEPTANCE = """reel 1
1st half
3:43-4:01
11:26-11:40
11:50-12:13
15:51-16:21
16:32-17:00
2nd Half
3:10-3:34
12:36-13:09
26:58-27:16
32:00-32:12

reel 2 Video
1st half
6:56-7:07
17:38-17:59
2nd half
5:05-5:15
5:33-5:44
6:33-6:47
6:58-7:12
7:23-7:50
8:05-8:21
"""

NO_HEADERS = "\n".join(l for l in ACCEPTANCE.splitlines()
                       if not any(w in l.lower() for w in ("reel", "half")))


def videos_of(plan):
    return [[r.video for r in reel.rows] for reel in plan.reels]


def test_acceptance_prefill():
    plan = clipplan.parse(ACCEPTANCE, LABELS)
    assert [r.name for r in plan.reels] == ["reel 1", "reel 2"]
    assert videos_of(plan) == [[0] * 5 + [1] * 4, [0] * 2 + [1] * 6]
    assert not plan.unselected and not plan.ignored
    assert all(r.chosen_by == "note" for r in plan.rows)


def test_acceptance_lengths_with_one_second_pad():
    plan = clipplan.parse(ACCEPTANCE, LABELS)
    chk = clipplan.check(plan, [2000.0, 2000.0], pad_start=0, pad_end=1)
    assert chk.can_cut and not chk.warnings
    per_reel = [[c.length for c in chk.cuts if c.row.reel == i] for i in (0, 1)]
    assert per_reel[0] == [19, 15, 24, 31, 29, 25, 34, 19, 13] and sum(per_reel[0]) == 209
    assert per_reel[1] == [12, 22, 11, 12, 15, 15, 28, 17] and sum(per_reel[1]) == 132


def test_without_headers_every_row_is_unselected_and_cut_is_blocked():
    plan = clipplan.parse(NO_HEADERS, LABELS)
    assert len(plan.rows) == 17 and len(plan.unselected) == 17
    assert len(plan.reels) == 1                   # no reel lines: one reel
    assert all(r.why == "no half header above it" for r in plan.rows)
    chk = clipplan.check(plan, [2000.0, 2000.0])
    assert not chk.can_cut and len(chk.errors) == 17
    # Choosing a video for every row unblocks it.
    picks = {r.key: 0 for r in plan.rows}
    assert clipplan.check(clipplan.parse(NO_HEADERS, LABELS, picks), [2000.0, 2000.0]).can_cut


def test_reel_order_is_as_written_not_grouped_by_video():
    plan = clipplan.parse("reel 1\n2nd half\n1:00-1:10\n1st half\n0:10-0:20\n2nd half\n2:00-2:10",
                          LABELS)
    assert [(r.video, r.start) for r in plan.rows] == [(1, 60), (0, 10), (1, 120)]


@pytest.mark.parametrize("header,expected", [
    ("1st half", 0), ("1ST HALF", 0), ("First half", 0), ("first half video", 0),
    ("2nd Half", 1), ("second half", 1), ("2nd hlaf", 1), ("2nd  half:", 1), ("Half 2", 1),
    ("2nd Half Video", 1),
])
def test_headers_match_labels(header, expected):
    plan = clipplan.parse(f"{header}\n1:00-1:10", LABELS)
    assert plan.rows[0].video == expected


@pytest.mark.parametrize("header,why", [
    ("half time", "doesn't say which half"),
    ("2md half", "doesn't say which half"),        # never guess the ordinal
    ("extra time 1", "doesn't match any video"),
])
def test_unmatched_headers_leave_rows_unselected(header, why):
    plan = clipplan.parse(f"{header}\n1:00-1:10", LABELS)
    assert plan.rows[0].video is None and why in plan.rows[0].why


def test_header_matching_several_labels_is_not_guessed():
    plan = clipplan.parse("1st half\n1:00-1:10", ["1st half (wide)", "1st half (close)"])
    assert plan.rows[0].video is None and "more than one" in plan.rows[0].why


def test_custom_labels_match_by_name():
    plan = clipplan.parse("Wide cam\n1:00-1:10\nclose cam\n2:00-2:10", ["Close cam", "Wide cam"])
    assert [r.video for r in plan.rows] == [1, 0]


def test_any_number_of_videos():
    labels = ["1st half", "2nd half", "Extra time 1", "Extra time 2", "Penalties"]
    plan = clipplan.parse("ET1\n1:00-1:10\nextra time 2\n0:10-0:20\npens\n0:05-0:09", labels)
    assert [r.video for r in plan.rows] == [2, 3, 4]


def test_new_reel_forgets_the_last_half():
    plan = clipplan.parse("reel 1\n2nd half\n1:00-1:10\nreel 2\n2:00-2:10", LABELS)
    assert [r.video for r in plan.rows] == [1, None]


def test_old_style_video_lines_start_reels():
    note = "Video Ballycastle\n1st half\n3:43-4:01\n2nd Half\n3:10-3:34\n2nd Video\n1st half\n6:56-7:07"
    plan = clipplan.parse(note, LABELS)
    assert [r.name for r in plan.reels] == ["Video Ballycastle", "2nd Video"]
    assert videos_of(plan) == [[0, 1], [0]]


def test_ignored_lines_are_listed():
    plan = clipplan.parse("Ballycastle clips\n1st half\n1:00-1:10\ngood hook here\n", LABELS)
    assert plan.ignored == [(1, "Ballycastle clips"), (4, "good hook here")]


def test_tolerates_crlf_blank_lines_dashes_and_hours():
    plan = clipplan.parse("reel 1\r\n\r\n1st half\r\n3:43 – 4:01\r\n1:02:03—1:02:30 Goal\r\n", LABELS)
    assert [(r.start, r.end, r.text) for r in plan.rows] == [(223, 241, ""), (3723, 3750, "Goal")]


def test_bad_rows_are_kept_and_block_cutting():
    plan = clipplan.parse("1st half\n4:01-3:43\n1:75-2:00\n1:00-1:10", LABELS)
    assert [r.position for r in plan.rows] == [1, 2, 3]
    assert "before it starts" in plan.rows[0].error and "59" in plan.rows[1].error
    chk = clipplan.check(plan, [100.0, 100.0])
    assert not chk.can_cut and len(chk.errors) == 2 and len(chk.cuts) == 1


# ---------------------------------------------------------- hand choices

def test_hand_choice_beats_prefill_and_survives_reparse():
    plan = clipplan.parse(ACCEPTANCE, LABELS)
    row = plan.reels[0].rows[5]                          # 3:10-3:34, pre-filled 2nd half
    choices = {row.key: 0}
    again = clipplan.parse(ACCEPTANCE, LABELS, choices)
    got = again.reels[0].rows[5]
    assert (got.video, got.chosen_by) == (0, "you")


def test_hand_choice_follows_its_clip_when_lines_are_inserted():
    plan = clipplan.parse(ACCEPTANCE, LABELS)
    key = plan.reels[0].rows[5].key                    # reel 1 · 3:10-3:34
    edited = ACCEPTANCE.replace("2nd Half\n", "2nd Half\n1:00-1:05\n", 1)   # a clip added above it
    again = clipplan.parse(edited, LABELS, {key: 0})
    moved = next(r for r in again.rows if r.range_text == "3:10-3:34")
    assert moved.position == 7 and moved.video == 0 and moved.chosen_by == "you"
    assert again.reels[0].rows[5].chosen_by == "note"   # the new clip isn't affected
    assert not again.stale_choices


def test_hand_choice_for_a_removed_clip_is_reported():
    plan = clipplan.parse(ACCEPTANCE, LABELS)
    key = plan.reels[0].rows[5].key
    again = clipplan.parse(ACCEPTANCE.replace("3:10-3:34\n", ""), LABELS, {key: 0})
    assert again.stale_choices == [key]
    assert clipplan.describe_key(key) == "reel 1 · 3:10-3:34"


def test_same_range_twice_in_a_reel_keeps_separate_choices():
    plan = clipplan.parse("1:00-1:10\n1:00-1:10", LABELS)
    a, b = plan.rows
    assert a.key != b.key
    again = clipplan.parse("1:00-1:10\n1:00-1:10", LABELS, {b.key: 1})
    assert [r.video for r in again.rows] == [None, 1]


# ---------------------------------------------------------- checking

def test_check_against_the_chosen_video():
    plan = clipplan.parse("1st half\n29:00-29:59\n2nd half\n29:00-29:59\n40:00-40:10", LABELS)
    chk = clipplan.check(plan, [30 * 60.0, 35 * 60.0], pad_end=1)
    first = chk.cuts[0]
    assert first.end == 30 * 60 and not first.warning          # pad trimmed at the end, clip fits
    assert chk.cuts[1].end == 29 * 60 + 60                       # padded normally
    assert len(chk.errors) == 1 and "only 35:00 long" in chk.errors[0][1]


def test_changing_a_rows_video_rechecks_it():
    plan = clipplan.parse("1st half\n33:00-33:10", LABELS)
    assert not clipplan.check(plan, [30 * 60.0, 35 * 60.0]).can_cut
    moved = clipplan.parse("1st half\n33:00-33:10", LABELS, {plan.rows[0].key: 1})
    assert clipplan.check(moved, [30 * 60.0, 35 * 60.0]).can_cut


def test_pad_start_is_trimmed_at_zero():
    plan = clipplan.parse("1st half\n0:00-0:05", LABELS)
    [c] = clipplan.check(plan, [100.0, 100.0], pad_start=2, pad_end=1).cuts
    assert (c.start, c.end) == (0, 6)
