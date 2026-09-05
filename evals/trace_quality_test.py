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


def valid_inner_call_events() -> list[dict[str, Any]]:
    """docs/log-format.md `tool/inner-call`: inner results stay out of derived messages."""
    events = valid_events()
    events[0]["data"]["contract"]["tools"] = ["compose_tools", "read"]
    events[4] = event(
        4,
        "assistant/message",
        {
            "step": 1,
            "request_id": "rq_1",
            "text": "",
            "tool_calls": [
                {"id": "outer", "name": "compose_tools", "args": {"source": "def main(): return 2"}}
            ],
            "stop": "tool",
            "usage": {"input": 10, "output": 2, "cache_read": 0},
            "interrupted": False,
        },
    )
    events[5:] = [
        event(
            5,
            "tool/inner-call",
            {"outer_call_id": "outer", "call_id": "inner", "index": 0, "name": "read", "args": {"path": "x"}},
        ),
        event(
            6,
            "tool/result",
            {
                "step": 1,
                "call_id": "inner",
                "name": "read",
                "value": {"content": "two"},
                "rendered": "two",
                "is_error": False,
                "duration_ms": 1,
                "synthetic": False,
            },
        ),
        event(
            7,
            "tool/result",
            {
                "step": 1,
                "call_id": "outer",
                "name": "compose_tools",
                "value": {"returned": 2},
                "rendered": "2",
                "is_error": False,
                "duration_ms": 2,
                "synthetic": False,
            },
        ),
        event(
            8,
            "model/request",
            {
                "step": 2,
                "attempt": 1,
                "request_id": "rq_2",
                "header_seq": 2,
                "consumed": [],
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Return the typed result."}]},
                    {
                        "role": "assistant",
                        "text": "",
                        "tool_calls": [
                            {"id": "outer", "name": "compose_tools", "args": {"source": "def main(): return 2"}}
                        ],
                    },
                    {
                        "role": "tool",
                        "call_id": "outer",
                        "name": "compose_tools",
                        "rendered": "2",
                        "is_error": False,
                    },
                ],
            },
        ),
        event(
            9,
            "assistant/message",
            {
                "step": 2,
                "request_id": "rq_2",
                "text": "",
                "tool_calls": [{"id": "returned", "name": "return", "args": {"value": {"count": 2}}}],
                "stop": "tool",
                "usage": {"input": 20, "output": 2, "cache_read": 0},
                "interrupted": False,
            },
        ),
        event(
            10,
            "tool/result",
            {
                "step": 2,
                "call_id": "returned",
                "name": "return",
                "value": {"count": 2},
                "rendered": '{"count":2}',
                "is_error": False,
                "duration_ms": 0,
                "synthetic": False,
            },
        ),
        event(11, "episode/end", {"outcome": {"kind": "completed", "value": {"count": 2}}}),
    ]
    return events


def evaluate_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temporary:
        log = Path(temporary) / "episode.jsonl"
        log.write_text(
            "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in events),
            encoding="utf-8",
        )
        return evaluate([log])


class TraceQualityTest(unittest.TestCase):
    def test_message_identity_and_redelivery_checks(self) -> None:
        """docs/log-format.md Team: queued ids are unique; receipts may repeat."""
        original = valid_events()
        ending = original.pop()
        message = {"message_id": "ep_test:tm_01", "from": "ep_test", "to": "ep_child", "content": []}
        receipt = {"message_id": message["message_id"], "to": "ep_child"}
        for kind, data in [("team/message", message), ("team/delivered", receipt), ("team/delivered", receipt)]:
            original.append(event(len(original), kind, data))
        original.append(event(len(original), "episode/end", ending["data"]))
        report = evaluate_events(original)
        self.assertTrue(report["valid"], report["violations"])
        changed = copy.deepcopy(original)
        changed[-2] = event(changed[-2]["seq"], "team/message", {**message, "content": [{"type": "text", "text": "different message"}]})
        report = evaluate_events(changed)
        self.assertTrue(any("team/message.message_id" in v["message"] for v in report["violations"]))
        changed = copy.deepcopy(original)
        changed[-2]["data"]["to"] = "ep_wrong"
        report = evaluate_events(changed)
        self.assertTrue(any("team/delivered" in v["message"] for v in report["violations"]))

    def test_duplicate_peer_inbox_items_fail_conformance(self) -> None:
        """docs/log-format.md Team: repeated delivery produces one inbox item."""
        events = valid_events()
        ending = events.pop()
        item = {"source": "peer", "from": "ep_sender", "message_id": "ep_lead:tm_01", "content": []}
        events.extend([event(len(events), "inbox/item", item), event(len(events) + 1, "inbox/item", item)])
        events.append(event(len(events), "episode/end", ending["data"]))
        report = evaluate_events(events)
        self.assertTrue(any("peer inbox message_id" in v["message"] for v in report["violations"]))

    def test_valid_trace_conforms(self) -> None:
        report = evaluate_events(valid_events())
        self.assertTrue(report["valid"], report["violations"])
        for dimension in (
            "declared_permissions",
            "reconstructable_evidence",
            "typed_outcomes",
        ):
            self.assertTrue(report["metrics"][dimension]["conformant"])

    def test_permission_mutation_is_detected(self) -> None:
        events = copy.deepcopy(valid_events())
        events[0]["data"]["contract"]["grants"]["read"] = ["relative"]
        self.assert_dimension_fails(events, "declared_permissions")

    def test_inner_call_trace_conforms_and_excludes_the_inner_result(self) -> None:
        report = evaluate_events(valid_inner_call_events())
        self.assertTrue(report["valid"], report["violations"])

    def test_inner_call_must_name_an_unsettled_model_issued_call(self) -> None:
        events = valid_inner_call_events()
        events[5]["data"]["outer_call_id"] = "missing"
        self.assert_dimension_fails(events, "reconstructable_evidence")

    def test_inner_call_index_counts_from_zero(self) -> None:
        events = valid_inner_call_events()
        events[5]["data"]["index"] = 1
        self.assert_dimension_fails(events, "reconstructable_evidence")

    def test_inner_result_in_a_model_request_is_detected(self) -> None:
        events = valid_inner_call_events()
        events[8]["data"]["messages"].insert(
            2,
            {"role": "tool", "call_id": "inner", "name": "read", "rendered": "two", "is_error": False},
        )
        self.assert_dimension_fails(events, "reconstructable_evidence")

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
        self.assert_dimension_fails(events, "declared_permissions")
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
