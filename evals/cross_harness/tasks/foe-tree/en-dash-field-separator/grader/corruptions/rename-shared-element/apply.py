#!/usr/bin/python3
"""Rename one shared item in its unit's files: the workspace check must then fail while every unit's tests pass."""

import json
import pathlib
import re
import sys

here = pathlib.Path(__file__).resolve().parent
workspace = pathlib.Path(sys.argv[1])
manifest = json.loads((here / "rename.json").read_text(encoding="utf-8"))
pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(manifest["element"]) + r"(?![A-Za-z0-9_])")
for relative in manifest["files"]:
    target = workspace / relative
    if not target.is_file():
        raise SystemExit(f"{target}: absent, so the corruption has nothing to rename")
    text, count = pattern.subn(manifest["replacement"], target.read_text(encoding="utf-8"))
    if count == 0:
        raise SystemExit(f"{target}: holds no {manifest['element']!r} to rename")
    target.write_text(text, encoding="utf-8")
