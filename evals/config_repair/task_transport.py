#!/usr/bin/python3
"""Deterministic task driver for the configuration-repair fixtures.

One invocation answers one model request. The first turn runs the build
command the contract's model block carries with the bash tool. The next
turn reports completion when that command exited zero and blocks on
missing-capability otherwise. The driver holds no repair knowledge:
its behavior never depends on the contract's grants, so a baseline run
and a candidate rerun differ only in what the runtime permits.
"""

import json
import re
import sys


def main() -> None:
    request = json.loads(sys.stdin.readline())
    request_id = request["request_id"]

    def emit(chunk: dict) -> None:
        print(
            json.dumps(
                {"type": "model/chunk", "request_id": request_id, "chunk": chunk},
                separators=(",", ":"),
            )
        )

    def call(call_id: str, name: str, args: dict) -> None:
        emit({"kind": "tool_call_start", "id": call_id, "name": name})
        emit({"kind": "tool_call_delta", "id": call_id, "delta": json.dumps(args)})
        emit({"kind": "tool_call_end", "id": call_id})

    def done(stop: str) -> None:
        emit({"kind": "done", "stop": stop, "usage": {"input": 40, "output": 20, "cache_read": 0}})

    tool_messages = [m for m in request["messages"] if m.get("role") == "tool"]
    if not tool_messages:
        call("run-build", "bash", {"command": request["options"]["command"]})
        done("tool")
        return
    serialized = json.dumps(tool_messages)
    if re.search(r'"exit_code":\s*0\b', serialized) or re.search(r"\bexit 0\b", serialized):
        emit({"kind": "text", "delta": "The build command completed and wrote the artifact."})
        done("end")
        return
    call(
        "report-blocked",
        "block",
        {
            "code": "missing-capability",
            "message": "The build command cannot execute under the contract's permissions.",
        },
    )
    done("tool")


if __name__ == "__main__":
    main()
