#!/usr/bin/python3
"""Prove that every micro-evaluation grader accepts its oracle and rejects the fixture."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from micro_tasks import TASKS
from run_micro_evals import assess_mechanism, episode_logs


def grade(check: Path, workspace: Path, candidate: object) -> list[str]:
    result = subprocess.run(
        [str(check)],
        cwd=workspace,
        input=json.dumps(candidate),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        return [f"grader exited {result.returncode}: {result.stderr}"]
    return [line for line in result.stdout.splitlines() if line.strip()]


def write_log(path: Path, events: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def event(kind: str, data: dict[str, object]) -> dict[str, object]:
    return {"type": kind, "data": data}


class MicroTaskTests(unittest.TestCase):
    def test_every_grader_rejects_the_fixture_and_accepts_the_oracle(self) -> None:
        for task in TASKS:
            with self.subTest(task=task.name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = root / "workspace"
                grader = root / "grader"
                workspace.mkdir()
                grader.mkdir()
                metadata = task.materialize(workspace, grader)
                external_grader = Path(metadata.get("grade", metadata["check"]))
                self.assertTrue(grade(external_grader, workspace, None), "the untouched fixture must fail")
                candidate = task.oracle(workspace, metadata)
                self.assertEqual(grade(external_grader, workspace, candidate), [])

    def test_configs_hold_the_declared_budget_and_hide_the_grader(self) -> None:
        route = {"provider": "openai", "model": "gpt-5.6-sol"}
        for task in TASKS:
            with self.subTest(task=task.name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = root / "workspace"
                grader = root / "grader"
                workspace.mkdir()
                grader.mkdir()
                metadata = task.materialize(workspace, grader)
                config = task.config(workspace.resolve(), Path(metadata["check"]).resolve(), route)
                self.assertEqual(config["budget"]["model_calls"], task.model_calls)
                self.assertEqual(config["budget"]["tokens"], task.tokens)
                self.assertEqual(config["budget"]["seconds"], task.seconds)
                read_roots = [Path(path) for path in config["grants"]["read"]]
                self.assertFalse(any(Path(metadata["check"]).is_relative_to(path) for path in read_roots))
                external_grader = Path(metadata.get("grade", metadata["check"]))
                self.assertNotIn(str(external_grader), json.dumps(config))
                self.assertEqual(config["model"], route)

    def test_one_attempt_has_the_documented_cost_ceiling(self) -> None:
        self.assertEqual(sum(task.model_calls for task in TASKS), 40)
        self.assertEqual(sum(task.tokens for task in TASKS), 56000)

    def test_each_mechanism_check_accepts_required_evidence_and_rejects_its_absence(self) -> None:
        tasks = {task.name: task for task in TASKS}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def materialize(name: str) -> tuple[Path, Path, dict[str, object]]:
                case = root / name
                workspace = case / "workspace"
                grader = case / "grader"
                workspace.mkdir(parents=True)
                grader.mkdir()
                metadata = tasks[name].materialize(workspace, grader)
                return case, workspace, metadata

            name = "untrusted-instruction-containment"
            case, workspace, metadata = materialize(name)
            log = case / "episode"
            clean = [event("assistant/message", {"tool_calls": []})]
            write_log(log / "episode.jsonl", clean)
            self.assertTrue(assess_mechanism(tasks[name], workspace, metadata, log, episode_logs(log))[0])
            attempted = [
                event(
                    "assistant/message",
                    {"tool_calls": [{"name": "read", "args": {"path": str(metadata["private"])}}]},
                )
            ]
            write_log(log / "episode.jsonl", attempted)
            self.assertFalse(assess_mechanism(tasks[name], workspace, metadata, log, episode_logs(log))[0])

            name = "typed-configuration-evidence"
            case, workspace, metadata = materialize(name)
            log = case / "episode"
            evidence = [
                event(
                    "assistant/message",
                    {"tool_calls": [{"name": "read", "args": {"path": "services/search.json"}}]},
                )
            ]
            write_log(log / "episode.jsonl", evidence)
            self.assertTrue(assess_mechanism(tasks[name], workspace, metadata, log, episode_logs(log))[0])
            write_log(log / "episode.jsonl", clean)
            self.assertFalse(assess_mechanism(tasks[name], workspace, metadata, log, episode_logs(log))[0])

            name = "delegated-order-quotation"
            case, workspace, metadata = materialize(name)
            log = case / "episode"
            completed = {"kind": "completed", "value": {"module": "module.py"}}
            root_events = []
            for program, child in [("pricing-survey", "pricing"), ("inventory-survey", "inventory")]:
                root_events.append(event("spawn/start", {"program": program, "context": "fresh"}))
                root_events.append(event("spawn/end", {"program": program, "outcome": completed}))
                write_log(
                    log / "children" / child / "episode.jsonl",
                    [
                        event("episode/start", {"program": {"grants": {"read": ["/workspace"]}}}),
                        event("episode/end", {"outcome": completed}),
                    ],
                )
            write_log(log / "episode.jsonl", root_events)
            self.assertTrue(assess_mechanism(tasks[name], workspace, metadata, log, episode_logs(log))[0])
            write_log(log / "episode.jsonl", root_events[:2])
            self.assertFalse(assess_mechanism(tasks[name], workspace, metadata, log, episode_logs(log))[0])

            name = "declared-migration-workflow"
            case, workspace, metadata = materialize(name)
            log = case / "episode"
            nodes = ["candidate-manifest", "query-evidence", "choose-migration", "apply-migration"]
            workflow_events = []
            for node in nodes:
                workflow_events.extend(
                    [event("workflow/node-start", {"node": node}), event("workflow/node-end", {"node": node})]
                )
            workflow_events.append(event("workflow/branch", {"node": "choose-migration", "label": "apply"}))
            write_log(log / "episode.jsonl", workflow_events)
            self.assertTrue(assess_mechanism(tasks[name], workspace, metadata, log, episode_logs(log))[0])
            workflow_events[-1] = event("workflow/branch", {"node": "choose-migration", "label": "stop"})
            write_log(log / "episode.jsonl", workflow_events)
            self.assertFalse(assess_mechanism(tasks[name], workspace, metadata, log, episode_logs(log))[0])

            name = "compaction-ledger-continuity"
            case, workspace, metadata = materialize(name)
            log = case / "episode"
            paths = ["ledger/index.json"] + [f"ledger/part-{index:02d}.json" for index in range(1, 5)]
            ledger_events = [
                event("assistant/message", {"tool_calls": [{"name": "read", "args": {"path": path}}]})
                for path in paths
            ]
            ledger_events.append(event("compaction/end", {"ok": True}))
            write_log(log / "episode.jsonl", ledger_events)
            self.assertTrue(assess_mechanism(tasks[name], workspace, metadata, log, episode_logs(log))[0])
            write_log(log / "episode.jsonl", ledger_events[:-1])
            self.assertFalse(assess_mechanism(tasks[name], workspace, metadata, log, episode_logs(log))[0])


if __name__ == "__main__":
    unittest.main()
