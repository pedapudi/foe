#!/usr/bin/python3
"""Author the teams tasks: fan-out tasks from sweep commits, and survey tasks whose answer a script computes.

A `fan-out` task is one change applied to many similar units. It is authored
from a sweep commit of this repository through `feature_removal.author`,
which supplies the fixture, the oracle, the hidden tests, the trace scrub,
and the recipe; this module adds what a fan-out needs. A unit is a crate
`crates/<name>` or a top-level directory or file in which the commit changes
an implementation file, named by that path. A crate or directory whose
touched files are all tests or fixtures is no unit: the grader restores the
commit form of every hidden test, so such a place holds nothing for the
agent to change and its verdict follows from other units. The commit's test
files are partitioned by unit, so a unit's verdict is one boolean: for a
crate, `cargo test -p <package>` with the unit's hidden tests restored and
every hidden test function seen running; for a directory, every hidden
`*_test.py` file run with the interpreter. A unit with no runnable hidden
test is unchecked, and the uniformity of the change is the fraction of
checked units that pass. The task text names every unit and states that
each is judged by its own tests; it does not say which units carry a
verdict, so an arm cannot leave the unchecked ones untouched by design. The
whole change is judged by the workspace check, which is `cargo test
--workspace`, `cargo clippy --workspace -- -D warnings`, and
`scripts/loc.sh`, and by the specification sentences the commit added under
`docs/`. `checks/run.sh` in the workspace runs the workspace check.

Two corruptions of the solved workspace are recorded. `revert-one-unit`
restores the parent form of one checked unit's implementation files; the
unit is one that no other checked unit's crate depends on, so its own tests
fail and every other unit's pass. `rename-shared-element` renames one
public Rust item defined in a touched file of one unit that a crate outside
every checked unit's dependency closure uses, so every unit's tests pass and
the workspace check fails; a commit in which no item qualifies records the
reason under `metadata.shared_element_absent` and carries the first
corruption alone. `task.json` records the family `teams`, the class
`fan-out`, `metadata.units` as an object of unit name to the unit's
workspace-relative paths, `metadata.n`, the hidden tests and package of
each unit, the checked units, and `metadata.interface_paths`, the
implementation files of touched crates that another touched crate depends
on. The grader keeps no `oracle.patch`: the oracle overlay and the hidden
tests hold the commit form of every changed file, and a sweep touches many.

A `survey` task is a question over the whole tree whose answer a script
computes. Two are defined. `error-messages`: every error message under
`crates/` that fails the AGENTS.md rule that an error names the key, event,
or rule involved, by the operational definition the task text states.
`config-rules`: every rule sentence of `docs/config.md` whose key no test
under `crates/` cites, where a test cites a key when a doc comment names
the document and the key. The oracle is the script's own output over the
recorded commit's tree, stored as `grader/oracle/candidate.json`; the
script itself is stored as `grader/survey.py`. The grade runs the script
over the workspace as graded, requires every recorded item to be there
still, and scores the returned value's precision and recall against the
script's answer; both are at least SURVEY_THRESHOLD for a pass. The one
corruption edits the tree so that the script finds items the recorded
answer lacks, enough that the answer's recall falls below the threshold;
the count follows from the threshold, and removing one true item from an
answer of at most nine lowers its recall past it.

The grade scripts print findings on standard output, as the protocol
requires, and record their measures beside them: a fan-out grade prints one
line `units: {name: passed}` on standard error and writes the same object to
`units.json` beside `grade.py` in the materialized root's grader directory
and in the grade's log directory; a survey grade prints one line
`measures: {precision, recall, ...}` on standard error and writes
`measures.json` in the same two places. The copy beside `grade.py` is the
one a runner reads after a grade, through `read_units` and
`read_measures`, since the log directory is named by the grade's start
time; `parse_units` and `parse_measures` read the standard-error lines
back. The protocol judges damage before the grade script runs, so the file
the grade leaves in the grader directory counts as damage only when the
same root is graded a second time; a root is graded once.

Every task is a recipe: `metadata.source` records the commit, and
`grader/workspace.patch` the diff from the base tree to the workspace, so
`protocol.materialize` regenerates the workspace from the repository. The
workspace copy is removed once the patch is written; `--keep-workspace`
keeps it for inspection. A fan-out grade runs cargo, so `verify` runs the
grader controls with a build-length timeout, as `feature_removal.verify`
does.

    /usr/bin/python3 evals/cross_harness/tasks/teams.py fan-out --repo . --commit SHA --out DIR --name NAME
    /usr/bin/python3 evals/cross_harness/tasks/teams.py survey --repo . --survey error-messages --out DIR --name NAME
    /usr/bin/python3 evals/cross_harness/tasks/teams.py verify --task DIR --scratch DIR
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import feature_removal as removal  # noqa: E402
import protocol  # noqa: E402
from protocol import COMPLETED, GRADER, WORKSPACE, Task  # noqa: E402

FAMILY = "teams"
FAN_OUT, SURVEY = "fan-out", "survey"
REVERT_CORRUPTION = "revert-one-unit"
RENAME_CORRUPTION = "rename-shared-element"
SURVEY_CORRUPTION = "unlisted-items"
SURVEY_SCRIPT = "survey.py"
SURVEY_THRESHOLD = 0.9
UNITS_PREFIX = "units: "
MEASURES_PREFIX = "measures: "
UNITS_FILE = "units.json"
MEASURES_FILE = "measures.json"
PROTECTED: tuple[str, ...] = ("scripts/loc.sh", "AGENTS.md", "checks/run.sh")
CRATES = removal.CRATES
GRADE_TIMEOUT_SECONDS = removal.GRADE_TIMEOUT_SECONDS
# Which measures a survey grade records: the precision and recall the
# thresholds read, and the counts they are computed from.
MEASURE_KEYS: tuple[str, ...] = ("precision", "recall", "returned", "matched", "true_items")

_PUBLIC_ITEM = re.compile(
    r"^\s*pub(?:\([^)]*\))?\s+(?:async\s+|unsafe\s+|const\s+)*(?:fn|struct|enum|const|static|trait|type)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
_PATH_DEPENDENCY = re.compile(r'^\s*[A-Za-z0-9_-]+\s*=\s*\{[^}]*path\s*=\s*"\.\./([A-Za-z0-9_-]+)"')
_MANIFEST_SECTION = re.compile(r"^\s*\[([^\]]+)\]")
_DEPENDENCY_SECTIONS = ("dependencies", "dev-dependencies", "build-dependencies")
_ERROR_ATTRIBUTE = re.compile(r'^(\s*#\[error\(")((?:[^"\\]|\\.)*)("\)\].*)$')
_ERROR_BACKTICK = re.compile(r"`[^`]+`")
# One segment of a key path, as the error-messages survey script defines it;
# the corruption's edits and the script must agree on what a key path is.
_KEY_SEGMENT = r"(?:[a-z_][a-z0-9_]+|\{[a-z_][a-z0-9_]+\})"
_ERROR_KEY_PATH = re.compile(r"(?<![A-Za-z0-9_{}])" + _KEY_SEGMENT + r"(?:\." + _KEY_SEGMENT + r")+(?![A-Za-z0-9_])")
_CITATION_DOCUMENT = "docs/config.md"


@dataclass(frozen=True)
class Unit:
    """One unit of a fan-out: what the commit touched in it and how it is checked."""

    name: str
    files: tuple[str, ...]
    implementation: tuple[str, ...]
    tests: tuple[str, ...]
    package: str | None

    @property
    def python_tests(self) -> tuple[str, ...]:
        """The hidden test files the interpreter runs directly: unittest files named `*_test.py`."""
        return tuple(path for path in self.tests if path.endswith("_test.py"))

    @property
    def checked(self) -> bool:
        """Whether the unit has a verdict of its own: implementation files to change, and a hidden test file for a crate or a runnable one for a directory."""
        if not self.implementation:
            return False
        if self.package is not None:
            return bool(self.tests)
        return bool(self.python_tests)

    @property
    def paths(self) -> list[str]:
        return [self.name]


def unit_name(path: str) -> str:
    """The unit a workspace-relative path belongs to: `crates/<name>`, else the top-level directory or file."""
    parts = Path(path).parts
    if len(parts) >= 3 and parts[0] == CRATES:
        return f"{CRATES}/{parts[1]}"
    return parts[0]


def partition_units(diffs: list[removal.FileDiff], workspace: Path) -> list[Unit]:
    """The units a commit changes an implementation file in, in name order, with each unit's files split into implementation and tests.

    A crate or directory whose touched files are all tests or fixtures is
    left out: the grader restores the commit form of every hidden test, so
    the agent has nothing to change there.
    """
    grouped: dict[str, list[removal.FileDiff]] = {}
    for diff in diffs:
        grouped.setdefault(unit_name(diff.path), []).append(diff)
    units: list[Unit] = []
    for name in sorted(grouped):
        members = grouped[name]
        if all(diff.is_test for diff in members):
            continue
        package = removal.package_name(workspace, name) if name.startswith(f"{CRATES}/") else None
        units.append(
            Unit(
                name=name,
                files=tuple(diff.path for diff in members),
                implementation=tuple(diff.path for diff in members if not diff.is_test),
                tests=tuple(diff.path for diff in members if diff.is_test),
                package=package,
            )
        )
    return units


def crate_dependencies(workspace: Path) -> dict[str, set[str]]:
    """Each crate directory's path dependencies on sibling crates, read from the `path = "../<name>"` entries of its manifest."""
    graph: dict[str, set[str]] = {}
    for manifest in sorted((workspace / CRATES).glob("*/Cargo.toml")):
        crate = f"{CRATES}/{manifest.parent.name}"
        found: set[str] = set()
        section = ""
        for line in manifest.read_text(encoding="utf-8").splitlines():
            header = _MANIFEST_SECTION.match(line)
            if header:
                section = header.group(1).strip()
                continue
            if section.split(".")[-1] not in _DEPENDENCY_SECTIONS and section not in _DEPENDENCY_SECTIONS:
                continue
            match = _PATH_DEPENDENCY.match(line)
            if match:
                found.add(f"{CRATES}/{match.group(1)}")
        graph[crate] = found
    return graph


def closure(graph: dict[str, set[str]], starts: list[str]) -> set[str]:
    """The crates reachable from the starts through the dependency graph, the starts included."""
    seen: set[str] = set()
    pending = list(starts)
    while pending:
        crate = pending.pop()
        if crate in seen:
            continue
        seen.add(crate)
        pending.extend(graph.get(crate, set()))
    return seen


def revert_unit_for(units: list[Unit], graph: dict[str, set[str]]) -> Unit:
    """The checked unit with implementation files that no other checked unit's crate depends on, first in name order."""
    checked = [unit for unit in units if unit.checked]
    for unit in checked:
        if not unit.implementation:
            continue
        dependents = [other for other in checked if other is not unit and unit.name in closure(graph, [other.name])]
        if not dependents:
            return unit
    names = ", ".join(unit.name for unit in checked) or "none"
    raise ValueError(f"no checked unit with implementation files is free of dependents among the checked units ({names}); pass --revert-unit to choose one")


@dataclass(frozen=True)
class SharedElement:
    """A public item whose rename in its unit breaks the workspace check and no unit's tests."""

    unit: str
    element: str
    replacement: str
    files: tuple[str, ...]
    used_by: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"unit": self.unit, "element": self.element, "replacement": self.replacement, "files": list(self.files), "used_by": list(self.used_by)}


def _users(repo: Path, commit: str, name: str) -> list[str]:
    """Every file under crates/ at the commit that holds the name as a whole word."""
    result = subprocess.run(
        [removal.GIT, "-C", str(repo), "grep", "-l", "-w", "-e", name, commit, "--", CRATES], capture_output=True, text=True, check=False
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git grep for {name!r} at {commit} exited {result.returncode}: {result.stderr.strip()}")
    return [line.split(":", 1)[1] for line in result.stdout.splitlines() if ":" in line]


def shared_element(repo: Path, commit: str, units: list[Unit], graph: dict[str, set[str]]) -> tuple[SharedElement | None, str]:
    """The first public item a rename corruption can use, or None with the reason.

    An item qualifies when a touched implementation file of a crate unit
    defines it, some crate outside the dependency closure of every checked
    unit uses it, no file of a crate inside that closure uses it except the
    defining unit's own non-test files, and, when the defining unit is
    checked, none of its test files uses it. Whole-word search is the test
    for use, so a common word is rejected more often than an item is
    accepted wrongly.
    """
    checked = [unit.name for unit in units if unit.checked]
    compiled = closure(graph, checked)
    for unit in units:
        if unit.package is None:
            continue
        for path in unit.implementation:
            if not path.endswith(".rs"):
                continue
            text = removal.show_file(repo, commit, path).decode("utf-8", "replace")
            for line in text.splitlines():
                match = _PUBLIC_ITEM.match(line)
                if not match:
                    continue
                name = match.group(1)
                users = _users(repo, commit, name)
                own = [user for user in users if unit_name(user) == unit.name]
                foreign = [user for user in users if unit_name(user) != unit.name]
                if unit.checked and any(removal.is_test_path(user) for user in own):
                    continue
                if not foreign or any(unit_name(user) in compiled for user in foreign):
                    continue
                files = tuple(sorted(user for user in own if not removal.is_test_path(user)))
                if path not in files:
                    continue
                replacement = name + ("Renamed" if name[0].isupper() else "_renamed")
                used_by = tuple(sorted({unit_name(user) for user in foreign}))
                return SharedElement(unit.name, name, replacement, files, used_by), ""
    return None, (
        "no public item defined in a touched implementation file is used by a crate outside the dependency closure "
        f"of the checked units ({', '.join(sorted(compiled)) or 'none'}) and by nothing inside it"
    )


def interface_paths(units: list[Unit], graph: dict[str, set[str]]) -> list[str]:
    """The implementation files of touched crates that another touched crate depends on."""
    touched = [unit.name for unit in units if unit.package is not None]
    shared: list[str] = []
    for unit in units:
        if unit.package is None:
            continue
        if any(unit.name in closure(graph, [other]) for other in touched if other != unit.name):
            shared.extend(unit.implementation)
    return sorted(shared)


def budget_for(checked_count: int) -> dict[str, int]:
    """The default budget of a fan-out, which grows with the units that carry a verdict."""
    return removal.budget_for(max(1, checked_count))


def fan_out_text(subject: str, body: str, sentences: dict[str, list[str]], units: list[Unit]) -> str:
    """The specification paragraph of a fan-out, drafted from the commit and its document changes."""
    parts = [subject.rstrip(".") + "."]
    if body:
        parts.append(body)
    # The text names every unit and no subset: which units carry a verdict
    # of their own is the grader's knowledge, and an arm told it could leave
    # the other units untouched without a finding.
    names = ", ".join(unit.name for unit in units)
    parts.append(
        f"The change applies to {len(units)} units: {names}. Each unit is judged by its own tests. "
        f"{removal.CHECKS_SCRIPT} runs the check the whole change is judged on: cargo test --workspace, "
        "cargo clippy --workspace -- -D warnings, and scripts/loc.sh."
    )
    if sentences:
        lines = ["The documents under docs/ describe the behavior. Each sentence below belongs in the document named before it, in the same words:"]
        for path, found in sentences.items():
            for sentence in found:
                lines.append(f"- {path}: {sentence}")
        parts.append("\n".join(lines))
    return protocol.autonomy_text("\n\n".join(parts))


WORKSPACE_CHECKS_SCRIPT = (
    "#!/bin/sh\n"
    "# The check the whole change is judged on: every crate's tests, clippy with warnings denied, and the line budgets.\n"
    "set -eu\n"
    'cd "$(dirname "$0")/.."\n'
    "cargo test --workspace\n"
    "cargo clippy --workspace -- -D warnings\n"
    "scripts/loc.sh\n"
)

SURVEY_CHECKS_SCRIPT = (
    "#!/bin/sh\n"
    "# A survey changes no file, so the check has nothing to run; the returned value is graded.\n"
    "exit 0\n"
)

_FAN_OUT_GRADE_TEMPLATE = r'''#!/usr/bin/python3
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
COMMAND_SECONDS = __COMMAND_SECONDS__
UNITS_PREFIX = "__UNITS_PREFIX__"
UNITS_FILE = "__UNITS_FILE__"
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
'''

assert _FAN_OUT_GRADE_TEMPLATE.count("__COMMAND_SECONDS__") == 1, "the fan-out grade template names the command budget once"
assert _FAN_OUT_GRADE_TEMPLATE.count("__UNITS_PREFIX__") == 1, "the fan-out grade template names the units prefix once"
assert _FAN_OUT_GRADE_TEMPLATE.count("__UNITS_FILE__") == 1, "the fan-out grade template names the units file once"
FAN_OUT_GRADE_SCRIPT = (
    _FAN_OUT_GRADE_TEMPLATE.replace("__COMMAND_SECONDS__", str(removal.GRADE_COMMAND_SECONDS))
    .replace("__UNITS_PREFIX__", UNITS_PREFIX)
    .replace("__UNITS_FILE__", UNITS_FILE)
)

REVERT_SCRIPT = r'''#!/usr/bin/python3
"""Restore the parent form of one unit's implementation files: that unit's tests must then fail and every other unit's pass."""

import json
import pathlib
import shutil
import sys

here = pathlib.Path(__file__).resolve().parent
workspace = pathlib.Path(sys.argv[1])
manifest = json.loads((here / "revert.json").read_text(encoding="utf-8"))
for relative in manifest["restore"]:
    target = workspace / relative
    if not target.is_file():
        raise SystemExit(f"{target}: absent, so the corruption has nothing to revert")
    shutil.copy2(here / "parent" / relative, target)
for relative in manifest["remove"]:
    target = workspace / relative
    if not target.is_file():
        raise SystemExit(f"{target}: absent, so the corruption has nothing to remove")
    target.unlink()
'''

RENAME_SCRIPT = r'''#!/usr/bin/python3
"""Rename one shared item in its unit's files: the workspace check must then fail while every unit's tests pass."""

import json
import pathlib
import re
import sys

here = pathlib.Path(__file__).resolve().parent
workspace = pathlib.Path(sys.argv[1])
manifest = json.loads((here / "rename.json").read_text(encoding="utf-8"))
pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(manifest["element"]) + r"(?![A-Za-z0-9_])")
for relative in manifest["files"]:
    target = workspace / relative
    if not target.is_file():
        raise SystemExit(f"{target}: absent, so the corruption has nothing to rename")
    text, count = pattern.subn(manifest["replacement"], target.read_text(encoding="utf-8"))
    if count == 0:
        raise SystemExit(f"{target}: holds no {manifest['element']!r} to rename")
    target.write_text(text, encoding="utf-8")
'''

EDIT_LINES_SCRIPT = r'''#!/usr/bin/python3
"""Replace recorded lines of the workspace, each asserted to read as recorded before it is replaced."""

import json
import pathlib
import sys

here = pathlib.Path(__file__).resolve().parent
workspace = pathlib.Path(sys.argv[1])
for edit in json.loads((here / "edits.json").read_text(encoding="utf-8")):
    target = workspace / edit["path"]
    if not target.is_file():
        raise SystemExit(f"{target}: absent, so the corruption has nothing to edit")
    lines = target.read_text(encoding="utf-8").split("\n")
    index = edit["line"] - 1
    if index >= len(lines) or lines[index] != edit["old"]:
        found = lines[index] if index < len(lines) else None
        raise SystemExit(f"{target}:{edit['line']}: reads {found!r}, and the corruption expected {edit['old']!r}")
    lines[index] = edit["new"]
    target.write_text("\n".join(lines), encoding="utf-8")
'''


def _write(path: Path, data: str, executable: bool = False) -> None:
    removal._write(path, data, executable)  # noqa: SLF001


def _finish(out: Path, task: Task, repo: Path, keep_workspace: bool) -> None:
    """Save the task, record the recipe and the protected hashes, and drop the workspace copy unless kept."""
    protocol.save(task, out)
    protocol.record_workspace_patch(out, repo)
    _write(out / GRADER / protocol.PROTECTED_FILE, json.dumps(removal._protected_record(out, task), indent=2, sort_keys=True) + "\n")  # noqa: SLF001
    if not keep_workspace:
        shutil.rmtree(out / WORKSPACE)


@dataclass(frozen=True)
class AuthoredFanOut:
    task: Task
    directory: Path
    units: tuple[Unit, ...]
    revert_unit: str
    shared: SharedElement | None
    sentences: dict[str, list[str]] = field(default_factory=dict)


def author_fan_out(
    repo: Path,
    commit: str,
    out: Path,
    name: str,
    allow_traces: list[str] | None = None,
    keep_workspace: bool = False,
    revert_unit: str | None = None,
) -> AuthoredFanOut:
    """Write the task directory for one sweep commit; see the module docstring for what it holds."""
    authored = removal.author(repo, commit, out, name, "solvable", allow_traces, keep_workspace=True)
    try:
        return _write_fan_out(repo, authored, out, name, keep_workspace, revert_unit)
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise


def _write_fan_out(repo: Path, authored: removal.Authored, out: Path, name: str, keep_workspace: bool, revert_name: str | None) -> AuthoredFanOut:
    source = authored.task.metadata[protocol.SOURCE_KEY]
    commit, parent = source["commit"], source["parent"]
    workspace, grader = out / WORKSPACE, out / GRADER
    diffs = removal.parse_diff(removal.commit_diff(repo, commit))
    tests, implementation = removal.partition(diffs)
    units = partition_units(diffs, workspace)
    graph = crate_dependencies(workspace)
    checked = [unit for unit in units if unit.checked]
    if not checked:
        raise ValueError(f"commit {commit} touches no unit with a runnable hidden test; a fan-out needs a verdict per unit")

    # The feature-removal grader and corruption give way to the fan-out's own,
    # and the commit diff is removed: the oracle overlay and the hidden tests
    # hold the commit form of every changed file, and a sweep touches many.
    (grader / "grade.py").unlink()
    (grader / removal.ORACLE_PATCH).unlink()
    shutil.rmtree(grader / protocol.CORRUPTIONS)
    _write(workspace / removal.CHECKS_SCRIPT, WORKSPACE_CHECKS_SCRIPT, executable=True)

    by_name = {unit.name: unit for unit in units}
    if revert_name is None:
        reverted = revert_unit_for(units, graph)
    elif revert_name in by_name and by_name[revert_name].checked and by_name[revert_name].implementation:
        reverted = by_name[revert_name]
    else:
        raise ValueError(f"--revert-unit names {revert_name!r}, which is not a checked unit with implementation files; the units are {', '.join(by_name)}")
    revert = grader / protocol.CORRUPTIONS / REVERT_CORRUPTION
    statuses = {diff.path: diff.status for diff in diffs}
    restore = [path for path in reverted.implementation if statuses[path] == removal.MODIFIED]
    remove = [path for path in reverted.implementation if statuses[path] == removal.ADDED]
    for path in restore:
        removal._write(revert / "parent" / path, removal.show_file(repo, parent, path))  # noqa: SLF001
    _write(revert / "revert.json", json.dumps({"unit": reverted.name, "restore": restore, "remove": remove}, indent=2) + "\n")
    _write(revert / "apply.py", REVERT_SCRIPT, executable=True)

    shared, reason = shared_element(repo, commit, units, graph)
    if shared is not None:
        rename = grader / protocol.CORRUPTIONS / RENAME_CORRUPTION
        _write(rename / "rename.json", json.dumps(shared.to_dict(), indent=2) + "\n")
        _write(rename / "apply.py", RENAME_SCRIPT, executable=True)

    hidden_names = {
        diff.path: removal.test_names(removal.show_file(repo, commit, diff.path).decode("utf-8", "replace")) for diff in tests if diff.path.endswith(".rs")
    }
    specification = {
        "task": name,
        "units": [
            {
                "name": unit.name,
                "package": unit.package,
                "python_tests": list(unit.python_tests),
                "hidden_test_names": {path: hidden_names.get(path, []) for path in unit.tests if path.endswith(".rs")},
            }
            for unit in checked
        ],
        "sentences": authored.sentences,
    }
    _write(grader / removal.SPECIFICATION_FILE, json.dumps(specification, indent=2) + "\n")
    _write(grader / "grade.py", FAN_OUT_GRADE_SCRIPT, executable=True)

    subject, body = removal.commit_message(repo, commit)
    metadata = dict(authored.task.metadata)
    metadata.update(
        {
            "units": {unit.name: unit.paths for unit in units},
            "n": len(units),
            "unit_files": {unit.name: list(unit.files) for unit in units},
            "unit_tests": {unit.name: list(unit.tests) for unit in units},
            "unit_packages": {unit.name: unit.package for unit in units},
            "checked_units": [unit.name for unit in checked],
            "interface_paths": interface_paths(units, graph),
            "revert_unit": reverted.name,
            "shared_element": None if shared is None else shared.to_dict(),
            "review": "pending: the text is drafted from the commit message and the document changes and has not been read by a person",
        }
    )
    if shared is None:
        metadata["shared_element_absent"] = reason
    task = replace(
        authored.task,
        family=FAMILY,
        class_name=FAN_OUT,
        text=fan_out_text(subject, body, authored.sentences, units),
        budget=budget_for(len(checked)),
        protected=PROTECTED,
        metadata=metadata,
    )
    _finish(out, task, repo, keep_workspace)
    return AuthoredFanOut(task, out, tuple(units), reverted.name, shared, authored.sentences)


ERROR_SURVEY_SCRIPT = r'''#!/usr/bin/python3
"""Every error message under crates/ that names no subject, by the operational definition the task text states.

An error message is the string literal of an `#[error("...")]` attribute
whose literal is the attribute's only argument, written on one line whose
first non-blank characters are `#[error(`, in a `.rs` file under crates/
that is neither named `*_test.rs` nor held under a `tests/` directory;
`#[error(transparent)]` carries no message, and an attribute with arguments
after the literal, `#[error("...", expr)]`, is outside the survey. A
message names its subject when it contains a backtick-quoted identifier, a
key path, or the placeholder `{key}`. A key path is two or more segments
joined by single dots, each segment a name of two or more characters from
lowercase letters, digits, and underscores that starts with a letter or
underscore, or such a name in braces.

    survey.py WORKSPACE

prints the failing messages as a JSON list of objects with `path`, `line`,
and `message`, in path and line order. A file the survey cannot read ends
the script with exit status 1 and a message naming the file.
"""

import json
import re
import sys
from pathlib import Path

ATTRIBUTE = re.compile(r'^\s*#\[error\("((?:[^"\\]|\\.)*)"\)\]')
BACKTICK = re.compile(r"`[^`]+`")
SEGMENT = r"(?:[a-z_][a-z0-9_]+|\{[a-z_][a-z0-9_]+\})"
KEY_PATH = re.compile(r"(?<![A-Za-z0-9_{}])" + SEGMENT + r"(?:\." + SEGMENT + r")+(?![A-Za-z0-9_])")
KEY_PLACEHOLDER = "{key}"
TEST_FILE = re.compile(r"(^|/)tests/|_test\.rs$")


def read_lines(file, relative):
    try:
        return file.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise SystemExit(f"{relative}: not UTF-8 at byte {error.start}, so the survey cannot read it") from error


def names_subject(message):
    return bool(BACKTICK.search(message) or KEY_PATH.search(message) or KEY_PLACEHOLDER in message)


def survey(root):
    items = []
    for file in sorted((root / "crates").rglob("*.rs")):
        relative = file.relative_to(root).as_posix()
        if TEST_FILE.search(relative) or not file.is_file():
            continue
        for number, line in enumerate(read_lines(file, relative), start=1):
            match = ATTRIBUTE.match(line)
            if match and not names_subject(match.group(1)):
                items.append({"path": relative, "line": number, "message": match.group(1)})
    return items


if __name__ == "__main__":
    print(json.dumps(survey(Path(sys.argv[1])), indent=2))
'''

RULES_SURVEY_SCRIPT = r'''#!/usr/bin/python3
"""Every rule sentence of docs/config.md whose key no test cites, by the operational definition the task text states.

A key is a heading of docs/config.md of the form `### `key``; its section
runs to the next heading. A rule sentence is a sentence of the section's
prose: the lines outside fenced code blocks and outside table rows, grouped
into paragraphs at blank lines, headings, and the start of a list item, and
each paragraph split into sentences at a period, question mark, or
exclamation mark that is followed by whitespace or the end of the paragraph
and lies outside a backtick span. A test cites a key when a `///` doc
comment line in a file named `*_test.rs` under crates/ contains
`docs/config.md` and the first backtick span after it on the same line is
a backtick-quoted identifier; that identifier is the key.

    survey.py WORKSPACE        the uncited sentences as a JSON list of objects with `key` and `sentence`
    survey.py WORKSPACE --all  every key with its sentences and whether a test cites it

An absent docs/config.md or a test file the survey cannot read ends the
script with exit status 1 and a message naming the file.
"""

import json
import re
import sys
from pathlib import Path

DOCUMENT = "docs/config.md"
HEADING = re.compile(r"^#{1,6} ")
KEY_HEADING = re.compile(r"^### `([A-Za-z_][A-Za-z0-9_]*)`\s*$")
CITATION = re.compile(r"^\s*///.*docs/config\.md[^`]*`([A-Za-z_][A-Za-z0-9_]*)`")
CODE_SPAN = re.compile(r"`[^`]*`")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+\.)\s")


def sentences(paragraph):
    text = " ".join(line.strip() for line in paragraph)
    masked = CODE_SPAN.sub(lambda match: "\0" * len(match.group(0)), text)
    found, start = [], 0
    for match in SENTENCE_END.finditer(masked):
        found.append(text[start : match.end()].strip())
        start = match.end()
    rest = text[start:].strip()
    if rest:
        found.append(rest)
    return found


def sections(document):
    """Each key's rule sentences, in document order."""
    found = {}
    key, fenced, paragraph = None, False, []

    def flush():
        if key is not None and paragraph:
            found.setdefault(key, []).extend(sentences(paragraph))
        paragraph.clear()

    for line in document.splitlines():
        if line.startswith("```"):
            flush()
            fenced = not fenced
            continue
        if fenced:
            continue
        if HEADING.match(line):
            flush()
            match = KEY_HEADING.match(line)
            key = match.group(1) if match else None
            continue
        if not line.strip() or line.lstrip().startswith("|"):
            flush()
            continue
        if LIST_ITEM.match(line):
            flush()
        paragraph.append(line)
    flush()
    return found


def read_text(root, relative):
    file = root / relative
    if not file.is_file():
        raise SystemExit(f"{relative}: absent from {root}, so the survey cannot read it")
    try:
        return file.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise SystemExit(f"{relative}: not UTF-8 at byte {error.start}, so the survey cannot read it") from error


def cited_keys(root):
    cited = set()
    for file in sorted((root / "crates").rglob("*_test.rs")):
        for line in read_text(root, file.relative_to(root).as_posix()).splitlines():
            match = CITATION.match(line)
            if match:
                cited.add(match.group(1))
    return cited


def survey(root):
    document = read_text(root, DOCUMENT)
    cited = cited_keys(root)
    return [{"key": key, "sentence": sentence} for key, found in sections(document).items() if key not in cited for sentence in found]


def everything(root):
    document = read_text(root, DOCUMENT)
    cited = cited_keys(root)
    return [{"key": key, "cited": key in cited, "sentences": found} for key, found in sections(document).items()]


if __name__ == "__main__":
    root = Path(sys.argv[1])
    print(json.dumps(everything(root) if "--all" in sys.argv[2:] else survey(root), indent=2))
'''

_SURVEY_GRADE_TEMPLATE = r'''#!/usr/bin/python3
"""Grade a survey: the precision and recall of the returned items against the survey script's answer over the workspace.

The script runs from the workspace. It reads the returned value from the
grader input, requires it to be an object whose `items` is a list of
objects carrying the identity keys `specification.json` names, runs
`survey.py` beside this script over the workspace, requires every item of
the recorded answer `oracle/candidate.json` to be in the script's answer
still, and scores the returned items against the script's answer: an item
matches when its identity keys match after normalization, precision is the
matched items over the returned items, recall the matched items over the
script's items, and both are at least the threshold for a pass. The
measures are printed as one line `measures: {...}` on standard error and
written to `measures.json` beside this script and under the grade's log
directory; the findings on standard output alone decide the grade. The copy
beside this script is at a path a runner knows from the root alone; the
protocol judges damage before this script runs, so the file counts as
damage only when the same root is graded again. When `survey.py` cannot
run over the workspace, the grade's finding carries the script's own
message, which names the file, and no measures are recorded.
"""

import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GRADER = Path(__file__).resolve().parent
WORKSPACE = Path.cwd()
MEASURES_PREFIX = "__MEASURES_PREFIX__"
MEASURES_FILE = "__MEASURES_FILE__"

specification = json.loads((GRADER / "specification.json").read_text(encoding="utf-8"))
host = {}
if (GRADER / "host.json").is_file():
    host = json.loads((GRADER / "host.json").read_text(encoding="utf-8"))
threshold = specification["threshold"]
identity = specification["identity"]
findings = []
arm = "unknown-arm"
candidate = None
try:
    payload = json.load(sys.stdin)
    arm = str(payload.get("arm") or arm)
    candidate = payload.get("candidate")
except Exception as error:  # noqa: BLE001
    findings.append(f"the grader input is not JSON: {error}")


def normalize_text(text):
    text = re.sub(r"[`*]", "", text)
    text = " ".join(text.split()).lower()
    return text.rstrip(".!?;:,")


def key_of(item):
    """The item's identity, or None with a finding when a key is absent or of the wrong kind."""
    parts = []
    for entry in identity:
        value = item.get(entry["key"])
        if entry["kind"] == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                return None, f"key {entry['key']} is {value!r}; expected an integer"
            parts.append(str(value))
        elif not isinstance(value, str) or not value.strip():
            return None, f"key {entry['key']} is {value!r}; expected a non-empty string"
        elif entry["kind"] == "path":
            parts.append(value[2:] if value.startswith("./") else value)
        else:
            parts.append(normalize_text(value))
    return tuple(parts), None


def keys_of(items, what):
    keys = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            findings.append(f"{what} item {index} is {item!r}; expected an object")
            continue
        key, problem = key_of(item)
        if key is None:
            findings.append(f"{what} item {index}: {problem}")
            continue
        keys.add(key)
    return keys


returned = []
if not isinstance(candidate, dict) or not isinstance(candidate.get("items"), list):
    findings.append(f"the returned value is {json.dumps(candidate)[:200]}; expected an object whose key items holds a list")
else:
    returned = candidate["items"]

surveyed = subprocess.run(["/usr/bin/python3", "-B", str(GRADER / "survey.py"), str(WORKSPACE)], capture_output=True, text=True, check=False)
measures = None
if surveyed.returncode != 0:
    reason = [line for line in surveyed.stderr.splitlines() if line.strip()][-1:] or [f"exit status {surveyed.returncode}"]
    findings.append(f"survey.py cannot run over the workspace {WORKSPACE}: {reason[0]}")
else:
    truth = json.loads(surveyed.stdout)
    recorded = json.loads((GRADER / "oracle" / "candidate.json").read_text(encoding="utf-8"))["items"]
    true_keys = keys_of(truth, "survey")
    recorded_keys = keys_of(recorded, "recorded")
    lost = sorted(recorded_keys - true_keys)
    if lost:
        findings.append(f"the workspace is surveyed as given, and {len(lost)} recorded item(s) are absent from it now: " + "; ".join(":".join(key) for key in lost[:5]))
    returned_keys = keys_of(returned, "returned")
    matched = returned_keys & true_keys
    precision = len(matched) / len(returned_keys) if returned_keys else 0.0
    recall = len(matched) / len(true_keys) if true_keys else (1.0 if not returned_keys else 0.0)
    measures = {"precision": precision, "recall": recall, "returned": len(returned_keys), "matched": len(matched), "true_items": len(true_keys)}
    if recall < threshold:
        findings.append(f"recall {recall:.3f} is below {threshold}: {len(matched)} of {len(true_keys)} true items were returned")
    if precision < threshold:
        findings.append(f"precision {precision:.3f} is below {threshold}: {len(matched)} of {len(returned_keys)} returned items are true")

if measures is not None:
    build_dir = Path(host.get("build_dir") or Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local" / "state" / "foe" / "cross-harness" / "build")
    grades = build_dir / specification["task"] / "grades" / re.sub(r"[^A-Za-z0-9_.-]+", "-", arm)
    grades.mkdir(parents=True, exist_ok=True)
    logs = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%dT%H%M%S") + "-", dir=grades)) / "logs"
    logs.mkdir()
    recorded_measures = json.dumps(measures, indent=2) + "\n"
    (logs / MEASURES_FILE).write_text(recorded_measures, encoding="utf-8")
    (GRADER / MEASURES_FILE).write_text(recorded_measures, encoding="utf-8")
    print(MEASURES_PREFIX + json.dumps(measures), file=sys.stderr)
print("\n".join(findings))
'''

assert _SURVEY_GRADE_TEMPLATE.count("__MEASURES_PREFIX__") == 1, "the survey grade template names the measures prefix once"
assert _SURVEY_GRADE_TEMPLATE.count("__MEASURES_FILE__") == 1, "the survey grade template names the measures file once"
SURVEY_GRADE_SCRIPT = _SURVEY_GRADE_TEMPLATE.replace("__MEASURES_PREFIX__", MEASURES_PREFIX).replace("__MEASURES_FILE__", MEASURES_FILE)
assert _KEY_SEGMENT in ERROR_SURVEY_SCRIPT, "the error-messages survey script defines a key path segment as the corruption's edits do"

ERROR_SURVEY_TEXT = """Survey the Rust crates under crates/ for error messages that fail the AGENTS.md rule that every error names the key, event, or rule involved. The rule is applied by the following operational definition, so the answer is well defined.

An error message is the string literal of an `#[error("...")]` attribute whose literal is the attribute's only argument, written on one line whose first non-blank characters are `#[error(`, in a `.rs` file under crates/ that is neither named `*_test.rs` nor held under a `tests/` directory; `#[error(transparent)]` carries no message, and an attribute with arguments after the literal, `#[error("...", expr)]`, is outside the survey. A message names its subject when it contains a backtick-quoted identifier (a backtick, one or more characters, a backtick), a key path, or the placeholder `{key}`. A key path is two or more segments joined by single dots, each segment a name of two or more characters from lowercase letters, digits, and underscores that starts with a letter or underscore, or such a name in braces; `model.base_url` and `model.{key}` are key paths. Every other message fails the rule.

Return every failing message as the value {"items": [...]}, one object per message with `path` (the file's workspace-relative path), `line` (the line number of the attribute, counting from 1), and `message` (the literal between the quotes, as written). The grade compares the returned items with the survey the definition yields over the workspace as given: an item matches when its `path` and `line` match, precision and recall are computed over the matches, and the task passes when both are at least 0.9. Change no file under crates/."""

RULES_SURVEY_TEXT = """Survey docs/config.md for rule sentences whose key no test cites. The survey is defined operationally, so the answer is well defined.

A key is a heading of docs/config.md of the form `### `key``, and its section runs to the next heading. A rule sentence is a sentence of the section's prose: the lines outside fenced code blocks and outside table rows, grouped into paragraphs at blank lines, headings, and the start of a list item, with each paragraph split into sentences at a period, question mark, or exclamation mark that is followed by whitespace or the end of the paragraph and lies outside a backtick span. A test cites a key when a `///` doc comment line in a file named `*_test.rs` under crates/ contains `docs/config.md` and the first backtick span after it on the same line is a backtick-quoted identifier; that identifier is the key.

Return every rule sentence of every key that no test cites as the value {"items": [...]}, one object per sentence with `key` and `sentence` (the sentence text as written, with its markup). The grade compares the returned items with the survey the definition yields over the workspace as given: an item matches when its `key` matches and its `sentence` matches after backticks and asterisks are removed, whitespace is collapsed, letters are lowercased, and trailing punctuation is dropped; precision and recall are computed over the matches, and the task passes when both are at least 0.9. Change no file under crates/ or docs/."""


@dataclass(frozen=True)
class Survey:
    """One survey: its script, its text, the identity of an item, and the shape of the returned value."""

    name: str
    script: str
    text: str
    identity: tuple[dict[str, str], ...]
    returns: dict[str, Any]


def _returns(properties: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["items"],
        "properties": {"items": {"type": "array", "items": {"type": "object", "required": sorted(properties), "properties": properties}}},
    }


SURVEYS: dict[str, Survey] = {
    "error-messages": Survey(
        "error-messages",
        ERROR_SURVEY_SCRIPT,
        ERROR_SURVEY_TEXT,
        ({"key": "path", "kind": "path"}, {"key": "line", "kind": "integer"}),
        _returns({"path": {"type": "string"}, "line": {"type": "integer"}, "message": {"type": "string"}}),
    ),
    "config-rules": Survey(
        "config-rules",
        RULES_SURVEY_SCRIPT,
        RULES_SURVEY_TEXT,
        ({"key": "key", "kind": "text"}, {"key": "sentence", "kind": "text"}),
        _returns({"key": {"type": "string"}, "sentence": {"type": "string"}}),
    ),
}

SURVEY_BUDGET: dict[str, int] = {"model_calls": 120, "input_tokens": 4_000_000, "output_tokens": 200_000, "seconds": 3600}


def run_survey(script: Path, workspace: Path, *options: str) -> Any:
    """The survey script's JSON output over a workspace."""
    result = subprocess.run(["/usr/bin/python3", "-B", str(script), str(workspace), *options], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{script} over {workspace} exited {result.returncode}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def items_to_add(count: int, threshold: float = SURVEY_THRESHOLD) -> int:
    """The fewest items the truth must gain for an answer of `count` items to fall below the recall threshold."""
    added = 1
    while count / (count + added) >= threshold:
        added += 1
    return added


def unname_message(message: str) -> str:
    """The message with every way of naming its subject removed."""
    without = _ERROR_BACKTICK.sub(lambda match: match.group(0).strip("`"), message)
    without = _ERROR_KEY_PATH.sub(lambda match: match.group(0).replace(".", " "), without)
    return without.replace("{key}", "{name}")


def error_survey_edits(workspace: Path, added: int) -> list[dict[str, Any]]:
    """Edits that strip the subject from the first `added` messages that name one, in path and line order."""
    edits: list[dict[str, Any]] = []
    for file in sorted((workspace / CRATES).rglob("*.rs")):
        relative = file.relative_to(workspace).as_posix()
        if removal.is_test_path(relative):
            continue
        for number, line in enumerate(file.read_text(encoding="utf-8").split("\n"), start=1):
            match = _ERROR_ATTRIBUTE.match(line)
            if not match:
                continue
            unnamed = unname_message(match.group(2))
            if unnamed == match.group(2):
                continue
            edits.append({"path": relative, "line": number, "old": line, "new": match.group(1) + unnamed + match.group(3)})
            if len(edits) == added:
                return edits
    raise ValueError(f"the tree under {workspace / CRATES} holds {len(edits)} messages that name a subject, and the corruption needs {added}")


def rules_survey_edits(workspace: Path, everything: list[dict[str, Any]], added: int) -> list[dict[str, Any]]:
    """Edits that remove every citation of the cited keys whose sentences, largest section first, add up to at least `added`."""
    cited = sorted((entry for entry in everything if entry["cited"]), key=lambda entry: (-len(entry["sentences"]), entry["key"]))
    chosen: list[str] = []
    total = 0
    for entry in cited:
        if total >= added:
            break
        chosen.append(entry["key"])
        total += len(entry["sentences"])
    if total < added:
        raise ValueError(f"the cited keys of {workspace / 'docs/config.md'} hold {total} sentences, and the corruption needs {added}")
    citation = re.compile(r"^\s*///.*" + re.escape(_CITATION_DOCUMENT) + r"[^`]*`([A-Za-z_][A-Za-z0-9_]*)`")
    edits: list[dict[str, Any]] = []
    for file in sorted((workspace / CRATES).rglob("*_test.rs")):
        relative = file.relative_to(workspace).as_posix()
        for number, line in enumerate(file.read_text(encoding="utf-8").split("\n"), start=1):
            match = citation.match(line)
            if match and match.group(1) in chosen:
                edits.append({"path": relative, "line": number, "old": line, "new": line.replace(_CITATION_DOCUMENT, "the contract document", 1)})
    return edits


def _apply_edits(workspace: Path, edits: list[dict[str, Any]]) -> None:
    for edit in edits:
        target = workspace / edit["path"]
        lines = target.read_text(encoding="utf-8").split("\n")
        assert lines[edit["line"] - 1] == edit["old"], f"{target}:{edit['line']} reads {lines[edit['line'] - 1]!r}, and the edit expected {edit['old']!r}"
        lines[edit["line"] - 1] = edit["new"]
        target.write_text("\n".join(lines), encoding="utf-8")


@dataclass(frozen=True)
class AuthoredSurvey:
    task: Task
    directory: Path
    items: list[dict[str, Any]]
    edits: list[dict[str, Any]]


def author_survey(repo: Path, survey: Survey, out: Path, name: str, commit: str = "HEAD", keep_workspace: bool = False) -> AuthoredSurvey:
    """Write the task directory for one survey over the repository at the commit; see the module docstring for what it holds."""
    if out.exists():
        raise FileExistsError(f"{out} exists; author writes a fresh task directory")
    commit = removal.git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()
    out.mkdir(parents=True)
    try:
        return _write_survey(repo, survey, out, name, commit, keep_workspace)
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise


def _write_survey(repo: Path, survey: Survey, out: Path, name: str, commit: str, keep_workspace: bool) -> AuthoredSurvey:
    workspace, grader = out / WORKSPACE, out / GRADER
    protocol.archive(repo, commit, workspace)
    _write(workspace / removal.CHECKS_SCRIPT, SURVEY_CHECKS_SCRIPT, executable=True)
    _write(grader / SURVEY_SCRIPT, survey.script, executable=True)
    items = run_survey(grader / SURVEY_SCRIPT, workspace)
    if not items:
        raise ValueError(f"the survey {survey.name} finds no item at {commit}; a survey task needs a non-empty answer")
    _write(grader / protocol.ORACLE / "candidate.json", json.dumps({"items": items}, indent=2) + "\n")
    added = items_to_add(len(items))
    if survey.name == "error-messages":
        edits = error_survey_edits(workspace, added)
    else:
        edits = rules_survey_edits(workspace, run_survey(grader / SURVEY_SCRIPT, workspace, "--all"), added)
    corruption = grader / protocol.CORRUPTIONS / SURVEY_CORRUPTION
    _write(corruption / "edits.json", json.dumps(edits, indent=2) + "\n")
    _write(corruption / "apply.py", EDIT_LINES_SCRIPT, executable=True)
    # The corruption is proven on a copy: the script must find every recorded
    # item still and at least `added` more, so the recorded answer's recall
    # falls below the threshold.
    scratch = out / "corruption-check"
    shutil.copytree(workspace, scratch, symlinks=True)
    try:
        _apply_edits(scratch, edits)
        corrupted = run_survey(grader / SURVEY_SCRIPT, scratch)
    finally:
        shutil.rmtree(scratch)
    identity = [entry["key"] for entry in survey.identity]
    before = {tuple(str(item[key]) for key in identity) for item in items}
    after = {tuple(str(item[key]) for key in identity) for item in corrupted}
    if not before <= after or len(after) - len(before) < added:
        raise ValueError(f"the corruption of {survey.name} leaves {len(after)} items where {len(before)} were recorded and {added} had to be added")

    _write(grader / removal.SPECIFICATION_FILE, json.dumps({"task": name, "threshold": SURVEY_THRESHOLD, "identity": list(survey.identity)}, indent=2) + "\n")
    _write(grader / "grade.py", SURVEY_GRADE_SCRIPT, executable=True)
    _write(grader / protocol.GRADE_SCRIPT, removal.GRADE_WRAPPER, executable=True)
    task = Task(
        name=name,
        family=FAMILY,
        class_name=SURVEY,
        text=protocol.autonomy_text(survey.text),
        correct_statuses=frozenset({COMPLETED}),
        correct_codes=frozenset(),
        budget=dict(SURVEY_BUDGET),
        protected=PROTECTED,
        metadata={
            protocol.SOURCE_KEY: protocol.source_record(repo, out, commit=commit),
            "survey": survey.name,
            "returns": survey.returns,
            "threshold": SURVEY_THRESHOLD,
            "item_count": len(items),
            "corruption_adds": len(after) - len(before),
            "review": "revised: the text was written in specification voice and read by a person; the operational definition is the survey script's",
        },
    )
    _finish(out, task, repo, keep_workspace)
    return AuthoredSurvey(task, out, items, edits)


def _parse_prefixed(stderr: str, prefix: str) -> dict[str, Any] | None:
    for line in reversed(stderr.splitlines()):
        if line.startswith(prefix):
            value = json.loads(line[len(prefix) :])
            if not isinstance(value, dict):
                raise ValueError(f"the grade line {line!r} does not hold an object")
            return value
    return None


def parse_units(stderr: str) -> dict[str, bool] | None:
    """Each unit's verdict from a fan-out grade's standard error, or None when the grade printed none."""
    value = _parse_prefixed(stderr, UNITS_PREFIX)
    if value is None:
        return None
    if not all(isinstance(passed, bool) for passed in value.values()):
        raise ValueError(f"the {UNITS_PREFIX.strip()} line holds {value!r}; expected an object of unit name to a boolean")
    return {str(name): passed for name, passed in value.items()}


def parse_measures(stderr: str) -> dict[str, float] | None:
    """The precision, recall, and counts from a survey grade's standard error, or None when the grade printed none."""
    value = _parse_prefixed(stderr, MEASURES_PREFIX)
    if value is None:
        return None
    return _checked_measures(value, MEASURES_PREFIX.strip())


def _checked_measures(value: dict[str, Any], what: str) -> dict[str, float]:
    missing = [key for key in MEASURE_KEYS if key not in value]
    if missing:
        raise ValueError(f"the {what} lacks {', '.join(missing)}")
    return {key: value[key] for key in MEASURE_KEYS}


def _read_recorded(root: Path, name: str) -> dict[str, Any] | None:
    path = root / GRADER / name
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not hold an object")
    return value


def read_units(root: Path) -> dict[str, bool] | None:
    """Each unit's verdict from the `units.json` a fan-out grade left in the materialized root's grader directory, or None when no grade left one."""
    value = _read_recorded(root, UNITS_FILE)
    if value is None:
        return None
    if not all(isinstance(passed, bool) for passed in value.values()):
        raise ValueError(f"{root / GRADER / UNITS_FILE} holds {value!r}; expected an object of unit name to a boolean")
    return {str(name): passed for name, passed in value.items()}


def read_measures(root: Path) -> dict[str, float] | None:
    """The precision, recall, and counts from the `measures.json` a survey grade left in the materialized root's grader directory, or None when no grade left one."""
    value = _read_recorded(root, MEASURES_FILE)
    if value is None:
        return None
    return _checked_measures(value, str(root / GRADER / MEASURES_FILE))


def _print_controls(results: list[removal.TimedControl]) -> bool:
    held = True
    for timed in results:
        control = timed.control
        verdict = "held" if control.held else "FAILED"
        expected = "pass" if control.expected_pass else "fail"
        observed = "passed" if control.passed else "failed"
        print(f"{control.name}: {verdict} (expected to {expected}, {observed}) in {timed.seconds:.0f}s")
        for finding in control.findings:
            print(f"  {finding[:400]}")
        held = held and control.held
    return held


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    fan_out = commands.add_parser(FAN_OUT, help="write a fan-out task directory from one sweep commit")
    fan_out.add_argument("--repo", required=True, type=Path, help="the repository the commit lives in")
    fan_out.add_argument("--commit", required=True, help="the sweep commit")
    fan_out.add_argument("--out", required=True, type=Path, help="the task directory to create")
    fan_out.add_argument("--name", required=True, help="the task's name")
    fan_out.add_argument("--allow-traces", nargs="*", default=[], metavar="IDENTIFIER", help="added identifiers whose hits in the workspace are accepted")
    fan_out.add_argument("--revert-unit", help="the unit the revert corruption restores; default the first checked leaf unit with implementation files")
    fan_out.add_argument("--keep-workspace", action="store_true", help="keep the workspace copy beside the recipe, for inspection")
    survey = commands.add_parser(SURVEY, help="write a survey task directory over the repository at a commit")
    survey.add_argument("--repo", required=True, type=Path, help="the repository to survey")
    survey.add_argument("--survey", required=True, choices=sorted(SURVEYS), help="which survey")
    survey.add_argument("--commit", default="HEAD", help="the commit whose tree is surveyed; default HEAD")
    survey.add_argument("--out", required=True, type=Path, help="the task directory to create")
    survey.add_argument("--name", required=True, help="the task's name")
    survey.add_argument("--keep-workspace", action="store_true", help="keep the workspace copy beside the recipe, for inspection")
    verifying = commands.add_parser("verify", help="run the grader controls with a build-length timeout")
    verifying.add_argument("--task", required=True, type=Path, help="the task directory")
    verifying.add_argument("--scratch", required=True, type=Path, help="where each control's root is materialized")
    verifying.add_argument("--timeout", type=int, default=GRADE_TIMEOUT_SECONDS, help="seconds one grade may take")
    args = parser.parse_args(argv)

    try:
        if args.command == FAN_OUT:
            authored = author_fan_out(args.repo.resolve(), args.commit, args.out.resolve(), args.name, args.allow_traces, args.keep_workspace, args.revert_unit)
            print(f"task {authored.task.name} written to {authored.directory}")
            for unit in authored.units:
                state = "checked" if unit.checked else "unchecked"
                print(f"unit {unit.name}: {state}, {len(unit.implementation)} implementation file(s), {len(unit.tests)} hidden test(s)")
            print(f"revert corruption: {authored.revert_unit}")
            if authored.shared is None:
                print(f"rename corruption: none; {authored.task.metadata['shared_element_absent']}")
            else:
                print(f"rename corruption: {authored.shared.element} in {authored.shared.unit}, used by {', '.join(authored.shared.used_by)}")
            print(f"specification sentences: {sum(len(found) for found in authored.sentences.values())}")
            return 0
        if args.command == SURVEY:
            authored_survey = author_survey(args.repo.resolve(), SURVEYS[args.survey], args.out.resolve(), args.name, args.commit, args.keep_workspace)
            print(f"task {authored_survey.task.name} written to {authored_survey.directory}")
            print(f"items: {len(authored_survey.items)}; the corruption edits {len(authored_survey.edits)} line(s)")
            return 0
        started = time.monotonic()
        results = removal.verify(args.task.resolve(), args.scratch.resolve(), args.timeout)
        held = _print_controls(results)
        print(f"wall time: {time.monotonic() - started:.0f}s")
        return 0 if held else 1
    except (ValueError, FileExistsError, FileNotFoundError, RuntimeError) as error:
        print(f"teams: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
