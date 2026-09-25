"""Shared cases: the same file is checked against web/notes.js (tests/web)."""
import json
from pathlib import Path

import pytest

from hclip import notes

CASES = json.loads((Path(__file__).parent / "data" / "note_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_case(case):
    note = notes.parse(case["text"])
    assert note.title == case["title"]
    assert [[s.tag, len(s.clips)] for s in note.sections] == case["sections"]
    assert [[p.line, p.fatal] for p in note.problems] == case["problems"]
    if "clips" in case:
        got = [[c.start, c.end, c.label] for s in note.sections for c in s.clips]
        assert got == case["clips"]
