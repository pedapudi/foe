#!/usr/bin/python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "run_self_improvement", Path(__file__).with_name("run_self_improvement.py")
)
assert _SPEC and _SPEC.loader
run_self_improvement = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = run_self_improvement
_SPEC.loader.exec_module(run_self_improvement)


def repository(root: Path, content: str = "[workspace]\n") -> None:
    root.mkdir()
    subprocess.run(["/usr/bin/git", "init", "--quiet", str(root)], check=True)
    (root / "Cargo.toml").write_text(content, encoding="utf-8")
    subprocess.run(["/usr/bin/git", "-C", str(root), "add", "Cargo.toml"], check=True)
    subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(root),
            "-c",
            "user.name=Foe Test",
            "-c",
            "user.email=foe@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Create test repository",
        ],
        check=True,
    )


class SelfImprovementTest(unittest.TestCase):
    def evaluated_fixture(self, root: Path) -> tuple[Path, Path, Path, dict[str, str]]:
        candidate = root / "candidate"
        repository(candidate)
        binary = root / "foe"
        binary.write_bytes(b"runtime")
        evaluated = {
            "source_tree": run_self_improvement.clean_source_tree(candidate),
            "runtime_binary": run_self_improvement.sha256_file(binary),
        }
        evidence = root / "evidence.json"
        evidence.write_text(json.dumps({"evaluated_foe": evaluated}), encoding="utf-8")
        return candidate, binary, evidence, evaluated

    def test_workflow_has_a_finite_allowance_and_default_coding_tools(self) -> None:
        self.assertEqual(
            run_self_improvement.LIMITS,
            {"model_calls": 12, "input_tokens": 300_000, "output_tokens": 20_000, "seconds": 1_200},
        )
        config = run_self_improvement.config(
            Path("/candidate"), Path("/evidence/report.json"), Path("/check"), {"provider": "p", "model": "m"}
        )
        diagnosis = config["workflow"]["nodes"]["diagnose-runtime"]["model"]
        child = config["workflow"]["nodes"]["improve-runtime"]["model"]
        self.assertTrue(set(run_self_improvement.CODING_TOOLS).issubset(config["tools"]))
        self.assertTrue(set(run_self_improvement.CODING_TOOLS).issubset(child["tools"]))
        self.assertNotIn("edit", diagnosis["tools"])
        self.assertEqual(diagnosis["budget"]["model_calls"] + child["budget"]["model_calls"], 12)
        self.assertEqual(
            diagnosis["budget"]["input_tokens"] + child["budget"]["input_tokens"], 300_000
        )
        self.assertEqual(config["workflow"]["nodes"]["improve-runtime"]["follows"], ["task", "diagnose-runtime"])

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

    def test_matching_candidate_and_binary_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate, binary, evidence, evaluated = self.evaluated_fixture(Path(directory))
            self.assertEqual(
                run_self_improvement.verify_evaluated_build(candidate, binary, evidence),
                evaluated,
            )

    def test_missing_evaluated_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate, binary, evidence, _ = self.evaluated_fixture(Path(directory))
            evidence.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "lacks evaluated_foe"):
                run_self_improvement.verify_evaluated_build(candidate, binary, evidence)

    def test_another_source_tree_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, binary, evidence, _ = self.evaluated_fixture(root)
            other = root / "other"
            repository(other, "[workspace]\nmembers = []\n")
            report = json.loads(evidence.read_text(encoding="utf-8"))
            report["evaluated_foe"]["source_tree"] = run_self_improvement.clean_source_tree(other)
            evidence.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "candidate source tree differs"):
                run_self_improvement.verify_evaluated_build(candidate, binary, evidence)

    def test_dirty_candidate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate, binary, evidence, _ = self.evaluated_fixture(Path(directory))
            (candidate / "untracked").write_text("content", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source tree is not clean"):
                run_self_improvement.verify_evaluated_build(candidate, binary, evidence)

    def test_another_runtime_binary_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate, binary, evidence, _ = self.evaluated_fixture(Path(directory))
            binary.write_bytes(b"different runtime")
            with self.assertRaisesRegex(ValueError, "runtime binary differs"):
                run_self_improvement.verify_evaluated_build(candidate, binary, evidence)

    def test_evaluated_build_mismatches_precede_episode_directory_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, binary, evidence, evaluated = self.evaluated_fixture(root)
            mismatches = {
                "source": {**evaluated, "source_tree": "git-tree-sha1:" + "c" * 40},
                "binary": {**evaluated, "runtime_binary": "sha256:" + "d" * 64},
            }
            for name, mismatch in mismatches.items():
                with self.subTest(name=name):
                    evidence.write_text(json.dumps({"evaluated_foe": mismatch}), encoding="utf-8")
                    output = root / f"output-{name}"
                    arguments = [
                        "run_self_improvement.py",
                        "--foe",
                        str(binary),
                        "--candidate",
                        str(candidate),
                        "--evidence",
                        str(evidence),
                        "--keep",
                        str(output),
                        "--confirm-spend",
                    ]
                    with (
                        mock.patch.object(sys, "argv", arguments),
                        contextlib.redirect_stdout(io.StringIO()),
                        self.assertRaises(SystemExit),
                    ):
                        run_self_improvement.main()
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
