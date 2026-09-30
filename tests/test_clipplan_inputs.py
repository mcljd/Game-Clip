"""Everything a coach might paste into the timings box, and what the app must
make of it. Each case lists, per clip: (reel name, range as typed, video or
None, blocks-cutting?) plus the lines it should list as not understood.
Videos are labelled "1st half" (0) and "2nd half" (1) unless a case says so."""

import pytest

from hclip import clipplan

H1, H2, NONE = 0, 1, None
OK, BAD = False, True
LABELS = ["1st half", "2nd half"]


def rows(text, labels=LABELS):
    plan = clipplan.parse(text, labels)
    return ([(plan.reels[r.reel].name, r.range_text, r.video, bool(r.error)) for r in plan.rows],
            [t for _, t in plan.ignored])


CASES = {
    # ------------------------------------------------------------- halves
    "half headers spelled every way": (
        "1st half\n1:00-1:10\nSecond Half\n1:20-1:30\nFIRST HALF:\n1:40-1:50\n2nd half.\n2:00-2:10\n"
        "first-half\n2:20-2:30\nHalf 2\n2:40-2:50\nhalf one\n3:00-3:10\nHalf Two\n3:20-3:30",
        [H1, H2, H1, H2, H1, H2, H1, H2], []),
    "glued and short forms": (
        "1sthalf\n1:00-1:10\n2ndHalf\n1:20-1:30\nH1\n1:40-1:50\nH2\n2:00-2:10\n1H\n2:20-2:30\n"
        "2h\n2:40-2:50\n1st\n3:00-3:10\n2nd\n3:20-3:30",
        [H1, H2, H1, H2, H1, H2, H1, H2], []),
    "typos in 'half' are fine": (
        "1st hlaf\n1:00-1:10\n2nd haf\n1:20-1:30\nsecnd half\n1:40-1:50\n1st hallf\n2:00-2:10\nfrist half\n2:20-2:30",
        [H1, H2, H2, H1, H1], []),
    "a half header with words and emoji around it": (
        "2nd half 🔥 (wind behind)\n1:00-1:10\n** 1st Half **\n1:20-1:30\n2nd half video\n1:40-1:50",
        [H2, H1, H2], []),
    "unclear halves are never guessed": (
        "half time\n1:00-1:10\n2md half\n1:20-1:30\nthird half\n1:40-1:50",
        [NONE, NONE, NONE], []),
    "no header at all": ("1:00-1:10\n2:00-2:10", [NONE, NONE], []),
    "a half header then a new reel forgets it": (
        "2nd half\n1:00-1:10\nreel 2\n1:20-1:30", [H2, NONE], []),

    # ------------------------------------------------------------- times
    "time formats and separators": (
        "1st half\n3:43-4:01\n3.43-4.01\n3:43 - 4:01\n3:43–4:01\n3:43—4:01\n3:43 to 4:01\n3:43−4:01\n"
        "03:43-04:01\n1:02:03-1:02:30\n59:50-1:00:10\n3:43\t-\t4:01\n90:00-91:00",
        [H1] * 12, []),
    "list markers from the phone": (
        "1st half\n- 3:43-4:01\n• 3:43-4:01\n* 3:43-4:01\n1. 3:43-4:01\n2) 3:43-4:01\n◦ 3:43-4:01\n"
        "☐ 3:43-4:01\n✓ 3:43-4:01\n-3:43-4:01\n    3:43-4:01\n1.52-2.10",
        [H1] * 11, []),
    "words before, after, or both": (
        "1st half\nGoal 3:43-4:01\n3:43-4:01 Point - Conor\n3:43-4:01: great score\nnice one 3:43-4:01 wide",
        [H1] * 4, []),
    "a header and a clip on the same line": (
        "2nd half 3:10-3:34\n1st half: 1:00-1:10 goal", [H2, H1], []),
    "two clips on one line": (
        "1st half\n3:43-4:01 5:00-5:10\n3:43-4:01 goal, 5:00-5:10 point", [H1] * 4, []),

    # ------------------------------------------------------------- mistakes block cutting
    "broken times are kept as red rows": (
        "1st half\n3:43-4:1\n3:43-\n3:43\n1:75-2:00\n4:01-3:43\n3:43-4:01-4:10\n3:43 4:01\n5:00-5:00",
        [(H1, BAD)] * 8, []),
    "a WhatsApp time stuck on the end": ("1st half\n8:05-8:21 19:03", [(H1, BAD)], []),

    # ------------------------------------------------------------- reels
    "reel headers": (
        "Reel 1\n1st half\n1:00-1:10\nREEL 2\n1st half\n1:00-1:10\nreel3\n1st half\n1:00-1:10\n"
        "Reel #4\n1st half\n1:00-1:10\nreel 5:\n1st half\n1:00-1:10\nReel 6 - forwards\n1st half\n1:00-1:10\n"
        "Reel 7 2nd half\n1:00-1:10\nReel 8: 1st half\n1:00-1:10\nreel 9 Video\n2nd half\n1:00-1:10",
        [("reel 1", H1), ("reel 2", H1), ("reel 3", H1), ("reel 4", H1), ("reel 5", H1),
         ("reel 6 - forwards", H1), ("reel 7", H2), ("reel 8", H1), ("reel 9", H2)], []),
    "reels named like the output files": (
        "bc clip 1\n1st half\n1:00-1:10\nbc clip 2\n2nd half\n2:00-2:10\nClip 3\n1st half\n3:00-3:10",
        [("bc clip 1", H1), ("bc clip 2", H2), ("Clip 3", H1)], []),
    "the coach's older 'Video' lines": (
        "Video Ballycastle\n1st half\n3:43-4:01\n2nd Half\n3:10-3:34\n2nd Video\n1st half\n6:56-7:07\n"
        "Video 3\n2nd half\n1:00-1:10\nnext video\n1st half\n1:00-1:10",
        [("Video Ballycastle", H1), ("Video Ballycastle", H2), ("2nd Video", H1),
         ("Video 3", H2), ("next video", H1)], []),
    "'video' in a note isn't a new reel": (
        "1st half\n1:00-1:10\nvideo ref\n1:20-1:30\nclipped the bar\n1:40-1:50",
        [("reel 1", H1)] * 3, ["video ref", "clipped the bar"]),
    "an empty reel is dropped": ("reel 1\nreel 2\n1st half\n1:00-1:10", [("reel 2", H1)], []),

    # ------------------------------------------------------------- other lines
    "titles and notes are listed, not lost": (
        "Ballycastle v Cushendall\n1st half\n1:00-1:10\ngood hook here\nscored at 3:43\n17",
        [H1], ["Ballycastle v Cushendall", "good hook here", "scored at 3:43", "17"]),
    "blank lines, spaces, CRLF and iPhone line breaks": (
        "reel 1\r\n\r\n   1st half   \r\n 1:00-1:10  2nd half 2:00-2:10\r\n\r\n",
        [H1, H2], []),
}


def _expand(expected):
    out = []
    for e in expected:
        if isinstance(e, tuple) and len(e) == 2 and isinstance(e[0], str):
            out.append((e[0], e[1], OK))
        elif isinstance(e, tuple):
            out.append((None, e[0], e[1]))
        else:
            out.append((None, e, OK))
    return out


@pytest.mark.parametrize("name", list(CASES))
def test_input(name):
    text, expected, ignored = CASES[name]
    got, got_ignored = rows(text)
    want = _expand(expected)
    assert len(got) == len(want), got
    for (reel, rng, video, bad), (w_reel, w_video, w_bad) in zip(got, want):
        assert video == w_video, (rng, got)
        assert bad == w_bad, (rng, got)
        if w_reel is not None:
            assert reel == w_reel, (rng, got)
    assert got_ignored == ignored


# ----------------------------------------------------------- details that matter

def test_text_screens_come_from_the_words_with_each_clip():
    plan = clipplan.parse("1st half\nGoal 3:43-4:01\n3:43-4:01 - Point - Conor\n"
                          "3:43-4:01 goal, 5:00-5:10 point\n2nd half 6:00-6:10 wide", LABELS)
    assert [r.text for r in plan.rows] == ["Goal", "Point - Conor", "goal", "point", "wide"]
    # Only a clip alone on a plain "range text" line can have its text edited in place.
    assert [r.own_line for r in plan.rows] == [False, True, False, False, False]


def test_list_markers_still_allow_text_editing():
    plan = clipplan.parse("1st half\n- 3:43-4:01\n1. 5:00-5:10 goal", LABELS)
    assert [r.own_line for r in plan.rows] == [True, True]


def test_broken_rows_say_what_is_wrong():
    plan = clipplan.parse("1st half\n3:43-4:1\n1:75-2:00\n4:01-3:43\n3:43-4:01-4:10\n5:00-5:00", LABELS)
    msgs = [r.error for r in plan.rows]
    assert "couldn't read “3:43-4:1”" in msgs[0]
    assert "seconds only go up to 59" in msgs[1]
    assert "ends (3:43) before it starts (4:01)" in msgs[2]
    assert "more than two times" in msgs[3]
    assert "ends (5:00) before it starts (5:00)" in msgs[4]
    assert plan.rows[0].range_text == "3:43-4:1"                  # shown as typed


def test_custom_labels():
    labels = ["Cushendall 1st half.m4v", "Cushendall 2nd half.m4v", "Drone"]
    got, _ = rows("First half\n1:00-1:10\nH2\n1:20-1:30\ndrone\n1:40-1:50", labels)
    assert [g[2] for g in got] == [0, 1, 2]


def test_labels_renamed_by_the_coach():
    got, _ = rows("1st half\n1:00-1:10\n2nd half\n1:20-1:30", ["Half 1", "Half 2"])
    assert [g[2] for g in got] == [0, 1]
    got, _ = rows("1st half\n1:00-1:10", ["1st half wide", "1st half close"])   # two matches
    assert got[0][2] is None


def test_extra_time_and_penalties_need_their_own_videos():
    labels = ["1st half", "2nd half", "Extra time 1", "Extra time 2", "Penalties"]
    got, _ = rows("ET1\n1:00-1:10\nextra time 2\n1:20-1:30\nET 2\n1:40-1:50\npens\n0:10-0:20\n"
                  "Extra time\n0:30-0:40", labels)
    assert [g[2] for g in got] == [2, 3, 3, 4, None]          # "Extra time" alone: which one?
    got, _ = rows("ET1\n1:00-1:10", LABELS)                   # no extra-time video added
    assert got[0][2] is None


def test_choices_follow_the_reel_name_when_a_reel_is_added_above():
    text = "reel 1\n1st half\n1:00-1:10\nreel 2\n1st half\n3:10-3:34\n"
    plan = clipplan.parse(text, LABELS)
    key = plan.reels[1].rows[0].key                           # reel 2 · 3:10-3:34
    edited = "reel 0\n1st half\n3:10-3:34\n" + text           # a new reel with the same range, above
    again = clipplan.parse(edited, LABELS, {key: 1})
    by_reel = {again.reels[r.reel].name: r.video for r in again.rows if r.range_text == "3:10-3:34"}
    assert by_reel == {"reel 0": 0, "reel 2": 1}
    assert not again.stale_choices


def test_whatsapp_copy_of_the_real_note():
    # As pasted from the coach's WhatsApp message (with its blank lines and
    # trailing spaces), after changing the headers to reels.
    text = ("reel 1 \n\n1st half\n3:43-4:01\n11:26-11:40\n11:50-12:13\n15:51-16:21\n16:32-17:00\n2nd Half\n"
            "3:10-3:34\n12:36-13:09\n26:58-27:16\n32:00-32:12\n\n\n\nreel 2 Video\n\n1st half\n6:56-7:07\n"
            "17:38-17:59\n2nd half \n5:05-5:15\n5:33-5:44\n6:33-6:47\n6:58-7:12\n7:23-7:50 \n8:05-8:21")
    plan = clipplan.parse(text, LABELS)
    chk = clipplan.check(plan, [2000.0, 2000.0], pad_end=1)
    assert chk.can_cut
    assert [sum(c.length for c in chk.cuts if c.row.reel == i) for i in (0, 1)] == [209, 132]
