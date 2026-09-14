# Changelog

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
