#!/usr/bin/python3

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from collect_diagnostics import (
    EVALUATION_FIELDS,
    collect,
    evaluation_metadata,
    evaluation_summary,
    input_growth_landmarks,
)


class CollectDiagnosticsTest(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, Path, dict[str, str]]:
        source = root / "source"
        source.mkdir()
        (source / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        subprocess.run(["git", "init", "--quiet", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "Cargo.toml"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Foe Test",
                "-c",
                "user.email=foe@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "Create source",
            ],
            check=True,
        )
        binary = root / "foe"
        binary.write_bytes(b"foe")
        tree = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD^{tree}"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        identity = {
            "source_tree": f"git-tree-sha1:{tree}",
            "runtime_binary": "sha256:" + hashlib.sha256(b"foe").hexdigest(),
        }
        run = root / "run"
        agent = run / "task" / "trial" / "agent"
        agent.mkdir(parents=True)
        (run / "campaign.json").write_text(
            json.dumps(
                {
                    "evaluated_foe": identity,
                    "dataset": "terminal-bench/example@1",
                    "label": "development",
                    "model": "openai-codex/gpt-5.6-luna",
                    "reasoning_effort": "low",
                    "service_tier": "default",
                    "token_limits": "measurement_only",
                    "requested_workers": 2,
                    "concurrency": 2,
                    "diagnosis_model": None,
                    "diagnosis_reasoning_effort": None,
                    "diagnosis_model_calls": None,
                    "unresolved_diagnosis_reasoning_effort": None,
                    "unresolved_diagnosis_model_calls": None,
                    "escalation_reasoning_effort": None,
                    "escalation_model_calls": None,
                    "completion_checker": None,
                }
            ),
            encoding="utf-8",
        )
        (agent / "foe-diagnostics.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "evidence_identity": {
                        "runtime_build": identity["runtime_binary"],
                        "episode_id": "ep_root",
                    },
                    "task": "terminal-bench/example",
                    "verifier_reward": 1.0,
                    "artifact_outcome_mismatch": False,
                    "verifier_feedback": {
                        "failure_classes": [],
                        "summary": {"tests": 1, "passed": 1, "failed": 0},
                    },
                    "verification_timeline": [
                        {
                            "episode_id": "ep_root",
                            "last_edit_seq": 7,
                            "results": [{"seq": 9, "tool": "bash", "exit_code": 0}],
                            "omitted_results": 0,
                            "outcome": {"kind": "completed"},
                        }
                    ],
                    "usage": {
                        "model_calls": 3,
                        "estimated_cost_usd": 0.01,
                        "per_request": [
                            {"seq": 1, "input_tokens": 100},
                            {"seq": 5, "input_tokens": 900},
                            {"seq": 9, "input_tokens": 500},
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        return source / "Cargo.toml", binary, run, identity

    def test_collector_binds_diagnostics_to_source_and_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            source, binary, run, identity = self.fixture(Path(directory))
            report = collect(source, binary, [run], {"example"})
        self.assertEqual(report["evaluated_foe"], identity)
        self.assertEqual(report["schema_version"], 3)
        diagnosis = report["trajectory_diagnostics"][0]
        self.assertEqual(diagnosis["task"], "terminal-bench/example")
        self.assertEqual(diagnosis["evaluation"]["label"], "development")
        self.assertEqual(diagnosis["evaluation"]["reasoning_effort"], "low")
        self.assertNotIn("per_request", diagnosis["usage"])
        self.assertEqual(diagnosis["verifier_feedback"]["summary"]["passed"], 1)
        self.assertEqual(
            diagnosis["verification_timeline"][0]["results"][0]["exit_code"],
            0,
        )
        self.assertEqual([row["seq"] for row in diagnosis["input_growth_landmarks"]], [1, 5, 9])
        self.assertEqual(
            {row["episode_id"] for row in diagnosis["input_growth_landmarks"]},
            {"ep_root"},
        )
        self.assertEqual(
            report["evaluation_summary"],
            [
                {
                    "task": "terminal-bench/example",
                    "model": "openai-codex/gpt-5.6-luna",
                    "reasoning_effort": "low",
                    "execution_configuration": {
                        "service_tier": "default",
                        "token_policy": "measurement_only",
                        "task_execution": {
                            "requested_workers": 2,
                            "scheduled_concurrency": 2,
                        },
                        "implementation": {
                            "model": "openai-codex/gpt-5.6-luna",
                            "reasoning_effort": "low",
                        }
                    },
                    "attempts": 1,
                    "verified_successes": 1,
                    "artifact_outcome_mismatches": 0,
                    "model_calls": 3,
                    "estimated_cost_usd": 0.01,
                }
            ],
        )

    def test_collector_rejects_a_different_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            source, binary, run, _ = self.fixture(Path(directory))
            path = next(run.glob("*/*/agent/foe-diagnostics.json"))
            report = json.loads(path.read_text(encoding="utf-8"))
            report["evidence_identity"]["runtime_build"] = "sha256:" + "0" * 64
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "different runtime identity"):
                collect(source, binary, [run], {"example"})

    def test_input_growth_resets_when_a_second_child_starts_lower(self):
        rows = [
            {"episode_id": "ep_child_a", "seq": 2, "input_tokens": 100},
            {"episode_id": "ep_child_a", "seq": 8, "input_tokens": 900},
            {"episode_id": "ep_child_b", "seq": 3, "input_tokens": 120},
        ]
        landmarks = input_growth_landmarks(rows)
        self.assertEqual(
            [(row["episode_id"], row["seq"], row["input_growth"]) for row in landmarks],
            [("ep_child_a", 2, 0), ("ep_child_a", 8, 800), ("ep_child_b", 3, 0)],
        )
        self.assertLessEqual(len(landmarks), 4)

    def test_collector_rejects_nullable_evaluation_metadata(self):
        for field in EVALUATION_FIELDS:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                source, binary, run, _ = self.fixture(Path(directory))
                manifest = run / "campaign.json"
                value = json.loads(manifest.read_text(encoding="utf-8"))
                value[field] = None
                manifest.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, f"string `{field}`"):
                    collect(source, binary, [run], {"example"})

    def test_collector_rejects_invalid_task_concurrency(self):
        with tempfile.TemporaryDirectory() as directory:
            source, binary, run, _ = self.fixture(Path(directory))
            manifest = run / "campaign.json"
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["concurrency"] = 3
            manifest.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid `concurrency`"):
                collect(source, binary, [run], {"example"})

    def test_summary_keeps_an_independent_audit_separate_from_a_bare_episode(self):
        manifest = {
            "dataset": "terminal-bench/example@1",
            "label": "bare",
            "model": "openai-codex/gpt-5.6-sol",
            "reasoning_effort": "low",
            "service_tier": "default",
            "token_limits": "measurement_only",
            "requested_workers": 1,
            "concurrency": 1,
            "diagnosis_model": None,
            "diagnosis_reasoning_effort": None,
            "diagnosis_model_calls": None,
            "unresolved_diagnosis_reasoning_effort": None,
            "unresolved_diagnosis_model_calls": None,
            "escalation_reasoning_effort": None,
            "escalation_model_calls": None,
            "completion_checker": None,
        }
        bare = evaluation_metadata(manifest, Path("bare/campaign.json"))
        manifest.update(
            {
                "label": "independent-audit",
                "escalation_reasoning_effort": "high",
                "escalation_model_calls": 60,
            }
        )
        audit = evaluation_metadata(manifest, Path("audit/campaign.json"))
        reports = [
            {
                "task": "terminal-bench/example",
                "evaluation": bare,
                "verifier_reward": 0.0,
                "usage": {"model_calls": 3, "estimated_cost_usd": 0.1},
            },
            {
                "task": "terminal-bench/example",
                "evaluation": audit,
                "verifier_reward": 1.0,
                "usage": {"model_calls": 8, "estimated_cost_usd": 0.3},
            },
        ]
        summary = evaluation_summary(reports)
        self.assertEqual(len(summary), 2)
        bare_summary = next(
            row for row in summary if "independent_audit" not in row["execution_configuration"]
        )
        audit_summary = next(
            row for row in summary if "independent_audit" in row["execution_configuration"]
        )
        self.assertEqual(bare_summary["verified_successes"], 0)
        self.assertEqual(
            audit_summary["execution_configuration"]["independent_audit"],
            {
                "model": "openai-codex/gpt-5.6-sol",
                "reasoning_effort": "high",
                "model_calls": 60,
            },
        )

    def test_collector_rejects_held_back_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            source, binary, run, _ = self.fixture(Path(directory))
            with self.assertRaisesRegex(ValueError, "outside development evidence"):
                collect(source, binary, [run], set())


if __name__ == "__main__":
    unittest.main()
