"""Filenames that are safe on Windows and sort in match order."""

from __future__ import annotations

import re
import unicodedata

from hclip import timecode

_MAX_LABEL = 40


def slugify(text: str, max_len: int = _MAX_LABEL) -> str:
    """'Point - J McLaughlin' -> 'point_j_mclaughlin'. Irish fadas are folded
    (Ó -> o) so names survive any filesystem or zip tool."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text[:max_len].rstrip("_")


def clip_filename(
    number: int, total: int, tag: str, start: float, end: float, label: str = ""
) -> str:
    width = max(2, len(str(total)))
    parts = [f"{number:0{width}d}", tag,
             f"{timecode.fmt_filename(start)}-{timecode.fmt_filename(end)}"]
    slug = slugify(label)
    if slug:
        parts.append(slug)
    return "_".join(parts) + ".mp4"


def folder_name(title: str) -> str:
    return slugify(title, 60) or "clips"
