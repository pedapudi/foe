#!/usr/bin/python3
"""Add the key to the block's table in docs/config.md: a protected document changes, and the grader must reject the arm."""

import pathlib
import re
import sys

document = pathlib.Path(sys.argv[1]) / "docs" / "config.md"
text = document.read_text(encoding="utf-8")
heading = "### `tool_defs`\n"
if text.count(heading) != 1:
    raise SystemExit(f"{document}: expected exactly one heading {heading.strip()!r}")
head, tail = text.split(heading)
header = re.search(r"^\|.*\|$", tail, re.MULTILINE)
row = re.search(r"^\| `[^`]+` \|.*$", tail, re.MULTILINE)
if header is None or row is None or header.start() > row.start():
    raise SystemExit(f"{document}: no table with a header row and a key row follows the heading {heading.strip()!r}")
# The row takes the table's own columns: the key first, the purpose last, and
# between them the value each named column takes for an optional integer.
names = [cell.strip().strip("`") for cell in header.group(0).strip().strip("|").split("|")]
if len(names) < 2:
    raise SystemExit(f"{document}: the table under {heading.strip()!r} has {len(names)} column(s); the corruption needs a key column and a meaning column")
between = {"type": "integer", "required": "no", "default": "unlimited"}
cells = ["`stdin`", *[between.get(name, "") for name in names[1:-1]], "names an absolute path to a file the executable receives on standard input in place of `/dev/null`"]
added = "| " + " | ".join(cells) + " |\n"
document.write_text(head + heading + tail[: row.start()] + added + tail[row.start() :], encoding="utf-8")
