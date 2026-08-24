#!/usr/bin/python3

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from run import harbor_command, lock_credential_state, read_cases, read_job_result, source_tree


class CasesTest(unittest.TestCase):
    def test_cases_pin_revision_and_separate_holdout(self):
        dataset, groups, tasks = read_cases(Path(__file__).with_name("cases.json"))
        self.assertEqual(dataset, "terminal-bench/terminal-bench-2-1@6")
        self.assertFalse(set(groups["development"]) & set(groups["holdout"]))
        self.assertEqual(groups["smoke"], ("fix-git",))
        self.assertGreater(tasks["fix-git"].input_tokens, tasks["fix-git"].output_tokens)

    def test_harbor_command_runs_one_task_at_a_time(self):
        _, _, tasks = read_cases(Path(__file__).with_name("cases.json"))
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
                runtime_digest="abc123",
                install_only=True,
            )
        self.assertEqual(command[command.index("--n-concurrent") + 1], "1")
        self.assertEqual(command[command.index("--n-attempts") + 1], "2")
        self.assertIn("terminal-bench/terminal-bench-2-1@6", command)
        self.assertIn("terminal-bench/fix-git", command)
        self.assertIn("foe_agent:FoeAgent", command)
        self.assertIn(f"trace_evaluator={root / 'trace_quality.py'}", command)
        self.assertIn(f"PYTHONPATH={root}", command)
        self.assertIn("input_tokens=120000", command)
        self.assertIn("output_tokens=20000", command)
        self.assertIn("--install-only", command)

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
