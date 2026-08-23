#!/usr/bin/python3
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "run_self_improvement", Path(__file__).with_name("run_self_improvement.py")
)
assert _SPEC and _SPEC.loader
run_self_improvement = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = run_self_improvement
_SPEC.loader.exec_module(run_self_improvement)


class SelfImprovementTest(unittest.TestCase):
    def test_workflow_has_a_finite_allowance_and_default_coding_tools(self) -> None:
        self.assertEqual(
            run_self_improvement.LIMITS,
            {"model_calls": 12, "input_tokens": 300_000, "output_tokens": 20_000, "seconds": 1_200},
        )
        config = run_self_improvement.config(
            Path("/candidate"), Path("/evidence/report.json"), Path("/check"), {"provider": "p", "model": "m"}
        )
        child = config["workflow"]["nodes"]["improve-runtime"]["model"]
        self.assertTrue(set(run_self_improvement.CODING_TOOLS).issubset(config["tools"]))
        self.assertTrue(set(run_self_improvement.CODING_TOOLS).issubset(child["tools"]))

    def test_checker_accepts_a_general_change_and_enforces_runtime_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for crate in ("log", "core", "code", "workflow", "context", "view", "cli"):
                (root / "crates" / crate / "src").mkdir(parents=True)
            (root / "docs").mkdir()
            source = root / "crates" / "core" / "src" / "lib.rs"
            source.write_text("fn original() {}\n", encoding="utf-8")
            document = root / "docs" / "design.md"
            document.write_text("Original behavior.\n", encoding="utf-8")
            check = root / "check"
            run_self_improvement.checker(check, root)

            source.write_text("fn improved() {}\n", encoding="utf-8")
            (root / "crates" / "core" / "src" / "lib_test.rs").write_text(
                "#[test]\nfn improvement_is_covered() {}\n", encoding="utf-8"
            )
            document.write_text("Improved behavior.\n", encoding="utf-8")
            accepted = subprocess.run([str(check)], text=True, capture_output=True, check=True)
            self.assertEqual(accepted.stdout.strip(), "")

            source.write_text("fn line() {}\n" * 6_001, encoding="utf-8")
            rejected = subprocess.run([str(check)], text=True, capture_output=True, check=True)
            self.assertIn("runtime contains 6001 counted lines", rejected.stdout)


if __name__ == "__main__":
    unittest.main()
