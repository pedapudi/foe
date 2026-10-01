#!/usr/bin/python3
"""Keep the surrounding whitespace: the visible test still passes, and the hidden checks must fail."""

import pathlib
import sys

workspace = pathlib.Path(sys.argv[1])
source = workspace / "src" / "greeting.py"
text = source.read_text(encoding="utf-8")
before = "trimmed = name.strip()"
if before not in text:
    raise SystemExit(f"{source}: {before!r} is absent, so the corruption has nothing to change")
source.write_text(text.replace(before, "trimmed = name"), encoding="utf-8")
