#!/usr/bin/python3
"""Remove the record the waiting step writes: the stop reached no verification, and the grader must reject the arm.

The record lies outside every protected root, so this control exercises the
grade script's own rule rather than the protected-file digests.
"""

import pathlib
import sys

record = pathlib.Path(sys.argv[1]) / "checks-wait.log"
if not record.is_file():
    raise SystemExit(f"{record} is absent; the oracle writes it and this corruption removes it")
record.unlink()
