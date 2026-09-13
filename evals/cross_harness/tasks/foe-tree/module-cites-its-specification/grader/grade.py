#!/usr/bin/python3
"""Hidden checks for a constructed control: each part's test, the integration test, clippy, and the line budgets.

The script runs from the workspace, which holds `Cargo.toml` and `crates/`.
It copies the workspace without its build directory, restores the hidden
test files into the copy, and runs the crate's test suite there once, so the
agent's own test files never decide the grade. A unit's verdict is whether
the one test named for it ran and passed; the whole change is judged by the
integration test, by every other test of the crate, by clippy with warnings
denied, and by `scripts/loc.sh`. A finding that judges one unit begins
`unit <name>`, which is how `report.py` reads a unit's verdict back.

The verdicts are printed as one line `units: {name: passed}` on standard
error and written to `units.json` beside this script and in the grade's log
directory; the findings on standard output alone decide the grade.
`specification.json` beside this script names the task, the package, the
units, and the integration test. An optional `host.json` beside it names
`cargo` and `build_dir`; otherwise cargo comes from PATH and the build lives
under the user's state directory, read from the passwd database so that no
environment variable decides it. Every grade of one task shares one cargo
target directory under the build directory, and each grade has a directory
of its own for the copy and the command logs.
"""

import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GRADER = Path(__file__).resolve().parent
WORKSPACE = Path.cwd()
# Every command of the grade shares this budget; the first to reach it ends the grade.
COMMAND_SECONDS = 1200
UNITS_PREFIX = "units: "
UNITS_FILE = "units.json"
TAIL_LINES = 12
_TEST_LINE = re.compile(r"^test ([A-Za-z0-9_:]+) \.\.\. (ok|FAILED|ignored)")

specification = json.loads((GRADER / "specification.json").read_text(encoding="utf-8"))
host = {}
if (GRADER / "host.json").is_file():
    host = json.loads((GRADER / "host.json").read_text(encoding="utf-8"))
findings = []
verdicts = {unit["name"]: False for unit in specification["units"]}
arm = "unknown-arm"
try:
    arm = str(json.load(sys.stdin).get("arm") or arm)
except Exception as error:  # noqa: BLE001
    findings.append(f"the grader input is not JSON: {error}")

cargo = host.get("cargo") or shutil.which("cargo")
if cargo is None:
    findings.append("cargo is absent from PATH and host.json names none")
if not (WORKSPACE / "Cargo.toml").is_file() or not (WORKSPACE / "crates").is_dir():
    findings.append(f"the grade script runs from the workspace, and {WORKSPACE} holds no Cargo.toml and crates/ directory")
    cargo = None
build_dir = Path(host.get("build_dir") or Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local" / "state" / "foe" / "cross-harness" / "build")
build = build_dir / specification["task"]
target = build / "target"
deadline = time.monotonic() + COMMAND_SECONDS


def run(name, command, cwd, logs):
    """Run one command in its own process group and log its output; its output and exit status, or None when it could not run."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        findings.append(f"{name}: `{' '.join(command)}` did not start; the {COMMAND_SECONDS}s the commands share is spent")
        return None
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=remaining)
    except subprocess.TimeoutExpired:
        # The group holds cargo's own children, which would otherwise keep
        # the target directory's lock after cargo is gone.
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        findings.append(f"{name}: `{' '.join(command)}` ran past the {COMMAND_SECONDS}s the commands share and was killed")
        return None
    elapsed = time.monotonic() - started
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{re.sub(r'[^A-Za-z0-9_.-]+', '-', name)}.log").write_text(output, encoding="utf-8")
    print(f"{name}: exit {process.returncode} in {elapsed:.0f}s", file=sys.stderr)
    return output, process.returncode


def tail(output):
    return " | ".join([line for line in output.splitlines() if line.strip()][-TAIL_LINES:])


logs = None
if cargo is not None:
    grades = build / "grades" / re.sub(r"[^A-Za-z0-9_.-]+", "-", arm)
    grades.mkdir(parents=True, exist_ok=True)
    grade = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%dT%H%M%S") + "-", dir=grades))
    copy = grade / "copy"
    logs = grade / "logs"
    # The copies keep permission bits and take fresh modification times, so
    # cargo sees every source as newer than the last build and rebuilds every
    # workspace crate; an artifact of an earlier grade is never reused.
    shutil.copytree(WORKSPACE, copy, symlinks=True, copy_function=shutil.copy, ignore=shutil.ignore_patterns("target"))
    hidden = GRADER / "tests"
    for file in sorted(path for path in hidden.rglob("*") if path.is_file()):
        destination = copy / file.relative_to(hidden)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(file, destination)

    package = specification["package"]
    results = {}
    tested = run("tests", [cargo, "test", "--target-dir", str(target), "-p", package, "--no-fail-fast"], copy, logs)
    if tested is None:
        for unit in specification["units"]:
            findings.append(f"unit {unit['name']}: the tests of {package} did not run, so the test {unit['test']} reported nothing")
        findings.append(f"the tests of {package} did not run")
    else:
        output, status = tested
        for line in output.splitlines():
            match = _TEST_LINE.match(line)
            if match:
                results[match.group(1)] = match.group(2)
        judged = {unit["test"] for unit in specification["units"]} | {specification["integration_test"]}
        for unit in specification["units"]:
            outcome = results.get(unit["test"])
            if outcome == "ok":
                verdicts[unit["name"]] = True
            elif outcome is None:
                findings.append(f"unit {unit['name']}: the test {unit['test']} did not run; the crate's tests did not build, or the test is absent")
            else:
                findings.append(f"unit {unit['name']}: the test {unit['test']} {outcome.lower()}")
        integration = results.get(specification["integration_test"])
        if integration != "ok":
            state = "did not run; the crate's tests did not build, or the test is absent" if integration is None else integration.lower()
            findings.append(f"the integration test {specification['integration_test']} {state}")
        other = sorted(name for name, outcome in results.items() if outcome == "FAILED" and name not in judged)
        if other:
            findings.append(f"the tests of {package} failed outside the units: {', '.join(other)}")
        if status != 0 and not findings:
            findings.append(f"`cargo test -p {package}` exited {status}: {tail(output)}")

    linted = run("clippy", [cargo, "clippy", "--target-dir", str(target), "-p", package, "--all-targets", "--", "-D", "warnings"], copy, logs)
    if linted is None:
        findings.append(f"clippy on {package} did not run")
    elif linted[1] != 0:
        findings.append(f"`cargo clippy -p {package} --all-targets -- -D warnings` exited {linted[1]}: {tail(linted[0])}")
    loc = copy / "scripts" / "loc.sh"
    if not loc.is_file():
        findings.append("scripts/loc.sh is absent from the workspace")
    else:
        counted = run("loc", [str(loc)], copy, logs)
        if counted is None:
            findings.append("scripts/loc.sh did not run")
        elif counted[1] != 0:
            findings.append(f"`scripts/loc.sh` exited {counted[1]}: {tail(counted[0])}")
    # The logs stay for inspection; the copy has served its purpose.
    shutil.rmtree(copy, ignore_errors=True)

recorded = json.dumps(verdicts, indent=2) + "\n"
if logs is not None:
    logs.mkdir(parents=True, exist_ok=True)
    (logs / UNITS_FILE).write_text(recorded, encoding="utf-8")
(GRADER / UNITS_FILE).write_text(recorded, encoding="utf-8")
print(UNITS_PREFIX + json.dumps(verdicts), file=sys.stderr)
print("\n".join(findings))
