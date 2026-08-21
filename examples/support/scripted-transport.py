#!/usr/bin/python3
"""Return deterministic model chunks for the workflow and sandbox demos."""

import json
import sys


USAGE = {"input": 0, "output": 0, "cache_read": 0}


def emit(request_id: str, chunk: dict) -> None:
    print(json.dumps({"type": "model/chunk", "request_id": request_id, "chunk": chunk}))


def call(request_id: str, call_id: str, name: str, args: dict) -> None:
    emit(request_id, {"kind": "tool_call_start", "id": call_id, "name": name})
    emit(request_id, {"kind": "tool_call_delta", "id": call_id, "delta": json.dumps(args)})
    emit(request_id, {"kind": "tool_call_end", "id": call_id})


def done(request_id: str, stop: str) -> None:
    emit(request_id, {"kind": "done", "stop": stop, "usage": USAGE})


def workflow(request: dict) -> None:
    request_id = request["request_id"]
    options = request["options"]
    tools = {tool["name"] for tool in request["tools"]}
    if "return" in tools:
        call(
            request_id,
            "plan",
            "return",
            {
                "value": {
                    "file": "src/calculator.py",
                    "todo": "Implement add.",
                    "change": "Return the sum of the two arguments.",
                    "branch": "apply",
                }
            },
        )
        done(request_id, "tool")
        return
    if "edit" in tools and not any(message["role"] == "tool" for message in request["messages"]):
        call(
            request_id,
            "apply",
            "edit",
            {
                "path": f'{options["project_dir"]}/src/calculator.py',
                "edits": [
                    {
                        "old_text": "def add(left: int, right: int) -> int:\n    # TODO: Implement add.\n    raise NotImplementedError\n",
                        "new_text": "def add(left: int, right: int) -> int:\n    return left + right\n",
                    }
                ],
            },
        )
        done(request_id, "tool")
        return
    if "edit" in tools:
        emit(request_id, {"kind": "text", "delta": "Implemented add and removed the TODO comment."})
        done(request_id, "end")
        return
    emit(request_id, {"kind": "error", "message": "workflow demo received an unexpected request", "retryable": False})


def sandbox(request: dict) -> None:
    request_id = request["request_id"]
    options = request["options"]
    if not any(message["role"] == "tool" for message in request["messages"]):
        call(request_id, "allowed", "cat", {"args": [options["allowed_file"]]})
        call(request_id, "denied", "cat", {"args": [options["denied_file"]]})
        done(request_id, "tool")
        return
    emit(
        request_id,
        {
            "kind": "text",
            "delta": "The granted file was readable. The file outside the grant was denied by the kernel.",
        },
    )
    done(request_id, "end")


def main() -> None:
    request = json.loads(sys.stdin.readline())
    scenario = request["model"]
    if scenario == "workflow-demo":
        workflow(request)
    elif scenario == "sandbox-demo":
        sandbox(request)
    else:
        emit(
            request["request_id"],
            {"kind": "error", "message": f"unknown demo scenario {scenario}", "retryable": False},
        )


if __name__ == "__main__":
    main()
