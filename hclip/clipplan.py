"""Turn pasted timings into a clip list: reels of clips, each clip from one
chosen video. Pure functions only (no ffmpeg, no files), so every rule here
is unit-tested.

The model:
- The coach adds any number of source VIDEOS, each with a label
  ("1st half", "2nd half", ...). Times are relative to the start of that file.
- The pasted text describes REELS (outputs). A reel is an ordered list of
  clips; each clip is (video, start, end). A reel can mix clips from any
  videos, in the order written.
- "reel 2" (or an older-style "2nd Video" line) starts a new reel. A new
  reel never means a new video file.
- A half header ("1st half", "2nd Half", "second half") pre-selects the video
  whose label matches it, for the clips under it, until the next header or
  reel. A header that matches no label (or several) and a clip with no header
  above it in its reel stay UNSELECTED: the app never guesses.
- The coach's own choice for a clip always wins over the pre-fill. Choices
  are keyed by reel + the clip's time range (+ which repeat of that range),
  so adding or removing other lines doesn't move them onto the wrong clip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from hclip import timecode
from hclip.notes import split_lines

_TIME = r"\d{1,3}[:.]\d{2}(?:[:.]\d{2})?"
# Dashes phones put in: hyphen, en/em dash, minus sign, non-breaking hyphens; or "to".
_PAIR_RE = re.compile(rf"({_TIME})\s*(?:-+|[–—−‐‑]|\bto\b)\s*({_TIME})", re.IGNORECASE)
_TIME_START_RE = re.compile(rf"^\s*{_TIME}")          # a line (or leftover) that starts with a time
_TIMEISH_RE = re.compile(r"^\s*\d{1,3}[:.]\d")        # ...or looks like it was meant to
# List markers phones add: "- ", "• ", "* ", "1. ", "2) ", checkboxes.
_BULLET_RE = re.compile(r"^\s*(?:[-–—•*·◦○●▪■□☐☑✓✔>]+|\d{1,2}[.)](?=\s))\s*")
_SEPARATORS = " \t-–—:,;|"

# Ordinals as people type them (and a few common slips).
_FIRST = {"1st", "1", "first", "one", "frist", "fisrt", "1rst", "1sr"}
_SECOND = {"2nd", "2", "second", "two", "secnd", "seccond", "secound", "2n", "2nf"}


def _words(text: str) -> list[str]:
    # "1sthalf" / "2ndHalf" -> "1st half"; "H2" / "2H" -> "2nd half".
    text = re.sub(r"(?i)(1st|2nd|first|second)(?=half)", r"\1 ", text)
    text = re.sub(r"(?i)\b(?:h([12])|([12])h)\b", lambda m: f"{'1st' if (m[1] or m[2]) == '1' else '2nd'} half", text)
    return re.findall(r"[a-z0-9]+", text.lower())


def _near(word: str, target: str) -> bool:
    """Same word allowing one typo (swap, missing, extra or wrong letter)."""
    if word == target:
        return True
    if abs(len(word) - len(target)) > 1 or len(word) < 3:
        return False
    if len(word) == len(target):
        diff = [i for i in range(len(word)) if word[i] != target[i]]
        if len(diff) == 1:
            return True
        return (len(diff) == 2 and diff[1] == diff[0] + 1
                and word[diff[0]] == target[diff[1]] and word[diff[1]] == target[diff[0]])
    short, long_ = sorted((word, target), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def period_key(text: str) -> tuple[str, int | None] | None:
    """What part of the match a header or label names: ("half", 1),
    ("half", 2), ("et", 1), ("et", 2), ("pens", None), ("half", None) when it
    doesn't say which half; None if it doesn't name a half at all.
    Typos are tolerated in "half" but never in which one (1st/2nd): a slip
    there could pick the wrong video. A line that's just "1st" or "2nd"
    names that half."""
    words = _words(text)
    joined = " ".join(words)
    if any(w.startswith("penalt") or w == "pens" for w in words):
        return ("pens", None)
    et = re.search(r"\bet\s*([12])\b|\bextra\b", joined)
    which: int | None = None
    for w in words:
        if w in _FIRST or re.fullmatch(r"et1", w):
            which = 1
            break
        if w in _SECOND or re.fullmatch(r"et2", w):
            which = 2
            break
    if et or any(re.fullmatch(r"et[12]", w) for w in words):
        if et and et.group(1):
            which = int(et.group(1))
        return ("et", which)
    if any(_near(w, "half") or w in ("haf", "hlf", "hf") for w in words):
        return ("half", which)
    if words in (["1st"], ["first"], ["2nd"], ["second"]):
        return ("half", which)
    return None


def reel_header(line: str) -> str | None:
    """The reel's name if this line starts one: "reel 2", "Reel 6 - forwards",
    "game clip 1", "2nd Video", "Video Final". None otherwise. ("2nd
    Half Video" names a half, not a reel.)"""
    words = _words(line)
    joined = " ".join(words)
    m = re.match(r"^(?:reel|reeel|rell)\s*(\d+)\b(.*)$", joined)
    if m:
        rest = re.sub(r"\b(videos?)\b", "", m[2])
        rest_raw = re.sub(r"(?i)^\W*(?:reel|reeel|rell)\s*\d+\W*|\bvideos?\b", "", line).strip(_SEPARATORS)
        if not rest.strip() or period_key(rest) is not None:
            return f"reel {int(m[1])}"
        return f"reel {int(m[1])} - {rest_raw}"
    if joined.startswith(("reel", "reeel", "rell")) and len(words) <= 3:
        return line.strip(_SEPARATORS) or "reel"
    if len(words) <= 4 and re.search(r"\bclips?\s*\d+$", joined):   # "Clip 2", "game clip 1"
        return line.strip(_SEPARATORS)
    # The coach's older notes: "2nd Video", "Video 3" start a reel. (A video
    # named before any clips, like "Video Final", is handled in parse().)
    if period_key(line) is None and re.fullmatch(
            r"(?:\d+(?:st|nd|rd|th)?|first|second|third|fourth|fifth|next|another)\s+videos?"
            r"|videos?\s*(?:\d+|one|two|three|four|five)", joined):
        return line.strip(_SEPARATORS)
    return None


def match_video(header: str, labels: Sequence[str]) -> tuple[int | None, str]:
    """The video a header pre-selects, or (None, why not)."""
    key = period_key(header)
    norm = " ".join(_words(header))
    exact = [i for i, lab in enumerate(labels) if " ".join(_words(lab)) == norm and norm]
    if len(exact) == 1:
        return exact[0], ""
    if key is not None:
        if key[0] != "pens" and key[1] is None:
            return None, f"“{header}” doesn't say which half"
        same = [i for i, lab in enumerate(labels) if period_key(lab) == key]
        if len(same) == 1:
            return same[0], ""
        if len(same) > 1:
            return None, f"“{header}” matches more than one video"
    return None, f"“{header}” doesn't match any video's label"


# --------------------------------------------------------------------------
# The clip list
# --------------------------------------------------------------------------

@dataclass
class Row:
    reel: int                 # 0-based reel index
    position: int             # 1-based position in its reel
    line: int                 # 1-based line in the pasted text
    start: float | None       # None when the time couldn't be read
    end: float | None
    range_text: str           # "3:10-3:34", as the key for the coach's choice
    text: str                 # words with the range: the text screen
    header: str | None        # the half header above it in this reel
    video: int | None = None  # index into the videos, or None
    chosen_by: str = ""       # "note" (pre-filled), "you" (picked by hand), ""
    why: str = ""             # why it's unselected, for the highlight
    error: str = ""           # a problem that blocks cutting (bad time, end<=start)
    key: str = ""
    own_line: bool = True     # the only clip on its line, written "range text": its text can be edited in place

    @property
    def length(self) -> float | None:
        if self.start is None or self.end is None:
            return None
        return self.end - self.start


@dataclass
class Reel:
    name: str
    rows: list[Row] = field(default_factory=list)
    key_name: str = ""        # the name, made unique ("reel 1 (2)"), for choice keys


@dataclass
class Plan:
    reels: list[Reel]
    ignored: list[tuple[int, str]]            # (line, text) not understood
    stale_choices: list[str] = field(default_factory=list)  # hand choices that no longer match a clip

    @property
    def rows(self) -> list[Row]:
        return [r for reel in self.reels for r in reel.rows]

    @property
    def unselected(self) -> list[Row]:
        return [r for r in self.rows if r.video is None]


def choice_key(reel: str, range_text: str, repeat: int) -> str:
    """Keyed by the reel's name, not its number, so adding a reel above
    doesn't move choices onto another reel's clips."""
    return f"{reel}|{range_text}|{repeat}"


def describe_key(key: str) -> str:
    reel, rng, repeat = key.rsplit("|", 2)
    return f"{reel} · {rng}" + (f" (repeat {int(repeat) + 1})" if int(repeat) else "")


def parse(text: str, labels: Sequence[str],
          choices: Mapping[str, int] | None = None) -> Plan:
    """The clip list for ``text`` with the videos labelled ``labels``.
    ``choices`` are the coach's own picks (key -> video index); they win over
    the pre-fill."""
    reels: list[Reel] = [Reel("reel 1")]
    ignored: list[tuple[int, str]] = []
    current: int | None = None          # pre-selected video from the last header
    header: str | None = None
    why = "no half header above it"
    seen: dict[str, int] = {}

    def start_reel(name: str) -> None:
        nonlocal current, header, why
        if reels[-1].rows:
            reels.append(Reel(name))
        else:
            reels[-1].name = name        # a header naming the reel before its clips
        current, header, why = None, None, "no half header above it"

    def set_half(line: str) -> None:
        nonlocal current, header, why
        header = line
        current, why = match_video(line, labels)

    def is_half_header(line: str) -> bool:
        words = _words(line)
        return period_key(line) is not None or any(
            words and " ".join(_words(lab)) == " ".join(words) for lab in labels)

    def header_line(line: str) -> bool:
        """Apply a reel and/or half header; False if the line is neither."""
        name = reel_header(line)
        if name is None and "video" in _words(line) and period_key(line) is None \
                and not any(r.rows for r in reels):
            name = line.strip(_SEPARATORS)          # "Video Final" above the first clip
        if name is not None:
            start_reel(name)
            rest = re.sub(r"(?i)^\W*(?:reel|reeel|rell)\s*\d+", "", line)
            if period_key(rest) is not None and re.match(r"(?i)^\W*(?:reel|reeel|rell)\s*\d+", line):
                set_half(rest.strip(_SEPARATORS))    # "Reel 2 - 2nd half"
            return True
        if is_half_header(line):
            set_half(line.strip(_SEPARATORS))
            return True
        return False

    names_used: dict[str, int] = {}

    def add_row(lineno: int, start_s: str, end_s: str, label: str, error: str = "",
                own_line: bool = True, range_text: str = "") -> None:
        reel_i = len(reels) - 1
        reel = reels[-1]
        if not reel.key_name:
            n = names_used[reel.name.lower()] = names_used.get(reel.name.lower(), 0) + 1
            reel.key_name = reel.name.lower() if n == 1 else f"{reel.name.lower()} ({n})"
        rng = range_text or f"{start_s}-{end_s}"
        row = Row(reel_i, len(reels[-1].rows) + 1, lineno, None, None, rng, label, header,
                  own_line=own_line)
        try:
            if start_s:
                row.start, row.end = timecode.parse(start_s), timecode.parse(end_s)
                if row.end <= row.start:
                    row.error = f"ends ({end_s}) before it starts ({start_s})"
        except timecode.TimecodeError as e:
            row.error = str(e)
        row.error = error or row.error
        n = seen.get(f"{reel.key_name}|{rng}", 0)
        seen[f"{reel.key_name}|{rng}"] = n + 1
        row.key = choice_key(reel.key_name, rng, n)
        row.video, row.chosen_by = (current, "note") if current is not None else (None, "")
        row.why = "" if current is not None else why
        reels[-1].rows.append(row)

    for lineno, raw in enumerate(split_lines(text), start=1):
        line = _BULLET_RE.sub("", raw, count=1).strip() if _BULLET_RE.match(raw) else raw.strip()
        if not line:
            continue
        pairs = list(_PAIR_RE.finditer(line))
        if pairs:
            before = line[:pairs[0].start()].strip(_SEPARATORS)
            label_before = ""
            if before and not header_line(before):
                label_before = before                     # "Goal 3:43-4:01"
            simple = len(pairs) == 1 and not before
            for i, m in enumerate(pairs):
                end = pairs[i + 1].start() if i + 1 < len(pairs) else len(line)
                after = line[m.end():end].strip(_SEPARATORS)
                error = ""
                if _TIME_START_RE.match(after):             # "3:43-4:01-4:10"
                    error = f"has more than two times (“{line}”). Write one range per line, like 3:43-4:01"
                label = " ".join(x for x in (label_before if i == 0 else "", after) if x)
                add_row(lineno, m[1], m[2], label, error, own_line=simple)
            continue
        if _TIMEISH_RE.match(line):
            # Meant to be a clip but can't be read: keep it as a row that blocks Cut.
            add_row(lineno, "", "", "", f"couldn't read “{line}”. Write it like 3:43-4:01",
                    range_text=line)
            continue
        if not header_line(line):
            ignored.append((lineno, line))
            if any(w in _FIRST | _SECOND for w in _words(line)):
                # Probably a half header we can't read ("2nd hfl"): don't keep
                # pre-filling the clips under it with the previous half.
                current, header, why = None, line, f"couldn't tell which half “{line}” means"

    reels = [r for r in reels if r.rows]
    plan = Plan(reels, ignored)
    # The coach's own picks win, matched by reel + range, not by position.
    choices = dict(choices or {})
    keys = {r.key: r for r in plan.rows}
    for key, video in choices.items():
        row = keys.get(key)
        if row is None:
            plan.stale_choices.append(key)
        elif 0 <= video < len(labels):
            row.video, row.chosen_by, row.why = video, "you", ""
    return plan


# --------------------------------------------------------------------------
# Checking each clip against its video
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Cut:
    row: Row
    start: float        # where the cut starts in the video (after padding)
    end: float
    warning: str = ""

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass
class Check:
    cuts: list[Cut]                       # rows that can be cut
    errors: list[tuple[Row, str]]         # rows blocking Cut, with the reason
    warnings: list[tuple[Row, str]]

    @property
    def can_cut(self) -> bool:
        return not self.errors and bool(self.cuts)


def check(plan: Plan, durations: Sequence[float | None], pad_start: float = 0.0,
          pad_end: float = 1.0) -> Check:
    """Every row against ITS video. Cut is blocked while any row has no video,
    an unreadable time, end <= start, or starts past the end of its video.
    Padding that would run past either end of the video is trimmed."""
    cuts: list[Cut] = []
    errors: list[tuple[Row, str]] = []
    warnings: list[tuple[Row, str]] = []
    for row in plan.rows:
        if row.error:
            errors.append((row, row.error))
            continue
        if row.video is None:
            errors.append((row, "choose a video for this clip" + (f" ({row.why})" if row.why else "")))
            continue
        duration = durations[row.video] if row.video < len(durations) else None
        assert row.start is not None and row.end is not None
        if duration is not None and row.start >= duration:
            errors.append((row, f"starts at {timecode.fmt(row.start)} but that video is only "
                                f"{timecode.fmt(duration)} long"))
            continue
        start = max(0.0, row.start - pad_start)
        end = row.end + pad_end
        warning = ""
        if duration is not None and end > duration:
            if row.end > duration:
                warning = (f"ends at {timecode.fmt(row.end)} but that video is only "
                           f"{timecode.fmt(duration)} long, so it's cut at the end")
                warnings.append((row, warning))
            end = duration
        cuts.append(Cut(row, start, end, warning))
    return Check(cuts, errors, warnings)
