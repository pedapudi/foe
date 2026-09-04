#!/usr/bin/python3
"""Return a prepared candidate contract as the repair-proposal answer."""

import json
from typing import Any


def respond(_request: dict[str, Any], *, candidate: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"kind": "tool_call_start", "id": "return-candidate", "name": "return"},
        {
            "kind": "tool_call_delta",
            "id": "return-candidate",
            "delta": json.dumps({"value": candidate}),
        },
        {"kind": "tool_call_end", "id": "return-candidate"},
        {
            "kind": "done",
            "stop": "tool",
            "usage": {"input": 40, "output": 20, "cache_read": 0},
        },
    ]
