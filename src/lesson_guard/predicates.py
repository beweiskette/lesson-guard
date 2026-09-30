"""Content predicates that plain regular expressions cannot express well.

Unary predicates take one text. Relational predicates compare the file
content before the change ("original") with the content after it.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Callable

# ---------------------------------------------------------------- line endings


def line_ending_styles(text: str) -> set[str]:
    """Return the set of line ending styles used in text: "crlf", "lf", "cr"."""
    styles: set[str] = set()
    crlf = text.count("\r\n")
    if crlf:
        styles.add("crlf")
    if text.count("\n") > crlf:
        styles.add("lf")
    if text.count("\r") > crlf:
        styles.add("cr")
    return styles


def mixed_line_endings(text: str) -> bool:
    """True when the text uses more than one line ending style."""
    return len(line_ending_styles(text)) > 1


def line_endings_changed(original: str | None, content: str | None) -> bool:
    """True when a change alters the set of line ending styles of a file.

    Typical case: a file that mixes CRLF and LF is rewritten with LF only.
    Returns False when either side is unknown or has no line breaks.
    """
    if original is None or content is None:
        return False
    before = line_ending_styles(original)
    after = line_ending_styles(content)
    if not before or not after:
        return False
    return before != after


# ------------------------------------------------------- double-encoded UTF-8

# A valid UTF-8 multi-byte sequence. If text decoded as cp1252/latin-1 still
# contains such a byte sequence, it was most likely UTF-8 decoded twice.
_UTF8_MULTIBYTE = re.compile(
    rb"[\xC2-\xDF][\x80-\xBF]|[\xE0-\xEF][\x80-\xBF]{2}|[\xF0-\xF4][\x80-\xBF]{3}"
)


def _to_single_bytes(text: str) -> bytes:
    out = bytearray()
    for ch in text:
        code = ord(ch)
        if code < 0x80:
            out.append(code)
            continue
        try:
            encoded = ch.encode("cp1252")
        except UnicodeEncodeError:
            encoded = b""
        if len(encoded) == 1:
            out.append(encoded[0])
        elif code < 0x100:
            out.append(code)
        else:
            # Not representable as one byte: acts as a separator.
            out.append(0)
    return bytes(out)


def utf8_double_encoded(text: str) -> bool:
    """True when text contains mojibake such as "Ã¤" or "â€™".

    These appear when UTF-8 bytes are decoded as cp1252 or latin-1 and then
    encoded as UTF-8 again.
    """
    if not any(ord(ch) >= 0xC2 for ch in text):
        return False
    return _UTF8_MULTIBYTE.search(_to_single_bytes(text)) is not None


# ------------------------------------------------------------- secret-likes

_PROVIDER_PATTERNS = [
    re.compile(p)
    for p in (
        r"\bsk-(?:ant-|proj-|live-)?[A-Za-z0-9_\-]{20,}",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
        r"\bgithub_pat_[A-Za-z0-9_]{40,}\b",
        r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
        r"\bAIza[0-9A-Za-z_\-]{35}\b",
        r"\bglpat-[A-Za-z0-9_\-]{20,}",
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----",
        r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    )
]

_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b[\w.\-]*(?:api[_\-]?key|secret|token|passw(?:or)?d|access[_\-]?key|auth[_\-]?key|credential)[\w.\-]*
    ["']?\s*(?::|=|:=|=>)\s*["']?
    (?P<value>[A-Za-z0-9_\-/+=.]{16,})
    """
)

_PLACEHOLDER_HINTS = (
    "your", "xxxx", "example", "changeme", "placeholder", "dummy", "redacted",
    "replace", "insert", "todo", "environ", "getenv", "process.env", "secrets.",
)


def _entropy(value: str) -> float:
    counts = Counter(value)
    total = len(value)
    return -sum(n / total * math.log2(n / total) for n in counts.values())


def _looks_random(value: str) -> bool:
    lowered = value.lower()
    if any(hint in lowered for hint in _PLACEHOLDER_HINTS):
        return False
    has_digit = any(c.isdigit() for c in value)
    has_alpha = any(c.isalpha() for c in value)
    return has_digit and has_alpha and _entropy(value) >= 3.5


def contains_secret_like(text: str) -> bool:
    """True when text contains a string that looks like a credential.

    Detects well-known key formats and high-entropy values assigned to names
    such as api_key, token, secret or password. Placeholders such as
    "your-api-key-here" or references to environment variables do not count.
    """
    for pattern in _PROVIDER_PATTERNS:
        if pattern.search(text):
            return True
    for match in _ASSIGNMENT.finditer(text):
        if _looks_random(match.group("value")):
            return True
    return False


# ---------------------------------------------------------- generated files

_GENERATED_MARKER = re.compile(
    r"(?i)(@generated\b|\bdo not edit\b|\bdon't edit\b|\bauto-?generated\b"
    r"|\bthis file (?:is|was) (?:automatically )?generated\b"
    r"|\bgenerated by .{1,80}(?:do not|don't) (?:edit|modify))"
)


def file_is_generated_marker(text: str) -> bool:
    """True when the head of the file carries a "generated, do not edit" marker."""
    head = "\n".join(text.splitlines()[:30])
    return _GENERATED_MARKER.search(head) is not None


# ------------------------------------------------------------------ registry

UnaryPredicate = Callable[[str], bool]
RelationalPredicate = Callable[["str | None", "str | None"], bool]

# name -> (function, default target)
UNARY: dict[str, tuple[UnaryPredicate, str]] = {
    "mixed_line_endings": (mixed_line_endings, "content"),
    "utf8_double_encoded": (utf8_double_encoded, "added"),
    "contains_secret_like": (contains_secret_like, "added"),
    "file_is_generated_marker": (file_is_generated_marker, "original"),
}

RELATIONAL: dict[str, RelationalPredicate] = {
    "line_endings_changed": line_endings_changed,
}

TARGETS = ("content", "original", "added", "command", "any")

ALL_NAMES = tuple(UNARY) + tuple(RELATIONAL)
