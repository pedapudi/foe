#!/usr/bin/python3
"""Return deterministic task responses for configuration-repair fixtures."""

import json
import re
from typing import Any


def call(call_id: str, name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"kind": "tool_call_start", "id": call_id, "name": name},
        {"kind": "tool_call_delta", "id": call_id, "delta": json.dumps(args)},
        {"kind": "tool_call_end", "id": call_id},
    ]


def done(stop: str) -> list[dict[str, Any]]:
    return [
        {"kind": "done", "stop": stop, "usage": {"input": 40, "output": 20, "cache_read": 0}}
    ]


def respond(request: dict[str, Any], *, command: str) -> list[dict[str, Any]]:
    """Run the named command, then report its result without inspecting grants."""
    tool_messages = [m for m in request["messages"] if m.get("role") == "tool"]
    if not tool_messages:
        return call("run-build", "bash", {"command": command}) + done("tool")
    serialized = json.dumps(tool_messages)
    if re.search(r'"exit_code":\s*0\b', serialized) or re.search(r"\bexit 0\b", serialized):
        return [
            {"kind": "text", "delta": "The build command completed and wrote the artifact."}
        ] + done("end")
    return call(
        "report-blocked",
        "block",
        {
            "code": "missing-capability",
            "message": "The build command cannot execute under the contract's permissions.",
        },
    ) + done("tool")
