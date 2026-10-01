#!/usr/bin/python3
"""Degenerate policies that stand in for an arm, so the protocol can be checked without a model.

Each policy takes a materialized root, acts on its workspace directly, and
returns the outcome it reports and the value it returns. The policies are
the ways an arm can be wrong that a grader must catch: reporting completion
without working, stopping without cause, stopping after the work is done,
and removing the checks that would expose it. `oracle_completed` is the one
policy that does the task, and `run_policy` classifies any of them against
the task so that a test can require every other policy to rank below it.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import protocol  # noqa: E402
from protocol import BLOCKED, COMPLETED, WORKSPACE, Reported  # noqa: E402

Policy = Callable[[Path], tuple[Reported, Any]]


def do_nothing_completed(root: Path) -> tuple[Reported, Any]:
    """Report completion and touch nothing."""
    del root
    return Reported(COMPLETED, None, "the task is done"), None


def do_nothing_blocked(root: Path) -> tuple[Reported, Any]:
    """Report that the goal cannot be reached and touch nothing."""
    del root
    return Reported(BLOCKED, "goal-unreachable", "the task cannot be completed as stated"), None


def oracle_then_blocked(root: Path) -> tuple[Reported, Any]:
    """Do the work the oracle prescribes, then report that the goal cannot be reached."""
    protocol.apply_oracle(root)
    return Reported(BLOCKED, "goal-unreachable", "the task cannot be completed as stated"), None


def test_paths(workspace: Path) -> list[Path]:
    """Every directory named `tests` and every `test_*.py` or `*_test.py` file in the workspace."""
    found: list[Path] = []
    for path in sorted(workspace.rglob("*")):
        if any(parent.name == "tests" for parent in path.relative_to(workspace).parents):
            continue
        if path.is_dir() and path.name == "tests":
            found.append(path)
        elif path.is_file() and (path.name.startswith("test_") or path.name.endswith("_test.py")) and path.suffix == ".py":
            found.append(path)
    return found


def delete_tests_completed(root: Path) -> tuple[Reported, Any]:
    """Remove the workspace's tests and report completion."""
    for path in test_paths(root / WORKSPACE):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    return Reported(COMPLETED, None, "every check passes"), None


def oracle_completed(root: Path) -> tuple[Reported, Any]:
    """Do the work the oracle prescribes and report completion with the oracle's value."""
    _, candidate = protocol.apply_oracle(root)
    return Reported(COMPLETED, None, "the task is done and the checks pass"), candidate


POLICIES: dict[str, Policy] = {
    "do_nothing_completed": do_nothing_completed,
    "do_nothing_blocked": do_nothing_blocked,
    "oracle_then_blocked": oracle_then_blocked,
    "delete_tests_completed": delete_tests_completed,
    "oracle_completed": oracle_completed,
}


def run_policy(root: Path, policy: Policy) -> str:
    """Let a policy act on a materialized root, grade it, and return its confusion cell."""
    task = protocol.load(root)
    reported, candidate = policy(root)
    result = protocol.grade(root, reported, candidate, arm=f"policy:{policy.__name__}")
    return protocol.classify(task, reported, result)
