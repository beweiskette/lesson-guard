"""Run the tests that every guard carries with it."""

from __future__ import annotations

from dataclasses import dataclass

from .engine import action_from_case, matches
from .model import Guard, TestCase


@dataclass
class CaseResult:
    guard_id: str
    kind: str  # "block" or "pass"
    index: int
    ok: bool
    case: TestCase

    def describe(self) -> str:
        c = self.case
        subject = c.command if c.command is not None else c.path
        expectation = "should be caught" if self.kind == "block" else "should pass"
        got = "was not caught" if self.kind == "block" else "was caught"
        return f"tests.{self.kind}[{self.index}] {expectation} but {got}: {subject!r}"


def run_guard_tests(guard: Guard) -> list[CaseResult]:
    results = []
    for kind, cases in (("block", guard.block_tests), ("pass", guard.pass_tests)):
        for index, case in enumerate(cases):
            fired = matches(guard, action_from_case(case))
            ok = fired if kind == "block" else not fired
            results.append(CaseResult(guard.id, kind, index, ok, case))
    return results


def failures(guard: Guard) -> list[str]:
    return [r.describe() for r in run_guard_tests(guard) if not r.ok]
