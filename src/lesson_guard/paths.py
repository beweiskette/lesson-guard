"""Path normalisation and glob matching with ``**`` support."""

from __future__ import annotations

import re
from functools import lru_cache


def normalize(path: str, cwd: str | None = None) -> str:
    """Forward slashes, no leading "./", relative to cwd when path lies below it."""
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if cwd:
        base = cwd.replace("\\", "/").rstrip("/")
        if base and p.lower().startswith(base.lower() + "/"):
            p = p[len(base) + 1 :]
    return p


@lru_cache(maxsize=512)
def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob into a regex.

    ``*`` and ``?`` do not cross "/", ``**/`` matches zero or more directories,
    a trailing ``**`` matches everything below.
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern.startswith("**", i):
                if pattern.startswith("**/", i):
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1 : j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j + 1
                continue
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("(?s:" + "".join(out) + r")\Z")


def glob_match(pattern: str, path: str) -> bool:
    """Match a normalised path against a glob.

    A pattern without "/" is matched against the file name only, like in
    .gitignore. Other patterns are matched against the whole path.
    """
    pattern = pattern.replace("\\", "/")
    if pattern.startswith("./"):
        pattern = pattern[2:]
    regex = glob_to_regex(pattern)
    if "/" not in pattern:
        return regex.match(path.rsplit("/", 1)[-1]) is not None
    return regex.match(path) is not None
