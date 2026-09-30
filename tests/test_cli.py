import io
import json
import shutil
import subprocess
import sys

import pytest

from lesson_guard.cli import main

from conftest import EXAMPLES

GUARDS = str(EXAMPLES / "guards")


def run_check(monkeypatch, capsys, payload, *args):
    raw = json.dumps(payload).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))
    code = main(["check", "--json-stdin", "--guards", GUARDS, *args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_check_claude_blocks_shell_command(monkeypatch, capsys):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": "curl -X POST http://localhost:9000/free"}}
    code, out, _ = run_check(monkeypatch, capsys, payload, "--format", "claude")
    assert code == 0
    spec = json.loads(out)["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny" and "image-server-no-free" in spec["permissionDecisionReason"]


def test_check_allows_harmless_command_silently(monkeypatch, capsys):
    code, out, err = run_check(monkeypatch, capsys, {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert (code, out, err) == (0, "", "")


def test_check_claude_write_with_secret(monkeypatch, capsys, tmp_path):
    payload = {"cwd": str(tmp_path), "tool_name": "Write",
               "tool_input": {"file_path": str(tmp_path / "dist" / "config.py"), "content": 'API_TOKEN = "Zx81Qw7Er6Ty5Ui4Op3As2Df"\n'}}
    code, out, _ = run_check(monkeypatch, capsys, payload, "--format", "claude")
    assert "no-secrets-in-files" in json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


def test_check_claude_write_normalising_line_endings(monkeypatch, capsys, tmp_path):
    target = tmp_path / "tool.py"
    target.write_bytes(b"a = 1\r\nb = 2\nc = 3\r\n")
    payload = {"cwd": str(tmp_path), "tool_name": "Write",
               "tool_input": {"file_path": str(target), "content": "a = 1\nb = 2\nc = 3\n"}}
    code, out, _ = run_check(monkeypatch, capsys, payload, "--format", "claude")
    assert "keep-mixed-line-endings" in out


def test_check_codex_apply_patch(monkeypatch, capsys):
    patch = "*** Begin Patch\n*** Add File: scripts/hello.py\n+print('GrÃ¼ezi')\n*** End Patch\n"
    code, out, _ = run_check(monkeypatch, capsys, {"tool_name": "apply_patch", "tool_input": {"command": patch}}, "--format", "codex")
    assert code == 0
    assert "no-double-encoded-utf8" in json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


def test_check_event_filter(monkeypatch, capsys):
    payload = {"tool_name": "Bash", "tool_input": {"command": "curl -X POST http://localhost:9000/free"}}
    code, out, _ = run_check(monkeypatch, capsys, payload, "--event", "pre_edit")
    assert (code, out) == (0, "")


def test_check_bad_json_fails_open_or_closed(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"{not json"), encoding="utf-8"))
    assert main(["check", "--json-stdin", "--guards", GUARDS]) == 1
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"{not json"), encoding="utf-8"))
    assert main(["check", "--json-stdin", "--guards", GUARDS, "--fail-closed"]) == 2
    assert "JSONDecodeError" in capsys.readouterr().err


def test_check_reports_invalid_guard_files(monkeypatch, capsys, tmp_path):
    (tmp_path / "broken.yaml").write_text("id: x\n", encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"tool_name": "Bash", "tool_input": {"command": "ls"}}'), encoding="utf-8"))
    assert main(["check", "--json-stdin", "--guards", str(tmp_path)]) == 1
    assert "skipped invalid guard" in capsys.readouterr().err


def test_test_command(capsys, tmp_path):
    assert main(["test", "--guards", GUARDS]) == 0
    assert "8 of 8 guard files passed" in capsys.readouterr().out
    assert main(["test", str(EXAMPLES / "guards" / "rejected")]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_list_command(capsys):
    assert main(["list", "--all", "--guards", GUARDS]) == 0
    out = capsys.readouterr().out
    assert "image-server-no-free " in out and "rejected:" in out


def test_compile_command_never_enables(capsys, tmp_path):
    out_dir = tmp_path / "g"
    code = main(["compile", str(EXAMPLES / "notes"), "--out", str(out_dir),
                 "--llm", "fake:" + str(EXAMPLES / "fake-llm" / "responses.json")])
    assert code == 0
    text = capsys.readouterr().out
    assert "8 proposed, 1 rejected" in text and "Nothing was enabled" in text
    assert not list(out_dir.glob("*.yaml"))


def test_install_prints_without_writing(capsys, tmp_path):
    for target in ("claude", "codex", "git"):
        assert main(["install", target, "--guards", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "check --format claude --json-stdin" in out
    assert "check --format codex --json-stdin" in out
    assert '"matcher": "Bash|apply_patch"' in out
    assert "check --event pre_commit" in out
    assert list(tmp_path.iterdir()) == []


def test_install_write_merges_settings(capsys, tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "x", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "other"}]}]}}), encoding="utf-8")
    assert main(["install", "claude", "--write", str(settings), "--guards", str(tmp_path)]) == 0
    assert main(["install", "claude", "--write", str(settings), "--guards", str(tmp_path)]) == 0
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["model"] == "x"
    commands = [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert commands[0] == "other" and len(commands) == 2
    assert "already registered" in capsys.readouterr().out


def test_install_git_refuses_foreign_hook(capsys, tmp_path):
    hook = tmp_path / "pre-commit"
    hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    assert main(["install", "git", "--write", str(hook), "--guards", str(tmp_path)]) == 1
    hook.unlink()
    assert main(["install", "git", "--write", str(hook), "--guards", str(tmp_path)]) == 0
    assert "installed by lesson-guard" in hook.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_pre_commit_mode_on_real_repo(capsys, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    (repo / "tool.py").write_bytes(b"a = 1\r\nb = 2\nc = 3\r\n")
    git("add", "tool.py")
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "init")

    # A harmless change passes.
    (repo / "tool.py").write_bytes(b"a = 1\r\nb = 2\nc = 4\r\n")
    git("add", "tool.py")
    assert main(["check", "--event", "pre_commit", "--guards", GUARDS, "--cwd", str(repo)]) == 0

    # Normalising the line endings is blocked, and so is a staged secret.
    (repo / "tool.py").write_bytes(b"a = 1\nb = 2\nc = 4\n")
    (repo / "dist").mkdir()
    (repo / "dist" / "conf.py").write_text('SECRET_KEY = "Pq9Lm2Xz7Rt4Vb6Nc1Ks8Hd"\n', encoding="utf-8")
    git("add", "tool.py", "dist/conf.py")
    capsys.readouterr()
    assert main(["check", "--event", "pre_commit", "--guards", GUARDS, "--cwd", str(repo / "dist")]) == 1
    err = capsys.readouterr().err
    assert "keep-mixed-line-endings" in err and "no-secrets-in-files" in err
