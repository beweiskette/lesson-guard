"""Compile markdown lesson notes into proposed guards.

Proposed guards are never active. Layout below the output directory:

    <out>/*.yaml            active guards (only "lesson-guard enable" puts files here)
    <out>/proposed/*.yaml   guards whose own tests passed
    <out>/rejected/*.yaml   guards that were invalid or failed their tests
    <out>/drafts/*.yaml     skeletons from --llm none, for a human to fill in

Privacy: notes with ``private: true`` in their front matter and notes matched
by an exclude glob (``--exclude`` or the ``.lesson-guard-ignore`` file in the
notes directory) are never read into a prompt or a draft. All other note text
is passed through ``redact`` first, and a proposal that contains one of the
redacted values is rejected.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .llm import Backend, LLMError
from .model import GuardError, dump_yaml, guard_from_dict, read_yaml
from .redact import Redacted, redact, scrub
from .selftest import failures

KEY_ORDER = ("id", "source", "description", "severity", "event", "match", "message", "tests")
REJECT_KEY = "rejected_reasons"

_ACTIONABLE = re.compile(
    r"(?i)\b(never|don'?t|do not|must not|mustn'?t|always|avoid|instead|only ever|must|"
    r"nie|niemals|nicht|immer|vermeide|vermeiden|statt|kein|keine|keinen)\b"
)
_SKIP_TYPES = {"user", "reference"}
IGNORE_FILE = ".lesson-guard-ignore"
_TRUE = {True, "true", "yes", "on", 1}


@dataclass
class Note:
    source: str
    meta: dict[str, Any]
    body: str

    @property
    def stem(self) -> str:
        return Path(self.source).stem

    @property
    def text(self) -> str:
        head = []
        for key in ("name", "description", "type"):
            if self.meta.get(key):
                head.append(f"{key}: {self.meta[key]}")
        return ("\n".join(head) + "\n\n" if head else "") + self.body.strip()


@dataclass
class CompileReport:
    proposed: list[tuple[str, Path]] = field(default_factory=list)
    rejected: list[tuple[str, Path, list[str]]] = field(default_factory=list)
    drafts: list[tuple[str, Path]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Outgoing:
    """A note that will be sent to the model (or written into a draft)."""

    note: Note
    redacted: Redacted

    @property
    def text(self) -> str:
        return self.redacted.text


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    text = text.lstrip("﻿")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            try:
                meta = yaml.safe_load("".join(lines[1:index])) or {}
            except yaml.YAMLError:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            return meta, "".join(lines[index + 1 :])
    return {}, text


def _source_label(notes_dir: Path, path: Path, notes_arg: str) -> str:
    rel = path.relative_to(notes_dir).as_posix()
    # Never put absolute local paths into guard files.
    prefix = Path(notes_arg).as_posix().rstrip("/") if not Path(notes_arg).is_absolute() else notes_dir.name
    return f"{prefix}/{rel}" if prefix not in ("", ".") else rel


def ignore_patterns(notes_dir: str | Path) -> list[str]:
    """Globs from the .lesson-guard-ignore file of the notes directory."""
    path = Path(notes_dir) / IGNORE_FILE
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def _excluded(rel: str, name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat) for pat in patterns)


def read_notes(
    notes_dir: str | Path, exclude: list[str] | None = None, excluded: list[str] | None = None
) -> list[Note]:
    """Read all markdown notes. Sources of excluded files go into ``excluded``."""
    notes_arg = str(notes_dir)
    root = Path(notes_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"notes directory not found: {notes_dir}")
    patterns = list(exclude or []) + ignore_patterns(root)
    notes = []
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if _excluded(rel, path.name, patterns):
            if excluded is not None:
                excluded.append(_source_label(root, path, notes_arg))
            continue
        meta, body = split_front_matter(path.read_text(encoding="utf-8", errors="replace"))
        notes.append(Note(source=_source_label(root, path, notes_arg), meta=meta, body=body))
    return notes


def is_private(note: Note) -> bool:
    value = note.meta.get("private")
    return (value.strip().lower() if isinstance(value, str) else value) in _TRUE


def actionable(note: Note) -> tuple[bool, str]:
    if not note.body.strip():
        return False, "empty note"
    if str(note.meta.get("type", "")).lower() in _SKIP_TYPES:
        return False, f"note type {note.meta.get('type')!r} rarely describes a checkable mistake"
    if not _ACTIONABLE.search(note.text):
        return False, "no rule-like wording (never, always, do not, instead, ...)"
    return True, ""


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-.")
    return (slug or "guard")[:64]


def ordered(data: dict[str, Any]) -> dict[str, Any]:
    out = {k: data[k] for k in KEY_ORDER if k in data}
    out.update({k: v for k, v in data.items() if k not in out})
    return out


def skeleton(note: Note, text: str | None = None) -> str:
    description = str(note.meta.get("description") or note.meta.get("name") or note.stem)
    guard = {
        "id": slugify(note.stem),
        "source": note.source,
        "description": description,
        "severity": "block",
        "event": ["pre_tool"],
        "match": {"command": "TODO-regex"},
        "message": "TODO: tell the agent what to do instead.",
        "tests": {"block": [], "pass": []},
    }
    header = [
        f"# Draft guard created from {note.source} by 'lesson-guard compile --llm none'.",
        "# Fill in event, match and tests, check with 'lesson-guard test <file>',",
        f"# then activate with 'lesson-guard enable {guard['id']}'.",
        "#",
        "# Note text:",
    ]
    body = note.text if text is None else text
    header += ["#   " + line if line else "#" for line in body.splitlines()]
    return "\n".join(header) + "\n" + dump_yaml(guard)


def validate_proposal(data: dict[str, Any], active_ids: set[str]) -> list[str]:
    """Reasons to reject a proposed guard. Empty list means it may be proposed."""
    try:
        guard = guard_from_dict(data)
    except GuardError as exc:
        return exc.errors
    reasons = failures(guard)
    if guard.id in active_ids:
        reasons.append(f"an active guard with id {guard.id!r} already exists")
    return reasons


def active_ids(out_dir: Path) -> set[str]:
    ids = set()
    for path in sorted(out_dir.glob("*.y*ml")):
        try:
            data = read_yaml(path)
        except yaml.YAMLError:
            continue
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            ids.add(data["id"])
    return ids


def plan(
    notes_dir: str | Path,
    all_notes: bool = False,
    exclude: list[str] | None = None,
) -> tuple[list[Outgoing], list[tuple[str, str]]]:
    """Decide which notes are used and redact them. Nothing is sent here.

    Returns the notes to use, with their redacted text, and the skipped notes
    with a reason.
    """
    excluded: list[str] = []
    notes = read_notes(notes_dir, exclude, excluded)
    skipped = [(source, "excluded by an exclude pattern") for source in excluded]
    outgoing = []
    for note in notes:
        if is_private(note):
            skipped.append((note.source, "marked private in its front matter"))
            continue
        ok, why = actionable(note)
        if not ok and not all_notes:
            skipped.append((note.source, why))
            continue
        outgoing.append(Outgoing(note, redact(note.text)))
    return outgoing, skipped


def compile_notes(
    notes_dir: str | Path,
    out_dir: str | Path,
    backend: Backend | None,
    all_notes: bool = False,
    exclude: list[str] | None = None,
) -> CompileReport:
    """Propose guards for every actionable note. backend=None writes skeletons."""
    out = Path(out_dir)
    report = CompileReport()
    existing = active_ids(out) if out.is_dir() else set()

    outgoing, skipped = plan(notes_dir, all_notes, exclude)
    report.skipped.extend(skipped)
    for item in outgoing:
        note = item.note

        if backend is None:
            target = out / "drafts" / f"{slugify(note.stem)}.yaml"
            if target.exists():
                report.skipped.append((note.source, f"draft already exists: {target.name}"))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(skeleton(note, item.text), encoding="utf-8", newline="\n")
            report.drafts.append((slugify(note.stem), target))
            continue

        try:
            response = backend.propose(note.source, item.text)
        except LLMError as exc:
            report.errors.append((note.source, str(exc)))
            continue
        proposals = response.get("guards") or []
        if not proposals:
            report.skipped.append((note.source, str(response.get("skip_reason") or "model proposed no guard")))
            continue

        for index, proposal in enumerate(proposals):
            if not isinstance(proposal, dict):
                report.errors.append((note.source, f"proposal {index} is not an object"))
                continue
            # The model never saw the redacted values. If one shows up anyway,
            # it is removed and the guard is rejected for a human to look at.
            proposal, leaked = scrub(proposal, item.redacted.values)
            data = dict(proposal)
            data.pop(REJECT_KEY, None)
            data["source"] = note.source  # never trust the model with provenance
            data = ordered(data)
            gid = data.get("id") if isinstance(data.get("id"), str) else ""
            name = slugify(gid or f"{note.stem}-{index + 1}")
            reasons = validate_proposal(data, existing)
            if leaked:
                reasons.insert(0, "proposal contained a value that was redacted from the note; it was removed")
            if reasons:
                target = out / "rejected" / f"{name}.yaml"
                target.parent.mkdir(parents=True, exist_ok=True)
                data[REJECT_KEY] = reasons
                target.write_text(dump_yaml(data), encoding="utf-8", newline="\n")
                report.rejected.append((name, target, reasons))
            else:
                target = out / "proposed" / f"{name}.yaml"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(dump_yaml(data), encoding="utf-8", newline="\n")
                report.proposed.append((name, target))
    return report
