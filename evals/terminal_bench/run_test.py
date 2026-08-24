#!/usr/bin/python3

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from run import (
    harbor_command,
    lock_credential_state,
    read_cases,
    read_job_integrity,
    read_job_result,
    source_tree,
)


class CasesTest(unittest.TestCase):
    def test_cases_pin_revision_and_separate_task_sets(self):
        dataset, groups, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        self.assertEqual(dataset, "terminal-bench/terminal-bench-2-1@6")
        protected = (
            "development",
            "capability_search",
            "confirmation",
            "calibration",
            "calibration_holdout",
        )
        for index, left in enumerate(protected):
            for right in protected[index + 1 :]:
                self.assertFalse(set(groups[left]) & set(groups[right]))
        self.assertEqual(len(groups["development"]), 6)
        self.assertEqual(len(groups["capability_search"]), 12)
        self.assertEqual(len(groups["confirmation"]), 4)
        self.assertEqual(len(groups["calibration"]), 12)
        self.assertEqual(len(groups["calibration_holdout"]), 6)
        self.assertEqual(groups["smoke"], ("fix-git",))
        self.assertGreater(
            tasks["fix-git"].expected_input_tokens,
            tasks["fix-git"].expected_output_tokens,
        )
        self.assertGreaterEqual(min(task.model_calls for task in tasks.values()), 60)
        self.assertGreaterEqual(min(task.seconds for task in tasks.values()), 1800)
        self.assertEqual(pricing["openai-codex/gpt-5.6-sol"].output_per_million, 20.0)

    def test_harbor_command_runs_one_task_at_a_time(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = harbor_command(
                harbor=Path("/tools/harbor"),
                dataset="terminal-bench/terminal-bench-2-1@6",
                task=tasks["fix-git"],
                attempts=2,
                jobs_dir=root / "jobs",
                agent_module=root / "foe_agent.py",
                trace_evaluator=root / "trace_quality.py",
                foe=root / "foe",
                credential_state=root / "private.json",
                model="openai-codex/gpt-5.6-sol",
                reasoning_effort="low",
                diagnosis_model=None,
                diagnosis_reasoning_effort="high",
                diagnosis_model_calls=6,
                diagnosis_pricing=None,
                unresolved_diagnosis_reasoning_effort=None,
                unresolved_diagnosis_model_calls=6,
                escalation_reasoning_effort=None,
                escalation_model_calls=0,
                runtime_digest="abc123",
                pricing=pricing["openai-codex/gpt-5.6-sol"],
                install_only=True,
            )
        self.assertEqual(command[command.index("--n-concurrent") + 1], "1")
        self.assertEqual(command[command.index("--n-attempts") + 1], "2")
        self.assertIn("terminal-bench/terminal-bench-2-1@6", command)
        self.assertIn("terminal-bench/fix-git", command)
        self.assertIn("foe_agent:FoeAgent", command)
        self.assertIn(f"trace_evaluator={root / 'trace_quality.py'}", command)
        self.assertIn(f"PYTHONPATH={root}", command)
        self.assertNotIn("input_tokens=120000", command)
        self.assertNotIn("output_tokens=20000", command)
        self.assertIn("input_per_million=4.0", command)
        self.assertIn("--install-only", command)

    def test_hard_token_limits_are_an_explicit_runner_option(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        command = harbor_command(
            harbor=Path("/tools/harbor"),
            dataset="terminal-bench/terminal-bench-2-1@6",
            task=tasks["fix-git"],
            attempts=1,
            jobs_dir=Path("/tmp/jobs"),
            agent_module=Path("/tmp/foe_agent.py"),
            trace_evaluator=Path("/tmp/score-trace"),
            foe=Path("/tmp/foe"),
            credential_state=Path("/tmp/private.json"),
            model="openai-codex/gpt-5.6-sol",
            reasoning_effort="low",
            diagnosis_model=None,
            diagnosis_reasoning_effort="high",
            diagnosis_model_calls=6,
            diagnosis_pricing=None,
            unresolved_diagnosis_reasoning_effort=None,
            unresolved_diagnosis_model_calls=6,
            escalation_reasoning_effort=None,
            escalation_model_calls=0,
            runtime_digest="abc123",
            pricing=pricing["openai-codex/gpt-5.6-sol"],
            hard_token_limits=True,
        )
        self.assertIn("input_tokens=120000", command)
        self.assertIn("output_tokens=20000", command)

    def test_harbor_command_records_the_diagnosis_model_and_its_pricing(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        command = harbor_command(
            harbor=Path("/tools/harbor"),
            dataset="terminal-bench/terminal-bench-2-1@6",
            task=tasks["fix-git"],
            attempts=1,
            jobs_dir=Path("/tmp/jobs"),
            agent_module=Path("/tmp/foe_agent.py"),
            trace_evaluator=Path("/tmp/score-trace"),
            foe=Path("/tmp/foe"),
            credential_state=Path("/tmp/private.json"),
            model="openai-codex/gpt-5.6-sol",
            reasoning_effort="low",
            diagnosis_model="openai-codex/gpt-5.6-luna",
            diagnosis_reasoning_effort="high",
            diagnosis_model_calls=6,
            diagnosis_pricing=pricing["openai-codex/gpt-5.6-luna"],
            unresolved_diagnosis_reasoning_effort=None,
            unresolved_diagnosis_model_calls=6,
            escalation_reasoning_effort="xhigh",
            escalation_model_calls=18,
            runtime_digest="abc123",
            pricing=pricing["openai-codex/gpt-5.6-sol"],
        )
        self.assertIn("diagnosis_model=openai-codex/gpt-5.6-luna", command)
        self.assertIn("diagnosis_model_calls=6", command)
        self.assertIn("diagnosis_input_per_million=0.2", command)
        self.assertIn("escalation_reasoning_effort=xhigh", command)
        self.assertIn("escalation_model_calls=18", command)

    def test_harbor_command_records_conditional_unresolved_diagnosis(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        command = harbor_command(
            harbor=Path("/tools/harbor"),
            dataset="terminal-bench/terminal-bench-2-1@6",
            task=tasks["gpt2-codegolf"],
            attempts=1,
            jobs_dir=Path("/tmp/jobs"),
            agent_module=Path("/tmp/foe_agent.py"),
            trace_evaluator=Path("/tmp/score-trace"),
            foe=Path("/tmp/foe"),
            credential_state=Path("/tmp/private.json"),
            model="openai-codex/gpt-5.6-sol",
            reasoning_effort="low",
            diagnosis_model="openai-codex/gpt-5.6-luna",
            diagnosis_reasoning_effort="high",
            diagnosis_model_calls=6,
            diagnosis_pricing=pricing["openai-codex/gpt-5.6-luna"],
            unresolved_diagnosis_reasoning_effort="xhigh",
            unresolved_diagnosis_model_calls=12,
            escalation_reasoning_effort=None,
            escalation_model_calls=0,
            runtime_digest="abc123",
            pricing=pricing["openai-codex/gpt-5.6-sol"],
        )
        self.assertIn("unresolved_diagnosis_reasoning_effort=xhigh", command)
        self.assertIn("unresolved_diagnosis_model_calls=12", command)

    def test_job_result_reports_trial_exceptions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(
                json.dumps(
                    {
                        "n_total_trials": 2,
                        "stats": {
                            "n_completed_trials": 2,
                            "n_errored_trials": 1,
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = read_job_result(path)
        self.assertEqual(result["n_errored_trials"], 1)

    def test_job_integrity_separates_runtime_failure_from_missing_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            trial = job / "task__attempt"
            trial.mkdir()
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "trial_name": "task__attempt",
                        "exception_info": None,
                        "agent_result": {
                            "metadata": {
                                "foe_outcome": {
                                    "kind": "failed",
                                    "error": "provider response ended",
                                },
                                "foe_trace_conformant": True,
                                "foe_usage_reported": False,
                                "foe_unreported_model_calls": 1,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            integrity = read_job_integrity(job)
        self.assertEqual(
            integrity["infrastructure_failures"],
            ["task__attempt: Foe runtime failed: provider response ended"],
        )
        self.assertEqual(
            integrity["incomplete_resource_measurements"],
            ["task__attempt: 1 model call(s) lack provider usage"],
        )

    def test_source_tree_requires_a_clean_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Cargo.toml"
            source.write_text("[workspace]\n", encoding="utf-8")
            subprocess.run(["/usr/bin/git", "init", "--quiet", str(root)], check=True)
            subprocess.run(["/usr/bin/git", "-C", str(root), "add", "Cargo.toml"], check=True)
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=Foe Test",
                    "-c",
                    "user.email=foe@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "Create source tree",
                ],
                check=True,
            )
            self.assertRegex(source_tree(source), r"^git-tree-sha1:[0-9a-f]{40}$")
            (root / "change").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source tree is not clean"):
                source_tree(source)

    def test_credential_state_lock_rejects_a_second_campaign(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "credential.json"
            first = lock_credential_state(state)
            try:
                with self.assertRaisesRegex(ValueError, "another Terminal-Bench run"):
                    lock_credential_state(state)
            finally:
                first.close()


if __name__ == "__main__":
    unittest.main()
