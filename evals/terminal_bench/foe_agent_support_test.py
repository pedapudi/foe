#!/usr/bin/python3

import json
import tempfile
import unittest
from pathlib import Path

from foe_agent_support import build_program, read_episode_summary


class ProgramTest(unittest.TestCase):
    def test_program_declares_container_authority_and_split_allowances(self):
        program = build_program(
            "repair it",
            "openai-codex/gpt-5.6-sol",
            "/tmp/private.json",
            model_calls=12,
            input_tokens=120_000,
            output_tokens=20_000,
            seconds=600,
            reasoning_effort="low",
        )
        self.assertEqual(program["grants"], {"read": ["/"], "write": ["/"]})
        self.assertEqual(
            program["budget"],
            {
                "model_calls": 12,
                "input_tokens": 120_000,
                "output_tokens": 20_000,
                "seconds": 600,
            },
        )
        self.assertEqual(program["sandbox"], {"mode": "off"})
        self.assertEqual(program["model"]["reasoning_effort"], "low")
        self.assertEqual(program["model"]["token_file"], "/tmp/private.json")
        self.assertNotIn("api_key_file", program["model"])
        self.assertEqual(program["task"], "repair it")

    def test_program_rejects_unqualified_model(self):
        with self.assertRaisesRegex(ValueError, "provider/model"):
            build_program(
                "repair it",
                "gpt-5.6-sol",
                "/tmp/private.json",
                model_calls=1,
                input_tokens=1,
                output_tokens=1,
                seconds=1,
                reasoning_effort="low",
            )


class EpisodeSummaryTest(unittest.TestCase):
    def test_summary_requires_a_root_episode_log(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(FileNotFoundError, "episode log does not exist"):
                read_episode_summary(Path(directory))

    def test_summary_includes_child_usage_and_root_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "children" / "ep_child"
            child.mkdir(parents=True)
            (root / "episode.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"type": "model/request", "data": {}}),
                        json.dumps(
                            {
                                "type": "assistant/message",
                                "data": {"usage": {"input": 10, "output": 2, "cache_read": 4}},
                            }
                        ),
                        json.dumps({"type": "episode/end", "data": {"outcome": {"kind": "accepted"}}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (child / "episode.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"type": "model/request", "data": {}}),
                        json.dumps({"type": "tool/result", "data": {}}),
                        json.dumps(
                            {
                                "type": "assistant/message",
                                "data": {"usage": {"input": 7, "output": 3, "cache_read": 1}},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            summary = read_episode_summary(root)
        self.assertEqual(summary["model_calls"], 2)
        self.assertEqual(summary["tool_calls"], 1)
        self.assertEqual(summary["input_tokens"], 17)
        self.assertEqual(summary["output_tokens"], 5)
        self.assertEqual(summary["cache_read_tokens"], 5)
        self.assertEqual(summary["outcome"], {"kind": "accepted"})


if __name__ == "__main__":
    unittest.main()
