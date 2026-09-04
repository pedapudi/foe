#!/usr/bin/python3
"""Deterministic host responses for a lead, reviewer, and tester."""

import time

from response_chunks import call, done, error, step, text, tool_names

QUESTION = "Which checks cover src/cli.py?"
ANSWER = "tests/check.py covers src/cli.py with three cases, one of them the dry run."
WAIT_SECONDS = 0.05
WAIT_LIMIT = 60
WAITING = [
    ("read", {"path": "src/cli.py"}),
    ("grep", {"pattern": "dry_run", "path": "src"}),
    ("grep", {"pattern": "def ", "path": "src/cli.py"}),
]

DRY_RUN_EDITS = [
    {
        "old_text": 'def main(argv):\n    if len(argv) != 2:\n        print("usage: cli.py <pack|clean> <target>")',
        "new_text": 'def main(argv):\n    dry_run = "--dry-run" in argv\n    argv = [arg for arg in argv if arg != "--dry-run"]\n    if len(argv) != 2:\n        print("usage: cli.py [--dry-run] <pack|clean> <target>")',
    },
    {
        "old_text": "    print(perform(action, target))",
        "new_text": '    if dry_run:\n        print(f"would {action} {target}")\n        return 0\n    print(perform(action, target))',
    },
]


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


def received(request: dict, fragment: str) -> bool:
    """Return whether an inbox message contains the fragment."""
    return any(fragment in item for item in user_texts(request))


def wait_for_message(request: dict, chunks: list[dict], waiting_for: str) -> None:
    """Append one read-only call while waiting for a peer message."""
    turn = step(request)
    if turn > WAIT_LIMIT:
        error(chunks, f"{waiting_for} did not arrive within {WAIT_LIMIT} steps")
        return
    time.sleep(WAIT_SECONDS)
    name, args = WAITING[turn % len(WAITING)]
    call(chunks, f"tc_wait_{turn}", name, args)
    done(chunks, "tool")


def lead(request: dict) -> list[dict]:
    """Return the lead's next edit, delegation, wait, or completion."""
    chunks: list[dict] = []
    if step(request) == 0:
        call(chunks, "tc_edit", "edit", {"path": "src/cli.py", "edits": DRY_RUN_EDITS})
        done(chunks, "tool")
        return chunks
    if not called(request, "spawn"):
        call(
            chunks,
            "tc_spawn_reviewer",
            "spawn",
            {"contract": "reviewer", "task": "Review the change to src/cli.py, which adds a --dry-run flag."},
        )
        call(
            chunks,
            "tc_spawn_tester",
            "spawn",
            {"contract": "tester", "task": "Run `python3 -B tests/check.py` and report the result."},
        )
        done(chunks, "tool")
        return chunks
    if not called(request, "wait"):
        call(chunks, "tc_wait", "wait", {})
        done(chunks, "tool")
        return chunks
    if sum(1 for item in user_texts(request) if " ended: " in item) < 2:
        error(chunks, "wait returned before both members had ended")
        return chunks
    text(chunks, "The reviewer and the tester both reported. src/cli.py now takes --dry-run.")
    done(chunks, "end")
    return chunks


def reviewer(request: dict) -> list[dict]:
    """Return the reviewer's next read, message, report, or completion."""
    chunks: list[dict] = []
    if step(request) == 0:
        call(chunks, "tc_read", "read", {"path": "src/cli.py"})
        done(chunks, "tool")
        return chunks
    if not called(request, "send"):
        call(chunks, "tc_send", "send", {"to": "tester", "content": QUESTION})
        done(chunks, "tool")
        return chunks
    if not received(request, "tests/check.py covers"):
        wait_for_message(request, chunks, "the tester's answer")
        return chunks
    if not called(request, "notify"):
        finding = "1. --dry-run returns before perform() runs, and the tester confirms a case covers it."
        call(chunks, "tc_notify", "notify", {"content": f"review of src/cli.py: {finding}"})
        done(chunks, "tool")
        return chunks
    text(chunks, "Reported one finding to the lead.")
    done(chunks, "end")
    return chunks


def tester(request: dict) -> list[dict]:
    """Return the tester's next check, message, report, or completion."""
    chunks: list[dict] = []
    if step(request) == 0:
        call(chunks, "tc_bash", "bash", {"command": "python3 -B tests/check.py"})
        done(chunks, "tool")
        return chunks
    if not received(request, QUESTION):
        wait_for_message(request, chunks, "the reviewer's question")
        return chunks
    if not called(request, "send"):
        call(chunks, "tc_send", "send", {"to": "reviewer", "content": ANSWER})
        done(chunks, "tool")
        return chunks
    if not called(request, "notify"):
        verdict = "passed" if "checks passed" in results(request)[0] else "failed"
        call(chunks, "tc_notify", "notify", {"content": f"tests {verdict}: python3 -B tests/check.py"})
        done(chunks, "tool")
        return chunks
    text(chunks, "Ran the checks and reported the result to the lead.")
    done(chunks, "end")
    return chunks


def respond(request: dict) -> list[dict]:
    """Select a team role by the tools offered to the episode."""
    tools = tool_names(request)
    if "spawn" in tools:
        return lead(request)
    if "bash" in tools:
        return tester(request)
    return reviewer(request)
