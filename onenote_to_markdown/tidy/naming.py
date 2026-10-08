"""Spotting poor page titles and attachment names, and rule-based replacements.

OneNote titles pages it imports (Evernote notes especially) with the first
line of text, cut off with "...", and the exporter caps file names at 80
characters. Pages nobody titled come out as "Untitled page". The rules here
catch those; `llm.py` can do the naming instead when Claude is enabled.
"""
from __future__ import annotations

import posixpath
import re
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

UNTITLED = re.compile(r"^\(?\s*untitled(?:\s+(?:page|note))?\s*\)?$", re.I)
CODEISH = re.compile(r"https?:|www\.|\S@\S|[{}=;]|\$[A-Za-z_{(]|^\.")
TIMESTAMP = re.compile(r"\b(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+[A-Z][a-z]+\s+\d{1,2},?\s+\d{4}"
                       r"(?:\s+\d{1,2}[:\s]\d{2}\s*[AP]M)?", re.I)
CLOCK = re.compile(r"\b\d{1,2}[:\s]\d{2}[\s ]*[AP]M\b", re.I)
URL = re.compile(r"(?:https?://|www\.)[^\s)\]>]+", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CREDENTIAL = re.compile(r"\b(?:user\s*name|username|login|log-in|password|passwd|pwd|pass|pin|"
                        r"serial(?:\s+number)?|api\s*key|secret)\b", re.I)
CLAUSE_END = re.compile(r"\.{3}|…|[.!?;](?:\s|$)|\s[-–—]{1,2}\s|\(")
LABEL = re.compile(r"^(?:name|title|subject|topic|re|fw|fwd)$", re.I)
STRAY = '?"“”'           # curly quotes some imports turned into "?"
MAX_TITLE = 60
SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "of", "on", "or",
         "the", "to", "vs", "via", "with"}
DANGLING = SMALL | {"is", "are", "was", "be", "this", "that", "it", "we", "you", "i", "our", "your", "their"}
SITE_NAMES = {"youtube.com": "YouTube", "youtu.be": "YouTube", "vimeo.com": "Vimeo", "github.com": "GitHub",
              "stackoverflow.com": "Stack Overflow", "huffingtonpost.com": "HuffPost",
              "techcrunch.com": "TechCrunch", "mashable.com": "Mashable", "wikipedia.org": "Wikipedia"}
VIDEO_SITES = {"YouTube", "Vimeo"}
GENERIC_ASSET = re.compile(
    r"^(?:image|img|picture|pic|photo|untitled(?:[ _-]?(?:picture|image|attachment))?|screen[ _-]?shot|"
    r"asset|attachment|file|scan|clip[ _-]?image|missing-image)(?:[ _-]?\d+)?$"
    r"|^(?:galleryContent|lens_processed_|IMG[_-]|DSC[_-]?|PXL_|Screenshot_)[\w-]*$"
    r"|^[0-9a-f]{16,}$|^\d+$", re.I)
GENERIC_ALT = {"image", "picture", "photo", "attachment", "file", "untitled", "untitledpicture"}
TABLE_RULE = re.compile(r"^\|[\s:|-]*\|$")


def norm(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", s.lower())


def clean_inline(line: str) -> str:
    """Markdown line -> plain text."""
    s = line.replace(" ", " ")
    s = re.sub(r"^\s*#+\s*", "", s)
    s = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+(?:\[[ xX]\]\s+)?", "", s)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"<((?:https?|mailto):[^>]+)>", r"\1", s)
    s = re.sub(r"(\*\*|__|~~|`)", "", s)
    s = re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", s)
    if s.strip().startswith("|"):
        s = " ".join(c.strip() for c in s.strip().strip("|").split("|") if c.strip())
    return re.sub(r"\s+", " ", s).strip()


def content_lines(content: str) -> List[Tuple[str, str]]:
    """(raw, plain) for each line with text, skipping OneNote tag tables."""
    out = []
    for raw in content.split("\n"):
        s = raw.strip()
        if not s or TABLE_RULE.match(s) or s.startswith("| **Tags:**"):
            continue
        plain = clean_inline(raw)
        if plain:
            out.append((raw, plain))
    return out


def first_line(content: str) -> str:
    lines = content_lines(content)
    return lines[0][1] if lines else ""


def title_problem(title: str, first: str) -> Optional[str]:
    """Why a page title needs replacing, or None if it's fine."""
    t = title.strip()
    if UNTITLED.match(t):
        return "untitled"
    if t.endswith(("...", "…")):
        return "cut-off first line"
    if t[:1] in STRAY:
        return "stray quote marks"
    if CODEISH.search(t):
        return "URL, email or code"
    if len(t) > 80:
        return "too long"
    nt, nf = norm(t), norm(first)
    if len(t) >= 30 and len(nf) > len(nt) and nf.startswith(nt):
        return "cut-off first line"
    return None


def smart_case(text: str) -> str:
    words = text.split()
    out = []
    for i, w in enumerate(words):
        if any(c.isupper() for c in w) or (i and w.lower() in SMALL):
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def url_title(url: str) -> Optional[str]:
    """`http://techcrunch.com/2019/05/02/remote-work-tools/?utm=…` -> "TechCrunch - Remote Work Tools"."""
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "http://" + url
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    labels = host.split(".")
    registrable = ".".join(labels[-2:])
    site = SITE_NAMES.get(host) or SITE_NAMES.get(registrable) or \
        (labels[0].capitalize() if len(labels) == 2 else host)
    words: List[str] = []
    for seg in reversed([s for s in parts.path.split("/") if s]):
        seg = re.sub(r"\.(html?|php|aspx?|jsp|cfm|pdf|docx?|xlsx?|pptx?|txt)$", "", seg, flags=re.I)
        tokens = [t for t in re.split(r"[-_+.\s]+", seg) if re.search(r"[A-Za-z]{2}", t) and not re.search(r"\d", t)]
        if tokens and seg.lower() not in ("index", "main", "default", "watch", "home"):
            words = tokens[:6]
            break
    if words:
        return f"{site} - {smart_case(' '.join(w.lower() if w.isupper() else w for w in words))}"
    return f"{site} Video" if site in VIDEO_SITES else f"{site} Link"


def shorten(text: str) -> str:
    t = TIMESTAMP.sub(" ", text)
    t = CLOCK.sub(" ", t)
    t = EMAIL.sub(" ", t)
    if re.sub(URL, "", t).strip():
        t = URL.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" :,-")
    m = CREDENTIAL.search(t)
    if m:
        before = t[:m.start()].strip(" :,-")
        return f"{before} Login" if before else "Login Details"
    m = CLAUSE_END.search(t)
    if m:
        head = t[:m.start()].strip(" :,-")
        if len(head.split()) >= 2:
            t = head
    if ": " in t:
        head, tail = t.split(": ", 1)
        if LABEL.match(head) and tail.strip():
            t = tail
        elif len(head.split()) >= 2:
            t = head
        elif tail.strip():
            t = f"{head} - {' '.join(tail.split()[:4])}"
    if len(t) > MAX_TITLE:
        comma = t.find(", ")
        if 0 < comma and len(t[:comma].split()) >= 2:
            t = t[:comma]
    words: List[str] = []
    for w in t.split():
        if len(" ".join(words + [w])) > MAX_TITLE:
            break
        words.append(w)
    while words and words[-1].lower().strip(",.;:-") in DANGLING:
        words.pop()
    return " ".join(words).strip(" ,.;:-")


def finish(candidate: Optional[str], old: str) -> Optional[str]:
    if not candidate:
        return None
    c = re.sub(r"\s+", " ", candidate).strip(" ,.;:-")
    if c[:1] in STRAY:
        c = c.strip(STRAY + " ,.;:-")
    o = old.strip()
    if len(c) < 3 or c == o:
        return None
    if norm(c) == norm(o) and o[:1] not in STRAY and not o.endswith(("...", "…")):
        return None                     # only case or punctuation would change
    return c[:1].upper() + c[1:]


def heuristic_title(title: str, content: str, images: List[Tuple[str, str]]) -> Optional[str]:
    """Best rule-based title for a page, or None to keep the current one.

    `images` are (alt text, file name) for the images on the page.
    """
    lines = content_lines(content)
    if not lines:
        for alt, fname in images:
            name = name_from_alt(alt, fname) or (None if is_generic_asset(fname) else
                                                 posixpath.splitext(fname)[0])
            if name:
                return finish(name, title)
        return None
    raw, plain = lines[0]
    bold = re.match(r"^\s*(?:[-*+]\s+)?\*\*([^*]{2,80}?)\*\*", raw)
    if bold and 1 <= len(bold.group(1).split()) <= 8:
        return finish(bold.group(1), title)
    url = URL.search(plain) if (URL.match(plain) or CODEISH.search(title)) else None
    if url and (URL.match(plain) or not re.sub(URL, "", plain).strip()):
        return finish(url_title(url.group(0).rstrip(".,;")), title)
    return finish(shorten(plain), title)


def is_generic_asset(filename: str) -> bool:
    stem, ext = posixpath.splitext(filename)
    inner, inner_ext = posixpath.splitext(stem)
    if inner_ext and inner_ext.lower() == ext.lower():
        stem = inner
    return bool(GENERIC_ASSET.match(stem.strip()))


def looks_like_ocr(text: str) -> bool:
    """Text read off an image (OneNote stores it as alt text): several lines, or mostly SHOUTING fragments."""
    if "\n" in text.strip():
        return True
    words = [w for w in re.findall(r"[A-Za-z]{2,}", text)]
    return len(words) > 3 and sum(w.isupper() for w in words) / len(words) > 0.4


def name_from_alt(alt: str, filename: str) -> Optional[str]:
    """A file name from OneNote's alt text, when the alt text says something."""
    a = re.sub(r"^\s*video web content titled:\s*", "", alt or "", flags=re.I)
    a = re.sub(r"[^\w\s'&,.()+-]", " ", a)
    a = re.sub(r"\s+", " ", a).strip(" .,-")
    if len(a) < 3 or norm(a) in GENERIC_ALT or norm(a) == norm(posixpath.splitext(filename)[0]) \
            or is_generic_asset(a + ".x") or looks_like_ocr(alt):
        return None
    return " ".join(a.split()[:10])
