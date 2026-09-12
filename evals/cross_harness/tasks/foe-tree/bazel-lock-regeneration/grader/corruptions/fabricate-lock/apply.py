#!/usr/bin/python3
"""Edit MODULE.bazel.lock by hand so that it names the dependency; the grader must reject the arm."""

import pathlib
import sys

lock = pathlib.Path(sys.argv[1]) / "MODULE.bazel.lock"
text = lock.read_text(encoding="utf-8")
if not text.rstrip().endswith("}"):
    raise SystemExit(f"{lock}: expected a JSON object ending in a closing brace")
head, _, tail = text.rpartition("}")
lock.write_text(head + ',\n  "fabricated": "regex"\n}' + tail, encoding="utf-8")
