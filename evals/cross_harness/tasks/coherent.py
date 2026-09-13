#!/usr/bin/python3
"""Author the constructed coherent controls of the teams family.

A `coherent` task is a control: one change that no division into workers
improves, so one agent doing the work alone is the right answer and an arm
that spawns workers on it has divided where dividing does not pay. What
makes a task a control here is measured rather than asserted. A worker's
write grant is a directory, and the delegating node of the teams graph is
told to give each worker a directory no other worker writes. Every file the
change of these tasks touches lies in one directory, so no two workers can
be given directories that do not overlap: the division the instruction
requires does not exist. `change_directory` computes that directory and
refuses a construction whose change spreads over more, and `metadata.division`
records it. `tasks/teams.py` authors the `fan-out` tasks, whose units do lie
in directories that do not overlap and whose metadata records that instead.

The change of each task still has parts, one per module of the crate it
works in, and the grade gives each part its own verdict so that a partial
change is visible. The parts are recorded under `metadata.units` because
`report.py` reads per-part verdicts under that key; they are not units a
delegation can be given, which is the point of the control. The task text
names no part, so an arm has to decide for itself how the work divides.

Two tasks are defined, both inside a crate whose own test suite passes
inside a kernel sandbox, so that the visible check can pass in every
environment an arm runs it in.

`bounded-result-names-its-bound` works in `crates/code`. Each of the six
built-in coding tools cuts a result short when one of the crate's bounds is
reached, and the change makes a cut result name that bound under `bound` in
its canonical value. The six names come from one table, `BOUNDS` in
`crates/code/src/lib.rs`, which is the shared element. A part is one tool
module.

`module-cites-its-specification` works in `crates/contract`. Each module of
the crate implements a section of one specification document and states the
citation in its module documentation, and `SPECIFICATIONS` in
`crates/contract/src/lib.rs` holds one row per module. The fixture carries
the registry with `document` in it, so the change applies the convention to
the crate's six remaining modules. A part is one module.

Every task is a recipe. The workspace is the base commit's tree with the
fixture edits, the check suite, and the raised line ceilings applied; the
diff is recorded as `grader/workspace.patch` and the workspace copy is
removed, so a task directory holds no tree. Every scripted edit asserts
that its source stands exactly once in the file it edits.

The grader holds the hidden test files, an oracle overlay of the solved
implementation files, and two corruptions of that solved workspace:

    revert-one-unit         one part restored to its fixture form. That
                            part's test fails and the integration test passes,
                            which is what shows the parts are independent.
    rename-shared-element   one part made to name a shared element other than
                            the one the table gives it. The integration test
                            fails and no part's test fails, which is what shows
                            that a part disagreeing with the table is caught
                            by the integration test alone.

The grade script restores the hidden tests into a copy of the workspace,
runs the crate's suite once, reads each part's verdict from its own named
test, reads the whole change's verdict from the integration test, and then
runs clippy and the line budgets. It prints one line `units: {name: passed}`
on standard error and writes the same object to `units.json` beside itself,
which `tasks/teams.py:read_units` reads.

    /usr/bin/python3 evals/cross_harness/tasks/coherent.py emit --task NAME --repo . --out DIR
    /usr/bin/python3 evals/cross_harness/tasks/coherent.py verify --task DIR --scratch DIR
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import feature_removal as removal  # noqa: E402
import protocol  # noqa: E402
import teams  # noqa: E402
from protocol import GRADER, WORKSPACE, Task  # noqa: E402

FAMILY, CLASS_NAME = "teams", "coherent"
CHECKS_SCRIPT = removal.CHECKS_SCRIPT
PROTECTED: tuple[str, ...] = teams.PROTECTED
UNITS_FILE = teams.UNITS_FILE
UNITS_PREFIX = teams.UNITS_PREFIX
REVERT_CORRUPTION = teams.REVERT_CORRUPTION
RENAME_CORRUPTION = teams.RENAME_CORRUPTION
SPECIFICATION_FILE = removal.SPECIFICATION_FILE
GRADE_TIMEOUT_SECONDS = removal.GRADE_TIMEOUT_SECONDS
# The evaluation's own directory, relative to the repository it evaluates. A
# base tree holding it would show an arm the classes, the grading rules, and
# the construction its own task came from, so a base commit whose tree holds
# it is refused.
EVALUATION_ROOT = teams.EVALUATION_ROOT

# The budget of every control of this module, which is the budget the
# harvested fan-out tasks carry. The change of each task lives in one crate,
# so the work does not grow with the repository the way a sweep does, and a
# run stays comparable across the classes of the family.
BUDGET: dict[str, int] = {"model_calls": 120, "input_tokens": 4_000_000, "output_tokens": 180_000, "seconds": 4500}


# One scripted replacement, and the edits that move one line ceiling. Both
# constructed classes of this family state their changes as these, so they
# live with the fan-out authoring tool and are used from here.
Edit = teams.Edit
ceiling_edits = teams.ceiling_edits


@dataclass(frozen=True)
class Unit:
    """One part of the change: the file it touches and the hidden test that judges it."""

    name: str
    file: str
    test_file: str
    test_name: str


@dataclass(frozen=True)
class Construction:
    """One constructed control: the crate, the parts, the shared element, and every edit that makes the three states."""

    name: str
    crate: str
    package: str
    subject: str
    units: tuple[Unit, ...]
    interface_paths: tuple[str, ...]
    integration_test: str
    # The fixture is the base tree with these edits; the oracle overlay is the
    # fixture with these further edits applied to the implementation files.
    fixture_edits: tuple[Edit, ...]
    oracle_edits: tuple[Edit, ...]
    # The hidden test files, as the fixture's file plus this appended text.
    test_appends: tuple[tuple[str, str], ...]
    # The line ceilings the fixture raises, so that the solved workspace fits
    # under `scripts/loc.sh` without a ceiling commit of the arm's own.
    ceilings: tuple[Edit, ...]
    text: str
    revert_unit: str
    # The corruption that makes one unit name a shared element the table does
    # not give it: the unit, and the replacement inside that unit's file.
    rename: Edit = field(default=None)  # type: ignore[assignment]
    rename_unit: str = ""
    # A substring of the test names the visible check leaves out, and why.
    # The hidden grade runs the whole suite on the host, where no sandbox
    # stands in the way.
    check_skip: str = ""
    check_skip_reason: str = ""


# ---- the check suite and the grade script ---------------------------------------


def checks_script(package: str, skip: str = "", reason: str = "") -> str:
    """The visible check: the crate's tests, clippy on the same crate, and the line budgets.

    `skip` names a substring of the test names the suite leaves out, and
    `reason` states why. A test that a kernel sandbox breaks measures the
    sandbox rather than the change, and both harnesses under comparison run
    their commands inside one, so this suite has to pass in each of them.
    """
    filtered = f" -- --skip {skip}" if skip else ""
    note = f"# {reason}\n" if reason else ""
    return (
        "#!/bin/sh\n"
        "# The check this task is judged on: the crate's tests, clippy with\n"
        "# warnings denied on the same crate, and the line budgets.\n"
        + note
        + "set -eu\n"
        'cd "$(dirname "$0")/.."\n'
        f"cargo test -p {package}{filtered}\n"
        f"cargo clippy -p {package} --all-targets -- -D warnings\n"
        "scripts/loc.sh\n"
    )


GRADE_WRAPPER = removal.GRADE_WRAPPER

_GRADE_TEMPLATE = r'''#!/usr/bin/python3
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
COMMAND_SECONDS = __COMMAND_SECONDS__
UNITS_PREFIX = "__UNITS_PREFIX__"
UNITS_FILE = "__UNITS_FILE__"
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
'''

assert _GRADE_TEMPLATE.count("__COMMAND_SECONDS__") == 1, "the grade template names the command budget once"
assert _GRADE_TEMPLATE.count("__UNITS_PREFIX__") == 1, "the grade template names the units prefix once"
assert _GRADE_TEMPLATE.count("__UNITS_FILE__") == 1, "the grade template names the units file once"
GRADE_SCRIPT = (
    _GRADE_TEMPLATE.replace("__COMMAND_SECONDS__", str(removal.GRADE_COMMAND_SECONDS))
    .replace("__UNITS_PREFIX__", UNITS_PREFIX)
    .replace("__UNITS_FILE__", UNITS_FILE)
)


CORRUPTION_SCRIPT = r'''#!/usr/bin/python3
"""Apply one recorded mutation of the solved workspace; every replacement asserts that its source stands exactly once."""

import json
import pathlib
import sys

here = pathlib.Path(__file__).resolve().parent
workspace = pathlib.Path(sys.argv[1])
for edit in json.loads((here / "edits.json").read_text(encoding="utf-8")):
    target = workspace / edit["path"]
    if not target.is_file():
        raise SystemExit(f"{target}: absent, so the corruption has nothing to change")
    text = target.read_text(encoding="utf-8")
    found = text.count(edit["old"])
    if found != 1:
        raise SystemExit(f"{target}: the corruption's source stands {found} times; it stands exactly once in the solved workspace")
    target.write_text(text.replace(edit["old"], edit["new"]), encoding="utf-8")
'''


# ---- the ceilings the fixtures raise --------------------------------------------


# The coding tools take the change of the first task, and the group over the
# coding tools and team coordination holds no room at the base commit, so the
# fixture raises both. A group ceiling may not exceed the sum of the ceilings
# it bounds, which 1,975 and 940 make 2,915.
TOOLS_CEILING = ceiling_edits(
    "tools     ",
    "coding tools",
    (
        ("AGENTS.md", "`code` under CEILING"),
        ("docs/design.md", "The coding tools in `crates/code` stay under CEILING lines."),
    ),
    1900,
    1975,
) + ceiling_edits(
    "tools+team ",
    None,
    (
        ("AGENTS.md", "Coding tools and team coordination together stay under\n  CEILING."),
        ("README.md", "Coding tools and team coordination together stay under CEILING lines."),
        ("docs/design.md", "together stay under CEILING lines."),
    ),
    2765,
    2915,
)
CONTRACT_CEILING = teams.CONTRACT_CEILING


# ---- the bound-naming change in crates/code -------------------------------------

_BOUNDS_ANCHOR = """/// Characters kept of each of the interpreter's own output streams.
pub const PYTHON_DIAGNOSTIC_MAX_CHARS: usize = 4_096;
"""

_BOUNDS_TABLE = _BOUNDS_ANCHOR + """
/// Every bound these tools enforce that can cut one result short, by the
/// name a cut result gives it and by what the bound holds. A result the
/// crate cut names its bound under `bound` in the canonical value, and a
/// complete result leaves that field null, so a caller learns what was lost
/// without reading the notice text. A result carries no name outside this
/// table. docs/tools.md states the value of each bound.
pub const BOUNDS: &[(&str, &str)] = &[
    ("bash.output", "the lines one bash result keeps"),
    ("compose_tools.source", "the bytes of one compose_tools source"),
    ("edit.diff", "the diff lines one edit renders"),
    ("grep.results", "the matches and result lines one grep collects"),
    ("read.window", "the lines, entries, and characters one read shows"),
    ("session.output", "the lines one session result keeps"),
];

/// Whether a name is one of [`BOUNDS`].
pub fn is_bound(name: &str) -> bool {
    BOUNDS.iter().any(|(bound, _)| *bound == name)
}
"""

BOUNDS_ORACLE: tuple[Edit, ...] = (
    Edit("crates/code/src/lib.rs", _BOUNDS_ANCHOR, _BOUNDS_TABLE),
    Edit(
        "crates/code/src/read.rs",
        """        json!({"path": path.display().to_string(), "offset": offset, "total_entries": total,
               "shown": count, "truncated": truncated, "entries": items}),
""",
        """        json!({"path": path.display().to_string(), "offset": offset, "total_entries": total,
               "shown": count, "truncated": truncated, "entries": items,
               "bound": truncated.then_some("read.window")}),
""",
    ),
    Edit(
        "crates/code/src/read.rs",
        """                "truncated": truncated,
                "content": content,
""",
        """                "truncated": truncated,
                "bound": truncated.then_some("read.window"),
                "content": content,
""",
    ),
    Edit(
        "crates/code/src/grep.rs",
        """                "complete": complete,
                "hits": collected.hits,
""",
        """                "complete": complete,
                "bound": collected.stopped_at.map(|_| "grep.results"),
                "hits": collected.hits,
""",
    ),
    Edit(
        "crates/code/src/edit.rs",
        """                "removed": d.removed,
                "diff": d.text,
""",
        """                "removed": d.removed,
                "bound": (d.text.lines().count() > EDIT_DIFF_MAX_LINES).then_some("edit.diff"),
                "diff": d.text,
""",
    ),
    Edit(
        "crates/code/src/bash.rs",
        """                "truncated": output.truncated,
                "spill": output.spill,""",
        """                "truncated": output.truncated,
                "bound": output.truncated.then_some("bash.output"),
                "spill": output.spill,""",
    ),
    Edit(
        "crates/code/src/session.rs",
        """            "truncated": output.truncated, "spill": output.spill,
""",
        """            "truncated": output.truncated, "spill": output.spill,
            "bound": output.truncated.then_some("session.output"),
""",
    ),
    Edit(
        "crates/code/src/python.rs",
        """            return ToolValue::failed(
                foe_core::ToolFailureCode::LimitExceeded,""",
        """            let mut refused = ToolValue::failed(
                foe_core::ToolFailureCode::LimitExceeded,""",
    ),
    Edit(
        "crates/code/src/python.rs",
        """                json!({ "limit": "source_bytes", "actual": a.source.len(), "maximum": PYTHON_SOURCE_MAX_BYTES }),
            );
""",
        """                json!({ "limit": "source_bytes", "actual": a.source.len(), "maximum": PYTHON_SOURCE_MAX_BYTES }),
            );
            refused.value["bound"] = json!("compose_tools.source");
            return refused;
""",
    ),
)

_BOUNDS_TESTS: dict[str, str] = {
    "crates/code/src/read_test.rs": '''
/// docs/tools.md `read`: a result the crate cut names the bound that cut it,
/// and a complete result names none.
#[tokio::test]
async fn a_cut_read_names_its_bound_and_a_complete_read_names_none() {
    let fx = Fixture::new();
    let text: String = (1..=2500).map(|i| format!("line {i}\\n")).collect();
    fx.write("big.txt", &text);
    let v = read(&fx, json!({"path": "big.txt"})).await;
    assert_eq!(v.value["truncated"], true, "{}", v.value);
    let named = v.value["bound"].as_str().unwrap_or_default();
    assert!(!named.is_empty(), "a cut read names no bound: {}", v.value);
    fx.write("small.txt", "one\\ntwo\\n");
    let v = read(&fx, json!({"path": "small.txt"})).await;
    assert!(v.value["bound"].is_null(), "a complete read names a bound: {}", v.value);
}
''',
    "crates/code/src/grep_test.rs": '''
/// docs/tools.md `grep`: a search the crate stopped at a collection bound
/// names that bound, and a search that reached the end of the tree names none.
#[tokio::test]
async fn a_stopped_search_names_its_bound_and_a_complete_search_names_none() {
    let fx = Fixture::new();
    let text: String = (1..=10_001).map(|i| format!("alpha {i}\\n")).collect();
    fx.write("many.txt", &text);
    let v = grep(&fx, json!({"pattern": "alpha", "limit": 1})).await;
    assert_eq!(v.value["complete"], false, "{}", v.value);
    let named = v.value["bound"].as_str().unwrap_or_default();
    assert!(!named.is_empty(), "a stopped search names no bound: {}", v.value);
    let fx = Fixture::new();
    fx.write("few.txt", "alpha\\n");
    let v = grep(&fx, json!({"pattern": "alpha"})).await;
    assert!(v.value["bound"].is_null(), "a complete search names a bound: {}", v.value);
}
''',
    "crates/code/src/edit_test.rs": '''
/// docs/tools.md `edit`: a rendering the crate cut at the diff bound names
/// that bound, and a diff shown whole names none.
#[tokio::test]
async fn a_cut_diff_names_its_bound_and_a_whole_diff_names_none() {
    let fx = Fixture::new();
    let before: String = (1..=300).map(|i| format!("line {i}\\n")).collect();
    let after: String = (1..=300).map(|i| format!("row {i}\\n")).collect();
    fx.write("wide.txt", &before);
    let v = edit(&fx, json!({"path": "wide.txt", "edits": [replace(&before, &after)]})).await;
    assert!(!v.is_error, "{v:?}");
    let named = v.value["bound"].as_str().unwrap_or_default();
    assert!(!named.is_empty(), "a cut diff names no bound: {}", v.value);
    fx.write("narrow.txt", "alpha\\n");
    let v = edit(&fx, json!({"path": "narrow.txt", "edits": [replace("alpha", "beta")]})).await;
    assert!(v.value["bound"].is_null(), "a whole diff names a bound: {}", v.value);
}
''',
    "crates/code/src/bash_test.rs": '''
/// docs/tools.md `bash`: a result whose output the crate cut names the bound
/// that cut it, and a result that kept every line names none.
#[tokio::test]
async fn a_cut_bash_result_names_its_bound_and_a_whole_one_names_none() {
    let fx = Fixture::new();
    let long: String = (1..=2500).map(|i| format!("line {i}\\n")).collect();
    let exec = Arc::new(FakeExecutor::new(result(0, &long, "")));
    let v = Bash::new().call(json!({"command": "seq 2500"}), &ctx_with_executor(&fx, exec)).await;
    assert_eq!(v.value["truncated"], true, "{}", v.value);
    let named = v.value["bound"].as_str().unwrap_or_default();
    assert!(!named.is_empty(), "a cut bash result names no bound: {}", v.value);
    let exec = Arc::new(FakeExecutor::new(result(0, "one\\n", "")));
    let v = Bash::new().call(json!({"command": "echo one"}), &ctx_with_executor(&fx, exec)).await;
    assert!(v.value["bound"].is_null(), "a whole bash result names a bound: {}", v.value);
}
''',
    "crates/code/src/session_test.rs": '''
/// docs/tools.md `session`: a poll whose output the crate cut names the bound
/// that cut it, and a poll that kept every line names none.
#[tokio::test]
async fn a_cut_poll_names_its_bound_and_a_whole_poll_names_none() {
    let fx = Fixture::new();
    let long: String = (1..=2500).map(|i| format!("line {i}\\n")).collect();
    let sessions = Arc::new(FakeSessions::new(alive(1, "server")).with_output(&long, ""));
    let v = Session::new().call(json!({"action": "poll", "session": 1}), &ctx_with_sessions(&fx, sessions)).await;
    assert_eq!(v.value["truncated"], true, "{}", v.value);
    let named = v.value["bound"].as_str().unwrap_or_default();
    assert!(!named.is_empty(), "a cut poll names no bound: {}", v.value);
    let sessions = Arc::new(FakeSessions::new(alive(1, "server")).with_output("one\\n", ""));
    let v = Session::new().call(json!({"action": "poll", "session": 1}), &ctx_with_sessions(&fx, sessions)).await;
    assert!(v.value["bound"].is_null(), "a whole poll names a bound: {}", v.value);
}
''',
    "crates/code/src/python_test.rs": '''
/// docs/tools.md `compose_tools`: a source the crate refused at the source
/// bound names that bound.
#[tokio::test]
async fn a_refused_source_names_its_bound() {
    let source = "#".repeat(PYTHON_SOURCE_MAX_BYTES + 1);
    let v = run_source(&source, Arc::new(FakeComposer::default())).await;
    assert!(v.is_error, "{v:?}");
    let named = v.value["bound"].as_str().unwrap_or_default();
    assert!(!named.is_empty(), "a refused source names no bound: {}", v.value);
}
''',
    "crates/code/src/lib_test.rs": '''
/// One vocabulary of bounds: every name a cut result carries stands in
/// [`super::BOUNDS`], so a tool that names a bound the table does not hold
/// is a tool no caller can interpret.
#[tokio::test]
async fn every_bound_a_cut_result_names_stands_in_the_table() {
    use crate::testing::{ctx, ctx_with_executor, ctx_with_sessions, FakeExecutor};
    use foe_core::{CallCtx, ExecResult, SessionOutput, SessionRequest, SessionStatus, Tool, ToolValue};
    use serde_json::json;
    use std::sync::Arc;

    struct OneSession(std::sync::Mutex<SessionOutput>);
    impl foe_core::Sessions for OneSession {
        fn start(&self, _: SessionRequest) -> Result<SessionStatus, foe_core::CapError> {
            Ok(status())
        }
        fn take_output(&self, _: u64) -> Result<(SessionStatus, SessionOutput), foe_core::CapError> {
            Ok((status(), std::mem::take(&mut *self.0.lock().unwrap())))
        }
        fn write_stdin(&self, _: u64, _: &[u8]) -> Result<SessionStatus, foe_core::CapError> {
            Ok(status())
        }
        fn signal(&self, _: u64, _: &str) -> Result<SessionStatus, foe_core::CapError> {
            Ok(status())
        }
        fn stop(&self, _: u64) -> Result<SessionStatus, foe_core::CapError> {
            Ok(status())
        }
        fn settle(&self) -> Vec<foe_core::SessionSettlement> {
            Vec::new()
        }
    }
    fn status() -> SessionStatus {
        SessionStatus { id: 1, name: "server".into(), alive: true, exit_code: None, seconds: 3 }
    }

    #[derive(Default)]
    struct NoComposer;
    #[async_trait::async_trait]
    impl foe_core::Composer for NoComposer {
        async fn call(
            &self,
            _: &str,
            _: serde_json::Value,
        ) -> Result<(serde_json::Value, bool), foe_core::RuntimeError> {
            Ok((json!({}), false))
        }
    }

    fn executor(stdout: &str) -> Arc<FakeExecutor> {
        Arc::new(FakeExecutor::new(ExecResult {
            exit_code: Some(0),
            stdout: stdout.into(),
            stderr: Vec::new(),
            timed_out: false,
            duration: std::time::Duration::from_millis(10),
        }))
    }

    let long: String = (1..=2500).map(|i| format!("line {i}\\n")).collect();
    let fx = crate::testing::Fixture::new();
    fx.write("big.txt", &(1..=2500).map(|i| format!("line {i}\\n")).collect::<String>());
    fx.write("many.txt", &(1..=10_001).map(|i| format!("alpha {i}\\n")).collect::<String>());
    let before: String = (1..=300).map(|i| format!("line {i}\\n")).collect();
    let after: String = (1..=300).map(|i| format!("row {i}\\n")).collect();
    fx.write("wide.txt", &before);

    let python_ctx = |handle: Arc<FakeExecutor>| -> CallCtx {
        let mut c = ctx_with_executor(&fx, handle);
        c.composer = Some(Arc::new(NoComposer));
        c
    };
    let sessions = Arc::new(OneSession(std::sync::Mutex::new(SessionOutput {
        stdout: long.clone().into_bytes(),
        stderr: Vec::new(),
    })));

    let mut seen: Vec<(String, ToolValue)> = Vec::new();
    seen.push(("read".into(), crate::read::Read::new().call(json!({"path": "big.txt"}), &ctx(&fx)).await));
    seen.push(("grep".into(), crate::grep::Grep::new().call(json!({"pattern": "alpha", "limit": 1}), &ctx(&fx)).await));
    seen.push((
        "edit".into(),
        crate::edit::Edit::new()
            .call(json!({"path": "wide.txt", "edits": [{"old_text": before, "new_text": after}]}), &ctx(&fx))
            .await,
    ));
    seen.push((
        "bash".into(),
        crate::bash::Bash::new().call(json!({"command": "seq 2500"}), &ctx_with_executor(&fx, executor(&long))).await,
    ));
    seen.push((
        "session".into(),
        crate::session::Session::new()
            .call(json!({"action": "poll", "session": 1}), &ctx_with_sessions(&fx, sessions))
            .await,
    ));
    seen.push((
        foe_core::COMPOSING_TOOL.into(),
        crate::python::Python::new()
            .call(json!({"source": "#".repeat(super::PYTHON_SOURCE_MAX_BYTES + 1)}), &python_ctx(executor("")))
            .await,
    ));

    let mut named = Vec::new();
    for (tool, value) in &seen {
        let Some(bound) = value.value.get("bound").and_then(|b| b.as_str()) else { continue };
        assert!(super::is_bound(bound), "{tool} names the bound {bound:?}, which crates/code/src/lib.rs BOUNDS lacks");
        named.push(bound.to_owned());
    }
    assert!(!named.is_empty(), "no tool named a bound, so the table was never exercised");
    let mut sorted: Vec<&(&str, &str)> = super::BOUNDS.iter().collect();
    sorted.sort();
    let mut unique = sorted.clone();
    unique.dedup_by_key(|entry| entry.0);
    assert_eq!(sorted.len(), unique.len(), "BOUNDS names one bound twice");
}
''',
}

BOUNDS_TEXT = """\
Every built-in coding tool in crates/code cuts one result short when a bound \
of the crate is reached: read stops at the lines, entries, and characters one \
call shows, grep stops at the matches and result lines it collects, edit cuts \
the diff it renders, bash and session cut the output they keep, and \
compose_tools refuses a source above the source bound. A result the crate cut \
names the bound that cut it under `bound` in the canonical value, and a \
complete result leaves that field null.

The names come from one table, `BOUNDS` in crates/code/src/lib.rs, which holds \
every bound name beside what the bound holds, and `is_bound` reports whether a \
name stands in that table. The six names are `read.window`, `grep.results`, \
`edit.diff`, `bash.output`, `session.output`, and `compose_tools.source`. A \
result carries no name the table lacks.

checks/run.sh runs the check the whole change is judged on: the tests of \
foe-code, clippy with warnings denied on the same crate, and scripts/loc.sh.
"""

BOUNDS = Construction(
    name="bounded-result-names-its-bound",
    crate="crates/code",
    package="foe-code",
    subject="Name the bound that cut a built-in coding tool's result",
    units=(
        Unit("read", "crates/code/src/read.rs", "crates/code/src/read_test.rs", "read::tests::a_cut_read_names_its_bound_and_a_complete_read_names_none"),
        Unit(
            "grep",
            "crates/code/src/grep.rs",
            "crates/code/src/grep_test.rs",
            "grep::tests::a_stopped_search_names_its_bound_and_a_complete_search_names_none",
        ),
        Unit("edit", "crates/code/src/edit.rs", "crates/code/src/edit_test.rs", "edit::tests::a_cut_diff_names_its_bound_and_a_whole_diff_names_none"),
        Unit(
            "bash",
            "crates/code/src/bash.rs",
            "crates/code/src/bash_test.rs",
            "bash::tests::a_cut_bash_result_names_its_bound_and_a_whole_one_names_none",
        ),
        Unit(
            "session",
            "crates/code/src/session.rs",
            "crates/code/src/session_test.rs",
            "session::tests::a_cut_poll_names_its_bound_and_a_whole_poll_names_none",
        ),
        Unit("compose_tools", "crates/code/src/python.rs", "crates/code/src/python_test.rs", "python::tests::a_refused_source_names_its_bound"),
    ),
    interface_paths=("crates/code/src/lib.rs",),
    integration_test="tests::every_bound_a_cut_result_names_stands_in_the_table",
    fixture_edits=(),
    oracle_edits=BOUNDS_ORACLE,
    test_appends=tuple(_BOUNDS_TESTS.items()),
    ceilings=tuple(TOOLS_CEILING),
    text=BOUNDS_TEXT,
    revert_unit="grep",
    check_skip="inner_call",
    check_skip_reason=(
        "The three compose_tools tests whose names hold `inner_call` dispatch an inner tool call over the\n"
        "# interpreter's Unix socket pair, which a kernel sandbox may deny, so this suite leaves them out."
    ),
    rename=Edit(
        "crates/code/src/read.rs",
        '"bound": truncated.then_some("read.window"),\n                "content": content,',
        '"bound": truncated.then_some("read.lines"),\n                "content": content,',
    ),
    rename_unit="read",
)


# ---- the specification-citation change in crates/contract -----------------------

_SPECIFICATION_ANCHOR = "// ---- errors --------------------------------------------------------------------\n"

_SPECIFICATION_REGISTRY = (
    """// ---- specifications ------------------------------------------------------------

/// The specification each module of this crate implements: the module's
/// name, the document, and the section heading within it. A module states
/// the same citation in a `Specification:` line of its module documentation,
/// and that section stands as a heading in that document, so a section
/// renamed in a document fails this crate's tests rather than leaving a
/// citation that points nowhere.
pub const SPECIFICATIONS: &[(&str, &str, &str)] = &[("document", "docs/config.md", "Keys")];

/// The document and section a module of this crate implements.
pub fn specification(module: &str) -> Option<(&'static str, &'static str)> {
    SPECIFICATIONS.iter().find(|(name, _, _)| *name == module).map(|(_, document, section)| (*document, *section))
}

"""
    + _SPECIFICATION_ANCHOR
)

# Each module's last documentation line, and the specification the oracle cites.
_CITATIONS: dict[str, tuple[str, str, str]] = {
    "document": ("//! form, for example `child_contracts.survey.grants.read[0]`.", "docs/config.md", "Keys"),
    "fingerprint": (
        "//! running binary belongs to `foe_core::fingerprint::runtime_info`.",
        "docs/design.md",
        "Execution contracts and fingerprints",
    ),
    "harness_text": ("//! template text, with the placeholders, is what fingerprint hashes.", "docs/design.md", "Tools"),
    "inspect": ("//! resolved episode contract.", "docs/design.md", "The command line"),
    "schema": ("//! schema it does not read.", "docs/config.md", "JSON Schema subset"),
    "tools": ("//! refused here, before any tool is constructed.", "docs/design.md", "Tools"),
    "workflow": ("//! document, validation, and fingerprint need.", "docs/workflow.md", "The graph"),
}
# The module the fixture already cites, so that the change extends a
# convention the crate already follows rather than introducing one.
_SEEDED = "document"
_CITED_MODULES: tuple[str, ...] = tuple(name for name in sorted(_CITATIONS) if name != _SEEDED)


def _citation_edit(module: str) -> Edit:
    anchor, document, section = _CITATIONS[module]
    return Edit(f"crates/contract/src/{module}.rs", anchor + "\n", f'{anchor}\n//!\n//! Specification: {document} "{section}".\n')


_SPECIFICATION_TABLE = "pub const SPECIFICATIONS: &[(&str, &str, &str)] = &[\n" + "".join(
    f'    ("{module}", "{_CITATIONS[module][1]}", "{_CITATIONS[module][2]}"),\n' for module in sorted(_CITATIONS)
) + "];"

_SPECIFICATION_TESTS = '''
// ---- specification citations ---------------------------------------------------

/// The documents AGENTS.md "Read first" names, the design and the five
/// specifications, which are the only documents a module may cite.
const SPECIFICATION_DOCUMENTS: [&str; 6] = [
    "docs/compaction.md",
    "docs/config.md",
    "docs/log-format.md",
    "docs/protocol.md",
    "docs/design.md",
    "docs/workflow.md",
];

fn source_directory() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src")
}

fn repository_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// The modules of this crate: every `.rs` file under `src` that is neither
/// the crate root nor a test file.
fn crate_modules() -> Vec<String> {
    let directory = source_directory();
    let entries = std::fs::read_dir(&directory).unwrap_or_else(|e| panic!("{}: {e}", directory.display()));
    let mut names: Vec<String> = entries
        .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
        .filter(|name| name.ends_with(".rs") && name != "lib.rs" && !name.ends_with("_test.rs"))
        .map(|name| name.trim_end_matches(".rs").to_owned())
        .collect();
    names.sort();
    names
}

/// The document and section a module's documentation cites, or None when it
/// cites none. A module states at most one citation.
fn stated_citation(module: &str) -> Option<(String, String)> {
    let path = source_directory().join(format!("{module}.rs"));
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let mut found: Vec<(String, String)> = Vec::new();
    for line in text.lines() {
        let Some(rest) = line.trim().strip_prefix("//! Specification: ") else { continue };
        let cited = rest
            .trim()
            .strip_suffix('.')
            .unwrap_or_else(|| panic!("{}: {line:?} does not end with a period", path.display()));
        let (document, quoted) = cited
            .split_once(' ')
            .unwrap_or_else(|| panic!("{}: {line:?} names a document and no section", path.display()));
        let section = quoted
            .strip_prefix('"')
            .and_then(|rest| rest.strip_suffix('"'))
            .unwrap_or_else(|| panic!("{}: {line:?} does not quote its section", path.display()));
        found.push((document.to_owned(), section.to_owned()));
    }
    assert!(found.len() <= 1, "crates/contract/src/{module}.rs states {} citations; a module states one", found.len());
    found.pop()
}

/// Whether the document holds the section as a heading of any level.
fn heading_stands(document: &str, section: &str) -> bool {
    let path = repository_root().join(document);
    let Ok(text) = std::fs::read_to_string(&path) else { return false };
    text.lines().any(|line| line.starts_with('#') && line.trim_start_matches('#').trim() == section)
}

/// One module states a citation, to a specification document, whose section stands there.
fn cites_a_standing_section(module: &str) {
    let (document, section) = stated_citation(module)
        .unwrap_or_else(|| panic!("crates/contract/src/{module}.rs states no `Specification:` line"));
    assert!(
        SPECIFICATION_DOCUMENTS.contains(&document.as_str()),
        "crates/contract/src/{module}.rs cites {document}, which AGENTS.md \\"Read first\\" does not name"
    );
    assert!(
        heading_stands(&document, &section),
        "crates/contract/src/{module}.rs cites {document} \\"{section}\\", and that document holds no such heading"
    );
}
'''

_SPECIFICATION_UNIT_TEST = '''
/// AGENTS.md "Read first": the module states the specification it implements,
/// and the section it names stands in that document.
#[test]
fn the_{module}_module_cites_a_standing_section() {{
    cites_a_standing_section("{module}");
}}
'''

_SPECIFICATION_INTEGRATION = '''
/// One table of citations. `SPECIFICATIONS` names every module of the crate,
/// every module that states a citation states the one the table gives it, and
/// every section the table names stands in its document.
#[test]
fn every_module_agrees_with_the_specification_table() {
    let modules = crate_modules();
    let mut listed: Vec<String> = super::SPECIFICATIONS.iter().map(|(name, _, _)| (*name).to_owned()).collect();
    listed.sort();
    assert_eq!(
        listed, modules,
        "SPECIFICATIONS in crates/contract/src/lib.rs names {listed:?}; the crate's modules are {modules:?}"
    );
    for module in &modules {
        let Some((document, section)) = stated_citation(module) else { continue };
        let (given_document, given_section) = super::specification(module)
            .unwrap_or_else(|| panic!("SPECIFICATIONS holds no entry for the module {module}"));
        assert_eq!(
            (document.as_str(), section.as_str()),
            (given_document, given_section),
            "crates/contract/src/{module}.rs cites a specification other than the one SPECIFICATIONS gives it"
        );
    }
    for (module, document, section) in super::SPECIFICATIONS {
        assert!(
            heading_stands(document, section),
            "SPECIFICATIONS gives {module} the section {document} \\"{section}\\", and that document holds no such heading"
        );
    }
}
'''

SPECIFICATION_TEXT = """\
Each module of crates/contract implements one section of one specification \
document of this repository, and states which one in a line of its module \
documentation reading `//! Specification: <document> "<section>".`, where the \
section stands as a heading in that document. A module states one citation, \
and the document it names is one of the six AGENTS.md "Read first" names: \
docs/design.md, docs/config.md, docs/log-format.md, docs/protocol.md, \
docs/workflow.md, and docs/compaction.md. crates/contract/src/document.rs \
carries its citation already.

The table `SPECIFICATIONS` in crates/contract/src/lib.rs holds one row for \
every module of the crate: the module's name, the document, and the section. \
The citation a module states is the one the table gives it, and the table \
names every module of the crate. `specification` reads one module's row.

checks/run.sh runs the check the whole change is judged on: the tests of \
foe-contract, clippy with warnings denied on the same crate, and \
scripts/loc.sh.
"""

SPECIFICATIONS = Construction(
    name="module-cites-its-specification",
    crate="crates/contract",
    package="foe-contract",
    subject="Cite the specification each module of the contract crate implements",
    units=tuple(
        Unit(module, f"crates/contract/src/{module}.rs", "crates/contract/src/lib_test.rs", f"tests::the_{module}_module_cites_a_standing_section")
        for module in _CITED_MODULES
    ),
    interface_paths=("crates/contract/src/lib.rs",),
    integration_test="tests::every_module_agrees_with_the_specification_table",
    fixture_edits=(
        Edit("crates/contract/src/lib.rs", _SPECIFICATION_ANCHOR, _SPECIFICATION_REGISTRY),
        _citation_edit(_SEEDED),
    ),
    oracle_edits=(
        Edit(
            "crates/contract/src/lib.rs",
            'pub const SPECIFICATIONS: &[(&str, &str, &str)] = &[("document", "docs/config.md", "Keys")];',
            _SPECIFICATION_TABLE,
        ),
        *(_citation_edit(module) for module in _CITED_MODULES),
    ),
    test_appends=(
        (
            "crates/contract/src/lib_test.rs",
            _SPECIFICATION_TESTS
            + "".join(_SPECIFICATION_UNIT_TEST.format(module=module) for module in _CITED_MODULES)
            + _SPECIFICATION_INTEGRATION,
        ),
    ),
    ceilings=tuple(CONTRACT_CEILING),
    text=SPECIFICATION_TEXT,
    revert_unit="tools",
    rename=Edit("crates/contract/src/workflow.rs", '//! Specification: docs/workflow.md "The graph".', '//! Specification: docs/workflow.md "Recovery".'),
    rename_unit="workflow",
)

CONSTRUCTIONS: dict[str, Construction] = {construction.name: construction for construction in (BOUNDS, SPECIFICATIONS)}

# The band a constructed task's text sits in: long enough to specify the
# change once the unit list is gone, and no longer than the texts a person has
# read. It guards this module's own texts and states nothing about the rest of
# the tree, whose texts are longer where the specification sentences are many.
TEXT_MIN, TEXT_MAX = 1000, 1900


# ---- authoring ------------------------------------------------------------------


def change_directory(construction: Construction) -> str:
    """The one directory every file the change touches lies in, and the reason the task is a control.

    A worker's write grant is a directory. When every file the change touches
    lies in one directory, no two workers can be given directories that do not
    overlap, so the division the delegating node's instruction requires does
    not exist and one agent doing the work alone is the only answer that
    conforms. Authoring refuses a construction that spreads over more, because
    such a change would be divisible and the control would establish nothing.
    """
    paths = {unit.file for unit in construction.units} | set(construction.interface_paths) | {edit.path for edit in construction.oracle_edits}
    directories = sorted({Path(path).parent.as_posix() for path in paths})
    if len(directories) != 1:
        raise ValueError(
            f"the change of {construction.name} lies in {len(directories)} directories ({', '.join(directories)}); "
            "a control's change lies in one, so that no two workers can be given directories that do not overlap"
        )
    return directories[0]


def task_text(construction: Construction) -> str:
    """The task text: the specification paragraphs, then the closing sentence."""
    return construction.text.strip() + "\n\n" + protocol.CLOSING


def _write(path: Path, data: str, executable: bool = False) -> None:
    removal._write(path, data, executable)  # noqa: SLF001


def build_fixture(repo: Path, commit: str, destination: Path, construction: Construction) -> None:
    """The workspace an arm starts from: the commit's tree, the fixture edits, the raised ceilings, and the check suite."""
    protocol.archive(repo, commit, destination)
    if (destination / EVALUATION_ROOT).exists():
        raise ValueError(
            f"the tree of {commit} holds {EVALUATION_ROOT}, so the workspace would show an arm the evaluation's own "
            "instruments, tasks, and grading rules; pass --commit with a commit whose tree lacks that path"
        )
    for edit in (*construction.ceilings, *construction.fixture_edits):
        edit.apply(destination)
    _write(
        destination / CHECKS_SCRIPT,
        checks_script(construction.package, construction.check_skip, construction.check_skip_reason),
        executable=True,
    )


def metadata_of(construction: Construction, commit: str, source: dict[str, Any]) -> dict[str, Any]:
    names = [unit.name for unit in construction.units]
    return {
        # What makes this a control rather than a fan-out: every file the
        # change touches lies in this one directory, so no two of the parts
        # below can be given write roots that do not overlap.
        "division": {"changed_directory": change_directory(construction), "separable": False},
        protocol.SOURCE_KEY: source,
        "crate": construction.crate,
        "package": construction.package,
        "units": {unit.name: [unit.file] for unit in construction.units},
        "n": len(construction.units),
        "unit_files": {unit.name: [unit.file] for unit in construction.units},
        "unit_tests": {unit.name: [unit.test_file] for unit in construction.units},
        "unit_test_names": {unit.name: unit.test_name for unit in construction.units},
        "checked_units": names,
        "interface_paths": list(construction.interface_paths),
        "integration_test": construction.integration_test,
        "revert_unit": construction.revert_unit,
        "rename_unit": construction.rename_unit,
        "authored": "by construction: the units, the shared element, and the tests are chosen rather than read from a commit",
        "review": "pending: the text was drafted with the construction and has not been read by a person",
    }


def author(repo: Path, out: Path, construction: Construction, commit: str, keep_workspace: bool = False) -> Task:
    """Write one constructed control's task directory; see the module docstring for what it holds."""
    if out.exists():
        raise FileExistsError(f"{out} exists; author writes a fresh task directory")
    resolved = removal.git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()
    change_directory(construction)
    text = task_text(construction)
    if not TEXT_MIN <= len(text) <= TEXT_MAX:
        raise ValueError(f"task {construction.name}: the text is {len(text)} characters; the band is {TEXT_MIN} to {TEXT_MAX}")
    out.mkdir(parents=True)
    try:
        workspace, grader = out / WORKSPACE, out / GRADER
        build_fixture(repo, resolved, workspace, construction)

        # The oracle overlay: the fixture with the change applied, holding only
        # the files the change touches.
        solved = out / "solved"
        shutil.copytree(workspace, solved, symlinks=True)
        for edit in construction.oracle_edits:
            edit.apply(solved)
        changed = sorted({edit.path for edit in construction.oracle_edits})
        for relative in changed:
            _write(grader / protocol.ORACLE / WORKSPACE / relative, (solved / relative).read_text(encoding="utf-8"))

        # The hidden tests: the fixture's test file with the task's test appended.
        for relative, appended in construction.test_appends:
            source = (workspace / relative).read_text(encoding="utf-8")
            if appended.strip() in source:
                raise ValueError(f"{relative}: the hidden test already stands in the fixture")
            _write(grader / removal.HIDDEN_TESTS / relative, source.rstrip("\n") + "\n" + appended)

        specification = {
            "task": construction.name,
            "package": construction.package,
            "units": [{"name": unit.name, "test": unit.test_name} for unit in construction.units],
            "integration_test": construction.integration_test,
        }
        _write(grader / SPECIFICATION_FILE, json.dumps(specification, indent=2) + "\n")
        _write(grader / "grade.py", GRADE_SCRIPT, executable=True)
        _write(grader / protocol.GRADE_SCRIPT, GRADE_WRAPPER, executable=True)

        # The two corruptions, recorded against the solved workspace.
        revert = next(unit for unit in construction.units if unit.name == construction.revert_unit)
        reverted = [
            {"path": edit.path, "old": edit.new, "new": edit.old} for edit in construction.oracle_edits if edit.path == revert.file
        ]
        if not reverted:
            raise ValueError(f"task {construction.name}: the unit {revert.name} has no oracle edit to revert")
        _write(grader / protocol.CORRUPTIONS / REVERT_CORRUPTION / "edits.json", json.dumps(reverted, indent=2) + "\n")
        _write(grader / protocol.CORRUPTIONS / REVERT_CORRUPTION / "apply.py", CORRUPTION_SCRIPT, executable=True)
        rename = [{"path": construction.rename.path, "old": construction.rename.old, "new": construction.rename.new}]
        _write(grader / protocol.CORRUPTIONS / RENAME_CORRUPTION / "edits.json", json.dumps(rename, indent=2) + "\n")
        _write(grader / protocol.CORRUPTIONS / RENAME_CORRUPTION / "apply.py", CORRUPTION_SCRIPT, executable=True)
        shutil.rmtree(solved)

        source = protocol.source_record(repo, out, commit=resolved, subject=construction.subject)
        task = Task(
            name=construction.name,
            family=FAMILY,
            class_name=CLASS_NAME,
            text=text,
            correct_statuses=frozenset({protocol.COMPLETED}),
            correct_codes=frozenset(),
            budget=dict(BUDGET),
            protected=PROTECTED,
            metadata=metadata_of(construction, resolved, source),
        )
        protocol.save(task, out)
        protocol.record_workspace_patch(out, repo)
        _write(grader / protocol.PROTECTED_FILE, json.dumps(removal._protected_record(out, task), indent=2, sort_keys=True) + "\n")  # noqa: SLF001
        if not keep_workspace:
            shutil.rmtree(workspace)
        return task
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    emit = commands.add_parser("emit", help="write one constructed control's task directory")
    emit.add_argument("--task", required=True, choices=sorted(CONSTRUCTIONS), help="which construction")
    emit.add_argument("--repo", required=True, type=Path, help="the repository the base tree comes from")
    emit.add_argument("--commit", required=True, help=f"the commit whose tree the fixture is built from; its tree may not hold {EVALUATION_ROOT}")
    emit.add_argument("--out", required=True, type=Path, help="the task directory to create")
    emit.add_argument("--keep-workspace", action="store_true", help="keep the workspace copy beside the recipe, for inspection")
    verifying = commands.add_parser("verify", help="run the grader controls with a build-length timeout")
    verifying.add_argument("--task", required=True, type=Path, help="the task directory")
    verifying.add_argument("--scratch", required=True, type=Path, help="where each control's root is materialized")
    verifying.add_argument("--timeout", type=int, default=GRADE_TIMEOUT_SECONDS, help="seconds one grade may take")
    args = parser.parse_args(argv)

    try:
        if args.command == "emit":
            construction = CONSTRUCTIONS[args.task]
            task = author(args.repo.resolve(), args.out.resolve(), construction, args.commit, args.keep_workspace)
            print(f"task {task.name} written to {args.out}")
            print(f"parts: {', '.join(unit.name for unit in construction.units)}")
            print(f"interface: {', '.join(construction.interface_paths)}")
            print(f"the change lies in one directory, {task.metadata['division']['changed_directory']}, so no two parts can be given write roots that do not overlap")
            print(f"text: {len(task.text)} characters")
            return 0
        started = time.monotonic()
        results = removal.verify(args.task.resolve(), args.scratch.resolve(), args.timeout)
        held = teams._print_controls(results)  # noqa: SLF001
        print(f"wall time: {time.monotonic() - started:.0f}s")
        return 0 if held else 1
    except (ValueError, FileExistsError, FileNotFoundError, RuntimeError) as error:
        print(f"coherent: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
