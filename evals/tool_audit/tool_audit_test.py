#!/usr/bin/python3
"""Unit tests for the tool mistake audit's case table and checks."""

from __future__ import annotations

import unittest

import responses
import run_tool_audit

# The closed failure-code vocabulary from docs/tools.md.
CODES = {
    "invalid-call",
    "capability-denied",
    "unavailable",
    "process-start-failed",
    "process-exit",
    "timed-out",
    "budget-exhausted",
    "limit-exceeded",
    "operation-failed",
    "interrupted",
}

# Every tool the mistake episode's contract declares.
DECLARED = {"read", "grep", "edit", "bash", "session", "compose_tools", "retrieve"}


class CaseTable(unittest.TestCase):
    def test_case_ids_are_unique(self) -> None:
        names = [entry["case"] for entry in run_tool_audit.CASES]
        self.assertEqual(len(names), len(set(names)))

    def test_every_declared_tool_is_audited(self) -> None:
        audited = {entry["tool"] for entry in run_tool_audit.CASES}
        self.assertTrue(DECLARED <= audited, DECLARED - audited)

    def test_every_code_is_in_the_closed_vocabulary(self) -> None:
        for entry in run_tool_audit.CASES:
            self.assertIn(entry["code"], CODES, entry["case"])

    def test_the_common_mistake_kinds_are_present(self) -> None:
        names = {entry["case"] for entry in run_tool_audit.CASES}
        for required in (
            "read-wrong-field-name",
            "read-missing-required-path",
            "read-path-wrong-type",
            "read-offset-below-minimum",
            "session-task-lifetime-without-grant",
        ):
            self.assertIn(required, names)

    def test_capability_denials_are_distinct_and_final(self) -> None:
        for entry in run_tool_audit.CASES:
            if entry["code"] == "capability-denied":
                self.assertFalse(entry["retryable"], entry["case"])

    def test_call_ids_match_case_names(self) -> None:
        for entry in run_tool_audit.CASES:
            self.assertEqual(entry["call"]["id"], entry["case"])


class Checks(unittest.TestCase):
    def result(self, **overrides) -> dict:
        base = {
            "call_id": "read-missing-required-path",
            "name": "read",
            "is_error": True,
            "failure": {
                "code": "invalid-call",
                "message": "The arguments for `read` are invalid: arguments: lacks required property `path`",
                "retryable": True,
                "details": {},
            },
        }
        base.update(overrides)
        return base

    def entry(self) -> dict:
        return next(e for e in run_tool_audit.CASES if e["case"] == "read-missing-required-path")

    def test_matching_result_yields_no_findings(self) -> None:
        results = {"read-missing-required-path": self.result()}
        self.assertEqual(run_tool_audit.check_case(self.entry(), results, {}), [])

    def test_changed_message_is_a_finding(self) -> None:
        changed = self.result()
        changed["failure"] = dict(changed["failure"], message="something else entirely")
        results = {"read-missing-required-path": changed}
        findings = run_tool_audit.check_case(self.entry(), results, {})
        self.assertTrue(any("message differs" in finding for finding in findings))

    def test_changed_code_is_a_finding(self) -> None:
        changed = self.result()
        changed["failure"] = dict(changed["failure"], code="operation-failed")
        results = {"read-missing-required-path": changed}
        findings = run_tool_audit.check_case(self.entry(), results, {})
        self.assertTrue(any("failure code" in finding for finding in findings))

    def test_missing_result_is_a_finding(self) -> None:
        findings = run_tool_audit.check_case(self.entry(), {}, {})
        self.assertEqual(len(findings), 1)
        self.assertIn("no tool/result", findings[0])

    def test_substitute_replaces_markers(self) -> None:
        out = run_tool_audit.substitute(f"at {run_tool_audit.DENIED}", {run_tool_audit.DENIED: "/d/secret"})
        self.assertEqual(out, "at /d/secret")

    def test_inventory_counts_by_code(self) -> None:
        summary = run_tool_audit.inventory(run_tool_audit.CASES)
        self.assertEqual(summary["cases"], len(run_tool_audit.CASES))
        self.assertEqual(sum(summary["by_code"].values()), len(run_tool_audit.CASES))


class ResponseExpansion(unittest.TestCase):
    def test_expand_replaces_byte_sentinel(self) -> None:
        expanded = responses.expand({"source": {"$bytes": 5}, "other": 1})
        self.assertEqual(expanded, {"source": "#####", "other": 1})

    def test_expand_passes_non_objects_through(self) -> None:
        self.assertEqual(responses.expand([1, 2]), [1, 2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
