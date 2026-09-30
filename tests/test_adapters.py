import json

from lesson_guard.adapters import render
from lesson_guard.engine import Action, Finding


def finding(build_guard, severity="block", gid="g1"):
    return Finding(build_guard(id=gid, severity=severity, message=f"msg {gid}"), Action("pre_tool", "Bash", command="rm -rf x"))


def test_claude_deny(build_guard):
    out = render("claude", [finding(build_guard)])
    assert out.exit_code == 0
    data = json.loads(out.stdout)
    spec = data["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert "[g1]" in spec["permissionDecisionReason"] and "notes/sample.md" in spec["permissionDecisionReason"]


def test_claude_warn_does_not_set_a_decision(build_guard):
    out = render("claude", [finding(build_guard, "warn")])
    data = json.loads(out.stdout)
    assert "permissionDecision" not in data["hookSpecificOutput"]
    assert "msg g1" in data["hookSpecificOutput"]["additionalContext"]
    assert "msg g1" in data["systemMessage"]


def test_block_reason_includes_warnings(build_guard):
    out = render("claude", [finding(build_guard, "block", "a"), finding(build_guard, "warn", "b")])
    reason = json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "[a]" in reason and "[b]" in reason


def test_no_findings_is_silent():
    for fmt in ("claude", "codex"):
        out = render(fmt, [])
        assert (out.stdout, out.exit_code) == ("", 0)


def test_codex_deny_and_warn(build_guard):
    deny = json.loads(render("codex", [finding(build_guard)]).stdout)
    assert deny["hookSpecificOutput"]["permissionDecision"] == "deny"
    warn = json.loads(render("codex", [finding(build_guard, "warn")]).stdout)
    assert set(warn) == {"hookSpecificOutput", "systemMessage"}
    # Codex fails open on these fields, so they must never be emitted.
    for data in (deny, warn):
        assert "continue" not in data and "stopReason" not in data and "suppressOutput" not in data
        assert data["hookSpecificOutput"].get("permissionDecision") in (None, "deny")


def test_text_and_json_exit_codes(build_guard):
    assert render("text", [finding(build_guard)]).exit_code == 1
    assert render("text", [finding(build_guard, "warn")]).exit_code == 0
    assert render("text", []).exit_code == 0
    data = json.loads(render("json", [finding(build_guard, "warn")]).stdout)
    assert data["decision"] == "warn" and data["findings"][0]["id"] == "g1"


def test_output_is_ascii_safe(build_guard):
    f = Finding(build_guard(message="Umlaut ü und €"), Action("pre_tool", "Bash", command="rm -rf x"))
    out = render("claude", [f]).stdout
    assert out.isascii()
    assert "ü" in json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
