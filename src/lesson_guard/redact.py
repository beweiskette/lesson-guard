"""Redact secret-like strings before text leaves the machine or reaches an agent.

Used for note text sent to a model, for draft files that copy note text, and
for the messages lesson-guard prints back to an agent. Detection is a
heuristic: it removes well-known key formats, private key blocks, bearer
tokens, values assigned to names like password or token, and long
high-entropy strings. It cannot know every secret format.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .predicates import _PLACEHOLDER_HINTS, _PROVIDER_PATTERNS

MARK = "[REDACTED:{kind}]"

_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)",
    re.DOTALL,
)

_BEARER = re.compile(r"(?i)\b(bearer|basic|token)(\s+)(?P<value>[A-Za-z0-9\-._~+/]{8,}=*)")

_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(?P<name>[\w.\-]*(?:api[_\-]?key|secret|token|passw(?:or)?d|pwd|access[_\-]?key|auth[_\-]?key
        |credential|private[_\-]?key|client[_\-]?secret)[\w.\-]*)
    (?P<sep>["']?[ \t]*(?::=|=>|=|:)[ \t]*)
    (?P<quote>["'`]?)
    (?P<value>[^\s"'`,;]{4,})
    """
)

_TOKEN = re.compile(r"(?<![A-Za-z0-9_\-+/=])[A-Za-z0-9_\-+/=]{32,}(?![A-Za-z0-9_\-+/=])")

# Anything already redacted is left alone.
_ALREADY = re.compile(r"\[REDACTED:[a-z-]+\]")


@dataclass(frozen=True)
class Redaction:
    kind: str
    value: str


@dataclass
class Redacted:
    text: str
    redactions: list[Redaction] = field(default_factory=list)

    @property
    def values(self) -> list[str]:
        return [r.value for r in self.redactions]


def _entropy(value: str) -> float:
    counts = Counter(value)
    total = len(value)
    return -sum(n / total * math.log2(n / total) for n in counts.values())


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    if _ALREADY.search(value) or value[0] in "$<{%[(":
        return True
    if len(set(value)) <= 2:  # "****", "xxxx", "...."
        return True
    return any(hint in lowered for hint in _PLACEHOLDER_HINTS)


def _high_entropy(value: str) -> bool:
    has_digit = any(c.isdigit() for c in value)
    has_alpha = any(c.isalpha() for c in value)
    # Base62/base64 secrets score about 4.5 and more; hex hashes stay below 4.
    return has_digit and has_alpha and _entropy(value) >= 4.2 and not _is_placeholder(value)


def redact(text: str) -> Redacted:
    """Return text with secret-like strings replaced by [REDACTED:kind]."""
    found: list[Redaction] = []

    def mark(kind: str, value: str) -> str:
        found.append(Redaction(kind, value))
        return MARK.format(kind=kind)

    text = _PRIVATE_KEY_BLOCK.sub(lambda m: mark("private-key", m.group(0)), text)
    for pattern in _PROVIDER_PATTERNS:
        text = pattern.sub(lambda m: m.group(0) if m.group(0).startswith("-----") else mark("provider-key", m.group(0)), text)

    def bearer(m: re.Match[str]) -> str:
        value = m.group("value")
        if _is_placeholder(value) or value.isalpha() and value.islower():
            return m.group(0)
        return m.group(1) + m.group(2) + mark("bearer", value)

    text = _BEARER.sub(bearer, text)

    def assignment(m: re.Match[str]) -> str:
        value = m.group("value")
        if _is_placeholder(value):
            return m.group(0)
        # "The token: never commit it." is prose, not a credential. A colon
        # followed by a plain lowercase word is left alone.
        if ":" in m.group("sep") and "=" not in m.group("sep") and not m.group("quote") and re.fullmatch(r"[a-z]+", value):
            return m.group(0)
        return m.group("name") + m.group("sep") + m.group("quote") + mark("assignment", value)

    text = _ASSIGNMENT.sub(assignment, text)
    text = _TOKEN.sub(lambda m: mark("high-entropy", m.group(0)) if _high_entropy(m.group(0)) else m.group(0), text)
    return Redacted(text, found)


def redact_text(text: str) -> str:
    return redact(text).text


def scrub(data: Any, values: list[str]) -> tuple[Any, bool]:
    """Replace every occurrence of the given values in all strings of data.

    Returns the cleaned structure and whether anything was replaced.
    """
    values = sorted({v for v in values if len(v) >= 4}, key=len, reverse=True)
    hit = False

    def walk(item: Any) -> Any:
        nonlocal hit
        if isinstance(item, str):
            for value in values:
                if value in item:
                    hit = True
                    item = item.replace(value, MARK.format(kind="removed"))
            return item
        if isinstance(item, dict):
            return {walk(k): walk(v) for k, v in item.items()}
        if isinstance(item, list):
            return [walk(v) for v in item]
        return item

    return walk(data), hit
