"""Turn a parsed note plus one video (or set of files) per half into clips."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from hclip import ffmpeg, naming, timecode
from hclip.notes import Clip, Note, Problem

# If a clip's end runs past the video by less than this, trim it to the end
# rather than dropping it (coaches round up the last clip of a half).
END_SLACK_SECONDS = 15.0


@dataclass
class HalfVideo:
    """One section's footage: a single file, or several joined in order."""
    files: list[Path]
    duration: float
    reencode_audio: bool
    _concat_list: Path | None = None

    @classmethod
    def open(cls, files: Sequence[str | Path]) -> "HalfVideo":
        if not files:
            raise ValueError("no video files given")
        ordered = ffmpeg.sort_parts(files)
        infos = [ffmpeg.probe(p) for p in ordered]
        return cls(
            files=ordered,
            duration=sum(i.duration for i in infos),
            reencode_audio=any(i.audio_needs_reencode for i in infos),
        )

    @property
    def is_concat(self) -> bool:
        return len(self.files) > 1

    def source(self) -> Path:
        if not self.is_concat:
            return self.files[0]
        if self._concat_list is None:
            self._concat_list = ffmpeg.concat_list_tempfile(self.files)
        return self._concat_list

    def close(self) -> None:
        if self._concat_list is not None:
            self._concat_list.unlink(missing_ok=True)
            self._concat_list = None


@dataclass
class Job:
    number: int
    section_tag: str
    clip: Clip
    start: float
    end: float
    half: HalfVideo
    out: Path

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Plan:
    jobs: list[Job]
    problems: list[Problem] = field(default_factory=list)


def plan(note: Note, halves: Sequence[HalfVideo], out_dir: Path) -> Plan:
    """Match sections to videos and check every clip fits its video."""
    if len(note.sections) != len(halves):
        names = ", ".join(s.name for s in note.sections) or "none"
        raise ValueError(
            f"the note has {len(note.sections)} section(s) ({names}) "
            f"but {len(halves)} video(s) were given"
        )
    problems: list[Problem] = []
    kept: list[tuple[str, Clip, float, float, HalfVideo]] = []
    for section, half in zip(note.sections, halves):
        for c in section.clips:
            if c.start >= half.duration:
                problems.append(Problem(
                    c.line,
                    f"starts at {timecode.fmt(c.start)} but the {section.name} "
                    f"video is only {timecode.fmt(half.duration)} long - skipped",
                    True,
                ))
                continue
            end = c.end
            if end > half.duration:
                if end - half.duration > END_SLACK_SECONDS:
                    problems.append(Problem(
                        c.line,
                        f"ends at {timecode.fmt(c.end)} but the {section.name} "
                        f"video is only {timecode.fmt(half.duration)} long - skipped",
                        True,
                    ))
                    continue
                end = half.duration
                problems.append(Problem(
                    c.line, f"trimmed to end of video ({timecode.fmt(end)})", False
                ))
            kept.append((section.tag, c, c.start, end, half))

    total = len(kept)
    jobs = [
        Job(n, tag, c, start, end, half,
            out_dir / naming.clip_filename(n, total, tag, c.start, c.end, c.label))
        for n, (tag, c, start, end, half) in enumerate(kept, start=1)
    ]
    return Plan(jobs, problems)


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Progress:
    job: Job
    index: int          # 0-based position in this run
    total: int
    fraction: float     # of this clip, 0..1
    overall: float      # of the whole run by clip duration, 0..1
    elapsed: float
    remaining: float | None


@dataclass(frozen=True)
class Result:
    job: Job
    status: str         # "made" | "skipped" | "failed"
    error: str = ""


def _part_path(out: Path) -> Path:
    return out.with_name(out.name + ".part")


def run(
    jobs: Sequence[Job],
    *,
    precise: bool = False,
    force: bool = False,
    on_progress: Callable[[Progress], None] | None = None,
    on_result: Callable[[Result], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[Result]:
    """Cut each job. Existing clips are skipped unless ``force``. Clips are
    written to ``<name>.part`` and renamed only once ffmpeg succeeds, so an
    interrupted run never leaves a half-written clip that looks finished."""
    ff = ffmpeg.find_binary("ffmpeg")
    results: list[Result] = []
    todo = [j for j in jobs if force or not j.out.exists()]
    todo_ids = {id(j) for j in todo}
    total_secs = sum(j.duration for j in todo) or 1.0
    done_secs = 0.0
    started = time.monotonic()

    def emit(r: Result) -> None:
        results.append(r)
        if on_result:
            on_result(r)

    for j in jobs:
        if id(j) not in todo_ids:
            emit(Result(j, "skipped"))

    for i, job in enumerate(todo):
        if should_stop and should_stop():
            break
        job.out.parent.mkdir(parents=True, exist_ok=True)
        part = _part_path(job.out)
        cmd = ffmpeg.build_cut_cmd(
            ff, job.half.source(), job.start, job.duration, part,
            precise=precise, reencode_audio=job.half.reencode_audio,
            is_concat=job.half.is_concat,
        )

        def progress(secs: float, job: Job = job, i: int = i) -> None:
            if not on_progress:
                return
            frac = min(1.0, secs / job.duration) if job.duration else 1.0
            overall = min(1.0, (done_secs + frac * job.duration) / total_secs)
            elapsed = time.monotonic() - started
            remaining = elapsed * (1 - overall) / overall if overall > 0.02 else None
            on_progress(Progress(job, i, len(todo), frac, overall, elapsed, remaining))

        try:
            ffmpeg.run(cmd, progress)
            os.replace(part, job.out)
            emit(Result(job, "made"))
        except ffmpeg.FFmpegError as e:
            part.unlink(missing_ok=True)
            emit(Result(job, "failed", str(e)))
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        progress(job.duration)
        done_secs += job.duration

    return results
