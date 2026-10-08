# Contributing

Thanks for taking an interest. The most valuable contribution is a bug report
for a page that renders wrongly.

## Reporting a rendering problem

1. Run the tool with `--json` on the section that contains the page.
2. Open an issue describing what OneNote shows versus what the Markdown shows,
   and attach the page's JSON dump from `_json/` (it contains the page's text
   and property names, so review it for anything private first). If you can
   share the `.one` file itself, even better.

## Development

```bash
git clone https://github.com/rans0r/onenote-to-markdown
cd onenote-to-markdown
pip install -e ".[dev]"
pytest
```

The exporter is three modules:

* `onenote_to_markdown/fsshttpb.py` — the MS‑FSSHTTPB container. Generic; knows
  nothing about OneNote beyond the packaging header.
* `onenote_to_markdown/onestore.py` — MS‑ONESTORE property sets and the object
  graph; the `PROP_NAMES` / `JCID_NAMES` tables live here. Names ending in
  `?` are guesses from observed data rather than the spec — corrections
  welcome.
* `onenote_to_markdown/cli.py` — MS‑ONE node semantics and the Markdown renderer.

`onenote_to_markdown/tidy/` is the `--tidy` cleaner ([docs/tidy.md](docs/tidy.md)).
It works on exported Markdown, not `.one` files. It loads the export into an
in-memory model (`vault.py`). Each step in `steps.py` changes that model and
logs what it did, and links are rewritten once at the end. Only then is
anything written. Title rules are in `naming.py`, Claude naming in `llm.py`
(the only module that needs a dependency, imported only with `--tidy-llm`),
and the sensitive-data scan in `scan.py`. Tests in `tests/test_tidy.py`
build small synthetic exports. Please keep it that way.

Specifications: [MS‑FSSHTTPB](https://learn.microsoft.com/en-us/openspecs/sharepoint_protocols/ms-fsshttpb/),
[MS‑ONESTORE](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-onestore/),
[MS‑ONE](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-one/).

## Ideas that would be welcome

* A reader for the classic revision-store container (`.one` files written by
  desktop OneNote), reusing the property-set layer.
* Rasterizing ink strokes.
* Test fixtures: small synthetic `.one` files that exercise tables, lists,
  images and tags without containing anyone's real notes.

Please don't commit real notebooks or exports; `.gitignore` excludes
`*.one` / `*.onetoc2` for that reason.
