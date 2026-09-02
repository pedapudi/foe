#!/usr/bin/python3
"""The exec transport this example runs: fixed chunks in place of a provider.

The `exec` provider starts this transport executable once per model request
with the model name as its single argument, writes one `model/request` line
to its standard input, and reads `model/chunk` lines from its standard
output. `docs/models.md` specifies that exchange.

The transport answers three requests: a `read` call on the file named by
the `readme` option, then a `record_finding` call, then one sentence of
assistant text. `read` is a built-in tool the episode runs inside its
sandbox; `record_finding` is a host tool the Python application runs in its
own process.

This file imports the standard library alone, so it needs no copy inside a
read root and the configuration names it where it lies.
`examples/support/README.md` states when a transport script does need one.
"""

import json
import sys

USAGE = {"input": 0, "output": 0, "cache_read": 0}
SUMMARY = "Recorded one finding against the calculator project."


def emit(request_id: str, chunk: dict) -> None:
    print(json.dumps({"type": "model/chunk", "request_id": request_id, "chunk": chunk}))


def call(request_id: str, call_id: str, name: str, args: dict) -> None:
    emit(request_id, {"kind": "tool_call_start", "id": call_id, "name": name})
    emit(request_id, {"kind": "tool_call_delta", "id": call_id, "delta": json.dumps(args)})
    emit(request_id, {"kind": "tool_call_end", "id": call_id})
    emit(request_id, {"kind": "done", "stop": "tool", "usage": USAGE})


def main() -> None:
    request = json.loads(sys.stdin.readline())
    request_id = request["request_id"]
    answered = [message["name"] for message in request["messages"] if message["role"] == "tool"]
    if not answered:
        call(request_id, "read-readme", "read", {"path": request["options"]["readme"]})
    elif "record_finding" not in answered:
        finding = {"component": "calculator", "summary": "The README states no supported Python version."}
        call(request_id, "record", "record_finding", finding)
    else:
        emit(request_id, {"kind": "text", "delta": SUMMARY})
        emit(request_id, {"kind": "done", "stop": "end", "usage": USAGE})


if __name__ == "__main__":
    main()
