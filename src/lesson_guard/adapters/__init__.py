"""Output adapters. Each agent's hook protocol lives in exactly one module."""

from __future__ import annotations

from dataclasses import dataclass

from ..engine import Finding


@dataclass
class Rendered:
    stdout: str
    stderr: str
    exit_code: int


def describe(finding: Finding) -> str:
    g = finding.guard
    where = f" ({finding.action.path})" if finding.action.path else ""
    return f"[{g.id}]{where} {g.message} (lesson: {g.source})"


def summary(findings: list[Finding], blocking: bool) -> str:
    chosen = [f for f in findings if f.blocking == blocking]
    head = "lesson-guard blocked this action:" if blocking else "lesson-guard warning:"
    return "\n".join([head] + [f"- {describe(f)}" for f in chosen])


def render(fmt: str, findings: list[Finding]) -> Rendered:
    from . import claude, codex, text

    renderers = {
        "claude": claude.render,
        "codex": codex.render,
        "text": text.render,
        "json": text.render_json,
    }
    if fmt not in renderers:
        raise ValueError(f"unknown output format {fmt!r}")
    return renderers[fmt](findings)
