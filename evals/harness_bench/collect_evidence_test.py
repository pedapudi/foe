#!/usr/bin/python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
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
    evaluated = {
        "source_tree": "git-tree-sha1:" + "a" * 40,
        "runtime_binary": "sha256:" + "b" * 64,
    }

    def test_outcome_excludes_returned_artifact(self) -> None:
        outcome = {"kind": "completed", "value": {"large": "x" * 10_000}}
        self.assertEqual(collect_evidence.outcome_summary(outcome), {"kind": "completed"})

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

    def collect_reports(
        self,
        micro_identity: dict[str, str] | None,
        harness_identity: dict[str, str] | None,
    ) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            micro = {"results": [], "aggregate": {}, "evaluated_foe": micro_identity}
            harness = {"attempts": [], "summary": {}, "evaluated_foe": harness_identity}
            micro_path = root / "micro.json"
            harness_path = root / "harness.json"
            micro_path.write_text(json.dumps(micro), encoding="utf-8")
            harness_path.write_text(json.dumps(harness), encoding="utf-8")
            return collect_evidence.collect(micro_path, harness_path)

    def test_matching_evaluated_builds_are_carried_into_evidence(self) -> None:
        evidence = self.collect_reports(self.evaluated, self.evaluated)
        self.assertEqual(evidence["evaluated_foe"], self.evaluated)

    def test_missing_evaluated_build_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "micro evaluation report .* lacks evaluated_foe"):
            self.collect_reports(None, self.evaluated)

    def test_source_and_binary_mismatches_are_rejected(self) -> None:
        for field, replacement in (
            ("source_tree", "git-tree-sha1:" + "c" * 40),
            ("runtime_binary", "sha256:" + "d" * 64),
        ):
            with self.subTest(field=field):
                other = {**self.evaluated, field: replacement}
                with self.assertRaisesRegex(ValueError, f"different {field}"):
                    self.collect_reports(self.evaluated, other)

    def test_prior_self_improvement_build_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            micro = root / "micro.json"
            harness = root / "harness.json"
            result = root / "self-improvement.json"
            micro.write_text(
                json.dumps({"results": [], "aggregate": {}, "evaluated_foe": self.evaluated}),
                encoding="utf-8",
            )
            harness.write_text(
                json.dumps({"attempts": [], "summary": {}, "evaluated_foe": self.evaluated}),
                encoding="utf-8",
            )
            result.write_text(
                json.dumps(
                    {
                        "episode": str(root / "episode"),
                        "evaluated_foe": {
                            **self.evaluated,
                            "runtime_binary": "sha256:" + "d" * 64,
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "self-improvement result .* runtime_binary"):
                collect_evidence.collect(micro, harness, [result])


if __name__ == "__main__":
    unittest.main()
