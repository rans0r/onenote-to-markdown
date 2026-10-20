"""The cleanup steps, in the order they run.

Each step only changes the in-memory Vault and logs what it did. Order
matters: blank pages are judged before anything moves, titles are fixed
before pages become folders (folders take their page's name), and
attachments move last so they land next to the page's final location.
"""
from __future__ import annotations

import csv
import posixpath
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from ..cli import safe_name
from . import naming as N
from .vault import NUMBERED, Note, Vault, common_dir, key

from urllib.parse import unquote

JUNK_FILES = {".ds_store", "thumbs.db", "desktop.ini", ".localized"}
INDEX_TABLE = re.compile(r"^\| (?:#|Section|Notebook) \| (?:Page|Pages|Sections) \|", re.M)
DATE_LINE = re.compile(r"^\*[^*\n]*\b(?:1[89]|20)\d\d\b[^*\n]*\*[ \t]*$")
# OneNote 2007 pages carry their title, "Monday, June 04, 2007" and "9:15 AM" as the first lines of text
DAY_LINE = re.compile(r"^(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+[A-Z][a-z]+\s+\d{1,2},?\s+\d{4}$")
CLOCK_LINE = re.compile(r"^\d{1,2}:\d{2}\s*[AP]M$", re.I)
EVERNOTE_SUFFIX = re.compile(r"^(.*\S)\s+\(Evernote import on \d{4}-\d{2}-\d{2}T[\d-]+\)$")


@dataclass
class Options:
    skip: Set[str] = field(default_factory=set)
    only: Set[str] = field(default_factory=set)
    llm: bool = False
    llm_model: str = "claude-opus-5-5"
    renames: Optional[str] = None
    folder_note: str = "same-name"        # same-name | zero | index
    folder_numbers: bool = False
    drop_fields: Optional[List[str]] = None
    keep_fields: List[str] = field(default_factory=list)
    keep_versions: bool = False
    keep_json: bool = False
    chunk_pattern: str = r"Pages \d+-\d+"
    workers: int = 4


@dataclass
class Context:
    opts: Options
    namer: object = None                  # llm.ClaudeNamer when --llm
    ran: List[str] = field(default_factory=list)
    renames: List[dict] = field(default_factory=list)
    unlinked: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- helpers
def header_end(body: str, titles: Set[str]) -> int:
    """Where page content starts: after the `# Title` heading the exporter
    writes, and the italic date line under it, when they are there."""
    lines = body.split("\n")
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or not lines[i].startswith("# ") or lines[i][2:].strip() not in titles:
        return 0
    i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and DATE_LINE.match(lines[i]):
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
    return min(offsets[i], len(body)) if i < len(lines) else len(body)


def old_header_end(body: str, title: str) -> int:
    """Where content starts after a OneNote 2007 title, day and time block, or 0 if there isn't one."""
    lines = [line.replace("\u00a0", " ").strip() for line in body.split("\n")]
    i = 0
    while i < len(lines) and not lines[i]:
        i += 1
    if i + 1 >= len(lines) or " ".join(lines[i].split()) != " ".join(title.split()) \
            or not DAY_LINE.match(lines[i + 1]):
        return 0
    i += 2
    if i < len(lines) and CLOCK_LINE.match(lines[i]):
        i += 1
    while i < len(lines) and not lines[i]:
        i += 1
    return min(sum(len(line) + 1 for line in body.split("\n")[:i]), len(body))


def titles_of(note: Note) -> Set[str]:
    return {note.orig_title.strip(), note.title.strip()}


def content(note: Note) -> str:
    return note.body[header_end(note.body, titles_of(note)):]


def is_page(note: Note) -> bool:
    """A page the exporter wrote (`NN - Title.md`), not an index or an old version."""
    return note.number is not None and "_versions" not in note.parts[:-1] and not note.folder_only


def strip_number(name: str) -> str:
    m = NUMBERED.match(name)
    return m.group(2) if m else posixpath.splitext(name)[0]


def page_images(v: Vault, note: Note) -> List[Tuple[str, str, object]]:
    """(alt, file name, item) for each local image on a page."""
    from . import links as L
    out = []
    for link in L.iter_links(note.body):
        if link.is_image:
            item, _ = v.resolve(note, link.dest)
            if item is not None and not item.is_note:
                out.append((link.alt, item.name, item))
    return out


# ------------------------------------------------------------------ steps
def scaffolding(v: Vault, ctx: Context):
    """Index files, old versions, JSON dumps and system files."""
    for item in v.alive():
        parts, name = item.parts, item.name
        if name.lower() in JUNK_FILES or name.startswith("._") or "__MACOSX" in parts[:-1]:
            v.delete(item, "scaffolding", "system file")
        elif "_versions" in parts[:-1] and not ctx.opts.keep_versions:
            v.delete(item, "scaffolding", "older page version")
        elif "_json" in parts[:-1] and not ctx.opts.keep_json:
            v.delete(item, "scaffolding", "JSON dump")
        elif name.lower() == "readme.md" and item.is_note and not item.has_fm and INDEX_TABLE.search(item.body):
            v.delete(item, "scaffolding", "generated index")


def recycle_bin(v: Vault, ctx: Context):
    for item in v.alive():
        if "OneNote_RecycleBin" in item.parts[:-1]:
            v.delete(item, "recycle-bin", "deleted in OneNote")


def blank_pages(v: Vault, ctx: Context):
    for note in v.notes():
        if not is_page(note) or content(note).strip():
            continue
        if any(not c.deleted for c in note.children):
            note.folder_only = True
            v.log("blank-pages", "keep folder", note, "blank page with sub-pages")
        else:
            v.delete(note, "blank-pages", "no content")


def chunks(v: Vault, ctx: Context):
    """Merge sections like `Pages 1-100`, `Pages 101-200` into their notebook folder."""
    pattern = re.compile(rf"^(?:{ctx.opts.chunk_pattern})$", re.I)
    dirs: Dict[str, int] = {}
    for item in v.alive():
        for i, part in enumerate(item.parts[:-1]):
            if pattern.match(part):
                m = re.search(r"\d+", part)
                dirs["/".join(item.parts[:i + 1])] = int(m.group(0)) if m else 1
    def pages_in(d):
        return [n for n in v.notes() if n.dir == d and n.number is not None]

    # one number width per parent, so 099 sorts before 101
    tops: Dict[str, int] = {}
    for d, start in dirs.items():
        top = max((start - 1 + int(n.number) for n in pages_in(d)), default=0)
        tops[posixpath.dirname(d)] = max(tops.get(posixpath.dirname(d), 0), top)
    for d in sorted(dirs, key=lambda d: (-d.count("/"), dirs[d])):
        start, parent = dirs[d], posixpath.dirname(d)
        members = [i for i in v.alive() if i.path.startswith(d + "/")]
        pages = pages_in(d)
        width = max(2, len(str(tops[parent])))
        for item in sorted(members, key=lambda i: i.path):
            rest = item.path[len(d) + 1:]
            if item in pages:
                rest = f"{start - 1 + int(item.number):0{width}d} - {strip_number(item.name)}.md"
            v.place(item, posixpath.join(parent, rest) if parent else rest, "chunks")


def evernote_names(v: Vault, ctx: Context):
    renames: Dict[str, str] = {}
    for item in v.alive():
        for i, part in enumerate(item.parts[:-1]):
            m = EVERNOTE_SUFFIX.match(part)
            if m:
                renames.setdefault("/".join(item.parts[:i + 1]), m.group(1))
    for d in sorted(renames, key=lambda d: -d.count("/")):
        parent = posixpath.dirname(d)
        target = v.unique_dir(posixpath.join(parent, renames[d]) if parent else renames[d])
        for item in [i for i in v.alive() if i.path.startswith(d + "/")]:
            v.place(item, target + item.path[len(d):], "evernote-names")

def onenote_links(v: Vault, ctx: Context):
    """Convert internal OneNote page links to local Markdown links.

    OneNote links contain the target page title, for example:

        onenote:#Lektion%201%20-%20Effektiv%20respons
            &section-id={...}
            &page-id={...}

    Resolve that title against the exported pages and replace the
    OneNote URL with a link to the target page's original Markdown path.

    Vault.finalize_links() will later rewrite the link to the target's
    final location after titles, nesting and other tidy steps have run.
    """
    from . import links as L

    # Build an index of exported pages by OneNote title.
    # Keep a list for each title so duplicate titles are detected
    # instead of silently linking to the wrong page.
    by_title: Dict[str, List[Note]] = {}

    for target_note in v.notes():
        if not is_page(target_note):
            continue

        # Normally orig_title and title are identical at this point,
        # but indexing both makes this step tolerant of earlier changes.
        for title in {target_note.orig_title.strip(), target_note.title.strip()}:
            if not title:
                continue

            bucket = by_title.setdefault(title.casefold(), [])

            # Avoid adding the same Note twice when both titles are equal.
            if target_note not in bucket:
                bucket.append(target_note)

    # Scan every exported page for Markdown links whose destination
    # is a OneNote URL.
    for note in v.notes():
        if not is_page(note):
            continue

        body = note.body
        replacements = []

        for link in L.iter_links(body):
            # Images are irrelevant here.
            if link.is_image:
                continue

            dest = link.dest.strip()

            if not dest.lower().startswith("onenote:"):
                continue

            # Example:
            #
            # onenote:#Lektion%201%20-%20Effektiv%20respons
            #   &section-id={...}
            #   &page-id={...}
            #
            # Everything between '#' and the first '&' is the
            # URL-encoded OneNote page title.
            target = dest[len("onenote:"):]

            # OneNote links may point either within the current .one file:
            #   onenote:#Page%20Title&section-id=...
            #
            # or to another .one file:
            #   onenote:Other%20Section.one#Page%20Title&section-id=...
            #
            # In both cases, the page title follows '#'.
            if "#" not in target:
                continue

            _, page_part = target.rsplit("#", 1)
            encoded_title = page_part.split("&", 1)[0]
            target_title = unquote(encoded_title).strip()

            if not target_title:
                continue

            matches = by_title.get(target_title.casefold(), [])

            # Do not guess if the target cannot be identified uniquely.
            if len(matches) != 1:
                continue

            target_note = matches[0]

            # The local link must initially point to target_note.orig,
            # because Vault.resolve() resolves links relative to the
            # source page's original location.
            source_dir = posixpath.dirname(note.orig) or "."
            relative = posixpath.relpath(target_note.orig, source_dir)

            # Encode spaces and other characters exactly as normal
            # Markdown links produced by the exporter.
            relative = L.encode(relative)

            replacements.append(
                (link.start, link.end, relative)
            )

        if not replacements:
            continue

        # Work backwards through the text so replacing one destination
        # cannot invalidate offsets belonging to later links.
        for start, end, replacement in reversed(replacements):
            body = body[:start] + replacement + body[end:]

        note.body = body

        v.log(
            "onenote-links",
            "edit",
            note,
            f"converted {len(replacements)} OneNote link(s)",
        )


def titles(v: Vault, ctx: Context):
    """Retitle pages named after a cut-off first line, a URL or code, or nothing."""
    from_file = read_renames(ctx.opts.renames) if ctx.opts.renames else None
    jobs: List[dict] = []
    for note in v.notes():
        if not is_page(note):
            continue
        if from_file is not None:
            wanted = from_file.get(key(note.orig))
            if wanted and wanted != note.title:
                jobs.append({"note": note, "reason": "renames file", "title": wanted, "source": "renames file"})
            continue
        body = content(note)
        reason = N.title_problem(note.title, N.first_line(body))
        if reason:
            jobs.append({"note": note, "reason": reason, "title": None, "source": ""})

    def propose(job):
        note = job["note"]
        body = content(note)
        images = page_images(v, note)
        if ctx.namer is not None:
            from .llm import load_image
            image = None
            if not N.content_lines(body):       # an image-only page: let Claude see the image
                image = next(filter(None, (load_image(i.src) for _, _, i in images)), None)
            title = ctx.namer.page_title(note.title, note.dir, body, image)
            if title:
                return title, "Claude"
        return N.heuristic_title(note.title, body, [(alt, name) for alt, name, _ in images]), "rules"

    pending = [j for j in jobs if j["title"] is None]
    with ThreadPoolExecutor(max_workers=max(1, ctx.opts.workers if ctx.namer else 1)) as pool:
        for job, (title, source) in zip(pending, pool.map(propose, pending)):
            job["title"], job["source"] = title, source

    for job in jobs:
        note, new = job["note"], job["title"]
        row = {"path": note.orig, "old_title": note.title, "new_title": new or "", "reason": job["reason"],
               "source": job["source"] if new else "kept", "note": note}
        ctx.renames.append(row)
        if new:
            retitle(v, note, new)


def retitle(v: Vault, note: Note, new: str):
    new = " ".join(new.split())
    old = note.title
    note.fm.set("title", new)
    lines = note.body.split("\n")
    for i, line in enumerate(lines):
        if line.strip():
            if line.startswith("# ") and line[2:].strip() in (old, note.orig_title):
                lines[i] = "# " + new
                note.body = "\n".join(lines)
            break
    v.place(note, posixpath.join(note.dir, f"{note.number} - {safe_name(new)}.md"), "titles", "rename")


def header(v: Vault, ctx: Context):
    """Drop the `# Title` heading and date line that repeat the front matter."""
    for note in v.notes():
        if not note.has_fm:
            continue
        end = header_end(note.body, titles_of(note))
        old = old_header_end(note.body[end:], note.title)
        if end or old:
            note.body = "\n" + note.body[end + old:]
            v.log("header", "edit", note, "removed title heading and date line")


def nest(v: Vault, ctx: Context):
    """Pages with sub-pages become folders holding the page and its sub-pages."""
    opts = ctx.opts

    def folder_note_name(folder: str, note: Note) -> str:
        if opts.folder_note == "index":
            return "index.md"
        if opts.folder_note == "zero":
            return f"00 - {strip_number(note.name)}.md"
        return posixpath.basename(folder) + ".md"

    def place_tree(note: Note):
        kids = [c for c in note.children if not c.deleted]
        if not kids:
            return
        title = strip_number(note.name)
        name = f"{note.number} - {title}" if opts.folder_numbers and note.number else title
        folder = v.unique_dir(posixpath.join(note.dir, name))
        if note.folder_only:
            v.delete(note, "nest", "blank page; its folder holds the sub-pages")
        else:
            v.place(note, posixpath.join(folder, folder_note_name(folder, note)), "nest")
        width = max(2, len(str(len(kids))))
        for i, kid in enumerate(kids, 1):
            v.place(kid, posixpath.join(folder, f"{i:0{width}d} - {strip_number(kid.name)}.md"), "nest")
            place_tree(kid)

    for note in v.notes():
        if note.parent is None and note.number is not None:
            place_tree(note)


def attachments(v: Vault, ctx: Context):
    """Move each image or file next to the pages that use it."""
    refs = v.backlinks(include=lambda n: "_versions" not in n.parts[:-1])
    for item in v.assets():
        users = refs.get(item)
        if users:
            target = common_dir(n.dir for n, _ in users)
        elif "assets" in item.parts[:-1]:
            # nothing links to it: move it out of assets/ into that section's folder
            i = max(n for n, part in enumerate(item.parts[:-1]) if part == "assets")
            target = "/".join(item.parts[:i])
            ctx.unlinked.append(item.orig)
        else:
            continue
        if item.dir != target:
            v.place(item, posixpath.join(target, item.name) if target else item.name, "attachments")


def attachment_names(v: Vault, ctx: Context):
    """Name generically named images after their alt text, contents or page."""
    refs = v.backlinks(include=lambda n: "_versions" not in n.parts[:-1])
    jobs = []
    counts: Dict[Tuple[str, str], int] = {}
    for item in v.assets():
        users = refs.get(item)
        if not users or not N.is_generic_asset(item.name):
            continue
        note, link = users[0]
        ext = posixpath.splitext(item.name)[1]
        jobs.append({"item": item, "note": note, "alt": link.alt, "ext": ext})

    def propose(job):
        item, note = job["item"], job["note"]
        name = N.name_from_alt(job["alt"], item.name)
        if name:
            return name
        if ctx.namer is not None:
            from .llm import load_image
            image = load_image(item.src)
            if image:
                name = ctx.namer.image_name(note.title, job["alt"], image)
                if name:
                    return name
        return None

    with ThreadPoolExecutor(max_workers=max(1, ctx.opts.workers if ctx.namer else 1)) as pool:
        names = list(pool.map(propose, jobs))
    for job, name in zip(jobs, names):
        item, note = job["item"], job["note"]
        if not name:
            if N.UNTITLED.match(note.title.strip()):
                continue                      # "(Untitled).jpg" says no more than "image.jpg"
            name = note.title
            n = counts[(note.path, name)] = counts.get((note.path, name), 0) + 1
            if n > 1:
                name = f"{name} {n}"
        v.place(item, posixpath.join(item.dir, safe_name(name) + job["ext"]), "attachment-names", "rename")


def frontmatter(v: Vault, ctx: Context):
    """Drop fields the folder structure now shows."""
    drop = ctx.opts.drop_fields
    if drop is None:
        drop = ["notebook", "section"] + (["level"] if "nest" in ctx.ran else [])
    drop = [f for f in drop if f not in ctx.opts.keep_fields]
    for note in v.notes():
        removed = [f for f in drop if note.fm.drop(f)]
        if removed:
            v.log("frontmatter", "edit", note, "removed " + ", ".join(removed))


def read_renames(path: str) -> Dict[str, str]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {key(r["path"]): r["new_title"].strip() for r in csv.DictReader(f)
                if r.get("path") and (r.get("new_title") or "").strip()}


STEPS: List[Tuple[str, Callable[[Vault, Context], None], str]] = [
    ("scaffolding", scaffolding,
     "remove the exporter's README.md indexes, _versions/ and _json/ (unless you asked for them), "
     "and system files (.DS_Store, __MACOSX, Thumbs.db, desktop.ini)"),
    ("recycle-bin", recycle_bin, "remove OneNote_RecycleBin/ – pages deleted in OneNote"),
    ("blank-pages", blank_pages, "remove pages with nothing but a title and date"),
    ("chunks", chunks, "merge 'Pages 1-100'-style sections into their notebook folder"),
    ("evernote-names", evernote_names, "drop ' (Evernote import on …)' from folder names"),
    ("onenote-links", onenote_links, "convert internal OneNote page links to local Markdown links"),
    ("titles", titles, "retitle pages named after a cut-off first line, a URL or code, or Untitled"),
    ("header", header, "remove the '# Title' heading and date line that repeat the front matter"),
    ("nest", nest, "turn pages that have sub-pages into folders"),
    ("attachments", attachments, "move images and files next to the pages that use them"),
    ("attachment-names", attachment_names, "give generically named images (image-1.jpg, …) real names"),
    ("frontmatter", frontmatter, "drop notebook, section and level from the front matter"),
]
STEP_NAMES = [name for name, _, _ in STEPS]
