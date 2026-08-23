#!/usr/bin/python3
from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
