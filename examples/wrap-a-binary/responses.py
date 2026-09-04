#!/usr/bin/python3
"""Deterministic host responses that drive the wrap-a-binary example.

Five turns exercise the wrapped checker, which
also proposes completion because the checker is the `done_when` verifier.
The verifier reports both findings as an inbox item. One
finding is resolved before completion, so the verifier reports the remaining
finding. The second finding is then resolved and the checker runs again. The empty verifier
result completes the episode without a redundant final model request.

The host runner loads this file and supplies each response over the protocol.
"""

from response_chunks import call, done, step, text

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


def edit(chunks: list[dict], call_id: str, before: str, after: str) -> None:
    call(chunks, call_id, "edit", {"path": SOURCE, "edits": [{"old_text": before, "new_text": after}]})


def respond(request: dict) -> list[dict]:
    """Return the checker call, edit, or completion for the current step."""
    chunks: list[dict] = []
    turn = step(request)
    if turn == 0:
        call(chunks, "tc_1", "style", {"args": []})
    elif turn == 1:
        edit(chunks, "tc_2", IMPORTS_BEFORE, IMPORTS_AFTER)
    elif turn == 2:
        text(chunks, FIRST_REPORT)
        done(chunks, "end")
        return chunks
    elif turn == 3:
        edit(chunks, "tc_3", RETURN_BEFORE, RETURN_AFTER)
    elif turn == 4:
        call(chunks, "tc_4", "style", {"args": []})
    else:
        raise RuntimeError(f"unexpected request turn {turn}")
    done(chunks, "tool")
    return chunks
