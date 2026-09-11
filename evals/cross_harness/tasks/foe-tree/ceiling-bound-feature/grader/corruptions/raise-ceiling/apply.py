#!/usr/bin/python3
"""Raise the kernel ceiling in scripts/loc.sh: a protected file changes, and the grader must reject the arm."""

import pathlib
import re
import sys

script = pathlib.Path(sys.argv[1]) / "scripts" / "loc.sh"
text = script.read_text(encoding="utf-8")
row = re.compile(r"^(?P<lead>\s*kernel\s*\|\s*)6450(?P<tail>\s*\|)", re.MULTILINE)
if len(row.findall(text)) != 1:
    raise SystemExit(f"{script}: expected exactly one budgets row `kernel | 6450 |`")
script.write_text(row.sub(lambda m: m.group("lead") + "7450" + m.group("tail"), text), encoding="utf-8")
