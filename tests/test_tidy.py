import csv
import datetime as dt
import hashlib
import os
import re
import zipfile
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from onenote_to_markdown import cli
from onenote_to_markdown.tidy import Options, run
from onenote_to_markdown.tidy import cli as tidy_cli
from onenote_to_markdown.tidy import naming as N
from onenote_to_markdown.tidy import scan as S
from onenote_to_markdown.tidy.links import iter_links
from onenote_to_markdown.tidy.steps import header_end, old_header_end
from onenote_to_markdown.tidy.vault import Vault

WHEN = dt.datetime(2024, 3, 5, 14, 30)
DATE = "*Tuesday, March 5, 2024 2:30 PM*\n\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def page(title, body="Some text.\n", level=1, section="Work", notebook="Notes", dated=True):
    """A page as the exporter writes it."""
    p = cli.PageOut(title=title, level=level, created=WHEN, modified=WHEN, body=body, space=None)
    return cli.frontmatter(p, section, notebook) + f"# {title}\n\n" + (DATE if dated else "") + body


def build(root, files):
    for rel, data in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def tree(root):
    out = {}
    for d, _, fs in os.walk(root):
        for f in fs:
            path = os.path.join(d, f)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root).replace(os.sep, "/")] = fh.read()
    return out


def broken_links(root):
    bad = []
    for rel, data in tree(root).items():
        if rel.endswith(".md"):
            for link in iter_links(data.decode("utf-8")):
                if re.match(r"^[a-z][a-z0-9+.-]*:|^#|^/", link.dest, re.I) or link.dest == "missing-image":
                    continue
                if not os.path.exists(os.path.join(root, os.path.dirname(rel), unquote(link.dest))):
                    bad.append((rel, link.dest))
    return bad


def tidy(src, out, **opts):
    return run(str(src), str(out), Options(**opts), str(out) + "-tidy-report", echo=lambda s: None)


def fm(text):
    return dict(re.findall(r"^(\w+): (.*)$", text.split("\n---\n", 1)[0], re.M))


@pytest.fixture
def export(tmp_path):
    src = tmp_path / "onenote-md"
    build(src, {
        "Notes/README.md": "# Notes\n\n| Section | Pages | Old versions | Paragraphs | Chars |\n|---|---|---|---|---|\n",
        "Notes/Work/README.md": "# Work\n\n| # | Page | Level | Created | Modified | Paragraphs | Chars |\n|---|---|---|\n",
        "Notes/Work/01 - Projects.md": page("Projects", "Receipt: 📎 [Flight Receipt (NYC - SFO).pdf]"
                                                       "(assets/Flight%20Receipt%20%28NYC%20-%20SFO%29.pdf)\n"),
        "Notes/Work/02 - Alpha.md": page("Alpha", "Mock-up:\n\n![image](assets/image-1.png)\n\nSee "
                                                  "[the specs](03%20-%20Specs.md).\n", level=2),
        "Notes/Work/03 - Specs.md": page("Specs", "Needs a login page.\n", level=3),
        "Notes/Work/04 - Untitled page.md": page("Untitled page", ""),
        "Notes/Work/05 - Groceries for the week and other things to remember when going.md": page(
            "Groceries for the week and other things to remember when going...",
            "Groceries for the week and other things to remember when going shopping. Milk, eggs.\n"),
        "Notes/Work/06 - Café “Plans”.md": page("Café “Plans”", "Unicode title.\n", dated=False),
        "Notes/Work/assets/image-1.png": PNG,
        "Notes/Work/assets/Flight Receipt (NYC - SFO).pdf": b"%PDF-1.4 flight",
        "Notes/Work/assets/orphan.bin": b"nobody links here",
        "Notes/Work/_versions/Alpha - version 1 - 2024-01-01.md": page("Alpha", "old"),
        "Notes/OneNote_RecycleBin/Deleted Pages/01 - Old.md": page("Old", "gone\n"),
        "Notes/.DS_Store": b"\x00\x00",
        "Ideas (Evernote import on 2016-06-30T21-49-30)/Pages 1-100/01 - Thought.md":
            page("Thought", "First.\n![chart](assets/chart.png)\n![](assets/same.png)\n", section="Pages 1-100"),
        "Ideas (Evernote import on 2016-06-30T21-49-30)/Pages 1-100/assets/chart.png": PNG + b"A",
        "Ideas (Evernote import on 2016-06-30T21-49-30)/Pages 1-100/assets/same.png": PNG + b"S",
        "Ideas (Evernote import on 2016-06-30T21-49-30)/Pages 101-200/01 - Later.md":
            page("Later", "Second.\n![chart](assets/chart.png)\n![](assets/same.png)\n", section="Pages 101-200"),
        "Ideas (Evernote import on 2016-06-30T21-49-30)/Pages 101-200/assets/chart.png": PNG + b"B",
        "Ideas (Evernote import on 2016-06-30T21-49-30)/Pages 101-200/assets/same.png": PNG + b"S",
    })
    return src


def test_typical_export_is_cleaned(export, tmp_path):
    out = tmp_path / "clean"
    summary = tidy(export, out)
    files = tree(out)
    assert set(files) == {
        "Notes/Work/Projects/Projects.md",
        "Notes/Work/Projects/Flight Receipt (NYC - SFO).pdf",
        "Notes/Work/Projects/Alpha/Alpha.md",
        "Notes/Work/Projects/Alpha/Alpha.png",
        "Notes/Work/Projects/Alpha/01 - Specs.md",
        "Notes/Work/05 - Groceries for the week and other things to remember when.md",
        "Notes/Work/06 - Café “Plans”.md",
        "Notes/Work/orphan.bin",
        "Ideas/001 - Thought.md",
        "Ideas/101 - Later.md",
        "Ideas/chart.png",
        "Ideas/chart (2).png",
        "Ideas/same.png",
    }
    alpha = files["Notes/Work/Projects/Alpha/Alpha.md"].decode()
    assert alpha.startswith('---\ntitle: "Alpha"\ncreated: "2024-03-05 14:30:00 UTC"\nmodified:')
    assert "\n---\n\nMock-up:\n\n![image](Alpha.png)\n\nSee [the specs](01%20-%20Specs.md).\n" in alpha
    assert "notebook:" not in alpha and "section:" not in alpha and "level:" not in alpha
    projects = files["Notes/Work/Projects/Projects.md"].decode()
    assert "(Flight%20Receipt%20%28NYC%20-%20SFO%29.pdf)" in projects
    groceries = files["Notes/Work/05 - Groceries for the week and other things to remember when.md"]
    assert fm(groceries.decode())["title"] == '"Groceries for the week and other things to remember when"'
    assert summary["attention"]["unlinked"] == ["Notes/Work/assets/orphan.bin"]
    assert broken_links(out) == []


def test_source_is_untouched_and_nothing_is_lost(export, tmp_path):
    before = tree(export)
    out = tmp_path / "clean"
    tidy(export, out)
    assert tree(export) == before
    kept = {hashlib.sha1(d).digest() for p, d in before.items()
            if not p.endswith((".md", ".DS_Store")) and "_versions" not in p and "RecycleBin" not in p}
    assert kept <= {hashlib.sha1(d).digest() for d in tree(out).values()}


def test_second_run_changes_nothing(export, tmp_path):
    tidy(export, tmp_path / "once")
    summary = tidy(tmp_path / "once", tmp_path / "twice")
    assert summary["vault"].actions == []
    assert tree(tmp_path / "once") == tree(tmp_path / "twice")


def test_blank_parent_keeps_its_folder(tmp_path):
    src = tmp_path / "src"
    build(src, {
        "N/S/01 - Layouts.md": page("Layouts", ""),
        "N/S/02 - Grid.md": page("Grid", "display: grid\n", level=2),
        "N/S/03 - Empty.md": page("Empty", "", level=2),
    })
    tidy(src, tmp_path / "out")
    assert set(tree(tmp_path / "out")) == {"N/S/Layouts/01 - Grid.md"}


def test_folder_note_styles(tmp_path):
    src = tmp_path / "src"
    build(src, {"N/S/01 - Parent.md": page("Parent", "p\n"), "N/S/02 - Kid.md": page("Kid", "k\n", level=2)})
    tidy(src, tmp_path / "zero", folder_note="zero", folder_numbers=True)
    assert set(tree(tmp_path / "zero")) == {"N/S/01 - Parent/00 - Parent.md", "N/S/01 - Parent/01 - Kid.md"}
    tidy(src, tmp_path / "index", folder_note="index")
    assert set(tree(tmp_path / "index")) == {"N/S/Parent/index.md", "N/S/Parent/01 - Kid.md"}


def test_shared_and_clashing_attachments(tmp_path):
    src = tmp_path / "src"
    build(src, {
        "N/A/01 - One.md": page("One", "![logo](assets/logo.png)\n![image](assets/image.png)\n![image](assets/image-1.png)\n"),
        "N/B/01 - Two.md": page("Two", "![logo](../A/assets/logo.png)\n![Photo](assets/photo.png)\n"),
        "N/B/assets/photo.png": PNG + b"photo",
        "N/A/assets/logo.png": PNG + b"logo",
        "N/A/assets/image.png": PNG + b"1",
        "N/A/assets/image-1.png": PNG + b"2",
    })
    out = tmp_path / "out"
    tidy(src, out)
    assert set(tree(out)) == {"N/A/01 - One.md", "N/A/One.png", "N/A/One 2.png", "N/logo.png",
                              "N/B/01 - Two.md", "N/B/Two.png"}
    assert broken_links(out) == []


def test_section_named_like_a_page_and_case_clash(tmp_path):
    src = tmp_path / "src"
    build(src, {
        "N/S/01 - Notes.md": page("Notes", "p\n"),
        "N/S/02 - Kid.md": page("Kid", "k\n", level=2),
        "N/S/notes/readme.txt": b"a folder that is already there",
    })
    tidy(src, tmp_path / "out")
    assert set(tree(tmp_path / "out")) == {"N/S/Notes (2)/Notes (2).md", "N/S/Notes (2)/01 - Kid.md",
                                           "N/S/notes/readme.txt"}


def test_renames_file_round_trip(export, tmp_path):
    tidy(export, tmp_path / "first")
    report = str(tmp_path / "first") + "-tidy-report/renames.csv"
    rows = list(csv.DictReader(open(report, encoding="utf-8")))
    assert [r["new_title"] for r in rows] == ["Groceries for the week and other things to remember when"]
    rows[0]["new_title"] = "Shopping List"
    with open(report, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    tidy(export, tmp_path / "second", renames=report)
    assert "Notes/Work/05 - Shopping List.md" in tree(tmp_path / "second")


def test_skip_and_only(export, tmp_path):
    summary = tidy(export, tmp_path / "out", only={"recycle-bin", "scaffolding"})
    assert summary["ctx"].ran == ["scaffolding", "recycle-bin"]
    assert "Notes/Work/01 - Projects.md" in tree(tmp_path / "out")
    with pytest.raises(tidy_cli.TidyError):
        tidy(export, tmp_path / "bad", skip={"nope"})


def test_claude_naming_is_used_when_enabled(export, tmp_path, monkeypatch):
    from onenote_to_markdown.tidy import llm

    class FakeNamer:
        def __init__(self, model):
            self.errors = []

        def page_title(self, title, folder, content, image=None):
            return "Weekly Groceries"

        def image_name(self, page_title, alt, image):
            return "Alpha Mock-up"

    monkeypatch.setattr(llm, "ClaudeNamer", FakeNamer)
    out = tmp_path / "out"
    tidy(export, out, llm=True)
    files = tree(out)
    assert "Notes/Work/05 - Weekly Groceries.md" in files
    assert "Notes/Work/Projects/Alpha/Alpha Mock-up.png" in files
    assert broken_links(out) == []


# ------------------------------------------------------------ headers
@pytest.mark.parametrize("body,rest", [
    ("# T\n\n" + DATE + "Text\n", "Text\n"),
    ("# T\n\n*Tuesday, March 5, 2024*\n\nText\n", "Text\n"),
    ("# T\n\nText\n", "Text\n"),
    ("# T\n\n*Not a date*\n", "*Not a date*\n"),
    ("# Other\n\nText\n", "# Other\n\nText\n"),
    ("# T\n\n" + DATE, ""),
])
def test_header_end(body, rest):
    assert body[header_end(body, {"T"}):] == rest


def test_old_onenote_header():
    body = "\n\u00a0Roadmap  \nMonday, June 04, 2007  \n9:15 AM  \n**What ships first?**\n"
    assert body[old_header_end(body, "Roadmap"):] == "**What ships first?**\n"
    assert old_header_end("Roadmap\nSome text\n", "Roadmap") == 0


# ------------------------------------------------------------- titles
@pytest.mark.parametrize("title,first,problem", [
    ("Untitled page", "", "untitled"),
    ("(Untitled)", "", "untitled"),
    ("Roadmap Monday, June 04, 2007 9:15 AM What ships first in the next rel...", "Roadmap", "cut-off first line"),
    ("http://www.example.org/", "", "URL, email or code"),
    ("?Simplicity is the ultimate sophistication.?", "", "stray quote marks"),
    ("Change 25 to $50 in all three places", "", None),
    ("The lessons of history are written by the wounded not the dead.", "", None),
    ("Meeting notes", "Meeting notes for Tuesday", None),
])
def test_title_problem(title, first, problem):
    assert N.title_problem(title, first) == problem


@pytest.mark.parametrize("title,content,images,new", [
    ("Roadmap Monday, June 04, 2007 9:15 AM What ships...", "Roadmap\nMonday, June 04, 2007\n", [], "Roadmap"),
    ("After the upgrade the build took twice as long...",
     "After the upgrade the build took twice as long. Caching seems to be off\n", [],
     "After the upgrade the build took twice as long"),
    ("http://techcrunch.com/2019/05/02/remote-work-tools/?utm_source=x",
     "http://techcrunch.com/2019/05/02/remote-work-tools/?utm_source=x", [], "TechCrunch - Remote Work Tools"),
    ("http://youtu.be/abc123XYZ", "http://youtu.be/abc123XYZ", [], "YouTube Video"),
    ("Name: Jane Example DOB: Jan 1...", "Name: Jane Example\nDOB: Jan 1 1990\n", [], "Jane Example"),
    ("?Simplicity is the ultimate sophistication.?", "?Simplicity is the ultimate sophistication.?\n", [],
     "Simplicity is the ultimate sophistication"),
    ("Bank site...", "Bank site password: hunter2\n", [], "Bank site Login"),
    ("Untitled", "**Trip Plans**\nDay one\n", [], "Trip Plans"),
    ("Untitled", "", [("Video web content titled: Product Demo", "image-3.jpg")], "Product Demo"),
    ("Untitled", "", [("image", "image.jpg")], None),
])
def test_heuristic_title(title, content, images, new):
    assert N.heuristic_title(title, content, images) == new


@pytest.mark.parametrize("name,generic", [
    ("image.png", True), ("image-12.jpg", True), ("Untitled picture.png", True), ("IMG_2041.JPG", True),
    ("image.png.png", True), ("3f2a9c0d1e4b5a6f7c8d.png", True), ("Trip Map.png", False), ("logo.svg", False),
])
def test_is_generic_asset(name, generic):
    assert N.is_generic_asset(name) is generic


def test_alt_text_names():
    assert N.name_from_alt("Video web content titled: Intro to Rust Lifetimes", "image.jpg") == "Intro to Rust Lifetimes"
    assert N.name_from_alt("image", "image.jpg") is None
    assert N.name_from_alt("TOTAL DUE NET 30 INVOICE NO 4471 BILL TO", "x.png") is None


# --------------------------------------------------------------- scan
def test_scan_finds_secrets_without_echoing_them():
    text = ("Bank password: hunter2\nAWS AKIAABCDEFGHIJKLMNOP\ncard 4111 1111 1111 1111\n"
            "not a card 1234 5678 9012 3456\n")
    kinds = [f.kind for f in S.scan_text("p.md", text)]
    assert "password or PIN" in kinds and "card number" in kinds and len(kinds) == 3
    assert all("hunter2" not in f.sample for f in S.scan_text("p.md", text))
    assert [f.kind for f in S.scan_name("x/github-recovery-codes.txt")] == ["recovery codes file"]
    red = S.redact("password: hunter2, mail me at a@b.co")
    assert "hunter2" not in red and "a@b.co" not in red


# ---------------------------------------------------------------- llm
def test_claude_namer_request_and_redaction():
    pytest.importorskip("anthropic")
    from onenote_to_markdown.tidy.llm import ClaudeNamer
    sent = {}

    def create(**kw):
        sent.update(kw)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text='{"title": "Router Setup."}')])

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    namer = ClaudeNamer("claude-opus-5-5", client=client)
    assert namer.page_title("Untitled", "Home", "Router admin password: hunter2\nSSID homenet") == "Router Setup"
    assert sent["model"] == "claude-opus-5-5"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert "hunter2" not in str(sent["messages"])

    refuse = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: SimpleNamespace(stop_reason="refusal", content=[]))))
    namer = ClaudeNamer(client=refuse)
    assert namer.page_title("Untitled", "", "x") is None and namer.errors


# ---------------------------------------------------------------- cli
def test_standalone_cli(export, tmp_path, capsys):
    assert tidy_cli.main([str(export), "-o", str(export / "inside")]) == 2
    assert tidy_cli.main([str(export), "--dry-run"]) == 0
    assert not os.path.exists(str(export) + "-tidy")
    assert os.path.exists(str(export) + "-tidy-report/report.md")
    assert tidy_cli.main([str(export)]) == 0
    assert "Notes/Work/Projects/Projects.md" in tree(str(export) + "-tidy")
    assert tidy_cli.main([str(export)]) == 2            # won't write into a non-empty folder


def test_in_place_keeps_a_backup(export, tmp_path):
    before = tree(export)
    assert tidy_cli.main([str(export), "--in-place"]) == 0
    assert "Notes/Work/Projects/Projects.md" in tree(export)
    backup, = [p for p in os.listdir(tmp_path) if p.startswith("onenote-md-before-tidy-")]
    with zipfile.ZipFile(tmp_path / backup) as z:
        assert {n[len("onenote-md/"):] for n in z.namelist() if not n.endswith("/")} == set(before)


def test_exporter_tidy_flag(export, tmp_path):
    out = tmp_path / "md"

    def fake_export(staging):
        for rel, data in tree(export).items():
            build(staging, {rel: data})
        return 0

    ap = cli.argparse.ArgumentParser()
    ap.add_argument("-o", "--output")
    ap.add_argument("--versions", action="store_true")
    ap.add_argument("--json", action="store_true")
    tidy_cli.add_exporter_arguments(ap)
    a = ap.parse_args(["-o", str(out), "--tidy"])
    assert tidy_cli.wants_tidy(a)
    assert tidy_cli.export_and_tidy(a, fake_export) == 0
    assert "Notes/Work/Projects/Projects.md" in tree(out)
    assert os.path.exists(str(out) + "-tidy-report/report.md")
    assert tidy_cli.export_and_tidy(a, fake_export) == 1     # Notes/ and Ideas/ are already there
