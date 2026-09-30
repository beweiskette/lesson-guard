# lesson-guard

[Deutsch](README.de.md)

AI coding agents collect lessons in prose: memory notes, `CLAUDE.md` and `AGENTS.md` rules, incident logs.
Prose gets skipped, forgotten or outvoted by the next instruction, so the same mistake comes back.
lesson-guard turns those notes into small executable guards that run as agent hooks and as a git
pre-commit hook. A guard blocks the mistake before it happens and tells the agent what to do instead.

Each guard carries its own test cases: examples it must catch and examples it must let through.
A guard whose tests fail is never activated.

Other projects either write guards by hand or only record new prose rules. lesson-guard compiles the
notes you already have into guards, tests them, and serves the same guard set to Claude Code, Codex
and git.

## How it works

```
notes/*.md  --compile-->  guards/proposed/*.yaml  --enable-->  guards/*.yaml  --check-->  Claude Code hook
                          guards/rejected/*.yaml  (tests failed)                          Codex hook
                          guards/drafts/*.yaml    (--llm none)                            git pre-commit
```

1. `lesson-guard compile NOTES_DIR` reads markdown notes (front matter such as `name`,
   `description`, `type` is understood) and proposes guards. With `--llm claude` a model writes
   them; with `--llm none` you get a skeleton per note to fill in yourself.
2. Every proposed guard is validated and its own tests are run. Passing guards land in
   `guards/proposed/`, failing ones in `guards/rejected/` together with the reasons.
3. Nothing is active until you run `lesson-guard enable ID`, which re-runs the tests first.
4. `lesson-guard check` evaluates the active guards against a hook payload or the staged files.

## Install

Python 3.11 or newer. The only runtime dependency is PyYAML.

```
git clone https://github.com/beweiskette/lesson-guard.git
cd lesson-guard
python -m pip install .
```

`pipx install .` works as well and keeps the tool out of your project environments. The hooks call
`lesson-guard`, so the command has to be on the `PATH` of the agent. If it is not, pass the full path
with `lesson-guard install ... --command /path/to/lesson-guard`.

## Quick start

The `examples/` folder has seven synthetic notes, canned model answers and the resulting guards.

```
lesson-guard test --guards examples/guards
lesson-guard compile examples/notes --out /tmp/guards --llm fake:examples/fake-llm/responses.json
```

Output of the second command:

```
send      examples/notes/backup-duplicates.md -> the model (fake), 0 values redacted
send      examples/notes/double-encoding.md -> the model (fake), 0 values redacted
send      examples/notes/generated-client.md -> the model (fake), 0 values redacted
send      examples/notes/image-server-free.md -> the model (fake), 0 values redacted
send      examples/notes/line-endings.md -> the model (fake), 0 values redacted
send      examples/notes/no-keys-in-packages.md -> the model (fake), 0 values redacted

proposed  no-class-files-in-backups        /tmp/guards/proposed/no-class-files-in-backups.yaml
proposed  no-double-encoded-utf8           /tmp/guards/proposed/no-double-encoded-utf8.yaml
proposed  no-direct-edit-generated         /tmp/guards/proposed/no-direct-edit-generated.yaml
proposed  image-server-no-free             /tmp/guards/proposed/image-server-no-free.yaml
proposed  keep-mixed-line-endings          /tmp/guards/proposed/keep-mixed-line-endings.yaml
proposed  no-secrets-in-files              /tmp/guards/proposed/no-secrets-in-files.yaml
proposed  no-secret-files                  /tmp/guards/proposed/no-secret-files.yaml
proposed  no-env-in-archives               /tmp/guards/proposed/no-env-in-archives.yaml
rejected  image-server-no-free-loose       /tmp/guards/rejected/image-server-no-free-loose.yaml
            - tests.pass[0] should pass but was caught: 'curl https://example.org/freedom'
skipped   examples/notes/answer-style.md: note type 'user' rarely describes a checkable mistake

8 proposed, 1 rejected, 0 drafts, 1 skipped, 0 errors. Nothing was enabled; use 'lesson-guard enable ID'.
```

The rejected guard matched `/free` anywhere, so its own pass case (`/freedom`) failed. That is the
point of carrying tests: a guard that is too broad gets switched off by annoyed users, so it is
stopped before it is ever enabled.

Try a hook payload by hand:

```
echo '{"tool_name":"Bash","tool_input":{"command":"curl -X POST http://localhost:9000/free"}}' \
  | lesson-guard check --json-stdin --format claude --guards examples/guards
```

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "lesson-guard blocked this action:\n- [image-server-no-free] Calling /free unloads every model on the shared image server for all users. Wait for the queue to drain or ask the owner instead. (lesson: examples/notes/image-server-free.md)"}}
```

## Your own notes

```
lesson-guard compile path/to/notes --out guards --llm claude     # model proposes guards (costs tokens)
lesson-guard compile path/to/notes --out guards --llm none       # skeletons for manual work
lesson-guard list --all
lesson-guard test guards/proposed
lesson-guard enable keep-mixed-line-endings
```

`--llm claude` runs `claude -p --output-format json --json-schema ... --tools ""` once per actionable
note, so no tools are available to the model and the answer must match the schema. Before anything
is sent, `compile` prints one `send` line per note that will reach the model, with the number of
redacted values. `--dry-run` prints the exact prompt per note and stops there. The model cannot set
the `source` field, the compiler fills it in. See [Security](#security) for what is filtered.

Notes whose front matter says `type: user` or `type: reference`, and notes without rule-like wording
(never, always, do not, instead, ...), are skipped. `--all-notes` turns that filter off,
`--exclude GLOB` skips files such as an index. Notes with `private: true` in their front matter and
notes listed in `NOTES_DIR/.lesson-guard-ignore` (one glob per line, `#` for comments) are never
read into a prompt or a draft.

## Guard format

```yaml
id: keep-mixed-line-endings
source: examples/notes/line-endings.md
description: Files that mix CRLF and LF must keep their line endings.
severity: block                  # block or warn
event: [pre_edit, pre_commit]    # pre_tool, pre_edit, pre_commit
match:
  predicates:
  - line_endings_changed
  - name: mixed_line_endings
    on: original
message: This file mixes CRLF and LF on purpose and the change normalizes that. Keep the
  original line endings and change only the lines you mean to change.
tests:
  block:
  - path: tools/runner.py
    original: "a = 1\r\nb = 2\nc = 3\r\n"
    content: "a = 1\nb = 2\nc = 4\n"
  pass:
  - path: tools/runner.py
    original: "a = 1\r\nb = 2\nc = 3\r\n"
    content: "a = 1\r\nb = 2\nc = 4\r\n"
```

Events and the fields a guard can look at:

| event | when | fields |
|---|---|---|
| `pre_tool` | before a shell command or any non-edit tool call | `tool`, `command` (for non-shell tools: the tool input as JSON) |
| `pre_edit` | before an agent writes a file | `tool`, `path`, `content` (file after the change, if known), `original` (file before), `added` (text the change adds) |
| `pre_commit` | for each staged file on `git commit` | `path`, `content` (staged), `original` (HEAD), `added` (added lines) |

Matchers. All given matchers must hold; a list means "any of these".

| key | meaning |
|---|---|
| `tool` | regex, full match on the tool name (`Bash`, `PowerShell`, `Write`, `Edit`, `apply_patch`, ...) |
| `command`, `command_not` | regex searched in the command |
| `path`, `path_not` | glob; `**` crosses directories; a glob without `/` matches the file name only |
| `content`, `content_not`, `added`, `added_not` | regex (multi-line mode) searched in the text |
| `predicates` | list of predicate names, `!name` negates, or `{name, on, negate}` |

Predicates:

| name | default target | true when |
|---|---|---|
| `mixed_line_endings` | `content` | the text uses more than one line ending style |
| `line_endings_changed` | original and content | the set of line ending styles differs before and after |
| `utf8_double_encoded` | `added` | the text contains mojibake such as `Ã¤` or `â€™` |
| `contains_secret_like` | `added` | known key formats, private key headers, or high-entropy values assigned to names like `api_key`, `token`, `password` |
| `file_is_generated_marker` | `original` | the first 30 lines carry `@generated`, `DO NOT EDIT`, `auto-generated` or similar |

`on` can be `content`, `original`, `added`, `command` or `any`.

Test cases use the same fields. The event defaults to the guard's first event, the tool to `Bash`,
`Write` or `git`. For edits, `added` defaults to `content`, as for a full-file write. Every guard needs
at least one `block` and one `pass` case.

## Wiring it into the agents

`lesson-guard install claude|codex|git` prints the configuration. With `--write PATH` it merges the
entry into that file (existing hooks stay, a second run changes nothing). The guards directory is
written as an absolute path.

### Claude Code

```
lesson-guard install claude --guards guards --write .claude/settings.json
```

This registers a `PreToolUse` hook for `Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit` running
`lesson-guard check --format claude --json-stdin`. A blocking finding returns
`permissionDecision: "deny"` with the guard messages as reason, which the agent reads. A warning
returns `additionalContext` for the model and a `systemMessage` for you, without a decision, so the
normal permission prompt still applies.

### Codex CLI

```
lesson-guard install codex --guards guards --write .codex/hooks.json
```

This registers a `PreToolUse` hook for `Bash|apply_patch` running
`lesson-guard check --format codex --json-stdin`. Patches are split into one `pre_edit` action per
file. Codex treats `permissionDecision: "ask"` and a few other fields as unsupported and then lets
the call through, so the Codex adapter only emits `deny`, `additionalContext` and `systemMessage`.

### git

```
lesson-guard install git --guards guards --write .git/hooks/pre-commit
```

The hook runs `lesson-guard check --event pre_commit` and exits with 1 when a guard blocks. An
existing pre-commit hook that was not written by lesson-guard is not overwritten.

### Failure behaviour

If lesson-guard itself fails (broken payload, unreadable guard), `check` prints the reason and exits
with 1. Both agents treat that as a non-blocking error, so the call proceeds and you see the error.
`--fail-closed` exits with 2 instead, which blocks. Invalid guard files are skipped with a message; the
valid ones still run.

## Commands

| command | purpose |
|---|---|
| `check` | evaluate active guards; `--json-stdin` for agent hooks, `--event pre_commit` for git |
| `test [PATH...]` | run the tests inside guard files |
| `list [--all]` | list active guards, with `--all` also proposed, drafts, rejected |
| `compile NOTES_DIR` | propose guards; `--llm claude`, `none` or `fake:FILE`; `--dry-run` shows the payload |
| `enable ID` / `disable ID` | move a guard into or out of the active set |
| `install claude/codex/git` | print or merge the hook configuration |

`--guards DIR` selects the guards directory everywhere (default `guards`, or `$LESSON_GUARD_DIR`).

## Security

Memory notes can contain secrets and private details. `lesson-guard compile` handles them like this:

- Notes marked `private: true` in their front matter, notes matched by `--exclude GLOB` and notes
  listed in `NOTES_DIR/.lesson-guard-ignore` are skipped. They appear in the report as skipped, with
  the reason, and their text is never read into a prompt or a draft file.
- The text of every other note is redacted before it is sent to the model or copied into a draft.
  Replaced with `[REDACTED:kind]` are: known key formats (`sk-...`, `ghp_...`, `github_pat_...`,
  `AKIA...`, `xox...`, `AIza...`, `glpat-...`, JWTs), whole private key blocks, `Bearer` and `Basic`
  credentials, values assigned to names like `password`, `token`, `secret`, `api_key` (for example
  `password=...` or `token: ...`), and long high-entropy strings. Placeholders such as
  `your-api-key-here`, `$VAR` or `os.environ[...]` stay.
- `compile` prints which notes are sent and how many values were redacted in each, before the first
  model call. `--dry-run` prints the exact payload for each note and neither calls the model nor
  writes files.
- A proposed guard that contains one of the values redacted from its note is rejected, and the value
  is removed from the file in `guards/rejected/`.

`lesson-guard check` redacts its own output in the same way. The guard message, the file path of the
checked action and error messages pass through the redaction before they reach the agent
(`permissionDecisionReason`, `additionalContext`, `systemMessage`) or the terminal. The command and
the file content being checked are never echoed.

The example guard `no-secret-files` blocks writing and committing `.env`, `.env.*`, `*.pem`,
`id_rsa`, `id_dsa`, `id_ecdsa` and `id_ed25519` by file name, whatever their content. `.env.example`,
`.env.sample` and `.env.template` stay allowed, and `no-secrets-in-files` checks those templates for
real-looking keys.

## Limitations

- Guards are regular expressions, globs and a few predicates. They catch the shape of a mistake,
  not its intent. A command that does the same thing in a different way passes.
- Hooks see tool calls. A script that writes a file is a `pre_tool` command, not a `pre_edit`.
- For Codex `apply_patch` updates the file content after the patch is not reconstructed, so
  `line_endings_changed` cannot fire there; the git pre-commit hook still catches it. For Claude
  `Edit` the content is reconstructed only when `old_string` occurs literally in the file.
- `contains_secret_like` and the redaction are heuristics. They miss secrets in unusual formats
  (a short password in prose, a hex key without a telling name) and can hide harmless long random
  strings such as some URLs. Private data that is not secret-like, such as names, is not redacted:
  mark such notes `private: true`. Use `--dry-run` to see what would leave the machine.
- `no-secret-files` also blocks certificate files with the `.pem` extension, including public ones.
  In the git pre-commit mode binary files and files over 2 MB are not turned into actions, so a
  path-only guard does not see them either.
- Globs are case-sensitive. Files over 2 MB and binary files are not inspected.
- Regexes proposed by a model are not checked for catastrophic backtracking. Read proposed guards
  before enabling them.
- The hook adapters follow the published hook protocols of Claude Code and Codex and are covered by
  unit tests on the JSON they emit. They were not run inside a live agent session during development,
  and the `--llm claude` backend was only tested with a fake process runner, because the tests never
  call a paid model.

## License

MIT, see [LICENSE](LICENSE).
