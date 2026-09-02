#!/usr/bin/python3
"""Unit tests for the repair-loop runner's pure helpers."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from run_repair_loop import (
    episode_outcome,
    granted_execute_paths,
    plan_warning_codes,
    proposal_contract,
    repair_model_block,
    PipelineError,
)


class PlanReadingTest(unittest.TestCase):
    def test_reads_warning_codes(self):
        plan = {"warnings": [{"code": "external-commands-unavailable"}, {"code": "other"}]}
        self.assertEqual(plan_warning_codes(plan), ["external-commands-unavailable", "other"])

    def test_reads_declared_execute_grants_only(self):
        plan = {
            "resolved_permissions": [
                {
                    "contract": "contract",
                    "permissions": {
                        "execute": [
                            {"path": "/usr/bin/python3.13", "reason": "declared by contract.grants.execute"},
                            {
                                "path": "/usr/lib/ld-linux.so.2",
                                "reason": "ELF dynamic loader for declared by contract.grants.execute",
                            },
                            {"path": "/usr/bin/bash", "reason": "built-in bash and session tools"},
                        ]
                    },
                }
            ]
        }
        self.assertEqual(granted_execute_paths(plan), ["/usr/bin/python3.13"])


class EpisodeOutcomeTest(unittest.TestCase):
    def test_returns_the_final_outcome(self):
        events = [
            {"seq": 0, "type": "episode/start", "data": {"id": "ep"}},
            {"seq": 9, "type": "episode/end", "data": {"outcome": {"kind": "blocked", "code": "missing-capability"}}},
        ]
        self.assertEqual(episode_outcome(events)["kind"], "blocked")

    def test_a_log_without_an_end_event_is_an_error(self):
        with self.assertRaises(PipelineError):
            episode_outcome([{"seq": 0, "type": "episode/start", "data": {}}])


class ProposalContractTest(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp())
        self.contract = proposal_contract(
            self.workspace,
            self.workspace / "candidate-check.py",
            {"provider": "exec", "model": "prepared-candidate", "exec": "/x", "candidate_file": "/y"},
            3,
        )

    def test_completion_requires_a_verified_returned_object(self):
        self.assertEqual(self.contract["done_when"], {"verify": "check", "returns": {"type": "object"}})
        self.assertIn("check", self.contract["tools"])

    def test_the_child_reads_only_the_evidence_workspace(self):
        self.assertEqual(self.contract["grants"], {"read": [str(self.workspace)]})

    def test_the_task_never_names_the_repair(self):
        text = self.contract["task"] + self.contract["instructions"]["role"]
        self.assertNotIn("grants.execute", text)
        self.assertNotIn("/usr/bin/", text)


class RepairModelBlockTest(unittest.TestCase):
    def arguments(self, **overrides) -> argparse.Namespace:
        values = {
            "repair_with_file": None,
            "repair_with_model": None,
            "repair_api_key_file": None,
            "repair_reasoning_effort": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_the_file_mode_uses_the_deterministic_transport(self):
        workspace = Path(tempfile.mkdtemp())
        candidate = workspace / "given.json"
        candidate.write_text("{}", encoding="utf-8")
        block = repair_model_block(self.arguments(repair_with_file=candidate), workspace)
        self.assertEqual(block["provider"], "exec")
        self.assertEqual(block["candidate_file"], str(workspace / "prepared-candidate.json"))
        self.assertTrue((workspace / "prepared-candidate-transport.py").is_file())

    def test_the_model_mode_uses_the_given_route(self):
        block = repair_model_block(
            self.arguments(repair_with_model="acme/coder-1", repair_reasoning_effort="high"),
            Path(tempfile.mkdtemp()),
        )
        self.assertEqual(block, {"provider": "acme", "model": "coder-1", "reasoning_effort": "high"})

    def test_a_route_without_a_slash_is_an_error(self):
        with self.assertRaises(PipelineError):
            repair_model_block(self.arguments(repair_with_model="coder-1"), Path(tempfile.mkdtemp()))


if __name__ == "__main__":
    unittest.main()
