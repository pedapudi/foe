#!/usr/bin/python3
"""Author the teams tasks: fan-out tasks, harvested from a sweep commit or constructed, and survey tasks whose answer a script computes.

A `fan-out` task is one change applied to many similar units. It is authored
either from a sweep commit of this repository or by construction; the two
paths differ only in where the units, the change, and the tests come from,
and produce the same task directory. The harvested path is described first
and the constructed path after it.

A harvested fan-out comes from a sweep commit through `feature_removal.author`,
which supplies the fixture, the oracle, the hidden tests, the trace scrub,
and the recipe; this module adds what a fan-out needs. A unit is a crate
`crates/<name>` or a top-level directory or file in which the commit changes
an implementation file, named by that path. Three kinds of place are not
units. A crate or directory whose touched files are all tests or fixtures is
none: the grader restores the commit form of every hidden test, so such a
place holds nothing for the agent to change. A place whose touched
implementation files are all interface files is none either. The interface is
what no worker can be given: the implementation files of a touched crate that
another touched crate depends on, which the interface node of the graph
writes before any worker starts, and the files that lie in no directory a
write grant can name, such as a top-level `README.md`. What remains is the
set a delegation can hand out, and authoring refuses a commit that leaves
fewer than two of them or more than UNIT_MAX, the workers one delegation
runs.

Every unit carries a verdict of its own, and authoring refuses a commit that
leaves one without. For a crate the verdict is `cargo test -p <package>`
with the unit's hidden tests restored and every hidden test function seen
running. For a directory or a file the verdict is every hidden `*_test.py`
file run with the interpreter, and when the commit gives the unit none, the
authoring tool generates one: it records, per file the change touches in the
unit, the lines that tell the commit's form of the file apart from the
parent's, and the check requires every added line to stand in the file and
no removed line to. The uniformity of the change is the fraction of units
that pass. The task text names no unit, because how the change divides is
what the task measures. The whole change is judged by the workspace check,
which is `cargo test --workspace`, `cargo clippy --workspace -- -D
warnings`, and `scripts/loc.sh`, and by the specification sentences the
commit added under `docs/`. `checks/run.sh` in the workspace runs the
workspace check. A task may narrow that check to a set of packages, which
`workspace_checks_script` writes and `metadata.packages` records; the grade
then runs those packages in place of the workspace. Both harnesses under
comparison run their commands inside a kernel sandbox, and the suites of
`foe-core`, `foe-code`, `foe-transport`, `foe-view`, and the command-line
crate bind or connect loopback sockets or start an interpreter over a socket
pair, which a sandbox denies. A check that runs one of them can pass for no
arm however well the arm worked, so a task whose change stands in crates a
sandbox leaves alone names them; `SANDBOX_SAFE_PACKAGES` holds the set that
was measured on this host.

Two corruptions of the solved workspace are recorded. `revert-one-unit`
restores the parent form of one unit's implementation files; the unit is one
that no other unit's crate depends on, so its own tests fail and every other
unit's pass. `rename-shared-element` renames one
public Rust item defined in a touched file of one unit that a crate outside
every checked unit's dependency closure uses, so every unit's tests pass and
the workspace check fails; a commit in which no item qualifies records the
reason under `metadata.shared_element_absent` and carries the first
corruption alone. `task.json` records the family `teams`, the class
`fan-out`, `metadata.units` as an object of unit name to the unit's
workspace-relative paths, `metadata.n`, the hidden tests and package of
each unit, the units that carry a verdict, `metadata.generated_unit_checks`
naming the generated check of each unit that needed one,
`metadata.interface_paths`, the files no worker can be given, and
`metadata.division`, which records the
write root a delegation would grant each unit and that no two of them
overlap. That last is what separates this class from the `coherent` controls
of `tasks/coherent.py`, whose change lies in one directory and admits no such
division. The grader keeps no `oracle.patch`: the oracle overlay and the hidden
tests hold the commit form of every changed file, and a sweep touches many.

A constructed fan-out states its change as a sequence of scripted edits
rather than reading it from a commit, so a change no commit of this
repository made can be posed. `Construction` holds them, in three states:
the fixture an arm starts from, which is the base tree with the raised line
ceilings and the edits that put the shared element in place; the oracle
overlay, which is the fixture with the change applied; and the two
corruptions of that overlay. Every edit asserts that its source stands
exactly once in the file it edits, so a construction whose anchors have
moved fails authoring rather than leaving a task that silently lacks part of
its change. `tasks/coherent.py` constructs the coherent controls of this
family the same way, and the two differ in the division alone: a control's
whole change lies in one directory, and a construction here gives each unit
a crate directory of its own, which is what a write grant can name.

A construction names, for each unit, that directory, the package whose suite
carries the unit's verdict, the files the change touches there, and the test
file whose hidden copy judges it. It names the crate that holds the shared
element every unit builds against, which no worker is given and which the
interface node of the teams graph writes first, and it names the test that
judges the units against each other, which runs in the whole-change step
rather than in any unit's. `check_construction` refuses a construction whose
units overlap, which numbers fewer than two or more than UNIT_MAX, which
leaves a unit without a verdict or with a change under UNIT_LINES_MIN lines,
whose change reaches outside every unit and the interface, or whose
whole-change check runs a package a sandbox breaks. Two constructions are
defined. `input-bound-named-in-refusal` bounds every input read from outside
the process against a table in `crates/contract` and names the bound in the
refusal, over `crates/context`, `crates/evidence`, and `crates/workflow`.
`left-out-input-is-named-rather-than-dropped` makes a value derived from a
log or a bundle state what it left out, against a table in `crates/log`,
over `crates/evidence`, `crates/telemetry`, and `crates/workflow`.

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

    /usr/bin/python3 evals/cross_harness/tasks/teams.py fan-out --repo . --commit SHA --out DIR --name NAME [--text FILE]
    /usr/bin/python3 evals/cross_harness/tasks/teams.py construct --task NAME --repo . --commit SHA --out DIR
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
# The units one fan-out may hold. A delegation runs at most this many worker
# episodes, and the survey's and the delegating node's report schemas admit
# at most this many entries, so a task naming more units than this cannot be
# divided the way the graph declares. `contracts/graphs.py` WORKER_EPISODES
# holds the same number and `tasks/teams_test.py` holds them to it.
UNIT_MAX = 8
# The packages whose own test suites pass inside a kernel sandbox, measured on
# this host by running `cargo test -p <package>` twice for each: under the
# Codex CLI workspace sandbox policy, and inside a foe episode with
# `sandbox.mode: required`. The suites left out are those of `foe-core`,
# `foe-code`, `foe-transport`, `foe-view`, and the command-line crate `foe`.
# They bind or connect loopback sockets, place a store outside a declared
# write root, or start an interpreter over a socket pair, and one sandbox or
# the other denies each, so a check suite that runs one of them can pass for
# no arm however well the arm worked. `foe-code` fails under the Codex policy
# alone and `foe-log` inside a foe episode alone, so the set is what passed
# under both. A constructed fan-out is held to this set, and `admission.py`
# measures a whole task the same way.
SANDBOX_SAFE_PACKAGES: frozenset[str] = frozenset(
    {"foe-context", "foe-contract", "foe-evidence", "foe-log", "foe-team", "foe-telemetry", "foe-workflow"}
)
# The one test a package of that set needs left out of a check suite, by a
# substring of its name. `foe-log` passes under the Codex sandbox policy and
# fails inside a foe episode on this test alone, which makes a directory
# unwritable and then writes to it: the sandbox refuses the write before the
# writer under test sees it, so the test measures the sandbox rather than the
# change. A construction whose check runs such a package states the same
# substring as its `check_skip`, and the hidden grade runs the whole suite on
# the host, where no sandbox stands in the way.
SANDBOX_SKIPPED_TESTS: dict[str, str] = {"foe-log": "file_failures_preserve_the_original_recording_error"}
# The implementation lines one unit's change writes, below which delegating
# the unit costs more than doing it. A worker's episode carries the task text,
# the survey, and the interface, and returns a report, and a change of a few
# lines is finished before that traffic is paid for.
UNIT_LINES_MIN = 12
# The longest line a generated unit check records. A generated bundle or a
# recorded fixture holds lines of thousands of characters, and whether such a
# line stands unchanged says nothing a reader of the finding can act on.
GENERATED_LINE_MAX = 300
# Where a generated unit check and its recorded lines live, as a
# workspace-relative directory. They reach the graded tree only as hidden
# tests, which the grade copies into its own copy of the workspace.
GENERATED_CHECK_DIRECTORY = "checks/units"
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
class Edit:
    """One scripted replacement in one file: the source must stand exactly once there.

    A construction is a sequence of these, and the assertion is what keeps a
    construction honest: an edit whose source has moved fails the authoring
    run rather than leaving a task that silently lacks part of its change.
    """

    path: str
    old: str
    new: str

    def apply(self, root: Path) -> None:
        target = root / self.path
        if not target.is_file():
            raise FileNotFoundError(f"{target} is absent; the edit of {self.path} has nothing to change")
        text = target.read_text(encoding="utf-8")
        found = text.count(self.old)
        if found != 1:
            raise ValueError(f"{target}: the edit's source stands {found} times; an edit's source stands exactly once")
        target.write_text(text.replace(self.old, self.new), encoding="utf-8")

    @property
    def written_lines(self) -> int:
        """The non-blank lines the edit writes that its source did not already hold, ignoring indentation.

        This is what states the size of a change: an edit that rewrites a
        function writes every line of the new form, and counting the
        difference in line count would report a rewrite as almost nothing.
        """
        before = {line.strip() for line in self.old.splitlines()}
        return sum(1 for line in self.new.splitlines() if line.strip() and line.strip() not in before)


def ceiling_edits(table_row: str, readme_row: str | None, prose: tuple[tuple[str, str], ...], old: int, new: int) -> list[Edit]:
    """The edits that move one line ceiling: the table in `scripts/loc.sh`, and the number every document quotes.

    `scripts/loc.sh` fails when `AGENTS.md`, `README.md`, or `docs/design.md`
    quotes a ceiling other than the one it holds, so they move together. A
    group ceiling stands in prose alone, so `readme_row` is None for one.
    `CEILING` in a prose anchor stands for the number with its separator.
    """
    quoted_old, quoted_new = f"{old:,}", f"{new:,}"
    edits = [Edit("scripts/loc.sh", f"{table_row}| {old} |", f"{table_row}| {new} |")]
    if readme_row is not None:
        edits.append(Edit("README.md", f"| {readme_row} | {quoted_old} |", f"| {readme_row} | {quoted_new} |"))
    for path, sentence in prose:
        edits.append(Edit(path, sentence.replace("CEILING", quoted_old), sentence.replace("CEILING", quoted_new)))
    return edits


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
        """Whether the commit's own tests give the unit a verdict: implementation files to change, and a hidden test file for a crate or a runnable one for a directory.

        A unit this leaves without a verdict is given a generated one by
        `write_generated_checks` when it is a directory or a file.
        """
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


def revert_unit_for(checked: list[Unit], graph: dict[str, set[str]]) -> Unit:
    """The unit carrying a verdict whose implementation files no other such unit's crate depends on, first in name order."""
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


def shared_element(repo: Path, commit: str, units: list[Unit], graph: dict[str, set[str]], checked: list[str]) -> tuple[SharedElement | None, str]:
    """The first public item a rename corruption can use, or None with the reason.

    An item qualifies when a touched implementation file of a crate unit
    defines it, some crate outside the dependency closure of every unit that
    carries a verdict uses it, no file of a crate inside that closure uses it
    except the defining unit's own non-test files, and, when the defining
    unit carries a verdict, none of its test files uses it. Whole-word search
    is the test for use, so a common word is rejected more often than an item
    is accepted wrongly. `checked` names the units that carry a verdict.
    """
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
                if unit.name in checked and any(removal.is_test_path(user) for user in own):
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
        f"of the units that carry a verdict ({', '.join(sorted(compiled)) or 'none'}) and by nothing inside it"
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


def grantable(unit: Unit, workspace: Path) -> bool:
    """Whether a delegation can give the unit a write root of its own, which docs/config.md `write` requires to be a directory.

    A commit that changes a top-level file, such as `README.md`, leaves a
    unit no worker can be granted: the nearest directory that holds it is the
    workspace, which overlaps every other unit. Such a file belongs to the
    node that writes what the workers cannot.
    """
    return (workspace / unit.name).is_dir()


def interface_only(unit: Unit, interface: list[str]) -> bool:
    """Whether every implementation file of the unit belongs to the interface, the set no worker can be given.

    Such a place is what the interface node of the teams graph writes before
    any worker starts, so counting it as a unit would give a worker work the
    graph has already assigned elsewhere.
    """
    return bool(unit.implementation) and all(path in interface for path in unit.implementation)


def check_unit_set(units: list[Unit], checked: list[Unit], commit: str) -> None:
    """Refuse a unit set a delegation cannot hand out or a grade cannot judge.

    A fan-out is one change over two or more units, a delegation runs at most
    UNIT_MAX workers, and the uniformity of the change is read from the units
    that pass, so a unit without a verdict of its own would leave the measure
    silent about part of the change.
    """
    named = ", ".join(unit.name for unit in units) or "none"
    if len(units) < 2:
        raise ValueError(f"commit {commit} leaves {len(units)} unit(s) once the interface is set apart ({named}); a fan-out is one change over two or more units")
    if len(units) > UNIT_MAX:
        raise ValueError(f"commit {commit} holds {len(units)} units, and a delegation runs at most {UNIT_MAX} workers: {named}")
    unchecked = [unit.name for unit in units if unit not in checked]
    if unchecked:
        raise ValueError(f"commit {commit} leaves {', '.join(unchecked)} without a verdict of its own; every unit of a fan-out carries one")


def fan_out_text(subject: str, body: str, sentences: dict[str, list[str]], text: str | None = None) -> str:
    """The specification paragraphs of a fan-out, followed by the checks paragraph and the sentence list.

    `text` replaces the paragraphs drafted from the commit subject and body
    with prose the caller supplies, for a task whose specification has been
    written rather than drafted. The text names no unit: how the change
    divides is what the task measures, so an arm shown the division has been
    given the answer.
    """
    if text:
        parts = [text.strip()]
    else:
        parts = [subject.rstrip(".") + "."]
        if body:
            parts.append(body)
    parts.append(
        "Each part of the tree the change reaches is judged by the tests that cover it. "
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


def workspace_checks_script(packages: tuple[str, ...] = (), skip: str = "", reason: str = "") -> str:
    """The check the whole change is judged on: the tests of the packages named, clippy with warnings denied on the same packages, and the line budgets.

    A task that names no package is judged over the whole workspace. Both
    harnesses under comparison run their commands inside a kernel sandbox,
    and the suites of `foe-core`, `foe-code`, `foe-transport`, `foe-view`,
    and the command-line crate bind or connect loopback sockets or start an
    interpreter over a socket pair, which a sandbox denies. A check over the
    whole workspace runs those suites and can pass for no arm, so a task
    whose change lies in crates a sandbox leaves alone names those crates
    here and is judged over them. `skip` names a substring of the test names
    the suite leaves out and `reason` states why, for the single tests a
    sandbox breaks inside a crate that otherwise passes; the hidden grade
    runs the whole suite on the host, where no sandbox stands in the way.
    """
    selection = " ".join(f"-p {name}" for name in packages) or "--workspace"
    targets = " --all-targets" if packages else ""
    filtered = f" -- --skip {skip}" if skip else ""
    subject = "the tests of " + ", ".join(packages) if packages else "every crate's tests"
    note = f"# {reason}\n" if reason else ""
    return (
        "#!/bin/sh\n"
        f"# The check the whole change is judged on: {subject}, clippy with warnings denied on the same crates,\n"
        "# and the line budgets.\n"
        + note
        + "set -eu\n"
        'cd "$(dirname "$0")/.."\n'
        f"cargo test {selection}{filtered}\n"
        f"cargo clippy {selection}{targets} -- -D warnings\n"
        "scripts/loc.sh\n"
    )


WORKSPACE_CHECKS_SCRIPT = workspace_checks_script()

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
    # The whole change is judged over the packages `integration` names and
    # over the whole workspace when it names none. `test_packages` are the
    # suites the unit steps did not already run, which is where a task puts
    # the test that judges the units against each other; `lint_packages` are
    # every package the change touches.
    integration = specification.get("integration") or {}
    tested = [argument for name in integration.get("test_packages", []) for argument in ("-p", name)] or ["--workspace"]
    named_packages = [argument for name in integration.get("lint_packages", []) for argument in ("-p", name)]
    linted = named_packages + ["--all-targets"] if named_packages else ["--workspace"]
    whole = run("integration test", [cargo, "test", "--target-dir", str(target), *tested], copy, logs)
    judging = integration.get("test")
    if judging and whole is not None:
        ran = {match.group(1) for match in (_TEST_LINE.match(line) for line in whole.splitlines()) if match}
        if judging not in ran:
            findings.append(f"integration: the test {judging} judges the units against each other and did not run")
    run("integration clippy", [cargo, "clippy", "--target-dir", str(target), *linted, "--", "-D", "warnings"], copy, logs)
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

_UNIT_CHECK_TEMPLATE = r'''#!/usr/bin/python3
"""Whether one unit of a sweep carries the change, for a unit the commit gives no runnable test.

The unit is a directory rather than a crate, so no cargo suite judges it.
`__DATA__` beside this file records, for each file the change
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
UNIT = "__UNIT__"
# Findings beyond this many say the same thing again; the count states the rest.
REPORTED_MAX = 20

recorded = json.loads((HERE / "__DATA__").read_text(encoding="utf-8"))
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
'''

assert _UNIT_CHECK_TEMPLATE.count("__UNIT__") == 1, "the unit check template names the unit once"
assert _UNIT_CHECK_TEMPLATE.count("__DATA__") == 2, "the unit check template names its data file in the docstring and in the read"


def check_slug(unit: str) -> str:
    """The file-name stem of a unit's generated check: the unit's name with every run of other characters as one hyphen."""
    return re.sub(r"[^A-Za-z0-9]+", "-", unit).strip("-")


def discriminating_lines(repo: Path, parent: str, commit: str, path: str, added_file: bool) -> dict[str, list[str]]:
    """The lines that tell the commit's form of one file apart from its parent's, stripped of surrounding whitespace.

    A line the two forms share says nothing about whether the change was
    made, and a line longer than GENERATED_LINE_MAX is left out because a
    generated bundle or a recorded fixture holds lines no reader of a
    finding can act on. `added_file` names a file the commit creates, whose
    parent form is empty.
    """

    def lines(text: str) -> list[str]:
        return [stripped for stripped in (line.strip() for line in text.splitlines()) if stripped and len(stripped) <= GENERATED_LINE_MAX]

    after = lines(removal.show_file(repo, commit, path).decode("utf-8", "replace"))
    before = [] if added_file else lines(removal.show_file(repo, parent, path).decode("utf-8", "replace"))
    return {
        "added": [line for line in dict.fromkeys(after) if line not in set(before)],
        "removed": [line for line in dict.fromkeys(before) if line not in set(after)],
    }


def generated_check(repo: Path, parent: str, commit: str, unit: Unit, statuses: dict[str, str]) -> dict[str, dict[str, list[str]]]:
    """The recorded lines of a unit's generated check, by file; empty when no file of the unit tells its two forms apart."""
    recorded: dict[str, dict[str, list[str]]] = {}
    for path in unit.implementation:
        found = discriminating_lines(repo, parent, commit, path, statuses[path] == removal.ADDED)
        if found["added"] or found["removed"]:
            recorded[path] = found
    return recorded


def write_generated_checks(grader: Path, repo: Path, parent: str, commit: str, units: list[Unit], statuses: dict[str, str]) -> dict[str, str]:
    """Write a generated check for every unit that is a directory the commit gives no runnable test for; the check path of each, by unit.

    A crate unit is left alone: its verdict is its package's cargo suite,
    and a generated line check over the same files would also fail under the
    rename corruption, which must leave every unit passing.
    """
    written: dict[str, str] = {}
    for unit in units:
        if unit.package is not None or unit.python_tests:
            continue
        recorded = generated_check(repo, parent, commit, unit, statuses)
        if not recorded:
            continue
        slug = check_slug(unit.name)
        data = f"{GENERATED_CHECK_DIRECTORY}/{slug}.json"
        check = f"{GENERATED_CHECK_DIRECTORY}/{slug}_test.py"
        _write(grader / removal.HIDDEN_TESTS / data, json.dumps(recorded, indent=2) + "\n")
        _write(
            grader / removal.HIDDEN_TESTS / check,
            _UNIT_CHECK_TEMPLATE.replace("__UNIT__", unit.name).replace("__DATA__", f"{slug}.json"),
            executable=True,
        )
        written[unit.name] = check
    return written


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
    text: str | None = None,
) -> AuthoredFanOut:
    """Write the task directory for one sweep commit; see the module docstring for what it holds."""
    authored = removal.author(repo, commit, out, name, "solvable", allow_traces, keep_workspace=True)
    try:
        return _write_fan_out(repo, authored, out, name, keep_workspace, revert_unit, text)
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise


def _write_fan_out(
    repo: Path, authored: removal.Authored, out: Path, name: str, keep_workspace: bool, revert_name: str | None, text: str | None
) -> AuthoredFanOut:
    source = authored.task.metadata[protocol.SOURCE_KEY]
    commit, parent = source["commit"], source["parent"]
    workspace, grader = out / WORKSPACE, out / GRADER
    diffs = removal.parse_diff(removal.commit_diff(repo, commit))
    tests, implementation = removal.partition(diffs)
    statuses = {diff.path: diff.status for diff in diffs}
    touched = partition_units(diffs, workspace)
    graph = crate_dependencies(workspace)
    # The interface is what no worker can be given: the implementation files
    # another touched crate depends on, and the files that lie in no directory
    # a write grant can name. A place whose whole change is interface is no
    # unit. Counting such places as units named more units than a delegation
    # can hold and gave a worker work the graph had already assigned.
    interface = sorted(set(interface_paths(touched, graph)) | {path for unit in touched if not grantable(unit, workspace) for path in unit.implementation})
    units = [unit for unit in touched if not interface_only(unit, interface)]
    generated = write_generated_checks(grader, repo, parent, commit, units, statuses)
    checked = [unit for unit in units if unit.checked or unit.name in generated]
    check_unit_set(units, checked, commit)

    # The feature-removal grader and corruption give way to the fan-out's own,
    # and the commit diff is removed: the oracle overlay and the hidden tests
    # hold the commit form of every changed file, and a sweep touches many.
    (grader / "grade.py").unlink()
    (grader / removal.ORACLE_PATCH).unlink()
    shutil.rmtree(grader / protocol.CORRUPTIONS)
    _write(workspace / removal.CHECKS_SCRIPT, WORKSPACE_CHECKS_SCRIPT, executable=True)

    by_name = {unit.name: unit for unit in units}
    if revert_name is None:
        reverted = revert_unit_for(checked, graph)
    elif revert_name in by_name and by_name[revert_name] in checked and by_name[revert_name].implementation:
        reverted = by_name[revert_name]
    else:
        raise ValueError(f"--revert-unit names {revert_name!r}, which is not a checked unit with implementation files; the units are {', '.join(by_name)}")
    revert = grader / protocol.CORRUPTIONS / REVERT_CORRUPTION
    restore = [path for path in reverted.implementation if statuses[path] == removal.MODIFIED]
    remove = [path for path in reverted.implementation if statuses[path] == removal.ADDED]
    for path in restore:
        removal._write(revert / "parent" / path, removal.show_file(repo, parent, path))  # noqa: SLF001
    _write(revert / "revert.json", json.dumps({"unit": reverted.name, "restore": restore, "remove": remove}, indent=2) + "\n")
    _write(revert / "apply.py", REVERT_SCRIPT, executable=True)

    # The rename corruption looks over every place the commit touched, the
    # interface included: it is a control on the workspace check, and the
    # item it renames need not lie in a unit a worker would be given.
    shared, reason = shared_element(repo, commit, touched, graph, [unit.name for unit in checked])
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
                "python_tests": [*unit.python_tests, *([generated[unit.name]] if unit.name in generated else [])],
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
            "generated_unit_checks": dict(sorted(generated.items())),
            "interface_paths": interface,
            "revert_unit": reverted.name,
            "shared_element": None if shared is None else shared.to_dict(),
            # What makes this a fan-out rather than a control: a delegation
            # can grant each unit one of these roots and no two of them
            # overlap, so the division the survey's own instruction requires
            # exists.
            "division": {"write_roots": [unit.name for unit in units], "separable": True},
            "review": (
                "revised: the specification paragraphs were supplied to the authoring tool as written; the checks paragraph and the sentence list are the tool's"
                if text
                else "pending: the text is drafted from the commit message and the document changes and has not been read by a person"
            ),
        }
    )
    if shared is None:
        metadata["shared_element_absent"] = reason
    task = replace(
        authored.task,
        family=FAMILY,
        class_name=FAN_OUT,
        text=fan_out_text(subject, body, authored.sentences, text),
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


# ---- constructed fan-outs -------------------------------------------------------

# The band a constructed fan-out's text sits in: long enough to specify the
# change once no unit list is given, and no longer than the texts a person has
# read on this tree.
CONSTRUCTED_TEXT_MIN, CONSTRUCTED_TEXT_MAX = 1200, 3200
# The evaluation's own directory, relative to the repository it evaluates. A
# base tree holding it would show an arm the classes, the grading rules, and
# the construction its own task came from, so a base commit whose tree holds
# it is refused.
EVALUATION_ROOT = Path(__file__).resolve().parents[1].relative_to(protocol.repository_of(Path(__file__)))


@dataclass(frozen=True)
class ConstructedUnit:
    """One unit of a constructed fan-out: the directory a delegation grants it, the package whose suite judges it, and where its change and its verdict live."""

    # The unit's name is the directory a worker is granted, so two units of
    # one construction can be given write roots that do not overlap.
    name: str
    package: str
    files: tuple[str, ...]
    test_file: str


@dataclass(frozen=True)
class Construction:
    """One fan-out authored by construction: the units, the shared element, and every edit that makes the fixture, the oracle, and the corruptions.

    `tasks/coherent.py` constructs the controls of this family the same way
    and states the same three states. What separates the two is the division:
    a control's whole change lies in one directory and a construction here
    spreads over one directory per unit, which is what a write grant can name.
    """

    name: str
    subject: str
    units: tuple[ConstructedUnit, ...]
    # The crate that holds the shared element every unit builds against, which
    # the interface node of the teams graph writes before any worker starts.
    interface_package: str
    interface_paths: tuple[str, ...]
    # The test that judges the units against each other, and the file whose
    # hidden copy carries it. It runs in the whole-change step rather than in
    # any unit's, so a unit that disagrees with the shared element fails the
    # task without failing the worker that wrote it.
    integration_test_file: str
    integration_test: str
    # The fixture is the base tree with these edits and the raised ceilings;
    # the oracle overlay is the fixture with these further edits.
    fixture_edits: tuple[Edit, ...]
    oracle_edits: tuple[Edit, ...]
    # Edits to a fixture test file that the change makes necessary: a change
    # that alters a signature or a message an existing test asserts on leaves
    # that test stale. They belong to the change, so the oracle overlay
    # carries them and the visible check passes over a solved workspace, and
    # the hidden copy of the file carries them before the append.
    test_edits: tuple[Edit, ...]
    # The hidden test files, as the fixture's file, so edited, plus this
    # appended text.
    test_appends: tuple[tuple[str, str], ...]
    ceilings: tuple[Edit, ...]
    # Sentences of a document the fixture writes that the grade requires to
    # stand in the workspace as graded, so the specification cannot be deleted.
    sentences: dict[str, tuple[str, ...]]
    text: str
    revert_unit: str
    # The corruption that makes one unit state a name the shared table gives
    # another crate: every unit's tests still pass and the whole-change test
    # fails, which is what shows the shared element is checked.
    rename: Edit
    rename_unit: str
    # A substring of the test names the visible check leaves out, and why. A
    # test a kernel sandbox breaks measures the sandbox rather than the
    # change, and both harnesses under comparison run their commands inside
    # one, so the visible check has to pass in each of them.
    check_skip: str = ""
    check_skip_reason: str = ""

    @property
    def packages(self) -> list[str]:
        """Every package the change touches, which is what the visible check and the whole-change lint run over."""
        return sorted({self.interface_package, *(unit.package for unit in self.units)})

    def unit_of(self, path: str) -> ConstructedUnit | None:
        """The unit a workspace-relative path belongs to, or None when the path lies outside every unit."""
        return next((unit for unit in self.units if path == unit.name or path.startswith(f"{unit.name}/")), None)


def check_construction(construction: Construction) -> None:
    """Refuse a construction a delegation cannot hand out, a grade cannot judge, or whose division does not exist.

    A write grant is a directory, so two units whose directories overlap
    cannot both be granted and the division the delegating node's
    instruction requires does not exist. A delegation runs at most UNIT_MAX
    workers, and a fan-out needs two. Every unit carries a change inside the
    directory it is granted and a hidden test of its own, or the measure is
    silent about part of the change, and a change under UNIT_LINES_MIN lines
    is not worth a worker. The shared element and the test that judges the
    units against each other lie outside every unit, so no worker is given
    either and no worker is failed by the other's disagreement. Both
    corruptions name units the construction holds. The whole-change check
    runs packages whose suites pass inside a kernel sandbox, leaving out by
    name the one test such a package breaks on.
    """
    names = [unit.name for unit in construction.units]
    if len(names) < 2:
        raise ValueError(f"{construction.name} holds {len(names)} unit(s); a fan-out is one change over two or more units")
    if len(names) > UNIT_MAX:
        raise ValueError(f"{construction.name} holds {len(names)} units, and a delegation runs at most {UNIT_MAX} workers")
    if len(set(names)) != len(names):
        raise ValueError(f"{construction.name} names a unit twice: {', '.join(names)}")
    for unit in construction.units:
        for other in construction.units:
            if unit is not other and (unit.name == other.name or unit.name.startswith(f"{other.name}/")):
                raise ValueError(f"{construction.name}: the units {unit.name} and {other.name} overlap, so no two workers can be granted both")
    changed = sorted({edit.path for edit in construction.oracle_edits})
    for path in changed:
        unit = construction.unit_of(path)
        if unit is None and path not in construction.interface_paths:
            raise ValueError(f"{construction.name}: the change touches {path}, which lies in no unit and is not named as interface")
        if unit is not None and path not in unit.files:
            raise ValueError(f"{construction.name}: the change touches {path}, which the unit {unit.name} does not list")
    for path in construction.interface_paths:
        if construction.unit_of(path) is not None:
            raise ValueError(f"{construction.name}: the interface file {path} lies inside a unit, so a worker would be given the shared element")
    appended = dict(construction.test_appends)
    for edit in construction.test_edits:
        if edit.path not in appended:
            raise ValueError(f"{construction.name}: the test edit of {edit.path} reaches no hidden test file, so the grade would not see it")
        if construction.unit_of(edit.path) is None:
            raise ValueError(f"{construction.name}: the test edit of {edit.path} lies in no unit, so no worker could make it")
    for unit in construction.units:
        if unit.test_file not in appended:
            raise ValueError(f"{construction.name}: the unit {unit.name} has no hidden test, so its completion is not measured")
        if not removal.test_names(appended[unit.test_file]):
            raise ValueError(f"{construction.name}: the hidden test of the unit {unit.name} declares no test function")
        if not any(edit.path in unit.files for edit in construction.oracle_edits):
            raise ValueError(f"{construction.name}: the unit {unit.name} has no oracle edit, so there is nothing for a worker to do")
    if construction.integration_test_file not in appended:
        raise ValueError(f"{construction.name}: {construction.integration_test_file} carries the whole-change test and is no hidden test file")
    if construction.integration_test not in removal.test_names(appended[construction.integration_test_file]):
        raise ValueError(f"{construction.name}: {construction.integration_test_file} declares no test named {construction.integration_test}")
    if construction.unit_of(construction.integration_test_file) is not None:
        raise ValueError(f"{construction.name}: the whole-change test stands in {construction.integration_test_file}, inside a unit, so one worker would be judged by it")
    if construction.revert_unit not in names:
        raise ValueError(f"{construction.name}: the revert corruption names {construction.revert_unit}, which is not a unit")
    if construction.rename_unit not in names:
        raise ValueError(f"{construction.name}: the rename corruption names {construction.rename_unit}, which is not a unit")
    if construction.rename.path not in next(unit for unit in construction.units if unit.name == construction.rename_unit).files:
        raise ValueError(f"{construction.name}: the rename corruption edits {construction.rename.path}, which the unit {construction.rename_unit} does not hold")
    outside = sorted(package for package in construction.packages if package not in SANDBOX_SAFE_PACKAGES)
    if outside:
        raise ValueError(
            f"{construction.name}: the whole-change check runs {', '.join(outside)}, whose suites do not pass inside a kernel sandbox; "
            "both harnesses run their commands inside one, so such a check can pass for no arm"
        )
    if bool(construction.check_skip) != bool(construction.check_skip_reason):
        raise ValueError(f"{construction.name}: a check that leaves a test out states why, and a reason without a name states nothing")
    for package, skipped in SANDBOX_SKIPPED_TESTS.items():
        if package in construction.packages and construction.check_skip != skipped:
            raise ValueError(
                f"{construction.name}: the whole-change check runs {package}, whose test {skipped} a sandbox breaks, "
                f"and the check leaves out {construction.check_skip or 'nothing'}"
            )
    small = sorted(f"{unit.name} ({unit_lines(construction, unit)} lines)" for unit in construction.units if unit_lines(construction, unit) < UNIT_LINES_MIN)
    if small:
        raise ValueError(
            f"{construction.name}: {', '.join(small)} write fewer than {UNIT_LINES_MIN} lines, so delegating them costs more than doing them"
        )


def unit_lines(construction: Construction, unit: ConstructedUnit) -> int:
    """The implementation lines the change writes inside one unit, which is what states whether delegating that unit pays."""
    return sum(edit.written_lines for edit in construction.oracle_edits if edit.path in unit.files)


def normalized_sentence(sentence: str) -> str:
    """One specification sentence as the grade compares it: without backticks, asterisks, and link targets, with whitespace collapsed, lowercased."""
    without = re.sub(r"[`*]", "", sentence)
    without = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", without)
    return " ".join(without.split()).lower()


def construction_text(construction: Construction) -> str:
    """The task text: the specification paragraphs, then the closing sentence."""
    return protocol.autonomy_text(construction.text)


def build_construction_fixture(repo: Path, commit: str, destination: Path, construction: Construction) -> None:
    """The workspace an arm starts from: the commit's tree, the raised ceilings, the fixture edits, and the check suite."""
    protocol.archive(repo, commit, destination)
    if (destination / EVALUATION_ROOT).exists():
        raise ValueError(
            f"the tree of {commit} holds {EVALUATION_ROOT}, so the workspace would show an arm the evaluation's own "
            "instruments, tasks, and grading rules; pass --commit with a commit whose tree lacks that path"
        )
    for edit in (*construction.ceilings, *construction.fixture_edits):
        edit.apply(destination)
    _write(
        destination / removal.CHECKS_SCRIPT,
        workspace_checks_script(tuple(construction.packages), construction.check_skip, construction.check_skip_reason),
        executable=True,
    )


def construction_metadata(construction: Construction, source: dict[str, Any]) -> dict[str, Any]:
    names = [unit.name for unit in construction.units]
    return {
        protocol.SOURCE_KEY: source,
        "units": {unit.name: [unit.name] for unit in construction.units},
        "n": len(names),
        "unit_files": {unit.name: list(unit.files) for unit in construction.units},
        "unit_tests": {unit.name: [unit.test_file] for unit in construction.units},
        "unit_packages": {unit.name: unit.package for unit in construction.units},
        "unit_lines": {unit.name: unit_lines(construction, unit) for unit in construction.units},
        "checked_units": names,
        "generated_unit_checks": {},
        "interface_paths": list(construction.interface_paths),
        "interface_package": construction.interface_package,
        "integration_test": construction.integration_test,
        "packages": construction.packages,
        "revert_unit": construction.revert_unit,
        "rename_unit": construction.rename_unit,
        "check_skip": construction.check_skip,
        "check_skip_reason": construction.check_skip_reason,
        # What makes this a fan-out rather than a control: a delegation can
        # grant each unit one of these roots and no two of them overlap, so
        # the division the survey's own instruction requires exists.
        "division": {"write_roots": names, "separable": True},
        "authored": "by construction: the units, the shared element, and the tests are chosen rather than read from a commit",
        "review": "pending: the text was drafted with the construction and has not been read by a person",
    }


def author_construction(repo: Path, out: Path, construction: Construction, commit: str, keep_workspace: bool = False) -> Task:
    """Write one constructed fan-out's task directory; see the module docstring for what it holds."""
    if out.exists():
        raise FileExistsError(f"{out} exists; author writes a fresh task directory")
    check_construction(construction)
    resolved = removal.git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()
    text = construction_text(construction)
    if not CONSTRUCTED_TEXT_MIN <= len(text) <= CONSTRUCTED_TEXT_MAX:
        raise ValueError(f"task {construction.name}: the text is {len(text)} characters; the band is {CONSTRUCTED_TEXT_MIN} to {CONSTRUCTED_TEXT_MAX}")
    out.mkdir(parents=True)
    try:
        workspace, grader = out / WORKSPACE, out / GRADER
        build_construction_fixture(repo, resolved, workspace, construction)
        for path, sentences in construction.sentences.items():
            document = normalized_sentence((workspace / path).read_text(encoding="utf-8"))
            for sentence in sentences:
                if normalized_sentence(sentence) not in document:
                    raise ValueError(f"{path} of the fixture lacks the specification sentence: {sentence}")

        # The oracle overlay: the fixture with the change applied, holding
        # only the files the change touches.
        solved = out / "solved"
        shutil.copytree(workspace, solved, symlinks=True)
        for edit in (*construction.oracle_edits, *construction.test_edits):
            edit.apply(solved)
        for relative in sorted({edit.path for edit in (*construction.oracle_edits, *construction.test_edits)}):
            _write(grader / protocol.ORACLE / WORKSPACE / relative, (solved / relative).read_text(encoding="utf-8"))

        # The hidden tests: the fixture's test file with the task's test
        # appended, so an arm's own test file never decides a verdict.
        hidden_names: dict[str, list[str]] = {}
        for relative, appended in construction.test_appends:
            source = (workspace / relative).read_text(encoding="utf-8")
            for edit in (edit for edit in construction.test_edits if edit.path == relative):
                found = source.count(edit.old)
                if found != 1:
                    raise ValueError(f"{relative}: the test edit's source stands {found} times; an edit's source stands exactly once")
                source = source.replace(edit.old, edit.new)
            if appended.strip() in source:
                raise ValueError(f"{relative}: the hidden test already stands in the fixture")
            _write(grader / removal.HIDDEN_TESTS / relative, source.rstrip("\n") + "\n" + appended)
            hidden_names[relative] = removal.test_names(appended)

        specification = {
            "task": construction.name,
            "units": [
                {
                    "name": unit.name,
                    "package": unit.package,
                    "python_tests": [],
                    "hidden_test_names": {unit.test_file: hidden_names[unit.test_file]},
                }
                for unit in construction.units
            ],
            "integration": {
                "test_packages": [construction.interface_package],
                "lint_packages": construction.packages,
                "test": construction.integration_test,
            },
            "sentences": {path: [normalized_sentence(sentence) for sentence in sentences] for path, sentences in construction.sentences.items()},
        }
        _write(grader / removal.SPECIFICATION_FILE, json.dumps(specification, indent=2) + "\n")
        _write(grader / "grade.py", FAN_OUT_GRADE_SCRIPT, executable=True)
        _write(grader / protocol.GRADE_SCRIPT, removal.GRADE_WRAPPER, executable=True)

        # The two corruptions, recorded against the solved workspace.
        reverted = next(unit for unit in construction.units if unit.name == construction.revert_unit)
        edits = [{"path": edit.path, "old": edit.new, "new": edit.old} for edit in construction.oracle_edits if edit.path in reverted.files]
        _write(grader / protocol.CORRUPTIONS / REVERT_CORRUPTION / "edits.json", json.dumps(edits, indent=2) + "\n")
        _write(grader / protocol.CORRUPTIONS / REVERT_CORRUPTION / "apply.py", CONSTRUCTED_CORRUPTION_SCRIPT, executable=True)
        rename = [{"path": construction.rename.path, "old": construction.rename.old, "new": construction.rename.new}]
        _write(grader / protocol.CORRUPTIONS / RENAME_CORRUPTION / "edits.json", json.dumps(rename, indent=2) + "\n")
        _write(grader / protocol.CORRUPTIONS / RENAME_CORRUPTION / "apply.py", CONSTRUCTED_CORRUPTION_SCRIPT, executable=True)
        shutil.rmtree(solved)

        source = protocol.source_record(repo, out, commit=resolved, subject=construction.subject)
        task = Task(
            name=construction.name,
            family=FAMILY,
            class_name=FAN_OUT,
            text=text,
            correct_statuses=frozenset({COMPLETED}),
            correct_codes=frozenset(),
            budget=budget_for(len(construction.units)),
            protected=PROTECTED,
            metadata=construction_metadata(construction, source),
        )
        _finish(out, task, repo, keep_workspace)
        return task
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise


# ---- the corruption script the constructed tasks share --------------------------

CONSTRUCTED_CORRUPTION_SCRIPT = r'''#!/usr/bin/python3
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


# ---- the ceilings the constructed fixtures raise --------------------------------

CONTRACT_CEILING = ceiling_edits(
    "contract  ",
    "execution contracts",
    (("AGENTS.md", "`contract` stays under CEILING"), ("docs/design.md", "apart under CEILING lines.")),
    1575,
    1650,
)
WORKFLOW_CEILING = ceiling_edits(
    "workflow  ",
    "workflows",
    (("AGENTS.md", "`workflow` stays under CEILING,"), ("docs/design.md", "stays under CEILING lines.")),
    1050,
    1100,
)


# ---- the input-bound change over three crates -----------------------------------

_TOOLS_HEADING = """## Tools

A tool has a specification and an implementation."""

_INPUT_BOUNDS_SPECIFICATION = (
    """### Input bounds

Every input a crate reads from outside the process is bounded, and a crate
that reads one refuses an input above the bound and names the bound in the
refusal. A caller then learns from the message alone which limit it met and
what that limit holds. The bounds stand in one table, `INPUT_BOUNDS` in
`foe_contract`, which holds each bound beside the largest input it admits and
what the count measures. A bound is named for the crate that enforces it, a
dot, and what the bound covers, so a refusal locates the rule that produced
it. A crate enforces no bound the table lacks and states no bound name the
table gives another crate.

"""
    + _TOOLS_HEADING
)

_INPUT_BOUNDS_SENTENCES: dict[str, tuple[str, ...]] = {
    "docs/design.md": (
        "Every input a crate reads from outside the process is bounded, and a crate that reads one refuses an "
        "input above the bound and names the bound in the refusal.",
        "A crate enforces no bound the table lacks and states no bound name the table gives another crate.",
    )
}

_ERRORS_ANCHOR = "// ---- errors --------------------------------------------------------------------\n"

_INPUT_BOUNDS_TABLE = '''// ---- input bounds ---------------------------------------------------------------

/// One bound on an input a crate of this repository reads from outside the
/// process: the name a refusal gives it, the largest input it admits, and
/// what the count measures. A crate that reads such an input refuses one
/// above the bound and names the bound in the refusal, so a caller learns
/// from the message alone which limit it met and what that limit holds. A
/// name is the crate that enforces the bound, a dot, and what the bound
/// covers, and a crate enforces no bound this table lacks.
pub struct InputBound {
    pub name: &'static str,
    pub maximum: usize,
    pub measures: &'static str,
}

/// Every bounded input, in byte order of `name`.
pub const INPUT_BOUNDS: &[InputBound] = &[InputBound {
    name: "contract.document",
    maximum: 1_048_576,
    measures: "the bytes of one execution contract document",
}];

/// The bound of that name, or `None` when the table holds none.
pub fn input_bound(name: &str) -> Option<&'static InputBound> {
    INPUT_BOUNDS.iter().find(|bound| bound.name == name)
}

/// `Ok` when `measured` lies within the bound of that name. The error is the
/// rule the input broke, which a caller states beside the key it read. A name
/// the table lacks is itself a rule failure, so no refusal names a bound the
/// table does not declare.
pub fn within_input_bound(name: &str, measured: usize) -> Result<(), String> {
    match input_bound(name) {
        None => Err(format!("names the input bound `{name}`, which foe_contract::INPUT_BOUNDS lacks")),
        Some(bound) if measured > bound.maximum => {
            Err(format!("is at most {} bytes by the input bound `{name}`, and measured {measured}", bound.maximum))
        }
        Some(_) => Ok(()),
    }
}

''' + _ERRORS_ANCHOR

_DOCUMENT_SEED_OLD = '''/// Reads, parses, validates, and resolves a document from `path`.
pub fn load(path: &Path) -> Result<ResolvedContract, ContractError> {
    let config = parse(&std::fs::read_to_string(path)?)?;
    resolve(&config)
}
'''
_DOCUMENT_SEED_NEW = '''/// The bound this crate enforces on a document it reads.
pub const DOCUMENT: &str = "contract.document";

/// Reads, parses, validates, and resolves a document from `path`.
pub fn load(path: &Path) -> Result<ResolvedContract, ContractError> {
    let text = std::fs::read_to_string(path)?;
    crate::within_input_bound(DOCUMENT, text.len()).map_err(|rule| invalid("contract.document", rule))?;
    let config = parse(&text)?;
    resolve(&config)
}
'''

_INPUT_BOUNDS_ROWS_OLD = '''pub const INPUT_BOUNDS: &[InputBound] = &[InputBound {
    name: "contract.document",
    maximum: 1_048_576,
    measures: "the bytes of one execution contract document",
}];'''

_INPUT_BOUNDS_ROWS_NEW = '''pub const INPUT_BOUNDS: &[InputBound] = &[
    InputBound {
        name: "context.continuation_task",
        maximum: 16_384,
        measures: "the bytes of the task text a compaction carries across the cut",
    },
    InputBound {
        name: "context.summary",
        maximum: 65_536,
        measures: "the bytes of the summary a compaction carries across the cut",
    },
    InputBound {
        name: "contract.document",
        maximum: 1_048_576,
        measures: "the bytes of one execution contract document",
    },
    InputBound {
        name: "evidence.bundle_file",
        maximum: 4_194_304,
        measures: "the bytes of one file of an evidence bundle",
    },
    InputBound {
        name: "evidence.bundle_total",
        maximum: 67_108_864,
        measures: "the bytes of every file an evidence manifest lists",
    },
    InputBound {
        name: "workflow.bound_value",
        maximum: 262_144,
        measures: "the bytes of one value bound into a tool node's arguments",
    },
    InputBound {
        name: "workflow.node_arguments",
        maximum: 524_288,
        measures: "the bytes of one tool node's resolved arguments",
    },
];'''

_EVIDENCE_EDITS: tuple[Edit, ...] = (
    Edit(
        "crates/evidence/src/lib.rs",
        """pub fn digest_of(bytes: &[u8]) -> String {""",
        """/// The bounds this crate enforces on a bundle it reads: one retained file,
/// and every file the manifest lists together. The manifest states the
/// length of each file, so a bundle over either bound is refused before any
/// file of it is read.
pub const BUNDLE_FILE: &str = "evidence.bundle_file";
pub const BUNDLE_TOTAL: &str = "evidence.bundle_total";

/// `Ok` when `measured` lies within the named bound, and otherwise the
/// refusal stated against `key`. A length past what this machine can address
/// is past every bound, so it is measured as the largest addressable count.
fn within_bundle_bound(bound: &str, key: impl Into<String>, measured: u64) -> Result<(), EvidenceError> {
    let measured = usize::try_from(measured).unwrap_or(usize::MAX);
    foe_contract::within_input_bound(bound, measured).map_err(|rule| invalid(key, rule))
}

pub fn digest_of(bytes: &[u8]) -> String {""",
    ),
    Edit(
        "crates/evidence/src/lib.rs",
        """    for (index, file) in manifest.files.iter().enumerate() {
        require_manifest_path(&format!("{key}.files[{index}].path"), &file.path)?;
        require_digest(&format!("{key}.files[{index}].sha256"), &file.sha256)?;
        if index > 0 && manifest.files[index - 1].path >= file.path {
            return Err(invalid(format!("{key}.files[{index}].path"), "follows the prior path in byte order"));
        }
    }
""",
        """    let mut together: u64 = 0;
    for (index, file) in manifest.files.iter().enumerate() {
        require_manifest_path(&format!("{key}.files[{index}].path"), &file.path)?;
        require_digest(&format!("{key}.files[{index}].sha256"), &file.sha256)?;
        within_bundle_bound(BUNDLE_FILE, format!("{key}.files[{index}].bytes"), file.bytes)?;
        together = together.saturating_add(file.bytes);
        if index > 0 && manifest.files[index - 1].path >= file.path {
            return Err(invalid(format!("{key}.files[{index}].path"), "follows the prior path in byte order"));
        }
    }
    within_bundle_bound(BUNDLE_TOTAL, format!("{key}.files"), together)?;
""",
    ),
    Edit(
        "crates/evidence/src/lib.rs",
        """        .map_err(|error| invalid("evidence.manifest", format!("is readable: {}: {error}", dir.display())))?;
    let manifest = check_manifest(&bytes)?;""",
        """        .map_err(|error| invalid("evidence.manifest", format!("is readable: {}: {error}", dir.display())))?;
    within_bundle_bound(BUNDLE_FILE, "evidence.manifest", bytes.len() as u64)?;
    let manifest = check_manifest(&bytes)?;""",
    ),
    Edit(
        "crates/evidence/src/lib.rs",
        """            .map_err(|error| invalid(format!("evidence file {}", file.path), format!("is readable: {error}")))?;
        if content.len() as u64 != file.bytes""",
        """            .map_err(|error| invalid(format!("evidence file {}", file.path), format!("is readable: {error}")))?;
        within_bundle_bound(BUNDLE_FILE, format!("evidence file {}", file.path), content.len() as u64)?;
        if content.len() as u64 != file.bytes""",
    ),
    Edit(
        "crates/evidence/src/lib.rs",
        """                files.push(ManifestFile {
                    path: parts.join("/"),""",
        """                within_bundle_bound(BUNDLE_FILE, format!("evidence file {}", parts.join("/")), content.len() as u64)?;
                files.push(ManifestFile {
                    path: parts.join("/"),""",
    ),
)

_CONTEXT_EDITS: tuple[Edit, ...] = (
    Edit(
        "crates/context/src/lib.rs",
        "use foe_contract::{ContextConfig, DoneWhen};",
        "use foe_contract::{within_input_bound, ContextConfig, DoneWhen};",
    ),
    Edit(
        "crates/context/src/lib.rs",
        """    fn continuation(&self, state: &ContextState, cut: &Cut, prior: Option<&CompactionSummary>) -> ContinuationState {
        let task = state.events.iter().find_map(|e| match &e.data {
            EventData::InboxItem(item) if item.source == InboxSource::Task => Some(blocks_text(&item.content)),
            _ => None,
        });
        let carried = prior.map(|p| p.state.clone()).unwrap_or_default();
        ContinuationState {
            task: task.unwrap_or_default(),""",
        """    /// The state the next request carries in place of the summarized span,
    /// or the rule the task text broke. The task text enters every later
    /// request, so text above its bound would grow the projection that
    /// compaction exists to shrink, and the compaction fails instead.
    fn continuation(
        &self,
        state: &ContextState,
        cut: &Cut,
        prior: Option<&CompactionSummary>,
    ) -> Result<ContinuationState, String> {
        let task = state.events.iter().find_map(|e| match &e.data {
            EventData::InboxItem(item) if item.source == InboxSource::Task => Some(blocks_text(&item.content)),
            _ => None,
        });
        let task = task.unwrap_or_default();
        within_input_bound(CONTINUATION_TASK, task.len()).map_err(|rule| format!("the task text {rule}"))?;
        let carried = prior.map(|p| p.state.clone()).unwrap_or_default();
        Ok(ContinuationState {
            task,""",
    ),
    Edit(
        "crates/context/src/lib.rs",
        """            covered: cut.covered,
            budget_remaining: state.remaining,
        }
    }
}""",
        """            covered: cut.covered,
            budget_remaining: state.remaining,
        })
    }
}""",
    ),
    Edit(
        "crates/context/src/lib.rs",
        """        let user = prompt(prior.map(|p| p.summary.as_str()), &span);
        let continuation = self.continuation(state, cut, prior);
        let failed = |error: &str, usage| Summarized::Failed { error: error.to_string(), usage };
""",
        """        let user = prompt(prior.map(|p| p.summary.as_str()), &span);
        let failed = |error: &str, usage| Summarized::Failed { error: error.to_string(), usage };
        let continuation = match self.continuation(state, cut, prior) {
            Ok(continuation) => continuation,
            Err(rule) => return Ok(failed(&rule, Usage::default())),
        };
""",
    ),
    Edit(
        "crates/context/src/lib.rs",
        """            Answer::Message { message, request_seq } => {
                let summary = CompactionSummary {
                    step: message.step,
                    summary: message.text,
                    state: continuation,
                    first_kept_seq: cut.first_kept_seq,
                    summary_request_seq: request_seq,
                };
                let kept: u64 = steps(state.events, cut.first_kept_seq).iter().map(|(_, t)| t).sum();
                let opening = summary.state.task.len() + render_continuation(&summary).len();
                let active_estimate = kept + tokens(opening);
                Summarized::Summary { summary: Box::new(summary), usage: message.usage, active_estimate }
            }
        })""",
        """            Answer::Message { message, request_seq } => match within_input_bound(SUMMARY, message.text.len()) {
                Err(rule) => failed(&format!("the summary {rule}"), message.usage),
                Ok(()) => {
                    let summary = CompactionSummary {
                        step: message.step,
                        summary: message.text,
                        state: continuation,
                        first_kept_seq: cut.first_kept_seq,
                        summary_request_seq: request_seq,
                    };
                    let kept: u64 = steps(state.events, cut.first_kept_seq).iter().map(|(_, t)| t).sum();
                    let opening = summary.state.task.len() + render_continuation(&summary).len();
                    let active_estimate = kept + tokens(opening);
                    Summarized::Summary { summary: Box::new(summary), usage: message.usage, active_estimate }
                }
            },
        })""",
    ),
    Edit(
        "crates/context/src/lib.rs",
        """/// The token estimate for `bytes` of text: one token per four bytes.""",
        """/// The bounds this crate enforces on the text a compaction carries across
/// the cut: the summary the model returned, and the task text read from the
/// log. Either enters every later request, so a compaction that would carry
/// text above a bound fails and names the bound.
pub const SUMMARY: &str = "context.summary";
pub const CONTINUATION_TASK: &str = "context.continuation_task";

/// The token estimate for `bytes` of text: one token per four bytes.""",
    ),
)

_WORKFLOW_EDITS: tuple[Edit, ...] = (
    Edit(
        "crates/workflow/src/bind.rs",
        """use serde_json::{Map, Value};""",
        """use serde_json::{Map, Value};

/// The bounds this crate enforces on the values entering a tool node: one
/// bound value, and the node's resolved arguments together. A value whose
/// serialization fails is past every bound rather than unbounded, so it
/// measures the largest addressable count and is refused.
pub const BOUND_VALUE: &str = "workflow.bound_value";
pub const NODE_ARGUMENTS: &str = "workflow.node_arguments";

/// The bytes one value occupies once serialized.
fn measured(value: &Value) -> usize {
    serde_json::to_string(value).map_or(usize::MAX, |text| text.len())
}""",
    ),
    Edit(
        "crates/workflow/src/bind.rs",
        """        let path = object.get("pointer").and_then(Value::as_str).unwrap_or("");
        pointer(&whole, path)
            .cloned()
            .ok_or_else(|| format!("binds `{name}` at pointer `{path}`, which is absent from its value"))
    }
    walk(&Value::Object(args.clone()), input)
}""",
        """        let path = object.get("pointer").and_then(Value::as_str).unwrap_or("");
        let bound = pointer(&whole, path)
            .cloned()
            .ok_or_else(|| format!("binds `{name}` at pointer `{path}`, which is absent from its value"))?;
        foe_contract::within_input_bound(BOUND_VALUE, measured(&bound))
            .map_err(|rule| format!("binds `{name}` at pointer `{path}`, whose value {rule}"))?;
        Ok(bound)
    }
    let resolved = walk(&Value::Object(args.clone()), input)?;
    foe_contract::within_input_bound(NODE_ARGUMENTS, measured(&resolved))
        .map_err(|rule| format!("resolves to arguments whose serialization {rule}"))?;
    Ok(resolved)
}""",
    ),
)

_EVIDENCE_TEST = '''
/// docs/design.md "Input bounds": a manifest listing a file above the
/// per-file bound, or files that together exceed the total bound, is refused
/// with the bound named, and a manifest at either bound is accepted.
#[test]
fn a_manifest_over_an_input_bound_is_refused_before_a_file_is_read() {
    use super::{check_manifest, Manifest, ManifestFile, BUNDLE_FILE, BUNDLE_TOTAL};
    fn listed(sizes: &[u64]) -> Vec<u8> {
        let files = sizes
            .iter()
            .enumerate()
            .map(|(index, bytes)| ManifestFile {
                path: format!("file{index:04}.bin"),
                bytes: *bytes,
                sha256: digest('d'),
            })
            .collect();
        let manifest = Manifest {
            schema_version: 1,
            files,
            proposal_log: "file0000.bin".into(),
            adoption_record: "file0000.bin".into(),
        };
        manifest_bytes(&manifest).unwrap()
    }
    let bound = |name: &str| {
        foe_contract::input_bound(name).unwrap_or_else(|| panic!("{name} stands in INPUT_BOUNDS")).maximum as u64
    };
    let (one, together) = (bound(BUNDLE_FILE), bound(BUNDLE_TOTAL));
    check_manifest(&listed(&[one])).expect("a file at the per-file bound is accepted");
    let refused = check_manifest(&listed(&[one + 1])).unwrap_err().to_string();
    assert!(refused.contains(BUNDLE_FILE), "a file over the per-file bound is refused without naming it: {refused}");
    let count = (together / one + 1) as usize;
    let refused = check_manifest(&listed(&vec![one; count])).unwrap_err().to_string();
    assert!(refused.contains(BUNDLE_TOTAL), "files over the total bound are refused without naming it: {refused}");
}

/// docs/design.md "Input bounds": building a manifest refuses what verifying
/// one would refuse, so a bundle this crate builds is a bundle it can read.
#[test]
fn building_a_manifest_refuses_a_file_over_the_per_file_bound() {
    let root = tempfile::tempdir().unwrap();
    let maximum = foe_contract::input_bound(super::BUNDLE_FILE).expect("the bound stands in INPUT_BOUNDS").maximum;
    std::fs::write(root.path().join("large.bin"), vec![b'x'; maximum + 1]).unwrap();
    let refused = build_manifest(root.path(), "large.bin", "large.bin").unwrap_err().to_string();
    assert!(refused.contains(super::BUNDLE_FILE), "an oversized file is accepted into a manifest: {refused}");
}
'''

_CONTEXT_TEST = '''
/// docs/design.md "Input bounds": text a compaction would carry across the
/// cut is bounded, and a compaction that would carry more fails naming the
/// bound it broke rather than growing the projection it exists to shrink.
#[tokio::test]
async fn text_above_an_input_bound_fails_the_compaction_naming_the_bound() {
    let policy = Policy::new(config(Some(4_000), 500, 34), 4_000, 100, None);
    let cut = Cut {
        first_kept_seq: 9,
        covered: Covered { first_seq: 1, last_seq: 8 },
        projected_tokens: 3252,
        exceeds_window: false,
    };
    let bound =
        |name: &str| foe_contract::input_bound(name).unwrap_or_else(|| panic!("{name} stands in INPUT_BOUNDS")).maximum;
    let events = episode();
    let mut at = Scripted { answer: Some(response(&"x".repeat(bound(super::SUMMARY)))), asked: None };
    assert!(
        matches!(policy.summarize(&state(&events), &cut, &mut at).await.unwrap(), Summarized::Summary { .. }),
        "a summary at the bound is refused"
    );
    let mut over = Scripted { answer: Some(response(&"x".repeat(bound(super::SUMMARY) + 1))), asked: None };
    let Summarized::Failed { error, .. } = policy.summarize(&state(&events), &cut, &mut over).await.unwrap() else {
        panic!("a summary over its bound has to fail the compaction")
    };
    assert!(error.contains(super::SUMMARY), "the failure does not name the bound: {error}");

    let mut long_task = episode();
    for event in &mut long_task {
        if let EventData::InboxItem(item) = &mut event.data {
            if item.source == InboxSource::Task {
                item.content = text(&"y".repeat(bound(super::CONTINUATION_TASK) + 1));
            }
        }
    }
    let mut call = Scripted { answer: Some(response("## Goal\\nfix")), asked: None };
    let Summarized::Failed { error, .. } = policy.summarize(&state(&long_task), &cut, &mut call).await.unwrap() else {
        panic!("a task text over its bound has to fail the compaction")
    };
    assert!(error.contains(super::CONTINUATION_TASK), "the failure does not name the bound: {error}");
}
'''

_WORKFLOW_TEST = '''
/// docs/design.md "Input bounds": a value bound into a tool node's arguments
/// is refused above its bound, and arguments that together exceed their own
/// bound are refused as well; each refusal names the bound it broke.
#[test]
fn a_value_above_its_input_bound_is_refused_naming_the_bound() {
    let bound =
        |name: &str| foe_contract::input_bound(name).unwrap_or_else(|| panic!("{name} stands in INPUT_BOUNDS")).maximum;
    // A JSON string of n - 2 characters serializes to n bytes with its quotes.
    let sized = |bytes: usize| Value::String("x".repeat(bytes - 2));
    let at = sized(bound(super::BOUND_VALUE));
    let over = sized(bound(super::BOUND_VALUE) + 1);
    let inputs = |name: &str| match name {
        "at" => Some(at.clone()),
        "over" => Some(over.clone()),
        _ => None,
    };
    let one = json!({ "p": { "$node": "at" } });
    resolve(one.as_object().unwrap(), &inputs).expect("a value at its bound resolves");
    let one = json!({ "p": { "$node": "over" } });
    let refused = resolve(one.as_object().unwrap(), &inputs).unwrap_err();
    assert!(refused.contains(super::BOUND_VALUE), "a value over its bound is refused without naming it: {refused}");

    let count = bound(super::NODE_ARGUMENTS) / bound(super::BOUND_VALUE) + 1;
    let args: serde_json::Map<String, Value> =
        (0..count).map(|index| (format!("k{index}"), json!({ "$node": "at" }))).collect();
    let refused = resolve(&args, &inputs).unwrap_err();
    assert!(
        refused.contains(super::NODE_ARGUMENTS),
        "arguments over their bound are refused without naming it: {refused}"
    );
}
'''

_INPUT_BOUNDS_INTEGRATION = '''
// ---- input bounds ---------------------------------------------------------------

/// The repository root, from this crate's manifest directory.
fn repository_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// The name of every crate directory under `crates/`.
fn crate_names() -> Vec<String> {
    let directory = repository_root().join("crates");
    let entries = std::fs::read_dir(&directory).unwrap_or_else(|e| panic!("{}: {e}", directory.display()));
    let mut names: Vec<String> = entries
        .map(|entry| entry.unwrap())
        .filter(|entry| entry.path().is_dir())
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

/// Every Rust source of one crate that is neither a test file nor the file
/// the table itself stands in. The table names every bound, so that file is
/// the one place every name is expected.
fn enforcing_sources(name: &str) -> Vec<(String, String)> {
    let mut found = Vec::new();
    let mut pending = vec![repository_root().join("crates").join(name).join("src")];
    while let Some(directory) = pending.pop() {
        let Ok(entries) = std::fs::read_dir(&directory) else { continue };
        for entry in entries.map(|entry| entry.unwrap().path()) {
            if entry.is_dir() {
                pending.push(entry);
                continue;
            }
            let relative = entry.strip_prefix(repository_root()).unwrap().to_string_lossy().into_owned();
            let is_test = relative.ends_with("_test.rs") || relative.contains("/tests/");
            if !relative.ends_with(".rs") || is_test || relative == "crates/contract/src/lib.rs" {
                continue;
            }
            found.push((relative, std::fs::read_to_string(&entry).unwrap()));
        }
    }
    found.sort();
    found
}

/// One vocabulary of input bounds. The table names each bound once and in
/// order, a name is the crate that enforces the bound and what the bound
/// covers, and the only bound name a crate states is one of its own, so the
/// name in a refusal locates the rule that produced it.
#[test]
fn every_bound_a_crate_names_is_the_one_the_table_gives_that_crate() {
    let crates = crate_names();
    let mut prior = "";
    for bound in super::INPUT_BOUNDS {
        assert!(
            prior < bound.name,
            "INPUT_BOUNDS holds {:?} after {prior:?}; the table is in byte order of name",
            bound.name
        );
        prior = bound.name;
        assert!(bound.maximum > 0, "the bound {} admits nothing", bound.name);
        assert!(!bound.measures.trim().is_empty(), "the bound {} states nothing about what it measures", bound.name);
        let (owner, rest) =
            bound.name.split_once('.').unwrap_or_else(|| panic!("the bound {} is not `<crate>.<what>`", bound.name));
        assert!(
            crates.contains(&owner.to_string()),
            "the bound {} names the crate {owner}, which crates/ lacks",
            bound.name
        );
        assert!(!rest.is_empty(), "the bound {} names no part of that crate's input", bound.name);
    }
    for name in &crates {
        let sources = enforcing_sources(name);
        let enforces = sources.iter().any(|(_, text)| text.contains("within_input_bound("));
        let mut own = false;
        for (path, text) in &sources {
            for bound in super::INPUT_BOUNDS {
                if !text.contains(&format!("{:?}", bound.name)) {
                    continue;
                }
                let owner = bound.name.split_once('.').map(|(owner, _)| owner).unwrap_or_default();
                assert_eq!(
                    owner, name,
                    "{path} states the input bound {}, which the table gives to another crate",
                    bound.name
                );
                own = true;
            }
        }
        assert!(!enforces || own, "crates/{name} enforces an input bound the table does not hold");
    }
}
'''

_INPUT_BOUNDS_TEXT = """\
Every input a crate of this repository reads from outside the process is \
bounded. A crate that reads such an input refuses one above the bound and \
names the bound in the refusal, so a caller learns from the message alone \
which limit it met and what that limit holds. docs/design.md "Input bounds" \
states the rule.

The bounds come from one table, `INPUT_BOUNDS` in crates/contract/src/lib.rs, \
which holds each bound name beside the largest input it admits and a sentence \
saying what the count measures. A bound name is the crate that enforces the \
bound, a dot, and what the bound covers. `input_bound` reads one row, and \
`within_input_bound` reports whether a measured count lies within a named \
bound and returns as its error the rule the input broke. \
crates/contract/src/document.rs enforces `contract.document` already, and no \
other crate enforces a bound yet.

Give the table a row for each of the six bounds below, in byte order of name, \
and refuse above each one where that input is read, stating the refusal \
against the key the input arrived under. A crate declares the names of the \
bounds it enforces as public constants of that crate and states no bound name \
the table gives another crate.

- `evidence.bundle_file`, at most 4,194,304 bytes, one file of an evidence \
bundle, declared as `BUNDLE_FILE`. It is enforced on the manifest's own \
bytes, on the length the manifest states for each file it lists, on each file \
read while a bundle is verified, and on each file read while a manifest is \
built.
- `evidence.bundle_total`, at most 67,108,864 bytes, every file an evidence \
manifest lists, declared as `BUNDLE_TOTAL` and enforced on the sum of the \
lengths the manifest states.
- `context.summary`, at most 65,536 bytes, the summary a compaction carries \
across the cut, declared as `SUMMARY`. A summary above it fails the \
compaction rather than being carried.
- `context.continuation_task`, at most 16,384 bytes, the task text a \
compaction carries across the cut, declared as `CONTINUATION_TASK`. A task \
text above it fails the compaction before the summarization request is made.
- `workflow.bound_value`, at most 262,144 bytes, one value bound into a tool \
node's arguments, declared as `BOUND_VALUE` and measured as the bytes of the \
value's serialization.
- `workflow.node_arguments`, at most 524,288 bytes, one tool node's resolved \
arguments, declared as `NODE_ARGUMENTS` and measured the same way.

A value whose serialization fails, and a length past what this machine can \
address, are past every bound rather than unbounded.

checks/run.sh runs the check the whole change is judged on: the tests of \
foe-context, foe-contract, foe-evidence, and foe-workflow, clippy with \
warnings denied on the same crates, and scripts/loc.sh."""

INPUT_BOUNDS = Construction(
    name="input-bound-named-in-refusal",
    subject="Bound every input read from outside the process and name the bound in the refusal",
    units=(
        ConstructedUnit("crates/context", "foe-context", ("crates/context/src/lib.rs",), "crates/context/src/lib_test.rs"),
        ConstructedUnit("crates/evidence", "foe-evidence", ("crates/evidence/src/lib.rs",), "crates/evidence/src/lib_test.rs"),
        ConstructedUnit("crates/workflow", "foe-workflow", ("crates/workflow/src/bind.rs",), "crates/workflow/src/bind_test.rs"),
    ),
    interface_package="foe-contract",
    interface_paths=("crates/contract/src/lib.rs",),
    integration_test_file="crates/contract/src/lib_test.rs",
    integration_test="every_bound_a_crate_names_is_the_one_the_table_gives_that_crate",
    fixture_edits=(
        Edit("docs/design.md", _TOOLS_HEADING, _INPUT_BOUNDS_SPECIFICATION),
        Edit("crates/contract/src/lib.rs", _ERRORS_ANCHOR, _INPUT_BOUNDS_TABLE),
        Edit("crates/contract/src/document.rs", _DOCUMENT_SEED_OLD, _DOCUMENT_SEED_NEW),
    ),
    oracle_edits=(
        Edit("crates/contract/src/lib.rs", _INPUT_BOUNDS_ROWS_OLD, _INPUT_BOUNDS_ROWS_NEW),
        *_CONTEXT_EDITS,
        *_EVIDENCE_EDITS,
        *_WORKFLOW_EDITS,
    ),
    test_appends=(
        ("crates/context/src/lib_test.rs", _CONTEXT_TEST),
        ("crates/evidence/src/lib_test.rs", _EVIDENCE_TEST),
        ("crates/workflow/src/bind_test.rs", _WORKFLOW_TEST),
        ("crates/contract/src/lib_test.rs", _INPUT_BOUNDS_INTEGRATION),
    ),
    test_edits=(),
    ceilings=(*CONTRACT_CEILING, *WORKFLOW_CEILING),
    sentences=_INPUT_BOUNDS_SENTENCES,
    text=_INPUT_BOUNDS_TEXT,
    revert_unit="crates/evidence",
    rename=Edit(
        "crates/evidence/src/lib.rs",
        'pub const BUNDLE_FILE: &str = "evidence.bundle_file";',
        'pub const BUNDLE_FILE: &str = "workflow.bound_value";',
    ),
    rename_unit="crates/evidence",
)



# ---- the left-out change over three crates --------------------------------------

KERNEL_CEILING = ceiling_edits(
    "kernel    ",
    "kernel (`log` and `core`)",
    (
        ("AGENTS.md", "form the kernel and stay under CEILING"),
        ("docs/design.md", "its Rust source stays under CEILING lines,"),
    ),
    6450,
    6520,
)
TELEMETRY_CEILING = ceiling_edits(
    "telemetry ",
    "telemetry",
    (("AGENTS.md", "`telemetry` under CEILING"), ("docs/design.md", "`crates/telemetry` under CEILING lines.")),
    1000,
    1040,
)

_SIZE_HEADING = """## Size

The kernel is `log` and `core` — the log format, the loop, budgets, the"""

_LEFT_OUT_SPECIFICATION = (
    """### What a derived value left out

A value derived from a log or a bundle states what it left out of the input
it was derived from. A derived value that drops part of its input without
saying so leaves no reader able to tell that it happened, and the loss
reaches whatever rests on the value. The report holds the kind of thing left
out, how many of them there were, and the first few of them named. The kinds
stand in one table, `LEFT_OUT_KINDS` in `foe_log`, which holds each kind
beside what one of the things left out is. A kind is named for the crate
that reports it, a dot, and what was left out, so the kind in a report
locates the value that dropped something. A crate reports no kind the table
lacks and states no kind the table gives another crate.

"""
    + _SIZE_HEADING
)

_LEFT_OUT_SENTENCES: dict[str, tuple[str, ...]] = {
    "docs/design.md": (
        "A value derived from a log or a bundle states what it left out of the input it was derived from.",
        "A crate reports no kind the table lacks and states no kind the table gives another crate.",
    )
}

_STATE_ANCHOR = "/// A fold of the log into the state a reader needs. See [`fold`].\n"

_LEFT_OUT_TABLE = '''// ---- what a derived value left out ----------------------------------------------

/// What a value derived from a log or a bundle left out of the input it was
/// derived from: the kind of thing left out, how many of them there were,
/// and the first few of them named. A derived value that drops part of its
/// input without saying so leaves no reader able to tell that it happened,
/// so a crate that drops states what it dropped under a kind this table
/// holds. A kind is the crate that reports it, a dot, and what was left out.
#[derive(Debug, Clone, PartialEq)]
pub struct LeftOut {
    pub kind: String,
    pub count: usize,
    pub named: Vec<String>,
}

/// How many of the things left out a report names; the count states the rest.
pub const LEFT_OUT_NAMED: usize = 3;

/// Every kind of left-out input, beside what one of them is, in byte order of kind.
pub const LEFT_OUT_KINDS: &[(&str, &str)] =
    &[("log.partial_line", "bytes after the last complete line of an episode log")];

/// What one of the things of that kind is, or `None` when the table holds no such kind.
pub fn left_out_kind(kind: &str) -> Option<&'static str> {
    LEFT_OUT_KINDS.iter().find(|(name, _)| *name == kind).map(|(_, what)| *what)
}

/// A report of what was left out, naming the first [`LEFT_OUT_NAMED`] of it.
/// Nothing left out is no report, so a caller states an empty report as
/// nothing rather than as a count of zero.
pub fn left_out(kind: &str, named: Vec<String>) -> Option<LeftOut> {
    let count = named.len();
    let mut first = named;
    first.truncate(LEFT_OUT_NAMED);
    (count > 0).then(|| LeftOut { kind: kind.to_string(), count, named: first })
}

impl LeftOut {
    /// The sentence a report carries: how many were left out, what one of
    /// them is, and the first few of them named.
    pub fn sentence(&self) -> String {
        let what = left_out_kind(&self.kind).unwrap_or("something foe_log::LEFT_OUT_KINDS does not hold");
        format!("left out {} of: {what}; among them {}", self.count, self.named.join(", "))
    }
}

''' + _STATE_ANCHOR

_PARSE_LINES_ANCHOR = '''/// Parses complete lines of `bytes`; returns the events and the byte count
/// of the lines parsed. A trailing line without a newline is left unparsed.'''

_PARTIAL_TAIL_SEED = '''/// The kind this module reports what a read of a log left out under.
pub const PARTIAL_LINE: &str = "log.partial_line";

/// The bytes of `bytes` that no complete line covered, as what the read left
/// out, or `None` when every byte was consumed. A log cut short by a crash
/// ends in such a tail, and a reader that says nothing about it leaves
/// nobody able to tell that a line was lost.
pub fn partial_tail(bytes: &[u8], consumed: u64) -> Option<crate::LeftOut> {
    let left = bytes.len().saturating_sub(consumed as usize);
    let named = (left > 0).then(|| format!("{left} byte(s) after the last complete line"));
    crate::left_out(PARTIAL_LINE, named.into_iter().collect())
}

''' + _PARSE_LINES_ANCHOR

_LEFT_OUT_KINDS_OLD = '''pub const LEFT_OUT_KINDS: &[(&str, &str)] =
    &[("log.partial_line", "bytes after the last complete line of an episode log")];'''

_LEFT_OUT_KINDS_NEW = '''pub const LEFT_OUT_KINDS: &[(&str, &str)] = &[
    ("evidence.unchecked_file", "a file an evidence manifest lists that no established fact rests on"),
    ("log.partial_line", "bytes after the last complete line of an episode log"),
    ("telemetry.unreadable_line", "a line of an episode log this build cannot read"),
    ("workflow.unnamed_binding", "an argument shaped like a binding that names no node"),
];'''

_TELEMETRY_LEFT_OUT_EDITS: tuple[Edit, ...] = (
    Edit("crates/telemetry/src/lib.rs", "use foe_log::{Event, LogError, Usage};", "use foe_log::{Event, LeftOut, LogError, Usage};"),
    Edit(
        "crates/telemetry/src/lib.rs",
        '''pub fn emit_into(log: &Path, capture: &Path, key: Vec<u8>) -> Result<String, String> {
    let (events, dir, unparsed) = read_log(log).map_err(|error| format!("{}: {error}", log.display()))?;
    if unparsed > 0 {
        return Err(format!(
            "{}: {unparsed} line(s) this build cannot read; scrubbing coverage cannot be guaranteed. \\
             Nothing emitted.",
            log.display()
        ));
    }''',
        '''pub fn emit_into(log: &Path, capture: &Path, key: Vec<u8>) -> Result<String, String> {
    let (events, dir, unreadable) = read_log(log).map_err(|error| format!("{}: {error}", log.display()))?;
    if let Some(report) = unreadable {
        return Err(format!(
            "{}: the read {}; scrubbing coverage cannot be guaranteed. Nothing emitted.",
            log.display(),
            report.sentence()
        ));
    }''',
    ),
    Edit(
        "crates/telemetry/src/lib.rs",
        '''/// Reads a log given either its directory or the file itself, and returns
/// the events, the directory the log lives in, and how many lines did not
/// parse.
///
/// Structural validation is not applied: a log cut short by a crash still
/// describes everything that happened before the cut, and that is worth
/// emitting. A line whose event shape this build cannot read is a different
/// matter, because the scrubber learns the values it must remove from the
/// log itself. Losing the line that carries the granted roots loses the
/// known-value layer, and nothing downstream can tell that it happened.
/// Skipped lines are therefore counted for the caller to refuse on.
pub fn read_log(path: &Path) -> Result<(Vec<Event>, PathBuf, usize), LogError> {
    let file = if path.is_dir() { path.join(foe_log::fold::LOG_FILE) } else { path.to_path_buf() };
    let dir = if path.is_dir() { path.to_path_buf() } else { path.parent().unwrap_or(Path::new(".")).to_path_buf() };
    let text = std::fs::read_to_string(&file)?;
    let lines: Vec<&str> = text.lines().filter(|line| !line.trim().is_empty()).collect();
    let events: Vec<Event> = lines.iter().filter_map(|line| serde_json::from_str(line).ok()).collect();
    let unparsed = lines.len() - events.len();
    Ok((events, dir, unparsed))
}''',
        '''/// The kind this crate reports what a read of a log left out under.
pub const UNREADABLE_LINE: &str = "telemetry.unreadable_line";

/// Reads a log given either its directory or the file itself, and returns
/// the events, the directory the log lives in, and what the read left out.
///
/// Structural validation is not applied: a log cut short by a crash still
/// describes everything that happened before the cut, and that is worth
/// emitting. A line whose event shape this build cannot read is a different
/// matter, because the scrubber learns the values it must remove from the
/// log itself. Losing the line that carries the granted roots loses the
/// known-value layer, and nothing downstream can tell that it happened. Such
/// a line is therefore named, by its number in the file and by what the
/// parse said, for the caller to refuse on. A blank line is no line of the
/// log and takes no number of its own away from the ones that follow.
pub fn read_log(path: &Path) -> Result<(Vec<Event>, PathBuf, Option<LeftOut>), LogError> {
    let file = if path.is_dir() { path.join(foe_log::fold::LOG_FILE) } else { path.to_path_buf() };
    let dir = if path.is_dir() { path.to_path_buf() } else { path.parent().unwrap_or(Path::new(".")).to_path_buf() };
    let text = std::fs::read_to_string(&file)?;
    let mut events = Vec::new();
    let mut unreadable = Vec::new();
    for (index, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        match serde_json::from_str(line) {
            Ok(event) => events.push(event),
            Err(error) => unreadable.push(format!("line {}: {error}", index + 1)),
        }
    }
    Ok((events, dir, foe_log::left_out(UNREADABLE_LINE, unreadable)))
}''',
    ),
    Edit(
        "crates/telemetry/src/capture.rs",
        '''        let (events, dir, unparsed) = crate::read_log(Path::new(log)).map_err(|error| format!("{log}: {error}"))?;
        if unparsed > 0 {
            eprintln!(
                "!! {log}: {unparsed} line(s) this build cannot read; scrubbing coverage cannot be guaranteed. \\
                 Emission refuses this log. This is not what would be written."
            );
        }''',
        '''        let (events, dir, unreadable) = crate::read_log(Path::new(log)).map_err(|error| format!("{log}: {error}"))?;
        if let Some(report) = unreadable {
            eprintln!(
                "!! {log}: the read {}; scrubbing coverage cannot be guaranteed. Emission refuses this log. \\
                 This is not what would be written.",
                report.sentence()
            );
        }''',
    ),
)

_EVIDENCE_LEFT_OUT_EDITS: tuple[Edit, ...] = (
    Edit(
        "crates/evidence/src/lib.rs",
        """pub fn digest_of(bytes: &[u8]) -> String {""",
        """/// The kind this crate reports what a verified bundle left out under. A
/// bundle retains what its facts rest on; a file nothing rests on was
/// carried by a bundle nobody checked it against, and verification names it
/// rather than passing over it.
pub const UNCHECKED_FILE: &str = "evidence.unchecked_file";

pub fn digest_of(bytes: &[u8]) -> String {""",
    ),
    Edit(
        "crates/evidence/src/lib.rs",
        """    verify_provenance(&manifest.proposal_log, &record.verification_log, &logs)?;

    Ok(VerifiedAdoption {""",
        """    verify_provenance(&manifest.proposal_log, &record.verification_log, &logs)?;

    let mut rests_on: std::collections::BTreeSet<&str> = logs.keys().map(String::as_str).collect();
    rests_on.insert(manifest.adoption_record.as_str());
    rests_on.insert(fingerprint_file.path.as_str());
    if let Some(file) = listed(&record.artifact_manifest_sha256) {
        rests_on.insert(file.path.as_str());
    }
    if let Some(path) = &candidate_file {
        rests_on.insert(path.as_str());
    }
    let unchecked =
        manifest.files.iter().map(|file| file.path.clone()).filter(|path| !rests_on.contains(path.as_str())).collect();
    if let Some(report) = foe_log::left_out(UNCHECKED_FILE, unchecked) {
        return Err(invalid("evidence.manifest.files", report.sentence()));
    }

    Ok(VerifiedAdoption {""",
    ),
)

_WORKFLOW_LEFT_OUT_EDITS: tuple[Edit, ...] = (
    Edit(
        "crates/workflow/src/bind.rs",
        """use serde_json::{Map, Value};""",
        """use serde_json::{Map, Value};

/// The kind this crate reports what resolving a tool node's arguments left
/// out under. An object holding `pointer` and no `$node` is shaped like a
/// binding and binds nothing, so it was passed to the tool as written and
/// nobody could tell that the value it meant to carry never arrived.
pub const UNNAMED_BINDING: &str = "workflow.unnamed_binding";""",
    ),
    Edit(
        "crates/workflow/src/bind.rs",
        '''pub fn resolve(args: &Map<String, Value>, input: &dyn Fn(&str) -> Option<Value>) -> Result<Value, String> {
    fn walk(value: &Value, input: &dyn Fn(&str) -> Option<Value>) -> Result<Value, String> {
        let Some(object) = value.as_object() else {
            return Ok(match value {
                Value::Array(items) => Value::Array(items.iter().map(|v| walk(v, input)).collect::<Result<_, _>>()?),
                other => other.clone(),
            });
        };
        let Some(name) = object.get("$node").and_then(Value::as_str) else {
            let fields = object.iter().map(|(k, v)| Ok((k.clone(), walk(v, input)?)));
            return Ok(Value::Object(fields.collect::<Result<_, String>>()?));
        };
        let whole = input(name).ok_or_else(|| format!("binds `{name}`, which has produced no value"))?;
        let path = object.get("pointer").and_then(Value::as_str).unwrap_or("");
        pointer(&whole, path)
            .cloned()
            .ok_or_else(|| format!("binds `{name}` at pointer `{path}`, which is absent from its value"))
    }
    walk(&Value::Object(args.clone()), input)
}''',
        '''pub fn resolve(args: &Map<String, Value>, input: &dyn Fn(&str) -> Option<Value>) -> Result<Value, String> {
    fn walk(
        value: &Value,
        input: &dyn Fn(&str) -> Option<Value>,
        at: &str,
        unnamed: &mut Vec<String>,
    ) -> Result<Value, String> {
        let Some(object) = value.as_object() else {
            return Ok(match value {
                Value::Array(items) => Value::Array(
                    items
                        .iter()
                        .enumerate()
                        .map(|(index, v)| walk(v, input, &format!("{at}/{index}"), unnamed))
                        .collect::<Result<_, _>>()?,
                ),
                other => other.clone(),
            });
        };
        let Some(name) = object.get("$node").and_then(Value::as_str) else {
            if object.contains_key("pointer") {
                unnamed.push(format!("`{at}`"));
            }
            let fields = object.iter().map(|(k, v)| Ok((k.clone(), walk(v, input, &format!("{at}/{k}"), unnamed)?)));
            return Ok(Value::Object(fields.collect::<Result<_, String>>()?));
        };
        let whole = input(name).ok_or_else(|| format!("binds `{name}`, which has produced no value"))?;
        let path = object.get("pointer").and_then(Value::as_str).unwrap_or("");
        pointer(&whole, path)
            .cloned()
            .ok_or_else(|| format!("binds `{name}` at pointer `{path}`, which is absent from its value"))
    }
    let mut unnamed = Vec::new();
    let resolved = walk(&Value::Object(args.clone()), input, "", &mut unnamed)?;
    match foe_log::left_out(UNNAMED_BINDING, unnamed) {
        Some(report) => Err(report.sentence()),
        None => Ok(resolved),
    }
}''',
    ),
)

_TELEMETRY_LEFT_OUT_TEST_EDITS: tuple[Edit, ...] = (
    Edit(
        "crates/telemetry/tests/emission.rs",
        '    assert!(complaint.contains("1 line(s) this build cannot read"), "{complaint}");',
        '    assert!(complaint.contains("left out 1 of: a line of an episode log this build cannot read"), "{complaint}");',
    ),
    Edit(
        "crates/telemetry/tests/emission.rs",
        '''    let (events, dir, unparsed) = foe_telemetry::read_log(&fixtures().join("skewed")).unwrap();
    assert_eq!(unparsed, 1);''',
        '''    let (events, dir, unreadable) = foe_telemetry::read_log(&fixtures().join("skewed")).unwrap();
    assert_eq!(unreadable.expect("the skewed log holds a line this build cannot read").count, 1);''',
    ),
)

_WORKFLOW_LEFT_OUT_TEST_EDITS: tuple[Edit, ...] = (
    Edit(
        "crates/workflow/src/bind_test.rs",
        '''        "plain": { "pointer": "/x" }
    });
    let resolved = resolve(args.as_object().unwrap(), &inputs).unwrap();
    assert_eq!(
        resolved,
        json!({
            "pattern": "parse",
            "nested": { "list": [ "hits", 1 ], "whole": { "top_symbol": "parse", "count": 3 } },
            "plain": { "pointer": "/x" }
        })
    );''',
        '''        "plain": { "path": "/x" }
    });
    let resolved = resolve(args.as_object().unwrap(), &inputs).unwrap();
    assert_eq!(
        resolved,
        json!({
            "pattern": "parse",
            "nested": { "list": [ "hits", 1 ], "whole": { "top_symbol": "parse", "count": 3 } },
            "plain": { "path": "/x" }
        })
    );''',
    ),
)

_TELEMETRY_LEFT_OUT_TEST = '''
/// docs/design.md "What a derived value left out": a line this build cannot
/// read is named by its number in the file rather than counted alone, a blank
/// line takes no number of its own away from the lines that follow, and
/// emission refuses a log the read left anything out of.
#[test]
fn an_unreadable_line_is_named_by_its_number_and_refuses_the_emission() {
    let dir = temp("unreadable");
    let clean = std::fs::read_to_string(fixtures().join("clean").join("episode.jsonl")).unwrap();
    let mut lines: Vec<String> = clean.lines().map(str::to_owned).collect();
    lines.insert(1, String::new());
    lines.insert(2, "{\\"seq\\": 1, \\"type\\": \\"nothing/this/build/knows\\"}".into());
    lines.insert(3, "not json at all".into());
    let log = dir.join("episode.jsonl");
    std::fs::write(&log, lines.join("\\n") + "\\n").unwrap();

    let (events, _, unreadable) = foe_telemetry::read_log(&log).unwrap();
    let report = unreadable.expect("two lines of the log cannot be read");
    assert_eq!(report.kind, foe_telemetry::UNREADABLE_LINE);
    assert_eq!(report.count, 2);
    assert!(report.named[0].starts_with("line 3:"), "the first unreadable line is not named: {:?}", report.named);
    assert!(report.named[1].starts_with("line 4:"), "the second unreadable line is not named: {:?}", report.named);
    assert_eq!(events.len(), clean.lines().filter(|line| !line.trim().is_empty()).count());

    let refused = foe_telemetry::emit_into(&log, &dir.join("otel.jsonl"), key()).unwrap_err();
    assert!(refused.contains("line 3:"), "the refusal does not name the line: {refused}");
    assert!(!dir.join("otel.jsonl").exists(), "a refused log was emitted anyway");

    let (_, _, nothing) = foe_telemetry::read_log(&fixtures().join("clean")).unwrap();
    assert!(nothing.is_none(), "a readable log reports something left out");
}
'''

_EVIDENCE_LEFT_OUT_TEST = '''
/// docs/design.md "What a derived value left out": a bundle retains what its
/// facts rest on, so a retained file no established fact rests on is named
/// rather than passed over, and a bundle carrying no such file verifies.
#[test]
fn a_retained_file_no_fact_rests_on_is_named() {
    let root = tempfile::tempdir().unwrap();
    bundle(root.path(), None, None);
    verify_adoption(root.path(), None).expect("a bundle whose files all bear on its facts verifies");

    let root = tempfile::tempdir().unwrap();
    std::fs::write(root.path().join("notes.txt"), b"carried along").unwrap();
    bundle(root.path(), None, None);
    let refused = verify_adoption(root.path(), None).unwrap_err().to_string();
    assert!(refused.contains("notes.txt"), "the unchecked file is not named: {refused}");
    let what = foe_log::left_out_kind(super::UNCHECKED_FILE).expect("the kind stands in LEFT_OUT_KINDS");
    assert!(refused.contains(what), "the refusal does not say what was left out: {refused}");
}
'''

_WORKFLOW_LEFT_OUT_TEST = '''
/// docs/design.md "What a derived value left out": an object holding
/// `pointer` and no `$node` is shaped like a binding and binds nothing, so
/// resolution names it by where it stands rather than passing it to the tool
/// as written.
#[test]
fn an_argument_that_binds_nothing_is_named_rather_than_passed_on() {
    let inputs = |name: &str| match name {
        "manifest" => Some(json!({ "top_symbol": "parse" })),
        _ => None,
    };
    let args = json!({
        "pattern": { "$node": "manifest", "pointer": "/top_symbol" },
        "plain": { "pointer": "/x" },
        "list": [ { "pointer": "/y" } ]
    });
    let refused = resolve(args.as_object().unwrap(), &inputs).unwrap_err();
    assert!(refused.contains("/plain"), "the argument that binds nothing is not named: {refused}");
    assert!(refused.contains("/list/0"), "the one inside an array is not named: {refused}");
    let sound = json!({ "pattern": { "$node": "manifest", "pointer": "/top_symbol" }, "plain": { "path": "/x" } });
    let resolved: Value = resolve(sound.as_object().unwrap(), &inputs).unwrap();
    assert_eq!(resolved, json!({ "pattern": "parse", "plain": { "path": "/x" } }));
}
'''

_LEFT_OUT_INTEGRATION = '''
// ---- what a derived value left out ----------------------------------------------

/// The repository root, from this crate's manifest directory.
fn repository_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// The name of every crate directory under `crates/`.
fn crate_names() -> Vec<String> {
    let directory = repository_root().join("crates");
    let entries = std::fs::read_dir(&directory).unwrap_or_else(|e| panic!("{}: {e}", directory.display()));
    let mut names: Vec<String> = entries
        .map(|entry| entry.unwrap())
        .filter(|entry| entry.path().is_dir())
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

/// Every Rust source of one crate that is neither a test file nor the file
/// the table itself stands in. The table holds every kind, so that file is
/// the one place every kind is expected.
fn reporting_sources(name: &str) -> Vec<(String, String)> {
    let mut found = Vec::new();
    let mut pending = vec![repository_root().join("crates").join(name).join("src")];
    while let Some(directory) = pending.pop() {
        let Ok(entries) = std::fs::read_dir(&directory) else { continue };
        for entry in entries.map(|entry| entry.unwrap().path()) {
            if entry.is_dir() {
                pending.push(entry);
                continue;
            }
            let relative = entry.strip_prefix(repository_root()).unwrap().to_string_lossy().into_owned();
            let is_test = relative.ends_with("_test.rs") || relative.contains("/tests/");
            if !relative.ends_with(".rs") || is_test || relative == "crates/log/src/lib.rs" {
                continue;
            }
            found.push((relative, std::fs::read_to_string(&entry).unwrap()));
        }
    }
    found.sort();
    found
}

/// One vocabulary of what a derived value left out. The table holds each kind
/// once and in order, a kind is the crate that reports it and what was left
/// out, and the only kind a crate states is one of its own, so the kind in a
/// report locates the value that dropped something.
#[test]
fn every_kind_a_crate_reports_is_the_one_the_table_gives_that_crate() {
    let crates = crate_names();
    let mut prior = "";
    for (kind, what) in LEFT_OUT_KINDS {
        assert!(prior < *kind, "LEFT_OUT_KINDS holds {kind:?} after {prior:?}; the table is in byte order of kind");
        prior = kind;
        assert!(!what.trim().is_empty(), "the kind {kind} states nothing about what was left out");
        let (owner, rest) = kind.split_once('.').unwrap_or_else(|| panic!("the kind {kind} is not `<crate>.<what>`"));
        assert!(crates.contains(&owner.to_string()), "the kind {kind} names the crate {owner}, which crates/ lacks");
        assert!(!rest.is_empty(), "the kind {kind} names nothing that was left out");
    }
    for name in &crates {
        let sources = reporting_sources(name);
        let reports = sources.iter().any(|(_, text)| text.contains("left_out("));
        let mut own = false;
        for (path, text) in &sources {
            for (kind, _) in LEFT_OUT_KINDS {
                if !text.contains(&format!("{kind:?}")) {
                    continue;
                }
                let owner = kind.split_once('.').map(|(owner, _)| owner).unwrap_or_default();
                assert_eq!(owner, name, "{path} states the kind {kind}, which the table gives to another crate");
                own = true;
            }
        }
        assert!(!reports || own, "crates/{name} reports a kind the table does not hold");
    }
}
'''

_LEFT_OUT_TEXT = """\
A value this repository derives from a log or from a bundle states what it \
left out of the input it was derived from. A derived value that drops part \
of its input without saying so leaves no reader able to tell that it \
happened, and the loss reaches whatever rests on the value. \
docs/design.md "What a derived value left out" states the rule.

A report is `LeftOut` in crates/log/src/lib.rs: the kind of thing left out, \
how many of them there were, and the first `LEFT_OUT_NAMED` of them named. \
`left_out` builds one and returns none when nothing was left out, \
`left_out_kind` says what one thing of a kind is, and `LeftOut::sentence` \
renders the report for a message. The kinds stand in the table \
`LEFT_OUT_KINDS` beside what one of the things left out is. A kind is the \
crate that reports it, a dot, and what was left out. \
crates/log/src/fold.rs reports `log.partial_line` already, and no other \
crate reports a kind yet.

Give the table a row for each of the three kinds below, in byte order of \
kind, and report under it where that input is dropped. The crate that \
reports a kind declares its name as a public constant of that crate and \
states no kind the table gives another crate.

- `evidence.unchecked_file`, a file an evidence manifest lists that no \
established fact rests on, declared as `UNCHECKED_FILE`. Standalone adoption \
verification refuses such a bundle, naming the files against the key \
`evidence.manifest.files`. The files a fact rests on are the retained \
episode logs, the adoption record, the fingerprint document, the artifact \
manifest, and the attested candidate file when the accepted verification \
names one.
- `telemetry.unreadable_line`, a line of an episode log this build cannot \
read, declared as `UNREADABLE_LINE`. Reading a log returns the report in \
place of the count it returns now, naming each unreadable line by its number \
in the file and by what the parse said. A blank line is no line of the log \
and takes no number of its own away from the lines that follow. Emission and \
the preview command refuse a log the read left anything out of, and say what \
it left out.
- `workflow.unnamed_binding`, an argument shaped like a binding that names no \
node, declared as `UNNAMED_BINDING`. Resolving a tool node's arguments \
refuses when any object holds `pointer` and no `$node`, naming each one by \
the JSON Pointer of where it stands within the arguments, in backticks.

checks/run.sh runs the check the whole change is judged on: the tests of \
foe-evidence, foe-log, foe-telemetry, and foe-workflow, clippy with warnings \
denied on the same crates, and scripts/loc.sh."""

LEFT_OUT = Construction(
    name="left-out-input-is-named-rather-than-dropped",
    subject="Name what a derived value left out of the input it was derived from",
    units=(
        ConstructedUnit("crates/evidence", "foe-evidence", ("crates/evidence/src/lib.rs",), "crates/evidence/src/lib_test.rs"),
        ConstructedUnit(
            "crates/telemetry",
            "foe-telemetry",
            ("crates/telemetry/src/lib.rs", "crates/telemetry/src/capture.rs"),
            "crates/telemetry/tests/emission.rs",
        ),
        ConstructedUnit("crates/workflow", "foe-workflow", ("crates/workflow/src/bind.rs",), "crates/workflow/src/bind_test.rs"),
    ),
    interface_package="foe-log",
    interface_paths=("crates/log/src/lib.rs",),
    integration_test_file="crates/log/src/fold_test.rs",
    integration_test="every_kind_a_crate_reports_is_the_one_the_table_gives_that_crate",
    fixture_edits=(
        Edit("docs/design.md", _SIZE_HEADING, _LEFT_OUT_SPECIFICATION),
        Edit("crates/log/src/lib.rs", _STATE_ANCHOR, _LEFT_OUT_TABLE),
        Edit("crates/log/src/fold.rs", _PARSE_LINES_ANCHOR, _PARTIAL_TAIL_SEED),
    ),
    oracle_edits=(
        Edit("crates/log/src/lib.rs", _LEFT_OUT_KINDS_OLD, _LEFT_OUT_KINDS_NEW),
        *_EVIDENCE_LEFT_OUT_EDITS,
        *_TELEMETRY_LEFT_OUT_EDITS,
        *_WORKFLOW_LEFT_OUT_EDITS,
    ),
    test_edits=(*_TELEMETRY_LEFT_OUT_TEST_EDITS, *_WORKFLOW_LEFT_OUT_TEST_EDITS),
    test_appends=(
        ("crates/evidence/src/lib_test.rs", _EVIDENCE_LEFT_OUT_TEST),
        ("crates/telemetry/tests/emission.rs", _TELEMETRY_LEFT_OUT_TEST),
        ("crates/workflow/src/bind_test.rs", _WORKFLOW_LEFT_OUT_TEST),
        ("crates/log/src/fold_test.rs", _LEFT_OUT_INTEGRATION),
    ),
    ceilings=(*KERNEL_CEILING, *TELEMETRY_CEILING, *WORKFLOW_CEILING),
    sentences=_LEFT_OUT_SENTENCES,
    text=_LEFT_OUT_TEXT,
    revert_unit="crates/evidence",
    rename=Edit(
        "crates/evidence/src/lib.rs",
        'pub const UNCHECKED_FILE: &str = "evidence.unchecked_file";',
        'pub const UNCHECKED_FILE: &str = "telemetry.unreadable_line";',
    ),
    rename_unit="crates/evidence",
    check_skip="file_failures_preserve_the_original_recording_error",
    check_skip_reason=(
        "The one foe-log test whose name this holds makes a directory unwritable and then writes to it, which a\n"
        "# kernel sandbox refuses before the writer sees it, so this suite leaves it out."
    ),
)


CONSTRUCTIONS: dict[str, Construction] = {construction.name: construction for construction in (INPUT_BOUNDS, LEFT_OUT)}


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
    fan_out.add_argument("--text", type=Path, help="a file holding the specification paragraphs, in place of the ones drafted from the commit message")
    fan_out.add_argument("--keep-workspace", action="store_true", help="keep the workspace copy beside the recipe, for inspection")
    constructing = commands.add_parser("construct", help="write a constructed fan-out task directory")
    constructing.add_argument("--task", required=True, choices=sorted(CONSTRUCTIONS), help="which construction")
    constructing.add_argument("--repo", required=True, type=Path, help="the repository the base tree comes from")
    constructing.add_argument("--commit", required=True, help=f"the commit whose tree the fixture is built from; its tree may not hold {EVALUATION_ROOT}")
    constructing.add_argument("--out", required=True, type=Path, help="the task directory to create")
    constructing.add_argument("--keep-workspace", action="store_true", help="keep the workspace copy beside the recipe, for inspection")
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
            supplied = args.text.read_text(encoding="utf-8") if args.text else None
            authored = author_fan_out(
                args.repo.resolve(), args.commit, args.out.resolve(), args.name, args.allow_traces, args.keep_workspace, args.revert_unit, supplied
            )
            print(f"task {authored.task.name} written to {authored.directory}")
            generated_checks = authored.task.metadata["generated_unit_checks"]
            for unit in authored.units:
                judged = "its generated line check" if unit.name in generated_checks else "the commit's tests"
                print(f"unit {unit.name}: judged by {judged}, {len(unit.implementation)} implementation file(s), {len(unit.tests)} hidden test(s)")
            print(f"interface, set apart from the units: {', '.join(authored.task.metadata['interface_paths']) or 'none'}")
            print(f"revert corruption: {authored.revert_unit}")
            if authored.shared is None:
                print(f"rename corruption: none; {authored.task.metadata['shared_element_absent']}")
            else:
                print(f"rename corruption: {authored.shared.element} in {authored.shared.unit}, used by {', '.join(authored.shared.used_by)}")
            print(f"specification sentences: {sum(len(found) for found in authored.sentences.values())}")
            return 0
        if args.command == "construct":
            construction = CONSTRUCTIONS[args.task]
            constructed = author_construction(args.repo.resolve(), args.out.resolve(), construction, args.commit, args.keep_workspace)
            print(f"task {constructed.name} written to {args.out}")
            for unit in construction.units:
                print(f"unit {unit.name}: {unit_lines(construction, unit)} added line(s) in {', '.join(unit.files)}, judged by {unit.package} through {unit.test_file}")
            print(f"interface, set apart from the units: {', '.join(construction.interface_paths)}")
            print(f"the units lie in {len(construction.units)} directories that do not overlap, so a delegation can grant each one of them")
            print(f"whole-change check: {' '.join(construction.packages)}, judged against {construction.integration_test}")
            print(f"revert corruption: {construction.revert_unit}; rename corruption: {construction.rename_unit}")
            print(f"text: {len(constructed.text)} characters")
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
