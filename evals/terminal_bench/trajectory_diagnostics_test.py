#!/usr/bin/python3

import json
import tempfile
import unittest
from pathlib import Path

from trajectory_diagnostics import diagnose_episode, failure_locus, verifier_feedback


class TrajectoryDiagnosticsTest(unittest.TestCase):
    def ctrf_feedback(self, fixture: str) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trial = root / "result.json"
            trial.write_text("{}\n", encoding="utf-8")
            verifier = root / "verifier"
            verifier.mkdir()
            source = Path(__file__).with_name("testdata") / fixture
            (verifier / "ctrf.json").write_bytes(source.read_bytes())
            feedback = verifier_feedback(trial)
        self.assertIsNotNone(feedback)
        return feedback

    def test_dna_failures_share_a_coarse_check_and_keep_distinct_loci(self):
        temperature = self.ctrf_feedback("dna_tm_delta_ctrf.json")
        annealing = self.ctrf_feedback("dna_effective_annealing_length_ctrf.json")
        temperature_failure = temperature["failures"][0]
        annealing_failure = annealing["failures"][0]

        self.assertEqual(temperature_failure["name"], annealing_failure["name"])
        self.assertEqual(
            temperature_failure["failure_class"], annealing_failure["failure_class"]
        )
        self.assertEqual(
            temperature_failure["locus"],
            {
                "locus_sha256": temperature_failure["locus"]["locus_sha256"],
                "location": "tests/test_outputs.py:116",
                "assertion": "abs(fwd_tm - rev_tm) <= 5",
                "message": (
                    "Tm of forward and reverse primers must be within 5 degrees C "
                    "of each other."
                ),
            },
        )
        self.assertEqual(
            annealing_failure["locus"],
            {
                "locus_sha256": annealing_failure["locus"]["locus_sha256"],
                "location": "tests/test_outputs.py:99",
                "assertion": "15 <= len(extra_r) <= 45",
                "message": (
                    "Reverse primer annealing (incl. overhang match) must be "
                    "15–45 nt."
                ),
            },
        )
        self.assertNotEqual(
            temperature_failure["locus"]["locus_sha256"],
            annealing_failure["locus"]["locus_sha256"],
        )
        self.assertNotEqual(temperature["sha256"], annealing["sha256"])
        complete = {
            "total_failed_tests": 1,
            "retained_failed_tests": 1,
            "omitted_failed_tests": 0,
            "unlocated_failed_tests": 0,
            "ambiguous_failed_tests": 0,
        }
        self.assertEqual(temperature["failure_evidence_counts"], complete)
        self.assertEqual(annealing["failure_evidence_counts"], complete)

    def test_failure_locus_removes_volatile_paths_and_addresses(self):
        locus = failure_locus(
            {
                "name": "tests/test_worker.py::test_result",
                "message": "fallback",
                "trace": (
                    "> assert result == 0x7ff01234\n"
                    "E AssertionError: /tmp/pytest-391/result at 0x7ff01234 failed\n"
                    "/home/runner/build/tests/test_worker.py:47: AssertionError\n"
                ),
            },
            "AssertionError",
        )
        self.assertIsNotNone(locus)
        encoded = json.dumps(locus)
        self.assertEqual(locus["location"], "tests/test_worker.py:47")
        self.assertEqual(locus["assertion"], "result == <address>")
        self.assertNotIn("pytest-391", encoded)
        self.assertNotIn("7ff01234", encoded)

    def test_failure_locus_stabilizes_host_state_and_parameterized_names(self):
        first = failure_locus(
            {
                "name": "tests/test_worker.py::test_result[2026-08-26T10:22:31Z]",
                "trace": (
                    "> assert result == expected\n"
                    "E AssertionError: \x1b]0;first-title\x07failed at "
                    "/workspace/job-17 on 2026-08-26T10:22:31Z\n"
                    "/root/source/tests/test_worker.py:47: AssertionError\n"
                ),
            },
            "AssertionError",
        )
        second = failure_locus(
            {
                "name": "tests/test_worker.py::test_result[2026-08-26T11:23:32Z]",
                "trace": (
                    "> assert result == expected\n"
                    "E AssertionError: \x1b]0;second-title\x07failed at "
                    "/workspace/job-18 on 2026-08-26T11:23:32Z\n"
                    "/root/source/tests/test_worker.py:47: AssertionError\n"
                ),
            },
            "AssertionError",
        )
        self.assertEqual(first, second)
        encoded = json.dumps(first)
        self.assertNotIn("workspace", encoded)
        self.assertNotIn("root/source", encoded)
        self.assertNotIn("2026-08-26", encoded)
        self.assertNotIn("title", encoded)

    def test_failure_locus_removes_absolute_non_test_roots_and_malformed_osc(self):
        first = failure_locus(
            {
                "name": "check_result[first]",
                "trace": (
                    "> assert result == 0XABCD\n"
                    "E AssertionError: /workspace at 2026-08-26 10:22:31 "
                    "\x1b]unterminated title\n"
                    "/workspace/job-17/check.py:47: AssertionError\n"
                ),
            },
            "AssertionError",
        )
        second = failure_locus(
            {
                "name": "check_result[second]",
                "trace": (
                    "> assert result == 0X1234\n"
                    "E AssertionError: /root at 2026-08-26 11:23:32 "
                    "\x1b]another unterminated title\n"
                    "/root/job-18/check.py:47: AssertionError\n"
                ),
            },
            "AssertionError",
        )
        self.assertEqual(first, second)
        self.assertEqual(first["location"], "check.py:47")
        encoded = json.dumps(first)
        self.assertNotIn("workspace", encoded)
        self.assertNotIn("root", encoded)
        self.assertNotIn("unterminated", encoded)
        self.assertNotIn("0X", encoded)

    def test_verifier_feedback_marks_ambiguous_and_omitted_loci(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trial = root / "result.json"
            trial.write_text("{}\n", encoding="utf-8")
            verifier = root / "verifier"
            verifier.mkdir()
            tests = [
                {
                    "name": "test_monolithic",
                    "status": "failed",
                    "raw_status": "call_failed",
                    "trace": (
                        "> assert first_condition\n"
                        "/tests/test_outputs.py:10: AssertionError\n"
                        "> assert unrelated_condition\n"
                        "/tests/test_outputs.py:20: AssertionError\n"
                    ),
                },
                *[
                    {
                        "name": f"test_{index}",
                        "status": "failed",
                        "raw_status": "call_failed",
                        "trace": (
                            f"> assert value == {index}\n"
                            f"/tests/test_outputs.py:{30 + index}: AssertionError\n"
                        ),
                    }
                    for index in range(5)
                ],
            ]
            (verifier / "ctrf.json").write_text(
                json.dumps({"results": {"tests": tests}}), encoding="utf-8"
            )
            feedback = verifier_feedback(trial)
        self.assertEqual(
            feedback["failure_evidence_counts"],
            {
                "total_failed_tests": 6,
                "retained_failed_tests": 4,
                "omitted_failed_tests": 2,
                "unlocated_failed_tests": 0,
                "ambiguous_failed_tests": 1,
            },
        )
        self.assertIsNone(feedback["failures"][0]["locus"])
        self.assertTrue(feedback["failures"][0]["locus_ambiguous"])

    def test_verifier_feedback_bounds_status_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trial = root / "result.json"
            trial.write_text("{}\n", encoding="utf-8")
            verifier = root / "verifier"
            verifier.mkdir()
            huge = "grader-text-" * 5_000
            (verifier / "ctrf.json").write_text(
                json.dumps(
                    {
                        "results": {
                            "tests": [
                                {
                                    "name": "test_result[param-secret]",
                                    "status": huge,
                                    "raw_status": huge,
                                    "trace": "> assert result\n/tests/test.py:1: AssertionError",
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            feedback = verifier_feedback(trial)
        failure = feedback["failures"][0]
        self.assertEqual(failure["name"], "test_result[<parameter>]")
        self.assertEqual(len(failure["status"]), 64)
        self.assertEqual(len(failure["raw_status"]), 64)

    def test_verifier_summary_cannot_hide_omitted_failed_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trial = root / "result.json"
            trial.write_text("{}\n", encoding="utf-8")
            verifier = root / "verifier"
            verifier.mkdir()
            (verifier / "ctrf.json").write_text(
                json.dumps(
                    {
                        "results": {
                            "summary": {"failed": 3},
                            "tests": [
                                {
                                    "name": "test_result",
                                    "status": "failed",
                                    "trace": (
                                        "> assert result\n"
                                        "/tests/test_outputs.py:1: AssertionError"
                                    ),
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            feedback = verifier_feedback(trial)
        self.assertEqual(
            feedback["failure_evidence_counts"],
            {
                "total_failed_tests": 3,
                "retained_failed_tests": 1,
                "omitted_failed_tests": 2,
                "unlocated_failed_tests": 0,
                "ambiguous_failed_tests": 0,
            },
        )

    def test_verifier_feedback_distinguishes_missing_and_malformed_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trial = root / "result.json"
            trial.write_text("{}\n", encoding="utf-8")
            self.assertIsNone(verifier_feedback(trial))

            verifier = root / "verifier"
            verifier.mkdir()
            (verifier / "ctrf.json").write_text("{malformed\n", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                verifier_feedback(trial)
            (verifier / "ctrf.json").write_bytes(b"\xff\xfe{}")
            with self.assertRaises(json.JSONDecodeError):
                verifier_feedback(trial)

    def test_verifier_feedback_rejects_escaped_and_symlinked_results(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "retained"
            root.mkdir()
            outside = parent / "result.json"
            outside.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside its retained trial"):
                verifier_feedback(outside, artifact_root=root)

            linked = root / "result.json"
            linked.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                verifier_feedback(linked, artifact_root=root)

    def test_a_generic_ctrf_message_does_not_claim_an_exact_locus(self):
        self.assertIsNone(
            failure_locus(
                {
                    "name": "tests/test_worker.py::test_result",
                    "message": "The test failed in the call phase",
                },
                None,
            )
        )

    def test_diagnosis_measures_exact_replay_and_completed_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = [
                {
                    "seq": 0,
                    "type": "episode/start",
                    "data": {
                        "id": "ep_1",
                        "identity": "sha256:program",
                        "runtime": {"build": "sha256:runtime"},
                    },
                },
                {
                    "seq": 1,
                    "type": "model/request",
                    "data": {"step": 1, "messages": []},
                },
                {
                    "seq": 2,
                    "type": "assistant/message",
                    "data": {
                        "step": 1,
                        "usage": {"input": 100, "output": 10, "cache_read": 20},
                        "tool_calls": [
                            {"id": "call_1", "name": "bash", "args": {"command": "false"}}
                        ],
                    },
                },
                {
                    "seq": 3,
                    "type": "tool/result",
                    "data": {
                        "step": 1,
                        "call_id": "call_1",
                        "name": "bash",
                        "subject": "false · exit 1",
                        "rendered": "[exit 1]\nfailed\n",
                        "value": {"exit_code": 1, "timed_out": False, "truncated": False},
                        "is_error": False,
                    },
                },
                {
                    "seq": 4,
                    "type": "model/request",
                    "data": {
                        "step": 2,
                        "messages": [
                            {"role": "tool", "call_id": "call_1", "rendered": "[exit 1]\nfailed\n"}
                        ],
                    },
                },
                {
                    "seq": 5,
                    "type": "assistant/message",
                    "data": {
                        "step": 2,
                        "usage": {"input": 150, "output": 5, "cache_read": 100},
                        "tool_calls": [],
                    },
                },
                {
                    "seq": 6,
                    "type": "episode/end",
                    "data": {"outcome": {"kind": "completed", "value": "done"}},
                },
            ]
            (root / "episode.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            child = root / "children" / "ep_2"
            child.mkdir(parents=True)
            child_events = [
                {
                    "seq": 0,
                    "type": "episode/start",
                    "data": {
                        "id": "ep_2",
                        "parent_id": "ep_1",
                        "program": {
                            "name": "implementation",
                            "model": {"provider": "openai-codex", "model": "gpt-5.6-sol"},
                        },
                    },
                },
                {"seq": 1, "type": "model/request", "data": {"step": 1, "messages": []}},
                {
                    "seq": 2,
                    "type": "assistant/message",
                    "data": {
                        "step": 1,
                        "usage": {"input": 75, "output": 3, "cache_read": 25},
                        "tool_calls": [],
                    },
                },
                {"seq": 3, "type": "episode/end", "data": {"outcome": {"kind": "completed"}}},
            ]
            (child / "episode.jsonl").write_text(
                "\n".join(json.dumps(event) for event in child_events) + "\n",
                encoding="utf-8",
            )
            trial = root / "result.json"
            trial.write_text(
                json.dumps(
                    {
                        "task_name": "terminal-bench/example",
                        "task_checksum": "task-sha",
                        "verifier_result": {"rewards": {"reward": 1.0}},
                        "exception_info": None,
                    }
                ),
                encoding="utf-8",
            )
            report = diagnose_episode(root, trial_result=trial)

        self.assertEqual(report["evidence_identity"]["runtime_build"], "sha256:runtime")
        self.assertEqual(report["schema_version"], 5)
        self.assertEqual(report["usage"]["model_calls"], 3)
        self.assertEqual(report["usage"]["input_tokens"], 325)
        self.assertEqual(report["usage"]["cache_read_tokens"], 145)
        self.assertEqual(report["episodes"][1]["episode_id"], "ep_2")
        self.assertEqual(report["episodes"][1]["model"], "openai-codex/gpt-5.6-sol")
        self.assertEqual(report["usage"]["per_request"][-1]["episode_id"], "ep_2")
        self.assertEqual(report["largest_replayed_results"][0]["replayed_requests"], 1)
        self.assertEqual(report["largest_replayed_results"][0]["replayed_characters"], 16)
        self.assertEqual(report["tool_failures"][0]["exit_code"], 1)
        self.assertFalse(report["artifact_outcome_mismatch"])

    def test_diagnosis_retains_successful_validation_and_bounded_verifier_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = [
                {
                    "seq": 0,
                    "type": "episode/start",
                    "data": {
                        "id": "ep_failed",
                        "identity": "sha256:program",
                        "runtime": {"build": "sha256:runtime"},
                    },
                },
                {
                    "seq": 10,
                    "type": "tool/result",
                    "data": {
                        "step": 2,
                        "call_id": "edit_1",
                        "name": "edit",
                        "subject": "main.c: 1 edit",
                        "rendered": "changed",
                        "value": {},
                        "is_error": False,
                    },
                },
                {
                    "seq": 20,
                    "type": "tool/result",
                    "data": {
                        "step": 3,
                        "call_id": "bash_1",
                        "name": "bash",
                        "subject": "compile candidate · exit 0",
                        "rendered": "",
                        "value": {"exit_code": 0},
                        "is_error": False,
                    },
                },
                {
                    "seq": 30,
                    "type": "tool/result",
                    "data": {
                        "step": 4,
                        "call_id": "bash_2",
                        "name": "bash",
                        "subject": "run sample · exit 0",
                        "rendered": "sample output",
                        "value": {"exit_code": 0},
                        "is_error": False,
                    },
                },
                {
                    "seq": 40,
                    "type": "episode/end",
                    "data": {"outcome": {"kind": "completed", "value": "done"}},
                },
            ]
            (root / "episode.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            trial = root / "result.json"
            trial.write_text(
                json.dumps(
                    {
                        "task_name": "terminal-bench/example",
                        "task_checksum": "task-sha",
                        "verifier_result": {"rewards": {"reward": 0.0}},
                        "exception_info": None,
                    }
                ),
                encoding="utf-8",
            )
            verifier = root / "verifier"
            verifier.mkdir()
            (verifier / "ctrf.json").write_text(
                json.dumps(
                    {
                        "results": {
                            "summary": {"tests": 1, "passed": 0, "failed": 1},
                            "tests": [
                                {
                                    "name": "test_outputs.py::test_public_interface",
                                    "status": "failed",
                                    "raw_status": "call_failed",
                                    "message": "The semantic assertion failed",
                                    "trace": (
                                        "hidden setup value SECRET-VALUE\n"
                                        "> assert public_result == expected\n"
                                        "E AssertionError: public result differs\n"
                                        "/tests/test_outputs.py:41: AssertionError"
                                    ),
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            report = diagnose_episode(root, trial_result=trial)

        timeline = report["verification_timeline"][0]
        self.assertEqual(timeline["last_edit_seq"], 10)
        self.assertEqual(
            [(row["tool"], row["exit_code"]) for row in timeline["results"]],
            [("edit", None), ("bash", 0), ("bash", 0)],
        )
        feedback = report["verifier_feedback"]
        self.assertEqual(feedback["failure_classes"], ["AssertionError"])
        self.assertEqual(feedback["summary"]["failed"], 1)
        self.assertEqual(
            feedback["failures"][0]["name"],
            "test_outputs.py::test_public_interface",
        )
        self.assertEqual(
            feedback["failures"][0]["locus"]["assertion"],
            "public_result == expected",
        )
        self.assertNotIn("SECRET-VALUE", json.dumps(feedback))


if __name__ == "__main__":
    unittest.main()
