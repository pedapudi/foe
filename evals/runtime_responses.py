#!/usr/bin/python3
"""Return deterministic responses for the runtime evaluation cases."""

from __future__ import annotations

import json
from typing import Any


ORDINARY_USAGE = {"input": 1400, "output": 200, "cache_read": 0}
SMALL_USAGE = {"input": 20, "output": 10, "cache_read": 0}


Response = list[dict[str, Any]]


def call(call_id: str, name: str, args: dict[str, Any]) -> Response:
    return [
        {"kind": "tool_call_start", "id": call_id, "name": name},
        {"kind": "tool_call_delta", "id": call_id, "delta": json.dumps(args)},
        {"kind": "tool_call_end", "id": call_id},
    ]


def done(stop: str, usage: dict[str, int] = SMALL_USAGE) -> Response:
    return [{"kind": "done", "stop": stop, "usage": usage}]


def message_text(messages: list[dict[str, Any]]) -> str:
    texts: list[str] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        for block in message.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(str(block.get("text", "")))
    return "\n".join(texts)


def typed_outcome(_request: dict[str, Any], _options: dict[str, str]) -> Response:
    return call(
        "typed-return",
        "return",
        {"value": {"status": "accepted", "items": 2}},
    ) + done("tool")


def blocked_outcome(_request: dict[str, Any], _options: dict[str, str]) -> Response:
    return call(
        "blocked-return",
        "block",
        {"code": "missing-capability", "message": "The evaluation task lacks a required grant."},
    ) + done("tool")


def exhausted_outcome(_request: dict[str, Any], options: dict[str, str]) -> Response:
    return call("exhausting-read", "read", {"path": options["file"]}) + done("tool")


def failed_outcome(_request: dict[str, Any], _options: dict[str, str]) -> Response:
    return [{"kind": "error", "message": "deterministic response failure", "retryable": False}]


def permissions(request: dict[str, Any], options: dict[str, str]) -> Response:
    messages = request["messages"]
    if not any(message.get("role") == "tool" for message in messages):
        return (
            call("allowed-built-in-read", "read", {"path": options["allowed_file"]})
            + call("denied-built-in-read", "read", {"path": options["denied_file"]})
            + done("tool")
        )
    return [{"kind": "text", "delta": "The capability probe completed."}] + done("end")


def workflow(_request: dict[str, Any], _options: dict[str, str]) -> Response:
    return call(
        "workflow-return",
        "return",
        {"value": {"branch": "apply", "plan": "record the verified workflow result"}},
    ) + done("tool")


def compaction(request: dict[str, Any], options: dict[str, str]) -> Response:
    request_id = request["request_id"]
    if request_id.startswith("cmp_"):
        return [
            {
                "kind": "text",
                "delta": (
                    "## Goal\nRead two fixture files.\n\n"
                    "## Progress\nBoth fixture files were read.\n\n"
                    "## Decisions\nUse their recorded contents.\n\n"
                    "## Open items\nReport completion.\n\n"
                    "## Next step\nFinish the task."
                ),
            }
        ] + done("end", {"input": 200, "output": 50, "cache_read": 0})
    messages = request["messages"]
    if "## Continuation state" in message_text(messages):
        return [{"kind": "text", "delta": "Both fixture files were read after compaction."}] + done(
            "end", SMALL_USAGE
        )
    results = [message for message in messages if message.get("role") == "tool"]
    option = "file_a" if not results else "file_b"
    return call(f"read-{option}", "read", {"path": options[option]}) + done(
        "tool", ORDINARY_USAGE
    )


def sandbox(request: dict[str, Any], options: dict[str, str]) -> Response:
    messages = request["messages"]
    if not any(message.get("role") == "tool" for message in messages):
        return (
            call("allowed-read", "cat", {"args": [options["allowed_file"]]})
            + call("denied-read", "cat", {"args": [options["denied_file"]]})
            + done("tool")
        )
    return [{"kind": "text", "delta": "The permissions probe completed."}] + done("end")


def respond(
    request: dict[str, Any], *, scenario: str, options: dict[str, str] | None = None
) -> Response:
    handlers = {
        "typed-outcome": typed_outcome,
        "blocked-outcome": blocked_outcome,
        "exhausted-outcome": exhausted_outcome,
        "failed-outcome": failed_outcome,
        "declared-permissions": permissions,
        "workflow-provenance": workflow,
        "context-compaction": compaction,
        "kernel-sandbox": sandbox,
    }
    handler = handlers.get(scenario)
    if handler is None:
        return [
            {
                "kind": "error",
                "message": f"unknown evaluation scenario {scenario}",
                "retryable": False,
            }
        ]
    return handler(request, options or {})
