"""Backends that propose guards for a note.

- ClaudeBackend runs ``claude -p`` with a strict JSON schema.
- FakeBackend returns canned JSON; used by the tests and the examples, it
  never calls a model.
- The "none" backend is handled by the compiler: it writes a skeleton.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Protocol

from . import predicates as preds
from .model import EVENTS

_STR_OR_LIST = {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}
_CASE = {
    "type": "object",
    "properties": {
        "event": {"enum": list(EVENTS)},
        "tool": {"type": "string"},
        "command": {"type": "string"},
        "path": {"type": "string"},
        "content": {"type": "string"},
        "original": {"type": "string"},
        "added": {"type": "string"},
    },
    "additionalProperties": False,
}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "guards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._-]{0,63}$"},
                    "description": {"type": "string"},
                    "severity": {"enum": ["block", "warn"]},
                    "event": {"type": "array", "items": {"enum": list(EVENTS)}, "minItems": 1},
                    "match": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string"},
                            "command": _STR_OR_LIST,
                            "command_not": _STR_OR_LIST,
                            "path": _STR_OR_LIST,
                            "path_not": _STR_OR_LIST,
                            "content": _STR_OR_LIST,
                            "content_not": _STR_OR_LIST,
                            "added": _STR_OR_LIST,
                            "added_not": _STR_OR_LIST,
                            "predicates": {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "object"}]}},
                        },
                        "additionalProperties": False,
                    },
                    "message": {"type": "string"},
                    "tests": {
                        "type": "object",
                        "properties": {
                            "block": {"type": "array", "items": _CASE, "minItems": 1},
                            "pass": {"type": "array", "items": _CASE, "minItems": 1},
                        },
                        "required": ["block", "pass"],
                        "additionalProperties": False,
                    },
                },
                "required": ["id", "description", "severity", "event", "match", "message", "tests"],
                "additionalProperties": False,
            },
        },
        "skip_reason": {"type": "string"},
    },
    "required": ["guards"],
    "additionalProperties": False,
}

PROMPT_TEMPLATE = """You turn a lesson that an AI coding agent wrote down into zero or more
deterministic guards. A guard blocks or warns before the agent repeats the mistake.

Answer with JSON only, matching the given schema: {{"guards": [...], "skip_reason": "..."}}.
Return an empty "guards" list with a "skip_reason" when the lesson cannot be checked
mechanically (taste, communication style, facts about people, one-off history).

Events:
- pre_tool: before a shell command or other tool call. Fields: tool, command.
- pre_edit: before the agent writes a file. Fields: tool, path, content (file after
  the change, may be unknown), original (file before), added (text the edit adds).
- pre_commit: for every staged file in "git commit". Fields: path, content, original, added.

Matchers in "match" (all given matchers must hold; list values mean "any of"):
- tool: regex, full match on the tool name (Bash, PowerShell, Write, Edit, apply_patch, ...)
- command / command_not: Python regex searched in the command
- path / path_not: glob on the file path, "**" crosses directories, a glob without "/"
  matches the file name only
- content / content_not, added / added_not: Python regex (MULTILINE) searched in the text
- predicates: list of names, "!name" negates, or {{"name": ..., "on": content|original|added|command|any}}.
  Available: {predicates}

Rules:
- Be precise. A guard that fires on harmless actions will be switched off.
- Give at least two "block" test cases the guard must catch and at least two "pass"
  cases it must let through, including a near miss. Test cases use the same fields
  as the event (command, path, content, original, added).
- "message" tells the agent what to do instead, in one or two sentences.
- Use lowercase ids with dashes. Do not include the note source; it is added for you.

Lesson file: {source}
---
{text}
---
"""


def build_prompt(source: str, text: str) -> str:
    return PROMPT_TEMPLATE.format(source=source, text=text.strip(), predicates=", ".join(preds.ALL_NAMES))


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError("model output is not valid JSON")


def parse_response(raw: str) -> dict[str, Any]:
    """Accept the envelope of ``claude -p --output-format json`` or bare JSON."""
    data = _extract_json(raw)
    if isinstance(data, dict) and data.get("is_error"):
        raise LLMError(f"model call failed: {data.get('result') or data.get('subtype')}")
    if isinstance(data, dict) and "guards" not in data:
        if isinstance(data.get("structured_output"), dict):
            data = data["structured_output"]
        elif isinstance(data.get("result"), str):
            data = _extract_json(data["result"])
    if not isinstance(data, dict) or not isinstance(data.get("guards"), list):
        raise LLMError('model output lacks a "guards" list')
    return data


class Backend(Protocol):
    name: str

    def propose(self, source: str, text: str) -> dict[str, Any]: ...


Runner = Callable[..., subprocess.CompletedProcess]


class ClaudeBackend:
    """Calls the Claude Code CLI in print mode. This costs tokens."""

    name = "claude"

    def __init__(self, model: str | None = None, executable: str = "claude", runner: Runner = subprocess.run, timeout: int = 300):
        self.model = model
        self.executable = executable
        self.runner = runner
        self.timeout = timeout

    def argv(self) -> list[str]:
        exe = shutil.which(self.executable) or self.executable
        args = [
            exe, "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(RESPONSE_SCHEMA, separators=(",", ":")),
            "--tools", "",
            "--no-session-persistence",
        ]
        if self.model:
            args += ["--model", self.model]
        return args

    def propose(self, source: str, text: str) -> dict[str, Any]:
        try:
            proc = self.runner(
                self.argv(),
                input=build_prompt(source, text).encode("utf-8"),
                capture_output=True,
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise LLMError(f"{self.executable!r} not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError("claude -p timed out") from exc
        out = proc.stdout.decode("utf-8", errors="replace") if isinstance(proc.stdout, bytes) else proc.stdout
        if proc.returncode != 0 and not out.strip():
            err = proc.stderr.decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else proc.stderr
            raise LLMError(f"claude -p exited with {proc.returncode}: {err.strip()[:500]}")
        return parse_response(out)


class FakeBackend:
    """Returns canned responses keyed by note source path or file stem."""

    name = "fake"

    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.calls: list[str] = []
        self.texts: list[str] = []

    @classmethod
    def from_file(cls, path: str | Path) -> "FakeBackend":
        with open(path, "r", encoding="utf-8") as handle:
            return cls(json.load(handle))

    def propose(self, source: str, text: str) -> dict[str, Any]:
        self.calls.append(source)
        self.texts.append(text)
        stem = Path(source).stem
        for key in (source, source.replace("\\", "/"), stem):
            if key in self.responses:
                value = self.responses[key]
                raw = value if isinstance(value, str) else json.dumps(value)
                return parse_response(raw)
        return {"guards": [], "skip_reason": "no canned response for this note"}
