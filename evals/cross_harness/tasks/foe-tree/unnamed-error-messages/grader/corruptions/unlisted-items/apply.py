#!/usr/bin/python3
"""Replace recorded lines of the workspace, each asserted to read as recorded before it is replaced."""

import json
import pathlib
import sys

here = pathlib.Path(__file__).resolve().parent
workspace = pathlib.Path(sys.argv[1])
for edit in json.loads((here / "edits.json").read_text(encoding="utf-8")):
    target = workspace / edit["path"]
    if not target.is_file():
        raise SystemExit(f"{target}: absent, so the corruption has nothing to edit")
    lines = target.read_text(encoding="utf-8").split("\n")
    index = edit["line"] - 1
    if index >= len(lines) or lines[index] != edit["old"]:
        found = lines[index] if index < len(lines) else None
        raise SystemExit(f"{target}:{edit['line']}: reads {found!r}, and the corruption expected {edit['old']!r}")
    lines[index] = edit["new"]
    target.write_text("\n".join(lines), encoding="utf-8")
