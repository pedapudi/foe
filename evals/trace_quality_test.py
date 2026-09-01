#!/usr/bin/python3
"""Unit tests for deterministic trace conformance checks."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from trace_quality import EpisodeLog, Evaluation, _check_budgets, evaluate


def event(seq: int, kind: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"seq": seq, "time": 1000 + seq, "type": kind, "data": data}


def valid_events() -> list[dict[str, Any]]:
    task = "Return the typed result."
    messages = [{"role": "user", "content": [{"type": "text", "text": task}]}]
    return [
        event(
            0,
            "episode/start",
            {
                "id": "ep_test",
                "parent_id": None,
                "fork_origin": None,
                "team_id": None,
                "contract": {
                    "name": "typed-test",
                    "instructions": {"role": "Return a typed result."},
                    "tools": ["block"],
                    "tool_defs": {},
                    "grants": {"read": ["/tmp/project"], "write": [], "spawn": []},
                    "budget": {"model_calls": 2, "input_tokens": 800, "output_tokens": 200},
                    "done_when": {
                        "returns": {
                            "type": "object",
                            "properties": {"count": {"type": "integer"}},
                            "required": ["count"],
                        }
                    },
                },
                "contract_fingerprint": "sha256:test",
                "task": task,
                "runtime": {"version": "0.2.0", "build": "sha256:test"},
                "sandbox": {"mode": "off", "landlock_abi": 0},
            },
        ),
        event(
            1,
            "inbox/item",
            {
                "source": "task",
                "content": [{"type": "text", "text": task}],
                "from": None,
                "message_id": None,
            },
        ),
        event(
            2,
            "request/header",
            {"reason": "initial", "system": "Return a typed result.", "tools": [], "model": {}},
        ),
        event(
            3,
            "model/request",
            {
                "step": 1,
                "attempt": 1,
                "request_id": "rq_1",
                "header_seq": 2,
                "consumed": [1],
                "messages": messages,
            },
        ),
        event(
            4,
            "assistant/message",
            {
                "step": 1,
                "request_id": "rq_1",
                "text": "",
                "tool_calls": [],
                "stop": "end",
                "usage": {"input": 10, "output": 2, "cache_read": 0},
                "interrupted": False,
            },
        ),
        event(5, "episode/end", {"outcome": {"kind": "completed", "value": {"count": 2}}}),
    ]


def evaluate_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temporary:
        log = Path(temporary) / "episode.jsonl"
        log.write_text(
            "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in events),
            encoding="utf-8",
        )
        return evaluate([log])


class TraceQualityTest(unittest.TestCase):
    def test_valid_trace_conforms(self) -> None:
        report = evaluate_events(valid_events())
        self.assertTrue(report["valid"], report["violations"])
        for dimension in (
            "declared_authority",
            "reconstructable_evidence",
            "typed_outcomes",
        ):
            self.assertTrue(report["metrics"][dimension]["conformant"])

    def test_authority_mutation_is_detected(self) -> None:
        events = copy.deepcopy(valid_events())
        events[0]["data"]["contract"]["grants"]["read"] = ["relative"]
        self.assert_dimension_fails(events, "declared_authority")

    def test_message_mutation_is_detected(self) -> None:
        events = copy.deepcopy(valid_events())
        events[3]["data"]["messages"].append({"role": "user", "content": []})
        self.assert_dimension_fails(events, "reconstructable_evidence")

    def test_typed_value_mutation_is_detected(self) -> None:
        events = copy.deepcopy(valid_events())
        events[-1]["data"]["outcome"]["value"]["count"] = "two"
        self.assert_dimension_fails(events, "typed_outcomes")

    def test_malformed_landlock_abi_is_reported_rather_than_raised(self) -> None:
        events = copy.deepcopy(valid_events())
        events[0]["data"]["sandbox"]["landlock_abi"] = "unknown"
        report = evaluate_events(events)
        self.assert_dimension_fails(events, "declared_authority")
        self.assertEqual(report["observations"]["landlock_abis"], {"invalid": 1})

    def test_codex_output_overrun_is_accounted_without_claiming_a_cap(self) -> None:
        report = self.evaluate_child_output_overrun("openai-codex")
        self.assertTrue(report["metrics"]["hierarchical_budgets"]["conformant"], report["violations"])

    def test_provider_output_overrun_fails_when_the_route_accepts_a_cap(self) -> None:
        report = self.evaluate_child_output_overrun("openai")
        self.assertFalse(report["metrics"]["hierarchical_budgets"]["conformant"])
        self.assertTrue(
            any("output_tokens reservation" in item["message"] for item in report["violations"]),
            report["violations"],
        )

    def evaluate_child_output_overrun(self, provider: str) -> dict[str, Any]:
        outcome = {"kind": "completed", "value": "done"}
        parent = EpisodeLog(
            Path("parent"),
            [
                event(
                    0,
                    "episode/start",
                    {
                        "id": "parent",
                        "parent_id": None,
                        "contract": {
                            "name": "parent",
                            "budget": {
                                "model_calls": 2,
                                "input_tokens": 100,
                                "output_tokens": 100,
                                "max_episodes": 2,
                            },
                        },
                    },
                ),
                event(
                    1,
                    "budget/reserve",
                    {
                        "child_id": "child",
                        "reserved": {"model_calls": 1, "input_tokens": 10, "output_tokens": 10, "episodes": 1},
                    },
                ),
                event(2, "spawn/start", {"child_id": "child"}),
                event(3, "spawn/end", {"child_id": "child", "outcome": outcome}),
                event(
                    4,
                    "budget/release",
                    {
                        "child_id": "child",
                        "spent": {"model_calls": 1, "input_tokens": 20, "output_tokens": 20, "episodes": 1},
                    },
                ),
            ],
        )
        child = EpisodeLog(
            Path("child"),
            [
                event(0, "episode/start", {"id": "child", "parent_id": "parent", "contract": {"name": "child"}}),
                event(1, "request/header", {"model": {"provider": provider, "model": "test"}}),
                event(2, "model/request", {}),
                event(3, "assistant/message", {"usage": {"input": 20, "output": 20}}),
                event(4, "episode/end", {"outcome": outcome}),
            ],
        )
        evaluation = Evaluation()
        _check_budgets(evaluation, [parent, child])
        return evaluation.report()

    def assert_dimension_fails(self, events: list[dict[str, Any]], dimension: str) -> None:
        report = evaluate_events(events)
        self.assertFalse(report["metrics"][dimension]["conformant"])
        self.assertTrue(any(item["dimension"] == dimension for item in report["violations"]))


if __name__ == "__main__":
    unittest.main()
