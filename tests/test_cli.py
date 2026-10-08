import uuid

from onenote_to_markdown import cli, onestore as O


class _Fmt:
    def __init__(self, kw=None):
        self.kw = kw or {}

    def get(self, pid, default=None):
        return self.kw.get(pid, default)


def test_apply_format_wraps_core_and_keeps_whitespace():
    fmt = _Fmt({cli.P["Bold"]: True, cli.P["Italic"]: True})
    assert cli.MarkdownRenderer.apply_format("  hi  ", fmt, "") == "  ***hi***  "
    link = _Fmt({cli.P["Hyperlink"]: True})
    assert cli.MarkdownRenderer.apply_format("https://x.y/z", link, "") == "<https://x.y/z>"
    assert cli.MarkdownRenderer.apply_format("text", _Fmt(), "https://a.b") == "[text](https://a.b)"


def test_safe_name_strips_path_characters():
    assert cli.safe_name('a/b:c*d?"e<f>g|h') == "a b c d e f g h"
    assert cli.safe_name("   ") == "Untitled"
    assert len(cli.safe_name("x" * 200)) == 80


def test_keep_leading_spaces_uses_nbsp():
    out = cli.MarkdownRenderer.keep_leading_spaces("    indented")
    assert out.startswith(" " * 4) and out.endswith("indented")


def test_time_conversions():
    assert cli.time32(0).isoformat().startswith("1980-01-01")
    assert cli.filetime(129652901230000000).year == 2011
    assert cli.iso(None) == ""


def test_hyperlink_field_code_in_text():
    class Node:
        def __init__(self, text):
            self._t = text

        def get(self, pid, default=None):
            if pid == cli.P["RichEditTextUnicode"]:
                return self._t.encode("utf-16le")
            return default

    class Space:
        def resolve(self, oids):
            return []

    r = cli.MarkdownRenderer(None, "/tmp/unused")
    out = r.rich_text(Space(), Node('﷟HYPERLINK "https://example.com"Example'))
    assert out == "[Example](https://example.com)"


def test_md_path_escapes_link_breaking_characters():
    assert cli.md_path("assets/Flight Receipt (NYC - SFO).pdf") == "assets/Flight%20Receipt%20%28NYC%20-%20SFO%29.pdf"
    assert cli.md_path("a/100% #1?.png") == "a/100%25%20%231%3F.png"
    assert cli.md_path("café/<x>.md") == "café/%3Cx%3E.md"
