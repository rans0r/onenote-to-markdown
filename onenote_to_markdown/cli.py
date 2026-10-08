"""
Command-line entry point: convert OneNote section files (.one) into organized
Markdown.

Supports the *alternative packaging* (MS-FSSHTTPB) .one files that OneDrive
and SharePoint hand out when you download a notebook folder as a zip.
(Classic revision-store .one files written by desktop OneNote to a local
disk are a different container – see README.)

Usage:
    python -m onenote_to_markdown <notebook-folder | file.one ...> -o OUTPUT_DIR
        [--versions] [--json] [--notebook NAME] [--version]
        [--tidy [--tidy-llm] [--tidy-skip STEPS] [--tidy-renames CSV]]

Output layout:
    OUTPUT_DIR/<Notebook>/<Section>/
        README.md                     – index of pages (with hierarchy)
        01 - <Page title>.md          – one file per page, in notebook order
        assets/                       – images / embedded files
        _versions/                    – older page versions (with --versions)
        _json/                        – raw property dumps (with --json)

With --tidy the same run also cleans the export up for Markdown note apps:
index files, deleted pages and blank pages go, sub-pages become folders,
images sit next to their pages, and a report lands in OUTPUT_DIR-tidy-report.
See docs/tidy.md.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import datetime as dt
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import onestore as O
from . import __version__

# ----------------------------------------------------------------------------
# property ids used by the exporter (MS-ONE §2.1.12)
# ----------------------------------------------------------------------------
P = dict(
    ElementChildNodes=0x24001C20, ContentChildNodes=0x24001C1F, ListNodes=0x24001C26,
    StructureElementChildNodes=0x24001D5F, ChildGraphSpaceElementNodes=0x2C001D63,
    MetaDataObjectsAboveGraphSpace=0x24003442, OutlineElementChildLevel=0x0C001C03,
    RichEditTextUnicode=0x1C001C22, TextExtendedAscii=0x1C003498, TextRunIndex=0x1C001E12,
    TextRunFormatting=0x24001E13, ParagraphStyle=0x2000342C, ParagraphStyleId=0x1C00345A,
    Bold=0x08001C04, Italic=0x08001C05, Underline=0x08001C06, Strikethrough=0x08001C07,
    Superscript=0x08001C08, Subscript=0x08001C09, Font=0x1C001C0A, FontSize=0x10001C0B,
    Hyperlink=0x08001E14, WzHyperlinkUrl=0x1C001E20, NumberListFormat=0x1C001C1A,
    ListRestart=0x14001CB7, IsTitleText=0x08001CB4, IsTitleDate=0x08001CB5,
    IsTitleTime=0x08001C87, CachedTitleString=0x1C001CF3, PageLevel=0x14001DFF,
    PictureContainer=0x20001C3F, ImageFilename=0x1C001DD7, ImageAltText=0x1C001E58,
    PictureWidth=0x140034CD, PictureHeight=0x140034CE, EmbeddedFileContainer=0x20001D9B,
    EmbeddedFileName=0x1C001D9C, RowCount=0x14001D57, ColumnCount=0x14001D58,
    NoteTagStates=0x40003489, NoteTagDefinitionOid=0x20003488, NoteTagLabel=0x1C003468,
    NoteTagShape=0x10003464, ActionItemStatus=0x10003470, NoteTagCompleted=0x1400346F,
    LastModifiedTime=0x14001D7A, TopologyCreationTimeStamp=0x18001C65,
    LastModifiedTimeStamp=0x18001D77, CreationTimeStamp=0x14001D09,
    AuthorMostRecent=0x20001D79, AuthorOriginal=0x20001D78, Author=0x1C001D75,
    IsDeletedGraphSpaceContent=0x00001DE9, VersionProxyRefs=0x3400347B,
    IsBoilerText=0x08001C88, TextRunIsEmbeddedObject=0x08001E22,
    TextRunDataObject=0x24003458, LanguageID=0x14001C3B,
    MediaUrl=0x1C0035F3, InkData=0x20003415,
)

J = dict(
    Section=0x00060007, PageSeries=0x00060008, Page=0x0006000B, Outline=0x0006000C,
    OutlineElement=0x0006000D, RichText=0x0006000E, Image=0x00060011, NumberList=0x00060012,
    OutlineGroup=0x00060019, Table=0x00060022, TableRow=0x00060023, TableCell=0x00060024,
    Title=0x0006002C, PageMetaData=0x00020030, EmbeddedFile=0x00060035,
    PageManifest=0x00060037, VersionHistoryContent=0x0006003C, VersionProxy=0x0006003D,
    Ink=0x00060014, EmbeddedMedia=0x00060058,
)

HYPERLINK_FIELD = "﷟"


def utf16(b: Optional[bytes]) -> str:
    if not b:
        return ""
    if len(b) % 2:
        b = b[:-1]
    return b.decode("utf-16le", errors="replace").rstrip("\x00")


def time32(v: Optional[int]) -> Optional[dt.datetime]:
    """MS-ONE Time32: seconds since 1980-01-01 UTC."""
    if v is None:
        return None
    return dt.datetime(1980, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=v)


def filetime(v: Optional[int]) -> Optional[dt.datetime]:
    if not v:
        return None
    return dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(microseconds=v / 10)


def iso(d: Optional[dt.datetime]) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S UTC") if d else ""


def safe_name(s: str, limit: int = 80) -> str:
    s = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", s).strip().rstrip(".")
    s = re.sub(r"\s+", " ", s)
    return (s[:limit].rstrip() or "Untitled")


_MD_PATH_ESCAPES = {"%": "%25", " ": "%20", "(": "%28", ")": "%29", "<": "%3C", ">": "%3E", "#": "%23", "?": "%3F"}


def md_path(path: str) -> str:
    """A relative path as a Markdown link destination.

    Spaces and parentheses end a destination in CommonMark, so they (and `%`,
    which would otherwise read as an escape) are percent-encoded; other
    characters, including non-ASCII letters, stay readable.
    """
    return "".join(_MD_PATH_ESCAPES.get(c, c) for c in path)


# ----------------------------------------------------------------------------
# Renderer
# ----------------------------------------------------------------------------
@dataclass
class PageOut:
    title: str
    level: int
    created: Optional[dt.datetime]
    modified: Optional[dt.datetime]
    body: str
    space: O.ObjectSpace
    warnings: List[str] = field(default_factory=list)
    text_chars: int = 0        # plain characters emitted (for verification)
    paragraphs: int = 0
    is_version: bool = False


class MarkdownRenderer:
    def __init__(self, section: O.Section, assets_dir: str, asset_prefix: str = "assets"):
        self.sec = section
        self._prev_was_list = False
        self._asset_cache: Dict[str, str] = {}
        self.assets_dir = assets_dir
        self.asset_prefix = asset_prefix
        self.asset_count = 0
        self.warnings: List[str] = []
        self.text_chars = 0
        self.paragraphs = 0
        self.unknown_jcids: Dict[str, int] = {}

    # -- helpers --------------------------------------------------------------
    def kids(self, sp, node, pid) -> List[O.Node]:
        v = node.get(pid)
        if v is None:
            return []
        if not isinstance(v, list):
            v = [v]
        return sp.resolve(v)

    def warn(self, msg):
        self.warnings.append(msg)

    def write_asset(self, data: bytes, name: str) -> str:
        digest = hashlib.sha1(data).hexdigest()
        if digest in self._asset_cache:
            return self._asset_cache[digest]
        os.makedirs(self.assets_dir, exist_ok=True)
        self.asset_count += 1
        base = safe_name(re.split(r"[?#&]", name or "")[0] or f"asset-{self.asset_count}")
        if "." not in base:
            base += guess_ext(data)
        path = os.path.join(self.assets_dir, base)
        n = 1
        while os.path.exists(path):
            stem, ext = os.path.splitext(base)
            path = os.path.join(self.assets_dir, f"{stem}-{n}{ext}")
            n += 1
        with open(path, "wb") as f:
            f.write(data)
        rel = f"{self.asset_prefix}/{os.path.basename(path)}"
        self._asset_cache[digest] = rel
        return rel

    # -- text -----------------------------------------------------------------
    def rich_text(self, sp, node) -> str:
        text = utf16(node.get(P["RichEditTextUnicode"]))
        if not text and node.get(P["TextExtendedAscii"]):
            text = node.get(P["TextExtendedAscii"]).decode("cp1252", errors="replace").rstrip("\x00")
        idx = node.get(P["TextRunIndex"])
        fmts = self.kids(sp, node, P["TextRunFormatting"])
        bounds = [0]
        if idx:
            n = len(idx) // 4
            bounds += [int.from_bytes(idx[4 * i:4 * i + 4], "little") for i in range(n)]
        bounds.append(len(text))
        runs = []
        for i in range(len(bounds) - 1):
            seg = text[bounds[i]:bounds[i + 1]]
            fmt = fmts[i] if i < len(fmts) else None
            runs.append((seg, fmt))
        # merge hyperlink field codes: "﷟HYPERLINK \"url\"" + display text
        out = []
        pending_url = None
        for seg, fmt in runs:
            if HYPERLINK_FIELD in seg:
                m = re.match(r'﷟HYPERLINK\s+"([^"]*)"(.*)', seg, re.S)
                if m:
                    pending_url = m.group(1)
                    seg = m.group(2)
                    if not seg:
                        continue
            if pending_url is not None:
                out.append(f"[{seg}]({pending_url})")
                pending_url = None
                continue
            url = utf16(fmt.get(P["WzHyperlinkUrl"])) if fmt else ""
            out.append(self.apply_format(seg, fmt, url))
        self.text_chars += len(text)
        s = "".join(out)
        # soft line breaks inside a paragraph
        s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\v", "\n")
        return s

    @staticmethod
    def apply_format(seg: str, fmt, url: str) -> str:
        if not seg.strip() or fmt is None:
            return seg
        lead = seg[:len(seg) - len(seg.lstrip())]
        trail = seg[len(seg.rstrip()):]
        core = seg.strip()
        if fmt.get(P["Bold"]):
            core = f"**{core}**"
        if fmt.get(P["Italic"]):
            core = f"*{core}*"
        if fmt.get(P["Strikethrough"]):
            core = f"~~{core}~~"
        if fmt.get(P["Underline"]):
            core = f"<u>{core}</u>"
        if fmt.get(P["Superscript"]):
            core = f"<sup>{core}</sup>"
        if fmt.get(P["Subscript"]):
            core = f"<sub>{core}</sub>"
        if url:
            core = f"[{core}]({url})"
        elif fmt.get(P["Hyperlink"]) and re.match(r"^(https?://|www\.|mailto:)\S+$", core):
            core = f"<{core}>"          # auto-detected link: the text is the URL
        return lead + core + trail

    def style_id(self, sp, node) -> str:
        st = sp.node(node.get(P["ParagraphStyle"]))
        return utf16(st.get(P["ParagraphStyleId"])) if st else ""

    def list_marker(self, sp, oe) -> str:
        lists = self.kids(sp, oe, P["ListNodes"])
        if not lists:
            return ""
        fmt = utf16(lists[0].get(P["NumberListFormat"]))
        # NumberListFormat = <count><format chars>; U+FFFD marks where the
        # number goes (followed by a one-char number-format code).
        # Anything else (•, ○, Wingdings glyphs …) is a bullet.
        body = fmt[1:1 + ord(fmt[0])] if fmt and ord(fmt[0]) < 0x20 else fmt
        if "\ufffd" in body or re.search(r"[0-9a-zA-Z]", body):
            return "1. "
        return "- "

    def note_tags(self, sp, *nodes) -> str:
        states = []
        for nd in nodes:
            if nd is not None:
                states += nd.get(P["NoteTagStates"]) or []
        if not states:
            return ""
        marks = []
        for st in states:
            d = sp.node(st.get(P["NoteTagDefinitionOid"]))
            label = utf16(d.get(P["NoteTagLabel"])) if d else "tag"
            shape = d.get(P["NoteTagShape"]) if d else 0
            done = bool((st.get(P["ActionItemStatus"]) or 0) & 1) or bool(st.get(P["NoteTagCompleted"]))
            if shape and 3 <= shape <= 22 or "to do" in label.lower():
                marks.append("[x] " if done else "[ ] ")
            else:
                marks.append(f"`[{label}]` ")
        return "".join(marks)

    # -- structure ------------------------------------------------------------
    def outline_element(self, sp, oe, depth: int, lines: List[str], list_depth: int = 0, ordinal: int = 0):
        """Render one outline element and its nested children.

        depth       – nesting depth in the outline (0 = top level)
        list_depth  – how many list-item ancestors there are (drives Markdown
                      list indentation; other nesting is shown with no-break
                      spaces so it never turns into a code block)
        ordinal     – 1-based position among numbered siblings (0 = not numbered)
        """
        content = self.kids(sp, oe, P["ContentChildNodes"])
        lm, tags = self.list_marker(sp, oe), self.note_tags(sp, oe, *content)
        if lm == "1. " and ordinal:
            lm = f"{ordinal}. "
        if tags.startswith("[") and not lm:
            lm = "- "                      # GFM task list needs a list item
        marker = lm + tags
        is_list = bool(lm)
        if is_list:
            indent = "    " * list_depth
        else:
            indent = "    " * list_depth + "\u00a0" * 4 * max(depth - list_depth, 0)
        # OneNote stacks paragraphs on consecutive lines.  Reproduce that with a
        # Markdown hard line break ("  " + newline) between plain paragraphs;
        # list items, table rows and code fences already stand on their own.
        if lines and lines[-1].strip() and not is_list and not self._prev_was_list \
                and not lines[-1].rstrip().endswith("```") and not lines[-1].lstrip().startswith("|"):
            lines[-1] = lines[-1].rstrip() + "  "
        elif lines and lines[-1].strip() and (is_list != self._prev_was_list):
            lines.append("")
        self._prev_was_list = is_list
        wrote = False
        for c in content:
            if c.jcid == J["RichText"]:
                txt = self.rich_text(sp, c)
                style = self.style_id(sp, c)
                lines.extend(self.paragraph(txt, style, indent, marker))
                wrote = True
            elif c.jcid == J["Image"]:
                lines.append(indent + marker + self.image(sp, c))
                wrote = True
            elif c.jcid == J["Table"]:
                lines.extend(self.table(sp, c, "    " * list_depth))
                wrote = True
            elif c.jcid == J["EmbeddedFile"]:
                lines.append(indent + marker + self.embedded_file(sp, c))
                wrote = True
            elif c.jcid == J["Ink"]:
                lines.append(indent + marker + "*[ink drawing – not exported]*")
                wrote = True
            else:
                self.unknown(c)
        if not wrote and (marker.strip()):
            lines.append(indent + marker.rstrip())
        self.paragraphs += 1 if content else 0
        children = [ch for ch in self.kids(sp, oe, P["ElementChildNodes"])]
        n = 0
        for child in children:
            if child.jcid in (J["OutlineElement"], J["OutlineGroup"]):
                if self.list_marker(sp, child) == "1. ":
                    n += 1
                    self.outline_element(sp, child, depth + 1, lines, list_depth + is_list, n)
                else:
                    self.outline_element(sp, child, depth + 1, lines, list_depth + is_list, 0)
            else:
                self.unknown(child)

    def paragraph(self, txt: str, style: str, indent: str, marker: str) -> List[str]:
        style = (style or "").lower()
        if style in ("h1", "h2", "h3", "h4", "h5", "h6") and not marker and txt.strip():
            return [f"{indent}{'#' * (int(style[1]) + 1)} {txt}"]
        if style == "code":
            body = txt.split("\n")
            return [indent + "```"] + [indent + l for l in body] + [indent + "```"]
        if style == "blockquote":
            return [indent + marker + "> " + l for l in txt.split("\n")]
        if style == "cite":
            txt = f"*{txt}*" if txt.strip() else txt
        parts = [self.keep_leading_spaces(p) for p in txt.split("\n")]
        if not parts:
            return [indent + marker.rstrip()]
        out = [indent + marker + parts[0]]
        cont = indent + " " * len(marker)
        for extra in parts[1:]:
            out[-1] += "  "          # markdown hard line break
            out.append(cont + extra)
        return out

    @staticmethod
    def keep_leading_spaces(line: str) -> str:
        """Leading spaces/tabs would turn a line into a Markdown code block;
        swap them for no-break spaces so the indentation is preserved visually."""
        m = re.match(r"[ \t]+", line)
        if not m:
            return line
        lead = m.group(0).replace("\t", "    ").replace(" ", "\u00a0")
        return lead + line[m.end():]

    def image(self, sp, img) -> str:
        holder = sp.node(img.get(P["PictureContainer"]))
        data = holder.file_data if holder else None
        name = utf16(img.get(P["ImageFilename"]))
        alt = utf16(img.get(P["ImageAltText"])) or name or "image"
        # an embedded online video keeps its URL in a child media node
        media = [utf16(m.get(P["MediaUrl"])) for m in self.kids(sp, img, P["ContentChildNodes"])
                 if m.jcid == J["EmbeddedMedia"]]
        link = utf16(img.get(P["WzHyperlinkUrl"])) or next((m for m in media if m), "")
        if data:
            rel = self.write_asset(data, name or "image")
            md = f"![{alt}]({md_path(rel)})"
            return f"[{md}]({link})" if link else md
        if link:
            return f"[{alt}]({link})"
        self.warn(f"image without data (filename={name!r})")
        return f"![{alt}](missing-image)"

    def embedded_file(self, sp, node) -> str:
        holder = sp.node(node.get(P["EmbeddedFileContainer"]))
        name = utf16(node.get(P["EmbeddedFileName"])) or "attachment"
        if holder and holder.file_data:
            rel = self.write_asset(holder.file_data, name)
            return f"📎 [{name}]({md_path(rel)})"
        self.warn(f"embedded file without data ({name!r})")
        return f"📎 {name} (data missing)"

    def cell_text(self, sp, cell) -> str:
        lines: List[str] = []
        for oe in self.kids(sp, cell, P["ElementChildNodes"]):
            self.outline_element(sp, oe, 0, lines)
        out = []
        for l in lines:
            l = l.strip()
            if not l or re.match(r"^\|?(\s*-+\s*\|)+\s*$", l):
                continue                      # skip blanks and nested-table separator rows
            if l.startswith("|"):             # nested table row -> "a · b"
                l = " · ".join(c.strip() for c in l.strip("|").split("|"))
            out.append(l.replace("|", "\\|"))
        return "<br>".join(out)

    def table(self, sp, tbl, indent: str) -> List[str]:
        rows = [r for r in self.kids(sp, tbl, P["ElementChildNodes"]) if r.jcid == J["TableRow"]]
        grid = []
        for r in rows:
            cells = [c for c in self.kids(sp, r, P["ElementChildNodes"]) if c.jcid == J["TableCell"]]
            grid.append([self.cell_text(sp, c) for c in cells])
        if not grid:
            return []
        width = max(len(r) for r in grid)
        out = [indent + "| " + " | ".join(grid[0] + [""] * (width - len(grid[0]))) + " |",
               indent + "|" + " --- |" * width]
        for r in grid[1:]:
            out.append(indent + "| " + " | ".join(r + [""] * (width - len(r))) + " |")
        return out

    def unknown(self, node):
        self.unknown_jcids[node.jcid_name] = self.unknown_jcids.get(node.jcid_name, 0) + 1

    def outline(self, sp, ol, lines: List[str]):
        self._prev_was_list = False
        n = 0
        for oe in self.kids(sp, ol, P["ElementChildNodes"]):
            if oe.jcid in (J["OutlineElement"], J["OutlineGroup"]):
                if self.list_marker(sp, oe) == "1. ":
                    n += 1
                    self.outline_element(sp, oe, 0, lines, 0, n)
                else:
                    n = 0
                    self.outline_element(sp, oe, 0, lines, 0, 0)
            else:
                self.unknown(oe)

    # -- page -----------------------------------------------------------------
    def page(self, sp: O.ObjectSpace, meta: Optional[O.Node]) -> PageOut:
        self.warnings, self.text_chars, self.paragraphs = [], 0, 0
        manifest = sp.root
        page = None
        if manifest is not None:
            if manifest.jcid == J["Page"]:
                page = manifest
            else:
                for c in self.kids(sp, manifest, P["ContentChildNodes"]):
                    if c.jcid == J["Page"]:
                        page = c
        title_txt, date_txt, time_txt = "", "", ""
        body: List[str] = []
        if page is not None:
            # title block lives under StructureElementChildNodes
            for t in self.kids(sp, page, P["StructureElementChildNodes"]):
                if t.jcid != J["Title"]:
                    continue
                for ol in self.kids(sp, t, P["ElementChildNodes"]):
                    tmp: List[str] = []
                    self.outline(sp, ol, tmp)
                    text = "\n".join(l.strip() for l in tmp if l.strip())
                    if ol.get(P["IsTitleText"]):
                        title_txt = text
                    elif ol.get(P["IsTitleDate"]):
                        # holds both the date and time paragraphs
                        parts = [l.strip() for l in tmp if l.strip()]
                        date_txt = parts[0] if parts else ""
                        time_txt = parts[1] if len(parts) > 1 else ""
                    else:
                        title_txt = title_txt or text
            for c in self.kids(sp, page, P["ElementChildNodes"]):
                if c.jcid == J["Outline"]:
                    if body:
                        body.append("")
                    self.outline(sp, c, body)
                elif c.jcid == J["Image"]:
                    body.append(self.image(sp, c))
                elif c.jcid == J["Table"]:
                    body.extend(self.table(sp, c, ""))
                elif c.jcid == J["EmbeddedFile"]:
                    body.append(self.embedded_file(sp, c))
                elif c.jcid == J["Ink"]:
                    body.append("*[ink drawing – not exported]*")
                elif c.jcid == J["Title"]:
                    pass
                else:
                    self.unknown(c)
        cached = utf16(meta.get(P["CachedTitleString"])) if meta else ""
        if not cached and page is not None:
            cached = utf16(page.get(0x1C001D3C))
        title = title_txt.strip() or cached.strip() or "Untitled page"
        level = (meta.get(P["PageLevel"]) if meta else None) or 1
        created = filetime(meta.get(P["TopologyCreationTimeStamp"])) if meta else None
        modified = time32(page.get(P["LastModifiedTime"])) if page is not None else None
        header = []
        if date_txt or time_txt:
            header.append(f"*{date_txt} {time_txt}*".strip())
        text = "\n".join(header + ([""] if header else []) + body).rstrip() + "\n"
        text = re.sub(r"```\n\n( *)```\n", "", text)   # merge consecutive code paragraphs
        text = re.sub(r"(?m)^[ \u00a0]+$", "", text)      # whitespace-only lines -> empty
        text = re.sub(r"\n{4,}", "\n\n\n", text)         # at most two blank lines in a row
        return PageOut(title, level, created, modified, text, sp, list(self.warnings),
                       self.text_chars, self.paragraphs)


def guess_ext(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"%PDF":
        return ".pdf"
    if data[:2] == b"BM":
        return ".bmp"
    if data[:4] == b"RIFF":
        return ".webp"
    return ".bin"


# ----------------------------------------------------------------------------
# Section → files
# ----------------------------------------------------------------------------
def page_spaces_in_order(sec: O.Section):
    """Yield (ObjectSpace, PageMetaData node) for current pages in notebook order."""
    ssp = sec.section_space
    if ssp is None:
        # no section node – fall back to every page manifest we can find
        for sp in sec.spaces:
            if sp.root is not None and sp.root.jcid in (J["PageManifest"], J["Page"]):
                yield sp, sp.metadata_root
        return
    for series in ssp.resolve(ssp.root.get(P["ElementChildNodes"])):
        if series.jcid != J["PageSeries"]:
            continue
        osids = series.get(P["ChildGraphSpaceElementNodes"]) or []
        metas = ssp.resolve(series.get(P["MetaDataObjectsAboveGraphSpace"]))
        for i, osid in enumerate(osids):
            sp = sec.space_for(osid)
            meta = metas[i] if i < len(metas) else None
            if meta is not None and meta.get(P["IsDeletedGraphSpaceContent"]) is not None:
                continue
            if sp is None:
                continue
            # the page's own object space carries the authoritative metadata;
            # the section-level list can be stale / mis-ordered after page moves
            yield sp, (sp.metadata_root or meta)


def version_spaces(sec: O.Section, current: set):
    """Older versions of pages, reachable via VersionHistoryContent → VersionProxy."""
    for sp in sec.spaces:
        if sp.root is None or sp.root.jcid != J["VersionHistoryContent"]:
            continue
        for proxy in sp.resolve(sp.root.get(P["ElementChildNodes"])):
            for osid in proxy.get(P["VersionProxyRefs"]) or []:
                vsp = sec.space_for(osid)
                if vsp is not None and id(vsp) not in current:
                    yield vsp, proxy


def frontmatter(p: PageOut, section: str, notebook: str, extra: dict = None) -> str:
    fm = {"title": p.title, "notebook": notebook, "section": section, "level": p.level,
          "created": iso(p.created), "modified": iso(p.modified)}
    fm.update(extra or {})
    lines = ["---"]
    for k, v in fm.items():
        if v not in ("", None):
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def node_to_json(sp: O.ObjectSpace, node: O.Node, seen: set, depth=0):
    if node.oid in seen or depth > 40:
        return {"ref": f"{node.oid[0]}:{node.oid[1]}"}
    seen.add(node.oid)
    d = {"jcid": node.jcid_name, "oid": f"{node.oid[0]}:{node.oid[1]}"}
    for p in node.props:
        v = p.value
        if p.ptype in (O.PT_OID, O.PT_OIDS):
            items = v if isinstance(v, list) else [v]
            d[p.name] = [node_to_json(sp, sp.node(o), seen, depth + 1) if sp.node(o) else None
                         for o in items]
        elif isinstance(v, bytes):
            if p.name in ("RichEditTextUnicode", "CachedTitleString", "ParagraphStyleId", "Font",
                          "NumberListFormat", "WzHyperlinkUrl", "ImageFilename", "EmbeddedFileName",
                          "NoteTagLabel", "CachedTitleStringFromPage", "ImageAltText", "Author"):
                d[p.name] = utf16(v)
            else:
                d[p.name] = base64.b64encode(v).decode() if len(v) < 4096 else f"<{len(v)} bytes>"
        elif isinstance(v, tuple):
            d[p.name] = str(v)
        elif isinstance(v, list) and v and isinstance(v[0], O.PropertySet):
            d[p.name] = [{q.name: str(q.value) for q in ps} for ps in v]
        elif isinstance(v, O.PropertySet):
            d[p.name] = {q.name: str(q.value) for q in v}
        else:
            d[p.name] = v
    if node.file_data:
        d["file_data_bytes"] = len(node.file_data)
    return d


def export_section(one_path: str, out_root: str, notebook: str, opts, group: str = "") -> dict:
    section_name = os.path.splitext(os.path.basename(one_path))[0]
    with open(one_path, "rb") as f:
        data = f.read()
    sec = O.Section(data)
    sec_dir = os.path.join(out_root, safe_name(notebook),
                           *[safe_name(g) for g in group.split(os.sep) if g], safe_name(section_name))
    os.makedirs(sec_dir, exist_ok=True)
    renderer = MarkdownRenderer(sec, os.path.join(sec_dir, "assets"))

    pages: List[PageOut] = []
    current_ids = set()
    for sp, meta in page_spaces_in_order(sec):
        current_ids.add(id(sp))
        pages.append(renderer.page(sp, meta))

    index = ["# " + section_name, "", f"Notebook: **{notebook}**" + (f" / {group}" if group else "") + "  ", f"Source: `{os.path.basename(one_path)}`", "",
             "| # | Page | Level | Created | Modified | Paragraphs | Chars |", "|---|---|---|---|---|---|---|"]
    stats = {"section": section_name, "notebook": notebook, "group": group, "pages": 0, "versions": 0,
             "chars": 0, "paragraphs": 0, "warnings": [], "unknown_nodes": {}}
    for i, p in enumerate(pages, 1):
        fname = f"{i:02d} - {safe_name(p.title)}.md"
        with open(os.path.join(sec_dir, fname), "w", encoding="utf-8") as f:
            f.write(frontmatter(p, section_name, notebook))
            f.write(f"# {p.title}\n\n")
            f.write(p.body)
        indent = "&nbsp;&nbsp;&nbsp;" * (p.level - 1)
        index.append(f"| {i} | {indent}[{p.title}]({md_path(fname)}) | {p.level} | "
                     f"{iso(p.created)} | {iso(p.modified)} | {p.paragraphs} | {p.text_chars} |")
        stats["pages"] += 1
        stats["chars"] += p.text_chars
        stats["paragraphs"] += p.paragraphs
        stats["warnings"] += [f"{p.title}: {w}" for w in p.warnings]
        if opts.json:
            jdir = os.path.join(sec_dir, "_json")
            os.makedirs(jdir, exist_ok=True)
            with open(os.path.join(jdir, f"{i:02d} - {safe_name(p.title)}.json"), "w", encoding="utf-8") as f:
                json.dump(node_to_json(p.space, p.space.root, set()), f, indent=1,
                          ensure_ascii=False, default=str)

    if opts.versions:
        vdir = os.path.join(sec_dir, "_versions")
        n = 0
        for vsp, proxy in version_spaces(sec, current_ids):
            n += 1
            vp = renderer.page(vsp, vsp.metadata_root)
            when = filetime(proxy.get(P["LastModifiedTimeStamp"])) or time32(proxy.get(P["LastModifiedTime"]))
            os.makedirs(vdir, exist_ok=True)
            fname = f"{safe_name(vp.title)} - version {n} - {iso(when)[:19].replace(':', '-') or 'unknown'}.md"
            with open(os.path.join(vdir, fname), "w", encoding="utf-8") as f:
                f.write(frontmatter(vp, section_name, notebook, {"version_of": vp.title, "version_saved": iso(when)}))
                f.write(f"# {vp.title} (older version)\n\n")
                f.write(vp.body)
        stats["versions"] = n
        if n:
            index += ["", f"Older page versions: {n} (see `_versions/`)."]

    if renderer.unknown_jcids:
        stats["unknown_nodes"] = dict(renderer.unknown_jcids)
        index += ["", "Unhandled node types (content may be missing): " +
                  ", ".join(f"{k}×{v}" for k, v in renderer.unknown_jcids.items())]
    with open(os.path.join(sec_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(index) + "\n")
    return stats


ALT_PACKAGING_SIGNATURE = bytes.fromhex("e4525c7b8cd8a74daeb15378d02996d3")   # guidFileType of a .one
TOC_CHILDREN, TOC_FILENAME, TOC_INDEX, TOC_COLOR, TOC_DISPLAY = 0x24001CF6, 0x1C001D6B, 0x14001CB9, 0x14001CBE, 0x1C001D95


def read_toc(toc_path: str) -> List[dict]:
    """Section order (and colors) from a notebook's `Open Notebook.onetoc2`.

    OneDrive serves the TOC as a classic-format stub whose payload is an
    embedded alternative-packaging store, so we locate that payload and parse
    it with the normal reader.  Returns [{name, index, color}] in display order.
    """
    try:
        with open(toc_path, "rb") as f:
            data = f.read()
        start = data.find(ALT_PACKAGING_SIGNATURE)
        if start < 0:
            return []
        sec = O.Section(data[start:])
    except Exception:
        return []
    entries = []
    for sp in sec.spaces:
        root = sp.root
        if root is None:
            continue
        kids = sp.resolve(root.get(TOC_CHILDREN)) or [n for n in sp.nodes.values() if n.get(TOC_FILENAME)]
        for c in kids:
            name = utf16(c.get(TOC_FILENAME))
            if not name:
                continue
            entries.append({"name": name, "index": c.get(TOC_INDEX) or 0,
                            "color": c.get(TOC_COLOR), "display": utf16(c.get(TOC_DISPLAY))})
    # de-duplicate (older TOC revisions can repeat an entry) and order by index
    seen, ordered = set(), []
    for e in sorted(entries, key=lambda e: e["index"]):
        if e["name"].lower() not in seen:
            seen.add(e["name"].lower())
            ordered.append(e)
    return ordered


def find_sections(paths: List[str]):
    """Yield (path, notebook name, section-group subpath) for every .one file."""
    for p in paths:
        if os.path.isdir(p):
            notebook = os.path.basename(os.path.abspath(p).rstrip("/")) or "Notebook"
            for root, dirs, files in os.walk(p):
                group = os.path.relpath(root, p)
                group = "" if group == "." else group
                toc = read_toc(os.path.join(root, "Open Notebook.onetoc2"))
                order = {e["name"].lower(): i for i, e in enumerate(toc)}
                rank = lambda n: (order.get(n.lower(), len(order)), n.lower())
                dirs.sort(key=rank)
                for fn in sorted(files, key=rank):
                    if fn.lower().endswith(".one"):
                        yield os.path.join(root, fn), notebook, group
        elif p.lower().endswith(".one"):
            yield p, os.path.basename(os.path.dirname(os.path.abspath(p))) or "Notebook", ""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m onenote_to_markdown", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("inputs", nargs="+", help="notebook folder(s) or .one file(s)")
    ap.add_argument("-o", "--output", default="onenote-md", help="output directory")
    ap.add_argument("--versions", action="store_true", help="also export older page versions")
    ap.add_argument("--json", action="store_true", help="also dump raw page structure as JSON")
    ap.add_argument("--notebook", help="override the notebook name used for the output folder")
    from .tidy.cli import add_exporter_arguments, export_and_tidy, wants_tidy
    add_exporter_arguments(ap)
    opts = ap.parse_args(argv)
    if wants_tidy(opts):
        return export_and_tidy(opts, lambda out_root: export_all(opts, out_root))
    return export_all(opts, opts.output)


def export_all(opts, out_root: str) -> int:
    total = []
    for one_path, notebook, group in find_sections(opts.inputs):
        nb = opts.notebook or notebook
        try:
            st = export_section(one_path, out_root, nb, opts, group)
        except Exception as e:  # keep going with other sections
            print(f"ERROR {one_path}: {e}", file=sys.stderr)
            raise
        total.append(st)
        print(f"{one_path}: {st['pages']} pages, {st['paragraphs']} paragraphs, "
              f"{st['chars']} chars, {st['versions']} old versions"
              + (f", unknown nodes {st['unknown_nodes']}" if st['unknown_nodes'] else "")
              + (f", {len(st['warnings'])} warnings" if st['warnings'] else ""))
        for w in st["warnings"]:
            print("   warning:", w)
    if not total:
        print("no .one files found", file=sys.stderr)
        return 1
    write_notebook_index(out_root, total)
    return 0


def write_notebook_index(out_root: str, stats: List[dict]):
    by_nb: Dict[str, List[dict]] = {}
    for st in stats:
        by_nb.setdefault(st["notebook"], []).append(st)
    for nb, items in by_nb.items():
        lines = [f"# {nb}", "", "| Section | Pages | Old versions | Paragraphs | Chars |", "|---|---|---|---|---|"]
        for st in items:
            label = (st["group"] + " / " if st["group"] else "") + st["section"]
            link = "/".join(safe_name(x) for x in ([st["group"]] if st["group"] else []) + [st["section"]]) + "/README.md"
            lines.append(f"| [{label}]({md_path(link)}) | {st['pages']} | {st['versions']} | "
                         f"{st['paragraphs']} | {st['chars']} |")
        with open(os.path.join(out_root, safe_name(nb), "README.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
