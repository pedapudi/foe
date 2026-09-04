#!/usr/bin/python3
"""Deterministic host responses for the parent and its survey children."""

import re
from pathlib import Path

from response_chunks import call, done, error, step, text, tool_names

MODULES = ["src/config.py", "src/client.py"]

EDITS = {
    "src/config.py": [
        {"old_text": '{"timeout": 30, "retries": 3}', "new_text": '{"timeout_seconds": 30, "retries": 3}'},
        {"old_text": 'return settings["timeout"]', "new_text": 'return settings["timeout_seconds"]'},
    ],
    "src/client.py": [
        {"old_text": '"deadline": settings["timeout"]', "new_text": '"deadline": settings["timeout_seconds"]'},
    ],
}


def user_texts(request: dict) -> list[str]:
    """Return the text of every task and inbox message."""
    return [
        block.get("text", "")
        for message in request["messages"]
        if message["role"] == "user"
        for block in message["content"]
    ]


def called(request: dict, name: str) -> bool:
    """Return whether this episode has called the named tool."""
    return any(
        tool_call["name"] == name
        for message in request["messages"]
        if message["role"] == "assistant"
        for tool_call in message["tool_calls"]
    )


def results(request: dict) -> list[str]:
    """Return the rendered results of this episode's tool calls."""
    return [message["rendered"] for message in request["messages"] if message["role"] == "tool"]


def children_ended(request: dict) -> int:
    """Return the number of children whose end message has arrived."""
    return sum(1 for item in user_texts(request) if " ended: " in item)


def parent(request: dict) -> list[dict]:
    """Return the parent response selected by the episode state."""
    chunks: list[dict] = []
    if step(request) == 0:
        for module in MODULES:
            name = Path(module).stem
            task = f"Report every place {module} reads the configuration key `timeout`."
            call(chunks, f"tc_spawn_{name}", "spawn", {"contract": "survey", "name": f"{name}-survey", "task": task})
        done(chunks, "tool")
        return chunks
    if not called(request, "wait"):
        call(chunks, "tc_wait", "wait", {})
        done(chunks, "tool")
        return chunks
    if children_ended(request) < len(MODULES):
        error(chunks, "wait returned before every child had ended")
        return chunks
    if not called(request, "edit"):
        for module, edits in EDITS.items():
            call(chunks, f"tc_edit_{Path(module).stem}", "edit", {"path": module, "edits": edits})
        done(chunks, "tool")
        return chunks
    text(chunks, "Both surveys reported. The key is now `timeout_seconds` in src/config.py and src/client.py.")
    done(chunks, "end")
    return chunks


def survey(request: dict) -> list[dict]:
    """Return a survey child's next search, report, or completion."""
    chunks: list[dict] = []
    task = user_texts(request)[0]
    found = re.search(r"src/\S+\.py", task)
    if not found:
        error(chunks, f"the survey task names no module: {task}")
        return chunks
    module = found.group(0)
    if step(request) == 0:
        call(chunks, "tc_grep", "grep", {"pattern": "timeout", "path": module})
        done(chunks, "tool")
        return chunks
    if not called(request, "notify"):
        lines = [line for line in results(request)[0].splitlines() if line.startswith(module)]
        call(chunks, "tc_notify", "notify", {"content": f"survey of {module}: " + "; ".join(lines)})
        done(chunks, "tool")
        return chunks
    text(chunks, f"Reported every line of {module} that reads the key.")
    done(chunks, "end")
    return chunks


def respond(request: dict) -> list[dict]:
    """Select the response by the tools offered to the episode."""
    if "spawn" in tool_names(request):
        return parent(request)
    return survey(request)
