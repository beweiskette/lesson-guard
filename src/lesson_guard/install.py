"""Config snippets that register lesson-guard as a hook.

By default only printed. With a target path they are merged into that file.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

CLAUDE_MATCHER = "Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit"
CODEX_MATCHER = "Bash|apply_patch"
MARKER = "lesson-guard check"
GIT_MARKER = "# installed by lesson-guard"


def _quote(value: str) -> str:
    # Double quotes work in sh, bash and cmd. Forward slashes avoid escaping.
    return '"' + value.replace("\\", "/").replace('"', '\\"') + '"'


def check_command(command: str, fmt: str, guards_dir: str) -> str:
    return f"{command} check --format {fmt} --json-stdin --guards {_quote(guards_dir)}"


def claude_snippet(command: str, guards_dir: str) -> dict:
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": CLAUDE_MATCHER,
                    "hooks": [{"type": "command", "command": check_command(command, "claude", guards_dir), "timeout": 30}],
                }
            ]
        }
    }


def codex_snippet(command: str, guards_dir: str) -> dict:
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": CODEX_MATCHER,
                    "hooks": [
                        {
                            "type": "command",
                            "command": check_command(command, "codex", guards_dir),
                            "timeout": 30,
                            "statusMessage": "Checking lessons",
                        }
                    ],
                }
            ]
        }
    }


def git_script(command: str, guards_dir: str) -> str:
    return (
        "#!/bin/sh\n"
        f"{GIT_MARKER}\n"
        f"exec {command} check --event pre_commit --guards {_quote(guards_dir)}\n"
    )


def merge_hooks_json(target: Path, snippet: dict) -> bool:
    """Add our PreToolUse entry to a JSON settings file. False if already present."""
    data = {}
    if target.exists():
        text = target.read_text(encoding="utf-8").strip()
        data = json.loads(text) if text else {}
        if not isinstance(data, dict):
            raise ValueError(f"{target} does not contain a JSON object")
    hooks = data.setdefault("hooks", {})
    entries = hooks.setdefault("PreToolUse", [])
    for entry in entries:
        for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
            if MARKER in str(hook.get("command", "")):
                return False
    entries.extend(snippet["hooks"]["PreToolUse"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def write_git_hook(target: Path, script: str) -> None:
    if target.exists() and GIT_MARKER not in target.read_text(encoding="utf-8", errors="replace"):
        raise ValueError(f"{target} exists and was not written by lesson-guard; add the line by hand:\n{script}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(script, encoding="utf-8", newline="\n")
    mode = os.stat(target).st_mode
    os.chmod(target, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
