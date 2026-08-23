#!/usr/bin/python3

import json
import tempfile
import unittest
from pathlib import Path

from trajectory_diagnostics import diagnose_episode


class TrajectoryDiagnosticsTest(unittest.TestCase):
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
        self.assertEqual(report["schema_version"], 2)
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


if __name__ == "__main__":
    unittest.main()
