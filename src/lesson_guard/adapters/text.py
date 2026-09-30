"""Plain text output for terminals and git hooks, plus a neutral JSON form.

Exit code 1 when anything blocks, so a git pre-commit hook stops the commit.
"""

from __future__ import annotations

import json

from ..engine import Finding
from . import Rendered, summary


def render(findings: list[Finding]) -> Rendered:
    parts = []
    if any(f.blocking for f in findings):
        parts.append(summary(findings, blocking=True))
    if any(not f.blocking for f in findings):
        parts.append(summary(findings, blocking=False))
    blocked = any(f.blocking for f in findings)
    return Rendered("", "\n".join(parts), 1 if blocked else 0)


def render_json(findings: list[Finding]) -> Rendered:
    items = [
        {
            "id": f.guard.id,
            "severity": f.guard.severity,
            "event": f.action.event,
            "tool": f.action.tool,
            "path": f.action.path,
            "message": f.guard.message,
            "source": f.guard.source,
        }
        for f in findings
    ]
    blocked = any(f.blocking for f in findings)
    out = {"decision": "block" if blocked else ("warn" if findings else "allow"), "findings": items}
    return Rendered(json.dumps(out, ensure_ascii=True, indent=2), "", 1 if blocked else 0)
