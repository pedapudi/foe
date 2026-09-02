#!/usr/bin/python3
"""Submit the scripted mistake calls the case file names, one turn of all calls.

The runner writes the calls to a JSON file and names it in the `calls` key of
the model block, which the exec transport passes through as an option. The
first response issues every call; the second ends the episode with text, so
the run completes and the log carries one typed result per mistake.
"""

from __future__ import annotations

import json
import sys
from typing import Any

USAGE = {"input": 100, "output": 50, "cache_read": 0}


def emit(request_id: str, chunk: dict[str, Any]) -> None:
    response = {"type": "model/chunk", "request_id": request_id, "chunk": chunk}
    print(json.dumps(response, separators=(",", ":")))


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


def main() -> None:
    request = json.loads(sys.stdin.readline())
    request_id = request["request_id"]
    if any(message.get("role") == "tool" for message in request["messages"]):
        emit(request_id, {"kind": "text", "delta": "The mistake probe completed."})
        emit(request_id, {"kind": "done", "stop": "end", "usage": USAGE})
        return
    with open(request["options"]["calls"], encoding="utf-8") as handle:
        calls = json.load(handle)
    for call in calls:
        emit(request_id, {"kind": "tool_call_start", "id": call["id"], "name": call["name"]})
        emit(request_id, {"kind": "tool_call_delta", "id": call["id"], "delta": json.dumps(expand(call["args"]))})
        emit(request_id, {"kind": "tool_call_end", "id": call["id"]})
    emit(request_id, {"kind": "done", "stop": "tool", "usage": USAGE})


if __name__ == "__main__":
    main()
