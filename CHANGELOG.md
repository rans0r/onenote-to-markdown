# Changelog

## Unreleased

* `--tidy` cleans the export for Markdown readers such as
  [Skysa Notes](https://skysa.com/) in the same run. It removes index files,
  older versions, the recycle bin and blank pages, merges `Pages 1-100`
  sections, retitles pages named after a cut-off first line or URL, removes
  the repeated title and date, nests sub-pages into folders, and moves
  attachments next to their pages. It writes a report that also flags
  possible passwords and keys. `--tidy-llm` names pages and images with
  Claude. The same cleaner runs on existing exports as
  `python -m onenote_to_markdown.tidy`. See [docs/tidy.md](docs/tidy.md).
* Fixed: links to images and attachments whose names contain spaces or
  parentheses were written unencoded, so strict Markdown readers didn't
  show them.

## 0.1.0 — 2026-09-14

Initial release.

* Parser for OneNote's alternative packaging (MS‑FSSHTTPB) as served by
  OneDrive / SharePoint, including the 0x7FFF large-length stream objects used
  for images and attachments.
* MS‑ONESTORE property-set decoding, object graph and revision chains; reads
  section order from `Open Notebook.onetoc2`.
* Markdown export: outlines, lists, to-do tags, tables (incl. nested), text
  formatting, hyperlinks, images, attachments, embedded videos, page
  hierarchy, older page versions, per-notebook and per-section indexes.
* Documentation on getting `.one` files out of OneDrive.
