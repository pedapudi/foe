#!/usr/bin/python3
"""Deterministic responses for a lead and its two-level team."""

from response_chunks import call, done, error, step, text, tool_names

QUESTION = "Which checks cover src/cli.py?"
ANSWER = "tests/check.py covers src/cli.py with three cases, one of them the dry run."

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


def wait_for_peer(request: dict, chunks: list[dict]) -> None:
    """Wait mechanically for one peer inbox item."""
    call(
        chunks,
        f"tc_wait_peer_{step(request)}",
        "wait",
        {"until": [{"inbox": "peer"}], "timeout_seconds": 30},
    )
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
            {
                "contract": "reviewer",
                "task": "Review the change to src/cli.py, which adds a --dry-run flag.",
                "name": "reviewer",
                "scope": ["src/cli.py"],
            },
        )
        call(
            chunks,
            "tc_spawn_tester",
            "spawn",
            {
                "contract": "tester",
                "task": "Run `python3 -B tests/check.py` and report the result.",
                "name": "tester",
                "scope": ["tests/check.py"],
            },
        )
        call(
            chunks,
            "tc_spawn_integration",
            "spawn",
            {
                "contract": "integration",
                "task": "Inspect both reports, delegate a usage audit, and run the complete check.",
                "name": "integration",
                "blocked_by": ["task_01", "task_02"],
                "scope": ["src/cli.py", "tests/check.py"],
            },
        )
        done(chunks, "tool")
        return chunks
    if not called(request, "wait"):
        call(chunks, "tc_wait", "wait", {})
        done(chunks, "tool")
        return chunks
    if sum(1 for item in user_texts(request) if " ended: " in item) < 3:
        error(chunks, "wait returned before all three board tasks had ended")
        return chunks
    text(chunks, "Review, testing, nested usage audit, and integration completed for --dry-run.")
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
        wait_for_peer(request, chunks)
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
        wait_for_peer(request, chunks)
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


def integration(request: dict) -> list[dict]:
    """Inspect the parent board, delegate an audit, and run the final check."""
    chunks: list[dict] = []
    if not called(request, "team"):
        call(chunks, "tc_parent_board", "team", {})
        done(chunks, "tool")
        return chunks
    if not called(request, "spawn"):
        call(
            chunks,
            "tc_spawn_usage_auditor",
            "spawn",
            {
                "contract": "usage-auditor",
                "task": "Confirm the usage text and dry-run check agree.",
                "name": "usage-auditor",
                "scope": ["src/cli.py", "tests/check.py"],
            },
        )
        done(chunks, "tool")
        return chunks
    if not called(request, "wait"):
        call(chunks, "tc_wait_audit", "wait", {})
        done(chunks, "tool")
        return chunks
    if not called(request, "bash"):
        call(chunks, "tc_integration_check", "bash", {"command": "python3 -B tests/check.py"})
        done(chunks, "tool")
        return chunks
    if not called(request, "notify"):
        call(
            chunks,
            "tc_integration_report",
            "notify",
            {"content": "integration passed after the nested usage audit: python3 -B tests/check.py"},
        )
        done(chunks, "tool")
        return chunks
    text(chunks, "The nested audit and complete check passed.")
    done(chunks, "end")
    return chunks


def usage_auditor(request: dict) -> list[dict]:
    """Check the command usage against its dry-run case."""
    chunks: list[dict] = []
    if not called(request, "read"):
        call(chunks, "tc_read_check", "read", {"path": "tests/check.py"})
        done(chunks, "tool")
        return chunks
    if not called(request, "grep"):
        call(chunks, "tc_usage", "grep", {"pattern": "usage: cli.py [--dry-run]", "path": "src/cli.py", "literal": True})
        done(chunks, "tool")
        return chunks
    if not called(request, "notify"):
        call(
            chunks,
            "tc_usage_report",
            "notify",
            {"content": "usage audit passed: the help text and dry-run case agree"},
        )
        done(chunks, "tool")
        return chunks
    text(chunks, "Reported the usage audit to the integration lead.")
    done(chunks, "end")
    return chunks


def respond(request: dict) -> list[dict]:
    """Select a team role by the tools offered to the episode."""
    tools = tool_names(request)
    if "edit" in tools:
        return lead(request)
    if "spawn" in tools:
        return integration(request)
    if "bash" in tools:
        return tester(request)
    if "send" in tools:
        return reviewer(request)
    return usage_auditor(request)
