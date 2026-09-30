import json
import subprocess
from pathlib import Path

import pytest
import yaml

from lesson_guard.compiler import actionable, compile_notes, read_notes, split_front_matter
from lesson_guard.llm import RESPONSE_SCHEMA, ClaudeBackend, FakeBackend, LLMError, build_prompt, parse_response
from lesson_guard.model import load_guard, load_guards
from lesson_guard.store import StoreError, disable, enable

from conftest import EXAMPLES

# "\b" after --force would also match --force-with-lease; the guard's own
# pass tests caught exactly that while writing this file.
FORCE_PUSH = r"git\s+push\b.*\s(-f|--force)(\s|$)"


def good_guard(gid="no-force-push"):
    return {
        "id": gid,
        "description": "no force push",
        "severity": "block",
        "event": ["pre_tool"],
        "match": {"command": FORCE_PUSH},
        "message": "Do not force push; open a new branch instead.",
        "tests": {
            "block": [{"command": "git push --force"}, {"command": "git push -f origin main"}],
            "pass": [{"command": "git push"}, {"command": "git push --force-with-lease"}],
        },
    }


def bad_guard():
    g = good_guard("too-broad")
    g["match"] = {"command": "push"}
    return g


@pytest.fixture
def notes(tmp_path):
    d = tmp_path / "notes"
    d.mkdir()
    (d / "force-push.md").write_text(
        "---\nname: Force push\ndescription: never force push\ntype: feedback\n---\nNever force push to shared branches.\n",
        encoding="utf-8",
    )
    (d / "style.md").write_text("---\ntype: user\n---\nLikes tables.\n", encoding="utf-8")
    (d / "sub").mkdir()
    (d / "sub" / "history.md").write_text("We moved the server in spring.\n", encoding="utf-8")
    return d


def test_front_matter():
    meta, body = split_front_matter("---\nname: x\ntype: feedback\n---\nbody\n")
    assert meta == {"name": "x", "type": "feedback"} and body == "body\n"
    assert split_front_matter("no front matter") == ({}, "no front matter")
    assert split_front_matter("---\n: [broken\n---\nb")[0] == {}


def test_actionable_heuristic(notes):
    by_stem = {n.stem: n for n in read_notes(notes)}
    assert actionable(by_stem["force-push"])[0]
    assert not actionable(by_stem["style"])[0]
    assert not actionable(by_stem["history"])[0]


def test_source_labels_are_never_absolute(notes):
    for note in read_notes(notes):
        assert not Path(note.source).is_absolute()
        assert note.source.startswith("notes/")


def test_compile_with_fake_llm_proposes_and_rejects(notes, tmp_path):
    backend = FakeBackend({"force-push": {"guards": [good_guard(), bad_guard()]}})
    out = tmp_path / "guards"
    report = compile_notes(notes, out, backend)

    assert backend.calls == ["notes/force-push.md"]  # non-actionable notes never reach the model
    assert [gid for gid, _ in report.proposed] == ["no-force-push"]
    assert [gid for gid, _, _ in report.rejected] == ["too-broad"]
    assert "should pass but was caught" in report.rejected[0][2][0]
    assert {s for s, _ in report.skipped} == {"notes/style.md", "notes/sub/history.md"}

    # Nothing becomes active by compiling.
    assert load_guards(out) == ([], [])
    proposed = load_guard(out / "proposed" / "no-force-push.yaml")
    assert proposed.source == "notes/force-push.md"
    rejected = yaml.safe_load((out / "rejected" / "too-broad.yaml").read_text(encoding="utf-8"))
    assert rejected["rejected_reasons"]


def test_model_cannot_set_source_or_skip_tests(notes, tmp_path):
    g = good_guard()
    g["source"] = "/somewhere/else.md"
    no_tests = good_guard("no-tests")
    no_tests["tests"] = {"block": [], "pass": []}
    invalid = {"id": "Bad Id!", "match": {}}
    report = compile_notes(notes, tmp_path / "g", FakeBackend({"force-push": {"guards": [g, no_tests, invalid]}}))
    assert load_guard(tmp_path / "g" / "proposed" / "no-force-push.yaml").source == "notes/force-push.md"
    rejected = {gid: reasons for gid, _, reasons in report.rejected}
    assert any("tests.block needs" in r for r in rejected["no-tests"])
    assert any("id must match" in r for r in rejected["bad-id"])


def test_rejects_id_that_is_already_active(notes, tmp_path):
    out = tmp_path / "g"
    compile_notes(notes, out, FakeBackend({"force-push": {"guards": [good_guard()]}}))
    enable(out, "no-force-push")
    report = compile_notes(notes, out, FakeBackend({"force-push": {"guards": [good_guard()]}}))
    assert "already exists" in report.rejected[0][2][0]


def test_skip_reason_and_llm_errors(notes, tmp_path):
    class Broken:
        name = "broken"

        def propose(self, source, text):
            raise LLMError("boom")

    report = compile_notes(notes, tmp_path / "a", FakeBackend({"force-push": {"guards": [], "skip_reason": "taste"}}))
    assert ("notes/force-push.md", "taste") in report.skipped
    report = compile_notes(notes, tmp_path / "b", Broken())
    assert report.errors == [("notes/force-push.md", "boom")]


def test_skeleton_backend(notes, tmp_path):
    out = tmp_path / "g"
    report = compile_notes(notes, out, None)
    assert [gid for gid, _ in report.drafts] == ["force-push"]
    text = (out / "drafts" / "force-push.yaml").read_text(encoding="utf-8")
    assert "#   Never force push to shared branches." in text
    # A skeleton cannot be enabled before a human writes tests.
    with pytest.raises(StoreError, match="not valid"):
        enable(out, "force-push")
    # Existing drafts are never overwritten.
    report = compile_notes(notes, out, None)
    assert report.drafts == []
    assert any("draft already exists" in why for _, why in report.skipped)


def test_enable_and_disable(notes, tmp_path):
    out = tmp_path / "g"
    compile_notes(notes, out, FakeBackend({"force-push": {"guards": [good_guard(), bad_guard()]}}))
    with pytest.raises(StoreError, match="tests fail"):
        enable(out, "too-broad")
    path = enable(out, "no-force-push")
    assert path == out / "no-force-push.yaml"
    assert [g.id for g in load_guards(out)[0]] == ["no-force-push"]
    with pytest.raises(StoreError, match="already active"):
        enable(out, "no-force-push")
    disable(out, "no-force-push")
    assert load_guards(out)[0] == []
    with pytest.raises(StoreError, match="no proposed"):
        enable(out, "missing")


def test_fixed_rejected_guard_can_be_enabled(notes, tmp_path):
    out = tmp_path / "g"
    compile_notes(notes, out, FakeBackend({"force-push": {"guards": [bad_guard()]}}))
    target = out / "rejected" / "too-broad.yaml"
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    data["match"]["command"] = FORCE_PUSH
    target.write_text(yaml.safe_dump(data), encoding="utf-8")
    enable(out, "too-broad")
    assert "rejected_reasons" not in (out / "too-broad.yaml").read_text(encoding="utf-8")


def test_parse_response_variants():
    payload = {"guards": [good_guard()]}
    assert parse_response(json.dumps(payload)) == payload
    assert parse_response("```json\n" + json.dumps(payload) + "\n```") == payload
    envelope = {"type": "result", "is_error": False, "result": "", "structured_output": payload}
    assert parse_response(json.dumps(envelope)) == payload
    envelope = {"type": "result", "is_error": False, "result": "Here:\n" + json.dumps(payload)}
    assert parse_response(json.dumps(envelope)) == payload
    with pytest.raises(LLMError):
        parse_response(json.dumps({"type": "result", "is_error": True, "result": "rate limited"}))
    with pytest.raises(LLMError):
        parse_response("not json")


def test_claude_backend_uses_print_mode_and_schema_without_calling_claude():
    captured = {}

    def fake_run(argv, input, capture_output, timeout):
        captured["argv"] = argv
        captured["input"] = input.decode("utf-8")
        body = json.dumps({"type": "result", "is_error": False, "structured_output": {"guards": [good_guard()]}})
        return subprocess.CompletedProcess(argv, 0, body.encode("utf-8"), b"")

    backend = ClaudeBackend(model="some-model", runner=fake_run)
    result = backend.propose("notes/force-push.md", "Never force push.")
    argv = captured["argv"]
    assert result["guards"][0]["id"] == "no-force-push"
    assert "-p" in argv and argv[argv.index("--output-format") + 1] == "json"
    assert json.loads(argv[argv.index("--json-schema") + 1]) == RESPONSE_SCHEMA
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--model") + 1] == "some-model"
    assert "Never force push." in captured["input"] and "notes/force-push.md" in captured["input"]


def test_claude_backend_reports_failures():
    def failing(argv, input, capture_output, timeout):
        return subprocess.CompletedProcess(argv, 1, b"", b"not logged in")

    with pytest.raises(LLMError, match="not logged in"):
        ClaudeBackend(runner=failing).propose("n.md", "x")

    def missing(*args, **kwargs):
        raise FileNotFoundError

    with pytest.raises(LLMError, match="not found"):
        ClaudeBackend(runner=missing).propose("n.md", "x")


def test_prompt_lists_all_predicates():
    prompt = build_prompt("n.md", "text")
    for name in ("mixed_line_endings", "utf8_double_encoded", "contains_secret_like", "file_is_generated_marker", "line_endings_changed"):
        assert name in prompt


def test_examples_compile_to_the_shipped_guards(tmp_path):
    backend = FakeBackend.from_file(EXAMPLES / "fake-llm" / "responses.json")
    out = tmp_path / "guards"
    report = compile_notes(EXAMPLES / "notes", out, backend)
    proposed = sorted(gid for gid, _ in report.proposed)
    shipped = sorted(g.id for g in load_guards(EXAMPLES / "guards")[0])
    assert proposed == shipped
    assert [gid for gid, _, _ in report.rejected] == ["image-server-no-free-loose"]
    for gid in proposed:
        a = yaml.safe_load((out / "proposed" / f"{gid}.yaml").read_text(encoding="utf-8"))
        b = yaml.safe_load((EXAMPLES / "guards" / f"{gid}.yaml").read_text(encoding="utf-8"))
        a["source"] = b["source"] = None
        assert a == b, gid
