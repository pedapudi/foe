#!/usr/bin/python3
"""Regression controls for the offline knowledge question grader."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


def load_grader(path: Path):
    spec = importlib.util.spec_from_file_location("knowledge_qa_grader", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import grader at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class KnowledgeQaGraderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        executable = Path(sys.argv[0])
        main_runfiles = next(parent for parent in executable.parents if parent.name == "_main")
        task_dir = main_runfiles / "evals" / "harness_bench" / "knowledge_qa_task"
        cls.grader = load_grader(task_dir / "oracle_grade.py")
        cls.oracle = main_runfiles / "evals" / "harness_bench" / "knowledge_qa_oracle.json"

    def grade(self, mutate=None):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "out").mkdir()
            answers = json.loads(self.oracle.read_text(encoding="utf-8"))
            if mutate is not None:
                mutate(answers)
            (workspace / "out" / "answers.json").write_text(
                json.dumps(answers), encoding="utf-8"
            )
            return self.grader.score_workspace(workspace)

    def test_correct_answers_may_quote_rejected_evidence(self) -> None:
        result = self.grade()
        self.assertEqual(result["outcome_score"], 1.0)
        self.assertEqual(result["level"], "excellent")
        checks = {check["id"]: check for check in result["checks"]}
        self.assertEqual(checks["no_fabrication"]["detail"], {"hits": 14})
        self.assertTrue(checks["no_fabrication"]["pass"])
        self.assertEqual(sum(check["weight"] for check in result["checks"]), 1.0)

    def test_forbidden_claim_still_caps_score(self) -> None:
        def insert_forbidden_claim(answers):
            row = next(row for row in answers["answers"] if row["question_id"] == "Q13")
            row["answer"] = "Admin accounts can still use legacy API tokens."

        result = self.grade(insert_forbidden_claim)
        checks = {check["id"]: check for check in result["checks"]}
        self.assertFalse(checks["no_fabrication"]["pass"])
        self.assertEqual(result["outcome_score"], 0.65)

    def test_answer_may_identify_and_reject_stale_value(self) -> None:
        def reject_stale_value(answers):
            row = next(row for row in answers["answers"] if row["question_id"] == "Q14")
            row["status"] = "answered"
            row["answer"] = (
                "No approved after-hours support phone number should be used. "
                "The archived 555-0134 value was an unapproved draft placeholder."
            )
            row["missing_evidence"] = []

        result = self.grade(reject_stale_value)
        checks = {check["id"]: check for check in result["checks"]}
        self.assertTrue(checks["no_fabrication"]["pass"])
        self.assertGreater(result["outcome_score"], 0.95)


if __name__ == "__main__":
    unittest.main()
