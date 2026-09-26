import pytest

from hclip import timecode


@pytest.mark.parametrize("text,expected", [
    ("1:52", 112), ("01:52", 112), ("25:37", 1537), ("75:10", 4510),
    ("1:02:03", 3723), ("1.52", 112), (" 3:05 ", 185), ("0:00", 0),
])
def test_parse(text, expected):
    assert timecode.parse(text) == expected


@pytest.mark.parametrize("text", ["1:75", "1:5", "abc", "", "1:60:00", "1:52:"])
def test_parse_rejects(text):
    with pytest.raises(timecode.TimecodeError):
        timecode.parse(text)


def test_fmt():
    assert timecode.fmt(112) == "1:52"
    assert timecode.fmt(3723) == "1:02:03"
    assert timecode.fmt_filename(112) == "01m52"
    assert timecode.fmt_filename(3723) == "1h02m03"
