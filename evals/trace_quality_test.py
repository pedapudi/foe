#!/usr/bin/python3
"""Unit tests for deterministic trace conformance checks."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from trace_quality import evaluate


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
                "program": {
                    "name": "typed-test",
                    "instructions": {"role": "Return a typed result."},
                    "tools": ["block"],
                    "tool_defs": {},
                    "grants": {"read": ["/tmp/project"], "write": [], "spawn": []},
                    "budget": {"model_calls": 2, "tokens": 1000},
                    "done_when": {
                        "returns": {
                            "type": "object",
                            "properties": {"count": {"type": "integer"}},
                            "required": ["count"],
                        }
                    },
                },
                "identity": "sha256:test",
                "task": task,
                "runtime": {"version": "0.1.0", "build": "sha256:test"},
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
        events[0]["data"]["program"]["grants"]["read"] = ["relative"]
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

    def test_missing_concurrent_lease_is_detected(self) -> None:
        events = valid_events()
        events[-1:-1] = [
            event(5, "budget/reserve", {"child_id": "ep_child", "reserved": {"model_calls": 1}}),
            event(6, "budget/release", {"child_id": "ep_child", "spent": {"model_calls": 0}}),
        ]
        events[-1]["seq"] = 7
        report = evaluate_events(events)
        self.assertFalse(report["metrics"]["hierarchical_budgets"]["conformant"])
        self.assertTrue(any("concurrent subtree lease" in item["message"] for item in report["violations"]))

    def assert_dimension_fails(self, events: list[dict[str, Any]], dimension: str) -> None:
        report = evaluate_events(events)
        self.assertFalse(report["metrics"][dimension]["conformant"])
        self.assertTrue(any(item["dimension"] == dimension for item in report["violations"]))


if __name__ == "__main__":
    unittest.main()
