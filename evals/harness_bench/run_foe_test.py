#!/usr/bin/python3
from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("run_foe", Path(__file__).with_name("run_foe.py"))
assert _SPEC and _SPEC.loader
run_foe = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = run_foe
_SPEC.loader.exec_module(run_foe)


class HarnessBenchAdapterTest(unittest.TestCase):
    def test_evaluator_patch_identity_matches_the_pinned_patch(self) -> None:
        patch = Path(__file__).with_name("knowledge_qa_claim_scoring.patch")
        digest = "sha256:" + hashlib.sha256(patch.read_bytes()).hexdigest()
        self.assertEqual(run_foe.BENCHMARK_PATCH, digest)

    def test_prompt_substitutes_runtime_values(self) -> None:
        prompt = "$WORKSPACE/data at $MOCK_API_BASE and $WORKSPACE again"
        rendered = run_foe.render_prompt(
            prompt,
            Path("/tmp/workspace"),
            {"MOCK_API_BASE": "http://127.0.0.1:36001"},
        )
        self.assertEqual(
            rendered,
            "/tmp/workspace/data at http://127.0.0.1:36001 and /tmp/workspace again",
        )

    def test_instruction_block_parser_stops_at_next_key(self) -> None:
        text = 'task_id: "case"\ninstruction: |-\n  First line.\n  Second line.\nauthor_name: Person\n'
        self.assertEqual(run_foe.yaml_block(text, "instruction"), "First line.\nSecond line.")

    def test_fixture_digest_changes_with_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a").write_text("one", encoding="utf-8")
            first = run_foe.tree_digest(root)
            (root / "a").write_text("two", encoding="utf-8")
            self.assertNotEqual(first, run_foe.tree_digest(root))

    def test_limits_are_split_and_finite(self) -> None:
        self.assertEqual(len(run_foe.TASKS), 6)
        for task in run_foe.TASKS.values():
            self.assertGreater(task.input_tokens, task.output_tokens)
            self.assertGreater(task.model_calls, 0)
            self.assertGreater(task.seconds, 0)

    def test_confirmation_tasks_have_only_the_required_write_roots(self) -> None:
        self.assertEqual(
            run_foe.TASKS["085-flaky-test-root-cause"].write_paths,
            ("in/flakyqueue", "out"),
        )
        self.assertEqual(
            run_foe.TASKS["096-offline-knowledge-qa-insufficient-evidence"].write_paths,
            ("out",),
        )

    def test_flaky_task_receives_the_isolated_pytest_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = root / "tools" / "test"
            config = run_foe.contract(
                run_foe.TASKS["085-flaky-test-root-cause"],
                root,
                "task",
                {"provider": "p", "model": "m"},
                {"test": tool},
            )
        self.assertIn("test", config["tools"])
        self.assertEqual(config["tool_defs"]["test"]["cwd"], str(root / "in" / "flakyqueue"))
        self.assertIn(str(root / "pytest-venv"), config["grants"]["read"])

    def test_visible_test_wrapper_contains_no_attempt_path(self) -> None:
        for identifier in ("083-monorepo-interface-repair", "085-flaky-test-root-cause"):
            command = run_foe.visible_test_command(run_foe.TASKS[identifier])
            self.assertIn('${0%/*}/../pytest-venv/bin/python3', command)
            self.assertNotIn("/tmp/", command)

    def test_visible_test_wrapper_executes_sibling_python(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "tools" / "test"
            python = root / "pytest-venv" / "bin" / "python3"
            executable.parent.mkdir()
            python.parent.mkdir(parents=True)
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$*"\n', encoding="utf-8")
            python.chmod(0o755)
            command = run_foe.visible_test_command(run_foe.TASKS["085-flaky-test-root-cause"])
            executable.write_text(f"#!/bin/sh\n{command}\n", encoding="utf-8")
            executable.chmod(0o755)
            result = subprocess.run(
                [str(executable)],
                check=True,
                capture_output=True,
                cwd=root,
                text=True,
            )
        self.assertEqual(result.stdout.strip(), "-m pytest tests")

    def test_reasoning_effort_is_recorded_in_the_model_route(self) -> None:
        self.assertEqual(
            run_foe.model_route("openai-codex/gpt-5.6-sol", "medium"),
            {"provider": "openai-codex", "model": "gpt-5.6-sol", "reasoning_effort": "medium"},
        )
        self.assertEqual(
            run_foe.model_route("openai-codex/gpt-5.6-sol"),
            {"provider": "openai-codex", "model": "gpt-5.6-sol"},
        )

    def test_grade_score_prefers_programmatic_outcome(self) -> None:
        self.assertEqual(run_foe.grade_score({"outcome_score": 0.75, "score": 0.5}), 0.75)
        self.assertIsNone(run_foe.grade_score({"checks": []}))

    def test_report_preview_identifies_the_evaluated_source_and_binary(self) -> None:
        identity = {
            "source_tree": "git-tree-sha1:" + "a" * 40,
            "runtime_binary": "sha256:" + "b" * 64,
        }
        report = run_foe.preview(
            [run_foe.TASKS["015-security-injection-defense"]],
            1,
            {"provider": "p", "model": "m"},
            identity,
        )
        self.assertEqual(report["evaluated_foe"], identity)


if __name__ == "__main__":
    unittest.main()
