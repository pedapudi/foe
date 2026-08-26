#!/usr/bin/python3

import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from collect_diagnostics import (
    EVALUATION_FIELDS,
    collect,
    diagnostic_outcome,
    compact_result,
    compact_terminal_timelines,
    encoded_evidence,
    evaluation_metadata,
    evaluation_summary,
    input_growth_landmarks,
    main,
    repeated_failure_contrasts,
)
from run import read_cases


class CollectDiagnosticsTest(unittest.TestCase):
    def test_result_compaction_keeps_causal_fields_without_transport_details(self):
        row = {
            "call_id": "call_not_needed_by_the_optimizer",
            "canonical_characters": 9_000,
            "episode_id": "ep_child",
            "seq": 41,
            "step": 5,
            "tool": "bash",
            "subject": "x" * 400,
            "exit_code": 1,
            "is_error": False,
            "timed_out": False,
            "truncated": True,
            "rendered_characters": 8_000,
            "replayed_characters": 32_000,
            "replayed_requests": 4,
        }
        compact = compact_result(row, replay=True)
        self.assertEqual(compact["episode_id"], "ep_child")
        self.assertEqual(compact["replayed_characters"], 32_000)
        self.assertEqual(len(compact["subject"]), 180)
        self.assertTrue(compact["truncated"])
        self.assertNotIn("call_id", compact)
        self.assertNotIn("canonical_characters", compact)
        self.assertNotIn("is_error", compact)

    def test_terminal_timeline_excludes_a_different_child_claim(self):
        terminal = {"kind": "completed", "value": {"summary": "audited"}}
        implementation = {"kind": "completed", "value": {"summary": "implemented"}}
        timelines = [
            {
                "episode_id": "ep_implementation",
                "outcome": implementation,
                "results": [{"seq": 7, "tool": "return"}],
            },
            {
                "episode_id": "ep_audit",
                "outcome": terminal,
                "results": [{"seq": 11, "tool": "bash", "exit_code": 0}],
            },
        ]
        compact = compact_terminal_timelines(timelines, terminal)
        self.assertEqual([row["episode_id"] for row in compact], ["ep_audit"])
        self.assertEqual(compact[0]["results"], [{"seq": 11, "tool": "bash", "exit_code": 0}])

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
        trial = agent.parent
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "task_name": "terminal-bench/example",
                    "task_checksum": "sha256:task",
                    "verifier_result": {"rewards": {"reward": 1.0}},
                    "exception_info": None,
                }
            ),
            encoding="utf-8",
        )
        verifier = trial / "verifier"
        verifier.mkdir()
        ctrf = json.dumps(
            {
                "results": {
                    "summary": {"tests": 1, "passed": 1, "failed": 0},
                    "tests": [
                        {
                            "name": "test_outputs.py::test_public_interface",
                            "status": "passed",
                        }
                    ],
                }
            }
        ).encode()
        (verifier / "ctrf.json").write_bytes(ctrf)
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
                        "task_checksum": "sha256:task",
                    },
                    "task": "terminal-bench/example",
                    "verifier_reward": 1.0,
                    "trial_error": None,
                    "artifact_outcome_mismatch": False,
                    "verifier_feedback": {
                        "sha256": "sha256:" + hashlib.sha256(ctrf).hexdigest(),
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
        self.assertEqual(report["schema_version"], 6)
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
        encoded = encoded_evidence(report)
        self.assertTrue(encoded.endswith("\n"))
        self.assertNotIn("\n  ", encoded)

    def test_collector_reloads_the_retained_task_owned_verifier_report(self):
        with tempfile.TemporaryDirectory() as directory:
            source, binary, run, _ = self.fixture(Path(directory))
            diagnosis_path = next(run.glob("*/*/agent/foe-diagnostics.json"))
            trial = diagnosis_path.parent.parent
            fixture = Path(__file__).with_name("testdata") / "dna_tm_delta_ctrf.json"
            retained = fixture.read_bytes()
            (trial / "verifier" / "ctrf.json").write_bytes(retained)
            diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
            diagnosis["verifier_feedback"] = {
                "sha256": "sha256:" + hashlib.sha256(retained).hexdigest(),
                "failures": [{"name": "model-supplied-check", "message": "untrusted"}]
            }
            diagnosis_path.write_text(json.dumps(diagnosis), encoding="utf-8")

            report = collect(source, binary, [run], {"example"})

        feedback = report["trajectory_diagnostics"][0]["verifier_feedback"]
        self.assertEqual(feedback["source"], "verifier/ctrf.json")
        self.assertEqual(
            feedback["failures"][0]["locus"]["assertion"],
            "abs(fwd_tm - rev_tm) <= 5",
        )
        self.assertNotIn("model-supplied-check", json.dumps(feedback))

    def test_collector_rejects_stale_or_swapped_trial_artifacts(self):
        mutations = {
            "task": lambda result, diagnosis: result.update(
                task_name="terminal-bench/different"
            ),
            "task checksum": lambda result, diagnosis: result.update(
                task_checksum="sha256:different"
            ),
            "reward": lambda result, diagnosis: result["verifier_result"][
                "rewards"
            ].update(reward=0.0),
            "error": lambda result, diagnosis: result.update(
                exception_info={"type": "VerifierError"}
            ),
            "verifier digest": lambda result, diagnosis: diagnosis[
                "verifier_feedback"
            ].update(sha256="sha256:" + "0" * 64),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                source, binary, run, _ = self.fixture(Path(directory))
                diagnosis_path = next(run.glob("*/*/agent/foe-diagnostics.json"))
                result_path = diagnosis_path.parent.parent / "result.json"
                result = json.loads(result_path.read_text(encoding="utf-8"))
                diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
                mutate(result, diagnosis)
                result_path.write_text(json.dumps(result), encoding="utf-8")
                diagnosis_path.write_text(json.dumps(diagnosis), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "does not match"):
                    collect(source, binary, [run], {"example", "different"})

    def test_collector_rejects_a_symlinked_verifier_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, run, _ = self.fixture(root)
            report = next(run.glob("*/*/verifier/ctrf.json"))
            outside = root / "outside-ctrf.json"
            outside.write_bytes(report.read_bytes())
            report.unlink()
            report.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                collect(source, binary, [run], {"example"})

    def test_repeated_failure_contrast_keeps_distinct_loci_in_one_coarse_profile(self):
        def report(episode: str, reward: float, check: str, locus: str = "same") -> dict:
            return {
                "task": "terminal-bench/example",
                "evidence_identity": {"episode_id": episode},
                "verifier_reward": reward,
                "trial_error": None,
                "outcome": {"kind": "completed"},
                "artifact_outcome_mismatch": reward == 0,
                "verifier_feedback": {
                    "sha256": "sha256:" + ("1" if locus == "same" else "2") * 64,
                    "failure_evidence_counts": {
                        "total_failed_tests": int(reward == 0),
                        "retained_failed_tests": int(reward == 0),
                        "omitted_failed_tests": 0,
                        "unlocated_failed_tests": 0,
                        "ambiguous_failed_tests": 0,
                    },
                    "failures": (
                        [
                            {
                                "name": check,
                                "failure_class": "AssertionError",
                                "locus": {
                                    "locus_sha256": "sha256:" + (
                                        "3" if locus == "same" else "4"
                                    ) * 64,
                                    "location": (
                                        "tests/test_outputs.py:116"
                                        if locus == "same"
                                        else "tests/test_outputs.py:99"
                                    ),
                                    "assertion": (
                                        "abs(fwd_tm - rev_tm) <= 5"
                                        if locus == "same"
                                        else "15 <= len(extra_r) <= 45"
                                    ),
                                    "message": locus,
                                },
                            }
                        ]
                        if reward == 0
                        else []
                    )
                },
            }

        reports = [
            report("ep_failed_one", 0.0, "test_public_interface"),
            report("ep_failed_two", 0.0, "test_public_interface", "length"),
            report("ep_different_failure", 0.0, "test_file_layout"),
            report("ep_success", 1.0, ""),
        ]
        expected = {
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
                    "failed_attempts": [
                        {
                            "episode_id": "ep_failed_one",
                            "verifier_report_sha256": "sha256:" + "1" * 64,
                            "failure_evidence_counts": {
                                "total_failed_tests": 1,
                                "retained_failed_tests": 1,
                                "omitted_failed_tests": 0,
                                "unlocated_failed_tests": 0,
                                "ambiguous_failed_tests": 0,
                            },
                            "failure_loci": [
                                {
                                    "name": "test_public_interface",
                                    "failure_class": "AssertionError",
                                    "locus_sha256": "sha256:" + "3" * 64,
                                    "location": "tests/test_outputs.py:116",
                                    "assertion": "abs(fwd_tm - rev_tm) <= 5",
                                    "message": "same",
                                }
                            ],
                        },
                        {
                            "episode_id": "ep_failed_two",
                            "verifier_report_sha256": "sha256:" + "2" * 64,
                            "failure_evidence_counts": {
                                "total_failed_tests": 1,
                                "retained_failed_tests": 1,
                                "omitted_failed_tests": 0,
                                "unlocated_failed_tests": 0,
                                "ambiguous_failed_tests": 0,
                            },
                            "failure_loci": [
                                {
                                    "name": "test_public_interface",
                                    "failure_class": "AssertionError",
                                    "locus_sha256": "sha256:" + "4" * 64,
                                    "location": "tests/test_outputs.py:99",
                                    "assertion": "15 <= len(extra_r) <= 45",
                                    "message": "length",
                                }
                            ],
                        },
                    ],
                    "successful_episode_ids": ["ep_success"],
        }
        expected["contrast_sha256"] = "sha256:" + hashlib.sha256(
            json.dumps(
                expected,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        self.assertEqual(repeated_failure_contrasts(reports), [expected])

        reports = [
            report("ep_failed_one", 0.0, "test_public_interface"),
            {
                **report("ep_infrastructure_error", 0.0, "test_public_interface"),
                "trial_error": {"type": "DockerError"},
            },
            report("ep_success", 1.0, ""),
        ]
        self.assertEqual(repeated_failure_contrasts(reports), [])

        reports = [
            report("ep_failed_one", 0.0, "test_public_interface"),
            report("ep_failed_two", 0.0, "test_public_interface"),
            report("ep_success", 1.0, ""),
        ]
        for failed in reports[:2]:
            failed["verifier_feedback"]["failures"][0]["failure_class"] = None
            failed["verifier_feedback"]["failures"][0]["raw_status"] = "call_failed"
            failed["verifier_feedback"]["failures"][0]["locus"] = None
        self.assertEqual(repeated_failure_contrasts(reports), [])

    def test_missing_verifier_output_cannot_enter_a_failure_contrast(self):
        def failed(episode: str) -> dict:
            return {
                "task": "terminal-bench/example",
                "evidence_identity": {"episode_id": episode},
                "verifier_reward": 0.0,
                "trial_error": None,
                "outcome": {"kind": "completed"},
                "artifact_outcome_mismatch": True,
                "verifier_feedback": None,
            }

        reports = [
            failed("ep_failed_one"),
            failed("ep_failed_two"),
            {
                **failed("ep_success"),
                "verifier_reward": 1.0,
                "artifact_outcome_mismatch": False,
            },
        ]
        self.assertEqual(repeated_failure_contrasts(reports), [])

    def test_partial_failure_locus_sets_cannot_enter_a_failure_contrast(self):
        def failed(episode: str, incomplete_field: str) -> dict:
            counts = {
                "total_failed_tests": 2,
                "retained_failed_tests": 2,
                "omitted_failed_tests": 0,
                "unlocated_failed_tests": 0,
                "ambiguous_failed_tests": 0,
            }
            counts[incomplete_field] = 1
            if incomplete_field == "omitted_failed_tests":
                counts["retained_failed_tests"] = 1
            return {
                "task": "terminal-bench/example",
                "evidence_identity": {"episode_id": episode},
                "verifier_reward": 0.0,
                "trial_error": None,
                "outcome": {"kind": "completed"},
                "artifact_outcome_mismatch": True,
                "verifier_feedback": {
                    "sha256": "sha256:" + "1" * 64,
                    "failure_evidence_counts": counts,
                    "failures": [
                        {
                            "name": "test_public_interface",
                            "failure_class": "AssertionError",
                            "locus": {
                                "locus_sha256": "sha256:" + "2" * 64,
                                "assertion": "result is valid",
                            },
                        }
                    ],
                },
            }

        success = {
            **failed("ep_success", "unlocated_failed_tests"),
            "verifier_reward": 1.0,
            "artifact_outcome_mismatch": False,
        }
        for field in (
            "omitted_failed_tests",
            "unlocated_failed_tests",
            "ambiguous_failed_tests",
        ):
            with self.subTest(field=field):
                reports = [
                    failed("ep_failed_one", field),
                    failed("ep_failed_two", field),
                    success,
                ]
                self.assertEqual(repeated_failure_contrasts(reports), [])

    def test_one_episode_identity_cannot_name_different_verifier_evidence(self):
        def report(digest: str) -> dict:
            return {
                "task": "terminal-bench/example",
                "evidence_identity": {"episode_id": "ep_reused"},
                "verifier_reward": 0.0,
                "trial_error": None,
                "outcome": {"kind": "completed"},
                "artifact_outcome_mismatch": True,
                "verifier_feedback": {
                    "sha256": "sha256:" + digest * 64,
                    "failure_evidence_counts": {
                        "total_failed_tests": 1,
                        "retained_failed_tests": 1,
                        "omitted_failed_tests": 0,
                        "unlocated_failed_tests": 0,
                        "ambiguous_failed_tests": 0,
                    },
                    "failures": [
                        {
                            "name": "test_public_interface",
                            "failure_class": "AssertionError",
                            "locus": {
                                "locus_sha256": "sha256:" + digest * 64,
                                "assertion": "result is valid",
                            },
                        }
                    ],
                },
            }

        with self.assertRaisesRegex(ValueError, "inconsistent verifier failure evidence"):
            repeated_failure_contrasts([report("1"), report("2")])

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
        claim = compact["outcome"]["untrusted_completion_claim"]
        self.assertEqual(claim["validation"], ["format is valid"])
        self.assertEqual(claim["unresolved_risks"], ["behavior was not exercised"])
        self.assertEqual(compact["episodes"][0]["outcome"], {"kind": "completed"})

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

    def test_collector_requires_workflow_ownership_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            source, binary, run, _ = self.fixture(Path(directory))
            manifest = run / "campaign.json"
            value = json.loads(manifest.read_text(encoding="utf-8"))
            del value["built_in_workflow"]
            manifest.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "boolean `built_in_workflow`"):
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
            "built_in_workflow": False,
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

    def test_summary_keeps_a_built_in_workflow_separate_from_a_bare_episode(self):
        manifest = {
            "dataset": "terminal-bench/example@1",
            "label": "bare",
            "model": "openai-codex/gpt-5.6-sol",
            "reasoning_effort": "low",
            "service_tier": "default",
            "token_limits": "measurement_only",
            "built_in_workflow": False,
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
            with self.assertRaisesRegex(ValueError, "outside self-improvement evidence"):
                collect(source, binary, [run], set())

    def test_command_accepts_confirmation_and_rejects_held_back_tasks(self):
        cases = Path(__file__).with_name("cases.json")
        for task, expected_status in (
            ("build-pov-ray", 0),
            ("chess-best-move", 2),
            ("protein-assembly", 2),
        ):
            with self.subTest(task=task), tempfile.TemporaryDirectory() as directory:
                source, binary, run, _ = self.fixture(Path(directory))
                diagnosis_path = next(run.glob("*/*/agent/foe-diagnostics.json"))
                diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
                diagnosis["task"] = f"terminal-bench/{task}"
                diagnosis_path.write_text(json.dumps(diagnosis), encoding="utf-8")
                result_path = diagnosis_path.parent.parent / "result.json"
                result = json.loads(result_path.read_text(encoding="utf-8"))
                result["task_name"] = f"terminal-bench/{task}"
                result_path.write_text(json.dumps(result), encoding="utf-8")
                output = Path(directory) / "evidence.json"
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                    io.StringIO()
                ):
                    status = main(
                        [
                            "--source-root",
                            str(source),
                            "--foe",
                            str(binary),
                            "--run-dir",
                            str(run),
                            "--cases",
                            str(cases),
                            "--output",
                            str(output),
                        ]
                    )
                self.assertEqual(status, expected_status)
                self.assertEqual(output.exists(), expected_status == 0)

    def test_registry_excludes_protected_calibration_and_holdout_tasks(self):
        _, groups, _, _ = read_cases(Path(__file__).with_name("cases.json"))
        eligible = set(groups["self_improvement_evidence"])
        self.assertTrue(eligible.isdisjoint(groups["calibration"]))
        self.assertTrue(eligible.isdisjoint(groups["calibration_holdout"]))


if __name__ == "__main__":
    unittest.main()
