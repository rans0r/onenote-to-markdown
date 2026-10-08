"""Finding, resolving and re-encoding the local links inside a Markdown page."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterator
from urllib.parse import unquote

from ..cli import md_path

LINK_OPEN = re.compile(r"\]\(")
# ![alt](  – alt text may wrap onto several lines and contain one level of [brackets]
IMAGE_OPEN = re.compile(r"!\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
TITLE = re.compile(r'^(.*?)\s+"[^"]*"\s*$', re.S)


@dataclass
class Link:
    start: int          # span of the destination in the text (including <> if present)
    end: int
    dest: str           # the destination as written, without <> or a "title"
    is_image: bool
    alt: str = ""


def iter_links(text: str) -> Iterator[Link]:
    """Inline link and image destinations: `[text](dest)`, `![alt](dest "title")`, `[x](<dest>)`.

    The exporter writes attachment paths with raw spaces and parentheses
    (`assets/Flight Receipt (NYC - SFO).pdf`), which strict CommonMark rejects, so
    the destination runs to the first unbalanced `)` on the same line rather
    than stopping at whitespace.
    """
    images: Dict[int, str] = {m.end(): m.group(1) for m in IMAGE_OPEN.finditer(text)}
    n = len(text)
    for m in LINK_OPEN.finditer(text):
        j = m.end()
        while j < n and text[j] in " \t":
            j += 1
        alt = images.get(m.end())
        is_image = alt is not None
        if j < n and text[j] == "<":
            k = text.find(">", j + 1)
            if k < 0 or "\n" in text[j:k]:
                continue
            yield Link(j, k + 1, text[j + 1:k], is_image, alt or "")
            continue
        depth, k = 0, j
        while k < n:
            c = text[k]
            if c == "\n":
                break
            if c == "\\" and k + 1 < n:
                k += 2
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                if depth == 0:
                    break
                depth -= 1
            k += 1
        if k >= n or text[k] != ")" or k == j:
            continue
        raw = text[j:k]
        t = TITLE.match(raw)
        dest = t.group(1) if t else raw.rstrip()
        if dest:
            yield Link(j, j + len(dest), dest, is_image, alt or "")


def is_external(dest: str) -> bool:
    """URLs, mail links, in-page anchors, absolute and Windows network paths."""
    return bool(SCHEME.match(dest)) or dest.startswith(("#", "/", "\\"))


def decode(dest: str) -> str:
    return unquote(dest)


encode = md_path
