#!/usr/bin/python3
"""Restore the parent form of one unit's implementation files: that unit's tests must then fail and every other unit's pass."""

import json
import pathlib
import shutil
import sys

here = pathlib.Path(__file__).resolve().parent
workspace = pathlib.Path(sys.argv[1])
manifest = json.loads((here / "revert.json").read_text(encoding="utf-8"))
for relative in manifest["restore"]:
    target = workspace / relative
    if not target.is_file():
        raise SystemExit(f"{target}: absent, so the corruption has nothing to revert")
    shutil.copy2(here / "parent" / relative, target)
for relative in manifest["remove"]:
    target = workspace / relative
    if not target.is_file():
        raise SystemExit(f"{target}: absent, so the corruption has nothing to remove")
    target.unlink()
