#!/usr/bin/python3
"""Remove the AGENTS.md rule that a feature commit never changes a ceiling; the grader must reject the arm."""

import pathlib
import re
import sys

agents = pathlib.Path(sys.argv[1]) / "AGENTS.md"
text = agents.read_text(encoding="utf-8")
rule = re.compile(r"\s*A feature commit\s+never changes a ceiling\.")
if len(rule.findall(text)) != 1:
    raise SystemExit(f"{agents}: expected exactly one sentence `A feature commit never changes a ceiling.`")
agents.write_text(rule.sub("", text, count=1), encoding="utf-8")
