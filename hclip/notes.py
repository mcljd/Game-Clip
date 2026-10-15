"""Parse a coach's clip note, as typed into a phone's Notes app.

Example::

    Match clips
    1:52-2:10
    3:05-3:33 point from play

    2nd Half Video
    3:04-3:37

Some coaches make several videos from one match, each mixing both halves::

    Video Final          <- title, and the name of the first video
    3:43-4:01                  <- from the 1st half's recording
    2nd Half
    3:10-3:34                  <- from the 2nd half's recording
    2nd Video                  <- starts the next video (back to the 1st half)
    6:56-7:07
    2nd half
    5:05-5:15

Rules:
- A line holding a time range is a clip. Anything after the range is its label.
- A text line mentioning a half / extra time / penalties says which
  recording the next clips come from. Each recording is one section (one
  video file, or one set of camera files); naming a half again goes back to
  the same section.
- A text line with the word "video" (and no half in it) after clips starts
  a new output video (a group). Its clips start from the 1st half again.
- Any other text line before the first clip is the match title; after clips
  have appeared it starts a new section.
- Nothing is fatal here: every problem is collected with its line number so
  the coach can see them all at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from hclip import timecode

_TIME = r"\d{1,3}[:.]\d{2}(?:[:.]\d{2})?"
# Separators people actually type: -, en/em dash (iOS autocorrect), "to".
_RANGE_RE = re.compile(
    rf"^\s*({_TIME})\s*(?:-+|–|—|to)\s*({_TIME})\s*[-–—:,]?\s*(.*?)\s*$",
    re.IGNORECASE,
)
# A line that looks like it was meant to be a time range but isn't one.
_TIMEISH_RE = re.compile(r"^\s*\d{1,3}[:.]\d")

LONG_CLIP_SECONDS = 90.0


@dataclass(frozen=True)
class Clip:
    start: float
    end: float
    label: str
    line: int       # 1-based line number in the note
    group: int = 0  # which output video (index into Note.groups)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Section:
    name: str
    tag: str
    clips: list[Clip] = field(default_factory=list)


@dataclass(frozen=True)
class Problem:
    line: int
    message: str
    fatal: bool  # fatal problems drop that clip; warnings keep it


@dataclass
class Note:
    title: str
    sections: list[Section]   # recordings, in the order the note first names them
    problems: list[Problem]
    # Names of the output videos when the note has "video" lines (two or
    # more); empty means one video per section, as before.
    groups: list[str] = field(default_factory=list)

    @property
    def clips(self) -> list[Clip]:
        """Every clip in the order it's written in the note."""
        return sorted((c for s in self.sections for c in s.clips), key=lambda c: c.line)

    @property
    def clip_count(self) -> int:
        return sum(len(s.clips) for s in self.sections)

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.fatal]

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if not p.fatal]


def section_tag(name: str, position: int) -> str | None:
    """Short tag for a section header, or None if it isn't a GAA period name.

    ``position`` is the 1-based section number, used when the header doesn't
    say which half (e.g. just "Half time video").
    """
    n = name.lower()
    if "penalt" in n or re.search(r"\bpens\b", n):
        return "pens"
    extra = "extra" in n or re.search(r"\bet\s*\d?\b", n)
    if re.search(r"\b(1st|first|one)\b", n) or re.search(r"\b(et|time)\s*1\b", n):
        which = "1"
    elif re.search(r"\b(2nd|second|two)\b", n) or re.search(r"\b(et|time)\s*2\b", n):
        which = "2"
    else:
        which = None
    if extra:
        return f"et{which}" if which else "et"
    if "half" in n:
        return f"h{which or position}"
    return None


# Line breaks, the same set web/notes.js uses, so both count lines alike.
# Includes U+2028/U+2029, which iPhone Notes can put in pasted text.
_LINE_BREAK = re.compile("\r\n|[\n\r\u2028\u2029\x85]")


def split_lines(text: str) -> list[str]:
    return _LINE_BREAK.split(text)


_VIDEO_RE = re.compile(r"\bvideo\b", re.IGNORECASE)


def is_video_header(line: str) -> bool:
    """A line naming an output video ("2nd Video", "Video Final"),
    as opposed to a half ("2nd Half Video" names a half)."""
    return bool(_VIDEO_RE.search(line)) and section_tag(line, 1) is None


def parse(text: str) -> Note:
    title = ""
    title_is_video = False
    sections: list[Section] = []
    problems: list[Problem] = []
    current: Section | None = None
    groups: list[str] = []        # names of groups after the first
    first_name = ""               # a "Video 1" line naming the first group
    group = 0
    group_has_clips = False

    def new_section(name: str) -> Section:
        pos = len(sections) + 1
        tag = section_tag(name, pos) or f"part{pos}"
        s = Section(name=name, tag=tag)
        sections.append(s)
        return s

    def period_section(name: str) -> Section:
        """The section for a half: the same one each time it's named."""
        tag = section_tag(name, len(sections) + 1)
        for s in sections:
            if s.tag == tag:
                return s
        return new_section(name)

    for lineno, raw in enumerate(split_lines(text), start=1):
        line = raw.strip()
        if not line:
            continue

        m = _RANGE_RE.match(line)
        if m:
            start_s, end_s, label = m.groups()
            try:
                start = timecode.parse(start_s)
                end = timecode.parse(end_s)
            except timecode.TimecodeError as e:
                problems.append(Problem(lineno, f"{e}. This line is skipped.", True))
                continue
            if end <= start:
                problems.append(
                    Problem(lineno, f"ends ({end_s}) before it starts ({start_s}). This line is skipped.", True)
                )
                continue
            if current is None:
                current = period_section("1st Half")
            clip = Clip(start, end, label, lineno, group)
            if clip.duration > LONG_CLIP_SECONDS:
                problems.append(
                    Problem(
                        lineno,
                        f"this clip is {timecode.fmt(clip.duration)} long - is that a typo?",
                        False,
                    )
                )
            for other in current.clips:
                if other.group == group and clip.start < other.end and other.start < clip.end:
                    problems.append(
                        Problem(lineno, f"overlaps the clip on line {other.line}", False)
                    )
            current.clips.append(clip)
            group_has_clips = True
            continue

        if _TIMEISH_RE.match(line):
            problems.append(
                Problem(lineno, f"couldn't read “{line}”. Write it like 1:52-2:10. "
                        "This line is skipped.", True)
            )
            continue

        # A text line: title, a half, a new output video, or a new section.
        if section_tag(line, len(sections) + 1) is not None:
            current = period_section(line)
            if not current.clips and current.name != line:
                current.name = line      # header right after another: keep the latest wording
        elif is_video_header(line) and group_has_clips:
            groups.append(line)
            group += 1
            group_has_clips = False
            current = None               # each video starts from the 1st half
        elif is_video_header(line) and title:
            # A video's name written before its first clip ("Final" then "Video 1").
            if group == 0:
                first_name = line
            else:
                groups[-1] = line
        elif current is None and not title:
            title = line
            title_is_video = is_video_header(line)
        elif current is not None and current.clips:
            current = new_section(line)
        # Otherwise: stray text between headers - ignore.

    sections = [s for s in sections if s.clips]
    names: list[str] = []
    used = {c.group for s in sections for c in s.clips}
    if len(used) > 1:
        first = first_name or (title if title_is_video else "1st Video")
        names = [first, *groups]
    _dedupe_tags(sections)
    return Note(title=title, sections=sections, problems=problems, groups=names)


def _dedupe_tags(sections: list[Section]) -> None:
    seen: dict[str, int] = {}
    for s in sections:
        if s.tag in seen:
            seen[s.tag] += 1
            s.tag = f"{s.tag}_{seen[s.tag]}"
        else:
            seen[s.tag] = 1
