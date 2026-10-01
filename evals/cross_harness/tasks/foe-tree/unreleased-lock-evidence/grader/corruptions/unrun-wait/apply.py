#!/usr/bin/python3
"""Remove the waiting step from checks/run.sh so that the suite never waits; checks/ is protected, so the grader must reject the arm.

The step is two lines: the one that prints the wait marker and the one that
waits. Removing both leaves a suite that runs the line ceilings and then the
cargo steps, which is the suite an arm that wanted to skip the wait would
write. The `shorten-wait` control beside it keeps the step and cuts its
duration.
"""

import pathlib
import sys

script = pathlib.Path(sys.argv[1]) / "checks" / "run.sh"
text = script.read_text(encoding="utf-8")
step = 'echo "checks/run.sh step 2 waiting on lock" >&2\n/usr/bin/python3 checks/wait_for_lock.py checks/evidence.lock inf\n'
if text.count(step) != 1:
    raise SystemExit(f"{script}: expected exactly one waiting step {step!r}")
script.write_text(text.replace(step, ""), encoding="utf-8")
