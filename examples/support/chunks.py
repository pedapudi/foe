#!/usr/bin/python3
"""Model chunks for an example's transport script.

An example that runs without a provider names the `exec` provider and points
it at a script in its own directory. The script reads one request as JSON on
standard input and writes chunk lines to standard output; `docs/models.md`
gives the wire shape. These helpers write those lines, so each example's
script holds only the responses that example demonstrates.

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "support"))
    from chunks import call, done, emit, read_request

    request = read_request()
    call(request, "tc_1", "read", {"path": "/tmp/project/README.md"})
    done(request, "tool")
"""

import json
import sys

USAGE = {"input": 0, "output": 0, "cache_read": 0}


def read_request() -> dict:
    """The one request the runtime writes to this process."""
    return json.loads(sys.stdin.readline())


def emit(request: dict, chunk: dict) -> None:
    """Writes one chunk of the answer to the given request."""
    print(json.dumps({"type": "model/chunk", "request_id": request["request_id"], "chunk": chunk}))


def text(request: dict, delta: str) -> None:
    """Writes assistant text."""
    emit(request, {"kind": "text", "delta": delta})


def call(request: dict, call_id: str, name: str, args: dict) -> None:
    """Writes one complete tool call."""
    emit(request, {"kind": "tool_call_start", "id": call_id, "name": name})
    emit(request, {"kind": "tool_call_delta", "id": call_id, "delta": json.dumps(args)})
    emit(request, {"kind": "tool_call_end", "id": call_id})


def done(request: dict, stop: str, usage: dict | None = None) -> None:
    """Ends the answer. `stop` is `tool` when tool calls precede it, else `end`."""
    emit(request, {"kind": "done", "stop": stop, "usage": usage or USAGE})


def error(request: dict, message: str, retryable: bool = False) -> None:
    """Ends the answer with a provider error, which the runtime may retry."""
    emit(request, {"kind": "error", "message": message, "retryable": retryable})


def step(request: dict) -> int:
    """How many answers this episode has already received, counting from zero."""
    return sum(1 for message in request["messages"] if message["role"] == "assistant")


def tool_names(request: dict) -> set:
    """The names of the tools offered to this request."""
    return {tool["name"] for tool in request["tools"]}
