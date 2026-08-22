#!/usr/bin/python3
"""Model chunks for the subagents demo: a parent and its survey children.

The parent and every child run their own episode, and each episode calls
this program once per model request. The request says which episode is
asking: the parent is the episode offered the `spawn` tool, and a survey
child is offered `notify` instead.

The parent spawns one survey per module named in the task, calls `wait`,
which returns once both children have ended, and only then edits. One call
buys the whole wait, so the parent spends no model call on filler and
depends on nothing about how long a child takes.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "support"))
from chunks import call, done, error, read_request, step, text, tool_names

MODULES = ["src/config.py", "src/client.py"]

# The rename the task asks for, one entry per module.
EDITS = {
    "src/config.py": [
        {"old_text": '{"timeout": 30, "retries": 3}', "new_text": '{"timeout_seconds": 30, "retries": 3}'},
        {"old_text": 'return settings["timeout"]', "new_text": 'return settings["timeout_seconds"]'},
    ],
    "src/client.py": [
        {"old_text": '"deadline": settings["timeout"]', "new_text": '"deadline": settings["timeout_seconds"]'},
    ],
}


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


def children_ended(request: dict) -> int:
    """How many children have reported that they ended.

    The runtime appends one such message per child, after the child's own
    report, so counting them tells the parent that every child has settled
    and that its reservation has returned to the pool.
    """
    return sum(1 for item in user_texts(request) if " ended: " in item)


def parent(request: dict) -> None:
    if step(request) == 0:
        for module in MODULES:
            name = Path(module).stem
            task = f"Report every place {module} reads the configuration key `timeout`."
            call(request, f"tc_spawn_{name}", "spawn", {"program": "survey", "name": f"{name}-survey", "task": task})
        done(request, "tool")
        return
    if not called(request, "wait"):
        call(request, "tc_wait", "wait", {})
        done(request, "tool")
        return
    if children_ended(request) < len(MODULES):
        error(request, "wait returned before every child had ended")
        return
    if not called(request, "edit"):
        for module, edits in EDITS.items():
            call(request, f"tc_edit_{Path(module).stem}", "edit", {"path": module, "edits": edits})
        done(request, "tool")
        return
    text(request, "Both surveys reported. The key is now `timeout_seconds` in src/config.py and src/client.py.")
    done(request, "end")


def survey(request: dict) -> None:
    """A child: search the module its task names, report, then finish."""
    task = user_texts(request)[0]
    found = re.search(r"src/\S+\.py", task)
    if not found:
        error(request, f"the survey task names no module: {task}")
        return
    module = found.group(0)
    if step(request) == 0:
        call(request, "tc_grep", "grep", {"pattern": "timeout", "path": module})
        done(request, "tool")
        return
    if not called(request, "notify"):
        lines = [line for line in results(request)[0].splitlines() if line.startswith(module)]
        call(request, "tc_notify", "notify", {"content": f"survey of {module}: " + "; ".join(lines)})
        done(request, "tool")
        return
    text(request, f"Reported every line of {module} that reads the key.")
    done(request, "end")


request = read_request()
if "spawn" in tool_names(request):
    parent(request)
else:
    survey(request)
