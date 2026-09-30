"""Git pre-commit mode: every staged file becomes a pre_commit Action."""

from __future__ import annotations

import subprocess

from .engine import Action
from .payload import MAX_READ_BYTES, decode


class GitError(RuntimeError):
    pass


def _git(args: list[str], cwd: str | None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    if check and proc.returncode != 0:
        raise GitError(decode(proc.stderr).strip() or f"git {' '.join(args)} failed")
    return proc


def _blob(spec: str, cwd: str | None) -> str | None:
    proc = _git(["cat-file", "-p", spec], cwd, check=False)
    if proc.returncode != 0:
        return None
    data = proc.stdout
    if len(data) > MAX_READ_BYTES or b"\x00" in data[:8192]:
        return None
    return decode(data)


def _added_lines(path: str, cwd: str | None) -> str:
    proc = _git(["diff", "--cached", "--no-color", "--no-ext-diff", "-U0", "--", path], cwd, check=False)
    lines = []
    for raw in proc.stdout.split(b"\n"):
        if raw.startswith(b"+") and not raw.startswith(b"+++"):
            lines.append(decode(raw[1:]))
    return "\n".join(lines)


def staged_actions(cwd: str | None = None) -> list[Action]:
    # Paths from "git diff --name-only" are relative to the top level.
    cwd = decode(_git(["rev-parse", "--show-toplevel"], cwd).stdout).strip()
    proc = _git(["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"], cwd)
    names = [decode(n) for n in proc.stdout.split(b"\x00") if n]
    actions = []
    for name in names:
        content = _blob(f":{name}", cwd)
        if content is None:
            continue  # binary or too large
        original = _blob(f"HEAD:{name}", cwd)
        added = content if original is None else _added_lines(name, cwd)
        actions.append(
            Action(event="pre_commit", tool="git", path=name, content=content, original=original, added=added)
        )
    return actions
