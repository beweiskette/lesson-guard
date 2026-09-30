"""Guard file format: loading and validation.

A guard is a small YAML document. See README.md for the full format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import predicates as preds

EVENTS = ("pre_tool", "pre_edit", "pre_commit")
SEVERITIES = ("block", "warn")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

TOP_LEVEL_KEYS = {"id", "source", "description", "severity", "event", "match", "message", "tests"}
REGEX_KEYS = ("command", "command_not", "content", "content_not", "added", "added_not")
GLOB_KEYS = ("path", "path_not")
MATCH_KEYS = {"tool", "predicates", *REGEX_KEYS, *GLOB_KEYS}
CASE_KEYS = {"event", "tool", "command", "path", "content", "original", "added"}

DEFAULT_TOOL = {"pre_tool": "Bash", "pre_edit": "Write", "pre_commit": "git"}


class GuardError(ValueError):
    """Raised when a guard document is invalid."""

    def __init__(self, errors: list[str], origin: str = ""):
        self.errors = errors
        self.origin = origin
        prefix = f"{origin}: " if origin else ""
        super().__init__(prefix + "; ".join(errors))


@dataclass(frozen=True)
class PredicateSpec:
    name: str
    on: str
    negate: bool = False


@dataclass
class TestCase:
    event: str
    tool: str
    command: str | None = None
    path: str | None = None
    content: str | None = None
    original: str | None = None
    added: str | None = None


@dataclass
class Guard:
    id: str
    source: str
    description: str
    severity: str
    events: tuple[str, ...]
    message: str
    tool: re.Pattern[str] | None = None
    regex: dict[str, list[re.Pattern[str]]] = field(default_factory=dict)
    globs: dict[str, list[str]] = field(default_factory=dict)
    predicates: list[PredicateSpec] = field(default_factory=list)
    block_tests: list[TestCase] = field(default_factory=list)
    pass_tests: list[TestCase] = field(default_factory=list)
    origin: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_predicate(item: Any, errors: list[str]) -> PredicateSpec | None:
    if isinstance(item, str):
        negate = item.startswith("!")
        name = item[1:] if negate else item
        spec = {"name": name, "negate": negate}
    elif isinstance(item, dict):
        spec = item
        unknown = set(spec) - {"name", "on", "negate"}
        if unknown:
            errors.append(f"predicate has unknown keys: {sorted(unknown)}")
    else:
        errors.append(f"predicate must be a string or mapping, got {item!r}")
        return None
    name = spec.get("name")
    if name not in preds.ALL_NAMES:
        errors.append(f"unknown predicate {name!r}; known: {', '.join(preds.ALL_NAMES)}")
        return None
    if name in preds.RELATIONAL:
        on = "original+content"
        if "on" in spec:
            errors.append(f"predicate {name!r} compares original and content and takes no 'on'")
    else:
        on = spec.get("on", preds.UNARY[name][1])
        if on not in preds.TARGETS:
            errors.append(f"predicate {name!r}: 'on' must be one of {preds.TARGETS}")
            return None
    negate = spec.get("negate", False)
    if not isinstance(negate, bool):
        errors.append(f"predicate {name!r}: 'negate' must be true or false")
        negate = False
    return PredicateSpec(name=name, on=on, negate=negate)


def _parse_case(item: Any, default_event: str, events: tuple[str, ...], errors: list[str], label: str) -> TestCase | None:
    if not isinstance(item, dict):
        errors.append(f"{label}: test case must be a mapping")
        return None
    unknown = set(item) - CASE_KEYS
    if unknown:
        errors.append(f"{label}: unknown keys {sorted(unknown)}")
    event = item.get("event", default_event)
    if event not in events:
        errors.append(f"{label}: event {event!r} is not one of the guard's events {list(events)}")
        return None
    for key in ("tool", "command", "path", "content", "original", "added"):
        if key in item and item[key] is not None and not isinstance(item[key], str):
            errors.append(f"{label}: {key!r} must be a string")
            return None
    case = TestCase(
        event=event,
        tool=item.get("tool") or DEFAULT_TOOL[event],
        command=item.get("command"),
        path=item.get("path"),
        content=item.get("content"),
        original=item.get("original"),
        added=item.get("added"),
    )
    # Same as a full-file write: what is added is the whole new content.
    if case.added is None and case.content is not None and event != "pre_tool":
        case.added = case.content
    return case


def guard_from_dict(data: Any, origin: str = "") -> Guard:
    """Build a Guard from a parsed YAML document, raising GuardError on problems."""
    errors: list[str] = []
    if not isinstance(data, dict):
        raise GuardError(["guard document must be a mapping"], origin)

    unknown = set(data) - TOP_LEVEL_KEYS
    if unknown:
        errors.append(f"unknown top-level keys: {sorted(unknown)}")

    gid = data.get("id")
    if not isinstance(gid, str) or not ID_RE.match(gid):
        errors.append("id must match ^[a-z0-9][a-z0-9._-]{0,63}$")
        gid = str(gid)

    for key in ("source", "description", "message"):
        if not isinstance(data.get(key), str) or not data.get(key, "").strip():
            errors.append(f"{key!r} must be a non-empty string")

    severity = data.get("severity", "block")
    if severity not in SEVERITIES:
        errors.append(f"severity must be one of {SEVERITIES}")

    events = tuple(_as_list(data.get("event")))
    if not events or any(e not in EVENTS for e in events):
        errors.append(f"event must be one or more of {EVENTS}")
        events = tuple(e for e in events if e in EVENTS) or ("pre_tool",)

    match = data.get("match") or {}
    if not isinstance(match, dict):
        errors.append("match must be a mapping")
        match = {}
    unknown = set(match) - MATCH_KEYS
    if unknown:
        errors.append(f"match has unknown keys: {sorted(unknown)}")

    tool = None
    if match.get("tool") is not None:
        try:
            tool = re.compile(str(match["tool"]))
        except re.error as exc:
            errors.append(f"match.tool is not a valid regex: {exc}")

    regex: dict[str, list[re.Pattern[str]]] = {}
    for key in REGEX_KEYS:
        patterns = []
        for pattern in _as_list(match.get(key)):
            if not isinstance(pattern, str):
                errors.append(f"match.{key} must be a string or a list of strings")
                continue
            try:
                patterns.append(re.compile(pattern, re.MULTILINE))
            except re.error as exc:
                errors.append(f"match.{key}: invalid regex {pattern!r}: {exc}")
        if patterns:
            regex[key] = patterns

    globs: dict[str, list[str]] = {}
    for key in GLOB_KEYS:
        values = _as_list(match.get(key))
        if any(not isinstance(v, str) or not v for v in values):
            errors.append(f"match.{key} must be a glob or a list of globs")
            continue
        if values:
            globs[key] = values

    predicate_specs = []
    for item in _as_list(match.get("predicates")):
        spec = _parse_predicate(item, errors)
        if spec:
            predicate_specs.append(spec)

    positive = tool is not None or any(k in regex for k in ("command", "content", "added")) or "path" in globs or predicate_specs
    if not positive:
        errors.append("match needs at least one positive matcher (tool, command, path, content, added or predicates)")
    if "pre_tool" in events and ("path" in globs or "content" in regex):
        # Not an error in itself, but such a guard can never fire for shell commands.
        if len(events) == 1:
            errors.append("a pre_tool guard sees commands, not paths or file content")

    tests = data.get("tests") or {}
    if not isinstance(tests, dict):
        errors.append("tests must be a mapping with 'block' and 'pass' lists")
        tests = {}
    unknown = set(tests) - {"block", "pass"}
    if unknown:
        errors.append(f"tests has unknown keys: {sorted(unknown)}")
    block_tests: list[TestCase] = []
    pass_tests: list[TestCase] = []
    for kind, bucket in (("block", block_tests), ("pass", pass_tests)):
        items = tests.get(kind) or []
        if not isinstance(items, list):
            errors.append(f"tests.{kind} must be a list")
            continue
        for index, item in enumerate(items):
            case = _parse_case(item, events[0], events, errors, f"tests.{kind}[{index}]")
            if case:
                bucket.append(case)
    if not block_tests:
        errors.append("tests.block needs at least one example that must be caught")
    if not pass_tests:
        errors.append("tests.pass needs at least one example that must pass")

    if errors:
        raise GuardError(errors, origin)

    return Guard(
        id=gid,
        source=data["source"].strip(),
        description=data["description"].strip(),
        severity=severity,
        events=events,
        message=data["message"].strip(),
        tool=tool,
        regex=regex,
        globs=globs,
        predicates=predicate_specs,
        block_tests=block_tests,
        pass_tests=pass_tests,
        origin=origin,
        raw=data,
    )


def read_yaml(path: Path) -> Any:
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return yaml.safe_load(handle)


def load_guard(path: str | Path) -> Guard:
    path = Path(path)
    try:
        data = read_yaml(path)
    except yaml.YAMLError as exc:
        raise GuardError([f"invalid YAML: {exc}"], str(path)) from exc
    return guard_from_dict(data, origin=str(path))


def guard_files(directory: str | Path) -> list[Path]:
    """Guard files directly inside directory (not in proposed/, rejected/, drafts/)."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix in (".yaml", ".yml"))


def load_guards(directory: str | Path) -> tuple[list[Guard], list[GuardError]]:
    """Load all guards in a directory. Invalid files are returned as errors."""
    guards: list[Guard] = []
    problems: list[GuardError] = []
    seen: set[str] = set()
    for path in guard_files(directory):
        try:
            guard = load_guard(path)
        except GuardError as exc:
            problems.append(exc)
            continue
        if guard.id in seen:
            problems.append(GuardError([f"duplicate guard id {guard.id!r}"], str(path)))
            continue
        seen.add(guard.id)
        guards.append(guard)
    return guards, problems


class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    # Multi-line text reads best as a literal block. Text with CR must stay
    # double-quoted so that "\r\n" survives the round trip.
    if "\n" in value and "\r" not in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_Dumper.add_representer(str, _str_representer)


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100, default_flow_style=False)
