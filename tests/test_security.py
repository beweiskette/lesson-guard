"""Review findings: notes forwarded unfiltered, secret files committed.

Secret-like test values are assembled at runtime so that no string in this
repository looks like a real credential to secret scanners.
"""

import io
import json
import shutil
import subprocess
import sys

import pytest
import yaml

from lesson_guard.cli import main
from lesson_guard.compiler import compile_notes
from lesson_guard.engine import Action, evaluate
from lesson_guard.llm import ClaudeBackend, FakeBackend
from lesson_guard.model import guard_from_dict, load_guards
from lesson_guard.redact import redact

from conftest import EXAMPLES

GUARDS = str(EXAMPLES / "guards")

OPENAI = "sk" + "-proj-" + "Q7wE3rT9yU1iO5pA2sD8fG4h"
GITHUB = "gh" + "p_" + "Z3xC7vB1nM5qW9eR2tY6uI0oP4aS8dF1gH5j"
AWS = "AK" + "IA" + "QWERTYUIOP234567"
BEARER = "eyZ9" + "kLm3Nq7Rs1Tu5Vw9Xy2Ab"
PASSWORD = "Tr0ub4dor" + "-and-3"
TOKEN = "t0k" + "en4Lz8Mx2Nc6Vb"
HIGH_ENTROPY = "Hq7" + "Zp2Lk9Wm4Xr8Tn3Vb6Yc1Jd5Fs0Ga7Pe"
KEY_BODY = "MIIEowIBAAKCAQEA7" + "vQx9Lm2Pz"
PRIVATE_KEY = (
    "-----BEGIN " + "RSA PRIVATE KEY-----\n" + KEY_BODY + "\nq2W3e4R5t6Y7\n-----END " + "RSA PRIVATE KEY-----"
)
SECRETS = [OPENAI, GITHUB, AWS, BEARER, PASSWORD, TOKEN, HIGH_ENTROPY, KEY_BODY]

LEAKY_NOTE = f"""---
name: Deploy key handling
type: feedback
---
Never paste keys into the deploy script. Last time it had {OPENAI} and {GITHUB}
and the AWS id {AWS} in plain text.

The curl call used `Authorization: Bearer {BEARER}`.
Config had password={PASSWORD} and token: {TOKEN}
and a random blob {HIGH_ENTROPY} in the log.

{PRIVATE_KEY}

Always read them from the environment instead.
"""


def assert_clean(text):
    for secret in SECRETS:
        assert secret not in text, secret


# --------------------------------------------------------------- redaction


def test_redact_removes_every_secret_kind():
    result = redact(LEAKY_NOTE)
    assert_clean(result.text)
    kinds = {r.kind for r in result.redactions}
    assert {"provider-key", "bearer", "assignment", "high-entropy", "private-key"} <= kinds
    # The surrounding prose survives.
    assert "Never paste keys into the deploy script." in result.text
    assert "Authorization: Bearer [REDACTED" in result.text
    assert "password=[REDACTED" in result.text


def test_redact_keeps_placeholders_and_prose():
    text = (
        "API_KEY=your-api-key-here\n"
        'API_KEY = os.environ["API_KEY"]\n'
        "The token: never commit it.\n"
        "Commit 9f9911fa3b2c4d5e6f7a8b9c0d1e2f3a4b5c6d7e is fine.\n"
    )
    result = redact(text)
    assert result.text == text
    assert result.redactions == []


# ------------------------------------------------------------ compile path


@pytest.fixture
def leaky_notes(tmp_path):
    d = tmp_path / "notes"
    d.mkdir()
    (d / "deploy-keys.md").write_text(LEAKY_NOTE, encoding="utf-8")
    (d / "private-rule.md").write_text(
        "---\nname: Private\ntype: feedback\nprivate: true\n---\nNever mention the client Example Corp.\n",
        encoding="utf-8",
    )
    (d / "ignored.md").write_text("---\ntype: feedback\n---\nNever do the ignored thing.\n", encoding="utf-8")
    (d / ".lesson-guard-ignore").write_text("# notes that stay local\nignored.md\n", encoding="utf-8")
    return d


def test_compile_sends_only_redacted_text(leaky_notes, tmp_path):
    backend = FakeBackend({})
    report = compile_notes(leaky_notes, tmp_path / "g", backend)
    assert backend.calls == ["notes/deploy-keys.md"]
    assert_clean(backend.texts[0])
    assert "[REDACTED:" in backend.texts[0]
    skipped = dict(report.skipped)
    assert "private" in skipped["notes/private-rule.md"]
    assert "excluded" in skipped["notes/ignored.md"]


def test_claude_backend_receives_redacted_prompt(leaky_notes, tmp_path):
    captured = []

    def fake_run(argv, input, capture_output, timeout):
        captured.append(input.decode("utf-8"))
        body = json.dumps({"guards": [], "skip_reason": "x"})
        return subprocess.CompletedProcess(argv, 0, body.encode("utf-8"), b"")

    compile_notes(leaky_notes, tmp_path / "g", ClaudeBackend(runner=fake_run))
    assert len(captured) == 1
    assert_clean(captured[0])
    assert "Example Corp" not in captured[0]


def test_drafts_do_not_copy_secrets_or_private_notes(leaky_notes, tmp_path):
    out = tmp_path / "g"
    report = compile_notes(leaky_notes, out, None)
    assert [gid for gid, _ in report.drafts] == ["deploy-keys"]
    assert_clean((out / "drafts" / "deploy-keys.yaml").read_text(encoding="utf-8"))


def test_proposal_echoing_a_redacted_value_is_rejected(leaky_notes, tmp_path):
    guard = {
        "id": "no-leaked-key",
        "description": "d",
        "severity": "block",
        "event": ["pre_tool"],
        "match": {"command": "deploy"},
        "message": f"Do not use {GITHUB}.",
        "tests": {"block": [{"command": f"deploy {TOKEN}"}], "pass": [{"command": "ls"}]},
    }
    out = tmp_path / "g"
    report = compile_notes(leaky_notes, out, FakeBackend({"deploy-keys": {"guards": [guard]}}))
    assert report.proposed == []
    [(gid, path, reasons)] = report.rejected
    assert any("redacted" in r for r in reasons)
    assert_clean(path.read_text(encoding="utf-8"))


def test_cli_lists_notes_before_sending(leaky_notes, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(ClaudeBackend, "propose", lambda self, s, t: {"guards": [], "skip_reason": "x"})
    assert main(["compile", str(leaky_notes), "--out", str(tmp_path / "g"), "--llm", "claude"]) == 0
    out = capsys.readouterr().out
    assert "send      notes/deploy-keys.md" in out
    assert "redacted" in out
    assert "send      notes/private-rule.md" not in out
    assert_clean(out)


def test_cli_dry_run_shows_payload_and_calls_nothing(leaky_notes, tmp_path, capsys, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("dry run must not call the model")

    monkeypatch.setattr(ClaudeBackend, "propose", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    out_dir = tmp_path / "g"
    assert main(["compile", str(leaky_notes), "--out", str(out_dir), "--llm", "claude", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Lesson file: notes/deploy-keys.md" in out  # the exact prompt
    assert "[REDACTED:" in out and "Never paste keys" in out
    assert "Example Corp" not in out
    assert_clean(out)
    assert not out_dir.exists()


# ------------------------------------------------------------- check output


def _leaky_guard():
    return guard_from_dict({
        "id": "leaky",
        "source": "notes/x.md",
        "description": "d",
        "severity": "block",
        "event": ["pre_edit"],
        "match": {"path": "**/*.txt"},
        "message": f"Use the key {GITHUB} instead.",
        "tests": {"block": [{"path": "a/b.txt", "content": "x"}], "pass": [{"path": "a.md", "content": "x"}]},
    })


@pytest.mark.parametrize("fmt", ["claude", "codex", "text", "json"])
def test_check_output_never_echoes_secret_like_values(fmt):
    from lesson_guard.adapters import render

    action = Action(event="pre_edit", tool="Write", path=f"tmp/{TOKEN}/{HIGH_ENTROPY}.txt", content="x")
    findings = evaluate([_leaky_guard()], [action])
    assert findings
    result = render(fmt, findings)
    assert_clean(result.stdout + result.stderr)
    assert "leaky" in result.stdout + result.stderr


# ---------------------------------------------------------- secret files


def _pre(event, path, content="X=1\n"):
    return Action(event=event, tool="Write" if event == "pre_edit" else "git", path=path, content=content, added=content)


@pytest.mark.parametrize("event", ["pre_edit", "pre_commit"])
@pytest.mark.parametrize("path", [".env", "app/.env.local", ".env.production", "certs/server.pem", "id_rsa", "deploy/id_ed25519"])
def test_shipped_guards_block_secret_files(event, path):
    guards = load_guards(GUARDS)[0]
    fired = {f.guard.id for f in evaluate(guards, [_pre(event, path, "PLAIN=1\n")])}
    assert "no-secret-files" in fired, (event, path)


@pytest.mark.parametrize("path", [".env.example", "app/.env.sample", ".env.template", "id_rsa.pub", "docs/env.md"])
def test_shipped_guards_allow_templates(path):
    guards = load_guards(GUARDS)[0]
    fired = {f.guard.id for f in evaluate(guards, [_pre("pre_commit", path, "API_KEY=your-api-key-here\n")])}
    assert "no-secret-files" not in fired


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_committing_env_file_is_blocked(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    (repo / ".env.example").write_text("API_KEY=your-api-key-here\n", encoding="utf-8")
    git("add", ".env.example")
    assert main(["check", "--event", "pre_commit", "--guards", GUARDS, "--cwd", str(repo)]) == 0

    (repo / ".env").write_text("API_KEY=short\n", encoding="utf-8")
    git("add", ".env")
    capsys.readouterr()
    assert main(["check", "--event", "pre_commit", "--guards", GUARDS, "--cwd", str(repo)]) == 1
    assert "no-secret-files" in capsys.readouterr().err
