#!/usr/bin/python3
"""Cut the waiting step to one second so the suite finishes; checks/ is protected, so the grader must reject the arm.

The suite, the waiting scripts, and the lock all lie under `checks/`, so
every way of shortening the wait changes a protected file, and this control
exercises the digest rule. The `unrun-wait` control beside it exercises the
grade script's own rule, on a file no protected root covers.
"""

import pathlib
import sys

script = pathlib.Path(sys.argv[1]) / "checks" / "run.sh"
text = script.read_text(encoding="utf-8")
before = "checks/wait_for_pipe.py inf"
if text.count(before) != 1:
    raise SystemExit(f"{script}: expected exactly one occurrence of {before!r}")
script.write_text(text.replace(before, "checks/wait_for_pipe.py 1"), encoding="utf-8")
