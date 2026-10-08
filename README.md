# onenote-to-markdown

Export Microsoft OneNote notebooks to organized Markdown — straight from the
`.one` section files that OneDrive and SharePoint store, with no OneNote
installation, no Microsoft Graph tokens and no dependencies beyond Python 3.9+.

```
$ python -m onenote_to_markdown "My Notebook/" -o notes --versions
My Notebook/Work.one: 47 pages, 768 paragraphs, 19820 chars, 142 old versions
My Notebook/Personal.one: 17 pages, 145 paragraphs, 3484 chars, 22 old versions
```

```
notes/My Notebook/
├── README.md                     ← index of sections
├── Work/
│   ├── README.md                 ← index of pages (order, hierarchy, dates)
│   ├── 01 - Project kickoff.md
│   ├── 02 - Meeting notes.md
│   ├── assets/                   ← images and attachments
│   └── _versions/                ← older page versions (--versions)
└── Personal/
    └── …
```

## Why this exists

OneNote has no bulk "export to Markdown", and its file format is a binary
object store, not a document. The open-source readers that do exist parse the
*classic* revision-store format that desktop OneNote writes to a local disk —
but that is **not** what you get from OneDrive. Notebooks stored in OneDrive
or SharePoint use OneNote's *alternative packaging* (an MS‑FSSHTTPB cell store),
which those readers reject.

`onenote-to-markdown` implements that packaging from the published specifications
([MS‑FSSHTTPB], [MS‑ONESTORE], [MS‑ONE]) and walks the resulting object graph
into Markdown. Everything is pure Python and standard library.

## A clean export for Markdown readers

Add `--tidy` to get a clean export for Markdown readers, such as
[Skysa Notes](https://skysa.com/), in the same run:

```bash
python -m onenote_to_markdown "Work Notebook/" "Personal/" -o notes --tidy
```

It removes the index files, older versions, deleted and blank pages, gives
proper titles to pages OneNote named after a cut-off first line or a URL,
turns pages with sub-pages into folders, moves each image next to its page,
and keeps only `title`, `created` and `modified` in the front matter. Your
notes' text is never edited, and every change is listed in a report
(`notes-tidy-report/`). That report also flags possible passwords and keys
for you to review. `--tidy-llm` lets Claude name image-only pages and
generically named images.
See **[docs/tidy.md](docs/tidy.md)** for details, and for cleaning an export
you already have.

## Getting your `.one` files

OneDrive's web UI hides notebook internals — there is no *Download* on a
notebook. The trick is to copy the notebook into an ordinary folder and
download that folder as a zip; the zip contains one `.one` file per section
plus `Open Notebook.onetoc2`. The full walkthrough, with the alternatives
(Microsoft Graph, the sync client) and the dead ends, is in
**[docs/downloading-from-onedrive.md](docs/downloading-from-onedrive.md)**.

## Running it

There is nothing to install — the tool is pure Python 3.9+ with no
dependencies. Clone the repository and run the module:

```bash
git clone https://github.com/rans0r/onenote-to-markdown
cd onenote-to-markdown
python -m onenote_to_markdown "My Notebook/" -o notes
```

## Usage

```
python -m onenote_to_markdown <notebook-folder | file.one ...> [-o DIR] [--versions] [--json] [--notebook NAME]
                              [--tidy [--tidy-llm] [--tidy-skip STEPS] [--tidy-renames CSV]]
```

| option | effect |
|---|---|
| `-o DIR` | output root (default `onenote-md`) |
| `--versions` | also export the older page versions OneNote keeps inside the file (`_versions/`) |
| `--json` | dump each page's raw object tree as JSON (`_json/`) — a lossless safety net if the Markdown rendering misses something |
| `--notebook NAME` | override the notebook name (defaults to the folder containing the `.one` files) |
| `--tidy` | clean the export for Markdown note apps and write a report to `<DIR>-tidy-report/` ([docs/tidy.md](docs/tidy.md)) |
| `--tidy-llm` | with `--tidy`, let Claude name untitled pages and generically named images (`pip install anthropic`, Python 3.10+, `ANTHROPIC_API_KEY`) |
| `--tidy-skip STEPS` | with `--tidy`, comma-separated steps to leave out |
| `--tidy-renames CSV` | with `--tidy`, apply titles from an edited `renames.csv` |

Pass a notebook folder to convert every section in it (recursively, so
section groups such as `OneNote_RecycleBin/` become sub-folders), or
individual `.one` files.

Each page becomes one Markdown file with YAML front matter (`title`,
`notebook`, `section`, `level` — 1 for pages, 2+ for sub-pages — `created`,
`modified`), an H1 title, the page's date/time line, and the body. With
`--tidy`, sub-pages become folders and only `title`, `created` and `modified`
stay.

## What is preserved

* **Structure** — sections in the order OneNote shows them (read from
  `Open Notebook.onetoc2`), pages in notebook order, sub-page hierarchy,
  section groups, and per-notebook / per-section index files.
* **Text** — outlines and nested outline elements; paragraphs stacked with
  hard line breaks so OneNote's line layout survives; bold, italic, underline,
  strikethrough, super/subscript; paragraph styles (`h1`–`h6`, `code`,
  `blockquote`, `cite`); text stored as UTF‑16 *or* 8‑bit (both occur).
* **Lists and tags** — bulleted and numbered lists with nesting; to-do tags
  as GitHub task lists (`- [ ]` / `- [x]`), other tags as labels.
* **Links** — explicit hyperlinks, `HYPERLINK` field codes, auto-detected
  URLs, and embedded online videos (rendered as a link).
* **Tables** — including tables nested inside cells.
* **Images and attachments** — written to `assets/` (de-duplicated across
  page versions) and referenced from the Markdown.
* **History** — with `--versions`, every older page version OneNote still
  holds in the file.
* **Metadata** — page titles (falling back to OneNote's cached title when the
  title box is empty), creation and modification times.

Anything the renderer does not understand is counted and reported both on
stdout and in the section's `README.md`, so silent loss is visible. Leading
spaces are converted to no-break spaces so indentation survives rendering
without accidentally becoming a code block.

### Known limitations

* Markdown has no font size or color, so runs that differ only in those
  render as plain text.
* OneNote's `a.` / `i.` numbering styles render as `1.` (Markdown renumbers).
* Ink strokes are not rasterized; a placeholder marks where a drawing was.
* Objects that no longer have a parent in the page graph (content deleted in
  OneNote but still present in the store) are deliberately not exported.
* Classic revision-store `.one` files (written by desktop OneNote to a local
  disk, `guidFileFormat = {109ADD3F-911B-49F5-A5D0-1791EDC8AED8}`) are a
  different container and are **not** parsed — the tool reports
  `not a OneNote alternative-packaging file`. The object/property layer is
  reusable for them; only the container reader is missing (contributions
  welcome).

## Testing status

The tool has been run against 40 real notebooks (about 110 section files,
1,400 current pages, 1,400 older versions, 820,000 characters, plus images,
PDFs and other attachments) accumulated over 15 years of OneNote use, and the
output of several of them was compared page by page against OneNote Online.
Section order, page order and hierarchy, every paragraph, list nesting,
tables, tags, links and dates matched. Your notebooks will contain things
those did not — please open an issue with the `--json` dump of a page that
renders wrongly.

## How it works

```
onenote_to_markdown/
├── fsshttpb.py   MS-FSSHTTPB container: stream objects, data elements, object groups
├── onestore.py   MS-ONESTORE / MS-ONE object layer: property sets, object graph, revisions
├── cli.py        MS-ONE node semantics and the Markdown renderer
└── tidy/         --tidy: cleans an export for Markdown note apps (docs/tidy.md)
```

A `.one` file from OneDrive is a 64-byte header (`guidFileFormat =
{638DE92F-A6D4-4BC1-9A36-B3FC2511A5B7}`) followed by an MS‑FSSHTTPB *data
element package*. Inside it, a storage index maps each *cell* (an object space:
the section itself and one per page) to its current *revision manifest*;
revision manifests chain through base revisions and list the *object groups*
that hold the objects. Every OneNote object appears twice under the same id —
one partition carries its 4-byte JCID (node type), another its property set —
and file bytes for images and attachments live in a third partition or in a
separate object-data BLOB. Object references inside property sets are
positional into the object group's extended-GUID array.

`Open Notebook.onetoc2` is served by OneDrive as a classic-format stub with an
alternative-packaging payload embedded after the header; the tool finds that
payload to recover section order and colors.

The library layer is importable if you want something other than Markdown:

```python
from onenote_to_markdown import Section

sec = Section(open("Work.one", "rb").read())
for space in sec.spaces:              # section + one object space per page
    for node in space.nodes.values():
        print(node.jcid_name, node.props)
```

## Contributing

Bug reports with a `--json` dump (or, if you can share it, the `.one` file)
are the most useful thing. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE).

[MS‑FSSHTTPB]: https://learn.microsoft.com/en-us/openspecs/sharepoint_protocols/ms-fsshttpb/
[MS‑ONESTORE]: https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-onestore/
[MS‑ONE]: https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-one/
