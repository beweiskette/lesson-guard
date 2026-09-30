"""Evaluate guards against normalised agent actions."""

from __future__ import annotations

from dataclasses import dataclass

from . import paths
from . import predicates as preds
from .model import Guard, PredicateSpec, TestCase


@dataclass
class Action:
    """One thing an agent is about to do, independent of the agent.

    event:    pre_tool, pre_edit or pre_commit
    tool:     tool name as reported by the agent (Bash, Write, apply_patch, ...)
    command:  shell command, or the serialised input of a non-shell tool
    path:     target file of an edit or staged file of a commit
    content:  full file content after the change, when known
    original: file content before the change, when known
    added:    text the change adds (new_string, "+" lines, or the full write)
    """

    event: str
    tool: str = ""
    command: str | None = None
    path: str | None = None
    content: str | None = None
    original: str | None = None
    added: str | None = None


@dataclass
class Finding:
    guard: Guard
    action: Action

    @property
    def blocking(self) -> bool:
        return self.guard.severity == "block"


def action_from_case(case: TestCase) -> Action:
    return Action(
        event=case.event,
        tool=case.tool,
        command=case.command,
        path=case.path,
        content=case.content,
        original=case.original,
        added=case.added,
    )


def _any_search(patterns, text: str | None) -> bool:
    if text is None:
        return False
    return any(p.search(text) for p in patterns)


def _predicate_holds(spec: PredicateSpec, action: Action) -> bool:
    if spec.name in preds.RELATIONAL:
        result = preds.RELATIONAL[spec.name](action.original, action.content)
    else:
        func = preds.UNARY[spec.name][0]
        if spec.on == "any":
            texts = [action.content, action.original, action.added, action.command]
        else:
            texts = [getattr(action, spec.on)]
        result = any(func(t) for t in texts if t is not None)
    return not result if spec.negate else result


def matches(guard: Guard, action: Action, cwd: str | None = None) -> bool:
    """True when every matcher of the guard accepts the action."""
    if action.event not in guard.events:
        return False
    if guard.tool is not None and not guard.tool.fullmatch(action.tool or ""):
        return False

    for key in ("command", "content", "added"):
        if key in guard.regex and not _any_search(guard.regex[key], getattr(action, key)):
            return False
        negative = key + "_not"
        if negative in guard.regex and _any_search(guard.regex[negative], getattr(action, key)):
            return False

    if "path" in guard.globs or "path_not" in guard.globs:
        norm = paths.normalize(action.path, cwd) if action.path else None
        if "path" in guard.globs:
            if norm is None or not any(paths.glob_match(g, norm) for g in guard.globs["path"]):
                return False
        if "path_not" in guard.globs and norm is not None:
            if any(paths.glob_match(g, norm) for g in guard.globs["path_not"]):
                return False

    return all(_predicate_holds(spec, action) for spec in guard.predicates)


def evaluate(guards: list[Guard], actions: list[Action], cwd: str | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for action in actions:
        for guard in guards:
            if matches(guard, action, cwd):
                findings.append(Finding(guard=guard, action=action))
    return findings
