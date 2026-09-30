"""Turn agent hook payloads (Claude Code, Codex) into Actions.

Both agents send ``tool_name`` and ``tool_input`` on stdin for PreToolUse.
Tool names do not collide, so one normaliser serves both.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any, Callable

from .engine import Action

SHELL_TOOLS = {"Bash", "PowerShell", "shell", "local_shell", "exec_command", "unified_exec"}
MAX_READ_BYTES = 2_000_000

FileReader = Callable[[str], "str | None"]


def decode(data: bytes) -> str:
    """Decode file bytes as UTF-8 without touching line endings."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace")


def read_text_file(path: str) -> str | None:
    """Read a text file for comparison. Missing, huge or binary files give None."""
    try:
        p = Path(path)
        if not p.is_file() or p.stat().st_size > MAX_READ_BYTES:
            return None
        data = p.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:8192]:
        return None
    return decode(data)


def _resolve(path: str, cwd: str | None) -> str:
    if cwd and not os.path.isabs(path):
        return os.path.join(cwd, path)
    return path


def _first_str(mapping: dict, *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str):
            return value
    return None


def _apply_edits(original: str | None, edits: list[tuple[str, str, bool]]) -> str | None:
    """Apply Claude-style string replacements. None when any edit does not apply."""
    if original is None:
        return None
    text = original
    for old, new, replace_all in edits:
        if not old or old not in text:
            return None
        text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    return text


def _edit_list(tool_input: dict) -> list[dict]:
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        return [e for e in edits if isinstance(e, dict)]
    return [tool_input]


def _edit_actions(tool: str, tool_input: dict, cwd: str | None, reader: FileReader) -> list[Action]:
    default_path = _first_str(tool_input, "file_path", "path", "notebook_path")
    per_file: dict[str, list[tuple[str, str, bool]]] = {}
    for edit in _edit_list(tool_input):
        path = _first_str(edit, "file_path", "path") or default_path
        if not path:
            continue
        old = _first_str(edit, "old_string", "old_text") or ""
        new = _first_str(edit, "new_string", "new_text") or ""
        per_file.setdefault(path, []).append((old, new, bool(edit.get("replace_all"))))
    actions = []
    for path, edits in per_file.items():
        original = reader(_resolve(path, cwd))
        actions.append(
            Action(
                event="pre_edit",
                tool=tool,
                path=path,
                original=original,
                content=_apply_edits(original, edits),
                added="\n".join(new for _, new, _ in edits),
            )
        )
    return actions


def parse_apply_patch(text: str) -> list[dict[str, Any]]:
    """Parse the Codex apply_patch format into per-file changes.

    Returns dicts with keys: op ("add", "update", "delete"), path, move_to,
    content (for add), added (lines with "+").
    """
    start = text.find("*** Begin Patch")
    lines = text[start:].splitlines() if start >= 0 else text.splitlines()
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        if line.startswith("*** Add File: "):
            current = {"op": "add", "path": line[len("*** Add File: "):].strip(), "added": [], "move_to": None}
            files.append(current)
        elif line.startswith("*** Update File: "):
            current = {"op": "update", "path": line[len("*** Update File: "):].strip(), "added": [], "move_to": None}
            files.append(current)
        elif line.startswith("*** Delete File: "):
            current = {"op": "delete", "path": line[len("*** Delete File: "):].strip(), "added": [], "move_to": None}
            files.append(current)
        elif line.startswith("*** Move to: ") and current is not None:
            current["move_to"] = line[len("*** Move to: "):].strip()
        elif line.startswith("*** End Patch"):
            current = None
        elif line.startswith("***"):
            continue
        elif current is not None and line.startswith("+"):
            current["added"].append(line[1:])
    for change in files:
        change["added"] = "\n".join(change["added"])
        change["content"] = change["added"] + "\n" if change["op"] == "add" and change["added"] else None
        if change["op"] == "add" and change["content"] is None:
            change["content"] = ""
    return files


def _apply_patch_actions(tool: str, tool_input: Any, cwd: str | None, reader: FileReader) -> list[Action]:
    if isinstance(tool_input, dict):
        text = _first_str(tool_input, "command", "patch", "input") or ""
    else:
        text = str(tool_input or "")
    actions = []
    for change in parse_apply_patch(text):
        original = None if change["op"] == "add" else reader(_resolve(change["path"], cwd))
        # Content after an update is not reconstructed: hunks alone do not say
        # which line endings the patched file will have.
        actions.append(
            Action(
                event="pre_edit",
                tool=tool,
                path=change["move_to"] or change["path"],
                original=original,
                content=change["content"],
                added=change["added"],
            )
        )
    return actions


def _command_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return shlex.join(value)
    return None


def actions_from_payload(payload: dict, reader: FileReader = read_text_file) -> tuple[list[Action], str | None]:
    """Normalise a PreToolUse hook payload. Returns (actions, cwd)."""
    if not isinstance(payload, dict):
        raise ValueError("hook payload must be a JSON object")
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input")
    if tool_input is None:
        tool_input = {}

    if tool == "apply_patch":
        return _apply_patch_actions(tool, tool_input, cwd, reader), cwd

    if not isinstance(tool_input, dict):
        return [Action(event="pre_tool", tool=tool, command=str(tool_input))], cwd

    if tool in SHELL_TOOLS:
        command = _command_text(tool_input.get("command")) or _command_text(tool_input.get("cmd"))
        # Codex may still route an apply_patch through the shell tool.
        if command and "*** Begin Patch" in command:
            return _apply_patch_actions(tool, command, cwd, reader), cwd
        return [Action(event="pre_tool", tool=tool, command=command or "")], cwd

    if tool == "Write":
        path = _first_str(tool_input, "file_path", "path")
        content = _first_str(tool_input, "content", "file_text")
        if path is None:
            return [], cwd
        original = reader(_resolve(path, cwd))
        return [Action(event="pre_edit", tool=tool, path=path, content=content, original=original, added=content)], cwd

    if tool in ("Edit", "MultiEdit"):
        return _edit_actions(tool, tool_input, cwd, reader), cwd

    if tool == "NotebookEdit":
        path = _first_str(tool_input, "notebook_path", "file_path")
        if path is None:
            return [], cwd
        added = _first_str(tool_input, "new_source") or ""
        return [Action(event="pre_edit", tool=tool, path=path, original=reader(_resolve(path, cwd)), added=added)], cwd

    # Any other tool (MCP, web fetch, ...): guards see the serialised input.
    serialised = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
    return [Action(event="pre_tool", tool=tool, command=serialised)], cwd
