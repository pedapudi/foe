#!/usr/bin/python3
"""Return every scripted mistake call, followed by a completion response."""

from __future__ import annotations

import json
from typing import Any

USAGE = {"input": 100, "output": 50, "cache_read": 0}


def expand(args: Any) -> Any:
    """Replace {"$bytes": n} argument values with a string of n bytes.

    An oversize-source case needs a value past a tool's byte bound; the
    sentinel keeps the case file readable instead of carrying the bytes.
    """
    if not isinstance(args, dict):
        return args
    return {
        name: "#" * value["$bytes"] if isinstance(value, dict) and set(value) == {"$bytes"} else value
        for name, value in args.items()
    }


def respond(request: dict[str, Any], *, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if any(message.get("role") == "tool" for message in request["messages"]):
        return [
            {"kind": "text", "delta": "The mistake probe completed."},
            {"kind": "done", "stop": "end", "usage": USAGE},
        ]
    chunks = []
    for call in calls:
        chunks.extend(
            [
                {"kind": "tool_call_start", "id": call["id"], "name": call["name"]},
                {
                    "kind": "tool_call_delta",
                    "id": call["id"],
                    "delta": json.dumps(expand(call["args"])),
                },
                {"kind": "tool_call_end", "id": call["id"]},
            ]
        )
    chunks.append({"kind": "done", "stop": "tool", "usage": USAGE})
    return chunks
