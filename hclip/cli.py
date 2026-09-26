"""Command line: the coach-facing app is built on the same functions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hclip import cut, ffmpeg, notes, reel, timecode
from hclip.notes import Problem


def _read_note(arg: str) -> str:
    if arg == "-":
        return sys.stdin.read()
    return Path(arg).read_text(encoding="utf-8-sig")


def _print_problems(problems: list[Problem]) -> None:
    for p in sorted(problems, key=lambda p: p.line):
        kind = "ERROR" if p.fatal else "warning"
        print(f"  line {p.line}: {kind}: {p.message}", file=sys.stderr)


def cmd_info(args: argparse.Namespace) -> int:
    for path in args.videos:
        info = ffmpeg.probe(path)
        gop = ffmpeg.keyframe_interval(path)
        size_gb = info.size_bytes / 1e9
        print(f"{path}")
        print(f"  duration   {timecode.fmt(info.duration)}")
        print(f"  size       {size_gb:.2f} GB")
        print(f"  video      {info.video_codec} {info.width}x{info.height}"
              f" @ {info.fps:.2f} fps" if info.fps else f"  video      {info.video_codec}")
        print(f"  audio      {info.audio_codec or 'none'}"
              + (" (will be converted to AAC)" if info.audio_needs_reencode else ""))
        print(f"  keyframes  every {gop:.2f}s" if gop else "  keyframes  unknown")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    note = notes.parse(_read_note(args.note))
    n = note.clip_count
    print(f"{note.title or '(no title)'}: {n} clip{'' if n == 1 else 's'}")
    for s in note.sections:
        print(f"  {s.name} [{s.tag}] - {len(s.clips)} clip{'' if len(s.clips) == 1 else 's'}")
        for c in s.clips:
            label = f"  {c.label}" if c.label else ""
            print(f"    {timecode.fmt(c.start)}-{timecode.fmt(c.end)}"
                  f"  ({timecode.fmt(c.duration)}){label}")
    _print_problems(note.problems)
    return 1 if note.errors else 0


def cmd_cut(args: argparse.Namespace) -> int:
    note = notes.parse(_read_note(args.note))
    halves = [cut.HalfVideo.open(files) for files in args.half]
    try:
        p = cut.plan(note, halves, Path(args.out))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    problems = note.problems + p.problems
    if problems:
        print("Problems in the note:", file=sys.stderr)
        _print_problems(problems)
    mode = "precise (re-encoding video)" if args.precise else "fast (stream copy)"
    print(f"Cutting {len(p.jobs)} clips into {args.out} - {mode}")

    def on_progress(pr: cut.Progress) -> None:
        eta = f"{pr.remaining:.0f}s left" if pr.remaining is not None else "..."
        print(f"\r  [{pr.index + 1}/{pr.total}] {pr.job.out.name} "
              f"{pr.fraction:4.0%}  overall {pr.overall:4.0%}  {eta}   ",
              end="", flush=True)

    def on_result(r: cut.Result) -> None:
        if r.status == "skipped":
            print(f"  skip  {r.job.out.name} (exists; --force to redo)")
        elif r.status == "failed":
            print(f"\n  FAIL  {r.job.out.name}\n{r.error}", file=sys.stderr)
        else:
            print(f"\r  done  {r.job.out.name}" + " " * 30)

    try:
        results = cut.run(p.jobs, precise=args.precise, force=args.force,
                          on_progress=on_progress, on_result=on_result)
    finally:
        for h in halves:
            h.close()
    failed = [r for r in results if r.status == "failed"]
    made = sum(r.status == "made" for r in results)
    print(f"Made {made}, skipped {len(results) - made - len(failed)}, failed {len(failed)}.")
    return 1 if failed else 0


def cmd_reel(args: argparse.Namespace) -> int:
    note = notes.parse(_read_note(args.note))
    halves = [cut.HalfVideo.open(files) for files in args.half]
    out = Path(args.out)
    try:
        p = cut.plan(note, halves, out)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    problems = note.problems + p.problems
    if problems:
        print("Problems in the note:", file=sys.stderr)
        _print_problems(problems)
    title = args.title or note.title or "Match"
    reels = reel.plan_reels(note, p.jobs, out, title, halves=not args.whole, whole=args.whole)
    how = "re-encoding" if not reel.can_copy(p.jobs) else "stream copy"
    print(f"Making {len(reels)} video(s) from {len(p.jobs)} clips into {out} ({how})")

    def on_progress(pr: reel.ReelProgress) -> None:
        eta = f"{pr.remaining:.0f}s left" if pr.remaining is not None else "..."
        print(f"\r  {pr.stage:<28} {pr.overall:4.0%}  {eta}   ", end="", flush=True)

    try:
        results = reel.run_reels(reels, precise=args.precise, force=not args.skip_existing,
                                 keep_clips_dir=out / "Clips" if args.keep_clips else None,
                                 on_progress=on_progress)
    finally:
        for h in halves:
            h.close()
    print()
    for r in results:
        extra = f" (left out lines {', '.join(map(str, r.missing))})" if r.missing else ""
        print(f"  {r.status:<7} {r.reel.out.name}{extra}")
        if r.status == "failed" and r.error:
            print(r.error, file=sys.stderr)
    return 1 if any(r.status == "failed" for r in results) else 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="hclip", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("info", help="show duration, codecs, keyframe interval")
    p.add_argument("videos", nargs="+")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("check", help="read a coach's note and list the clips")
    p.add_argument("note", help="text file, or - for stdin (e.g. pbpaste | hclip check -)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("cut", help="cut the clips in a note")
    p.add_argument("note", help="text file, or - for stdin")
    p.add_argument("--half", action="append", nargs="+", required=True,
                   metavar="VIDEO",
                   help="video file(s) for one section, in note order; "
                        "repeat per half. Several files = joined (GoPro chapters)")
    p.add_argument("--out", default="clips", help="output folder")
    p.add_argument("--precise", action="store_true",
                   help="frame-accurate cuts (re-encodes; much slower)")
    p.add_argument("--force", action="store_true", help="redo clips that exist")
    p.set_defaults(func=cmd_cut)

    p = sub.add_parser("reel", help="make one highlights video per half (or --whole)")
    p.add_argument("note", help="text file, or - for stdin")
    p.add_argument("--half", action="append", nargs="+", required=True, metavar="VIDEO",
                   help="video file(s) for one section, in note order; repeat per half")
    p.add_argument("--out", default="highlights", help="output folder")
    p.add_argument("--title", default="", help="match name used in the file names")
    p.add_argument("--whole", action="store_true", help="one video for the whole match")
    p.add_argument("--keep-clips", action="store_true", help="also keep each clip")
    p.add_argument("--precise", action="store_true", help="frame-accurate (slower)")
    p.add_argument("--skip-existing", action="store_true",
                   help="don't remake videos that are already there")
    p.set_defaults(func=cmd_reel)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ffmpeg.FFmpegError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
        return 130
