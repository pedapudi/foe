#!/usr/bin/python3

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from run import Pricing
from run_self_improvement import (
    build_config,
    candidate_artifact_identity,
    check_candidate,
    measure_episode,
    model_config,
    rust_toolchain_identity,
    validate_program,
    write_candidate_check,
)


class SelfImprovementConfigTest(unittest.TestCase):
    def test_rust_toolchain_identity_hashes_every_validation_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("cargo", "rustc", "rustfmt", "clippy-driver"):
                (root / name).write_text(name + "\n", encoding="utf-8")
            identity = rust_toolchain_identity(root / "cargo")
            (root / "rustc").write_text("changed\n", encoding="utf-8")
            changed = rust_toolchain_identity(root / "cargo")
        self.assertEqual(sorted(identity), ["cargo", "clippy-driver", "rustc", "rustfmt"])
        self.assertNotEqual(identity["rustc"], changed["rustc"])
        self.assertEqual(identity["cargo"], changed["cargo"])

    def test_candidate_artifact_identity_binds_base_and_changed_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changed = root / "crates/core/src/lib.rs"
            changed.parent.mkdir(parents=True)
            changed.write_text("one\n", encoding="utf-8")
            first = candidate_artifact_identity(
                root, "git-tree-sha1:" + "1" * 40, ["docs/deleted.md", "crates/core/src/lib.rs"]
            )
            changed.write_text("two\n", encoding="utf-8")
            second = candidate_artifact_identity(
                root, "git-tree-sha1:" + "1" * 40, ["docs/deleted.md", "crates/core/src/lib.rs"]
            )
        self.assertEqual(first["files"]["docs/deleted.md"], "absent")
        self.assertNotEqual(first["files"]["crates/core/src/lib.rs"], second["files"]["crates/core/src/lib.rs"])
        self.assertNotEqual(first["digest"], second["digest"])

    def test_workflow_uses_typed_handoff_and_a_full_coding_surface(self):
        root = Path("/tmp/candidate")
        config = build_config(
            root,
            Path("/tmp/evidence.json"),
            Path("/tmp/check"),
            model_config("openai-codex/gpt-5.6-terra", "high"),
            model_config("openai-codex/gpt-5.6-luna", "high"),
            [Path("/opt/toolchain")],
            [Path("/repo/.git")],
            [Path("/opt/cargo-cache")],
            "Raise verified completion.",
        )
        nodes = config["workflow"]["nodes"]
        diagnosis = nodes["diagnose-runtime"]["model"]
        implementation = nodes["implement-runtime-improvement"]["model"]
        self.assertEqual(
            nodes["implement-runtime-improvement"]["follows"],
            ["task", "diagnose-runtime"],
        )
        self.assertEqual(implementation["tools"][:4], ["read", "grep", "edit", "bash"])
        self.assertEqual(diagnosis["tools"], ["block"])
        self.assertEqual(diagnosis["grants"]["read"], ["/tmp"])
        self.assertIn("block", config["tools"])
        self.assertNotIn("input_tokens", config["budget"])
        self.assertNotIn("output_tokens", implementation["budget"])
        self.assertEqual(diagnosis["model"]["model"], "gpt-5.6-luna")
        self.assertIn("higher-cost successful setting", diagnosis["instructions"]["controls"])
        self.assertIn("recorded program", diagnosis["instructions"]["controls"])
        returns = diagnosis["done_when"]["returns"]
        self.assertEqual(
            returns["required"],
            [
                "limitation",
                "attribution",
                "causal_contrast",
                "intervention",
                "activation_path",
                "preserved_controls",
                "falsification_condition",
            ],
        )
        self.assertIn("falsification_condition", returns["required"])
        self.assertNotIn("required_paths", returns["properties"])
        self.assertNotIn("runtime_activation", returns["properties"])
        self.assertNotIn("implementation_files", returns["properties"])
        self.assertNotIn("model", implementation)
        self.assertIn("reasoning settings", implementation["instructions"]["independence"])
        self.assertEqual(config["model"]["reasoning_effort"], "high")
        self.assertEqual(config["model"]["service_tier"], "priority")
        self.assertEqual(
            diagnosis["budget"],
            {"model_calls": 20, "seconds": 1800, "loop_threshold": 8},
        )
        self.assertIn("four model requests as a planning target", diagnosis["instructions"]["result"])
        self.assertIn("loop backstop", diagnosis["instructions"]["result"])
        self.assertIn("must not branch on", diagnosis["instructions"]["controls"])
        self.assertEqual(
            implementation["grants"]["write"],
            [
                *(str(root / directory) for directory in ("crates", "docs", "examples")),
                str(root / "target" / "foe-self-improvement-check"),
            ],
        )
        self.assertEqual(
            implementation["grants"]["read"],
            [str(root), "/repo/.git", "/opt/cargo-cache"],
        )
        self.assertEqual(implementation["grants"]["execute"], ["/opt/toolchain"])
        self.assertEqual(config["task"], "Raise verified completion.")
        self.assertEqual(config["budget"]["loop_threshold"], 8)
        self.assertEqual(implementation["budget"]["loop_threshold"], 8)

    def test_program_validation_reports_construction_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / "program.json"
            program.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "generated self-improvement program is invalid"):
                validate_program(Path("/bin/false"), program)

    def test_candidate_check_runs_rust_validation_and_accepts_clean_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            for directory_name in ("crates/core/src", "docs", "scripts"):
                (candidate / directory_name).mkdir(parents=True, exist_ok=True)
            implementation = candidate / "crates/core/src/lib.rs"
            regression = candidate / "crates/core/src/lib_test.rs"
            specification = candidate / "docs/design.md"
            implementation.write_text("pub fn value() -> u8 { 1 }\n", encoding="utf-8")
            regression.write_text("#[test]\nfn value_is_one() {}\n", encoding="utf-8")
            specification.write_text("The value is one.\n", encoding="utf-8")
            loc = candidate / "scripts/loc.sh"
            loc.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            subprocess.run(["git", "init", "--quiet", str(candidate)], check=True)
            subprocess.run(["git", "-C", str(candidate), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(candidate),
                    "-c",
                    "user.name=Foe Test",
                    "-c",
                    "user.email=foe@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "Create candidate",
                ],
                check=True,
            )
            toolchain = root / "toolchain/bin"
            toolchain.mkdir(parents=True)
            cargo = toolchain / "cargo"
            calls = root / "cargo-calls"
            cargo.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {calls}\nexit 0\n", encoding="utf-8")
            cargo.chmod(0o755)
            cargo_home = root / "cargo-home"
            cargo_home.mkdir()
            cargo_target = candidate / "target/check"
            cargo_target.mkdir(parents=True)
            check = root / "candidate-check"
            write_candidate_check(check, candidate, cargo, cargo_home, cargo_target)
            implementation.write_text("pub fn value() -> u8 { 2 }\n", encoding="utf-8")
            regression.write_text("#[test]\nfn value_is_two() {}\n", encoding="utf-8")
            specification.write_text("The value is two.\n", encoding="utf-8")
            accepted = check_candidate(check, candidate)
            sandboxed = subprocess.run(
                [str(check)], text=True, capture_output=True, check=True
            )
            cargo_calls = calls.read_text(encoding="utf-8").splitlines()
        self.assertTrue(accepted["accepted"], accepted)
        self.assertEqual(sandboxed.stdout, "\n")
        self.assertEqual(len(cargo_calls), 6)
        self.assertEqual(cargo_calls[1], "test --workspace")
        self.assertEqual(
            cargo_calls[4],
            "test --workspace --exclude foe --exclude foe-transport --exclude foe-view -- --skip sandbox::tests::",
        )

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
