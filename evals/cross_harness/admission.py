#!/usr/bin/python3
"""Decide whether a cross-harness task's visible check can pass in every environment an arm runs it in.

An arm is judged by a hidden grader and steered by the visible check suite
the workspace carries at `checks/run.sh`. Both harnesses under comparison
run their commands inside a kernel sandbox, and this repository's own crates
test sandboxing, so a check suite that runs one of those crate suites fails
inside a sandbox and passes outside it. The two sandboxes deny different
operations, so the two arms then meet differently broken suites and neither
can make the check pass. A task in that state measures the sandbox rather
than the harness and belongs out of the comparison.

`check` materializes each selected task's workspace into a scratch root,
copies the grader's oracle workspace over it, which is the solved state, and
runs `checks/run.sh` in three environments:

    host    a cleared environment on the host, outside every sandbox
    foe     one scripted episode of the foe runtime, `sandbox.mode: required`
    codex   `codex sandbox -P :workspace`, the Codex CLI sandbox policy

A task is admissible when the check exits zero in all three. For every
environment the report states the exit status, the wall time, and the
failing test names, and splits those names into the ones the hidden grader
relies on and the ones unrelated to the task, which is what separates a
broken task from a sound task standing on a crate that a sandbox breaks.
The report also names the crates the task's check invokes, so a reader sees
which tasks depend on a given crate suite.

A task whose grader carries no oracle workspace is measured against its
unsolved state, because that grader prescribes a stop rather than a
solution. Its check is not expected to exit zero, and the report says so in
the task's reasons and in the `oracle_solves_workspace` field, so a reader
can tell that verdict apart from a check a solved workspace still fails.

    admission.py check --tasks DIR [--task NAME]... [--foe PATH] [--out DIR]

The tool reads the task tree and writes nothing into it. The exit status is
0 when every selected task is admissible, 1 when one is not, and 2 when the
tool could not run.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent
EVALS = HERE.parent
for _directory in (EVALS, HERE, HERE / "tasks"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

import protocol  # noqa: E402

SCHEMA_VERSION = 1

HOST, FOE, CODEX = "host", "foe", "codex"
ENVIRONMENTS: tuple[str, ...] = (HOST, FOE, CODEX)

ADMISSIBLE, INADMISSIBLE, CHANGED = "admissible", "inadmissible", "changed"
VERDICTS: tuple[str, ...] = (ADMISSIBLE, INADMISSIBLE, CHANGED)

# The check suite of a task workspace, and the scratch directory it is given.
CHECK_SUITE = "checks/run.sh"
CHECK_SCRATCH = ".check-tmp"
# The search path a command receives, from docs/tools.md "bash".
SYSTEM_SEARCH_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
# The roots outside the system directories that the Rust toolchain lives in.
# A check suite that builds reads all of them and runs programs under the
# first of them.
TOOL_ROOT_SUFFIXES: tuple[str, ...] = (".cargo/bin", ".cargo", ".rustup")
SHARED_TOOL_ROOTS: tuple[str, ...] = ("/usr/lib/gcc",)
# The roots every foe document grants execute on, from contracts/graphs.py.
EXECUTE_ROOTS: tuple[str, ...] = ("/bin", "/usr/bin", "/usr/local/bin")
# The exit status a shell gives a command it cannot find, and the line the
# wrapper prints then, which run.py reads back as a fault rather than a finding.
COMMAND_NOT_FOUND_STATUS = 127
CHECK_SUITE_UNAVAILABLE = "the check suite could not run"
# The status recorded for an environment whose check did not finish inside
# the wall-clock bound, and for one whose result never arrived.
TIMED_OUT_STATUS, NO_RESULT_STATUS = -1, -2

# The name the scripted episode gives the check tool, and the episode log.
CHECK_TOOL = "check"
EPISODE_LOG = "episode.jsonl"
# How much longer than the check itself the scripted episode may run.
EPISODE_SLACK_SECONDS = 300
# The foe binary a caller that names none runs, from this checkout's own
# build. A measurement that stands beside a comparison run passes `--foe`
# naming the binary that run used, which is built from a clean checkout so
# that uncommitted work in the tree cannot change it. Every report records
# the binary it measured under `foe_binary`.
DEFAULT_FOE = str(EVALS.parent / "target" / "debug" / "foe")
# The Codex CLI command, taken from the search path, and the permission
# profile that grants its working directory.
CODEX_COMMAND, CODEX_PROFILE = "codex", ":workspace"
# How long one environment's check may run before it is recorded as unfinished.
DEFAULT_SECONDS = 900
# Where a caller that names no output directory writes.
DEFAULT_OUT = "~/.local/state/foe/cross-harness/admission"

REPORT_FILE, TABLE_FILE = "admission.json", "admission.md"

# `test <name> ... ok` and its verdicts, as cargo's libtest harness prints them.
_TEST_LINE = re.compile(r"^test (?P<name>\S.*?) \.\.\. (?P<verdict>ok|FAILED|ignored|.*)$")
# The header of a failure list, the indented names under it, and the header
# of one failure's captured output, which names the test as well.
_FAILURES_HEADER = re.compile(r"^\s*failures:\s*$")
_FAILURE_NAME = re.compile(r"^ {4}(?P<name>\S.*?)\s*$")
_FAILURE_OUTPUT = re.compile(r"^-{4} (?P<name>.+?) stdout -{4}$")
# `cargo test` at the start of a line, and the package selection in it.
_CARGO_TEST_LINE = re.compile(r"^\s*cargo\s+test\b(?P<arguments>.*)$")
_PACKAGE_FLAG = re.compile(r"(?:^|\s)(?:-p|--package)[ =](?P<name>[A-Za-z0-9_.-]+)")
_WHOLE_WORKSPACE = re.compile(r"(?:^|\s)--(?:workspace|all)(?:\s|$)")
# The `name` of a cargo manifest's `[package]` table.
_PACKAGE_TABLE = re.compile(r"^\[package\]\s*$", re.MULTILINE)
_PACKAGE_NAME = re.compile(r'^name\s*=\s*"(?P<name>[^"]+)"', re.MULTILINE)
# A Rust test function, and the attributes that may sit between the marker
# and the signature.
_RUST_TEST = re.compile(r"#\[test\]\s*(?:#\[[^\]]*\]\s*)*fn\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
_PYTHON_TEST = re.compile(r"^\s*def (?P<name>test_[A-Za-z0-9_]*)\s*\(", re.MULTILINE)

# How much of a failing environment's output the report keeps.
TAIL_CHARACTERS = 4000


class AdmissionError(Exception):
    """The tool cannot run, or one task cannot be measured: a path, a binary, or a task file is missing or unusable."""


def home() -> Path:
    """The invoking user's home directory, from the passwd database, so that no environment variable decides it."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def tool_roots() -> list[str]:
    """The toolchain roots outside the system directories, in the order a search path lists them."""
    return [str(home() / suffix) for suffix in TOOL_ROOT_SUFFIXES] + list(SHARED_TOOL_ROOTS)


def search_path() -> str:
    """The search path the check suite runs with: the system directories, then the toolchain roots."""
    return ":".join([SYSTEM_SEARCH_PATH, *tool_roots()])



def home_directory() -> str:
    """The real user's home, from the passwd database rather than the environment.

    A Codex arm inherits this value, and a toolchain manager reads it to find
    its installation, so a check run under any environment states the same one.
    """
    return pwd.getpwuid(os.getuid()).pw_dir

def wrapper_body(workspace: Path) -> str:
    """The shell body that runs a workspace's check suite with an environment of its own.

    Only PATH, LANG, HOME, and TMPDIR are set, because the foe runtime starts a
    configured executable with an empty environment, and the same body has
    to behave identically under the other harness's sandbox, which inherits
    one. The suite's standard error joins its standard output, so one stream
    carries every failing test name. A suite that ends with the status a
    shell gives a command it cannot find prints the line the runner reads as
    a fault rather than as a finding.
    """
    scratch = workspace / CHECK_SCRATCH
    return "\n".join(
        [
            f"PATH='{search_path()}'",
            "LANG=C.UTF-8",
            f"TMPDIR='{scratch}'",
            f"HOME='{home_directory()}'",
            "export PATH LANG HOME TMPDIR",
            f"mkdir -p '{scratch}' || {{ echo 'the scratch directory {scratch} cannot be created'; exit 1; }}",
            f"cd '{workspace}' || {{ echo 'the workspace {workspace} cannot be entered'; exit 1; }}",
            f"./{CHECK_SUITE} 2>&1",
            "status=$?",
            f'if [ "$status" -eq {COMMAND_NOT_FOUND_STATUS} ]; then echo \'{CHECK_SUITE_UNAVAILABLE}\'; fi',
            "exit $status",
            "",
        ]
    )


def write_wrapper(path: Path, workspace: Path) -> Path:
    """Write the wrapper the scripted episode runs as its check tool, and make it executable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + wrapper_body(workspace), encoding="utf-8")
    path.chmod(0o755)
    return path


def parse_test_names(output: str) -> list[str]:
    """Every test name the output reports a verdict for, in the order the lines appear, without repeats."""
    names: list[str] = []
    for line in output.splitlines():
        match = _TEST_LINE.match(line)
        if match is not None and match.group("name") not in names:
            names.append(match.group("name"))
    return names


def parse_failing_tests(output: str) -> list[str]:
    """Every failing test name cargo's output states, sorted and without repeats.

    A failing run prints the name three times: once as a `FAILED` verdict
    line, once as the header of the test's captured output, and once in the
    indented list under the run's `failures:` header. All three are read, so
    a run whose verdict lines interleaved under parallel execution still
    yields the complete set, and a run reporting several test binaries
    yields the union over them.
    """
    failing: set[str] = set()
    in_failures = False
    for line in output.splitlines():
        verdict = _TEST_LINE.match(line)
        if verdict is not None and verdict.group("verdict").startswith("FAILED"):
            failing.add(verdict.group("name"))
        captured = _FAILURE_OUTPUT.match(line)
        if captured is not None:
            failing.add(captured.group("name"))
        if _FAILURES_HEADER.match(line):
            in_failures = True
            continue
        if in_failures:
            listed = _FAILURE_NAME.match(line)
            if listed is None:
                in_failures = False
            else:
                failing.add(listed.group("name"))
    return sorted(failing)


def workspace_packages(workspace: Path) -> list[str]:
    """Every cargo package name the workspace declares under `crates/`, sorted."""
    names: set[str] = set()
    for manifest in sorted((workspace / "crates").glob("*/Cargo.toml")):
        text = manifest.read_text(encoding="utf-8", errors="replace")
        table = _PACKAGE_TABLE.search(text)
        if table is None:
            continue
        name = _PACKAGE_NAME.search(text, table.end())
        if name is not None:
            names.add(name.group("name"))
    return sorted(names)


def checked_crates(script: str, packages: Sequence[str] = ()) -> list[str]:
    """The cargo packages a check script's `cargo test` lines run, sorted.

    A line that names packages with `-p` contributes those names. A line
    that selects the whole workspace contributes every package given, which
    the caller reads from the workspace's manifests. A script with no
    `cargo test` line runs no crate suite and contributes nothing.
    """
    named: set[str] = set()
    for line in script.split("\n"):
        match = _CARGO_TEST_LINE.match(line)
        if match is None:
            continue
        arguments = match.group("arguments")
        named.update(flag.group("name") for flag in _PACKAGE_FLAG.finditer(arguments))
        if _WHOLE_WORKSPACE.search(arguments):
            named.update(packages)
    return sorted(named)


def hidden_test_names(task_dir: Path) -> list[str]:
    """Every test name the hidden grader relies on, from `grader/specification.json` and the `grader/tests/` tree."""
    names: set[str] = set()
    specification = task_dir / protocol.GRADER / "specification.json"
    if specification.is_file():
        try:
            document = json.loads(specification.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise AdmissionError(f"{specification}: not JSON: {error}") from error
        declared = document.get("hidden_test_names")
        if isinstance(declared, dict):
            for listed in declared.values():
                if isinstance(listed, list):
                    names.update(str(name) for name in listed)
        elif isinstance(declared, list):
            names.update(str(name) for name in declared)
        elif declared is not None:
            raise AdmissionError(f"{specification}: key hidden_test_names is {declared!r}; expected an object or a list of test names")
    tests = task_dir / protocol.GRADER / "tests"
    if tests.is_dir():
        for source in sorted(tests.rglob("*")):
            if not source.is_file():
                continue
            text = source.read_text(encoding="utf-8", errors="replace")
            if source.suffix == ".rs":
                names.update(match.group("name") for match in _RUST_TEST.finditer(text))
            elif source.suffix == ".py":
                names.update(match.group("name") for match in _PYTHON_TEST.finditer(text))
    return sorted(names)


def split_by_hidden(failing: Sequence[str], hidden: Sequence[str]) -> tuple[list[str], list[str]]:
    """The failing tests the hidden grader relies on, and the rest.

    A hidden test is named by its function alone and a failing test by its
    module path, so the two are compared on the last path component. A
    failure among the hidden tests means the task itself does not hold in
    that environment; a failure outside them means the task stands on a
    crate whose suite the environment breaks.
    """
    wanted = set(hidden)
    own = sorted(name for name in set(failing) if name.rsplit("::", 1)[-1] in wanted)
    unrelated = sorted(set(failing) - set(own))
    return own, unrelated


@dataclass(frozen=True)
class Measurement:
    """One run of a workspace's check suite in one environment."""

    environment: str
    exit_status: int
    seconds: float
    failing: list[str]
    names: list[str]
    output: str
    note: str | None = None

    @property
    def passed(self) -> bool:
        return self.exit_status == 0

    def to_dict(self, hidden: Sequence[str] = ()) -> dict[str, Any]:
        own, unrelated = split_by_hidden(self.failing, hidden)
        return {
            "environment": self.environment,
            "exit_status": self.exit_status,
            "seconds": round(self.seconds, 1),
            "failing_count": len(self.failing),
            "failing_tests": list(self.failing),
            "failing_hidden_tests": own,
            "failing_unrelated_tests": unrelated,
            "note": self.note,
            "output_tail": "" if self.passed else self.output[-TAIL_CHARACTERS:],
        }


def _measure_process(environment: str, command: list[str], workspace: Path, seconds: int) -> Measurement:
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=seconds, check=False)
    except subprocess.TimeoutExpired as expired:
        raw = expired.stdout or ""
        output = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        return Measurement(
            environment,
            TIMED_OUT_STATUS,
            time.monotonic() - started,
            parse_failing_tests(output),
            parse_test_names(output),
            output,
            f"the check did not finish within {seconds} seconds",
        )
    except OSError as error:
        raise AdmissionError(f"the {environment} check of {workspace} could not start: {command[0]}: {error}") from error
    output = result.stdout + result.stderr
    return Measurement(environment, result.returncode, time.monotonic() - started, parse_failing_tests(output), parse_test_names(output), output)


def measure_host(workspace: Path, seconds: int) -> Measurement:
    """Run the check suite on the host with a cleared environment, outside every sandbox."""
    scratch = workspace / CHECK_SCRATCH
    scratch.mkdir(parents=True, exist_ok=True)
    command = ["env", "-i", f"PATH={search_path()}", "LANG=C.UTF-8", f"HOME={home_directory()}", f"TMPDIR={scratch}", "sh", "-c", f"./{CHECK_SUITE}"]
    return _measure_process(HOST, command, workspace, seconds)


def measure_codex(workspace: Path, seconds: int, codex: str = CODEX_COMMAND) -> Measurement:
    """Run the check suite under the Codex CLI sandbox policy that grants the working directory.

    `codex sandbox` applies a policy to one command and involves no model,
    so the measurement spends nothing.
    """
    command = [codex, "sandbox", "-P", CODEX_PROFILE, "-C", str(workspace), "--", "/bin/sh", "-c", wrapper_body(workspace)]
    return _measure_process(CODEX, command, workspace, seconds)


def episode_document(workspace: Path, wrapper: Path, seconds: int) -> dict[str, Any]:
    """The version 4 file document of the scripted episode that runs the check once under a required sandbox.

    The workspace is granted execute as well as read and write, because a
    check suite runs the binaries its own build wrote under the workspace;
    without that grant the suite ends with the status a shell gives a file
    it may not execute.
    """
    roots = tool_roots()
    return {
        "version": 4,
        "name": "admission-check",
        "instructions": {"10-role": "Run the check tool once and report what it printed."},
        "tools": [CHECK_TOOL],
        "tool_defs": {
            CHECK_TOOL: {
                "exec": str(wrapper),
                "description": "Runs the workspace check suite with no arguments and reports its output and exit status.",
                "cwd": str(workspace),
                "timeout_seconds": seconds,
            }
        },
        "grants": {
            "read": [str(workspace), *roots],
            "write": [str(workspace)],
            "execute": [*EXECUTE_ROOTS, *roots, str(workspace)],
        },
        "budget": {"model_calls": 3, "seconds": seconds + EPISODE_SLACK_SECONDS},
        "sandbox": {"mode": "required"},
        "task": "Run the workspace check suite once and report its result.",
    }


def read_check_result(log: Path, seconds: int, elapsed: float, status: int) -> Measurement:
    """The check tool's result, read from a scripted episode's log."""
    if not log.is_file():
        raise AdmissionError(f"{log} is absent; the scripted episode recorded no log")
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        data = event.get("data")
        if event.get("type") != "tool/result" or not isinstance(data, dict) or data.get("name") != CHECK_TOOL:
            continue
        value = data.get("value")
        if data.get("is_error") or not isinstance(value, dict):
            detail = json.dumps(data.get("failure")) if data.get("failure") is not None else str(data.get("rendered") or "")
            return Measurement(FOE, NO_RESULT_STATUS, elapsed, [], [], detail, f"the check tool ended in error: {detail[:400]}")
        output = str(value.get("stdout") or "") + str(value.get("stderr") or "")
        exit_status, note = value.get("exit_code"), None
        if value.get("timed_out"):
            exit_status, note = TIMED_OUT_STATUS, f"the check did not finish within {seconds} seconds"
        elif not isinstance(exit_status, int):
            exit_status, note = NO_RESULT_STATUS, f"the check tool reported no exit code in {log}"
        return Measurement(FOE, exit_status, elapsed, parse_failing_tests(output), parse_test_names(output), output, note)
    return Measurement(FOE, NO_RESULT_STATUS, elapsed, [], [], "", f"{log} holds no {CHECK_TOOL} result; the episode ended with status {status}")


def measure_foe(workspace: Path, scratch: Path, binary: Path, seconds: int) -> Measurement:
    """Run the check suite inside one scripted foe episode under a required sandbox, with no model.

    The episode calls the check tool once and then ends with a text
    response. The configured tool's arguments schema requires an `args`
    property, so the scripted call passes an empty list.
    """
    import host_runtime  # noqa: PLC0415
    from runtime_responses import call, done  # noqa: PLC0415

    scratch.mkdir(parents=True, exist_ok=True)
    wrapper = write_wrapper(scratch / "check", workspace)
    config = scratch / "episode.json"
    config.write_text(json.dumps(episode_document(workspace, wrapper, seconds), indent=2) + "\n", encoding="utf-8")

    def responder(request: dict[str, Any]) -> list[dict[str, Any]]:
        if any(message.get("role") == "tool" for message in request["messages"]):
            return [{"kind": "text", "delta": "The check tool reported its result."}, *done("end")]
        return [*call("admission-check", CHECK_TOOL, {"args": []}), *done("tool")]

    started = time.monotonic()
    status, episode = host_runtime.run(binary, config, scratch / "log", responder)
    return read_check_result(episode / EPISODE_LOG, seconds, time.monotonic() - started, status)


def task_fingerprint(task_dir: Path) -> str:
    """A digest of the files that define a task, so a directory rewritten by another writer is noticed."""
    parts: list[str] = []
    for relative in (protocol.TASK_FILE, f"{protocol.GRADER}/{protocol.WORKSPACE_PATCH}"):
        path = task_dir / relative
        parts.append(f"{relative}:{protocol.sha256_file(path) if path.is_file() else 'absent'}")
    return " ".join(parts)


def materialize_workspace(task_dir: Path, root: Path) -> protocol.Task:
    """Build a task's workspace, and nothing else, into a fresh root.

    The root is handed to sandboxed commands, so it must never hold the
    grader directory. `protocol.materialize` writes the workspace alone when
    it accepts a `parts` argument; otherwise the task is built into a
    staging root beside this one and its workspace is moved across, which
    leaves the grader outside every root a command sees.
    """
    if "parts" in inspect.signature(protocol.materialize).parameters:
        task = protocol.materialize(task_dir, root, protocol.WORKSPACE)
    else:
        staging = root.parent / (root.name + "-staging")
        if staging.exists():
            raise AdmissionError(f"{staging} exists; the staging root of {task_dir} must be fresh")
        task = protocol.materialize(task_dir, staging)
        root.mkdir(parents=True)
        shutil.move(str(staging / protocol.WORKSPACE), str(root / protocol.WORKSPACE))
        shutil.copy2(staging / protocol.TASK_FILE, root / protocol.TASK_FILE)
    grader = root / protocol.GRADER
    if grader.exists():
        raise AdmissionError(f"{grader} exists after materializing the workspace of {task_dir}; a root handed to a sandboxed command holds no grader")
    return task


def oracle_workspace(task_dir: Path) -> Path | None:
    """The grader's solved workspace overlay, when the task has one."""
    overlay = task_dir / protocol.GRADER / protocol.ORACLE / protocol.WORKSPACE
    return overlay if overlay.is_dir() else None


def apply_oracle_overlay(task_dir: Path, workspace: Path) -> bool:
    """Copy the grader's solved workspace over the materialized one; whether an overlay existed."""
    overlay = oracle_workspace(task_dir)
    if overlay is None:
        return False
    shutil.copytree(overlay, workspace, symlinks=True, dirs_exist_ok=True)
    return True


@dataclass
class TaskReport:
    """What one selected task measured, and the verdict that follows."""

    name: str
    directory: Path
    family: str
    class_name: str
    oracle_solves_workspace: bool
    crates: list[str] = field(default_factory=list)
    hidden_tests: list[str] = field(default_factory=list)
    verdict: str = INADMISSIBLE
    reasons: list[str] = field(default_factory=list)
    measurements: dict[str, Measurement] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.name,
            "directory": str(self.directory),
            "family": self.family,
            "class_name": self.class_name,
            "oracle_solves_workspace": self.oracle_solves_workspace,
            "crates_checked": list(self.crates),
            "hidden_test_count": len(self.hidden_tests),
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "environments": {name: measurement.to_dict(self.hidden_tests) for name, measurement in self.measurements.items()},
        }


@dataclass(frozen=True)
class Settings:
    """What one sweep runs with."""

    tasks: Path
    selected: tuple[str, ...]
    foe: Path
    out: Path
    seconds: int
    codex: str = CODEX_COMMAND


def select_tasks(settings: Settings) -> list[Path]:
    """The task directories the sweep measures, in name order; a named task the directory lacks is refused."""
    if not settings.tasks.is_dir():
        raise AdmissionError(f"{settings.tasks} is not a directory; --tasks names the directory the task directories sit in")
    present = {path.name: path for path in sorted(settings.tasks.iterdir()) if (path / protocol.TASK_FILE).is_file()}
    if not settings.selected:
        if not present:
            raise AdmissionError(f"{settings.tasks} holds no task directory with a {protocol.TASK_FILE}")
        return list(present.values())
    chosen: list[Path] = []
    for name in settings.selected:
        if name not in present:
            raise AdmissionError(f"{settings.tasks / name} is not a task directory with a {protocol.TASK_FILE}; --task names one that is")
        chosen.append(present[name])
    return chosen


def verdict_of(report: TaskReport) -> None:
    """Set a task's verdict and the reasons for it from its measurements."""
    failing = [name for name in ENVIRONMENTS if not report.measurements[name].passed]
    if not failing:
        report.verdict = ADMISSIBLE
        return
    report.verdict = INADMISSIBLE
    if not report.oracle_solves_workspace:
        report.reasons.append("the grader's oracle carries no solved workspace, so the check ran against the unsolved state")
    for name in failing:
        measurement = report.measurements[name]
        own, unrelated = split_by_hidden(measurement.failing, report.hidden_tests)
        if measurement.note is not None:
            detail = measurement.note
        elif not measurement.failing:
            detail = "no test named a failure, so a step other than a crate suite failed"
        else:
            detail = f"{len(own)} of the hidden grader's tests and {len(unrelated)} unrelated tests failed"
            if own:
                detail += f"; the hidden ones are {', '.join(own)}"
            if unrelated:
                detail += f"; the unrelated ones are {', '.join(unrelated)}"
        report.reasons.append(f"the {name} check exited {measurement.exit_status}: {detail}")


def check_task(task_dir: Path, settings: Settings, scratch: Path) -> TaskReport:
    """Materialize one task, solve it with the oracle, measure the three environments, and judge.

    The host runs first, because its output is the inventory of test names
    the other two environments are read against.
    """
    task = protocol.load(task_dir)
    report = TaskReport(task.name, task_dir, task.family, task.class_name, oracle_workspace(task_dir) is not None)
    report.hidden_tests = hidden_test_names(task_dir)
    root = scratch / "root"
    materialize_workspace(task_dir, root)
    workspace = root / protocol.WORKSPACE
    apply_oracle_overlay(task_dir, workspace)
    script = workspace / CHECK_SUITE
    if not script.is_file():
        raise AdmissionError(f"{script} is absent; a task's visible check is its {CHECK_SUITE}")
    report.crates = checked_crates(script.read_text(encoding="utf-8"), workspace_packages(workspace))
    report.measurements = {
        HOST: measure_host(workspace, settings.seconds),
        FOE: measure_foe(workspace, scratch / FOE, settings.foe, settings.seconds),
        CODEX: measure_codex(workspace, settings.seconds, settings.codex),
    }
    verdict_of(report)
    return report


def markdown_table(reports: Sequence[TaskReport]) -> str:
    """A table of one row per task: the crates its check runs, the three environments, and the verdict."""
    lines = ["| task | class | crates | host | foe | codex | verdict |", "|---|---|---|---|---|---|---|"]
    for report in reports:
        cells = []
        for name in ENVIRONMENTS:
            measurement = report.measurements.get(name)
            if measurement is None:
                cells.append("not run")
                continue
            if measurement.exit_status == TIMED_OUT_STATUS:
                status = "timed out"
            elif measurement.exit_status == NO_RESULT_STATUS:
                status = "no result"
            else:
                status = f"exit {measurement.exit_status}"
            own, unrelated = split_by_hidden(measurement.failing, report.hidden_tests)
            failing = f", {len(own)} hidden and {len(unrelated)} unrelated failing" if measurement.failing else ""
            cells.append(f"{status}, {measurement.seconds:.0f}s{failing}")
        crates = ", ".join(report.crates) if report.crates else "none"
        lines.append(f"| {report.name} | {report.class_name} | {crates} | " + " | ".join(cells) + f" | {report.verdict} |")
    return "\n".join(lines) + "\n"


def write_report(reports: Sequence[TaskReport], settings: Settings, seconds: float) -> tuple[Path, Path]:
    """Write the JSON report and the Markdown table under the output directory, and return both paths."""
    settings.out.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": SCHEMA_VERSION,
        "command": "check",
        "tasks_directory": str(settings.tasks),
        "foe_binary": str(settings.foe),
        "seconds_bound": settings.seconds,
        "wall_seconds": round(seconds, 1),
        "counts": {verdict: sum(1 for report in reports if report.verdict == verdict) for verdict in VERDICTS},
        "tasks": [report.to_dict() for report in reports],
    }
    report_path, table_path = settings.out / REPORT_FILE, settings.out / TABLE_FILE
    report_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    table_path.write_text(
        f"# Admission of {settings.tasks}\n\n"
        f"The sweep of {len(reports)} tasks took {seconds:.0f} seconds, with a bound of {settings.seconds} seconds on one environment's check.\n\n"
        + markdown_table(reports)
        + "\n"
        + "".join(f"- `{report.name}`: {reason}\n" for report in reports for reason in report.reasons),
        encoding="utf-8",
    )
    return report_path, table_path


def sweep(settings: Settings) -> tuple[list[TaskReport], float]:
    """Measure every selected task, one at a time, and report each verdict as it is reached."""
    started = time.monotonic()
    reports: list[TaskReport] = []
    for task_dir in select_tasks(settings):
        before = task_fingerprint(task_dir)
        scratch = settings.out / "scratch" / task_dir.name
        if scratch.exists():
            shutil.rmtree(scratch)
        scratch.mkdir(parents=True)
        try:
            report = check_task(task_dir, settings, scratch)
        except (AdmissionError, OSError, ValueError) as error:
            task = protocol.load(task_dir) if (task_dir / protocol.TASK_FILE).is_file() else None
            report = TaskReport(
                task_dir.name,
                task_dir,
                task.family if task is not None else "unknown",
                task.class_name if task is not None else "unknown",
                oracle_workspace(task_dir) is not None,
            )
            report.reasons.append(f"{task_dir} could not be measured: {error}")
        if task_fingerprint(task_dir) != before:
            report.verdict = CHANGED
            report.reasons.append(f"{task_dir} was rewritten by another writer while it was measured, so its result is discarded")
        reports.append(report)
        print(f"{report.name}: {report.verdict}", flush=True)
    return reports, time.monotonic() - started


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="admission.py", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check",))
    parser.add_argument("--tasks", required=True, help="the directory the task directories sit in")
    parser.add_argument("--task", action="append", default=[], help="one task directory name to select; repeatable")
    parser.add_argument("--foe", default=DEFAULT_FOE, help="the foe binary the scripted episode runs")
    parser.add_argument("--out", default=DEFAULT_OUT, help="the directory the scratch roots and the report are written under")
    parser.add_argument("--seconds", type=int, default=DEFAULT_SECONDS, help="how long one environment's check may run before it is recorded as unfinished")
    parser.add_argument("--codex", default=CODEX_COMMAND, help="the Codex CLI command whose sandbox one environment uses")
    return parser.parse_args(list(argv))


def settings_of(arguments: argparse.Namespace) -> Settings:
    """The sweep settings the arguments name, with every path resolved and every binary checked."""
    out = Path(str(home()) + arguments.out[1:] if arguments.out.startswith("~") else arguments.out).resolve()
    binary = Path(arguments.foe).resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise AdmissionError(f"{binary} is not an executable file; --foe names the binary the scripted episode runs")
    if arguments.seconds <= 0:
        raise AdmissionError(f"--seconds is {arguments.seconds}; expected a positive number of seconds")
    if shutil.which(arguments.codex) is None:
        raise AdmissionError(f"{arguments.codex} is absent from the search path; --codex names the command whose sandbox one environment uses")
    return Settings(Path(arguments.tasks).resolve(), tuple(arguments.task), binary, out, arguments.seconds, arguments.codex)


def main(argv: Sequence[str]) -> int:
    try:
        arguments = parse_arguments(argv)
        settings = settings_of(arguments)
        reports, seconds = sweep(settings)
        report_path, table_path = write_report(reports, settings, seconds)
    except AdmissionError as error:
        print(f"admission.py: {error}", file=sys.stderr)
        return 2
    print(markdown_table(reports))
    print(f"{report_path}\n{table_path}")
    return 0 if all(report.verdict == ADMISSIBLE for report in reports) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
