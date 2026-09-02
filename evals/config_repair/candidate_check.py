#!/usr/bin/python3
"""Structural verifier for a proposed contract document.

The repair-proposal episode declares this executable as its
`done_when.verify` tool: the runtime passes the returned candidate as
JSON on standard input, and an empty output accepts it. Acceptance here
means only that the candidate has the shape of a contract document.
Whether the repair is acceptable is judged afterwards by the external
evaluator, which reruns the task; a candidate that would fail there
still passes this check, so trivial repairs are rejected by the
evaluator rather than hidden inside the proposal episode.
"""

import json
import sys


def findings(candidate: object) -> list[str]:
    if not isinstance(candidate, dict):
        return ["the candidate is not a JSON object"]
    found = []
    if candidate.get("version") != 4:
        found.append("the candidate does not declare contract document version 4")
    if not isinstance(candidate.get("tools"), list):
        found.append("the candidate has no tools list")
    if not isinstance(candidate.get("grants"), dict):
        found.append("the candidate has no grants object")
    if not isinstance(candidate.get("model"), dict):
        found.append("the candidate has no model block")
    return found


def main() -> None:
    try:
        candidate = json.load(sys.stdin)
    except ValueError:
        print("the candidate is not one JSON value")
        return
    for finding in findings(candidate):
        print(finding)


if __name__ == "__main__":
    main()
