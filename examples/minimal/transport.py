#!/usr/bin/python3
"""The model answers that drive the minimal example.

Six turns, one per model call: find the function, run the tests and see the
failure, read the function, edit it, run the tests again, then report. The
sixth turn carries no tool call, which is what completes an episode whose
configuration has no `done_when`.

The runner copies this file to `tools/transport.py` inside the disposable
project and `chunks.py` to `support/chunks.py` beside it. The transport runs
under the episode's sandbox, which lets it read the episode's read roots and
the file it was started from, so the module it imports has to live under the
read root.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "support"))

from chunks import call, done, read_request, step, text  # noqa: E402

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


def main() -> None:
    request = read_request()
    turn = step(request)
    if turn == 0:
        call(request, "tc_1", "grep", {"pattern": "def bracket_depth", "glob": "*.py"})
    elif turn == 1:
        call(request, "tc_2", "bash", {"command": TESTS})
    elif turn == 2:
        call(request, "tc_3", "read", {"path": "brackets.py"})
    elif turn == 3:
        call(request, "tc_4", "edit", {"path": "brackets.py", "edits": [{"old_text": BEFORE, "new_text": AFTER}]})
    elif turn == 4:
        call(request, "tc_5", "bash", {"command": TESTS})
    else:
        text(request, REPORT)
        done(request, "end")
        return
    done(request, "tool")


if __name__ == "__main__":
    main()
