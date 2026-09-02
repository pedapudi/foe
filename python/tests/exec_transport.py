#!/usr/bin/python3
"""A model transport for the `exec` provider, with fixed responses.

docs/models.md specifies the seam: the command receives the model name as
its single argument, reads one request object from standard input, and
writes `model/chunk` lines to standard output. The name selects the
responses, so one command serves every test that needs a model without a
credential.

`host-tool-then-text` calls the host tool `reference_count` on the first
request and answers with a sentence once a tool result is in the messages.
"""

import json
import sys

USAGE = {"input": 0, "output": 0, "cache_read": 0}
SUMMARY = "Done: 3 references."


def emit(request_id: str, chunk: dict) -> None:
    print(json.dumps({"type": "model/chunk", "request_id": request_id, "chunk": chunk}))


def host_tool_then_text(request: dict) -> None:
    request_id = request["request_id"]
    if any(message["role"] == "tool" for message in request["messages"]):
        emit(request_id, {"kind": "text", "delta": SUMMARY})
        emit(request_id, {"kind": "done", "stop": "end", "usage": USAGE})
        return
    args = json.dumps({"symbol": "add"})
    emit(request_id, {"kind": "tool_call_start", "id": "tc_1", "name": "reference_count"})
    emit(request_id, {"kind": "tool_call_delta", "id": "tc_1", "delta": args})
    emit(request_id, {"kind": "tool_call_end", "id": "tc_1"})
    emit(request_id, {"kind": "done", "stop": "tool", "usage": USAGE})


def main(name: str) -> None:
    request = json.loads(sys.stdin.readline())
    if name == "host-tool-then-text":
        host_tool_then_text(request)
        return
    emit(request["request_id"], {"kind": "error", "message": f"unknown responses {name}", "retryable": False})


if __name__ == "__main__":
    main(sys.argv[1])
