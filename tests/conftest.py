from __future__ import annotations

from pathlib import Path

import pytest

from lesson_guard.model import guard_from_dict

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def make_guard(**overrides):
    data = {
        "id": "sample",
        "source": "notes/sample.md",
        "description": "sample guard",
        "severity": "block",
        "event": ["pre_tool"],
        "match": {"command": r"\brm\s+-rf\b"},
        "message": "do not",
        "tests": {"block": [{"command": "rm -rf build"}], "pass": [{"command": "ls"}]},
    }
    data.update(overrides)
    return data


@pytest.fixture
def guard_dict():
    return make_guard


@pytest.fixture
def build_guard():
    def build(**overrides):
        return guard_from_dict(make_guard(**overrides))

    return build
