"""Command line for tidy, and the `--tidy` hook the exporter calls."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
import tempfile
from collections import Counter
from typing import Callable, List, Optional

from . import report
from .scan import scan_vault
from .steps import EVERNOTE_SUFFIX, STEP_NAMES, STEPS, Context, Options
from .vault import Vault

DESCRIPTION = """\
Clean up an onenote-to-markdown export so it reads well in Markdown note apps
(such as Skysa Notes, https://skysa.com/) and plain Markdown viewers.

Writes a cleaned copy (default: <export>-tidy) and a report next to it
(<export>-tidy-report/). The export itself is never changed unless you pass
--in-place, which writes a zip backup first.

steps, in order:
""" + "\n".join(f"  {name:17} {desc}" for name, _, desc in STEPS)


class TidyError(Exception):
    pass


def run(source: str, output: Optional[str], opts: Options, report_dir: str,
        dry_run: bool = False, echo: Callable[[str], None] = print) -> dict:
    """Clean `source` into `output` (which must be empty or missing). Returns a summary."""
    unknown = (opts.skip | opts.only) - set(STEP_NAMES)
    if unknown:
        raise TidyError(f"unknown step(s): {', '.join(sorted(unknown))} (steps: {', '.join(STEP_NAMES)})")
    v = Vault.load(source)
    pages = [n for n in v.notes() if n.has_fm and n.fm.get("title") is not None]
    if not pages:
        echo(f"warning: {source} doesn't look like an onenote-to-markdown export (no pages with front matter)")
    selected = [name for name in STEP_NAMES if (not opts.only or name in opts.only) and name not in opts.skip]
    ctx = Context(opts)
    if opts.llm and {"titles", "attachment-names"} & set(selected):
        from .llm import ClaudeNamer, ClaudeUnavailable
        try:
            ctx.namer = ClaudeNamer(opts.llm_model)
        except ClaudeUnavailable as e:
            raise TidyError(str(e)) from None
        echo(f"tidy: naming pages and images with {opts.llm_model}")
    for name, fn, _ in STEPS:
        if name in selected:
            fn(v, ctx)
            ctx.ran.append(name)
    broken = v.finalize_links()
    attention = report.attention(v, ctx, broken, scan_vault(v))
    errors = [f"{msg} (×{n})" if n > 1 else msg
              for msg, n in Counter(ctx.namer.errors if ctx.namer else []).items()]
    report_path = report.write(report_dir, v, ctx, attention, source, output or "", dry_run, errors)
    written = 0
    if not dry_run and output:
        written = v.write(output)
    return {"vault": v, "ctx": ctx, "attention": attention, "report": report_path, "written": written}


def print_summary(summary: dict, dry_run: bool, echo: Callable[[str], None] = print):
    v, ctx, att = summary["vault"], summary["ctx"], summary["attention"]
    counts = v.counts()
    echo("tidy" + (" (dry run)" if dry_run else "") + ":")
    for name in ctx.ran:
        echo(f"  {name:17} {counts.get(name, 0)} change(s)")
    notes = len(v.notes())
    echo(f"  {notes} pages and {len(v.alive()) - notes} other files" + ("" if dry_run else " written"))
    flags = [("possible passwords or keys", len(att["sensitive"])), ("broken links", len(att["broken"])),
             ("unlinked attachments", len(att["unlinked"])), ("code files", len(att["code"]))]
    flagged = ", ".join(f"{n} {label}" for label, n in flags if n)
    if flagged:
        echo(f"  check: {flagged}")
    echo(f"  report: {summary['report']}")


def parse_steps(value: Optional[str]) -> set:
    return {s.strip() for s in (value or "").split(",") if s.strip()}


# ------------------------------------------------------------ standalone
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m onenote_to_markdown.tidy", description=DESCRIPTION,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", help="folder written by onenote-to-markdown")
    ap.add_argument("-o", "--output", help="where to write the cleaned copy (default: <export>-tidy)")
    ap.add_argument("--in-place", action="store_true",
                    help="replace the export with the cleaned copy (a zip backup is written first)")
    ap.add_argument("--dry-run", action="store_true", help="write only the report, not the notes")
    ap.add_argument("--skip", metavar="STEPS", help="comma-separated steps to leave out")
    ap.add_argument("--only", metavar="STEPS", help="comma-separated steps to run, leaving out the rest")
    ap.add_argument("--llm", action="store_true",
                    help="let Claude name untitled pages and generically named images (pip install anthropic, "
                         "and an Anthropic API key; page excerpts are sent with sensitive values masked)")
    ap.add_argument("--llm-model", default=Options.llm_model, help="Claude model for --llm (default: %(default)s)")
    ap.add_argument("--renames", metavar="CSV",
                    help="apply titles from a renames.csv (from an earlier report) instead of choosing them")
    ap.add_argument("--folder-note", choices=["same-name", "zero", "index"], default="same-name",
                    help="name of a parent page inside its folder: Title/Title.md (default), "
                         "Title/00 - Title.md, or Title/index.md")
    ap.add_argument("--folder-numbers", action="store_true",
                    help="keep the page number on folders made from parent pages (21 - Banners/)")
    ap.add_argument("--keep-fields", metavar="FIELDS", help="front matter fields to keep, e.g. level")
    ap.add_argument("--drop-fields", metavar="FIELDS",
                    help="front matter fields to drop (default: notebook,section, plus level when pages are nested)")
    ap.add_argument("--keep-versions", action="store_true", help="keep _versions/ (older page versions)")
    ap.add_argument("--keep-json", action="store_true", help="keep _json/ (raw page dumps)")
    ap.add_argument("--chunk-pattern", default=Options.chunk_pattern,
                    help="regular expression for section folders to merge into their notebook (default: %(default)r)")
    ap.add_argument("--report-dir", help="where to write the report "
                                          "(default: <export>-tidy-report, or <output>-tidy-report with -o)")
    return ap


def options_from_args(a) -> Options:
    return Options(
        skip=parse_steps(a.skip), only=parse_steps(a.only), llm=a.llm, llm_model=a.llm_model,
        renames=a.renames, folder_note=a.folder_note, folder_numbers=a.folder_numbers,
        drop_fields=sorted(parse_steps(a.drop_fields)) if a.drop_fields else None,
        keep_fields=sorted(parse_steps(a.keep_fields)), keep_versions=a.keep_versions,
        keep_json=a.keep_json, chunk_pattern=a.chunk_pattern)


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    source = os.path.abspath(a.export)
    if not os.path.isdir(source):
        print(f"error: {a.export} is not a folder", file=sys.stderr)
        return 2
    if a.in_place and a.output:
        print("error: use either --in-place or -o, not both", file=sys.stderr)
        return 2
    output = source + ".tidy-tmp" if a.in_place else os.path.abspath(a.output or source + "-tidy")
    if not a.dry_run and os.path.exists(output) and os.listdir(output):
        print(f"error: {output} already exists and isn't empty", file=sys.stderr)
        return 2
    if os.path.commonpath([source, output]) == source:
        print("error: the output can't be inside the export", file=sys.stderr)
        return 2
    report_dir = os.path.abspath(a.report_dir or (output if a.output else source) + "-tidy-report")
    try:
        summary = run(source, output, options_from_args(a), report_dir, a.dry_run)
    except (TidyError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if a.in_place and not a.dry_run:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = shutil.make_archive(f"{source}-before-tidy-{stamp}", "zip",
                                     os.path.dirname(source), os.path.basename(source))
        old = source + ".tidy-old"
        os.rename(source, old)
        os.rename(output, source)
        shutil.rmtree(old)
        print(f"backup of the original: {backup}")
    print_summary(summary, a.dry_run)
    return 0


# ------------------------------------------------------- exporter hook
def add_exporter_arguments(ap: argparse.ArgumentParser):
    g = ap.add_argument_group("clean-up", "Tidy the export for Markdown note apps in the same run (docs/tidy.md).")
    g.add_argument("--tidy", action="store_true",
                   help="remove index files, old versions and deleted pages, fix titles, nest sub-pages, "
                        "move images next to their pages, and write a report to <output>-tidy-report")
    g.add_argument("--tidy-llm", action="store_true",
                   help="with --tidy: let Claude name untitled pages and generically named images "
                        "(pip install anthropic, and an Anthropic API key)")
    g.add_argument("--tidy-skip", metavar="STEPS", help=f"with --tidy: steps to leave out ({', '.join(STEP_NAMES)})")
    g.add_argument("--tidy-renames", metavar="CSV", help="with --tidy: apply reviewed titles from a renames.csv")


def wants_tidy(a) -> bool:
    return bool(a.tidy or a.tidy_llm or a.tidy_skip or a.tidy_renames)


def export_and_tidy(a, export: Callable[[str], int]) -> int:
    """Export into a staging folder, tidy it, and move only the clean result into -o."""
    output = os.path.abspath(a.output)
    opts = Options(skip=parse_steps(a.tidy_skip), llm=a.tidy_llm, renames=a.tidy_renames,
                   keep_versions=a.versions, keep_json=a.json)
    staging = tempfile.mkdtemp(prefix="onenote-md-export-")
    clean = tempfile.mkdtemp(prefix="onenote-md-tidy-")
    try:
        rc = export(staging)
        if rc:
            return rc
        names = os.listdir(staging)
        taken = [n for n in names
                 for candidate in {n, (EVERNOTE_SUFFIX.match(n) or [None, n])[1]}
                 if os.path.exists(os.path.join(output, candidate))]
        if taken:
            print(f"error: {', '.join(sorted(set(taken)))} already exist(s) in {a.output}; --tidy won't merge "
                  "into an existing notebook folder. Remove it or choose another -o.", file=sys.stderr)
            return 1
        try:
            summary = run(staging, clean, opts, output.rstrip(os.sep) + "-tidy-report")
        except TidyError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        os.makedirs(output, exist_ok=True)
        for n in sorted(os.listdir(clean)):
            shutil.move(os.path.join(clean, n), os.path.join(output, n))
        print_summary(summary, False)
        return 0
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(clean, ignore_errors=True)
