"""Codex CLI PreToolUse hook output.

Protocol (Codex hooks documentation): exit 0 and print JSON on stdout.
- deny:  {"hookSpecificOutput": {"hookEventName": "PreToolUse",
          "permissionDecision": "deny", "permissionDecisionReason": "..."}}
- warn:  hookSpecificOutput.additionalContext (model-visible) and
         systemMessage (shown as a warning).
Codex parses but does not support permissionDecision "ask", continue,
stopReason or suppressOutput for PreToolUse and marks such hook runs as
failed, so this adapter never emits them.
"""

from __future__ import annotations

import json

from ..engine import Finding
from . import Rendered, summary


def render(findings: list[Finding]) -> Rendered:
    blocks = [f for f in findings if f.blocking]
    warns = [f for f in findings if not f.blocking]
    if blocks:
        reason = summary(findings, blocking=True)
        if warns:
            reason += "\n" + summary(findings, blocking=False)
        out = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
        return Rendered(json.dumps(out, ensure_ascii=True), "", 0)
    if warns:
        text = summary(findings, blocking=False)
        out = {
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text},
            "systemMessage": text,
        }
        return Rendered(json.dumps(out, ensure_ascii=True), "", 0)
    return Rendered("", "", 0)
