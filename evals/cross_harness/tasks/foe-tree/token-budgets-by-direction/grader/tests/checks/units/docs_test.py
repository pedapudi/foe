#!/usr/bin/python3
"""Whether one unit of a sweep carries the change, for a unit the commit gives no runnable test.

The unit is a directory rather than a crate, so no cargo suite judges it.
`docs.json` beside this file records, for each file the change
touches in the unit, the lines that tell the commit's form of that file
apart from its parent's: a line the commit's form holds and the parent's
does not, and a line the parent's form holds and the commit's does not. The
check reads each file from the working directory, which is the workspace
root, and requires every added line to stand in it and no removed line to.
Leading and trailing whitespace is ignored, so re-indentation is no failure,
and a line may stand anywhere in the file, so a change around it is none
either.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
UNIT = "docs"
# Findings beyond this many say the same thing again; the count states the rest.
REPORTED_MAX = 20

recorded = json.loads((HERE / "docs.json").read_text(encoding="utf-8"))
failures = []
for path in sorted(recorded):
    file = Path(path)
    if not file.is_file():
        failures.append(f"{path}: absent from the workspace")
        continue
    try:
        present = {line.strip() for line in file.read_text(encoding="utf-8").splitlines()}
    except UnicodeDecodeError as error:
        failures.append(f"{path}: not UTF-8 at byte {error.start}, so the check cannot read it")
        continue
    for line in recorded[path]["added"]:
        if line not in present:
            failures.append(f"{path}: the change writes the line {line!r}, which the file lacks")
    for line in recorded[path]["removed"]:
        if line in present:
            failures.append(f"{path}: the change removes the line {line!r}, which the file still holds")

for failure in failures[:REPORTED_MAX]:
    print(failure)
if len(failures) > REPORTED_MAX:
    print(f"and {len(failures) - REPORTED_MAX} more")
if failures:
    print(f"unit {UNIT}: {len(failures)} line(s) of the change are absent or still present")
    sys.exit(1)
