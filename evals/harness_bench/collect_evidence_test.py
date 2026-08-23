#!/usr/bin/python3
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "collect_evidence", Path(__file__).with_name("collect_evidence.py")
)
assert _SPEC and _SPEC.loader
collect_evidence = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = collect_evidence
_SPEC.loader.exec_module(collect_evidence)


class CollectEvidenceTest(unittest.TestCase):
    def test_outcome_excludes_returned_artifact(self) -> None:
        outcome = {"kind": "completed", "value": {"large": "x" * 10_000}}
        self.assertEqual(collect_evidence.outcome_identity(outcome), {"kind": "completed"})

    def test_mechanism_excludes_budget_event_payloads(self) -> None:
        compact = collect_evidence.compact_mechanism(
            {
                "completed_children": 2,
                "child_reservations": [{"input_tokens": 4_000}] * 20,
                "child_releases": [{"input_tokens": 3_900}] * 20,
            }
        )
        self.assertEqual(compact, {"completed_children": 2})

    def test_argument_rendering_is_bounded(self) -> None:
        compact = collect_evidence.compact_arguments({"command": "x" * 500})
        self.assertLessEqual(len(compact["command"]), 123)


if __name__ == "__main__":
    unittest.main()
