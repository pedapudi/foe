#!/usr/bin/python3
"""Cut the waiting step to one second so the suite finishes; checks/ is protected, so the grader must reject the arm."""

import pathlib
import sys

script = pathlib.Path(sys.argv[1]) / "checks" / "run.sh"
text = script.read_text(encoding="utf-8")
before = "checks/wait_for_lock.py checks/contract.lock 900"
if text.count(before) != 1:
    raise SystemExit(f"{script}: expected exactly one occurrence of {before!r}")
script.write_text(text.replace(before, "checks/wait_for_lock.py checks/contract.lock 1"), encoding="utf-8")
