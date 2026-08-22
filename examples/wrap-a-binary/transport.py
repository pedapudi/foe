#!/usr/bin/python3
"""The model answers that drive the wrap-a-binary example.

Six turns, one per model call. The model runs the wrapped checker, resolves
one of its two findings, and finishes. The runtime then runs the same
executable as the `done_when` verifier, which still reports the other
finding, so the episode does not complete: the findings arrive as an inbox
item and the model gets another turn. It resolves the second finding, runs
the checker again, and finishes; this time the verifier prints nothing and
the episode completes.

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

SOURCE = "src/report.py"

IMPORTS_BEFORE = """import json
import math
"""

IMPORTS_AFTER = """import json
"""

RETURN_BEFORE = (
    '    return json.dumps({"rows": len(rows), "total": total, '
    '"mean": total / len(rows) if rows else 0.0, '
    '"widest": max((len(row["name"]) for row in rows), default=0)})\n'
)

RETURN_AFTER = """    fields = {
        "rows": len(rows),
        "total": total,
        "mean": total / len(rows) if rows else 0.0,
        "widest": max((len(row["name"]) for row in rows), default=0),
    }
    return json.dumps(fields)
"""

FIRST_REPORT = "Removed the unused math import."

SECOND_REPORT = "Split the long return statement so that no line is over 88 columns."


def edit(request: dict, call_id: str, before: str, after: str) -> None:
    call(request, call_id, "edit", {"path": SOURCE, "edits": [{"old_text": before, "new_text": after}]})


def main() -> None:
    request = read_request()
    turn = step(request)
    if turn == 0:
        call(request, "tc_1", "style", {"args": []})
    elif turn == 1:
        edit(request, "tc_2", IMPORTS_BEFORE, IMPORTS_AFTER)
    elif turn == 2:
        text(request, FIRST_REPORT)
        done(request, "end")
        return
    elif turn == 3:
        edit(request, "tc_3", RETURN_BEFORE, RETURN_AFTER)
    elif turn == 4:
        call(request, "tc_4", "style", {"args": []})
    else:
        text(request, SECOND_REPORT)
        done(request, "end")
        return
    done(request, "tool")


if __name__ == "__main__":
    main()
