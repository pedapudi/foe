#!/usr/bin/python3
"""Unit tests for the operational-failure digest over fixture attempt logs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from operational_digest import digest, digest_attempt, failure_field


def event(seq: int, kind: str, payload: dict) -> dict:
    return {"seq": seq, "type": kind, "data": payload}


def tool_result(seq: int, name: str, *, is_error=False, failure=None, value=None) -> dict:
    payload = {
        "step": 1,
        "call_id": f"call-{seq}",
        "name": name,
        "value": value if value is not None else {},
        "rendered": "",
        "is_error": is_error,
        "duration_ms": 1,
        "synthetic": False,
    }
    if failure is not None:
        payload["failure"] = failure
    return event(seq, "tool/result", payload)


def write_attempt(
    directory: Path,
    events: list[dict],
    *,
    warnings: list[dict] | None = None,
    evaluation: dict | None = None,
) -> Path:
    directory.mkdir(parents=True)
    (directory / "contract.json").write_text(json.dumps({"version": 4, "name": "fixture"}))
    (directory / "plan.json").write_text(
        json.dumps({"contract_fingerprint": "sha256:" + "0" * 64, "warnings": warnings or []})
    )
    episode = directory / "episode"
    episode.mkdir()
    (episode / "episode.jsonl").write_text("\n".join(json.dumps(item) for item in events) + "\n")
    if evaluation is not None:
        (directory / "evaluation.json").write_text(json.dumps(evaluation))
    return directory


def failing_attempt(root: Path, name: str = "attempt-baseline") -> Path:
    events = [
        event(0, "episode/start", {"id": "ep_base", "contract_fingerprint": "sha256:" + "0" * 64}),
        tool_result(
            3,
            "bash",
            value={
                "command": "/usr/bin/python3 report.py",
                "exit_code": 126,
                "permission_denial": "possible",
                "stderr": "/bin/bash: line 1: /usr/bin/python3: Permission denied\n",
            },
        ),
        tool_result(
            5,
            "bash",
            value={
                "command": "/usr/bin/python3 report.py",
                "exit_code": 126,
                "permission_denial": "possible",
            },
        ),
        tool_result(
            7,
            "read",
            is_error=True,
            failure={
                "code": "capability-denied",
                "message": "read: /etc/passwd is outside this tool's filesystem permissions",
                "retryable": False,
                "details": {"path": "/etc/passwd"},
            },
        ),
        tool_result(
            9,
            "edit",
            is_error=True,
            failure={
                "code": "invalid-call",
                "message": "arguments: lacks required property `path`",
                "retryable": True,
                "details": {},
            },
        ),
        tool_result(
            11,
            "edit",
            is_error=True,
            failure={
                "code": "invalid-call",
                "message": "arguments: lacks required property `path`",
                "retryable": True,
                "details": {},
            },
        ),
        event(12, "episode/end", {"outcome": {"kind": "blocked", "code": "missing-capability", "message": "denied"}}),
    ]
    warnings = [
        {
            "code": "external-commands-unavailable",
            "configuration_key": "contract.grants.execute",
            "contract": "contract",
            "message": "contract.grants.execute is empty",
        }
    ]
    evaluation = {
        "schema_version": 1,
        "fixture": "fixture",
        "verdict": "fail",
        "checks": [{"name": "task-artifact", "passed": True, "detail": "artifact present"}],
    }
    return write_attempt(root / name, events, warnings=warnings, evaluation=evaluation)


class FailureFieldTest(unittest.TestCase):
    def test_reads_the_details_field_first(self):
        failure = {"message": "arguments: lacks required property `path`", "details": {"field": "command"}}
        self.assertEqual(failure_field(failure), "command")

    def test_reads_a_required_property_from_the_message(self):
        failure = {"message": "arguments: lacks required property `path`", "details": {}}
        self.assertEqual(failure_field(failure), "path")

    def test_reads_an_unexpected_property_from_the_message(self):
        failure = {"message": "arguments has unexpected property `depth`", "details": {}}
        self.assertEqual(failure_field(failure), "depth")

    def test_returns_none_when_no_field_is_named(self):
        failure = {"message": "No tool named `block` is available to this contract.", "details": {}}
        self.assertIsNone(failure_field(failure))


class DigestAttemptTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.attempt = failing_attempt(self.root)
        self.report = digest_attempt(self.attempt)

    def test_reports_the_configuration_warning_from_plan_json(self):
        warnings = self.report["configuration_warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], "external-commands-unavailable")
        self.assertEqual(warnings[0]["source"], "plan.json")

    def test_reports_enforced_denials_with_citations(self):
        rows = self.report["enforced_permission_denials"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["episode_id"], "ep_base")
        self.assertEqual(rows[0]["seq"], 7)
        self.assertEqual(rows[0]["details"], {"path": "/etc/passwd"})

    def test_labels_possible_denials_as_heuristic(self):
        section = self.report["possible_permission_denials"]
        self.assertIn("heuristic", section["basis"])
        self.assertIn("not an established cause", section["basis"])
        self.assertEqual([row["seq"] for row in section["rows"]], [3, 5])
        self.assertTrue(all(row["exit_code"] == 126 for row in section["rows"]))

    def test_counts_typed_failures_by_tool_code_and_field(self):
        rows = self.report["typed_failure_counts"]
        invalid = [row for row in rows if row["failure_code"] == "invalid-call"]
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0]["tool"], "edit")
        self.assertEqual(invalid[0]["field"], "path")
        self.assertEqual(invalid[0]["count"], 2)
        self.assertEqual([citation["seq"] for citation in invalid[0]["citations"]], [9, 11])

    def test_reports_repeated_failed_commands(self):
        rows = self.report["repeated_failed_commands"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["command"], "/usr/bin/python3 report.py")
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual([citation["seq"] for citation in rows[0]["citations"]], [3, 5])

    def test_counts_calls_before_first_productive_action(self):
        rows = self.report["calls_before_first_productive"]
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["first_productive"])
        self.assertEqual(rows[0]["calls_before"], 5)

    def test_pairs_a_passing_artifact_with_a_noncompleted_outcome(self):
        rows = self.report["completed_artifacts_with_noncompleted_outcomes"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["check"], "task-artifact")
        self.assertEqual(rows[0]["outcome"]["kind"], "blocked")


class ProductiveActionTest(unittest.TestCase):
    def test_a_successful_execution_ends_the_count(self):
        root = Path(tempfile.mkdtemp())
        events = [
            event(0, "episode/start", {"id": "ep_ok"}),
            tool_result(2, "read", value={}),
            tool_result(4, "bash", value={"command": "true", "exit_code": 0}),
            tool_result(6, "bash", value={"command": "true", "exit_code": 0}),
            event(7, "episode/end", {"outcome": {"kind": "completed", "value": "done"}}),
        ]
        attempt = write_attempt(root / "attempt", events)
        rows = digest_attempt(attempt)["calls_before_first_productive"]
        self.assertEqual(rows[0]["first_productive"], {"seq": 4, "kind": "successful-execution"})
        self.assertEqual(rows[0]["calls_before"], 1)

    def test_an_accepted_verification_ends_the_count(self):
        root = Path(tempfile.mkdtemp())
        events = [
            event(0, "episode/start", {"id": "ep_verify"}),
            tool_result(2, "read", value={}),
            event(4, "verification/result", {"status": "accepted", "tool": "check"}),
            event(5, "episode/end", {"outcome": {"kind": "completed", "value": {}}}),
        ]
        attempt = write_attempt(root / "attempt", events)
        rows = digest_attempt(attempt)["calls_before_first_productive"]
        self.assertEqual(rows[0]["first_productive"], {"seq": 4, "kind": "verification-accepted"})
        self.assertEqual(rows[0]["calls_before"], 1)


class CrossAttemptTest(unittest.TestCase):
    def test_aggregates_only_the_declared_directories(self):
        root = Path(tempfile.mkdtemp())
        first = failing_attempt(root, "attempt-one")
        second = failing_attempt(root, "attempt-two")
        undeclared = failing_attempt(root, "attempt-undeclared")
        report = digest([first, second])
        cross = report["cross_attempt"]
        self.assertEqual(len(report["attempts"]), 2)
        invalid = [row for row in cross["typed_failure_counts"] if row["failure_code"] == "invalid-call"]
        self.assertEqual(invalid[0]["count"], 4)
        self.assertEqual(len(invalid[0]["attempts"]), 2)
        self.assertNotIn(str(undeclared), invalid[0]["attempts"])
        totals = cross["permission_denial_totals"]
        self.assertEqual([row["possible"] for row in totals], [2, 2])
        self.assertEqual([row["enforced"] for row in totals], [1, 1])
        warning = cross["configuration_warnings"][0]
        self.assertEqual(warning["code"], "external-commands-unavailable")
        self.assertEqual(len(warning["attempts"]), 2)

    def test_a_missing_attempt_directory_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            digest([Path(tempfile.mkdtemp()) / "absent"])


if __name__ == "__main__":
    unittest.main()
