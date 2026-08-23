#!/usr/bin/python3

import json
import tempfile
import unittest
from pathlib import Path

from foe_agent_support import build_program, estimate_usage_cost, read_episode_summary


class ProgramTest(unittest.TestCase):
    def test_program_declares_container_authority_and_split_allowances(self):
        program = build_program(
            "repair it",
            "openai-codex/gpt-5.6-sol",
            "/tmp/private.json",
            "/workspace",
            model_calls=12,
            input_tokens=120_000,
            output_tokens=20_000,
            seconds=600,
            reasoning_effort="low",
        )
        self.assertEqual(
            program["grants"],
            {"read": ["/workspace", "/"], "write": ["/"]},
        )
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

    def test_program_omits_soft_token_measurements_from_the_allowance(self):
        program = build_program(
            "repair it",
            "openai-codex/gpt-5.6-luna",
            "/tmp/private.json",
            "/workspace",
            model_calls=20,
            input_tokens=None,
            output_tokens=None,
            seconds=600,
            reasoning_effort="low",
        )
        self.assertEqual(program["budget"], {"model_calls": 20, "seconds": 600})

    def test_program_rejects_unqualified_model(self):
        with self.assertRaisesRegex(ValueError, "provider/model"):
            build_program(
                "repair it",
                "gpt-5.6-sol",
                "/tmp/private.json",
                "/workspace",
                model_calls=1,
                input_tokens=1,
                output_tokens=1,
                seconds=1,
                reasoning_effort="low",
            )

    def test_program_can_diagnose_then_implement_in_separate_model_episodes(self):
        program = build_program(
            "repair it",
            "openai-codex/gpt-5.6-sol",
            "/tmp/private.json",
            "/workspace",
            model_calls=20,
            input_tokens=None,
            output_tokens=None,
            seconds=600,
            reasoning_effort="low",
            diagnosis_model_name="openai-codex/gpt-5.6-luna",
            diagnosis_reasoning_effort="high",
            diagnosis_model_calls=6,
        )
        nodes = program["workflow"]["nodes"]
        diagnosis = nodes["diagnose-task"]["model"]
        implementation = nodes["implement-task"]["model"]
        self.assertEqual(diagnosis["model"]["model"], "gpt-5.6-luna")
        self.assertEqual(diagnosis["budget"]["model_calls"], 6)
        self.assertEqual(diagnosis["tools"], ["read", "grep"])
        self.assertNotIn("sandbox", diagnosis)
        self.assertIn("returns", diagnosis["done_when"])
        self.assertEqual(implementation["model"]["model"], "gpt-5.6-sol")
        self.assertEqual(implementation["budget"]["model_calls"], 14)
        self.assertEqual(nodes["implement-task"]["follows"], ["task", "diagnose-task"])
        self.assertTrue(nodes["implement-task"]["terminal"])


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

    def test_cost_uses_cached_rate_and_request_level_long_context_multiplier(self):
        cost = estimate_usage_cost(
            [
                {"input": 100_000, "output": 1_000, "cache_read": 80_000},
                {"input": 300_000, "output": 2_000, "cache_read": 100_000},
            ],
            input_per_million=4.0,
            cached_input_per_million=0.4,
            output_per_million=20.0,
            long_context_threshold=272_000,
            long_context_input_multiplier=2.0,
            long_context_output_multiplier=1.5,
        )
        self.assertAlmostEqual(cost, 1.872)

    def test_summary_prices_each_child_with_its_recorded_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "children" / "ep_child"
            child.mkdir(parents=True)
            for path, model, usage in [
                (root / "episode.jsonl", "gpt-5.6-sol", {"input": 1000, "output": 100, "cache_read": 0}),
                (child / "episode.jsonl", "gpt-5.6-luna", {"input": 1000, "output": 100, "cache_read": 0}),
            ]:
                events = [
                    {
                        "type": "episode/start",
                        "data": {"program": {"model": {"provider": "openai-codex", "model": model}}},
                    },
                    {"type": "assistant/message", "data": {"usage": usage}},
                ]
                if path == root / "episode.jsonl":
                    events.append({"type": "episode/end", "data": {"outcome": {"kind": "completed"}}})
                path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            common = {
                "cached_input_per_million": 0.0,
                "long_context_threshold": 272_000,
                "long_context_input_multiplier": 1.0,
                "long_context_output_multiplier": 1.0,
            }
            summary = read_episode_summary(
                root,
                {
                    "openai-codex/gpt-5.6-sol": {**common, "input_per_million": 4.0, "output_per_million": 20.0},
                    "openai-codex/gpt-5.6-luna": {**common, "input_per_million": 0.2, "output_per_million": 1.2},
                },
            )
        self.assertAlmostEqual(summary["estimated_cost_usd"], 0.00632)


if __name__ == "__main__":
    unittest.main()
