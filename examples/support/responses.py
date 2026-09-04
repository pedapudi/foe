#!/usr/bin/python3
"""Deterministic response functions shared by end-to-end examples."""

import json
import re

from response_chunks import call, done, emit


def workflow(request: dict) -> list[dict]:
    """Return the next workflow response selected by tool and message state."""
    chunks: list[dict] = []
    tools = {tool["name"] for tool in request["tools"]}
    if "return" in tools:
        call(
            chunks,
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
        done(chunks, "tool")
        return chunks
    if "edit" in tools and not any(message["role"] == "tool" for message in request["messages"]):
        call(
            chunks,
            "apply",
            "edit",
            {
                "path": "src/calculator.py",
                "edits": [
                    {
                        "old_text": "def add(left: int, right: int) -> int:\n    # TODO: Implement add.\n    raise NotImplementedError\n",
                        "new_text": "def add(left: int, right: int) -> int:\n    return left + right\n",
                    }
                ],
            },
        )
        done(chunks, "tool")
        return chunks
    if "edit" in tools:
        emit(chunks, {"kind": "text", "delta": "Implemented add and removed the TODO comment."})
        done(chunks, "end")
        return chunks
    emit(chunks, {"kind": "error", "message": "workflow demo received an unexpected request", "retryable": False})
    return chunks


def sandbox(request: dict) -> list[dict]:
    """Return two file calls followed by the sandbox result summary."""
    chunks: list[dict] = []
    if not any(message["role"] == "tool" for message in request["messages"]):
        task = request["messages"][0]["content"][0]["text"]
        paths = re.findall(r"/\S+\.txt", task)
        if len(paths) != 2:
            return [{"kind": "error", "message": "sandbox task must name two text files", "retryable": False}]
        call(chunks, "allowed", "cat", {"args": [paths[0]]})
        call(chunks, "denied", "cat", {"args": [paths[1]]})
        done(chunks, "tool")
        return chunks
    emit(
        chunks,
        {
            "kind": "text",
            "delta": "The granted file was readable. The file outside the grant was denied by the kernel.",
        },
    )
    done(chunks, "end")
    return chunks


def read_self_extension_files(chunks: list[dict]) -> None:
    for call_id, path in [
        ("read-tool", "crates/code/src/read.rs"),
        ("read-test", "crates/code/src/read_test.rs"),
        ("read-doc", "docs/tools.md"),
    ]:
        call(chunks, call_id, "read", {"path": path})


def edit_read_tool_and_test(chunks: list[dict]) -> None:
    call(
        chunks,
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
        chunks,
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


def edit_read_tool_docs(chunks: list[dict]) -> None:
    call(
        chunks,
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


def self_extension(request: dict) -> list[dict]:
    """Return the direct self-extension response selected by prior results."""
    chunks: list[dict] = []
    messages = request["messages"]
    tool_results = [message for message in messages if message["role"] == "tool"]
    if not tool_results:
        read_self_extension_files(chunks)
        done(chunks, "tool")
        return chunks
    if not any(message.get("name") == "edit" for message in tool_results):
        edit_read_tool_and_test(chunks)
        edit_read_tool_docs(chunks)
        done(chunks, "tool")
        return chunks
    emit(
        chunks,
        {"kind": "text", "delta": "Added total_bytes to the read result, its regression test, and its specification."},
    )
    done(chunks, "end")
    return chunks


def self_improvement_retry(request: dict) -> list[dict]:
    """Return the workflow response selected by results and verifier findings."""
    chunks: list[dict] = []
    messages = request["messages"]
    tool_results = [message for message in messages if message["role"] == "tool"]
    if not tool_results:
        read_self_extension_files(chunks)
        done(chunks, "tool")
        return chunks
    if not any(message.get("name") == "edit" for message in tool_results):
        edit_read_tool_and_test(chunks)
        done(chunks, "tool")
        return chunks
    if "Verification by `check` reported" in json.dumps(messages) and not any(
        message.get("call_id") == "edit-doc" for message in tool_results
    ):
        edit_read_tool_docs(chunks)
        done(chunks, "tool")
        return chunks
    docs_edited = any(message.get("call_id") == "edit-doc" for message in tool_results)
    call_id = "check-complete" if docs_edited else "check-before-docs"
    call(chunks, call_id, "check", {"args": []})
    done(chunks, "tool")
    return chunks
