#!/usr/bin/python3
"""Tests for the tool-composition assessment fixtures and decision rule."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from python_composition_assessment import (  # noqa: E402
    CONFIGURATIONS,
    TASKS,
    aggregate_configuration,
    development_gate,
    fixture_digest,
    mechanism_metrics,
    prepare_config,
    redact_value,
    recommendation,
    run_grader,
    schedule,
)


MODEL = {"provider": "fixture-provider", "model": "fixture-model"}


class FixtureTests(unittest.TestCase):
    def test_untouched_fixtures_fail_and_oracles_pass(self) -> None:
        for task in TASKS:
            with self.subTest(task=task.name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = root / "workspace"
                grader = root / "grader"
                workspace.mkdir()
                grader.mkdir()
                metadata = task.materialize(workspace, grader)
                passed, _, _ = run_grader(Path(metadata["grade"]), workspace, None)
                self.assertFalse(passed)
                if metadata["oracle"] is None:
                    task.oracle(workspace, metadata)
                    candidate = None
                else:
                    candidate = metadata["oracle"]
                passed, findings, _ = run_grader(Path(metadata["grade"]), workspace, candidate)
                self.assertTrue(passed, findings)

    def test_each_configuration_changes_only_its_declared_mechanism(self) -> None:
        task = next(task for task in TASKS if task.name == "completed-charge-leader")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            grader = root / "grader"
            workspace.mkdir()
            grader.mkdir()
            metadata = task.materialize(workspace, grader)
            shaped = {
                configuration.name: prepare_config(task, configuration, workspace, metadata, MODEL)
                for configuration in CONFIGURATIONS
            }
        ordinary = shaped["ordinary-coding-tools"]
        shell = shaped["shell-output-narrowing"]
        composition = shaped["tool-composition"]
        self.assertNotIn("compose_tools", ordinary["tools"])
        self.assertEqual(shell["tools"], ordinary["tools"])
        self.assertEqual(
            composition["tools"], [*ordinary["tools"][:4], "compose_tools", *ordinary["tools"][4:]]
        )
        self.assertEqual(set(shell["instructions"]) - set(ordinary["instructions"]), {"40-shell-output"})
        self.assertEqual(composition["instructions"], ordinary["instructions"])
        for config in shaped.values():
            self.assertEqual(config["sandbox"], {"mode": "required"})

    def test_holdout_prompts_do_not_name_a_composition_mechanism(self) -> None:
        forbidden = ("call_tool", "composition", "compose_tools", "shell", "bash")
        for task in (task for task in TASKS if task.task_set == "holdout"):
            with self.subTest(task=task.name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = root / "workspace"
                grader = root / "grader"
                workspace.mkdir()
                grader.mkdir()
                metadata = task.materialize(workspace, grader)
                config = prepare_config(task, CONFIGURATIONS[0], workspace, metadata, MODEL)
                prompt = config["task"].lower()
                self.assertTrue(all(term not in prompt for term in forbidden), prompt)

    def test_read_only_graders_detect_workspace_mutation(self) -> None:
        for name in ("shard-priority-total", "regional-capacity-selection", "completed-charge-leader"):
            task = next(task for task in TASKS if task.name == name)
            with self.subTest(task=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = root / "workspace"
                grader = root / "grader"
                workspace.mkdir()
                grader.mkdir()
                metadata = task.materialize(workspace, grader)
                first = next(path for path in sorted(workspace.rglob("*")) if path.is_file())
                first.write_text(first.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
                passed, findings, _ = run_grader(Path(metadata["grade"]), workspace, metadata["oracle"])
                self.assertFalse(passed)
                self.assertTrue(any("changed" in finding for finding in findings), findings)

    def test_fixture_digest_changes_with_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "sample.txt"
            path.write_text("one", encoding="utf-8")
            before = fixture_digest(root)
            path.write_text("two", encoding="utf-8")
            self.assertNotEqual(before, fixture_digest(root))


class MeasurementTests(unittest.TestCase):
    def test_route_values_are_removed_from_nested_report_text(self) -> None:
        report = {"error": "endpoint-name rejected model-name", "nested": ["model-name"]}
        redacted = redact_value(report, ["endpoint-name", "model-name"])
        text = json.dumps(redacted)
        self.assertNotIn("endpoint-name", text)
        self.assertNotIn("model-name", text)

    def test_mechanism_metrics_separate_inner_and_outer_renderings(self) -> None:
        events = [
            {
                "type": "assistant/message",
                "data": {
                    "tool_calls": [
                        {"id": "outer", "name": "compose_tools", "args": {"source": "def main(): return 3"}}
                    ]
                },
            },
            {
                "type": "tool/inner-call",
                "data": {"outer_call_id": "outer", "call_id": "inner", "index": 0, "name": "read", "args": {}},
            },
            {
                "type": "tool/result",
                "data": {
                    "call_id": "inner",
                    "name": "read",
                    "value": {"content": "abcdef"},
                    "rendered": "abcdef",
                    "is_error": False,
                },
            },
            {
                "type": "tool/result",
                "data": {
                    "call_id": "outer",
                    "name": "compose_tools",
                    "value": {"returned": 3},
                    "rendered": "3",
                    "is_error": False,
                },
            },
        ]
        measured = mechanism_metrics(events)
        self.assertEqual(measured["composition_calls"], 1)
        self.assertEqual(measured["inner_calls"], 1)
        self.assertEqual(measured["inner_calls_by_tool"], {"read": 1})
        self.assertEqual(measured["inner_rendered_bytes"], 6)
        self.assertEqual(measured["outer_composition_rendered_bytes"], 1)
        self.assertEqual(measured["top_level_rendered_bytes"], 1)

    def test_schedule_rotates_configuration_order(self) -> None:
        tasks = tuple(task for task in TASKS if task.task_set == "holdout")
        planned = schedule(tasks, 3)
        for attempt in range(1, 4):
            for task in tasks:
                names = [
                    configuration.name
                    for scheduled_attempt, scheduled_task, configuration in planned
                    if scheduled_attempt == attempt and scheduled_task == task
                ]
                self.assertEqual(set(names), {configuration.name for configuration in CONFIGURATIONS})
        first_names = [
            next(
                configuration.name
                for scheduled_attempt, scheduled_task, configuration in planned
                if scheduled_attempt == attempt and scheduled_task == tasks[0]
            )
            for attempt in range(1, 4)
        ]
        self.assertEqual(set(first_names), {configuration.name for configuration in CONFIGURATIONS})


def result(
    task: str,
    configuration: str,
    *,
    strict: bool = True,
    input_tokens: int = 100,
    composition_calls: int = 0,
) -> dict:
    return {
        "task": task,
        "task_set": "development" if task == "shard-priority-total" else "holdout",
        "configuration": configuration,
        "strict_success": strict,
        "infrastructure_error": None,
        "usage": {
            "usage_reported": True,
            "model_calls": 2,
            "input_tokens": input_tokens,
            "output_tokens": 20,
            "cache_read_tokens": 0,
            "total_tokens": input_tokens + 20,
        },
        "first_request_input_tokens": 30,
        "duration_seconds": 1.0,
        "mechanism": {
            "composition_calls": composition_calls,
            "shell_calls": 0,
            "inner_calls": composition_calls * 3,
        },
    }


class DecisionTests(unittest.TestCase):
    def held_back_results(self) -> list[dict]:
        results = []
        tasks = [task.name for task in TASKS if task.task_set == "holdout"]
        costs = {
            "ordinary-coding-tools": 100,
            "shell-output-narrowing": 90,
            "tool-composition": 75,
        }
        for configuration in costs:
            for task in tasks:
                for _ in range(3):
                    activation = int(configuration == "tool-composition" and task == "regional-capacity-selection")
                    results.append(
                        result(task, configuration, input_tokens=costs[configuration], composition_calls=activation)
                    )
        return results

    def test_all_attempt_input_is_divided_by_strict_successes(self) -> None:
        results = [
            result("batch-partition-repair", "ordinary-coding-tools", input_tokens=100),
            result("batch-partition-repair", "ordinary-coding-tools", strict=False, input_tokens=300),
        ]
        report = aggregate_configuration(results, "ordinary-coding-tools")
        self.assertEqual(report["input_tokens_per_strict_success"], 400)

    def test_material_savings_with_complete_quality_support_adoption(self) -> None:
        report = recommendation(self.held_back_results(), 3)
        self.assertTrue(report["include_composition_by_default"], report["findings"])
        self.assertLessEqual(report["total_token_ratio_against_lower_token_simpler_configuration"], 0.90)

    def test_one_composition_quality_failure_blocks_adoption(self) -> None:
        results = self.held_back_results()
        failed = next(result for result in results if result["configuration"] == "tool-composition")
        failed["strict_success"] = False
        report = recommendation(results, 3)
        self.assertFalse(report["include_composition_by_default"])
        self.assertTrue(any("did not pass every" in finding for finding in report["findings"]))

    def test_development_gate_requires_successful_composition(self) -> None:
        passed = [result("shard-priority-total", "tool-composition", composition_calls=1) for _ in range(2)]
        self.assertEqual(development_gate(passed, 2), (True, []))
        missing = copy.deepcopy(passed)
        missing[0]["mechanism"]["composition_calls"] = 0
        self.assertFalse(development_gate(missing, 2)[0])


if __name__ == "__main__":
    unittest.main()
