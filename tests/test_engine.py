import pytest

from lesson_guard.engine import Action, evaluate, matches
from lesson_guard.model import GuardError, guard_from_dict, load_guards
from lesson_guard.selftest import failures, run_guard_tests

from conftest import make_guard


def test_command_regex_and_negation(build_guard):
    g = build_guard(match={"command": r"\brm\s+-rf\b", "command_not": r"\bbuild\b"},
                    tests={"block": [{"command": "rm -rf dist"}], "pass": [{"command": "rm -rf build"}]})
    assert matches(g, Action("pre_tool", "Bash", command="rm -rf dist"))
    assert not matches(g, Action("pre_tool", "Bash", command="rm -rf build"))
    assert not matches(g, Action("pre_tool", "Bash", command=None))


def test_list_values_mean_any(build_guard):
    g = build_guard(match={"command": ["^git push --force", "^git reset --hard"]},
                    tests={"block": [{"command": "git reset --hard"}], "pass": [{"command": "git push"}]})
    assert matches(g, Action("pre_tool", "Bash", command="git push --force origin"))
    assert matches(g, Action("pre_tool", "Bash", command="git reset --hard HEAD~1"))
    assert not matches(g, Action("pre_tool", "Bash", command="git push"))


def test_tool_is_full_match(build_guard):
    g = build_guard(match={"tool": "Bash|PowerShell", "command": "x"},
                    tests={"block": [{"command": "x"}], "pass": [{"command": "y"}]})
    assert matches(g, Action("pre_tool", "PowerShell", command="x"))
    assert not matches(g, Action("pre_tool", "BashOutput", command="x"))


def test_event_filter(build_guard):
    g = build_guard()
    assert not matches(g, Action("pre_edit", "Write", command="rm -rf x", path="a"))


def test_path_and_content_all_must_hold(build_guard):
    g = build_guard(
        event=["pre_edit", "pre_commit"],
        match={"path": "src/**/*.py", "path_not": "**/test_*.py", "content": r"^import pdb"},
        tests={"block": [{"path": "src/a.py", "content": "import pdb\n"}], "pass": [{"path": "src/a.py", "content": "x\n"}]},
    )
    assert matches(g, Action("pre_edit", "Write", path="src/x/a.py", content="import pdb\n"))
    assert not matches(g, Action("pre_edit", "Write", path="src/x/test_a.py", content="import pdb\n"))
    assert not matches(g, Action("pre_edit", "Write", path="lib/a.py", content="import pdb\n"))
    assert not matches(g, Action("pre_edit", "Write", path="src/a.py", content=None))
    # Absolute paths are made relative to the working directory.
    assert matches(g, Action("pre_commit", "git", path="/repo/src/a.py", content="import pdb\n"), cwd="/repo")


def test_predicate_targets_and_negation(build_guard):
    g = build_guard(
        event=["pre_edit"],
        match={"predicates": [{"name": "contains_secret_like", "on": "added"}, "!file_is_generated_marker"]},
        tests={"block": [{"path": "a", "content": 'token = "Zx81Qw7Er6Ty5Ui4Op3As2Df"'}], "pass": [{"path": "a", "content": "x"}]},
    )
    secret = 'token = "Zx81Qw7Er6Ty5Ui4Op3As2Df"'
    assert matches(g, Action("pre_edit", "Edit", path="a", added=secret, original="x"))
    assert not matches(g, Action("pre_edit", "Edit", path="a", added="y", original=secret))
    assert not matches(g, Action("pre_edit", "Edit", path="a", added=secret, original="// @generated\n"))


def test_predicate_any_target(build_guard):
    g = build_guard(event=["pre_edit"], match={"predicates": [{"name": "mixed_line_endings", "on": "any"}]},
                    tests={"block": [{"path": "a", "original": "a\r\nb\n", "content": "x"}], "pass": [{"path": "a", "content": "x\n"}]})
    assert failures(g) == []


def test_warn_findings_are_not_blocking(build_guard):
    g = build_guard(severity="warn")
    findings = evaluate([g], [Action("pre_tool", "Bash", command="rm -rf x")])
    assert len(findings) == 1 and not findings[0].blocking


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"id": "Bad Id"}, "id must match"),
        ({"severity": "fatal"}, "severity"),
        ({"event": ["on_save"]}, "event must be"),
        ({"match": {}}, "positive matcher"),
        ({"match": {"command": "("}}, "invalid regex"),
        ({"match": {"predicates": ["no_such_thing"]}}, "unknown predicate"),
        ({"match": {"predicates": [{"name": "line_endings_changed", "on": "content"}]}}, "takes no 'on'"),
        ({"match": {"command": "x", "colour": "red"}}, "unknown keys"),
        ({"tests": {"block": [], "pass": [{"command": "x"}]}}, "tests.block needs"),
        ({"tests": {"block": [{"command": "x"}]}}, "tests.pass needs"),
        ({"tests": {"block": [{"command": "x", "event": "pre_edit"}], "pass": [{"command": "y"}]}}, "not one of the guard's events"),
        ({"message": ""}, "'message'"),
        ({"extra": 1}, "unknown top-level"),
    ],
)
def test_validation_errors(overrides, fragment):
    with pytest.raises(GuardError) as info:
        guard_from_dict(make_guard(**overrides))
    assert fragment in str(info.value)


def test_selftest_reports_failing_cases(build_guard):
    g = build_guard(match={"command": "rm"}, tests={"block": [{"command": "rm -rf x"}], "pass": [{"command": "rmdir x"}]})
    results = run_guard_tests(g)
    assert [r.ok for r in results] == [True, False]
    assert "should pass but was caught" in failures(g)[0]


def test_test_case_defaults(build_guard):
    g = build_guard(event=["pre_edit"], match={"added": "TODO"},
                    tests={"block": [{"path": "a.py", "content": "TODO\n"}], "pass": [{"path": "a.py", "content": "x\n"}]})
    case = g.block_tests[0]
    assert case.tool == "Write" and case.added == "TODO\n"
    assert failures(g) == []


def test_load_guards_reports_invalid_and_duplicates(tmp_path):
    import yaml

    good = make_guard()
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(good), encoding="utf-8")
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(good), encoding="utf-8")
    (tmp_path / "c.yaml").write_text("id: [unclosed", encoding="utf-8")
    (tmp_path / "proposed").mkdir()
    (tmp_path / "proposed" / "d.yaml").write_text(yaml.safe_dump(make_guard(id="other")), encoding="utf-8")
    guards, problems = load_guards(tmp_path)
    assert [g.id for g in guards] == ["sample"]
    assert len(problems) == 2
