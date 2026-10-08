"""In-memory model of an export: pages, other files, and where each one ends up.

Every step changes this model; nothing touches the disk until `write()`. That
keeps the source export untouched, makes a dry run exact, and lets all link
rewriting happen once, at the end, from each file's original location to its
final one.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import unicodedata
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from . import links as L

NUMBERED = re.compile(r"^(\d+) - (.+)\.md$", re.I)
FM_KEY = re.compile(r"^([A-Za-z_][\w-]*):(?:[ \t]+(.*))?$")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}


def key(path: str) -> str:
    """Comparison key for a path.

    macOS and Windows treat names that differ only in case – or in Unicode
    normalization, which macOS can change on its own – as the same file.
    """
    return unicodedata.normalize("NFC", path).casefold()


class FrontMatter:
    """The `---` block. The exporter writes one `key: <JSON value>` per line;
    anything else (hand-edited YAML lists, comments) is kept verbatim."""

    def __init__(self, lines: List[str]):
        self.entries: List[list] = []          # [key | None, value, raw lines]
        for line in lines:
            m = FM_KEY.match(line)
            if m:
                raw = (m.group(2) or "").strip()
                try:
                    value = json.loads(raw) if raw else ""
                except ValueError:
                    value = raw
                self.entries.append([m.group(1), value, [line]])
            elif self.entries:
                self.entries[-1][2].append(line)
            else:
                self.entries.append([None, None, [line]])

    def get(self, name: str, default=None):
        for k, v, _ in self.entries:
            if k == name:
                return v
        return default

    def set(self, name: str, value) -> None:
        line = f"{name}: {json.dumps(value, ensure_ascii=False)}"
        for e in self.entries:
            if e[0] == name:
                e[1], e[2] = value, [line]
                return
        self.entries.append([name, value, [line]])

    def drop(self, name: str) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e[0] != name]
        return len(self.entries) != before

    def lines(self) -> List[str]:
        return [line for e in self.entries for line in e[2]]


class Item:
    is_note = False

    def __init__(self, path: str, src: str):
        self.path = path            # current location, relative, "/"-separated
        self.orig = path            # where it was in the export
        self.src = src              # absolute path of the source file
        self.deleted = False
        self.merged_into: Optional[Item] = None

    @property
    def name(self) -> str:
        return posixpath.basename(self.path)

    @property
    def dir(self) -> str:
        return posixpath.dirname(self.path)

    @property
    def parts(self) -> List[str]:
        return self.path.split("/")

    def __repr__(self):
        return f"<{type(self).__name__} {self.path}>"


class Asset(Item):
    """Any file that isn't a page: images, attachments, system files."""

    _digest: Optional[str] = None

    def digest(self) -> str:
        if self._digest is None:
            h = hashlib.sha1()
            with open(self.src, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            self._digest = h.hexdigest()
        return self._digest


class Note(Item):
    is_note = True

    def __init__(self, path: str, src: str, text: str):
        super().__init__(path, src)
        self.original_text = text
        lines = text.split("\n")
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None) \
            if lines and lines[0].strip() == "---" else None
        if end is None:
            self.fm, self.has_fm, self.body = FrontMatter([]), False, text
        else:
            self.fm, self.has_fm = FrontMatter(lines[1:end]), True
            self.body = "\n".join(lines[end + 1:])
        self.orig_title = self.title
        self.parent: Optional[Note] = None
        self.children: List[Note] = []
        self.folder_only = False    # a blank page whose sub-pages keep its folder

    @property
    def title(self) -> str:
        t = self.fm.get("title")
        if isinstance(t, str) and t.strip():
            return t.strip()
        m = NUMBERED.match(self.name)
        return m.group(2) if m else posixpath.splitext(self.name)[0]

    @property
    def level(self) -> int:
        try:
            return max(1, int(self.fm.get("level") or 1))
        except (TypeError, ValueError):
            return 1

    @property
    def number(self) -> Optional[str]:
        m = NUMBERED.match(self.name)
        return m.group(1) if m else None

    def text(self) -> str:
        if not self.has_fm:
            return self.body
        fm = self.fm.lines()
        return "---\n" + "".join(line + "\n" for line in fm) + "---\n" + self.body


class Vault:
    def __init__(self, root: str):
        self.root = root
        self.items: List[Item] = []
        self.actions: List[dict] = []
        self.unreadable: List[str] = []
        self._files: Dict[str, Item] = {}               # key(path) -> live item
        self._dirs: Dict[str, int] = defaultdict(int)   # key(dir) -> live items below it
        self._orig: Dict[str, Item] = {}                # key(original path) -> item

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, root: str) -> "Vault":
        v = cls(root)
        for dirpath, dirs, files in os.walk(root):
            dirs.sort()
            for fn in sorted(files):
                src = os.path.join(dirpath, fn)
                rel = os.path.relpath(src, root).replace(os.sep, "/")
                item: Item
                if fn.lower().endswith(".md"):
                    try:
                        with open(src, encoding="utf-8") as f:
                            item = Note(rel, src, f.read())
                    except (UnicodeDecodeError, OSError):
                        v.unreadable.append(rel)
                        item = Asset(rel, src)
                else:
                    item = Asset(rel, src)
                v.items.append(item)
                v._index(item)
                v._orig[key(rel)] = item
        v._build_hierarchy()
        return v

    def _build_hierarchy(self):
        """Parent/child pages from each section's `level` values, in page order.

        Recorded before any step deletes or moves pages, so a sub-page always
        knows its real parent – even if that parent later turns out blank.
        """
        by_dir: Dict[str, List[Note]] = defaultdict(list)
        for n in self.notes():
            if n.number is not None and "_versions" not in n.parts[:-1]:
                by_dir[n.dir].append(n)
        for pages in by_dir.values():
            pages.sort(key=lambda n: int(n.number))
            stack: List[Note] = []
            for n in pages:
                while stack and stack[-1].level >= n.level:
                    stack.pop()
                if stack:
                    n.parent = stack[-1]
                    stack[-1].children.append(n)
                stack.append(n)

    # --------------------------------------------------------------- queries
    def alive(self) -> List[Item]:
        return [i for i in self.items if not i.deleted]

    def notes(self) -> List[Note]:
        return [i for i in self.items if i.is_note and not i.deleted]

    def assets(self) -> List[Asset]:
        return [i for i in self.items if not i.is_note and not i.deleted]

    def file_exists(self, path: str) -> bool:
        return key(path) in self._files

    def dir_exists(self, path: str) -> bool:
        return self._dirs.get(key(path), 0) > 0

    def resolve(self, note: Note, dest: str) -> Tuple[Optional[Item], bool]:
        """The item a link points at, and whether the target lies inside the export.

        Links are resolved from the page's *original* folder: the text of a link
        never changes until `finalize_links()`, so the original location is the
        one it was written against.
        """
        if L.is_external(dest):
            return None, False
        target = posixpath.normpath(posixpath.join(posixpath.dirname(note.orig), L.decode(dest)))
        if target == ".." or target.startswith("../"):
            return None, False
        item = self._orig.get(key(target))
        while item is not None and item.merged_into is not None:
            item = item.merged_into
        return item, True

    def backlinks(self, include=lambda n: True) -> Dict[Item, List[Tuple[Note, L.Link]]]:
        refs: Dict[Item, List[Tuple[Note, L.Link]]] = defaultdict(list)
        for n in self.notes():
            if not include(n):
                continue
            for link in L.iter_links(n.body):
                item, _ = self.resolve(n, link.dest)
                if item is not None and not item.deleted:
                    refs[item].append((n, link))
        return refs

    # --------------------------------------------------------------- changes
    def log(self, step: str, action: str, item_or_path, detail: str = "", **extra):
        path = item_or_path.orig if isinstance(item_or_path, Item) else item_or_path
        entry = {"step": step, "action": action, "path": path}
        if detail:
            entry["detail"] = detail
        entry.update(extra)
        self.actions.append(entry)

    def delete(self, item: Item, step: str, reason: str):
        if item.deleted:
            return
        self._unindex(item)
        item.deleted = True
        self.log(step, "delete", item, reason)

    def place(self, item: Item, new_path: str, step: str, action: str = "move") -> str:
        """Move or rename an item, avoiding clashes. Identical duplicate files merge."""
        new_path = posixpath.normpath(new_path)
        if new_path == item.path:
            return item.path
        self._unindex(item)
        final = new_path
        stem, ext = posixpath.splitext(posixpath.basename(new_path))
        if stem.upper() in WINDOWS_RESERVED:
            stem += "_"
            final = posixpath.join(posixpath.dirname(new_path), stem + ext)
        base = final
        n = 1
        while True:
            other = self._files.get(key(final))
            if other is not None and not item.is_note and not other.is_note and other.digest() == item.digest():
                item.deleted = True
                item.merged_into = other
                self.log(step, "merge", item, f"identical to {other.path}")
                return other.path
            if other is None and not self.dir_exists(final):
                break
            n += 1
            final = posixpath.join(posixpath.dirname(base), f"{stem} ({n}){ext}")
        old = item.path
        item.path = final
        self._index(item)
        self.log(step, action, item, "", to=final, **({"from": old} if old != item.orig else {}))
        return final

    def unique_dir(self, path: str) -> str:
        """A folder path that no existing file or folder already uses."""
        final, n = path, 1
        while self.file_exists(final) or self.dir_exists(final):
            n += 1
            final = f"{path} ({n})"
        return final

    def _index(self, item: Item):
        self._files[key(item.path)] = item
        d = item.dir
        while d:
            self._dirs[key(d)] += 1
            d = posixpath.dirname(d)

    def _unindex(self, item: Item):
        if self._files.get(key(item.path)) is item:
            del self._files[key(item.path)]
        d = item.dir
        while d:
            self._dirs[key(d)] -= 1
            d = posixpath.dirname(d)

    # ---------------------------------------------------------------- output
    def finalize_links(self) -> List[Tuple[Note, str, str]]:
        """Point every local link at its target's final location.

        Returns links that can't be satisfied: (page, link, "missing" | "removed").
        """
        broken: List[Tuple[Note, str, str]] = []
        for note in self.notes():
            body, out, pos = note.body, [], 0
            here = posixpath.dirname(note.path) or "."
            for link in L.iter_links(body):
                item, inside = self.resolve(note, link.dest)
                if not inside:
                    continue
                if item is None or item.deleted:
                    broken.append((note, link.dest, "missing" if item is None else "removed"))
                    continue
                new = L.encode(posixpath.relpath(item.path, here))
                if new != body[link.start:link.end]:
                    out += [body[pos:link.start], new]
                    pos = link.end
            if out:
                note.body = "".join(out) + body[pos:]
        return broken

    def write(self, out_root: str) -> int:
        written = 0
        for item in self.alive():
            dest = os.path.join(out_root, *item.parts)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if item.is_note and item.text() != item.original_text:
                with open(dest, "w", encoding="utf-8", newline="\n") as f:
                    f.write(item.text())
            else:
                shutil.copy2(item.src, dest)
            written += 1
        return written

    def counts(self) -> Dict[str, int]:
        c: Dict[str, int] = defaultdict(int)
        for a in self.actions:
            c[a["step"]] += 1
        return dict(c)


def common_dir(paths: Iterable[str]) -> str:
    paths = list(paths)
    if not paths:
        return ""
    try:
        return posixpath.commonpath(paths)
    except ValueError:
        return ""
