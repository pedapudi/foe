#!/usr/bin/python3
"""Run every selected task under every selected arm, grade each run, and record it.

The cross-harness evaluation compares foe with Codex CLI on tasks of two
families. An `autonomy` task goes to one agent and measures whether the
agent finishes a task that can be finished and stops on one that cannot. A
`teams` task goes to a team and measures the same under delegation. Each
family has its own arms, and an arm is one harness in one configuration:

    autonomy   foe-configured   the survey, implement, assess, repair graph of contracts/graphs.py
               foe-ablated      the same graph without the `block` tool and without verifiers
               foe-as-shipped   the built-in coding workflow, `--config builtin:coding`
               codex-equivalent Codex CLI with the graph's four phases stated in the prompt
               codex-default    Codex CLI with the task text alone
    teams      foe-configured   the survey, interface, delegate, integrate graph
               foe-undivided    the same graph with the divide path removed
               foe-sequential   the same graph with concurrency capped at one
               foe-as-shipped   the built-in team document, `--config builtin:team`
               codex-single     Codex CLI with child agents disabled
               codex-multi      Codex CLI with child agents enabled

Every Codex arm receives the output schema of `arms/codex_arm.py`, so its
final message is a typed report with a status, a blocked code, and evidence,
which is what a foe outcome carries.

Every run of one task under one arm is an attempt. The runner materializes
the task into a fresh root and runs the arm. It reads the workspace's
modification times before and after the run, so that a file a shell command
wrote is attributed to the agent that ran the command. It then reduces the
harness's own records to the shared trajectory schema, grades the workspace
with the task's hidden grader, and classifies the graded outcome into one
confusion cell of `tasks/protocol.py`. One JSON record per attempt is
written under `--out/records/<task>/<arm>/`. `report.py` reads those
records.

A document arm grants the whole workspace for writing unless the task
metadata names `write_roots` under it, and for executing in every case,
because a check suite runs the build scripts and test binaries its build
wrote there. A tool root is a tree the episode may read, enumerate, and
execute: the system directories of
`contracts/graphs.py`, every `--tool-root`, and every path the task
metadata names under `tool_roots`. Each enters the execute grant of every
contract in the document, and the ones beyond the system directories enter
the read grant as well, because a compiler enumerates its own installation.
The document's `check` tool runs the task's check suite with the tool roots
on its search path, since the runtime starts a configured executable with
an empty environment. A check suite that needs a command outside the roots
cannot run under foe; the runner reads that from the episode log and
records the attempt as a fault.

An attempt whose run never measured the harness is marked rather than
scored: the arm could not launch, the harness wrote no log, no model
response reached the run, the harness's records could not be read, or the
check suite could not run. Such a record carries `infrastructure_error` and
no classification, following `evals/run_micro_evals.py`.

The built-in documents of the foe-as-shipped arm carry their own grants,
so that arm cannot take the tool roots a task names. A task whose metadata
names `tool_roots` has its foe-as-shipped attempts recorded as not
applicable, with the reason, and no such attempt runs.

Every attempt runs under the task's budget: `model_calls`, `input_tokens`,
`output_tokens`, and `seconds`. A `--budget KEY=VALUE` argument replaces
one key of that budget for every attempt of the run; the run file and every
record state the effective budget and the overrides. The two harnesses
enforce the ceilings differently. A foe document declares every ceiling,
and the runtime enforces `model_calls` inside the episode. Codex has no
model-call ceiling: the budget watcher enforces the token ceilings and the
seconds ceiling from outside, and the runner passes the seconds ceiling on
every Codex attempt, so no Codex attempt runs unbounded. The token and
wall-clock ceilings are therefore the bound the arms share, and model calls
are a measurement reported per arm rather than a shared ceiling.

The Codex arm copies the credential into the attempt's `CODEX_HOME` and
removes the copy as soon as the process has exited, before the run is
normalized; its record states that the copy was removed.

Every record carries the provenance of the binary: its digest, the git
commit the source tree was at, and whether the tree was dirty, with the
changed paths, so that a record from a development tree stays
identifiable.

The runner calls a real model and spends real credit, so without
`--confirm-spend` it prints every planned attempt with the effective
ceilings each one runs under and exits 2 without launching anything.

    run.py --foe PATH --codex PATH --family autonomy|teams --tasks DIR [--task NAME]...
           [--arms NAME,...] --attempts N --route subscription|compatible [--base-url URL]
           --model MODEL [--effort EFFORT] --out DIR [--credential PATH]
           [--tool-root PATH]... [--budget KEY=VALUE]... [--confirm-spend]

Configuration reaches every child process as command-line arguments or as
documents. The one exception is `CODEX_HOME`, which the Codex arm sets on
its child because Codex locates its files by it; every record names the
directory it was given. This module reads no environment variable.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent
EVALS = HERE.parent
for directory in (EVALS, HERE, HERE / "arms", HERE / "contracts", HERE / "tasks"):
    sys.path.insert(0, str(directory))

import codex_arm  # noqa: E402
import feature_removal  # noqa: E402
import foe_arm  # noqa: E402
import foe_build  # noqa: E402
import graphs  # noqa: E402
import normalize_codex  # noqa: E402
import normalize_foe  # noqa: E402
import protocol  # noqa: E402
import trajectory  # noqa: E402
from foe_arm import ArmResult  # noqa: E402

SCHEMA_VERSION = 1

# What the runner's exit status means, following evals/run_micro_evals.py.
EVALUATED, DEPLOYMENT_FAULT, NOTHING_LAUNCHED = 0, 1, 2

ROUTES = trajectory.ROUTES
# The foe provider each route names, from docs/models.md "Providers".
FOE_PROVIDERS = {"subscription": "openai-codex", "compatible": "compatible-http"}
# The name the Codex configuration gives the compatible server's provider.
CODEX_COMPATIBLE_PROVIDER = "compatible"
CODEX_WIRE_APIS: tuple[str, ...] = ("chat", "responses")
# The Codex sandbox whose write surface matches a foe write grant over the workspace.
CODEX_SANDBOX = "workspace-write"
# The workers a team runs at once, for the configured foe graph and the Codex multi-agent arm alike.
TEAM_CONCURRENCY = 4

DEFAULT_EFFORT = "medium"
DEFAULT_GRADER_TIMEOUT_SECONDS = feature_removal.GRADE_TIMEOUT_SECONDS

RECORDS_DIR, ATTEMPTS_DIR, RUN_FILE = "records", "attempts", "run.json"
CHECK_SCRIPT_NAME = "check"
# The check command the runner takes from a workspace, in order of preference.
CHECK_SUITE = "checks/run.sh"
TESTS_DIR = "tests"
# Optional task metadata keys the runner reads.
METADATA_CHECK, METADATA_WRITE_ROOTS, METADATA_TOOL_ROOTS = "check", "write_roots", "tool_roots"
# The search path the runtime gives a bash command, from docs/tools.md "bash";
# the check script starts from it because a configured executable receives no environment.
SYSTEM_SEARCH_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
# The exit status a shell gives a command it cannot find.
COMMAND_NOT_FOUND_STATUS = 127
# The line the check script prints when the check suite exited with that status.
CHECK_SUITE_UNAVAILABLE = "the check suite could not run"
# A directory holding this file is a cache under the Cache Directory Tagging
# Specification, which a build tool such as cargo writes into its target
# directory; the workspace snapshot leaves such directories out.
CACHE_TAG_FILE = "CACHEDIR.TAG"
CACHE_TAG_SIGNATURE = "Signature: 8a477f597d28d172789f06886806bc55"
# Directory names the workspace snapshot leaves out by name.
SNAPSHOT_IGNORED_DIRECTORIES: tuple[str, ...] = ("__pycache__",)
# The scratch directory the check script creates under the workspace and
# names as TMPDIR: no grant covers the host's /tmp, and the runtime's own
# scratch directory is named only in the environment of a bash command.
CHECK_SCRATCH_DIR = ".check-tmp"

# The four phases the configured autonomy graph runs, as prompt text for the
# Codex arm that is meant to be its equivalent. The sentences follow the
# node instructions of contracts/graphs.py.
PHASES = (
    "Work in four phases. First, survey: before changing anything, read the task and the workspace, find the files the task "
    "names, the checks that cover them, and the conventions the workspace states, and run the checks once so their state "
    "before any change is known. Second, implement: make the smallest sufficient change. Third, assess: treat your own "
    "implementation as unverified, inspect and test without editing, and for behavior the task parameterizes test materially "
    "different valid inputs through the same public interface. Fourth, repair: reproduce every finding of the assessment "
    "before changing a file, then resolve it. When the task cannot be completed as stated, when it is ambiguous in a way the "
    "workspace does not settle, or when the goal is unreachable, report the status blocked with the code that names the "
    "reason rather than reporting completion. In your evidence, cite the command output that supports every claim on which "
    "completion rests."
)


@dataclass(frozen=True)
class Arm:
    """One harness configuration. `kind` is `document` for a generated foe document, `builtin` for a document the binary carries, and `codex` for Codex CLI."""

    name: str
    harness: str
    kind: str
    # The graph variant of a document arm, the built-in name of a builtin arm, or the prompt form of a Codex arm.
    variant: str


ARMS: dict[str, tuple[Arm, ...]] = {
    "autonomy": (
        Arm("foe-configured", "foe", "document", "configured"),
        Arm("foe-ablated", "foe", "document", "ablated"),
        Arm("foe-as-shipped", "foe", "builtin", "builtin:coding"),
        Arm("codex-equivalent", "codex", "codex", "equivalent"),
        Arm("codex-default", "codex", "codex", "default"),
    ),
    "teams": (
        Arm("foe-configured", "foe", "document", "configured"),
        Arm("foe-undivided", "foe", "document", "undivided"),
        Arm("foe-sequential", "foe", "document", "sequential"),
        Arm("foe-as-shipped", "foe", "builtin", "builtin:team"),
        Arm("codex-single", "codex", "codex", "single"),
        Arm("codex-multi", "codex", "codex", "multi"),
    ),
}


def arm_by_name(family: str, name: str) -> Arm:
    for arm in ARMS[family]:
        if arm.name == name:
            return arm
    raise ValueError(f"--arms names {name!r}, which is not an arm of the {family} family; choose from: {', '.join(arm.name for arm in ARMS[family])}")


@dataclass(frozen=True)
class Settings:
    """Everything one run shares across its attempts."""

    foe: Path
    codex: Path | None
    family: str
    attempts: int
    route: str
    base_url: str | None
    model: str
    effort: str
    out: Path
    credential: Path | None
    codex_wire_api: str
    grader_timeout: int
    source_root: Path
    # Absolute paths every document arm may read and execute, from --tool-root.
    tool_roots: tuple[str, ...] = ()
    # The budget keys `--budget` replaces for every attempt of the run.
    budget_overrides: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_roots": list(self.tool_roots),
            "budget_overrides": dict(self.budget_overrides),
            "foe": str(self.foe),
            "codex": None if self.codex is None else str(self.codex),
            "family": self.family,
            "attempts": self.attempts,
            "route": self.route,
            "base_url": self.base_url,
            "model": self.model,
            "effort": self.effort,
            "out": str(self.out),
            "credential": None if self.credential is None else str(self.credential),
            "codex_wire_api": self.codex_wire_api,
            "grader_timeout": self.grader_timeout,
            "source_root": str(self.source_root),
        }


@dataclass(frozen=True)
class Selected:
    """One task directory and the task it declares."""

    directory: Path
    task: protocol.Task


def discover_tasks(tasks_dir: Path, family: str, names: list[str] | None) -> list[Selected]:
    """The task directories under `tasks_dir` of the family, or the named ones; errors name the path or the name."""
    if not tasks_dir.is_dir():
        raise FileNotFoundError(f"--tasks {tasks_dir} is not a directory")
    found: dict[str, Selected] = {}
    for directory in sorted(path for path in tasks_dir.iterdir() if path.is_dir() and (path / protocol.TASK_FILE).is_file()):
        task = protocol.load(directory)
        if task.name in found:
            raise ValueError(f"{directory} and {found[task.name].directory} both declare the task {task.name!r}")
        found[task.name] = Selected(directory, task)
    if names:
        selected = []
        for name in names:
            if name not in found:
                raise ValueError(f"--task {name!r} is not a task under {tasks_dir}; found: {', '.join(sorted(found)) or 'none'}")
            if found[name].task.family != family:
                raise ValueError(f"--task {name!r} belongs to the {found[name].task.family} family rather than {family}")
            selected.append(found[name])
        return selected
    return [entry for entry in found.values() if entry.task.family == family]


def parse_budget(values: Sequence[str] | None) -> dict[str, int]:
    """The budget overrides `--budget KEY=VALUE` arguments name; every error names the argument."""
    overrides: dict[str, int] = {}
    for text in values or []:
        key, separator, value = text.partition("=")
        key = key.strip()
        if not separator or not key:
            raise ValueError(f"--budget {text!r} is not of the form KEY=VALUE; the keys are {', '.join(protocol.BUDGET_KEYS)}")
        if key not in protocol.BUDGET_KEYS:
            raise ValueError(f"--budget names {key!r}, which is not a budget key; the keys are {', '.join(protocol.BUDGET_KEYS)}")
        if key in overrides:
            raise ValueError(f"--budget names {key} twice")
        try:
            number = int(value.strip())
        except ValueError:
            raise ValueError(f"--budget {key}={value!r} is not an integer") from None
        if number <= 0:
            raise ValueError(f"--budget {key}={number} is not a positive integer")
        overrides[key] = number
    return overrides


def effective_budget(settings: Settings, task: protocol.Task) -> dict[str, int]:
    """The task's budget with the run's `--budget` overrides applied, over every key of `protocol.BUDGET_KEYS`."""
    return {key: settings.budget_overrides.get(key, task.budget[key]) for key in protocol.BUDGET_KEYS}


def codex_limits(budget: dict[str, int]) -> dict[str, int]:
    """The limits the Codex budget watcher enforces for a budget: both token ceilings and the seconds ceiling.

    The seconds ceiling is present on every Codex attempt, so no attempt
    runs unbounded; Codex has no model-call ceiling, which the module
    docstring states.
    """
    if "seconds" not in budget or budget["seconds"] <= 0:
        raise ValueError(f"the budget {budget!r} lacks a positive seconds ceiling; every Codex attempt needs one")
    return {"input_tokens": budget["input_tokens"], "output_tokens": budget["output_tokens"], "seconds": budget["seconds"]}


def not_applicable(arm: Arm, task: protocol.Task) -> str | None:
    """The reason the arm cannot run the task, or None when it can.

    A built-in document carries its own grants, so a task whose metadata
    names `tool_roots` has no way to grant them under the foe-as-shipped
    arm; the attempt is recorded rather than run.
    """
    named = task.metadata.get(METADATA_TOOL_ROOTS)
    if arm.kind == "builtin" and named:
        return f"the {arm.name} arm runs the built-in document {arm.variant}, whose grants cannot take the tool roots task {task.name!r} names under metadata.{METADATA_TOOL_ROOTS}: {', '.join(map(str, named))}"
    return None


def rotated(arms: list[Arm], attempt: int) -> list[Arm]:
    """The arms in the order attempt number `attempt` runs them: each attempt starts one arm later than the previous."""
    offset = (attempt - 1) % len(arms)
    return arms[offset:] + arms[:offset]


def planned(tasks: list[Selected], arms: list[Arm], attempts: int) -> list[tuple[int, Selected, Arm]]:
    """Every (attempt, task, arm) triple in launch order."""
    return [(attempt, entry, arm) for attempt in range(1, attempts + 1) for entry in tasks for arm in rotated(arms, attempt)]


def plan(settings: Settings, tasks: list[Selected], arms: list[Arm]) -> str:
    """State every attempt and the largest spend the run can incur, before any model is called."""
    triples = planned(tasks, arms, settings.attempts)
    attempt_word = "attempt" if settings.attempts == 1 else "attempts"
    task_word = "task" if len(tasks) == 1 else "tasks"
    arm_word = "arm" if len(arms) == 1 else "arms"
    lines = [
        f"This evaluation calls {settings.model} over the {settings.route} route and spends real credit.",
        f"Largest spend it can incur, at {settings.attempts} {attempt_word} of each of {len(tasks)} {task_word} under {len(arms)} {arm_word}:",
        "",
        f"  {'model calls':>11}  {'input':>9}  {'output':>8}  {'seconds':>7}  attempt",
    ]
    totals = {key: 0 for key in protocol.BUDGET_KEYS}
    skipped: list[str] = []
    for attempt, entry, arm in triples:
        reason = not_applicable(arm, entry.task)
        if reason is not None:
            skipped.append(f"  {entry.task.name} / {arm.name} / {attempt}: {reason}")
            continue
        budget = effective_budget(settings, entry.task)
        for key in protocol.BUDGET_KEYS:
            totals[key] += budget[key]
        lines.append(
            f"  {budget['model_calls']:>11}  {budget['input_tokens']:>9,}  {budget['output_tokens']:>8,}  {budget['seconds']:>7}  "
            f"{entry.task.name} / {arm.name} / {attempt}"
        )
    lines.append(f"  {totals['model_calls']:>11}  {totals['input_tokens']:>9,}  {totals['output_tokens']:>8,}  {totals['seconds']:>7}  every planned attempt")
    if settings.budget_overrides:
        lines.append("")
        lines.append("The ceilings above are the effective ones: --budget replaces " + ", ".join(f"{key}={value}" for key, value in settings.budget_overrides.items()) + " in every task's budget.")
    if skipped:
        lines.append("")
        lines.append("Recorded as not applicable and never launched:")
        lines.extend(skipped)
    lines.extend(
        [
            "",
            "A foe document arm declares every ceiling in its document, and the runtime enforces model_calls",
            "inside the episode. A foe-as-shipped arm runs under the built-in document's own model-call",
            "ceiling and no token ceiling; the seconds ceiling is the runner's cap. A Codex arm has its token",
            "and seconds ceilings enforced by the budget watcher on every attempt and no model-call ceiling,",
            "so the token and seconds ceilings are the shared bound and model calls are reported per arm.",
            "",
        ]
    )
    if any(arm.kind == "document" for arm in arms):
        lines.append("A foe document arm writes the whole workspace, or the roots a task names under metadata.write_roots. It executes")
        lines.append("the workspace, so that a check suite can run the binaries its build wrote, and it reads and executes:")
        for entry in tasks:
            lines.append(f"  {entry.task.name}: {', '.join(tool_roots(settings, entry.task))}")
        lines.extend(
            [
                "A check suite that needs a command outside those roots cannot run under foe, and the attempt",
                "is then recorded as a fault. Add --tool-root for each installation such a command lives in.",
                "",
            ]
        )
    lines.append("No attempt was launched. Add --confirm-spend to launch them.")
    return "\n".join(lines)


def check_command(task: protocol.Task, workspace: Path) -> list[str]:
    """The command the check tool runs from the workspace.

    In order: the command the task metadata names under `check`, the
    workspace's `checks/run.sh`, and the unit-test discovery a workspace
    with a `tests` directory and no check suite runs. A workspace with none
    of the three is refused by name.
    """
    named = task.metadata.get(METADATA_CHECK)
    if named is not None:
        if not isinstance(named, str) or not named.strip():
            raise ValueError(f"task {task.name!r}: metadata.{METADATA_CHECK} is {named!r}; expected a shell command string")
        return ["/usr/bin/bash", "-c", named]
    if (workspace / CHECK_SUITE).is_file():
        return ["/usr/bin/bash", CHECK_SUITE]
    if (workspace / TESTS_DIR).is_dir():
        return ["/usr/bin/python3", "-B", "-m", "unittest", "discover", "-s", TESTS_DIR, "-t", "."]
    raise ValueError(f"task {task.name!r}: the workspace {workspace} has neither {CHECK_SUITE} nor a {TESTS_DIR} directory, and metadata names no {METADATA_CHECK}")


def write_check_script(path: Path, workspace: Path, command: list[str], search_path: Sequence[str] = ()) -> Path:
    """An executable that runs the check command from the workspace and prints findings.

    The tool definition in contracts/graphs.py states that the check prints
    one finding per line, nothing when every check passes, and exits zero
    either way. The script turns a failing command's output and exit status
    into findings, so that a check suite of any convention fits. The runtime
    starts a configured executable with an empty environment, so the script
    sets its own. The search path is the system directories of docs/tools.md
    "bash" followed by every directory of `search_path`, and the language is
    the one a bash command receives. TMPDIR is CHECK_SCRATCH_DIR under the
    workspace, created with a cache tag so that the workspace snapshot
    leaves it out. A command the shell cannot find ends the suite with
    status 127, and the script then prints a line starting with
    CHECK_SUITE_UNAVAILABLE, which the runner reads back from the episode
    log.
    """
    system = SYSTEM_SEARCH_PATH.split(":")
    directories = [str(root) for root in search_path if str(root) not in system and Path(root).is_dir()]
    joined = shlex.join(command)
    scratch = shlex.quote(str(workspace / CHECK_SCRATCH_DIR))
    text = "\n".join(
        [
            "#!/bin/sh",
            "# The check tool of a cross-harness foe document: the task's check suite, run from the workspace.",
            f"PATH={shlex.quote(':'.join([SYSTEM_SEARCH_PATH, *directories]))}",
            "LANG=C.UTF-8",
            f"TMPDIR={scratch}",
            "export PATH LANG TMPDIR",
            f"cd {shlex.quote(str(workspace))} || {{ echo {shlex.quote(f'the workspace {workspace} cannot be entered')}; exit 0; }}",
            f"mkdir -p {scratch} && printf '%s\\n' {shlex.quote(CACHE_TAG_SIGNATURE)} > {scratch}/{CACHE_TAG_FILE} || {{ echo {shlex.quote(f'the scratch directory {workspace / CHECK_SCRATCH_DIR} cannot be created')}; exit 0; }}",
            f"output=$({joined} 2>&1)",
            "status=$?",
            f'if [ "$status" -eq {COMMAND_NOT_FOUND_STATUS} ]; then',
            "  printf '%s\\n' \"$output\" | tail -n 40",
            f"  echo {shlex.quote(f'{CHECK_SUITE_UNAVAILABLE}: {joined} exited {COMMAND_NOT_FOUND_STATUS}, so a command it names is absent from the search path')} \"$PATH\"",
            'elif [ "$status" -ne 0 ]; then',
            "  printf '%s\\n' \"$output\" | tail -n 40",
            f"  echo {shlex.quote(f'the check suite {joined} exited')} \"$status\"",
            "fi",
            "exit 0",
            "",
        ]
    )
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def write_roots(task: protocol.Task, workspace: Path) -> tuple[bool, list[str]]:
    """Whether the document writes the whole workspace, and otherwise the roots under it.

    The task metadata's `write_roots` narrows the grant when present. A
    task without it is written as a whole, because a check suite writes
    wherever its build writes and the Codex arms write the whole workspace.
    """
    named = task.metadata.get(METADATA_WRITE_ROOTS)
    if named is not None:
        if not isinstance(named, list) or not named or not all(isinstance(root, str) and root for root in named):
            raise ValueError(f"task {task.name!r}: metadata.{METADATA_WRITE_ROOTS} is {named!r}; expected a non-empty list of workspace-relative directories")
        for root in named:
            if not (workspace / root).is_dir():
                raise ValueError(f"task {task.name!r}: metadata.{METADATA_WRITE_ROOTS} names {root!r}, which is not a directory under {workspace}")
        return False, list(named)
    return True, []


def tool_roots(settings: Settings, task: protocol.Task) -> list[str]:
    """The tool roots of a document arm: the system roots, the run's `--tool-root` paths, and the task's `tool_roots`."""
    named = task.metadata.get(METADATA_TOOL_ROOTS)
    if named is None:
        named = []
    elif not isinstance(named, list) or not all(isinstance(root, str) and root for root in named):
        raise ValueError(f"task {task.name!r}: metadata.{METADATA_TOOL_ROOTS} is {named!r}; expected a list of absolute paths")
    roots: list[str] = []
    for root in (*graphs.EXECUTE_ROOTS, *settings.tool_roots, *named):
        if not Path(root).is_absolute():
            raise ValueError(f"task {task.name!r}: metadata.{METADATA_TOOL_ROOTS} names {root!r}, which is not an absolute path")
        if root in named and not Path(root).exists():
            raise ValueError(f"task {task.name!r}: metadata.{METADATA_TOOL_ROOTS} names {root!r}, which does not exist")
        if root not in roots:
            roots.append(root)
    return roots


def with_placeholder(document: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """The document with every mention of the workspace path replaced by the foe arm's placeholder.

    The foe arm inserts the workspace at the head of `grants.write` when a
    document names no placeholder, which would widen a write grant that
    names roots under the workspace to the whole workspace. Naming the
    workspace through the placeholder keeps the grants as generated.
    """
    prefix = str(workspace)
    count = 0

    def replace(value: Any) -> Any:
        nonlocal count
        if isinstance(value, str):
            if prefix in value:
                count += 1
                return value.replace(prefix, foe_arm.WORKSPACE_PLACEHOLDER)
            return value
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    replaced = replace(document)
    if count == 0:
        raise ValueError(f"the document {document.get('name')!r} names the workspace {prefix} nowhere; the grants cannot be placed")
    return replaced


def with_read_roots(document: dict[str, Any], roots: Sequence[str]) -> dict[str, Any]:
    """The document with `roots` appended to the read grant of every contract in it, the root and every node and child."""
    extra = [root for root in roots if root not in graphs.EXECUTE_ROOTS]

    def widen(value: Any) -> Any:
        if isinstance(value, dict):
            widened = {key: widen(item) for key, item in value.items()}
            grants = widened.get("grants")
            if isinstance(grants, dict) and isinstance(grants.get("read"), list):
                grants["read"] = [*grants["read"], *[root for root in extra if root not in grants["read"]]]
            return widened
        if isinstance(value, list):
            return [widen(item) for item in value]
        return value

    return widen(document)


def foe_document(arm: Arm, task: protocol.Task, workspace: Path, check: Path, tools: Sequence[str] = graphs.EXECUTE_ROOTS, budget: dict[str, int] | None = None) -> dict[str, Any]:
    """The generated document a document arm runs, with the budget, the write roots, and the tool roots.

    `budget` is the effective budget of the attempt; the task's own budget
    when None.
    """
    root_files, roots = write_roots(task, workspace)
    budget = {key: (task.budget if budget is None else budget)[key] for key in protocol.BUDGET_KEYS}
    # The workspace is executable because a check suite runs the build scripts and test binaries its build wrote there.
    execute = [*tools, str(workspace)]
    if task.family == "autonomy":
        document = graphs.autonomy(workspace, check, budget, ablated=arm.variant == "ablated", root_files=root_files, write_roots=roots or graphs.WRITE_ROOTS, execute=execute)
    else:
        document = graphs.teams(
            workspace, check, budget, variant=arm.variant, max_concurrent=TEAM_CONCURRENCY, root_files=root_files, write_roots=roots or graphs.WRITE_ROOTS, execute=execute
        )
    return with_placeholder(with_read_roots(document, tools), workspace)


def foe_route(settings: Settings) -> foe_arm.ModelRoute:
    provider = FOE_PROVIDERS[settings.route]
    if settings.route == "compatible":
        if not settings.base_url:
            raise ValueError("--route compatible needs --base-url; docs/models.md requires base_url for compatible-http")
        return foe_arm.ModelRoute(provider, settings.model, settings.base_url)
    return foe_arm.ModelRoute(provider, settings.model)


def codex_task_text(arm: Arm, task: protocol.Task) -> str:
    """The prompt a Codex arm receives: the task text, with the four phases appended for the equivalent arm."""
    if arm.variant == "equivalent":
        return task.text + "\n\n" + PHASES
    return task.text


def codex_providers(settings: Settings) -> dict[str, dict[str, Any]] | None:
    """The `model_providers` override a compatible route needs, and None on the subscription route."""
    if settings.route != "compatible":
        return None
    if not settings.base_url:
        raise ValueError("--route compatible needs --base-url; the Codex provider override carries it as base_url")
    return {CODEX_COMPATIBLE_PROVIDER: {"name": CODEX_COMPATIBLE_PROVIDER, "base_url": settings.base_url, "wire_api": settings.codex_wire_api}}


def snapshot(workspace: Path) -> dict[str, int]:
    """Every regular file under the workspace, by absolute path, with its modification time in milliseconds.

    A directory that holds CACHE_TAG_FILE, such as a cargo target
    directory, and a directory named in SNAPSHOT_IGNORED_DIRECTORIES are
    left out with everything below them, so that build output is never
    attributed to an agent as a file change.
    """
    found: dict[str, int] = {}
    for directory, subdirectories, filenames in os.walk(workspace):
        if CACHE_TAG_FILE in filenames:
            subdirectories[:] = []
            continue
        subdirectories[:] = [name for name in subdirectories if name not in SNAPSHOT_IGNORED_DIRECTORIES]
        for name in filenames:
            path = Path(directory) / name
            try:
                status = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISREG(status.st_mode):
                found[str(path)] = status.st_mtime_ns // 1_000_000
    return found


def builtin_command_line(binary: Path, task: str, document: str, log_dir: Path, route: foe_arm.ModelRoute) -> list[str]:
    """The running form of docs/design.md "The command line" for a document the binary carries."""
    return [str(binary), task, "--config", document, "--log-dir", str(log_dir), "--viewer", "off", "--model", f"{route.provider}/{route.model}"]


def run_builtin(arm: Arm, binary: Path, task: protocol.Task, workspace: Path, log_dir: Path, artifacts: Path, route: foe_arm.ModelRoute, seconds: int) -> ArmResult:
    """Run the task under a built-in document, from the workspace, and return what it reported.

    A built-in document grants the working directory, so the run starts in
    the workspace. It carries its own budget and reasoning effort; the
    runner's cap on seconds, `seconds` plus the foe arm's margin, is the
    only ceiling the runner adds. The running form takes a provider and a
    model and no base URL, so on the compatible route the URL comes from
    the model file `foe login compatible-http` wrote. Everything else
    follows `foe_arm.run`.
    """
    if not os.access(binary, os.X_OK):
        raise FileNotFoundError(f"foe binary {binary} is not an executable file")
    if not workspace.is_dir():
        raise FileNotFoundError(f"workspace {workspace} is not a directory")
    artifacts.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    command = builtin_command_line(binary, task.text, arm.variant, log_dir, route)
    timeout = seconds + foe_arm.TIMEOUT_MARGIN_SECONDS
    started_ms = foe_arm.now_ms()
    process = subprocess.Popen(command, cwd=workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    killed = False
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        killed = True
        foe_arm.terminate_group(process)
        out, err = process.communicate()
    ended_ms = foe_arm.now_ms()
    stdout, stderr = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    (artifacts / foe_arm.STDOUT_NAME).write_text(stdout, encoding="utf-8")
    (artifacts / foe_arm.STDERR_NAME).write_text(stderr, encoding="utf-8")
    exit_status = None if killed else process.returncode
    episode = foe_build.announced_log_dir(stderr, log_dir)
    outcome = foe_arm.outcome_line(stdout)
    result, candidate = foe_arm.interpret(outcome, killed, exit_status, stderr, timeout)
    record = {
        "harness": foe_arm.HARNESS,
        "commands": [command],
        "cwd": str(workspace),
        "codex_home": None,
        "config": arm.variant,
        "log_dir": str(log_dir),
        "episode_dir": str(episode),
        "episode_log_present": (episode / normalize_foe.LOG_NAME).is_file(),
        "stdout": str(artifacts / foe_arm.STDOUT_NAME),
        "stderr": str(artifacts / foe_arm.STDERR_NAME),
        "model": {"provider": route.provider, "model": route.model},
        "reasoning_effort_applied": False,
        "workspace_placement": "working-directory",
        "cap_seconds": seconds,
        "timeout_seconds": timeout,
        "killed": killed,
        "exit_status": exit_status,
        "outcome": outcome,
    }
    return ArmResult(arm.name, foe_arm.HARNESS, started_ms, ended_ms, exit_status, result, candidate, artifacts, record)


def run_arm(settings: Settings, arm: Arm, task: protocol.Task, workspace: Path, attempt_dir: Path) -> ArmResult:
    """Run one arm over a materialized workspace; the artifacts, logs, and check script live under `attempt_dir`."""
    artifacts = attempt_dir / "artifacts"
    budget = effective_budget(settings, task)
    seconds = budget["seconds"]
    if arm.kind == "document":
        tools = tool_roots(settings, task)
        check = write_check_script(attempt_dir / CHECK_SCRIPT_NAME, workspace, check_command(task, workspace), tools)
        document = foe_document(arm, task, workspace, check, tools, budget)
        spec = foe_arm.FoeSpec(arm.name, settings.foe, document, task.text, workspace, attempt_dir / "log", artifacts, foe_route(settings), seconds, settings.effort)
        return foe_arm.run(spec)
    if arm.kind == "builtin":
        return run_builtin(arm, settings.foe, task, workspace, attempt_dir / "log", artifacts, foe_route(settings), seconds)
    if settings.codex is None or settings.credential is None:
        raise ValueError(f"arm {arm.name} needs --codex and --credential")
    spec = codex_arm.CodexSpec(
        arm_name=arm.name,
        codex=settings.codex,
        task=codex_task_text(arm, task),
        workspace=workspace,
        artifacts=artifacts,
        sandbox=CODEX_SANDBOX,
        model=settings.model,
        reasoning_effort=settings.effort,
        credential_source=settings.credential,
        limits=codex_limits(budget),
        agents_enabled=arm.variant == "multi",
        max_threads=TEAM_CONCURRENCY if arm.variant == "multi" else None,
        model_providers=codex_providers(settings),
    )
    return codex_arm.run(spec)


def protocol_reported(reported: dict[str, Any]) -> protocol.Reported:
    """The arm's reported outcome in the grader's form.

    The grader's form admits a code only with a blocked status, so the limit
    an exhausted run names as its code moves into the evidence.
    """
    status = str(reported["status"])
    code = reported.get("code")
    evidence = [str(line) for line in reported.get("evidence") or []]
    if code is not None and status != protocol.BLOCKED:
        evidence.insert(0, f"{status}: {code}")
        code = None
    return protocol.Reported(status, None if code is None else str(code), "\n".join(evidence))


def normalize_result(settings: Settings, result: ArmResult) -> tuple[trajectory.Trajectory | None, dict[str, Any] | None, str | None]:
    """The trajectory of a finished arm, the foe trace-conformance report, and the fault that stopped either.

    A foe run without an episode log and a Codex run without a session file
    measured nothing, which is the fault the third value names. A log whose
    model calls all lack a response is the same kind of fault.
    """
    record = result.record
    conformance = None
    try:
        if result.harness == foe_arm.HARNESS:
            episode = Path(record["episode_dir"])
            if not record["episode_log_present"]:
                tail = foe_arm.stderr_tail(Path(record["stderr"]).read_text(encoding="utf-8")) or f"exit status {result.exit_status}"
                return None, None, f"foe wrote no episode log under {episode}: {tail}"
            trajectory_ = normalize_foe.normalize(episode, settings.route)
            try:
                conformance = normalize_foe.trace_conformance(episode)
            except RuntimeError as exc:
                conformance = {"valid": None, "error": str(exc)}
        else:
            last = Path(record["last_message"])
            # The dimension the budget watcher crossed is the code of the
            # trajectory's exhausted outcome, as it is of the arm's report.
            stop = record.get("stop")
            limit = str(stop["dimension"]) if isinstance(stop, dict) and stop.get("dimension") is not None else None
            trajectory_ = normalize_codex.normalize(Path(record["codex_home"]), Path(record["events"]), last if last.is_file() else None, result.exit_status, settings.route, limit)
    except (ValueError, OSError, LookupError, AttributeError, TypeError) as exc:
        # A record of an unexpected shape is a fault of this attempt alone; the later attempts still run.
        return None, None, f"the {result.harness} records could not be reduced to a trajectory: {type(exc).__name__}: {exc}"
    answered = sum(1 for agent in trajectory_.agents for call in agent.model_calls if call.ended_ms is not None)
    if answered == 0:
        evidence = result.reported["evidence"]
        return trajectory_, conformance, f"no model response reached the {result.harness} run: {evidence[0] if evidence else 'the run reported no evidence'}"
    return trajectory_, conformance, None


def outcomes_side_by_side(reported: dict[str, Any], trajectory_: trajectory.Trajectory | None) -> dict[str, Any]:
    """The arm's reported outcome and the trajectory's outcome, as status and code each, and whether the two agree.

    `agree` is None while the run has no trajectory.
    """
    arm = {"status": reported["status"], "code": reported.get("code")}
    if trajectory_ is None:
        return {"arm": arm, "trajectory": None, "agree": None}
    reduced = {"status": trajectory_.outcome.status, "code": trajectory_.outcome.code}
    return {"arm": arm, "trajectory": reduced, "agree": arm == reduced}


def check_suite_fault(episode: Path) -> str | None:
    """The first check result of the episode or a child stating that the check suite could not run, or None.

    The check script prints a line starting with CHECK_SUITE_UNAVAILABLE
    when the suite exited with the status a shell gives a command it cannot
    find. An attempt whose verifier could not run measured no harness, so
    that line is the attempt's fault.
    """
    for log in sorted(episode.rglob(normalize_foe.LOG_NAME)):
        try:
            events = normalize_foe.read_events(log)
        except ValueError:
            continue
        for event in events:
            data = event.get("data") if isinstance(event, dict) else None
            if event.get("type") != "tool/result" or not isinstance(data, dict) or data.get("name") != CHECK_SCRIPT_NAME or data.get("is_error"):
                continue
            value = data.get("value")
            stdout = value.get("stdout") if isinstance(value, dict) else None
            if not isinstance(stdout, str):
                continue
            for line in stdout.splitlines():
                if line.startswith(CHECK_SUITE_UNAVAILABLE):
                    return f"{line} ({log} seq {event.get('seq')})"
    return None


def run_attempt(settings: Settings, provenance: dict[str, Any], entry: Selected, arm: Arm, attempt: int) -> dict[str, Any]:
    """Materialize, run, normalize, grade, and classify one attempt, and return its record."""
    task = entry.task
    attempt_dir = attempt_path(settings.out, task.name, arm.name, attempt)
    root = attempt_dir / "root"
    workspace = root / protocol.WORKSPACE
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "task": task.to_dict(),
        "task_dir": str(entry.directory),
        "arm": arm.name,
        "harness": arm.harness,
        "attempt": attempt,
        "provenance": provenance,
        "budget": effective_budget(settings, task),
        "budget_overrides": dict(settings.budget_overrides),
        "tool_roots": tool_roots(settings, task),
        "not_applicable": not_applicable(arm, task),
        "paths": {"attempt_dir": str(attempt_dir), "root": str(root), "workspace": str(workspace)},
        "started_ms": foe_arm.now_ms(),
        "ended_ms": None,
        "arm_result": None,
        "reported": None,
        "candidate": None,
        "trajectory": None,
        "outcomes": None,
        "totals": None,
        "shell_writes_attributed": 0,
        "conformance": None,
        "grade": None,
        "classification": None,
        "infrastructure_error": None,
    }
    if record["not_applicable"] is not None:
        # The attempt is recorded and never launched; it enters no rate.
        record["ended_ms"] = foe_arm.now_ms()
        return record
    try:
        attempt_dir.mkdir(parents=True)
        protocol.materialize(entry.directory, root)
    except (OSError, ValueError) as exc:
        record["infrastructure_error"] = f"the task did not materialize: {exc}"
        record["ended_ms"] = foe_arm.now_ms()
        return record
    before = snapshot(workspace)
    try:
        result = run_arm(settings, arm, task, workspace, attempt_dir)
    except (OSError, ValueError) as exc:
        record["infrastructure_error"] = f"the arm could not launch: {exc}"
    else:
        after = snapshot(workspace)
        record["arm_result"] = result.to_dict()
        record["reported"] = dict(result.reported)
        record["candidate"] = result.candidate
        trajectory_, conformance, fault = normalize_result(settings, result)
        if fault is None and arm.kind == "document":
            fault = check_suite_fault(Path(result.record["episode_dir"]))
        record["conformance"] = conformance
        record["infrastructure_error"] = fault
        record["outcomes"] = outcomes_side_by_side(result.reported, trajectory_)
        if trajectory_ is not None:
            record["shell_writes_attributed"] = trajectory.attribute_shell_writes(trajectory_, before, after)
            record["trajectory"] = trajectory_.to_dict()
            record["totals"] = trajectory_.totals()
    reported = protocol_reported(record["reported"]) if record["reported"] else protocol.Reported(protocol.FAILED, None, record["infrastructure_error"] or "")
    graded = feature_removal.grade_with_timeout(root, reported, record["candidate"], arm.name, settings.grader_timeout)
    record["grade"] = {"passed": graded.passed, "findings": list(graded.findings), "damage": list(graded.damage)}
    if record["infrastructure_error"] is None:
        record["classification"] = protocol.classify(task, reported, graded)
    record["ended_ms"] = foe_arm.now_ms()
    return record


def record_path(out: Path, task_name: str, arm_name: str, attempt: int) -> Path:
    return out / RECORDS_DIR / task_name / arm_name / f"{attempt:02d}.json"


def attempt_path(out: Path, task_name: str, arm_name: str, attempt: int) -> Path:
    return out / ATTEMPTS_DIR / task_name / arm_name / f"{attempt:02d}"


def run_file_path(out: Path) -> Path:
    """The run file this run writes: `run.json`, or the next free `run-NN.json` when earlier runs wrote into `out`."""
    first = out / RUN_FILE
    if not first.exists():
        return first
    stem, suffix = first.stem, first.suffix
    number = 2
    while (out / f"{stem}-{number:02d}{suffix}").exists():
        number += 1
    return out / f"{stem}-{number:02d}{suffix}"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def git_output(root: Path, *args: str) -> str | None:
    """The standard output of a git command run in `root`, untrimmed, or None when git fails or is absent."""
    try:
        completed = subprocess.run(["/usr/bin/git", "-C", str(root), *args], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def foe_provenance(source_root: Path, binary: Path) -> dict[str, Any]:
    """The binary digest and the state of the source tree it was built from.

    `source_tree` is the git tree object of a clean checkout, and None
    otherwise. `git_head` is the commit the checkout is at and `dirty`
    whether its tracked or untracked state differs from that commit, with
    the differing paths under `changed_paths`; both are None when the
    source root is not inside a git checkout. A record from a dirty tree
    therefore still names the commit and the paths that changed.
    `source_tree_error` states why the clean tree could not be identified.
    """
    digest = foe_build.sha256_file(binary)
    root = source_root.resolve()
    if root.is_file():
        root = root.parent
    head = git_output(root, "rev-parse", "HEAD")
    head = head.strip() or None if head is not None else None
    status = git_output(root, "status", "--porcelain=v1", "--untracked-files=all") if head is not None else None
    # A porcelain line is two status characters, a space, and the path; the
    # status characters of a modified tracked file start with a space. A
    # status that could not be read leaves the tree's state unknown rather
    # than clean.
    changed = [line[3:] for line in status.splitlines() if len(line) > 3] if status else []
    dirty = None if head is None or status is None else bool(changed)
    try:
        source_tree, error = foe_build.evaluated_foe(source_root, binary)["source_tree"], None
    except ValueError as exc:
        source_tree, error = None, str(exc)
    if head is not None and status is None:
        error = f"git status failed in {root}, so whether the tree is dirty is unknown" + (f"; {error}" if error else "")
    return {"source_tree": source_tree, "runtime_binary": digest, "git_head": head, "dirty": dirty, "changed_paths": changed, "source_tree_error": error}


def codex_version(codex: Path) -> str | None:
    try:
        completed = subprocess.run([str(codex), "--version"], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() or None


def provenance_of(settings: Settings, needs_codex: bool) -> dict[str, Any]:
    return {
        "foe": foe_provenance(settings.source_root, settings.foe),
        "foe_binary": str(settings.foe),
        "codex": None if settings.codex is None else str(settings.codex),
        "codex_version": codex_version(settings.codex) if needs_codex and settings.codex is not None else None,
        "route": settings.route,
        "base_url": settings.base_url,
        "model": settings.model,
        "reasoning_effort": settings.effort,
        "recorded_at_ms": int(time.time() * 1000),
    }


def parse_arms(text: str | None, family: str) -> list[Arm]:
    if not text:
        return list(ARMS[family])
    names = [name.strip() for name in text.split(",") if name.strip()]
    if not names:
        raise ValueError(f"--arms {text!r} names no arm; choose from: {', '.join(arm.name for arm in ARMS[family])}")
    if len(set(names)) != len(names):
        raise ValueError("--arms names an arm twice")
    return [arm_by_name(family, name) for name in names]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog="Exit status 0 means every attempt evaluated the harness, 1 that at least one attempt hit a deployment fault, and 2 that nothing was launched.")
    parser.add_argument("--foe", required=True, type=Path, help="the foe binary")
    parser.add_argument("--codex", type=Path, default=None, help="the codex binary; needed by a Codex arm")
    parser.add_argument("--family", required=True, choices=protocol.FAMILIES, help="the task family")
    parser.add_argument("--tasks", required=True, type=Path, help="a directory holding task directories")
    parser.add_argument("--task", action="append", default=None, help="run only this task; may be repeated")
    parser.add_argument("--arms", default=None, help="comma-separated arm names; every arm of the family when omitted")
    parser.add_argument("--attempts", type=int, default=1, help="independent attempts per task and arm")
    parser.add_argument("--route", required=True, choices=ROUTES, help="how the model is reached")
    parser.add_argument("--base-url", default=None, help="the compatible server's base URL, ending in /v1")
    parser.add_argument("--model", required=True, help="the model name both harnesses request")
    parser.add_argument("--effort", default=DEFAULT_EFFORT, help=f"the reasoning effort both harnesses request; default {DEFAULT_EFFORT}")
    parser.add_argument("--out", required=True, type=Path, help="where attempts and records are written")
    parser.add_argument("--credential", type=Path, default=None, help="the Codex auth.json a login wrote; needed by a Codex arm")
    parser.add_argument("--codex-wire-api", default="chat", choices=CODEX_WIRE_APIS, help="the wire format the Codex compatible-route provider speaks")
    parser.add_argument("--grader-timeout", type=int, default=DEFAULT_GRADER_TIMEOUT_SECONDS, help="seconds one grade script may run")
    parser.add_argument("--source-root", type=Path, default=None, help="a path inside the foe checkout the binary was built from; the binary's own path when omitted")
    parser.add_argument(
        "--tool-root",
        action="append",
        type=Path,
        default=None,
        help="a tool installation every foe document arm may read and execute, such as a compiler's home; it also enters the check tool's search path; may be repeated",
    )
    parser.add_argument(
        "--budget",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help=f"replace one key of every task's budget for this run; the keys are {', '.join(protocol.BUDGET_KEYS)}; may be repeated",
    )
    parser.add_argument("--confirm-spend", action="store_true", help="launch the attempts; without it the plan is printed and nothing runs")
    args = parser.parse_args(argv)

    def refuse(message: str) -> int:
        print(f"cross harness: {message}", file=sys.stderr)
        return NOTHING_LAUNCHED

    if args.attempts < 1:
        return refuse("--attempts must be at least 1")
    if args.grader_timeout < 1:
        return refuse("--grader-timeout must be at least 1")
    foe = args.foe.resolve()
    if not os.access(foe, os.X_OK):
        return refuse(f"--foe {foe} is not an executable file")
    codex = None if args.codex is None else args.codex.resolve()
    if codex is not None and not os.access(codex, os.X_OK):
        return refuse(f"--codex {codex} is not an executable file")
    credential = None if args.credential is None else args.credential.resolve()
    if credential is not None and not credential.is_file():
        return refuse(f"--credential {credential} is not a file")
    tools: list[str] = []
    for root in args.tool_root or []:
        resolved = root.resolve()
        if not resolved.exists():
            return refuse(f"--tool-root {root} does not exist")
        tools.append(str(resolved))
    try:
        overrides = parse_budget(args.budget)
    except ValueError as exc:
        return refuse(str(exc))
    settings = Settings(
        foe=foe,
        codex=codex,
        family=args.family,
        attempts=args.attempts,
        route=args.route,
        base_url=args.base_url,
        model=args.model,
        effort=args.effort,
        out=args.out.resolve(),
        credential=credential,
        codex_wire_api=args.codex_wire_api,
        grader_timeout=args.grader_timeout,
        source_root=(args.source_root or args.foe).resolve(),
        tool_roots=tuple(tools),
        budget_overrides=overrides,
    )
    try:
        arms = parse_arms(args.arms, args.family)
        tasks = discover_tasks(args.tasks.resolve(), args.family, args.task)
        if args.route == "compatible":
            foe_route(settings)
        for entry in tasks:
            tool_roots(settings, entry.task)
            # The document builders refuse some budgets, such as a seconds
            # ceiling with no room for the check timeout; the refusal
            # belongs before the plan and before any attempt spends credit.
            try:
                graphs.check_budget(effective_budget(settings, entry.task))
            except ValueError as exc:
                raise ValueError(f"{entry.task.name}: the effective budget is refused: {exc}") from exc
    except (ValueError, FileNotFoundError) as exc:
        return refuse(str(exc))
    if not tasks:
        return refuse(f"no task of the {args.family} family under {args.tasks.resolve()}")
    needs_codex = any(arm.harness == "codex" for arm in arms)
    if needs_codex and (codex is None or credential is None):
        return refuse(f"the arms {', '.join(arm.name for arm in arms if arm.harness == 'codex')} need --codex and --credential")
    if not args.confirm_spend:
        print(plan(settings, tasks, arms))
        return NOTHING_LAUNCHED

    triples = planned(tasks, arms, settings.attempts)
    # An attempt directory without a record is what an interrupted run leaves; it is refused by name, since removing it is the user's decision.
    for attempt, entry, arm in triples:
        record = record_path(settings.out, entry.task.name, arm.name, attempt)
        if record.exists():
            return refuse(f"a record already exists: {record}")
        leftover = attempt_path(settings.out, entry.task.name, arm.name, attempt)
        if leftover.exists():
            return refuse(f"an attempt directory already exists without a record: {leftover}; remove it or choose another --out")
    settings.out.mkdir(parents=True, exist_ok=True)
    try:
        provenance = provenance_of(settings, needs_codex)
    except ValueError as exc:
        return refuse(str(exc))
    run_file = run_file_path(settings.out)
    write_json(
        run_file,
        {
            "schema_version": SCHEMA_VERSION,
            "settings": settings.to_dict(),
            "provenance": provenance,
            "arms": [arm.name for arm in arms],
            "tasks": [entry.task.name for entry in tasks],
            "budgets": {entry.task.name: effective_budget(settings, entry.task) for entry in tasks},
            "tool_roots": {entry.task.name: tool_roots(settings, entry.task) for entry in tasks},
            "plan": plan(settings, tasks, arms),
        },
    )

    faults = 0
    for attempt, entry, arm in triples:
        print(f"cross harness: attempt {attempt}, {entry.task.name}, {arm.name}", file=sys.stderr, flush=True)
        record = run_attempt(settings, provenance, entry, arm, attempt)
        write_json(record_path(settings.out, entry.task.name, arm.name, attempt), record)
        if record["not_applicable"] is not None:
            print(f"cross harness: {entry.task.name} under {arm.name} is not applicable: {record['not_applicable']}", file=sys.stderr, flush=True)
        elif record["infrastructure_error"] is not None:
            faults += 1
            print(f"cross harness: {entry.task.name} under {arm.name} did not evaluate the harness: {record['infrastructure_error']}", file=sys.stderr, flush=True)
        else:
            print(f"cross harness: {entry.task.name} under {arm.name}: {record['classification']}", file=sys.stderr, flush=True)
    print(json.dumps({"records": str(settings.out / RECORDS_DIR), "run": str(run_file), "attempts": len(triples), "infrastructure_failures": faults}))
    return DEPLOYMENT_FAULT if faults else EVALUATED


if __name__ == "__main__":
    sys.exit(main())
