from lesson_guard.payload import actions_from_payload, parse_apply_patch, read_text_file


def reader_from(files):
    def read(path):
        return files.get(path.replace("\\", "/"))

    return read


def test_claude_bash():
    actions, cwd = actions_from_payload({"cwd": "/p", "tool_name": "Bash", "tool_input": {"command": "ls -la"}})
    assert cwd == "/p"
    assert len(actions) == 1
    a = actions[0]
    assert (a.event, a.tool, a.command) == ("pre_tool", "Bash", "ls -la")


def test_claude_powershell_is_a_shell_tool():
    actions, _ = actions_from_payload({"tool_name": "PowerShell", "tool_input": {"command": "Get-ChildItem"}})
    assert actions[0].event == "pre_tool" and actions[0].command == "Get-ChildItem"


def test_claude_write_reads_original():
    files = {"/p/a.txt": "old\r\n"}
    actions, _ = actions_from_payload(
        {"cwd": "/p", "tool_name": "Write", "tool_input": {"file_path": "a.txt", "content": "new\n"}},
        reader=reader_from(files),
    )
    a = actions[0]
    assert (a.event, a.path, a.content, a.original, a.added) == ("pre_edit", "a.txt", "new\n", "old\r\n", "new\n")


def test_claude_edit_reconstructs_content():
    files = {"/p/a.py": "x = 1\ny = 2\n"}
    actions, _ = actions_from_payload(
        {"cwd": "/p", "tool_name": "Edit",
         "tool_input": {"file_path": "/p/a.py", "old_string": "y = 2", "new_string": "y = 3"}},
        reader=reader_from(files),
    )
    a = actions[0]
    assert a.content == "x = 1\ny = 3\n"
    assert a.added == "y = 3"


def test_edit_that_does_not_apply_leaves_content_unknown():
    files = {"/p/a.py": "x = 1\n"}
    actions, _ = actions_from_payload(
        {"cwd": "/p", "tool_name": "Edit", "tool_input": {"file_path": "a.py", "old_string": "zzz", "new_string": "q"}},
        reader=reader_from(files),
    )
    assert actions[0].content is None and actions[0].original == "x = 1\n"


def test_multiedit_and_alternate_field_names():
    files = {"/p/a.py": "a b c"}
    actions, _ = actions_from_payload(
        {"cwd": "/p", "tool_name": "MultiEdit", "tool_input": {"file_path": "a.py", "edits": [
            {"old_string": "a", "new_string": "A"},
            {"old_text": "c", "new_text": "C"},
        ]}},
        reader=reader_from(files),
    )
    assert actions[0].content == "A b C"
    assert actions[0].added == "A\nC"


def test_notebook_edit():
    actions, _ = actions_from_payload(
        {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "n.ipynb", "new_source": "print(1)"}},
        reader=lambda p: None,
    )
    assert actions[0].path == "n.ipynb" and actions[0].added == "print(1)"


def test_other_tools_are_serialised():
    actions, _ = actions_from_payload({"tool_name": "mcp__fetch__fetch", "tool_input": {"url": "http://x/free", "method": "POST"}})
    assert actions[0].event == "pre_tool"
    assert '"url": "http://x/free"' in actions[0].command


PATCH = """*** Begin Patch
*** Add File: docs/new.md
+# Title
+text
*** Update File: src/app.py
@@ def main():
-    print("a")
+    print("b")
*** Delete File: old.txt
*** Update File: src/x.py
*** Move to: src/y.py
@@
+z = 1
*** End Patch
"""


def test_parse_apply_patch():
    changes = parse_apply_patch(PATCH)
    assert [(c["op"], c["path"]) for c in changes] == [
        ("add", "docs/new.md"), ("update", "src/app.py"), ("delete", "old.txt"), ("update", "src/x.py")]
    assert changes[0]["content"] == "# Title\ntext\n"
    assert changes[1]["added"] == '    print("b")'
    assert changes[1]["content"] is None
    assert changes[3]["move_to"] == "src/y.py"


def test_codex_apply_patch_payload():
    files = {"/w/src/app.py": 'def main():\n    print("a")\n'}
    actions, _ = actions_from_payload(
        {"cwd": "/w", "tool_name": "apply_patch", "tool_input": {"command": PATCH}}, reader=reader_from(files))
    assert [a.path for a in actions] == ["docs/new.md", "src/app.py", "old.txt", "src/y.py"]
    assert all(a.event == "pre_edit" for a in actions)
    assert actions[1].original.startswith("def main")


def test_codex_shell_with_embedded_patch_and_argv_list():
    heredoc = "apply_patch <<'PATCH'\n" + PATCH + "PATCH"
    actions, _ = actions_from_payload({"tool_name": "Bash", "tool_input": {"command": heredoc}}, reader=lambda p: None)
    assert actions[0].path == "docs/new.md"
    actions, _ = actions_from_payload({"tool_name": "shell", "tool_input": {"command": ["bash", "-lc", "rm -rf x"]}})
    assert "rm -rf x" in actions[0].command


def test_read_text_file_keeps_line_endings_and_skips_binary(tmp_path):
    text = tmp_path / "t.txt"
    text.write_bytes(b"a\r\nb\n")
    assert read_text_file(str(text)) == "a\r\nb\n"
    binary = tmp_path / "b.bin"
    binary.write_bytes(b"\x00\x01\x02")
    assert read_text_file(str(binary)) is None
    assert read_text_file(str(tmp_path / "missing")) is None
