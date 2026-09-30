"""Command line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

from . import __version__
from .adapters import render
from .compiler import REJECT_KEY, compile_notes, plan
from .engine import evaluate
from .llm import ClaudeBackend, FakeBackend, build_prompt
from .model import GuardError, guard_files, guard_from_dict, load_guards, read_yaml
from .redact import redact_text
from .selftest import run_guard_tests
from .store import STAGES, StoreError, disable, enable

EXIT_ERROR = 1
EXIT_BLOCKING_ERROR = 2


def _default_guards() -> str:
    return os.environ.get("LESSON_GUARD_DIR", "guards")


def _err(message: str) -> None:
    print(message, file=sys.stderr)


# ------------------------------------------------------------------ check


def cmd_check(args: argparse.Namespace) -> int:
    error_code = EXIT_BLOCKING_ERROR if args.fail_closed else EXIT_ERROR
    try:
        guards, problems = load_guards(args.guards)
        for problem in problems:
            _err(f"lesson-guard: skipped invalid guard: {problem}")

        if args.json_stdin:
            from .payload import actions_from_payload

            raw = sys.stdin.buffer.read().decode("utf-8-sig")
            payload = json.loads(raw) if raw.strip() else {}
            actions, cwd = actions_from_payload(payload)
        elif args.event == "pre_commit":
            from .gitmode import staged_actions

            actions, cwd = staged_actions(args.cwd), None
        else:
            _err("lesson-guard check: use --json-stdin for agent hooks or --event pre_commit for git")
            return error_code

        if args.event != "auto":
            actions = [a for a in actions if a.event == args.event]
        findings = evaluate(guards, actions, cwd)
        result = render(args.format, findings)
    except Exception as exc:  # a broken hook must say why, not crash silently
        _err(redact_text(f"lesson-guard: {type(exc).__name__}: {exc}"))
        return error_code

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        _err(result.stderr)
    if problems and result.exit_code == 0 and not findings:
        return error_code
    return result.exit_code


# ------------------------------------------------------------------- test


def _collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in paths:
        p = Path(item)
        if p.is_dir():
            files.extend(guard_files(p))
        elif p.is_file():
            files.append(p)
        else:
            _err(f"not found: {item}")
    return files


def cmd_test(args: argparse.Namespace) -> int:
    files = _collect(args.paths or [args.guards])
    if not files:
        print("no guard files found")
        return 0
    failed = 0
    for path in files:
        try:
            # Rejected files carry their reasons; ignore them so a fixed
            # guard can be re-tested in place.
            data = read_yaml(path)
            if isinstance(data, dict):
                data.pop(REJECT_KEY, None)
            guard = guard_from_dict(data, origin=str(path))
        except yaml.YAMLError as exc:
            failed += 1
            print(f"INVALID {path}\n  - invalid YAML: {exc}")
            continue
        except GuardError as exc:
            failed += 1
            print(f"INVALID {path}")
            for error in exc.errors:
                print(f"  - {error}")
            continue
        results = run_guard_tests(guard)
        bad = [r for r in results if not r.ok]
        if bad:
            failed += 1
            print(f"FAIL    {guard.id} ({len(results) - len(bad)}/{len(results)} cases)")
            for r in bad:
                print(f"  - {r.describe()}")
        else:
            print(f"ok      {guard.id} ({len(results)} cases)")
    print(f"\n{len(files) - failed} of {len(files)} guard files passed")
    return 1 if failed else 0


# ------------------------------------------------------------------- list


def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.guards)
    guards, problems = load_guards(root)
    if not guards and not problems:
        print(f"no active guards in {root}")
    for g in guards:
        print(f"{g.id:32} {g.severity:5} {','.join(g.events):20} {g.description}")
    for problem in problems:
        print(f"INVALID {problem}")
    for stage in STAGES:
        files = guard_files(root / stage)
        if not files:
            continue
        if args.all:
            print(f"\n{stage}:")
            for path in files:
                try:
                    data = read_yaml(path)
                    gid = data.get("id") if isinstance(data, dict) else "?"
                except Exception:
                    gid = "?"
                print(f"  {gid}  ({path.name})")
        else:
            print(f"{len(files)} {stage} (see --all)")
    return 1 if problems else 0


# ---------------------------------------------------------------- compile


def _backend(spec: str, model: str | None):
    if spec == "none":
        return None
    if spec == "claude":
        return ClaudeBackend(model=model)
    if spec.startswith("fake:"):
        return FakeBackend.from_file(spec[len("fake:"):])
    raise SystemExit(f"unknown --llm {spec!r}; use claude, none or fake:FILE")


def _print_plan(args: argparse.Namespace, backend) -> None:
    """Say which notes leave the machine before anything is sent."""
    outgoing, skipped = plan(args.notes_dir, all_notes=args.all_notes, exclude=args.exclude)
    target = "a draft file" if backend is None else f"the model ({backend.name})"
    for item in outgoing:
        count = len(item.redacted.redactions)
        print(f"send      {item.note.source} -> {target}, {count} value{'s' if count != 1 else ''} redacted")
    if args.dry_run:
        for source, why in skipped:
            print(f"skipped   {source}: {why}")
        if backend is None:
            print("\n--llm none sends nothing to a model; the redacted note text goes into the draft files.")
        for item in outgoing:
            print(f"\n===== payload for {item.note.source} =====")
            print(build_prompt(item.note.source, item.text), end="")
            print(f"===== end of payload for {item.note.source} =====")
        print(f"\ndry run: {len(outgoing)} notes would be used, {len(skipped)} skipped. Nothing was sent or written.")
    elif outgoing:
        print()


def cmd_compile(args: argparse.Namespace) -> int:
    backend = _backend(args.llm, args.model)
    out = args.out or args.guards
    _print_plan(args, backend)
    if args.dry_run:
        return 0
    report = compile_notes(args.notes_dir, out, backend, all_notes=args.all_notes, exclude=args.exclude)
    for gid, path in report.proposed:
        print(f"proposed  {gid:32} {path.as_posix()}")
    for gid, path, reasons in report.rejected:
        print(f"rejected  {gid:32} {path.as_posix()}")
        for reason in reasons:
            print(f"            - {reason}")
    for gid, path in report.drafts:
        print(f"draft     {gid:32} {path.as_posix()}")
    for source, why in report.skipped:
        print(f"skipped   {source}: {why}")
    for source, why in report.errors:
        print(f"error     {source}: {why}")
    print(
        f"\n{len(report.proposed)} proposed, {len(report.rejected)} rejected, {len(report.drafts)} drafts, "
        f"{len(report.skipped)} skipped, {len(report.errors)} errors. Nothing was enabled; "
        f"use 'lesson-guard enable ID'."
    )
    return 1 if report.errors else 0


# ---------------------------------------------------------- enable/disable


def cmd_enable(args: argparse.Namespace) -> int:
    try:
        path = enable(args.guards, args.id)
    except StoreError as exc:
        _err(f"lesson-guard enable: {exc}")
        return 1
    print(f"enabled {args.id}: {path.as_posix()}")
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    try:
        path = disable(args.guards, args.id)
    except StoreError as exc:
        _err(f"lesson-guard disable: {exc}")
        return 1
    print(f"disabled {args.id}: moved to {path.as_posix()}")
    return 0


# ---------------------------------------------------------------- install


def cmd_install(args: argparse.Namespace) -> int:
    from . import install

    guards_dir = Path(args.guards).resolve().as_posix()
    if args.target == "git":
        script = install.git_script(args.command, guards_dir)
        if not args.write:
            print("# Save as .git/hooks/pre-commit and make it executable,")
            print("# or run: lesson-guard install git --write .git/hooks/pre-commit\n")
            print(script, end="")
            return 0
        try:
            install.write_git_hook(Path(args.write), script)
        except ValueError as exc:
            _err(str(exc))
            return 1
        print(f"wrote {args.write}")
        return 0

    snippet = (install.claude_snippet if args.target == "claude" else install.codex_snippet)(args.command, guards_dir)
    if not args.write:
        where = ".claude/settings.json or ~/.claude/settings.json" if args.target == "claude" else ".codex/hooks.json or ~/.codex/hooks.json"
        print(f"# Merge into {where}")
        print(f"# or run: lesson-guard install {args.target} --write <that file>\n")
        print(json.dumps(snippet, indent=2))
        return 0
    try:
        changed = install.merge_hooks_json(Path(args.write), snippet)
    except (ValueError, json.JSONDecodeError) as exc:
        _err(f"cannot merge into {args.write}: {exc}")
        return 1
    print(f"{'updated' if changed else 'already registered in'} {args.write}")
    return 0


# ----------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--guards", default=_default_guards(), help="guards directory (default: ./guards or $LESSON_GUARD_DIR)")

    parser = argparse.ArgumentParser(prog="lesson-guard", description="Executable guards compiled from agent lesson notes.")
    parser.add_argument("--version", action="version", version=f"lesson-guard {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", parents=[common], help="evaluate guards against a hook payload or staged files")
    p.add_argument("--event", default="auto", choices=["auto", "pre_tool", "pre_edit", "pre_commit"])
    p.add_argument("--json-stdin", action="store_true", help="read a PreToolUse hook payload from stdin")
    p.add_argument("--format", default=None, choices=["text", "json", "claude", "codex"],
                   help="output protocol (default: claude with --json-stdin, text otherwise)")
    p.add_argument("--cwd", default=None, help="repository for --event pre_commit")
    p.add_argument("--fail-closed", action="store_true", help="exit 2 (block) instead of 1 when lesson-guard itself fails")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("test", parents=[common], help="run the tests inside guard files")
    p.add_argument("paths", nargs="*", help="guard files or directories (default: the guards directory)")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("list", parents=[common], help="list guards")
    p.add_argument("--all", action="store_true", help="also list proposed, draft and rejected guards")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("compile", parents=[common], help="propose guards from markdown notes")
    p.add_argument("notes_dir")
    p.add_argument("--out", default=None, help="guards directory to write into (default: --guards)")
    p.add_argument("--llm", default="none", help="claude, none (skeletons) or fake:FILE (canned JSON)")
    p.add_argument("--model", default=None, help="model for --llm claude")
    p.add_argument("--all-notes", action="store_true", help="do not skip notes that look non-actionable")
    p.add_argument("--exclude", action="append", default=[],
                   help="glob of note files to skip (repeatable); also read from NOTES_DIR/.lesson-guard-ignore")
    p.add_argument("--dry-run", action="store_true",
                   help="print the exact redacted payload per note; call no model and write nothing")
    p.set_defaults(func=cmd_compile)

    p = sub.add_parser("enable", parents=[common], help="activate a proposed guard after re-running its tests")
    p.add_argument("id")
    p.set_defaults(func=cmd_enable)

    p = sub.add_parser("disable", parents=[common], help="move an active guard back to proposed/")
    p.add_argument("id")
    p.set_defaults(func=cmd_disable)

    p = sub.add_parser("install", parents=[common], help="print (or merge) the hook configuration")
    p.add_argument("target", choices=["claude", "codex", "git"])
    p.add_argument("--write", default=None, metavar="PATH", help="merge into this file instead of printing")
    p.add_argument("--command", default="lesson-guard", help="how the hook calls lesson-guard")
    p.set_defaults(func=cmd_install)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "format", "unset") is None:
        args.format = "claude" if args.json_stdin else "text"
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
