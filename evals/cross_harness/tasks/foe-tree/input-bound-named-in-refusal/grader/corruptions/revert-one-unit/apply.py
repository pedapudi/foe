#!/usr/bin/python3
"""Apply one recorded mutation of the solved workspace; every replacement asserts that its source stands exactly once."""

import json
import pathlib
import sys

here = pathlib.Path(__file__).resolve().parent
workspace = pathlib.Path(sys.argv[1])
for edit in json.loads((here / "edits.json").read_text(encoding="utf-8")):
    target = workspace / edit["path"]
    if not target.is_file():
        raise SystemExit(f"{target}: absent, so the corruption has nothing to change")
    text = target.read_text(encoding="utf-8")
    found = text.count(edit["old"])
    if found != 1:
        raise SystemExit(f"{target}: the corruption's source stands {found} times; it stands exactly once in the solved workspace")
    target.write_text(text.replace(edit["old"], edit["new"]), encoding="utf-8")
