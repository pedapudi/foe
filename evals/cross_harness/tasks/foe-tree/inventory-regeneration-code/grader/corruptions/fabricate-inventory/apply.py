#!/usr/bin/python3
"""Edit the inventory by hand so that it records another digest for the crate root; the grader must reject the arm."""

import pathlib
import re
import sys

inventory = pathlib.Path(sys.argv[1]) / "crates/code/inventory.toml"
text = inventory.read_text(encoding="utf-8")
line = re.compile(r'^"src/lib\.rs" = "[0-9a-f]{64}"$', re.MULTILINE)
if len(line.findall(text)) != 1:
    raise SystemExit(f"{inventory}: expected exactly one digest line for src/lib.rs")
inventory.write_text(line.sub('"src/lib.rs" = "' + "0" * 64 + '"', text, count=1), encoding="utf-8")
