#!/usr/bin/python3
"""Unit tests for conditions.py: no model, no network, no binary.

Every test checks the docs/evaluation.md gate "Mechanism exercised": a run in
which the property under test did not occur is reported apart, so each
attempt states whether it reached the condition its arm's control tests.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "contracts"))

import conditions  # noqa: E402
import graphs  # noqa: E402
import normalize_foe  # noqa: E402

WORKSPACE = Path("/w")
CHECK = Path("/w/checks/run.sh")
BUDGET = {"model_calls": 40, "seconds": 600}


def call(name: str) -> dict[str, Any]:
    return {"name": name, "started_ms": 1, "ended_ms": 2, "is_error": False, "arguments_digest": None, "summary": ""}


def agent(role: str, *names: str) -> dict[str, Any]:
    return {"id": role, "role": role, "depth": 0 if role == "root" else 1, "tool_calls": [call(name) for name in names]}


def record(arm: str, *agents: dict[str, Any], config: str | None = None) -> dict[str, Any]:
    trajectory = {"agents": list(agents)} if agents else None
    return {"arm": arm, "trajectory": trajectory, "arm_result": {"record": {"config": config}}}


SOLVABLE = {"name": "probe", "class_name": "solvable", "metadata": {}}
NON_TERMINATING = {"name": "probe-wait", "class_name": "non-terminating", "metadata": {}}


def entered(value: bool) -> conditions.WaitEntry:
    def wait_entry(_trajectory: dict[str, Any], _task: Mapping[str, Any]) -> dict[str, Any]:
        return {"entered": value, "evidence": [f"entered is {value}"]}

    return wait_entry


class RuntimeVerifierInvoked(unittest.TestCase):
    """The configured and lean arms test the runtime verifier, so an attempt counts only when it ran."""

    def test_a_verification_result_reaches_the_condition(self) -> None:
        for arm in ("foe-configured", "foe-lean"):
            result = conditions.condition_reached(record(arm, agent("root", normalize_foe.VERIFICATION_NAME), agent("implement", "check")), SOLVABLE)
            self.assertEqual(result["condition"], conditions.VERIFIER_INVOKED, arm)
            self.assertIs(result["reached"], True, arm)
            self.assertIn("1 verification/result calls", result["evidence"][0], arm)

    def test_the_verifier_timeout_shape_does_not_reach_it(self) -> None:
        """A regression from the verifier-timeout records: the implementing node called `check` and then `block`, and the runtime verified nothing."""
        result = conditions.condition_reached(record("foe-configured", agent("root"), agent("survey", "read"), agent("implement", "check", "check", "block")), NON_TERMINATING, wait_entry=entered(True))
        self.assertEqual(result["condition"], "runtime verifier invoked and wait entered")
        self.assertIs(result["reached"], False)
        self.assertEqual([part["reached"] for part in result["parts"]], [False, True])
        own = conditions.condition_reached(record("foe-configured", agent("implement", "check", "block")), SOLVABLE)
        self.assertEqual((own["condition"], own["reached"]), (conditions.VERIFIER_INVOKED, False))

    def test_a_record_without_a_trajectory_reaches_nothing(self) -> None:
        result = conditions.condition_reached(record("foe-configured"), SOLVABLE)
        self.assertIs(result["reached"], False)
        self.assertEqual(result["evidence"], ["the record holds no normalized trajectory"])


class VerifierAbsentStopAvailable(unittest.TestCase):
    """The unverified arm tests the verifier alone, so the runtime must verify nothing while `block` stays offered."""

    def setUp(self) -> None:
        self.unverified = graphs.autonomy(WORKSPACE, CHECK, BUDGET, "unverified")

    def test_no_verification_and_block_offered_reaches_it(self) -> None:
        result = conditions.condition_reached(record("foe-unverified", agent("implement", "check", "block")), SOLVABLE, document=self.unverified)
        self.assertEqual(result["condition"], conditions.VERIFIER_ABSENT)
        self.assertIs(result["reached"], True)
        self.assertIn("root.nodes.implement", result["evidence"][1])

    def test_a_verification_result_or_a_missing_block_does_not(self) -> None:
        ran = conditions.condition_reached(record("foe-unverified", agent("root", normalize_foe.VERIFICATION_NAME)), SOLVABLE, document=self.unverified)
        self.assertIs(ran["reached"], False)
        ablated = graphs.autonomy(WORKSPACE, CHECK, BUDGET, "ablated")
        missing = conditions.condition_reached(record("foe-unverified", agent("implement", "check")), SOLVABLE, document=ablated)
        self.assertIs(missing["reached"], False)
        self.assertIn("block offered by no contract", missing["evidence"])

    def test_the_document_is_read_from_the_path_the_record_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = graphs.write(self.unverified, Path(tmp) / "config.json")
            result = conditions.condition_reached(record("foe-unverified", agent("implement", "read"), config=str(path)), SOLVABLE)
            self.assertIs(result["reached"], True)
            gone = conditions.condition_reached(record("foe-unverified", agent("implement", "read"), config=str(Path(tmp) / "absent.json")), SOLVABLE)
            self.assertIs(gone["reached"], False)
            self.assertIn("cannot be read", gone["evidence"][-1])


    def test_a_declared_verifier_or_an_uninstructed_block_does_not(self) -> None:
        """docs/evaluation.md gate "Mechanism exercised": the document itself must hold the control, whatever the trajectory shows."""
        verified = copy.deepcopy(self.unverified)
        verified["workflow"]["nodes"]["implement"]["verify"] = "check"
        result = conditions.condition_reached(record("foe-unverified", agent("implement", "check")), SOLVABLE, document=verified)
        self.assertIs(result["reached"], False)
        self.assertIn("verifiers declared at root.nodes.implement.verify", result["evidence"])
        silent = copy.deepcopy(self.unverified)
        silent["workflow"]["nodes"]["repair"]["model"]["instructions"]["20-contract"] = "Treat the task text as the source of truth."
        result = conditions.condition_reached(record("foe-unverified", agent("implement", "check")), SOLVABLE, document=silent)
        self.assertIs(result["reached"], False)
        self.assertIn("block offered without its instruction by root.nodes.repair", result["evidence"])


class StopMechanismAbsent(unittest.TestCase):
    """The ablated arm tests the runtime without its stop, so no contract of its document may offer `block`."""

    def test_the_ablated_document_reaches_it_and_the_configured_one_does_not(self) -> None:
        ablated = conditions.condition_reached(record("foe-ablated", agent("implement", "edit")), SOLVABLE, document=graphs.autonomy(WORKSPACE, CHECK, BUDGET, "ablated"))
        self.assertEqual((ablated["condition"], ablated["reached"]), (conditions.STOP_ABSENT, True))
        leaked = conditions.condition_reached(record("foe-ablated", agent("implement", "edit")), SOLVABLE, document=graphs.autonomy(WORKSPACE, CHECK, BUDGET))
        self.assertIs(leaked["reached"], False)

    def test_an_ablated_document_that_declares_a_verifier_or_instructs_block_does_not(self) -> None:
        ablated = graphs.autonomy(WORKSPACE, CHECK, BUDGET, "ablated")
        verified = copy.deepcopy(ablated)
        verified["done_when"] = {"verify": "check", "retries": 1}
        result = conditions.condition_reached(record("foe-ablated", agent("implement", "edit")), SOLVABLE, document=verified)
        self.assertIs(result["reached"], False)
        self.assertIn("verifiers declared at root.done_when.verify", result["evidence"])
        told = copy.deepcopy(ablated)
        told["workflow"]["nodes"]["implement"]["model"]["instructions"]["30-stop"] = "When the task cannot be done, call `block`."
        self.assertIs(conditions.condition_reached(record("foe-ablated", agent("implement", "edit")), SOLVABLE, document=told)["reached"], False)


class NoneDeclared(unittest.TestCase):
    """A Codex arm declares no controlled mechanism; on a non-terminating task the wait alone is its condition."""

    def test_a_codex_arm_declares_none(self) -> None:
        for arm in ("codex-equivalent", "codex-default", "codex-single", "codex-multi"):
            result = conditions.condition_reached(record(arm, agent("root", "bash")), SOLVABLE)
            self.assertEqual((result["condition"], result["reached"]), (conditions.NONE_DECLARED, None), arm)

    def test_a_codex_arm_on_a_non_terminating_task_tests_the_wait(self) -> None:
        for value in (True, False):
            result = conditions.condition_reached(record("codex-equivalent", agent("root", "bash")), NON_TERMINATING, wait_entry=entered(value))
            self.assertEqual((result["condition"], result["reached"]), (conditions.WAIT_ENTERED, value))
            self.assertEqual(result["parts"][0]["condition"], conditions.NONE_DECLARED)

    def test_the_wait_without_a_trajectory_is_not_entered(self) -> None:
        result = conditions.condition_reached(record("codex-equivalent"), NON_TERMINATING, wait_entry=entered(True))
        self.assertIs(result["reached"], False)


class Records(unittest.TestCase):
    def test_every_record_under_a_records_directory_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            records = Path(tmp)
            for arm, agents in (("foe-configured", [agent("root", normalize_foe.VERIFICATION_NAME)]), ("codex-default", [agent("root", "bash")])):
                path = records / "probe" / arm / "01.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({**record(arm, *agents), "task": SOLVABLE, "attempt": 1}), encoding="utf-8")
            rows = conditions.records_conditions(records)
            self.assertEqual([(row["arm"], row["reached"]) for row in rows], [("codex-default", None), ("foe-configured", True)])


if __name__ == "__main__":
    unittest.main()
