import pytest

from hclip import cli, cut, ffmpeg, notes
from tests.conftest import needs_ffmpeg

pytestmark = [pytest.mark.integration, needs_ffmpeg]

NOTE = """Test match
0:10-0:16
0:31-0:40 Goal - Seán
2nd half
0:05-0:12
"""


def _cut(note_text, halves, out, **kw):
    note = notes.parse(note_text)
    hv = [cut.HalfVideo.open(h) for h in halves]
    p = cut.plan(note, hv, out)
    return p, cut.run(p.jobs, **kw)


def test_info(videos):
    info = ffmpeg.probe(videos["h1"])
    assert abs(info.duration - 60) < 0.2
    assert info.video_codec == "h264" and info.width == 320
    assert abs(ffmpeg.keyframe_interval(videos["h1"]) - 2.0) < 0.1


def test_cut_names_durations_and_resume(videos, tmp_path):
    p, results = _cut(NOTE, [[videos["h1"]], [videos["h2"]]], tmp_path)
    assert [r.status for r in results] == ["made"] * 3
    names = sorted(f.name for f in tmp_path.iterdir())
    assert names == ["01_h1_00m10-00m16.mp4", "02_h1_00m31-00m40_goal_sean.mp4",
                     "03_h2_00m05-00m12.mp4"]
    # Stream copy snaps to the keyframe at/before start (2s GOP here), so the
    # clip may begin up to one GOP early, never late.
    d = ffmpeg.probe(tmp_path / names[1]).duration
    assert 9 - 0.2 <= d <= 9 + 2.2
    assert not list(tmp_path.glob("*.part"))

    _, again = _cut(NOTE, [[videos["h1"]], [videos["h2"]]], tmp_path)
    assert [r.status for r in again] == ["skipped"] * 3
    _, forced = _cut(NOTE, [[videos["h1"]], [videos["h2"]]], tmp_path, force=True)
    assert [r.status for r in forced] == ["made"] * 3


def test_precise_is_frame_accurate(videos, tmp_path):
    _, results = _cut("0:11-0:16", [[videos["h1"]]], tmp_path, precise=True)
    assert results[0].status == "made"
    assert abs(ffmpeg.probe(results[0].job.out).duration - 5) < 0.15


def test_out_of_range_is_reported_not_crashed(videos, tmp_path):
    p, results = _cut("0:10-0:15\n1:30-1:40\n0:55-1:05", [[videos["h1"]]], tmp_path)
    msgs = {pr.line: pr for pr in p.problems}
    assert msgs[2].fatal and "only 1:00 long" in msgs[2].message
    assert not msgs[3].fatal and "trimmed" in msgs[3].message
    assert [r.status for r in results] == ["made", "made"]


def test_section_count_mismatch(videos, tmp_path):
    with pytest.raises(ValueError, match="2 section"):
        _cut(NOTE, [[videos["h1"]]], tmp_path)


def test_gopro_chapters_joined_without_intermediate_file(videos, tmp_path):
    half = cut.HalfVideo.open([videos["part2"], videos["part1"]])  # wrong order on purpose
    assert [f.name for f in half.files] == ["GX010001.MP4", "GX020001.MP4"]
    assert abs(half.duration - 40) < 0.3
    p = cut.plan(notes.parse("0:18-0:24"), [half], tmp_path)  # spans the join
    [r] = cut.run(p.jobs)
    half.close()
    assert r.status == "made"
    assert ffmpeg.probe(r.job.out).duration >= 5.5


def test_pcm_camcorder_audio(videos, tmp_path):
    _, [r] = _cut("0:04-0:09", [[videos["pcm"]]], tmp_path)
    assert r.status == "made", r.error
    assert ffmpeg.probe(r.job.out).audio_codec == "aac"


def test_ffmpeg_failure_surfaces_stderr_and_leaves_no_part(videos, tmp_path, monkeypatch):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(videos["h1"].read_bytes()[:20000])  # truncated file
    half = cut.HalfVideo(files=[broken], duration=60, reencode_audio=False)
    p = cut.plan(notes.parse("0:30-0:40"), [half], tmp_path / "out")
    [r] = cut.run(p.jobs)
    assert r.status == "failed" and r.error
    assert not list((tmp_path / "out").glob("*"))


def test_cli_end_to_end(videos, tmp_path, capsys):
    note = tmp_path / "note.txt"
    note.write_text(NOTE, encoding="utf-8")
    code = cli.main(["cut", str(note), "--half", str(videos["h1"]),
                     "--half", str(videos["h2"]), "--out", str(tmp_path / "clips")])
    assert code == 0
    assert len(list((tmp_path / "clips").glob("*.mp4"))) == 3
    assert cli.main(["check", str(note)]) == 0


def test_cli_reel(videos, tmp_path):
    note = tmp_path / "note.txt"
    note.write_text(NOTE, encoding="utf-8")
    args = ["reel", str(note), "--half", str(videos["h1"]), "--half", str(videos["h2"]),
            "--out", str(tmp_path / "o")]
    assert cli.main(args) == 0
    assert sorted(p.name for p in (tmp_path / "o").iterdir()) == [
        "Test match - 1st Half.mp4", "Test match - 2nd half.mp4"]
    assert cli.main(args + ["--whole"]) == 0
    assert (tmp_path / "o" / "Test match - Whole match.mp4").exists()
