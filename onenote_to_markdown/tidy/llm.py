"""Optional: ask Claude for page titles and image file names.

Needs the `llm` extra (`pip install "onenote-to-markdown[llm]"`, Python 3.10+)
and an Anthropic credential (ANTHROPIC_API_KEY, or a profile from `ant auth
login`). Only pages and images that need a new name are sent – page text is
cut to an excerpt and passwords, keys, card numbers, email addresses and phone
numbers are masked first (see scan.redact).
"""
from __future__ import annotations

import base64
import json
import os
import posixpath
from typing import List, Optional, Tuple

from .scan import redact

DEFAULT_MODEL = "claude-opus-5-5"
EXCERPT_CHARS = 1500
MAX_IMAGE_BYTES = 3_750_000          # stays under the API's 5 MB limit once base64-encoded
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".gif": "image/gif", ".webp": "image/webp"}
SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
    "additionalProperties": False,
}
PAGE_SYSTEM = """\
You name pages in a personal notes archive exported from Microsoft OneNote. \
Many pages were imported from Evernote and titled with a cut-off first line, \
or never titled at all. Give the page a short, specific title (2 to 7 words) \
that tells its owner what the page is about when scanning a list of files.

- Use Title Case and keep the names of people, companies, products and places.
- Never include passwords, PINs, account or card numbers, email addresses, \
phone numbers or other credentials, even if the page contains them. If the \
page is mainly a login, name the service, for example "Router Admin Login".
- If the page is mainly a quotation, use "Quote - <speaker or topic>". If it \
is mainly one link, name what the link is about. If it is a joke, use \
"Joke - <topic>".
- No dates unless the date is the point of the page. No trailing punctuation.
- Text shown as [redacted], [email] or [phone] was masked before you saw it."""
IMAGE_SYSTEM = """\
You name image files in a personal notes archive. Give the image a short \
file name (2 to 7 words, Title Case) that says what it shows, such as \
"Lakeside Apartments Logo" or "Cartoon - Monday Meetings". If the image \
shows passwords, account numbers or other credentials, describe what kind of \
document it is without repeating them. No file extension, no trailing \
punctuation."""


class ClaudeUnavailable(Exception):
    pass


class ClaudeNamer:
    def __init__(self, model: str = DEFAULT_MODEL, client=None):
        if client is None:
            try:
                import anthropic
            except ImportError:
                raise ClaudeUnavailable(
                    "Claude naming needs the Anthropic SDK: pip install anthropic (Python 3.10 or newer)") from None
            client = anthropic.Anthropic(max_retries=4)
        self.client = client
        self.model = model
        self.errors: List[str] = []
        self.disabled = False

    def page_title(self, title: str, folder: str, content: str,
                   image: Optional[Tuple[str, bytes]] = None) -> Optional[str]:
        excerpt = redact(content.strip())[:EXCERPT_CHARS]
        text = (f"Current title: {redact(title)}\nFolder: {folder}\n\n"
                f"<page>\n{excerpt or '(no text - the page holds only the image above)'}\n</page>")
        blocks = ([_image_block(*image)] if image else []) + [{"type": "text", "text": text}]
        return self._ask(PAGE_SYSTEM, blocks)

    def image_name(self, page_title: str, alt: str, image: Tuple[str, bytes]) -> Optional[str]:
        text = f"The image appears on a page titled: {redact(page_title)}"
        if alt:
            text += f"\nOneNote's description of the image: {redact(alt)}"
        return self._ask(IMAGE_SYSTEM, [_image_block(*image), {"type": "text", "text": text}])

    def _ask(self, system: str, content: list) -> Optional[str]:
        if self.disabled:
            return None
        import anthropic
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                system=system,
                messages=[{"role": "user", "content": content}],
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            self.disabled = True
            self.errors.append(f"Claude naming turned off: {e.message}")
            return None
        except anthropic.NotFoundError as e:
            self.disabled = True
            self.errors.append(f"Claude naming turned off (model {self.model!r}?): {e.message}")
            return None
        except anthropic.RateLimitError as e:
            self.errors.append(f"rate limited after retries: {e.message}")
            return None
        except anthropic.APIStatusError as e:
            self.errors.append(f"API error {e.status_code}: {e.message}")
            return None
        except anthropic.APIConnectionError:
            self.errors.append("could not reach the Anthropic API")
            return None
        if response.stop_reason in ("refusal", "max_tokens"):
            self.errors.append(f"no title ({response.stop_reason})")
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            title = json.loads(text)["title"]
        except (ValueError, KeyError, TypeError):
            self.errors.append("unreadable reply")
            return None
        title = " ".join(str(title).split()).strip(" .")
        return title or None


def load_image(path: str) -> Optional[Tuple[str, bytes]]:
    """(media type, bytes) for an image Claude can read, else None."""
    media = IMAGE_TYPES.get(posixpath.splitext(path)[1].lower())
    try:
        if not media or os.path.getsize(path) > MAX_IMAGE_BYTES:
            return None
        with open(path, "rb") as f:
            return media, f.read()
    except OSError:
        return None


def _image_block(media: str, data: bytes) -> dict:
    return {"type": "image",
            "source": {"type": "base64", "media_type": media, "data": base64.standard_b64encode(data).decode()}}
