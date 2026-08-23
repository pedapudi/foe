#!/usr/bin/python3

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from collect_diagnostics import collect, input_growth_landmarks


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
                    "token_limits": "measurement_only",
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
            report = collect(source, binary, [run])
        self.assertEqual(report["evaluated_foe"], identity)
        self.assertEqual(report["schema_version"], 2)
        diagnosis = report["trajectory_diagnostics"][0]
        self.assertEqual(diagnosis["task"], "terminal-bench/example")
        self.assertEqual(diagnosis["evaluation"]["label"], "development")
        self.assertEqual(diagnosis["evaluation"]["reasoning_effort"], "low")
        self.assertNotIn("per_request", diagnosis["usage"])
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
                collect(source, binary, [run])

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
        for field in ("dataset", "label", "model", "reasoning_effort", "token_limits"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                source, binary, run, _ = self.fixture(Path(directory))
                manifest = run / "campaign.json"
                value = json.loads(manifest.read_text(encoding="utf-8"))
                value[field] = None
                manifest.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, f"string `{field}`"):
                    collect(source, binary, [run])


if __name__ == "__main__":
    unittest.main()
