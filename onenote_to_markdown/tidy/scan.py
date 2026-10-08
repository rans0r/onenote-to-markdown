"""Finding passwords, keys and other sensitive data. Report only – nothing is edited.

The same patterns mask that data out of anything sent to Claude.
"""
from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass
from typing import Iterator, List, Optional

from .vault import Vault

TEXT_EXTENSIONS = {".txt", ".csv", ".json", ".ini", ".cfg", ".conf", ".env", ".yaml", ".yml", ".xml", ".log", ".md"}
MAX_TEXT_BYTES = 2 << 20

PASSWORD = re.compile(r"\b(?:password|passwd|pwd|passcode|pass|pin)\b[ \t]*([:=]?)[ \t]*(\S+)", re.I)
PATTERNS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("Stripe key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{16,}")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("API key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("US Social Security number", re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    # an authenticator (TOTP) secret on a line of its own: 16-32 base32 characters, at least one digit
    ("2FA secret", re.compile(r"^[ \t]*((?=[A-Z2-7 ]*[2-7])[A-Z2-7]{4}(?: ?[A-Z2-7]{4}){3,7})[ \t]*$", re.M)),
]
CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
RECOVERY_FILE = re.compile(r"(?i)(recovery|backup)[ _-]?codes?")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<!\w)\+?\d[\d ().-]{7,}\d(?!\w)")


@dataclass
class Finding:
    path: str
    line: int           # 0 when the finding is the file name itself
    kind: str
    sample: str         # masked


def mask(value: str) -> str:
    value = value.strip()
    if len(value) <= 4:
        return "•" * len(value)
    return f"{value[:2]}…({len(value)} chars)"


def luhn(digits: str) -> bool:
    total, alt = 0, False
    for d in reversed(digits):
        n = int(d)
        if alt:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
        alt = not alt
    return total % 10 == 0


def _password_value(m: re.Match) -> Optional[str]:
    sep, value = m.group(1), m.group(2).strip(".,;")
    if not value:
        return None
    if sep or re.search(r"[\d\W_]", value):
        return value
    return None


def scan_text(path: str, text: str) -> Iterator[Finding]:
    for i, line in enumerate(text.split("\n"), 1):
        for m in PASSWORD.finditer(line):
            value = _password_value(m)
            if value:
                yield Finding(path, i, "password or PIN", mask(value))
        for kind, rx in PATTERNS:
            for m in rx.finditer(line):
                yield Finding(path, i, kind, mask(m.group(m.lastindex or 0)))
        for m in CARD.finditer(line):
            digits = re.sub(r"\D", "", m.group(0))
            if 13 <= len(digits) <= 19 and digits[0] in "3456" and luhn(digits):
                yield Finding(path, i, "card number", mask(digits))


def scan_name(path: str) -> Iterator[Finding]:
    name = posixpath.basename(path)
    if RECOVERY_FILE.search(name):
        yield Finding(path, 0, "recovery codes file", name)
    for m in PASSWORD.finditer(name):
        value = _password_value(m)
        if value:
            yield Finding(path, 0, "password in file name", mask(value))


def scan_vault(v: Vault) -> List[Finding]:
    found: List[Finding] = []
    for item in v.alive():
        found += scan_name(item.path)
        if item.is_note:
            found += scan_text(item.path, item.text())
        elif posixpath.splitext(item.name)[1].lower() in TEXT_EXTENSIONS:
            try:
                if os.path.getsize(item.src) <= MAX_TEXT_BYTES:
                    with open(item.src, encoding="utf-8", errors="replace") as f:
                        found += scan_text(item.path, f.read())
            except OSError:
                pass
    return found


def redact(text: str) -> str:
    """Mask sensitive values before text leaves the machine."""
    def sub_password(m: re.Match) -> str:
        value = _password_value(m)
        return m.group(0).replace(value, "[redacted]") if value else m.group(0)

    text = PASSWORD.sub(sub_password, text)
    for _, rx in PATTERNS:
        text = rx.sub("[redacted]", text)
    text = CARD.sub(lambda m: "[redacted]" if len(re.sub(r"\D", "", m.group(0))) >= 13 else m.group(0), text)
    text = EMAIL.sub("[email]", text)
    return PHONE.sub("[phone]", text)
