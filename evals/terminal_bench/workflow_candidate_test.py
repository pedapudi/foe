#!/usr/bin/python3

import unittest

from workflow_candidate import create, require_matching_run, validate


class WorkflowCandidateTest(unittest.TestCase):
    def fixture(self):
        return create(
            {
                "source_tree": "git-tree-sha1:" + "1" * 40,
                "runtime_binary": "sha256:" + "2" * 64,
            },
            "sha256:" + "3" * 64,
            {
                "model": "openai-codex/gpt-5.6-sol",
                "reasoning_effort": "low",
                "service_tier": "default",
                "token_policy": "measurement_only",
                "workflow_ownership": "evaluation-runner",
                "completion_governance": "model-report",
            },
            {"reasoning_effort": "high", "model_calls": 60},
        )

    def test_candidate_binds_identity_evidence_controls_and_audit(self):
        candidate = self.fixture()
        self.assertEqual(validate(candidate), candidate)
        self.assertEqual(
            require_matching_run(
                candidate,
                model="openai-codex/gpt-5.6-sol",
                reasoning_effort="low",
                service_tier="default",
                token_policy="measurement_only",
                workflow_ownership="evaluation-runner",
                completion_governance="model-report",
            ),
            {"reasoning_effort": "high", "model_calls": 60},
        )

    def test_tampering_and_a_different_run_are_rejected(self):
        candidate = self.fixture()
        candidate["independent_audit"]["model_calls"] = 61
        with self.assertRaisesRegex(ValueError, "digest"):
            validate(candidate)
        candidate = self.fixture()
        with self.assertRaisesRegex(ValueError, "base configuration"):
            require_matching_run(
                candidate,
                model="openai-codex/gpt-5.6-sol",
                reasoning_effort="high",
                service_tier="default",
                token_policy="measurement_only",
                workflow_ownership="evaluation-runner",
                completion_governance="model-report",
            )

    def test_activation_controls_bind_ownership_and_completion_without_a_task(self):
        candidate = self.fixture()
        self.assertNotIn("task", candidate["base_configuration"])
        with self.assertRaisesRegex(ValueError, "base configuration"):
            require_matching_run(
                candidate,
                model="openai-codex/gpt-5.6-sol",
                reasoning_effort="low",
                service_tier="default",
                token_policy="measurement_only",
                workflow_ownership="foe-built-in",
                completion_governance="model-report",
            )
        with self.assertRaisesRegex(ValueError, "base configuration"):
            require_matching_run(
                candidate,
                model="openai-codex/gpt-5.6-sol",
                reasoning_effort="low",
                service_tier="default",
                token_policy="measurement_only",
                workflow_ownership="evaluation-runner",
                completion_governance="declared-verifier",
            )

    def test_candidate_accepts_a_sha256_git_tree_identity(self):
        candidate = create(
            {
                "source_tree": "git-tree-sha256:" + "4" * 64,
                "runtime_binary": "sha256:" + "2" * 64,
            },
            "sha256:" + "3" * 64,
            {
                "model": "openai-codex/gpt-5.6-sol",
                "reasoning_effort": "low",
                "service_tier": "default",
                "token_policy": "measurement_only",
                "workflow_ownership": "evaluation-runner",
                "completion_governance": "model-report",
            },
            {"reasoning_effort": "high", "model_calls": 60},
        )
        self.assertEqual(validate(candidate), candidate)


if __name__ == "__main__":
    unittest.main()
