#!/usr/bin/python3
"""Add the field the task asks for to the dataclass: the signature changes, and the hidden test must fail."""

import pathlib
import re
import sys

module = pathlib.Path(sys.argv[1]) / "python/foe/_contract.py"
text = module.read_text(encoding="utf-8")
last = re.compile(r"^(?P<indent>[ ]+)loop_threshold: .*$", re.MULTILINE)
if len(last.findall(text)) != 1:
    raise SystemExit(f"{module}: expected exactly one field line starting with `loop_threshold: `")
module.write_text(last.sub(lambda m: m.group(0) + "\n" + m.group("indent") + "tool_calls: int | None = None", text, count=1), encoding="utf-8")
