#!/usr/bin/python3

import json
import tempfile
import unittest
from pathlib import Path

from run_verifier_controls import (
    checker_control_command,
    evaluate_control_job,
    read_verifier_cases,
)


class VerifierControlTest(unittest.TestCase):
    def test_checker_control_matches_foe_empty_environment(self):
        self.assertEqual(
            checker_control_command("/tmp/foe-completion-check"),
            "/usr/bin/env -i /tmp/foe-completion-check",
        )

    def test_case_file_pins_dataset_and_resolves_artifacts(self):
        cases_path = Path(__file__).with_name("verifier_cases.json")
        cases = read_verifier_cases(
            cases_path,
            "terminal-bench/terminal-bench-2-1@6",
        )
        self.assertEqual(
            set(cases),
            {
                "cancel-async-tasks",
                "dna-assembly",
                "fix-git",
                "git-multibranch",
                "gpt2-codegolf",
                "large-scale-text-editing",
            },
        )
        self.assertTrue(cases["cancel-async-tasks"].checker.is_file())
        self.assertTrue(cases["cancel-async-tasks"].oracle.is_file())
        self.assertIn(
            "differ from the task-owned verifier input",
            cases["gpt2-codegolf"].contract,
        )
        self.assertEqual(
            cases["cancel-async-tasks"].checker.read_text(encoding="utf-8").splitlines()[0],
            "#!/usr/local/bin/python3",
        )
        with self.assertRaisesRegex(ValueError, "must equal"):
            read_verifier_cases(cases_path, "terminal-bench/terminal-bench-2-1@7")

    def test_control_job_requires_both_controls_and_the_hidden_reward(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "result.json").write_text(
                json.dumps(
                    {
                        "stats": {
                            "n_completed_trials": 1,
                            "n_errored_trials": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            trial = job / "task__attempt"
            trial.mkdir()
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "exception_info": None,
                        "agent_result": {
                            "metadata": {
                                "foe_verifier_controls": {
                                    "negative_control": {
                                        "accepted": False,
                                        "findings": ["artifact is missing"],
                                    },
                                    "oracle_control": {
                                        "accepted": True,
                                        "findings": [],
                                    },
                                }
                            }
                        },
                        "verifier_result": {"rewards": {"reward": 1.0}},
                    }
                ),
                encoding="utf-8",
            )
            result = evaluate_control_job(job)
            self.assertEqual(result["oracle_reward"], 1.0)
            self.assertEqual(result["negative_findings"], ["artifact is missing"])


if __name__ == "__main__":
    unittest.main()
