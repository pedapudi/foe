#!/usr/bin/python3
"""Hidden checks for a fan-out task: each unit's tests, the workspace check, and the specification sentences.

The script runs from the workspace, which holds `Cargo.toml` and `crates/`.
It copies the workspace without its build directory, restores the commit
form of every hidden test file into the copy, and runs each checked unit's
tests there: `cargo test -p <package>` for a crate, with the name of every
test function in the unit's hidden Rust test files required to appear in the
output, and the interpreter on every hidden `*_test.py` file for a
directory. A unit's verdict is one boolean. It then runs the workspace
check, `cargo test --workspace`, `cargo clippy --workspace -- -D warnings`,
and `scripts/loc.sh`, and requires every specification sentence the commit
added to a document to appear in the workspace's document.

The verdicts are printed as one line `units: {name: passed}` on standard
error and written to `units.json` beside this script and in the grade's log
directory; the findings on standard output alone decide the grade. The copy
beside this script is at a path a runner knows from the root alone; the
protocol judges damage before this script runs, so the file counts as
damage only when the same root is graded again. `specification.json`
beside this script names the task, the units, and the sentences. An
optional `host.json` beside it names `cargo` and `build_dir`; otherwise
cargo comes from PATH and the build lives under the user's state directory,
read from the passwd database so that no environment variable decides it.
Every grade of one task shares one cargo target directory under the build
directory, and each grade has a directory of its own for the copy and the
command logs.
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
_TEST_LINE = re.compile(r"^test (?:[A-Za-z0-9_:]+::)?([A-Za-z_][A-Za-z0-9_]*)(?: - should panic)? \.\.\. ")

specification = json.loads((GRADER / "specification.json").read_text(encoding="utf-8"))
host = {}
if (GRADER / "host.json").is_file():
    host = json.loads((GRADER / "host.json").read_text(encoding="utf-8"))
findings = []
verdicts = {}
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


def normalize(text):
    text = re.sub(r"[`*]", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return " ".join(text.split()).lower()


def run(name, command, cwd, logs):
    """Run one command in its own process group and log its output; its output, or None when it failed."""
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
    if process.returncode != 0:
        tail = [line for line in output.splitlines() if line.strip()][-TAIL_LINES:]
        findings.append(f"{name}: `{' '.join(command)}` exited {process.returncode}: " + " | ".join(tail))
        return None
    return output


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
    for unit in specification["units"]:
        name = unit["name"]
        before = len(findings)
        if unit["package"] is not None:
            output = run(f"unit {name} test", [cargo, "test", "--target-dir", str(target), "-p", unit["package"]], copy, logs)
            if output is not None:
                ran = {match.group(1) for match in (_TEST_LINE.match(line) for line in output.splitlines()) if match}
                for path, names in unit["hidden_test_names"].items():
                    absent = [test for test in names if test not in ran]
                    if absent:
                        findings.append(f"unit {name}: hidden test {path}: {', '.join(absent)} did not run; a test file under src/ compiles only through its mod declaration")
        for path in unit["python_tests"]:
            run(f"unit {name} {path}", ["/usr/bin/python3", "-B", str(copy / path)], copy, logs)
        verdicts[name] = len(findings) == before
    run("integration test", [cargo, "test", "--target-dir", str(target), "--workspace"], copy, logs)
    run("integration clippy", [cargo, "clippy", "--target-dir", str(target), "--workspace", "--", "-D", "warnings"], copy, logs)
    loc = copy / "scripts" / "loc.sh"
    if loc.is_file():
        run("integration loc", [str(loc)], copy, logs)
    else:
        findings.append("integration: scripts/loc.sh is absent from the workspace")
    # The logs stay for inspection; the copy has served its purpose.
    shutil.rmtree(copy, ignore_errors=True)

for path, sentences in specification["sentences"].items():
    document = WORKSPACE / path
    if not document.is_file():
        findings.append(f"{path} is absent from the workspace")
        continue
    text = normalize(document.read_text(encoding="utf-8"))
    for sentence in sentences:
        if sentence not in text:
            findings.append(f"{path} lacks the sentence: {sentence}")

recorded = json.dumps(verdicts, indent=2) + "\n"
if logs is not None:
    logs.mkdir(parents=True, exist_ok=True)
    (logs / UNITS_FILE).write_text(recorded, encoding="utf-8")
(GRADER / UNITS_FILE).write_text(recorded, encoding="utf-8")
print(UNITS_PREFIX + json.dumps(verdicts), file=sys.stderr)
print("\n".join(findings))
