"""Moving guards between proposed/drafts/rejected and the active set."""

from __future__ import annotations

from pathlib import Path

from .compiler import REJECT_KEY
from .model import GuardError, dump_yaml, guard_from_dict, guard_files, read_yaml
from .selftest import failures

STAGES = ("proposed", "drafts", "rejected")


class StoreError(RuntimeError):
    pass


def _find(directory: Path, guard_id: str) -> Path | None:
    for path in guard_files(directory):
        try:
            data = read_yaml(path)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("id") == guard_id:
            return path
    return None


def enable(guards_dir: str | Path, guard_id: str) -> Path:
    """Activate a guard after re-running its tests. Returns the new path."""
    root = Path(guards_dir)
    if _find(root, guard_id):
        raise StoreError(f"guard {guard_id!r} is already active")
    for stage in STAGES:
        source = _find(root / stage, guard_id)
        if source is None:
            continue
        data = read_yaml(source)
        data.pop(REJECT_KEY, None)
        try:
            guard = guard_from_dict(data, origin=str(source))
        except GuardError as exc:
            raise StoreError(f"{source}: guard is not valid: " + "; ".join(exc.errors)) from exc
        failed = failures(guard)
        if failed:
            raise StoreError(f"{source}: guard tests fail: " + "; ".join(failed))
        target = root / f"{guard_id}.yaml"
        target.write_text(dump_yaml(data), encoding="utf-8", newline="\n")
        source.unlink()
        return target
    raise StoreError(f"no proposed, draft or rejected guard with id {guard_id!r} in {root}")


def disable(guards_dir: str | Path, guard_id: str) -> Path:
    """Move an active guard back to proposed/."""
    root = Path(guards_dir)
    source = _find(root, guard_id)
    if source is None:
        raise StoreError(f"no active guard with id {guard_id!r} in {root}")
    target = root / "proposed" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise StoreError(f"{target} already exists")
    source.replace(target)
    return target
