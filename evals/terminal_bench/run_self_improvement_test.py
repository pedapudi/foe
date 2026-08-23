#!/usr/bin/python3

import json
import tempfile
import unittest
from pathlib import Path

from run import Pricing
from run_self_improvement import build_config, measure_episode, model_config


class SelfImprovementConfigTest(unittest.TestCase):
    def test_workflow_uses_typed_handoff_and_a_full_coding_surface(self):
        root = Path("/tmp/candidate")
        config = build_config(
            root,
            Path("/tmp/evidence.json"),
            Path("/tmp/check"),
            model_config("openai-codex/gpt-5.6-terra", "high"),
        )
        nodes = config["workflow"]["nodes"]
        diagnosis = nodes["diagnose-runtime"]["model"]
        implementation = nodes["implement-runtime-improvement"]["model"]
        self.assertEqual(
            nodes["implement-runtime-improvement"]["follows"],
            ["task", "diagnose-runtime"],
        )
        self.assertEqual(implementation["tools"][:4], ["read", "grep", "edit", "bash"])
        self.assertIn("bash", diagnosis["tools"])
        self.assertNotIn("input_tokens", config["budget"])
        self.assertNotIn("output_tokens", implementation["budget"])
        self.assertNotIn("model", diagnosis)
        self.assertNotIn("model", implementation)
        self.assertEqual(config["model"]["reasoning_effort"], "high")
        self.assertEqual(implementation["grants"]["write"], [str(root)])

    def test_episode_measurement_prices_each_model_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "children" / "ep_child"
            child.mkdir(parents=True)
            events = [
                {
                    "type": "episode/start",
                    "data": {
                        "program": {
                            "model": {
                                "provider": "openai-codex",
                                "model": "gpt-5.6-luna",
                            }
                        }
                    },
                },
                {"type": "model/request", "data": {}},
                {
                    "type": "assistant/message",
                    "data": {"usage": {"input": 1000, "cache_read": 500, "output": 100}},
                },
            ]
            (child / "episode.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            pricing = {
                "openai-codex/gpt-5.6-luna": Pricing(
                    source="https://example.invalid",
                    input_per_million=0.2,
                    cached_input_per_million=0.02,
                    output_per_million=1.2,
                    long_context_threshold=272000,
                    long_context_input_multiplier=2.0,
                    long_context_output_multiplier=1.5,
                )
            }
            measured = measure_episode(root, pricing)
        self.assertEqual(measured["model_calls"], 1)
        self.assertAlmostEqual(measured["estimated_cost_usd"], 0.00023)


if __name__ == "__main__":
    unittest.main()
