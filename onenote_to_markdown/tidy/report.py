"""The report written next to (never inside) the cleaned notes."""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import posixpath
import re
from collections import defaultdict
from typing import Dict, List, Tuple

from .scan import Finding
from .steps import STEPS, Context, content
from .vault import Note, Vault

CODE_EXTENSIONS = {".php", ".py", ".js", ".ts", ".json", ".bin", ".exe", ".dll", ".sh", ".bat", ".cmd",
                   ".ps1", ".sql", ".java", ".cs", ".rb", ".go", ".jar", ".msi", ".dmg", ".apk", ".vbs"}
LIST_LIMIT = 40
# Dropbox, Syncthing, OneDrive/Google Drive and macOS case-clash copies
SYNC_CONFLICT = re.compile(r"conflicted copy|\.sync-conflict-|\(case conflict|-conflict-\d", re.I)


def attention(v: Vault, ctx: Context, broken: List[Tuple[Note, str, str]], findings: List[Finding]) -> Dict[str, list]:
    near_empty = [n.path for n in v.notes() if n.has_fm and 0 < len("".join(content(n).split())) < 20]
    code = [a.path for a in v.assets()
            if posixpath.splitext(a.name)[1].lower() in CODE_EXTENSIONS and "_json" not in a.parts[:-1]]
    conflicts = sorted({i.path for i in v.alive() if SYNC_CONFLICT.search(i.name)})
    long_paths = [i.path for i in v.alive() if len(i.path) > 200]
    return {
        "sensitive": findings,
        "broken": [(n.path, dest, why) for n, dest, why in broken],
        "unlinked": ctx.unlinked,
        "near_empty": near_empty,
        "code": code,
        "conflicts": conflicts,
        "long_paths": long_paths,
        "unreadable": v.unreadable,
    }


def write(report_dir: str, v: Vault, ctx: Context, att: Dict[str, list], source: str, output: str,
          dry_run: bool, errors: List[str]) -> str:
    os.makedirs(report_dir, exist_ok=True)
    with open(os.path.join(report_dir, "actions.jsonl"), "w", encoding="utf-8") as f:
        for a in v.actions:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
    with open(os.path.join(report_dir, "renames.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["path", "old_title", "new_title", "reason", "source"], extrasaction="ignore")
        w.writeheader()
        w.writerows(ctx.renames)

    by_step: Dict[str, List[dict]] = defaultdict(list)
    for a in v.actions:
        by_step[a["step"]].append(a)
    md = [f"# Tidy report", "",
          f"- Source: `{source}`",
          f"- Output: `{output}`" + (" (dry run – nothing written)" if dry_run else ""),
          f"- Run: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
          f"- Steps: {', '.join(ctx.ran) or 'none'}", "",
          "Every change is listed in `actions.jsonl`; title changes are in `renames.csv`, which you can "
          "edit and pass back with `--renames` (or `--tidy-renames`).", ""]

    md += ["## Needs your attention", ""]
    sensitive: List[Finding] = att["sensitive"]
    sections = [
        ("Possible passwords, keys and other sensitive data", [f"`{f.path}`" + (f" line {f.line}" if f.line else "")
                                                                + f" – {f.kind}: {f.sample}" for f in sensitive]),
        ("Links that point at nothing", [f"`{p}` → `{d}` ({why})" for p, d, why in att["broken"]]),
        ("Attachments no page links to (kept, moved out of assets/)", [f"`{p}`" for p in att["unlinked"]]),
        ("Pages with almost no text", [f"`{p}`" for p in att["near_empty"]]),
        ("Code and program files", [f"`{p}`" for p in att["code"]]),
        ("Possible sync-conflict copies", [f"`{p}`" for p in att["conflicts"]]),
        ("Paths over 200 characters (may fail on Windows)", [f"`{p}`" for p in att["long_paths"]]),
        ("Pages that aren't valid UTF-8 (copied unchanged)", [f"`{p}`" for p in att["unreadable"]]),
        ("Claude naming problems", errors),
    ]
    any_attention = False
    for title, rows in sections:
        if not rows:
            continue
        any_attention = True
        md += [f"### {title} ({len(rows)})", ""] + [f"- {r}" for r in rows[:200]]
        if len(rows) > 200:
            md.append(f"- … and {len(rows) - 200} more")
        md.append("")
    if not any_attention:
        md += ["Nothing found.", ""]

    md += ["## Changes by step", ""]
    for name, _, description in STEPS:
        if name not in ctx.ran:
            continue
        rows = by_step.get(name, [])
        md += [f"### {name} – {len(rows)} change(s)", "", f"{description[0].upper()}{description[1:]}.", ""]
        for a in rows[:LIST_LIMIT]:
            line = f"- {a['action']} `{a['path']}`"
            if "to" in a:
                line += f" → `{a['to']}`"
            if a.get("detail"):
                line += f" ({a['detail']})"
            md.append(line)
        if len(rows) > LIST_LIMIT:
            md.append(f"- … and {len(rows) - LIST_LIMIT} more in `actions.jsonl`")
        md.append("")
    if ctx.renames:
        md += ["## Title changes", "", "| Page | Old title | New title | Why | By |", "|---|---|---|---|---|"]
        for r in ctx.renames:
            cells = [r["note"].path, r["old_title"], r["new_title"] or "*(kept)*", r["reason"], r["source"]]
            md.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
        md.append("")
        if ctx.namer is None and any(not r["new_title"] for r in ctx.renames):
            md += ["*(kept)* pages had no text or descriptive image names to take a title from. "
                   "`--llm` (`--tidy-llm`) lets Claude look at their images.", ""]
    path = os.path.join(report_dir, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return path
