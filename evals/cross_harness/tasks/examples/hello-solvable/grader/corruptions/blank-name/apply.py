#!/usr/bin/python3
"""Drop the stranger case: the visible test still passes, and the hidden checks must fail."""

import pathlib
import sys

workspace = pathlib.Path(sys.argv[1])
source = workspace / "src" / "greeting.py"
text = source.read_text(encoding="utf-8")
before = '(trimmed or "stranger")'
if before not in text:
    raise SystemExit(f"{source}: {before!r} is absent, so the corruption has nothing to change")
source.write_text(text.replace(before, "trimmed"), encoding="utf-8")
