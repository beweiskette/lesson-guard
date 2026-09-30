"""Claude Code PreToolUse hook output.

Protocol (Claude Code hooks reference): exit 0 and print JSON on stdout.
- deny:  {"hookSpecificOutput": {"hookEventName": "PreToolUse",
          "permissionDecision": "deny", "permissionDecisionReason": "..."}}
- warn:  hookSpecificOutput.additionalContext reaches the model, systemMessage
         is shown to the user. No permissionDecision is set, so the normal
         permission flow still applies ("allow" would skip the user's prompt).
- nothing found: no output, exit 0.
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
