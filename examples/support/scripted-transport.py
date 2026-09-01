#!/usr/bin/python3
"""Return deterministic model chunks for the end-to-end demos."""

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


def read_self_extension_files(request_id: str) -> None:
    for call_id, path in [
        ("read-tool", "crates/code/src/read.rs"),
        ("read-test", "crates/code/src/read_test.rs"),
        ("read-doc", "docs/tools.md"),
    ]:
        call(request_id, call_id, "read", {"path": path})


def edit_read_tool_and_test(request_id: str) -> None:
    call(
        request_id,
        "edit-tool",
        "edit",
        {
            "path": "crates/code/src/read.rs",
            "edits": [
                {
                    "old_text": "        let total = s.total;\n",
                    "new_text": ("        let total = s.total;\n        let total_bytes = s.bytes;\n"),
                },
                {
                    "old_text": '                "total_lines": total,\n                "shown": shown_n,\n',
                    "new_text": (
                        '                "total_lines": total,\n'
                        '                "total_bytes": total_bytes,\n'
                        '                "shown": shown_n,\n'
                    ),
                },
            ],
        },
    )
    call(
        request_id,
        "edit-test",
        "edit",
        {
            "path": "crates/code/src/read_test.rs",
            "edits": [
                {
                    "old_text": '    assert_eq!(v.value["total_lines"], 3);\n',
                    "new_text": (
                        '    assert_eq!(v.value["total_bytes"], 14);\n'
                        '    assert_eq!(v.value["total_lines"], 3);\n'
                    ),
                }
            ],
        },
    )


def edit_read_tool_docs(request_id: str) -> None:
    call(
        request_id,
        "edit-doc",
        "edit",
        {
            "path": "docs/tools.md",
            "edits": [
                {
                    "old_text": (
                        "| `path`, `offset`, `shown`, `truncated`; a file adds `total_lines`, `content`, `version`; "
                        "a directory adds `total_entries`, `entries` with `path` and `type` |"
                    ),
                    "new_text": (
                        "| `path`, `offset`, `shown`, `truncated`; a file adds `total_lines`, `total_bytes`, "
                        "`content`, `version`; a directory adds `total_entries`, `entries` with `path` and `type` |"
                    ),
                },
                {
                    "old_text": (
                        "caps the window below the line limit; `truncated` is true whenever lines\n"
                        "remain beyond the window, whatever the cause.\n"
                    ),
                    "new_text": (
                        "caps the window below the line limit; `truncated` is true whenever lines\n"
                        "remain beyond the window, whatever the cause. The canonical `total_bytes`\n"
                        "field is the complete source file's byte count, independent of the displayed\n"
                        "window and before line-ending normalization.\n"
                    ),
                },
            ],
        },
    )


def self_extension(request: dict) -> None:
    request_id = request["request_id"]
    messages = request["messages"]
    tool_results = [message for message in messages if message["role"] == "tool"]
    if not tool_results:
        read_self_extension_files(request_id)
        done(request_id, "tool")
        return
    if not any(message.get("name") == "edit" for message in tool_results):
        edit_read_tool_and_test(request_id)
        edit_read_tool_docs(request_id)
        done(request_id, "tool")
        return
    emit(
        request_id,
        {"kind": "text", "delta": "Added total_bytes to the read result, its regression test, and its specification."},
    )
    done(request_id, "end")


def self_improvement_retry(request: dict) -> None:
    request_id = request["request_id"]
    messages = request["messages"]
    tool_results = [message for message in messages if message["role"] == "tool"]
    if not tool_results:
        read_self_extension_files(request_id)
        done(request_id, "tool")
        return
    if not any(message.get("name") == "edit" for message in tool_results):
        edit_read_tool_and_test(request_id)
        done(request_id, "tool")
        return
    if "Verification by `check` reported" in json.dumps(messages) and not any(
        message.get("call_id") == "edit-doc" for message in tool_results
    ):
        edit_read_tool_docs(request_id)
        done(request_id, "tool")
        return
    docs_edited = any(message.get("call_id") == "edit-doc" for message in tool_results)
    call_id = "check-complete" if docs_edited else "check-before-docs"
    call(request_id, call_id, "check", {"args": []})
    done(request_id, "tool")


def main() -> None:
    request = json.loads(sys.stdin.readline())
    scenario = request["model"]
    if scenario == "workflow-demo":
        workflow(request)
    elif scenario == "sandbox-demo":
        sandbox(request)
    elif scenario == "self-extension-demo":
        self_extension(request)
    elif scenario == "self-improvement-retry-demo":
        self_improvement_retry(request)
    else:
        emit(
            request["request_id"],
            {"kind": "error", "message": f"unknown demo scenario {scenario}", "retryable": False},
        )


if __name__ == "__main__":
    main()
