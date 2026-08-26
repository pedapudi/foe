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
    collect_from_corpus,
    development_tasks,
    diagnostic_outcome,
    encoded_evidence,
    evaluation_metadata,
    evaluation_summary,
    input_growth_landmarks,
    main as collect_main,
    repeated_failure_contrasts,
)
from trajectory_corpus import snapshot_corpus


class CollectDiagnosticsTest(unittest.TestCase):
    def test_development_tasks_excludes_protected_evaluation_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = Path(directory) / "cases.json"
            cases.write_text(
                json.dumps(
                    {
                        "groups": {
                            "development": ["develop"],
                            "capability_search": ["probe"],
                            "confirmation": ["confirm"],
                            "calibration_holdout": ["sealed"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(development_tasks(cases), {"develop", "probe"})

    def test_diagnostic_outcome_keeps_completion_claim_only_when_requested(self):
        completion = {
            "kind": "completed",
            "value": {
                "summary": "self-certified",
                "changed_paths": ["artifact"],
                "validation": ["checked a proxy"],
                "unresolved_risks": ["public interface untested"],
            },
        }
        self.assertEqual(
            diagnostic_outcome(completion),
            {"kind": "completed"},
        )
        self.assertEqual(
            diagnostic_outcome(completion, True),
            {
                "kind": "completed",
                "untrusted_completion_claim": {
                    "summary": "self-certified",
                    "changed_paths": ["artifact"],
                    "validation": ["checked a proxy"],
                    "unresolved_risks": ["public interface untested"],
                },
            },
        )
        self.assertEqual(
            diagnostic_outcome({"kind": "blocked", "code": "stuck", "message": "details"}),
            {"kind": "blocked", "code": "stuck", "message": "details"},
        )

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
                    "built_in_workflow": False,
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
        self.assertEqual(report["schema_version"], 4)
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
                        "built_in_workflow": False,
                        "service_tier": "default",
                        "token_policy": "measurement_only",
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

    def test_corpus_collection_matches_direct_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, run, identity = self.fixture(root)
            campaign_path = run / "campaign.json"
            campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
            campaign["tasks"] = [{"name": "example"}]
            campaign_path.write_text(json.dumps(campaign), encoding="utf-8")
            trial = run / "task" / "trial"
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "task_name": "terminal-bench/example",
                        "agent_result": {
                            "metadata": {"foe_credential_exposed": False}
                        }
                    }
                ),
                encoding="utf-8",
            )
            episode = trial / "agent" / "foe-episode"
            episode.mkdir()
            (episode / "episode.jsonl").write_text(
                json.dumps(
                    {
                        "seq": 1,
                        "type": "episode/start",
                        "data": {"runtime": {"build": identity["runtime_binary"]}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            cases = root / "cases.json"
            cases.write_text(
                json.dumps(
                    {
                        "dataset": "terminal-bench/example@1",
                        "groups": {
                            "development": ["example"],
                            "capability_search": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            manifest = snapshot_corpus(source, binary, [run], cases, root / "corpus")
            direct = collect(source, binary, [run], {"example"})
            from_corpus = collect_from_corpus(manifest, cases, identity)
            rejected = collect_main(
                [
                    "--corpus",
                    str(manifest),
                    "--cases",
                    str(cases),
                    "--expected-source-tree",
                    identity["source_tree"],
                    "--expected-runtime-binary",
                    identity["runtime_binary"],
                    "--expected-report-sha256",
                    "sha256:" + "0" * 64,
                ]
            )
        self.assertEqual(from_corpus, direct)
        self.assertEqual(rejected, 2)
        encoded = encoded_evidence(direct)
        self.assertTrue(encoded.endswith("\n"))
        self.assertNotIn("\n  ", encoded)

    def test_repeated_failure_contrast_requires_two_matching_failures_and_a_success(self):
        def report(episode: str, reward: float, check: str) -> dict:
            return {
                "task": "terminal-bench/example",
                "evidence_identity": {"episode_id": episode},
                "verifier_reward": reward,
                "trial_error": None,
                "outcome": {"kind": "completed"},
                "artifact_outcome_mismatch": reward == 0,
                "verifier_feedback": {
                    "failures": (
                        [{"name": check, "failure_class": "AssertionError"}]
                        if reward == 0
                        else []
                    )
                },
            }

        reports = [
            report("ep_failed_one", 0.0, "test_public_interface"),
            report("ep_failed_two", 0.0, "test_public_interface"),
            report("ep_different_failure", 0.0, "test_file_layout"),
            report("ep_success", 1.0, ""),
        ]
        self.assertEqual(
            repeated_failure_contrasts(reports),
            [
                {
                    "task": "terminal-bench/example",
                    "failure_profile": {
                        "outcome": {"kind": "completed"},
                        "artifact_outcome_mismatch": True,
                        "failed_verifier_checks": [
                            {
                                "name": "test_public_interface",
                                "failure_class": "AssertionError",
                            }
                        ],
                    },
                    "failed_episode_ids": ["ep_failed_one", "ep_failed_two"],
                    "successful_episode_ids": ["ep_success"],
                }
            ],
        )

        reports = [
            report("ep_failed_one", 0.0, "test_public_interface"),
            {
                **report("ep_infrastructure_error", 0.0, "test_public_interface"),
                "trial_error": {"type": "DockerError"},
            },
            report("ep_success", 1.0, ""),
        ]
        self.assertEqual(repeated_failure_contrasts(reports), [])

    def test_collector_rejects_a_different_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            source, binary, run, _ = self.fixture(Path(directory))
            path = next(run.glob("*/*/agent/foe-diagnostics.json"))
            report = json.loads(path.read_text(encoding="utf-8"))
            report["evidence_identity"]["runtime_build"] = "sha256:" + "0" * 64
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "different runtime identity"):
                collect(source, binary, [run], {"example"})

    def test_collector_labels_failed_completion_evidence_as_untrusted(self):
        with tempfile.TemporaryDirectory() as directory:
            source, binary, run, _ = self.fixture(Path(directory))
            path = next(run.glob("*/*/agent/foe-diagnostics.json"))
            diagnosis = json.loads(path.read_text(encoding="utf-8"))
            diagnosis["artifact_outcome_mismatch"] = True
            diagnosis["outcome"] = {
                "kind": "completed",
                "value": {
                    "summary": "the task is complete",
                    "changed_paths": ["answer.txt"],
                    "validation": ["format is valid"],
                    "unresolved_risks": ["behavior was not exercised"],
                },
            }
            diagnosis["episodes"] = [
                {
                    "episode_id": "ep_child",
                    "model_calls": 3,
                    "outcome": diagnosis["outcome"],
                }
            ]
            path.write_text(json.dumps(diagnosis), encoding="utf-8")
            report = collect(source, binary, [run], {"example"})
        compact = report["trajectory_diagnostics"][0]
        self.assertEqual(compact["outcome"], {"kind": "completed"})
        claim = compact["episodes"][0]["outcome"][
            "untrusted_completion_claim"
        ]
        self.assertEqual(claim["validation"], ["format is valid"])
        self.assertEqual(claim["unresolved_risks"], ["behavior was not exercised"])

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

    def test_summary_keeps_an_independent_audit_separate_from_a_bare_episode(self):
        manifest = {
            "dataset": "terminal-bench/example@1",
            "label": "bare",
            "model": "openai-codex/gpt-5.6-sol",
            "reasoning_effort": "low",
            "service_tier": "default",
            "token_limits": "measurement_only",
            "built_in_workflow": False,
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

    def test_summary_keeps_a_built_in_workflow_separate_from_a_bare_episode(self):
        manifest = {
            "dataset": "terminal-bench/example@1",
            "label": "bare",
            "model": "openai-codex/gpt-5.6-sol",
            "reasoning_effort": "low",
            "service_tier": "default",
            "token_limits": "measurement_only",
            "built_in_workflow": False,
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
        manifest.update({"label": "built-in", "built_in_workflow": True})
        built_in = evaluation_metadata(manifest, Path("built-in/campaign.json"))
        reports = [
            {
                "task": "terminal-bench/example",
                "evaluation": bare,
                "verifier_reward": 0.0,
                "usage": {"model_calls": 3, "estimated_cost_usd": 0.1},
            },
            {
                "task": "terminal-bench/example",
                "evaluation": built_in,
                "verifier_reward": 1.0,
                "usage": {"model_calls": 8, "estimated_cost_usd": 0.3},
            },
        ]
        summary = evaluation_summary(reports)
        self.assertEqual(len(summary), 2)
        self.assertEqual(
            sorted(row["execution_configuration"]["built_in_workflow"] for row in summary),
            [False, True],
        )

    def test_collector_rejects_held_back_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            source, binary, run, _ = self.fixture(Path(directory))
            with self.assertRaisesRegex(ValueError, "outside development evidence"):
                collect(source, binary, [run], set())


if __name__ == "__main__":
    unittest.main()
