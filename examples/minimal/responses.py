#!/usr/bin/python3
"""Deterministic host responses that drive the minimal example.

Six turns: find the function, run the tests and see the
failure, read the function, edit it, run the tests again, then report. The
sixth turn carries no tool call, which is what completes an episode whose
configuration has no `done_when`.

The host runner loads this file and supplies each response over the protocol.
"""

from response_chunks import call, done, step, text

TESTS = "python3 -m unittest test_brackets"

BEFORE = """    depth = 0
    for character in text:
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
    return depth
"""

AFTER = """    depth = 0
    greatest = 0
    for character in text:
        if character == "[":
            depth += 1
            greatest = max(greatest, depth)
        elif character == "]":
            depth -= 1
    return greatest
"""

REPORT = (
    "bracket_depth returned the depth left at the end of the string. "
    "It now returns the greatest depth reached, and test_nested_brackets passes."
)


def respond(request: dict) -> list[dict]:
    """Return the tool call or final report for the current step."""
    chunks: list[dict] = []
    turn = step(request)
    if turn == 0:
        call(chunks, "tc_1", "grep", {"pattern": "def bracket_depth", "glob": "*.py"})
    elif turn == 1:
        call(chunks, "tc_2", "bash", {"command": TESTS})
    elif turn == 2:
        call(chunks, "tc_3", "read", {"path": "brackets.py"})
    elif turn == 3:
        call(chunks, "tc_4", "edit", {"path": "brackets.py", "edits": [{"old_text": BEFORE, "new_text": AFTER}]})
    elif turn == 4:
        call(chunks, "tc_5", "bash", {"command": TESTS})
    else:
        text(chunks, REPORT)
        done(chunks, "end")
        return chunks
    done(chunks, "tool")
    return chunks
