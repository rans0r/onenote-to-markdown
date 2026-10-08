# Cleaning up the export (`--tidy`)

The exporter is built to lose nothing: it writes index files, older page
versions, deleted pages, and every page exactly as OneNote titled it. That is
right for an archive, but awkward to open in a note app. `--tidy` is designed
to give you a clean export for Markdown readers, such as
[Skysa Notes](https://skysa.com/), and for any app or viewer that treats a
folder of `.md` files as a notebook.

```bash
python -m onenote_to_markdown "Work Notebook/" "Personal/" -o notes --tidy
```

This runs in the same pass as the export: the raw export goes to a temporary
folder, is cleaned there, and only the clean result is written to `-o`. A
report is written next to it in `notes-tidy-report/`.

Export all your notebooks in **one** command (pass several folders, as above).
Each run writes its own report, so separate runs into the same `-o` replace
each other's reports. `--tidy` also refuses to merge into a notebook folder
that already exists in `-o`.

## Before and after

```
notes/Work Notebook/                       notes/Work Notebook/
├── README.md                              ├── Projects/
├── Projects/                              │   ├── 01 - Kickoff.md
│   ├── README.md                          │   ├── 06 - TechCrunch - Remote Work Tools.md
│   ├── 01 - Kickoff.md                    │   └── Website Redesign/
│   ├── 02 - Website Redesign.md           │       ├── Website Redesign.md
│   ├── 03 - Wireframes.md   (sub-page)    │       ├── Website Redesign.png
│   ├── 04 - Untitled page.md (blank)      │       ├── 01 - Wireframes.md
│   ├── 05 - Copy Deck.md    (sub-page)    │       └── 02 - Copy Deck.md
│   ├── 06 - http techcrunch.com 2019 0…md └── Ideas/
│   ├── assets/image-1.png                     ├── 001 - App Idea.md
│   └── _versions/…                            └── 101 - Reading List.md
├── Ideas (Evernote import on 2016-…)/
│   ├── Pages 1-100/01 - App Idea.md
│   └── Pages 101-200/01 - Reading List.md
└── OneNote_RecycleBin/…
```

A page before and after:

```markdown
---                                        ---
title: "Website Redesign"                  title: "Website Redesign"
notebook: "Work Notebook"                  created: "2024-03-05 14:30:00 UTC"
section: "Projects"                        modified: "2024-03-07 09:12:44 UTC"
level: 1                                   ---
created: "2024-03-05 14:30:00 UTC"
modified: "2024-03-07 09:12:44 UTC"        Mock-up from Tuesday:
---
                                           ![image](Website%20Redesign.png)
# Website Redesign

*Tuesday, March 5, 2024 2:30 PM*

Mock-up from Tuesday:

![image](assets/image-1.png)
```

## What it changes

The steps run in this order. Leave any of them out with `--tidy-skip`
(for example `--tidy-skip nest,titles`).

| step | what it does |
|---|---|
| `scaffolding` | Removes the exporter's `README.md` index files, `_versions/` and `_json/` (kept if you passed `--versions` or `--json`), and system files such as `.DS_Store`, `Thumbs.db` and `__MACOSX/`. |
| `recycle-bin` | Removes `OneNote_RecycleBin/`, which holds pages deleted in OneNote. |
| `blank-pages` | Removes pages with nothing but a title and a date. A blank page that has sub-pages is kept as a folder. |
| `chunks` | OneNote splits notebooks imported from Evernote into sections called `Pages 1-100`, `Pages 101-200`, and so on. Their pages move up into the notebook folder and are numbered straight through (`001` to `250`). |
| `evernote-names` | Drops ` (Evernote import on 2016-06-30T21-49-30)` from folder names. |
| `titles` | Gives a new title to pages whose title is a cut-off first line (`Meeting with Sam about the budget for next quart...`), a bare URL, an email address or code, `Untitled`, or more than 80 characters. The front matter title, the file name and links to the page all change together. |
| `header` | Removes the `# Title` heading and italic date line that repeat the front matter. Pages first written in OneNote 2007 also repeat the title, day and time as their first lines of text; those go too. |
| `nest` | A page with sub-pages becomes a folder with the same name. The page goes inside as `Title/Title.md`, followed by its sub-pages, and this repeats for deeper levels. |
| `attachments` | Moves each image and attachment out of `assets/` to sit next to the page that uses it. A file used by several pages goes to the nearest folder they share. A file no page links to moves out of `assets/` into its section folder and is listed in the report. |
| `attachment-names` | Renames generic image names (`image-1.jpg`, `Untitled picture.png`, `IMG_2041.JPG`) after the image's alt text, or after its page (`Website Redesign.png`, `Website Redesign 2.png`). |
| `frontmatter` | Drops `notebook` and `section` from the front matter, since the folders now show them. Also drops `level` when `nest` ran. `title`, `created` and `modified` stay. |

Every link that points to a moved or renamed file is rewritten, and link
paths are encoded so that names with spaces and parentheses work in strict
Markdown readers. Anything that could not be resolved is listed in the report.

### What it never does

* **It never changes the export it reads.** The standalone tool writes to a
  new folder, and `--in-place` writes a zip backup first.
* **It never edits the text of your notes.** The only changes inside a page
  are removing the repeated heading and date, changing the front matter, and
  rewriting link paths.
* **It never deletes a page that has content, or any attachment.** The only
  things deleted are the index files, older versions, recycle bin, system
  files and blank pages listed above. When two identical copies of an
  attachment end up in the same folder under the same name, they are merged
  into one file.
* **It never removes sensitive data.** Passwords and keys it finds are listed
  in the report for you to deal with. They are never changed.

Running it again on its own output changes nothing.

## The report

`<output>-tidy-report/` holds three files:

* **`report.md`** starts with **Needs your attention**. That section lists:
  * possible passwords, PINs, API keys, card numbers and recovery-code
    files, with the values masked
  * links that point at nothing
  * attachments no page links to
  * pages with almost no text
  * code and program files
  * likely sync-conflict copies
  * paths over 200 characters, which can fail on Windows
  * files that weren't valid UTF-8

  After that it summarises each step's changes and gives a table of every
  title change.
* **`renames.csv`** has one row per page whose title was flagged: the old
  title, the new one, the reason, and whether the new title came from the
  rules or from Claude.
* **`actions.jsonl`** has one line per change: every deletion, move, rename
  and edit, with the original and final paths.

### Reviewing titles

Titles chosen by rules are a starting point. To pick your own, edit the
`new_title` column of `renames.csv` and run the export again with it:

```bash
python -m onenote_to_markdown "Work Notebook/" -o notes-v2 --tidy --tidy-renames notes-tidy-report/renames.csv
```

With a renames file, titles come only from the file. Leave a `new_title`
blank to keep the page's original title.

## Naming with Claude (optional)

The built-in rules handle cut-off first lines and URLs well. They can't
name a page that holds only a photo or a screenshot, or a page whose first
line is a long sentence. `--tidy-llm` asks Claude to name those pages and
generically named images, looking at the image when a page has no text:

```bash
pip install anthropic               # Python 3.10 or newer
export ANTHROPIC_API_KEY=sk-ant-…
python -m onenote_to_markdown "Work Notebook/" -o notes --tidy --tidy-llm
```

* **What is sent:** Claude only sees the pages and images that need a name.
  For each one it gets the current title, the folder name, up to 1,500
  characters of the page, and the image, if the page has no text. Before
  anything is sent, passwords, keys, card numbers, email addresses and phone
  numbers are masked. Nothing is sent for pages whose titles are already
  fine.
* **Cost:** One short request for each page listed in `renames.csv` and
  each generically named image whose alt text doesn't describe it.
* **Failures:** If a request fails or Claude declines, the page falls back to
  the rule-based title. A bad API key or unknown model turns Claude naming
  off for the rest of the run. Problems are listed in the report.
* **Model:** `claude-opus-5-5` by default. Change it with `--llm-model` in
  the standalone tool.

The rest of the tool needs nothing but Python 3.9+ and the standard library.

## Cleaning an export you already have

If you exported without `--tidy`, run the cleaner on the export folder:

```bash
python -m onenote_to_markdown.tidy notes                 # writes notes-tidy/ and notes-tidy-report/
python -m onenote_to_markdown.tidy notes -o clean        # writes clean/ and clean-tidy-report/
python -m onenote_to_markdown.tidy notes --dry-run       # report only
python -m onenote_to_markdown.tidy notes --in-place      # replaces notes/, after zipping it to notes-before-tidy-<time>.zip
```

The standalone tool has the full set of options:

| option | effect |
|---|---|
| `-o DIR` | where to write the cleaned copy (default `<export>-tidy`; must be empty or missing) |
| `--in-place` | replace the export with the cleaned copy, after writing a zip backup next to it |
| `--dry-run` | write only the report |
| `--skip STEPS` / `--only STEPS` | comma-separated steps to leave out, or to run on their own |
| `--llm`, `--llm-model MODEL` | name pages and images with Claude (see above) |
| `--renames CSV` | apply titles from an edited `renames.csv` |
| `--folder-note same-name\|zero\|index` | name of a parent page inside its folder: `Title/Title.md` (default), `Title/00 - Title.md`, or `Title/index.md` |
| `--folder-numbers` | keep the page number on folders made from parent pages (`02 - Website Redesign/`) |
| `--keep-fields FIELDS` / `--drop-fields FIELDS` | choose which front matter fields stay (for example `--keep-fields level`) |
| `--keep-versions`, `--keep-json` | keep `_versions/` and `_json/` |
| `--chunk-pattern REGEX` | which section names count as chunks to merge (default `Pages \d+-\d+`) |
| `--report-dir DIR` | where to write the report (default `<export>-tidy-report`, or `<output>-tidy-report` with `-o`) |

## Limitations

* Rule-based titles come from the first line of a page and are kept to about
  60 characters. Long first sentences get cut at a word boundary, so review
  `renames.csv` or use `--tidy-llm`.
* Pages that are only an image with a generic name keep their `Untitled`
  title unless you use `--tidy-llm`.
* Links between pages are rewritten when they are relative Markdown links.
  OneNote's own `onenote:` links point back into OneNote and are left as they
  are.
* The sensitive-data scan is a list of likely matches, not a guarantee. Read
  through notes you plan to sync or share.
