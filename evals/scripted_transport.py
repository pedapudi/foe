#!/usr/bin/python3
"""Return deterministic model responses for the runtime evaluation cases."""

from __future__ import annotations

import json
import sys
from typing import Any


ORDINARY_USAGE = {"input": 1400, "output": 200, "cache_read": 0}
SMALL_USAGE = {"input": 20, "output": 10, "cache_read": 0}


def emit(request_id: str, chunk: dict[str, Any]) -> None:
    response = {"type": "model/chunk", "request_id": request_id, "chunk": chunk}
    print(json.dumps(response, separators=(",", ":")))


def call(request_id: str, call_id: str, name: str, args: dict[str, Any]) -> None:
    emit(request_id, {"kind": "tool_call_start", "id": call_id, "name": name})
    emit(request_id, {"kind": "tool_call_delta", "id": call_id, "delta": json.dumps(args)})
    emit(request_id, {"kind": "tool_call_end", "id": call_id})


def done(request_id: str, stop: str, usage: dict[str, int] = SMALL_USAGE) -> None:
    emit(request_id, {"kind": "done", "stop": stop, "usage": usage})


def message_text(messages: list[dict[str, Any]]) -> str:
    texts: list[str] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        for block in message.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(str(block.get("text", "")))
    return "\n".join(texts)


def typed_outcome(request: dict[str, Any]) -> None:
    request_id = request["request_id"]
    call(
        request_id,
        "typed-return",
        "return",
        {"value": {"status": "accepted", "items": 2}},
    )
    done(request_id, "tool")


def blocked_outcome(request: dict[str, Any]) -> None:
    request_id = request["request_id"]
    call(
        request_id,
        "blocked-return",
        "block",
        {"code": "missing-capability", "message": "The evaluation task lacks a required grant."},
    )
    done(request_id, "tool")


def exhausted_outcome(request: dict[str, Any]) -> None:
    request_id = request["request_id"]
    call(request_id, "exhausting-read", "read", {"path": request["options"]["file"]})
    done(request_id, "tool")


def failed_outcome(request: dict[str, Any]) -> None:
    emit(
        request["request_id"],
        {"kind": "error", "message": "deterministic transport failure", "retryable": False},
    )


def authority(request: dict[str, Any]) -> None:
    request_id = request["request_id"]
    messages = request["messages"]
    if not any(message.get("role") == "tool" for message in messages):
        call(request_id, "allowed-built-in-read", "read", {"path": request["options"]["allowed_file"]})
        call(request_id, "denied-built-in-read", "read", {"path": request["options"]["denied_file"]})
        done(request_id, "tool")
        return
    emit(request_id, {"kind": "text", "delta": "The capability probe completed."})
    done(request_id, "end")


def workflow(request: dict[str, Any]) -> None:
    request_id = request["request_id"]
    call(
        request_id,
        "workflow-return",
        "return",
        {"value": {"branch": "apply", "plan": "record the verified workflow result"}},
    )
    done(request_id, "tool")


def compaction(request: dict[str, Any]) -> None:
    request_id = request["request_id"]
    if request_id.startswith("cmp_"):
        emit(
            request_id,
            {
                "kind": "text",
                "delta": (
                    "## Goal\nRead two fixture files.\n\n"
                    "## Progress\nBoth fixture files were read.\n\n"
                    "## Decisions\nUse their recorded contents.\n\n"
                    "## Open items\nReport completion.\n\n"
                    "## Next step\nFinish the task."
                ),
            },
        )
        done(request_id, "end", {"input": 200, "output": 50, "cache_read": 0})
        return
    messages = request["messages"]
    if "## Continuation state" in message_text(messages):
        emit(request_id, {"kind": "text", "delta": "Both fixture files were read after compaction."})
        done(request_id, "end", SMALL_USAGE)
        return
    results = [message for message in messages if message.get("role") == "tool"]
    option = "file_a" if not results else "file_b"
    call(request_id, f"read-{option}", "read", {"path": request["options"][option]})
    done(request_id, "tool", ORDINARY_USAGE)


def sandbox(request: dict[str, Any]) -> None:
    request_id = request["request_id"]
    messages = request["messages"]
    if not any(message.get("role") == "tool" for message in messages):
        call(request_id, "allowed-read", "cat", {"args": [request["options"]["allowed_file"]]})
        call(request_id, "denied-read", "cat", {"args": [request["options"]["denied_file"]]})
        done(request_id, "tool")
        return
    emit(request_id, {"kind": "text", "delta": "The authority probe completed."})
    done(request_id, "end")


def main() -> None:
    request = json.loads(sys.stdin.readline())
    scenario = request["model"]
    handlers = {
        "eval-typed-outcome": typed_outcome,
        "eval-blocked-outcome": blocked_outcome,
        "eval-exhausted-outcome": exhausted_outcome,
        "eval-failed-outcome": failed_outcome,
        "eval-authority": authority,
        "eval-workflow": workflow,
        "eval-compaction": compaction,
        "eval-sandbox": sandbox,
    }
    handler = handlers.get(scenario)
    if handler is None:
        emit(
            request["request_id"],
            {"kind": "error", "message": f"unknown evaluation scenario {scenario}", "retryable": False},
        )
        return
    handler(request)


if __name__ == "__main__":
    main()
