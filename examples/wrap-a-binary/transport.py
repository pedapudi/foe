#!/usr/bin/python3
"""The model answers that drive the wrap-a-binary example.

Five turns, one per model call. The model runs the wrapped checker, which
also proposes completion because the checker is the `done_when` verifier.
The verifier reports both findings as an inbox item. The model resolves one
finding and finishes, so the verifier reports the remaining finding. The
model resolves that finding and runs the checker again. The empty verifier
result completes the episode without a redundant final model request.

The runner copies this file to `tools/transport.py` inside the disposable
project and `chunks.py` to `support/chunks.py` beside it. The transport runs
under the episode's sandbox, which lets it read the episode's read roots and
the file it was started from, so the module it imports has to live under the
read root.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd().parent / "support"))

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
        raise SystemExit(f"unexpected request turn {turn}")
    done(request, "tool")


if __name__ == "__main__":
    main()
