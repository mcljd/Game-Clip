"""Parse and format the times coaches type: "1:52", "01:52", "1:02:03", "1.52"."""

from __future__ import annotations

import re

_TIME_RE = re.compile(r"^(\d{1,3})[:.](\d{2})(?:[:.](\d{2}))?$")


class TimecodeError(ValueError):
    pass


def parse(text: str) -> float:
    """Return seconds for M:SS, MM:SS, H:MM:SS (':' or '.' as separator)."""
    m = _TIME_RE.match(text.strip())
    if not m:
        raise TimecodeError(f"not a time: {text!r}")
    a, b, c = m.groups()
    if c is None:
        minutes, seconds = int(a), int(b)
        hours = 0
    else:
        hours, minutes, seconds = int(a), int(b), int(c)
        if minutes >= 60:
            raise TimecodeError(f"minutes must be under 60 in {text!r}")
    if seconds >= 60:
        raise TimecodeError(f"seconds must be under 60 in {text!r}")
    return float(hours * 3600 + minutes * 60 + seconds)


def fmt(seconds: float) -> str:
    """Human display: 1:52, or 1:02:03 past the hour."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_filename(seconds: float) -> str:
    """Filename-safe (no colons, which Windows forbids): 01m52, 1h02m03."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}" if h else f"{m:02d}m{s:02d}"
