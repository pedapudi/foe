#!/usr/bin/python3
"""Hidden checks for a feature-removal task: the commit's tests, clippy, the line budgets, and the specification sentences.

The script runs from the workspace, which holds `Cargo.toml` and `crates/`.
It copies the workspace without its build directory, restores the commit
form of every test file into the copy, and runs `cargo test` and `cargo
clippy -- -D warnings` for the touched crates and `scripts/loc.sh` there, so
the agent's own test files never decide the grade. A Rust test file under
`src/` compiles only through a `mod` declaration in the implementation, so
the script requires the name of every test function in a hidden test file to
appear in the `cargo test` output. It then requires every specification
sentence the commit added to a document to appear in the workspace's
document.

`specification.json` beside this script names the crates, the hidden test
names, and the sentences. An optional `host.json` beside it names `cargo`
and `build_dir`; otherwise cargo comes from PATH and the build lives under
the user's state directory, read from the passwd database so that no
environment variable decides it. Under the build directory each task has a
cargo target directory that every grade of the task shares, and each grade
has a directory of its own for the copy and the command logs.
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
# The three commands share this budget; the first to reach it ends the grade.
COMMAND_SECONDS = 1200
TAIL_LINES = 12
_TEST_LINE = re.compile(r"^test (?:[A-Za-z0-9_:]+::)?([A-Za-z_][A-Za-z0-9_]*)(?: - should panic)? \.\.\. ")

specification = json.loads((GRADER / "specification.json").read_text(encoding="utf-8"))
host = {}
if (GRADER / "host.json").is_file():
    host = json.loads((GRADER / "host.json").read_text(encoding="utf-8"))
findings = []
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
# Each task builds in its own target directory. Cargo names a workspace
# member's artifacts without the workspace path, so two tasks sharing one
# directory would serve each other stale crates. Every grade of one task
# shares the directory, so its dependencies compile once; cargo serializes
# grades that run at once on its lock for the directory.
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
    (logs / f"{name}.log").write_text(output, encoding="utf-8")
    print(f"{name}: exit {process.returncode} in {elapsed:.0f}s", file=sys.stderr)
    if process.returncode != 0:
        tail = [line for line in output.splitlines() if line.strip()][-TAIL_LINES:]
        findings.append(f"{name}: `{' '.join(command)}` exited {process.returncode}: " + " | ".join(tail))
        return None
    return output


if cargo is not None:
    grades = build / "grades" / re.sub(r"[^A-Za-z0-9_.-]+", "-", arm)
    grades.mkdir(parents=True, exist_ok=True)
    grade = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%dT%H%M%S") + "-", dir=grades))
    copy = grade / "copy"
    logs = grade / "logs"
    # The copies keep permission bits and take fresh modification times, so
    # cargo sees every source as newer than the last build and rebuilds the
    # workspace crates instead of reusing an artifact of an earlier grade.
    shutil.copytree(WORKSPACE, copy, symlinks=True, copy_function=shutil.copy, ignore=shutil.ignore_patterns("target"))
    hidden = GRADER / "tests"
    for file in sorted(path for path in hidden.rglob("*") if path.is_file()):
        destination = copy / file.relative_to(hidden)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(file, destination)
    packages = []
    for name in specification["packages"]:
        packages += ["-p", name]
    output = run("test", [cargo, "test", "--target-dir", str(target), *packages], copy, logs)
    if output is not None:
        ran = {match.group(1) for match in (_TEST_LINE.match(line) for line in output.splitlines()) if match}
        for path, names in specification["hidden_test_names"].items():
            absent = [name for name in names if name not in ran]
            if absent:
                findings.append(f"hidden test {path}: {', '.join(absent)} did not run; a test file under src/ compiles only through its mod declaration")
    run("clippy", [cargo, "clippy", "--target-dir", str(target), *packages, "--", "-D", "warnings"], copy, logs)
    loc = copy / "scripts" / "loc.sh"
    if loc.is_file():
        run("loc", [str(loc)], copy, logs)
    else:
        findings.append("scripts/loc.sh is absent from the workspace")
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

print("\n".join(findings))
