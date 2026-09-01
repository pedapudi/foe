#!/usr/bin/python3
"""Model chunks for the team demo: a lead and its two members.

The lead and both members run their own episode, and each episode calls
this contract once per model request. The request says which episode is
asking, because each contract is offered a different set of tools: the lead
holds `spawn`, the tester holds `bash`, and the reviewer holds neither.

The lead waits for both members with one `wait` call, which returns when
every child it started has ended. The members wait for each other's
messages, which no tool covers, so each spends model calls on a rotation of
read-only calls until the message arrives; the rotation keeps any one call
from repeating in three consecutive steps, which would end the episode as
looping.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd().parent / "support"))
from chunks import call, done, error, read_request, step, text, tool_names

QUESTION = "Which checks cover src/cli.py?"
ANSWER = "tests/check.py covers src/cli.py with three cases, one of them the dry run."

# One waiting step is one model call and one process. The episodes this one
# waits for answer within a fraction of a second; the limit bounds a run
# whose messages never arrive, which fails with the message below.
WAIT_SECONDS = 0.05
WAIT_LIMIT = 60
WAITING = [
    ("read", {"path": "src/cli.py"}),
    ("grep", {"pattern": "dry_run", "path": "src"}),
    ("grep", {"pattern": "def ", "path": "src/cli.py"}),
]

# The flag the lead adds to the command line, as two edits of src/cli.py.
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


def user_texts(request: dict) -> list:
    """The text of every user message: the task and every inbox item."""
    return [
        block.get("text", "")
        for message in request["messages"]
        if message["role"] == "user"
        for block in message["content"]
    ]


def called(request: dict, name: str) -> bool:
    """Whether this episode has already called the named tool."""
    return any(
        tool_call["name"] == name
        for message in request["messages"]
        if message["role"] == "assistant"
        for tool_call in message["tool_calls"]
    )


def results(request: dict) -> list:
    """The rendered result of every tool call this episode has made."""
    return [message["rendered"] for message in request["messages"] if message["role"] == "tool"]


def received(request: dict, fragment: str) -> bool:
    """Whether a message carrying the fragment has reached this episode."""
    return any(fragment in item for item in user_texts(request))


def wait_for_message(request: dict, waiting_for: str) -> None:
    """One step of waiting for a peer message: a read-only call and a pause."""
    n = step(request)
    if n > WAIT_LIMIT:
        error(request, f"{waiting_for} did not arrive within {WAIT_LIMIT} steps")
        return
    time.sleep(WAIT_SECONDS)
    name, args = WAITING[n % len(WAITING)]
    call(request, f"tc_wait_{n}", name, args)
    done(request, "tool")


def lead(request: dict) -> None:
    """Edit the file, spawn both members, wait for both to end."""
    if step(request) == 0:
        call(request, "tc_edit", "edit", {"path": "src/cli.py", "edits": DRY_RUN_EDITS})
        done(request, "tool")
        return
    if not called(request, "spawn"):
        call(
            request,
            "tc_spawn_reviewer",
            "spawn",
            {"contract": "reviewer", "task": "Review the change to src/cli.py, which adds a --dry-run flag."},
        )
        call(
            request,
            "tc_spawn_tester",
            "spawn",
            {"contract": "tester", "task": "Run `python3 -B tests/check.py` and report the result."},
        )
        done(request, "tool")
        return
    if not called(request, "wait"):
        call(request, "tc_wait", "wait", {})
        done(request, "tool")
        return
    if sum(1 for item in user_texts(request) if " ended: " in item) < 2:
        error(request, "wait returned before both members had ended")
        return
    text(request, "The reviewer and the tester both reported. src/cli.py now takes --dry-run.")
    done(request, "end")


def reviewer(request: dict) -> None:
    """Read the change, ask the tester what covers it, report to the lead."""
    if step(request) == 0:
        call(request, "tc_read", "read", {"path": "src/cli.py"})
        done(request, "tool")
        return
    if not called(request, "send"):
        call(request, "tc_send", "send", {"to": "tester", "content": QUESTION})
        done(request, "tool")
        return
    if not received(request, "tests/check.py covers"):
        wait_for_message(request, "the tester's answer")
        return
    if not called(request, "notify"):
        finding = "1. --dry-run returns before perform() runs, and the tester confirms a case covers it."
        call(request, "tc_notify", "notify", {"content": f"review of src/cli.py: {finding}"})
        done(request, "tool")
        return
    text(request, "Reported one finding to the lead.")
    done(request, "end")


def tester(request: dict) -> None:
    """Run the checks, answer the reviewer, report the result to the lead."""
    if step(request) == 0:
        call(request, "tc_bash", "bash", {"command": "python3 -B tests/check.py"})
        done(request, "tool")
        return
    if not received(request, QUESTION):
        wait_for_message(request, "the reviewer's question")
        return
    if not called(request, "send"):
        call(request, "tc_send", "send", {"to": "reviewer", "content": ANSWER})
        done(request, "tool")
        return
    if not called(request, "notify"):
        verdict = "passed" if "checks passed" in results(request)[0] else "failed"
        call(request, "tc_notify", "notify", {"content": f"tests {verdict}: python3 -B tests/check.py"})
        done(request, "tool")
        return
    text(request, "Ran the checks and reported the result to the lead.")
    done(request, "end")


request = read_request()
tools = tool_names(request)
if "spawn" in tools:
    lead(request)
elif "bash" in tools:
    tester(request)
else:
    reviewer(request)
