"""Parse a coach's clip note, as typed into a phone's Notes app.

Example::

    Dunloy clips
    1:52-2:10
    3:05-3:33 point from play

    2nd Half Video
    3:04-3:37

Rules:
- A line holding a time range is a clip. Anything after the range is its label.
- A text line mentioning a half / extra time / penalties starts a new section.
- Any other text line before the first clip is the match title; after clips
  have appeared it starts a new section (each section is one video).
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
    line: int  # 1-based line number in the note

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
    sections: list[Section]
    problems: list[Problem]

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


def parse(text: str) -> Note:
    title = ""
    sections: list[Section] = []
    problems: list[Problem] = []
    current: Section | None = None

    def new_section(name: str) -> Section:
        pos = len(sections) + 1
        tag = section_tag(name, pos) or f"part{pos}"
        s = Section(name=name, tag=tag)
        sections.append(s)
        return s

    for lineno, raw in enumerate(text.splitlines(), start=1):
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
                problems.append(Problem(lineno, f"couldn't read time: {e}", True))
                continue
            if end <= start:
                problems.append(
                    Problem(lineno, f"end {end_s} is not after start {start_s}", True)
                )
                continue
            if current is None:
                current = new_section("1st Half")
            clip = Clip(start, end, label, lineno)
            if clip.duration > LONG_CLIP_SECONDS:
                problems.append(
                    Problem(
                        lineno,
                        f"clip is {timecode.fmt(clip.duration)} long - typo?",
                        False,
                    )
                )
            for other in current.clips:
                if clip.start < other.end and other.start < clip.end:
                    problems.append(
                        Problem(lineno, f"overlaps the clip on line {other.line}", False)
                    )
            current.clips.append(clip)
            continue

        if _TIMEISH_RE.match(line):
            problems.append(
                Problem(lineno, f"couldn't read {line!r} - expected like 1:52-2:10", True)
            )
            continue

        # A text line: title, or a section header.
        is_period = section_tag(line, len(sections) + 1) is not None
        if is_period and (current is None or current.clips):
            current = new_section(line)
        elif is_period:
            # Header right after another empty header: rename it.
            current.name = line
            current.tag = section_tag(line, len(sections)) or current.tag
        elif current is None and not title:
            title = line
        elif current is not None and current.clips:
            current = new_section(line)
        # Otherwise: stray text between headers - ignore.

    sections = [s for s in sections if s.clips]
    _dedupe_tags(sections)
    return Note(title=title, sections=sections, problems=problems)


def _dedupe_tags(sections: list[Section]) -> None:
    seen: dict[str, int] = {}
    for s in sections:
        if s.tag in seen:
            seen[s.tag] += 1
            s.tag = f"{s.tag}_{seen[s.tag]}"
        else:
            seen[s.tag] = 1
