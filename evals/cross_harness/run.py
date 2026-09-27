#!/usr/bin/python3
"""Run every selected task under every selected arm, grade each run, and record it.

The cross-harness evaluation compares foe with Codex CLI on tasks of two
families. An `autonomy` task goes to one agent and measures whether the
agent finishes a task that can be finished and stops on one that cannot. A
`teams` task goes to a team and measures the same under delegation. Each
family has its own arms, and an arm is one harness in one configuration:

    autonomy   foe-configured   the survey, implement, assess, repair graph of contracts/graphs.py
               foe-ablated      the same graph without the `block` tool and without verifiers
               foe-lean         the same graph without the survey node; the implementing node reads the workspace itself
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
which is what a foe outcome carries. A task whose grade reads a value of its
own records that value's shape under `metadata.returns`: the shape is
declared on the two nodes that can end the foe document's teams graph and
merged into the Codex arm's final-message schema, so both harnesses can
return what the grade needs.

Every run of one task under one arm is an attempt. The runner materializes
the task's workspace into a fresh root, runs the arm, and materializes the
task's grader into the same root once the arm process has exited. The
grader is absent while the arm runs because a Codex arm's sandbox reads the
whole filesystem, as the containment matrix records, so a grader beside the
workspace would give one harness the hidden tests, the oracle, and the
corruptions that the other harness's read grant keeps out of reach. The
protected-path hashes are recorded from the workspace as it stood before
the arm ran. Each record also carries `grader_paths_named`, every path
under the root's grader directory that the normalized trajectory names, so
that a reader can check the containment held even for a harness that
reached outside its sandbox by some other route. The runner reads the
workspace's modification times before and after the run, so that a file a
shell command wrote is attributed to the agent that ran the command. It
then reduces the harness's own records to the shared trajectory schema,
grades the workspace with the task's hidden grader, and classifies the
graded outcome into one confusion cell of `tasks/protocol.py`. One JSON
record per attempt is written under `<out>/records/<task>/<arm>/`, and
`report.py` reads them. The record's `grade` carries the grader's
verdict, its findings, and the damage judged beside it; for a fan-out
task of the teams family it also carries `units`, each unit's verdict as
`{name: passed}`, read from the `units.json` the grade script of
`tasks/teams.py` leaves in the materialized root's grader directory, and
null when no grade left one.
For every other task `units` is null.

The runner takes one run document, a JSON file, and launches nothing
without `--confirm-spend`:

    run.py DOCUMENT [--confirm-spend]

The document's keys follow. An unknown key and a value of the wrong type
are refused by name. A relative path resolves against the directory the
document is in, and a leading `~` expands to the home directory.

    tasks           a directory holding task directories; required
    select          the task names to run; every task under `tasks` when absent
    arms            the arm names to run; every arm of the family when absent
    attempts        independent attempts per task and arm; default 1
    resume          continue a run into an output directory that already holds
                    records; default false. With it true, an attempt whose record
                    exists is skipped, counted, and left out of the plan's spend,
                    and an attempt directory without a record, which an
                    interrupted run leaves, is renamed aside and named on standard
                    error before that attempt runs again, since it holds the only
                    transcript of an attempt that spent credit.
                    With it false both are refused by path and nothing is launched
    model           an object: `route` (subscription or compatible), `name`,
                    `effort` (default medium); on the compatible route
                    `base_url` (required there and refused on the subscription
                    route) and `codex_wire_api` (chat or responses, default chat)
    budget          an object whose keys, from `model_calls`, `input_tokens`,
                    `output_tokens`, and `seconds`, replace the same key of every
                    task's budget for every attempt of the run
    tool_roots      directories every foe document arm may read and execute
    harnesses       an object: `foe` (default `target/debug/foe` under the git
                    checkout holding the document, or the one holding the
                    current directory when the document is outside any),
                    `codex` (a path, or a bare command name looked up on PATH;
                    default `codex`), and `credential` (the auth.json a Codex
                    login wrote; default `~/.codex/auth.json`); only a Codex
                    arm needs `codex` and `credential`, and they are refused
                    as missing only when such an arm is selected
    out             where attempts and records are written; default
                    `~/.local/state/foe/cross-harness/<document file stem>`
    grader_timeout  seconds one grade script may run
    source_root     a path inside the foe checkout the binary was built from;
                    the binary's own path when absent
    foe_config_dir  foe's configuration directory, where the run plants its
                    foe canary; default `~/.config/foe`, the directory the
                    binary resolves for the real user

The family is the one the selected tasks declare in their `task.json`; a
selection spanning two families is refused naming both. The run file
records the resolved document under `document`, so a reader can verify the
run from the document alone.

A document arm grants the whole workspace for writing unless the task
metadata names `write_roots` under it, and for executing in every case,
because a check suite runs the build scripts and test binaries its build
wrote there. A tool root is a tree the episode may read, enumerate, and
execute: the system directories of `contracts/graphs.py`, every directory
of the run document's `tool_roots`, and every path the task metadata names
under `tool_roots`. Each enters the execute grant of every
contract in the document, and the ones beyond the system directories enter
the read grant as well, because a compiler enumerates its own installation.
The document's `check` tool runs the task's check suite with the tool roots
on its search path, since the runtime starts a configured executable with
an empty environment. A check suite that needs a command outside the roots
cannot run under foe; the runner reads that from the episode log and
records the attempt as a fault.

An attempt whose run never measured the harness is marked rather than
scored: the arm could not launch, the arm ran and its budget watcher then
failed, the harness wrote no log, no model response reached the run, the
harness's records could not be read, the check suite could not run, or the
provider failed the attempt. Such a
record carries `infrastructure_error` and no classification, following
`evals/run_micro_evals.py`; the arm result, the trajectory, and the grade
stay in the record, so the attempt is auditable.

A provider outage is a condition of the model service and says nothing
about a harness, so the runner records it as an infrastructure fault
before any classification. A foe episode reports it as blocked with the
code `recovery-exhausted`, which the model loop of
`crates/core/src/loop_.rs` reaches when a rate limit or a provider error
outlasts the retries the seconds budget can fund; the same code from a
workflow bound, which the evidence names as `max_fires` or
`recovery.max_interventions`, is the graph ending the episode and stays a
classified stop. A Codex run reports it as a failed status whose evidence
or whose recorded stream errors name a rate limit, a server fault, or an
authentication failure.

The built-in documents of the foe-as-shipped arm carry their own grants,
so that arm cannot take tool roots from the run document or from a task. A
task needs tool roots when the run document names `tool_roots` or the
task's metadata does. Such a task has its foe-as-shipped attempts recorded
as not applicable, with the reason, and no such attempt runs. The
container of `environment/environment.md` places the toolchain under
`/usr/local`, one of the built-in execute roots of `contracts/graphs.py`,
so inside the container a run document without `tool_roots` runs the
foe-as-shipped arm on every task. The record of a foe-as-shipped attempt
names the built-in execute roots under `tool_roots`, which are the roots
its document grants.

A task whose metadata names `presumes_absent`, as the missing-capability
task of `tasks/constructions.py` does, presumes that program absent from
every arm's search path, and its grader reads no search path. The runner
holds the premise: before the plan it resolves the program against the
system search path and the tool roots the foe arms execute, and against
the PATH a Codex child inherits when a Codex arm is selected, and refuses
the run by name when the program is found.

A task whose metadata names `presumes_unimportable`, as the
inventory-regeneration tasks do, presumes that module absent from the
interpreter a grade runs under, since the artifact the task asks for can
be regenerated only by a program that imports it. The runner holds that
premise the same way: before the plan it imports the module with
`protocol.PYTHON` and refuses the run by name, stating where the module
was found, when the import succeeds. A host that can import it grades the
task against a premise that does not hold, and a silent mis-grade is worse
than a refusal.

Every attempt runs under the task's budget: `model_calls`, `input_tokens`,
`output_tokens`, and `seconds`. Each key of the document's `budget`
replaces that key of every task's budget for every attempt of the run; the
run file and every record state the effective budget and the overrides. The two harnesses
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

Every run plants two canary sentences, each generated for the run from a
random identifier, and records them in the run file. The Codex arm writes
one into the fresh `CODEX_HOME` of every attempt as `config.toml` under
the `developer_instructions` key, the user configuration file that
`--ignore-user-config` states it does not load. The runner writes the
other into foe's configuration directory as `AGENTS.md`, a file foe never
reads by design. That directory is `~/.config/foe` of the real user: the
binary resolves it from the passwd database, and no argument or
environment value moves it, so the document key `foe_config_dir` names it
for a test that must leave the real directory untouched. The runner
passes nothing about the file to foe and removes it once the attempts
have ended. `gates/isolation.py` searches every recorded request for both
sentences after the run.

The runner calls a real model and spends real credit, so without
`--confirm-spend` it prints every value the document resolved to and every
planned attempt with the effective ceilings each one runs under, and exits
2 without launching anything.

Configuration reaches every child process as command-line arguments or as
documents. The one exception is `CODEX_HOME`, which the Codex arm sets on
its child because Codex locates its files by it; every record names the
directory it was given. This module reads the environment in two places
the document's rules call for: the PATH lookup of a bare `harnesses.codex`
command name, and the home directory a leading `~` expands to; and it
reads PATH once more, when a Codex arm is selected, to check that a
program a task presumes absent under `metadata.presumes_absent` is absent
from the search path the Codex child inherits. The same check resolves the
program against the system search path and the tool roots the foe arms
execute, and refuses the run by name when the program is found.
"""

from __future__ import annotations

import argparse
import json
import keyword
import os
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent
EVALS = HERE.parent
for directory in (EVALS, HERE, HERE / "arms", HERE / "contracts", HERE / "gates", HERE / "tasks"):
    sys.path.insert(0, str(directory))

import codex_arm  # noqa: E402
import feature_removal  # noqa: E402
import foe_arm  # noqa: E402
import foe_build  # noqa: E402
import graphs  # noqa: E402
import normalize_codex  # noqa: E402
import normalize_foe  # noqa: E402
import protocol  # noqa: E402
import teams  # noqa: E402
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

# The keys of a run document, of its `model` object, and of its `harnesses` object; the module docstring states each one.
DOCUMENT_KEYS: tuple[str, ...] = ("tasks", "select", "arms", "attempts", "resume", "model", "budget", "tool_roots", "harnesses", "out", "grader_timeout", "source_root", "foe_config_dir")
MODEL_KEYS: tuple[str, ...] = ("route", "name", "effort", "base_url", "codex_wire_api")
HARNESS_KEYS: tuple[str, ...] = ("foe", "codex", "credential")
# The foe binary a document without `harnesses.foe` runs, under the git checkout the document or the current directory is in.
DEFAULT_FOE = "target/debug/foe"
# The Codex command a document without `harnesses.codex` looks up on PATH.
DEFAULT_CODEX = "codex"
# The credential a document without `harnesses.credential` copies: the auth.json a Codex login writes.
DEFAULT_CREDENTIAL = "~/.codex/auth.json"
# A document without `out` writes under this directory, in a subdirectory named by the document's file stem.
DEFAULT_OUT_ROOT = "~/.local/state/foe/cross-harness"
# foe's configuration directory, from crates/transport/src/paths.rs: the
# binary resolves it under the real user's home and reads its credentials
# and default model file there, and nothing else.
DEFAULT_FOE_CONFIG_DIR = "~/.config/foe"
DEFAULT_CODEX_WIRE_API = "chat"
# The entry a git checkout's root holds: a directory, or the file a worktree keeps in its place.
GIT_ENTRY = ".git"

RECORDS_DIR, ATTEMPTS_DIR, RUN_FILE = "records", "attempts", "run.json"
# What is appended, with a number, to the directory of an attempt that
# was interrupted, when key resume sets that directory aside.
INTERRUPTED_SUFFIX = "-interrupted-"
# The two canaries of a run, by name: the sentence the Codex arm writes into
# each attempt's CODEX_HOME configuration file, and the sentence the runner
# writes into foe's configuration directory as a file foe never reads.
CODEX_CONFIG_CANARY, FOE_CONFIG_CANARY = "codex_config", "foe_config"
CANARY_NAMES: tuple[str, ...] = (CODEX_CONFIG_CANARY, FOE_CONFIG_CANARY)
CANARY_FILE = "AGENTS.md"
# Every canary sentence starts with this text, so the runner can tell its own file from one it must leave alone.
CANARY_PREFIX = "This sentence is the "
CHECK_SCRIPT_NAME = "check"
# The check command the runner takes from a workspace, in order of preference.
CHECK_SUITE = "checks/run.sh"
TESTS_DIR = "tests"
# Optional task metadata keys the runner reads.
METADATA_CHECK, METADATA_WRITE_ROOTS, METADATA_TOOL_ROOTS = "check", "write_roots", "tool_roots"
# The shape a task requires of the value its grade reads. A task that
# states one has it declared on the foe document's terminal nodes and
# merged into the Codex arm's final-message schema, so both harnesses can
# return the value the grade needs.
METADATA_RETURNS = "returns"
# The task metadata key naming the one program a missing-capability task presumes absent from every arm's search path.
METADATA_PRESUMES_ABSENT = "presumes_absent"
# The task metadata key naming the one module a missing-capability task presumes the grading interpreter cannot import.
METADATA_PRESUMES_UNIMPORTABLE = "presumes_unimportable"
# The search path the runtime gives a bash command, from docs/tools.md "bash";
# the check script starts from it because a configured executable receives no environment.
SYSTEM_SEARCH_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
# The exit status a shell gives a command it cannot find.
COMMAND_NOT_FOUND_STATUS = 127
# The foe blocked code a spent retry, an outage the seconds budget cannot
# outwait, and a workflow recovery bound all end an episode with, from
# crates/core/src/loop_.rs and crates/workflow/src/run.rs.
RECOVERY_EXHAUSTED = "recovery-exhausted"
# The two workflow bounds that reach that code; their messages name the
# bound, and a graph ending its own episode is harness behavior.
WORKFLOW_RECOVERY_BOUNDS: tuple[str, ...] = ("max_fires", "recovery.max_interventions")
# The provider conditions a failed Codex run names, each with the pattern
# its evidence or its recorded stream errors match, case folded.
PROVIDER_FAULT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("a rate limit", r"rate[ _-]?limit|\b429\b|too many requests|usage limit|quota"),
    ("a server fault", r"\b5(?:00|02|03|04)\b|server[ _-]?error|service unavailable|bad gateway|gateway timeout|overloaded"),
    ("an authentication failure", r"\b401\b|\b403\b|unauthorized|forbidden|authentication|invalid[ _-]?api[ _-]?key|not logged in"),
)
# A relative path that leaves its own directory and enters a directory
# named like the grader: a command may run in any subdirectory of the
# workspace, so such a path names the grader wherever the command ran.
PARENT_STEPS_TO_GRADER = re.compile(r"^(?:\.\./)+" + re.escape(protocol.GRADER) + r"(?:/|$)")
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
        Arm("foe-lean", "foe", "document", "lean"),
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
    raise ValueError(f"key arms names {name!r}, which is not an arm of the {family} family; the arms are {', '.join(arm.name for arm in ARMS[family])}")


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
    # Absolute paths every document arm may read and execute, from the document's `tool_roots`.
    tool_roots: tuple[str, ...] = ()
    # The budget keys the document's `budget` replaces for every attempt of the run.
    budget_overrides: dict[str, int] = field(default_factory=dict)
    # The run's canary sentences by CANARY_NAMES; empty when the run plants none.
    canaries: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_roots": list(self.tool_roots),
            "budget_overrides": dict(self.budget_overrides),
            "canaries": dict(self.canaries),
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


def discover_tasks(tasks_dir: Path, names: Sequence[str] | None) -> list[Selected]:
    """Every task directory under `tasks_dir`, or the ones `select` names in that order; errors name the key and the path or the name."""
    if not tasks_dir.is_dir():
        raise FileNotFoundError(f"key tasks names {tasks_dir}, which is not a directory")
    found: dict[str, Selected] = {}
    for directory in sorted(path for path in tasks_dir.iterdir() if path.is_dir() and (path / protocol.TASK_FILE).is_file()):
        task = protocol.load(directory)
        if task.name in found:
            raise ValueError(f"{directory} and {found[task.name].directory} both declare the task {task.name!r}")
        found[task.name] = Selected(directory, task)
    if names is None:
        if not found:
            raise ValueError(f"key tasks names {tasks_dir}, which holds no task directory")
        return list(found.values())
    selected = []
    for name in names:
        if name not in found:
            raise ValueError(f"key select names {name!r}, which is not a task under {tasks_dir}; the tasks are {', '.join(sorted(found)) or 'none'}")
        selected.append(found[name])
    return selected


def shared_family(tasks: list[Selected], tasks_dir: Path) -> str:
    """The one family every selected task declares; a selection spanning two families is refused naming both."""
    first_of: dict[str, str] = {}
    for entry in tasks:
        first_of.setdefault(entry.task.family, entry.task.name)
    if len(first_of) > 1:
        (family_a, name_a), (family_b, name_b) = list(first_of.items())[:2]
        raise ValueError(f"the selected tasks under {tasks_dir} span two families: {name_a} is {family_a} and {name_b} is {family_b}; use select to name the tasks of one family")
    return next(iter(first_of))


def select_arms(names: Sequence[str] | None, family: str) -> list[Arm]:
    """The arms the document's `arms` names, in that order, or every arm of the family when the key is absent."""
    if names is None:
        return list(ARMS[family])
    if not names:
        raise ValueError(f"key arms is []; expected at least one arm name from {', '.join(arm.name for arm in ARMS[family])}")
    if len(set(names)) != len(names):
        raise ValueError("key arms names an arm twice")
    return [arm_by_name(family, name) for name in names]


def parse_budget(value: Any, document: Path) -> dict[str, int]:
    """The budget overrides the document's `budget` object names; every error names the document and the key."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{document}: key budget is {value!r}; expected an object over the keys {', '.join(protocol.BUDGET_KEYS)}")
    overrides: dict[str, int] = {}
    for key, number in value.items():
        if key not in protocol.BUDGET_KEYS:
            raise ValueError(f"{document}: key budget.{key} is not a budget key; the keys are {', '.join(protocol.BUDGET_KEYS)}")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise ValueError(f"{document}: key budget.{key} is {number!r}; expected a positive integer")
        overrides[key] = number
    return overrides


class Keyed:
    """Typed reads of one object of a run document; every error names the document and the qualified key."""

    def __init__(self, document: Path, data: dict[str, Any], prefix: str = "") -> None:
        self.document = document
        self.data = data
        self.prefix = prefix

    def error(self, key: str, rule: str) -> ValueError:
        return ValueError(f"{self.document}: key {self.prefix}{key} {rule}")

    def refuse_unknown(self, known: Sequence[str]) -> None:
        for key in self.data:
            if key not in known:
                raise self.error(key, f"is unknown; the keys are {', '.join(known)}")

    def string(self, key: str, default: str | None = None, choices: Sequence[str] | None = None, required: bool = False) -> str | None:
        value = self.data.get(key, default)
        if value is None:
            if required:
                raise self.error(key, "is absent; expected a non-empty string" + (f" from {', '.join(choices)}" if choices else ""))
            return None
        if not isinstance(value, str) or not value:
            raise self.error(key, f"is {value!r}; expected a non-empty string")
        if choices is not None and value not in choices:
            raise self.error(key, f"is {value!r}; expected one of {', '.join(choices)}")
        return value

    def boolean(self, key: str, default: bool) -> bool:
        value = self.data.get(key, default)
        if not isinstance(value, bool):
            raise self.error(key, f"is {value!r}; expected true or false")
        return value

    def positive_integer(self, key: str, default: int) -> int:
        value = self.data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise self.error(key, f"is {value!r}; expected a positive integer")
        return value

    def strings(self, key: str) -> list[str] | None:
        value = self.data.get(key)
        if value is None:
            return None
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise self.error(key, f"is {value!r}; expected a list of non-empty strings")
        return list(value)

    def obj(self, key: str, known: Sequence[str], required: bool = False) -> Keyed:
        value = self.data.get(key)
        if value is None:
            if required:
                raise self.error(key, f"is absent; expected an object with the keys {', '.join(known)}")
            value = {}
        if not isinstance(value, dict):
            raise self.error(key, f"is {value!r}; expected an object with the keys {', '.join(known)}")
        nested = Keyed(self.document, value, f"{self.prefix}{key}.")
        nested.refuse_unknown(known)
        return nested


def read_document(path: Path) -> Keyed:
    """The run document at `path` as an object with no unknown key; an unreadable file, a file that is not JSON, and a value that is not an object are refused by path."""
    resolved = path.resolve()
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(f"the run document {resolved} cannot be read: {exc.strerror}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"the run document {resolved} is not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError(f"the run document {resolved} is not an object; the keys are {', '.join(DOCUMENT_KEYS)}")
    keyed = Keyed(resolved, data)
    keyed.refuse_unknown(DOCUMENT_KEYS)
    return keyed


def document_path(keyed: Keyed, value: str) -> Path:
    """A path the document names: a leading ~ expands to the home directory, and a relative path resolves against the document's directory."""
    expanded = Path(value).expanduser()
    return (expanded if expanded.is_absolute() else keyed.document.parent / expanded).resolve()


def document_out(keyed: Keyed) -> Path:
    """The document's `out` directory, or the directory under DEFAULT_OUT_ROOT named by the document's file stem."""
    named = keyed.string("out")
    if named is None:
        return (Path(DEFAULT_OUT_ROOT).expanduser() / keyed.document.stem).resolve()
    return document_path(keyed, named)


def checkout_root(start: Path) -> Path | None:
    """The nearest directory at or above `start` holding GIT_ENTRY, or None when no ancestor does."""
    for candidate in (start, *start.parents):
        if (candidate / GIT_ENTRY).exists():
            return candidate
    return None


@dataclass(frozen=True)
class Document:
    """One run document with every default applied and every path resolved.

    `codex` is the executable `harnesses.codex` resolved to: the PATH
    lookup of a bare command name, or the named path; it is None when the
    lookup found nothing. `credential` is the named file whether or not it
    exists. `codex_fault` and `credential_fault` name what stops a Codex
    arm, and the runner refuses them only when a Codex arm is selected.
    """

    path: Path
    tasks: Path
    select: tuple[str, ...] | None
    arms: tuple[str, ...] | None
    attempts: int
    resume: bool
    route: str
    model: str
    effort: str
    base_url: str | None
    codex_wire_api: str
    budget: dict[str, int]
    tool_roots: tuple[str, ...]
    foe: Path
    # The value of `harnesses.codex` as written, or DEFAULT_CODEX.
    codex_named: str
    codex: Path | None
    credential: Path
    out: Path
    grader_timeout: int
    source_root: Path
    foe_config_dir: Path

    def codex_fault(self) -> str | None:
        if self.codex is None:
            return f"key harnesses.codex names {self.codex_named!r}, which is absent from PATH"
        if not self.codex.is_file() or not os.access(self.codex, os.X_OK):
            return f"key harnesses.codex names {self.codex}, which is not an executable file"
        return None

    def credential_fault(self) -> str | None:
        if not self.credential.is_file():
            return f"key harnesses.credential names {self.credential}, which is not a file"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "tasks": str(self.tasks),
            "select": None if self.select is None else list(self.select),
            "arms": None if self.arms is None else list(self.arms),
            "attempts": self.attempts,
            "resume": self.resume,
            "model": {"route": self.route, "name": self.model, "effort": self.effort, "base_url": self.base_url, "codex_wire_api": self.codex_wire_api},
            "budget": dict(self.budget),
            "tool_roots": list(self.tool_roots),
            "harnesses": {
                "foe": str(self.foe),
                "codex": None if self.codex is None else str(self.codex),
                "codex_named": self.codex_named,
                "credential": str(self.credential),
            },
            "out": str(self.out),
            "grader_timeout": self.grader_timeout,
            "source_root": str(self.source_root),
            "foe_config_dir": str(self.foe_config_dir),
        }


def load_document(path: Path) -> Document:
    """The run document at `path`, checked key by key and resolved; every error names the document and the key.

    The checks that depend on the selected arms, whether the Codex binary
    and the credential exist, are left to the caller through
    `Document.codex_fault` and `Document.credential_fault`.
    """
    keyed = read_document(path)
    tasks = document_path(keyed, keyed.string("tasks", required=True))
    select = keyed.strings("select")
    if select is not None and not select:
        raise keyed.error("select", "is []; expected at least one task name, or no select key to run every task")
    arms = keyed.strings("arms")
    attempts = keyed.positive_integer("attempts", 1)
    resume = keyed.boolean("resume", False)
    model = keyed.obj("model", MODEL_KEYS, required=True)
    route = model.string("route", choices=ROUTES, required=True)
    name = model.string("name", required=True)
    effort = model.string("effort", DEFAULT_EFFORT)
    base_url = model.string("base_url")
    wire_api = model.string("codex_wire_api", choices=CODEX_WIRE_APIS)
    if route == "compatible":
        if base_url is None:
            raise model.error("base_url", "is absent; the compatible route needs the server's base URL, ending in /v1")
    else:
        if base_url is not None:
            raise model.error("base_url", f"is {base_url!r}; the {route} route reaches the model without a base URL, so the key is refused there")
        if wire_api is not None:
            raise model.error("codex_wire_api", f"is {wire_api!r}; the key names the compatible server's wire format and is refused on the {route} route")
    budget = parse_budget(keyed.data.get("budget"), keyed.document)
    tool_roots_: list[str] = []
    for root in keyed.strings("tool_roots") or []:
        resolved = document_path(keyed, root)
        if not resolved.exists():
            raise keyed.error("tool_roots", f"names {root!r}, which resolves to {resolved} and does not exist")
        tool_roots_.append(str(resolved))
    harnesses = keyed.obj("harnesses", HARNESS_KEYS)
    foe_named = harnesses.string("foe")
    if foe_named is None:
        checkout = checkout_root(keyed.document.parent) or checkout_root(Path.cwd())
        if checkout is None:
            raise harnesses.error("foe", f"is absent, and neither the document's directory {keyed.document.parent} nor the current directory {Path.cwd()} is inside a git checkout, so the default {DEFAULT_FOE} has no checkout to resolve against")
        foe = checkout / DEFAULT_FOE
    else:
        foe = document_path(keyed, foe_named)
    if not foe.is_file() or not os.access(foe, os.X_OK):
        raise harnesses.error("foe", f"names {foe}, which is not an executable file")
    codex_named = harnesses.string("codex", DEFAULT_CODEX) or DEFAULT_CODEX
    if "/" in codex_named:
        codex: Path | None = document_path(keyed, codex_named)
    else:
        found = shutil.which(codex_named)
        codex = None if found is None else Path(found).resolve()
    credential = document_path(keyed, harnesses.string("credential", DEFAULT_CREDENTIAL) or DEFAULT_CREDENTIAL)
    source_named = keyed.string("source_root")
    config_named = keyed.string("foe_config_dir")
    return Document(
        path=keyed.document,
        tasks=tasks,
        select=None if select is None else tuple(select),
        arms=None if arms is None else tuple(arms),
        attempts=attempts,
        resume=resume,
        route=route,
        model=name,
        effort=effort or DEFAULT_EFFORT,
        base_url=base_url,
        codex_wire_api=wire_api or DEFAULT_CODEX_WIRE_API,
        budget=budget,
        tool_roots=tuple(tool_roots_),
        foe=foe,
        codex_named=codex_named,
        codex=codex,
        credential=credential,
        out=document_out(keyed),
        grader_timeout=keyed.positive_integer("grader_timeout", DEFAULT_GRADER_TIMEOUT_SECONDS),
        source_root=foe if source_named is None else document_path(keyed, source_named),
        foe_config_dir=document_path(keyed, config_named or DEFAULT_FOE_CONFIG_DIR),
    )


def canaries() -> dict[str, str]:
    """The two canary sentences of one run, each carrying a fresh random identifier so that no earlier run's text matches."""
    return {
        name: f"{CANARY_PREFIX}{name.replace('_', ' ')} isolation canary {uuid.uuid4()}; a model request that carries it was built from a file the harness must never read."
        for name in CANARY_NAMES
    }


def plant_foe_canary(config_dir: Path, sentence: str) -> Path:
    """Write the foe canary into foe's configuration directory as CANARY_FILE, a file foe never reads by design, and return its path.

    A file already at that path that is not a canary of this runner is left
    as it is and refused by path, because the directory belongs to the user.
    """
    path = config_dir / CANARY_FILE
    if path.exists() and not (path.is_file() and path.read_text(encoding="utf-8").startswith(CANARY_PREFIX)):
        raise ValueError(f"key foe_config_dir names {config_dir}, and {path} exists there without a canary sentence of this runner; move it or name another directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sentence + "\n", encoding="utf-8")
    return path


def remove_foe_canary(path: Path, sentence: str) -> bool:
    """Remove the foe canary at `path` when it still holds `sentence`, and return whether it was removed.

    A file holding another sentence was planted by a later run into the
    same directory and stays, since that run removes it.
    """
    if not path.is_file() or path.read_text(encoding="utf-8") != sentence + "\n":
        return False
    path.unlink()
    return True


def effective_budget(settings: Settings, task: protocol.Task) -> dict[str, int]:
    """The task's budget with the run's overrides applied, over every key of `protocol.BUDGET_KEYS`."""
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


def needed_tool_roots(settings: Settings, task: protocol.Task) -> list[str]:
    """The tool roots the task needs beyond the system roots, as the run document and the task's metadata name them, in that order.

    A root is compared in its normalized spelling, so that a trailing slash
    or a doubled separator names the same directory as the system root.
    """
    named = task.metadata.get(METADATA_TOOL_ROOTS) or []
    roots: list[str] = []
    for root in (*settings.tool_roots, *map(str, named)):
        normalized = os.path.normpath(root)
        if normalized not in graphs.EXECUTE_ROOTS and normalized not in roots:
            roots.append(normalized)
    return roots


def presumed_absent_fault(settings: Settings, task: protocol.Task, arms: Sequence[Arm]) -> str | None:
    """Where the program `metadata.presumes_absent` names is found among the directories the selected arms run commands from, or None.

    A missing-capability task presumes one program absent, and its grader
    reads no search path, so the runner is what holds the premise. A foe
    arm runs commands from the system search path of docs/tools.md "bash"
    and executes the task's tool roots; a Codex arm inherits the runner's
    PATH, which is the one environment read this check makes. A host where
    the program is found cannot run the task, and the run is refused by
    name before the plan.
    """
    named = task.metadata.get(METADATA_PRESUMES_ABSENT)
    if named is None:
        return None
    if not isinstance(named, str) or not named.strip() or "/" in named:
        raise ValueError(f"task {task.name!r}: metadata.{METADATA_PRESUMES_ABSENT} is {named!r}; expected the bare name of a program")
    premise = f"task {task.name!r} presumes {named} absent under metadata.{METADATA_PRESUMES_ABSENT}, and"
    remedy = "the task's premise does not hold on this host; leave the task out with select, or run it on a host without the program"
    if any(arm.harness == "foe" for arm in arms):
        directories = [*SYSTEM_SEARCH_PATH.split(":"), *tool_roots(settings, task)]
        found = shutil.which(named, path=":".join(directories))
        if found is not None:
            return f"{premise} {found} is under {Path(found).parent}, which the foe arms run commands from; {remedy}"
    for arm in arms:
        if arm.harness != "codex":
            continue
        found = shutil.which(named)
        if found is not None:
            return f"{premise} {found} is on the PATH the {arm.name} arm inherits; {remedy}"
        break
    return None


def import_interpreters() -> list[str]:
    """The interpreters an attempt of a task that presumes a module unimportable reaches, in the order they are asked.

    `protocol.PYTHON` is the interpreter every grade script names in its
    shebang. The `python3` of SYSTEM_SEARCH_PATH is the one an arm's own
    command resolves, and is asked as well when it is another file, since
    a host whose two interpreters hold different modules breaks the premise
    for the arm or for the grade.
    """
    interpreters = [protocol.PYTHON]
    reached = shutil.which("python3", path=SYSTEM_SEARCH_PATH)
    if reached is not None and os.path.realpath(reached) != os.path.realpath(protocol.PYTHON):
        interpreters.append(reached)
    return interpreters


def presumed_unimportable_fault(task: protocol.Task) -> str | None:
    """Where the module `metadata.presumes_unimportable` names is found by an interpreter the attempt reaches, or None.

    A task that asks for a derived artifact only one generator can produce
    presumes the module that generator imports absent, and its grader reads
    no site directory, so the runner is what holds the premise. Every
    interpreter of `import_interpreters` is asked, each from an empty
    directory of its own, since the directory the runner was started in is
    one no grade and no arm command runs from and its files would otherwise
    be importable here alone. The interpreter inherits the runner's
    environment, as a grade script does, so a module a variable such as
    PYTHONPATH puts on the path is found; that inheritance is the one
    environment read this check makes. A host where
    the module imports would grade the task against a premise that does not
    hold, and the run is refused by name before the plan.
    """
    named = task.metadata.get(METADATA_PRESUMES_UNIMPORTABLE)
    if named is None:
        return None
    if not isinstance(named, str) or not named.isidentifier() or keyword.iskeyword(named):
        raise ValueError(f"task {task.name!r}: metadata.{METADATA_PRESUMES_UNIMPORTABLE} is {named!r}; expected a module name")
    program = f"import {named}; print(getattr({named}, '__file__', None) or 'a module the interpreter builds in')"
    for interpreter in import_interpreters():
        with tempfile.TemporaryDirectory(prefix="presumes-unimportable-") as elsewhere:
            try:
                completed = subprocess.run([interpreter, "-c", program], cwd=elsewhere, capture_output=True, text=True, timeout=60, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ValueError(f"task {task.name!r} presumes {named} unimportable under metadata.{METADATA_PRESUMES_UNIMPORTABLE}, and {interpreter} could not be asked whether it imports: {exc}") from exc
        if completed.returncode == 0:
            return (
                f"task {task.name!r} presumes {named} unimportable under metadata.{METADATA_PRESUMES_UNIMPORTABLE}, and {interpreter} "
                f"imports it from {completed.stdout.strip() or 'an unnamed location'}, which is an interpreter the attempt reaches; "
                "the task's premise does not hold on this host; leave the task out with select, or run it on a host without the module"
            )
    found = module_on_disk(named)
    if found is not None:
        return (
            f"task {task.name!r} presumes {named} unimportable under metadata.{METADATA_PRESUMES_UNIMPORTABLE}, and no interpreter "
            f"imports it, but a copy of it is on this host at {found}. An arm that reads outside its workspace can put that copy on a "
            "path and run the generator, so the premise holds for an arm the kernel confines to its grants and not for an arm whose "
            "sandbox reads the filesystem, and the task compares the two sandboxes rather than the two harnesses; leave the task out "
            "with select, or run it on a host without the module"
        )
    return None


# Where a Python module may sit without any interpreter importing it: a
# package directory, a single-file module, a distribution's metadata, or a
# cached wheel. A distribution spells its name with either separator, so both
# are searched.
MODULE_SEARCH_ROOTS: tuple[str, ...] = ("/usr", "/opt", "/usr/local")
MODULE_SEARCH_SECONDS = 120


def module_on_disk(named: str) -> str | None:
    """A path holding the module `named` that no interpreter imports, or None.

    `presumed_unimportable_fault` asks every interpreter whether it imports
    the module, which finds a copy on a search path and misses one beside it.
    A module vendored inside another package, or a wheel in a download cache,
    is importable by an arm that reads the filesystem, finds the copy, and
    names its directory. The premise of the task is that the module cannot be
    reached at all, so this searches the home directory and the system
    prefixes for a copy under any of the four shapes a module takes on disk.
    The runner's own state directory is left out: the attempts it holds carry
    workspaces, and a module inside one of those is the fixture, not the host.
    """
    spellings = {named, named.replace("_", "-")}
    patterns: list[str] = []
    for spelling in sorted(spellings):
        patterns += [spelling, f"{spelling}.py", f"{spelling}-*.dist-info", f"{spelling}-*.whl"]
    roots = [str(Path.home()), *MODULE_SEARCH_ROOTS]
    state = Path(DEFAULT_OUT_ROOT).expanduser()
    # The name tests are parenthesised: -o binds looser than the implicit
    # -a, so an unparenthesised list would print only the last name.
    command = ["find", *(root for root in roots if Path(root).is_dir()), "-path", str(state), "-prune", "-o", "("]
    for index, pattern in enumerate(patterns):
        command += ["-name", pattern]
        if index != len(patterns) - 1:
            command.append("-o")
    command += [")", "-print", "-quit"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=MODULE_SEARCH_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    hit = completed.stdout.strip().splitlines()
    return hit[0] if hit else None


def not_applicable(settings: Settings, arm: Arm, task: protocol.Task) -> str | None:
    """The reason the arm cannot run the task, or None when it can.

    A built-in document carries its own grants, so a task that needs tool
    roots, from the run document's `tool_roots` or its own metadata, has no
    way to receive them under the foe-as-shipped arm; the attempt is
    recorded and never launched. A host whose toolchain sits under the
    built-in execute roots, as the container's does, names no tool roots
    and the arm runs.
    """
    if arm.kind != "builtin" or not needed_tool_roots(settings, task):
        return None
    sources: list[str] = []
    document_roots = [root for root in settings.tool_roots if root not in graphs.EXECUTE_ROOTS]
    if document_roots:
        sources.append(f"the run document names under tool_roots: {', '.join(document_roots)}")
    named = task.metadata.get(METADATA_TOOL_ROOTS) or []
    if named:
        sources.append(f"task {task.name!r} names under metadata.{METADATA_TOOL_ROOTS}: {', '.join(map(str, named))}")
    return (
        f"the {arm.name} arm runs the built-in document {arm.variant}, whose grants cannot take the tool roots {', and '.join(sources)}; "
        f"the arm runs a task whose tools sit under the built-in execute roots {', '.join(graphs.EXECUTE_ROOTS)} and whose document names no tool roots"
    )


def rotated(arms: list[Arm], attempt: int, task_index: int) -> list[Arm]:
    """The arms in the order attempt number `attempt` of the task at `task_index` runs them.

    The order starts one arm later for each attempt and one arm later for
    each task, so that the arm that goes first varies across tasks as well
    as across attempts. A run of one attempt per task would otherwise give
    every task to the same arm first, and an effect of running first, such
    as a shared cache the first arm fills, would land on that arm alone.
    """
    offset = (attempt - 1 + task_index) % len(arms)
    return arms[offset:] + arms[:offset]


def planned(tasks: list[Selected], arms: list[Arm], attempts: int) -> list[tuple[int, Selected, Arm]]:
    """Every (attempt, task, arm) triple in launch order, with the arm order rotated by attempt number and task position."""
    return [(attempt, entry, arm) for attempt in range(1, attempts + 1) for index, entry in enumerate(tasks) for arm in rotated(arms, attempt, index)]


def header(document: Document, settings: Settings, tasks: list[Selected], arms: list[Arm]) -> list[str]:
    """Every value the run document resolved to, one per line, so that a reader can verify the run from the document alone."""
    if settings.codex is None:
        codex = f"none; {document.codex_fault()}, and no selected arm needs it"
    elif "/" in document.codex_named:
        codex = str(settings.codex)
    else:
        codex = f"{settings.codex} (the command {document.codex_named!r} on PATH)"
    credential = f"none; {document.credential_fault()}, and no selected arm needs it" if settings.credential is None else str(settings.credential)
    overrides = ", ".join(f"{key}={value}" for key, value in settings.budget_overrides.items())
    rows = [
        ("tasks", str(document.tasks)),
        ("family", settings.family),
        ("selected", ", ".join(entry.task.name for entry in tasks) + ("" if document.select is not None else " (every task under tasks)")),
        ("arms", ", ".join(arm.name for arm in arms) + ("" if document.arms is not None else f" (every arm of the {settings.family} family)")),
        ("attempts", str(settings.attempts)),
        ("resume", "an attempt already recorded is skipped" if document.resume else "an existing record or attempt directory is refused"),
        ("foe", str(settings.foe)),
        ("codex", codex),
        ("credential", credential),
        ("route", settings.route),
        ("model", settings.model),
        ("effort", settings.effort),
        ("out", str(settings.out)),
        ("tool roots", ", ".join(settings.tool_roots) or "none beyond the system roots"),
        ("budget", f"{overrides} replace the same keys of every task's budget" if overrides else "every task's own budget"),
        ("grader timeout", f"{settings.grader_timeout} seconds"),
        ("source root", str(settings.source_root)),
        ("foe config dir", str(document.foe_config_dir)),
    ]
    if settings.route == "compatible":
        rows[10:10] = [("base URL", str(settings.base_url)), ("codex wire API", settings.codex_wire_api)]
    width = max(len(label) for label, _ in rows)
    return [f"Run document {document.path} resolved to:", *[f"  {label:<{width}}  {value}" for label, value in rows], ""]


def plan(settings: Settings, tasks: list[Selected], arms: list[Arm], document: Document) -> str:
    """State every resolved value, every attempt this run launches, and the largest spend it can incur, before any model is called.

    Under key resume an attempt whose record is already under `out` is
    listed apart and its ceilings are left out of the total, so that the
    spend a reader authorizes is the spend the run can incur.
    """
    triples = planned(tasks, arms, settings.attempts)
    attempt_word = "attempt" if settings.attempts == 1 else "attempts"
    task_word = "task" if len(tasks) == 1 else "tasks"
    arm_word = "arm" if len(arms) == 1 else "arms"
    lines = [
        *header(document, settings, tasks, arms),
        f"This evaluation calls {settings.model} over the {settings.route} route and spends real credit.",
        f"Largest spend it can incur, at {settings.attempts} {attempt_word} of each of {len(tasks)} {task_word} under {len(arms)} {arm_word}:",
        "",
        f"  {'model calls':>11}  {'input':>9}  {'output':>8}  {'seconds':>7}  attempt",
    ]
    totals = {key: 0 for key in protocol.BUDGET_KEYS}
    skipped: list[str] = []
    recorded: list[str] = []
    for attempt, entry, arm in triples:
        already = record_path(settings.out, entry.task.name, arm.name, attempt)
        if document.resume and already.exists():
            recorded.append(f"  {entry.task.name} / {arm.name} / {attempt}: {already}")
            continue
        reason = not_applicable(settings, arm, entry.task)
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
    total_label = "every attempt this run launches" if recorded else "every planned attempt"
    lines.append(f"  {totals['model_calls']:>11}  {totals['input_tokens']:>9,}  {totals['output_tokens']:>8,}  {totals['seconds']:>7}  {total_label}")
    if recorded:
        lines.append("")
        lines.append("Already recorded under key resume and never launched again:")
        lines.extend(recorded)
    if settings.budget_overrides:
        lines.append("")
        lines.append("The ceilings above are the effective ones: the document's budget replaces " + ", ".join(f"{key}={value}" for key, value in settings.budget_overrides.items()) + " in every task's budget.")
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
                "is then recorded as a fault. Add each installation such a command lives in to the document's tool_roots.",
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


# What a check suite writes while it runs: a compiler's output directory and
# the wrapper's private temporary directory. Both are created before an arm
# starts, for two reasons. A grant names a directory, and a directory that
# does not exist cannot be granted; and creating one inside the workspace
# needs write on the workspace, which a node that only reads does not have,
# so a node asked to run the tests could not run them. Creating them here
# leaves the same workspace to every arm.
CHECK_DIRECTORIES: tuple[str, ...] = ("target", CHECK_SCRATCH_DIR)


def make_check_directories(workspace: Path) -> None:
    """Create the directories a check suite writes into, before any arm runs."""
    for name in CHECK_DIRECTORIES:
        (workspace / name).mkdir(parents=True, exist_ok=True)


def write_check_script(path: Path, workspace: Path, command: list[str], search_path: Sequence[str] = ()) -> Path:
    """An executable that runs the check command from the workspace and prints findings.

    The tool definition in contracts/graphs.py states that the check prints
    one finding per line, nothing when every check passes, and exits zero
    either way. The script turns a failing command's output and exit status
    into findings, so that a check suite of any convention fits. The runtime
    starts a configured executable with an empty environment, so the script
    sets its own. The search path is the system directories of docs/tools.md
    "bash" followed by every directory of `search_path`, and the language is
    the one a bash command receives. HOME is the real user's home directory,
    read from the passwd database rather than from the environment, because
    that is what a Codex arm inherits and the two arms must run the same
    check. It is a string rather than a grant: the sandbox still refuses
    every path the contract does not name, and the toolchain directories the
    suite needs are named under the document's tool roots. Setting it to the
    workspace instead makes a toolchain manager look for its installation
    inside the workspace, find none, and attempt a download the sandbox
    denies, which fails the check for a reason that has nothing to do with
    the task. TMPDIR is CHECK_SCRATCH_DIR under
    the workspace, created with a cache tag so that the workspace snapshot
    leaves it out. It is not /tmp, which foe denies by design, so a suite
    whose tests require the shared temporary directory cannot run under a
    foe arm and its task belongs outside this evaluation. A command the shell cannot find ends the suite with
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
            f"HOME={shlex.quote(pwd.getpwuid(os.getuid()).pw_dir)}",
            f"TMPDIR={scratch}",
            "export PATH LANG HOME",
            f"cd {shlex.quote(str(workspace))} || {{ echo {shlex.quote(f'the workspace {workspace} cannot be entered')}; exit 0; }}",
            # A private temporary directory is a convenience, not a
            # requirement: the suite runs without one. Ending the check
            # because the directory could not be prepared reports no finding
            # about the workspace and hides whatever the suite would have
            # said, which is what happened when a sandbox refused the cache
            # tag inside a directory it had just allowed to be created.
            f"if mkdir -p {scratch} && printf '%s\\n' {shlex.quote(CACHE_TAG_SIGNATURE)} > {scratch}/{CACHE_TAG_FILE} 2>/dev/null; then",
            "  export TMPDIR",
            "else",
            "  unset TMPDIR",
            "fi",
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
    """The tool roots of a document arm: the system roots, the run document's `tool_roots`, and the task metadata's `tool_roots`."""
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
        # A root is granted in its normalized spelling, so that a trailing slash or a doubled separator adds no second root.
        normalized = os.path.normpath(root)
        if normalized not in roots:
            roots.append(normalized)
    return roots


def recorded_tool_roots(settings: Settings, arm: Arm, task: protocol.Task) -> list[str]:
    """The roots the arm's commands run under, for the attempt record.

    A document arm executes the merged tool roots of `tool_roots`. A
    built-in arm runs a document the binary carries, whose grants cover the
    built-in execute roots and nothing the run document or the task names,
    so its record states those roots alone. A Codex arm inherits the
    runner's PATH and no grant; its record keeps the roots a document arm
    of the same task holds, so that the records of one task state the same
    roots for the harnesses that are compared.
    """
    if arm.kind == "builtin":
        return list(graphs.EXECUTE_ROOTS)
    return tool_roots(settings, task)


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
        document = graphs.autonomy(
            workspace,
            check,
            budget,
            ablated=arm.variant == "ablated",
            lean=arm.variant == "lean",
            root_files=root_files,
            write_roots=roots or graphs.WRITE_ROOTS,
            execute=execute,
        )
    else:
        document = graphs.teams(
            workspace,
            check,
            budget,
            variant=arm.variant,
            max_concurrent=TEAM_CONCURRENCY,
            root_files=root_files,
            write_roots=roots or graphs.WRITE_ROOTS,
            execute=execute,
            returns=task.metadata.get(METADATA_RETURNS),
        )
    return with_placeholder(with_read_roots(document, tools), workspace)


def foe_route(settings: Settings) -> foe_arm.ModelRoute:
    provider = FOE_PROVIDERS[settings.route]
    if settings.route == "compatible":
        if not settings.base_url:
            raise ValueError("the compatible route needs model.base_url; docs/models.md requires base_url for compatible-http")
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
        raise ValueError("the compatible route needs model.base_url; the Codex provider override carries it as base_url")
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


class WatcherFailure(RuntimeError):
    """The budget watcher of a Codex attempt failed and ended the process tree.

    `codex_budget_watcher.run_with_watcher` raises only once the child has
    exited or been terminated, so the attempt launched and spent credit.
    The attempt carries this as its infrastructure fault and the run goes on
    to the next attempt; every other RuntimeError is a fault of the runner
    itself and ends the run.
    """


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
        raise ValueError(f"arm {arm.name} needs harnesses.codex and harnesses.credential")
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
        output_schema=codex_arm.schema_for(task.metadata.get(METADATA_RETURNS)),
        config_canary=settings.canaries.get(CODEX_CONFIG_CANARY),
    )
    try:
        return codex_arm.run(spec)
    except RuntimeError as exc:
        raise WatcherFailure(f"the arm ran and its budget watcher then failed, so the attempt spent credit and reported nothing: {exc}") from exc


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


def grader_paths_named(trajectory_: dict[str, Any] | None, root: Path) -> list[str]:
    """Every path under the root's grader directory that the normalized trajectory names, in order.

    The paths a command named, the path of every recorded file change, and
    the summary of every tool call are read. A tool call records the digest
    of its arguments rather than their content, so its summary is the one
    place a path of a tool call can appear, and an older record that carries
    no tool calls reads as carrying none. An absolute path is compared with
    the symbolic links of both sides resolved, so that a link in the output
    path matches. A relative path is compared against the workspace, and is
    also named when it steps out of its directory into a grader directory,
    since the directory a shell command ran in is not recorded.

    The grader directory does not exist while the arm runs, so every path
    listed is an attempt to look rather than a file that was read. An empty
    list therefore says the arm named no such path, and a listed path says
    where a reader looks in the arm's own records for what it did next.
    """
    if trajectory_ is None:
        return []
    grader = os.path.realpath(root / protocol.GRADER)
    workspace = os.path.realpath(root / protocol.WORKSPACE)
    named: list[str] = []
    for agent in trajectory_.get("agents", []):
        candidates = [path for command in agent.get("commands", []) for path in command.get("paths_named", [])]
        candidates.extend(change.get("path") for change in agent.get("file_changes", []))
        candidates.extend(call.get("summary") for call in agent.get("tool_calls", []))
        for candidate in candidates:
            if not isinstance(candidate, str) or not candidate or candidate in named:
                continue
            if not os.path.isabs(candidate) and PARENT_STEPS_TO_GRADER.match(candidate):
                named.append(candidate)
                continue
            resolved = os.path.realpath(candidate if os.path.isabs(candidate) else os.path.join(workspace, candidate))
            if (resolved + os.sep).startswith(grader + os.sep):
                named.append(candidate)
    return named


def model_authored_evidence(result: ArmResult) -> bool:
    """Whether the evidence a Codex arm reported carries the model's own sentences rather than the arm's.

    The arm reports the status `failed` in two shapes. When the run wrote a
    last message that is a JSON object, the arm keeps that message's
    evidence even where it replaces a status word the schema does not
    allow, so the sentences are the model's own: a sentence about a
    sandbox, a registry, or an HTTP status is then part of a report about
    the task. When no such message was written, the evidence is the arm's
    own record of how the run ended, which is where a provider condition
    appears. A record that names no last message settles neither case, and
    its evidence is read as the model's, so that a record which says
    nothing about who wrote it leaves the attempt scored.
    """
    path = result.record.get("last_message")
    if not isinstance(path, str) or not path:
        return True
    try:
        return isinstance(json.loads(Path(path).read_text(encoding="utf-8")), dict)
    except (OSError, ValueError):
        return False


def provider_outage(result: ArmResult) -> str | None:
    """The provider condition that ended the arm, or None when the harness ended it.

    An outage of the model service measures the service, so the attempt
    carries it as an infrastructure fault and no classification. A foe
    episode reports one as blocked with RECOVERY_EXHAUSTED, which the model
    loop reaches when a rate limit or a provider error outlasts the retries
    the seconds budget can fund; the same code carrying a workflow bound of
    WORKFLOW_RECOVERY_BOUNDS is the graph ending its own episode and stays a
    stop the runner classifies. A Codex run reports one as a failed status
    whose recorded stream errors, or whose evidence where the arm rather
    than the model wrote it, name a provider condition of
    PROVIDER_FAULT_PATTERNS. The model's own sentences are left out of that
    scan, since a report about a sandbox, a registry, or an HTTP status the
    task involves would otherwise take a wrong stop or a false completion
    out of every rate.
    """
    reported = result.reported
    status, code = str(reported.get("status")), reported.get("code")
    evidence = [str(line) for line in reported.get("evidence") or []]
    if result.harness == foe_arm.HARNESS:
        if status != protocol.BLOCKED or code != RECOVERY_EXHAUSTED:
            return None
        message = " ".join(evidence).strip() or "the run reported no evidence"
        if any(bound in message for bound in WORKFLOW_RECOVERY_BOUNDS):
            return None
        return f"the provider ended the attempt: foe reported {protocol.BLOCKED} with the code {RECOVERY_EXHAUSTED}: {message}"
    if status != protocol.FAILED:
        return None
    lines = [] if model_authored_evidence(result) else evidence
    for line in [*lines, *[str(item) for item in result.record.get("errors") or []]]:
        for condition, pattern in PROVIDER_FAULT_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                return f"the provider ended the attempt with {condition}: {line.strip()}"
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
        "tool_roots": recorded_tool_roots(settings, arm, task),
        "not_applicable": not_applicable(settings, arm, task),
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
        "grader_paths_named": [],
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
        # The grader is written after the arm has exited, so that no arm can read the hidden tests, the oracle, or the corruptions while it runs.
        protocol.materialize(entry.directory, root, protocol.WORKSPACE)
    except (OSError, ValueError) as exc:
        record["infrastructure_error"] = f"the task did not materialize: {exc}"
        record["ended_ms"] = foe_arm.now_ms()
        return record
    make_check_directories(workspace)
    before = snapshot(workspace)
    try:
        result = run_arm(settings, arm, task, workspace, attempt_dir)
    except WatcherFailure as exc:
        # The child had already run, so the record says what was spent rather than that nothing started.
        record["infrastructure_error"] = str(exc)
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
        record["grader_paths_named"] = grader_paths_named(record["trajectory"], root)
        # A provider outage measures the model service, so it replaces the classification of an attempt that would otherwise be read as a stop.
        record["infrastructure_error"] = record["infrastructure_error"] or provider_outage(result)
    try:
        protocol.materialize(entry.directory, root, protocol.GRADER)
    except (OSError, ValueError) as exc:
        record["infrastructure_error"] = record["infrastructure_error"] or f"the task's grader did not materialize after the arm ran: {exc}"
        record["ended_ms"] = foe_arm.now_ms()
        return record
    reported = protocol_reported(record["reported"]) if record["reported"] else protocol.Reported(protocol.FAILED, None, record["infrastructure_error"] or "")
    graded = feature_removal.grade_with_timeout(root, reported, record["candidate"], arm.name, settings.grader_timeout)
    record["grade"] = {"passed": graded.passed, "findings": list(graded.findings), "damage": list(graded.damage), "units": None}
    if task.family == teams.FAMILY and task.class_name == teams.FAN_OUT:
        try:
            record["grade"]["units"] = teams.read_units(root)
        except ValueError as exc:
            # The grade ran, but its per-unit record cannot be read; the attempt measured no unit verdict.
            record["infrastructure_error"] = record["infrastructure_error"] or f"the fan-out grade left an unreadable units record: {exc}"
    if record["infrastructure_error"] is None:
        record["classification"] = protocol.classify(task, reported, graded)
    record["ended_ms"] = foe_arm.now_ms()
    return record


def set_aside(directory: Path) -> Path:
    """Rename an interrupted attempt's directory to a free name beside itself, and return the new path.

    The directory holds the episode log, the Codex event stream and standard
    error, and the fresh CODEX_HOME of an attempt that spent real credit,
    and after a kill that skipped the write-back that home holds the only
    copy of a credential Codex refreshed. It is therefore kept where an
    operator finds it, and the attempt runs again into a fresh directory.
    """
    number = 1
    while True:
        aside = directory.parent / f"{directory.name}{INTERRUPTED_SUFFIX}{number:02d}"
        if not aside.exists():
            directory.rename(aside)
            return aside
        number += 1


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog="Exit status 0 means every attempt evaluated the harness, 1 that at least one attempt hit a deployment fault, and 2 that nothing was launched.")
    parser.add_argument("document", type=Path, help="the run document, a JSON file whose keys the module docstring states")
    parser.add_argument("--confirm-spend", action="store_true", help="launch the attempts; without it the resolved document and the plan are printed and nothing runs")
    args = parser.parse_args(argv)

    def refuse(message: str) -> int:
        print(f"cross harness: {message}", file=sys.stderr)
        return NOTHING_LAUNCHED

    try:
        document = load_document(args.document)
        tasks = discover_tasks(document.tasks, document.select)
        family = shared_family(tasks, document.tasks)
        arms = select_arms(document.arms, family)
    except (ValueError, FileNotFoundError) as exc:
        return refuse(str(exc))
    needs_codex = any(arm.harness == "codex" for arm in arms)
    fault = document.codex_fault() or document.credential_fault()
    if needs_codex and fault is not None:
        return refuse(f"the arms {', '.join(arm.name for arm in arms if arm.harness == 'codex')} need harnesses.codex and harnesses.credential: {fault}")
    settings = Settings(
        foe=document.foe,
        codex=document.codex if document.codex_fault() is None else None,
        family=family,
        attempts=document.attempts,
        route=document.route,
        base_url=document.base_url,
        model=document.model,
        effort=document.effort,
        out=document.out,
        credential=document.credential if document.credential_fault() is None else None,
        codex_wire_api=document.codex_wire_api,
        grader_timeout=document.grader_timeout,
        source_root=document.source_root,
        tool_roots=document.tool_roots,
        budget_overrides=document.budget,
        canaries=canaries(),
    )
    try:
        if settings.route == "compatible":
            foe_route(settings)
        for entry in tasks:
            tool_roots(settings, entry.task)
            # A premise the host breaks is refused before the plan, since it
            # would let an attempt run whose grade measures the host and no harness.
            fault = presumed_absent_fault(settings, entry.task, arms)
            if fault is not None:
                raise ValueError(fault)
            fault = presumed_unimportable_fault(entry.task)
            if fault is not None:
                raise ValueError(fault)
            # The document builders refuse some budgets, such as a seconds
            # ceiling with no room for the check timeout; the refusal
            # belongs before the plan and before any attempt spends credit.
            try:
                graphs.check_budget(effective_budget(settings, entry.task))
            except ValueError as exc:
                raise ValueError(f"{entry.task.name}: the effective budget is refused: {exc}") from exc
    except (ValueError, FileNotFoundError) as exc:
        return refuse(str(exc))
    if not args.confirm_spend:
        print(plan(settings, tasks, arms, document))
        return NOTHING_LAUNCHED

    triples = planned(tasks, arms, settings.attempts)
    # An attempt directory without a record is what an interrupted run leaves.
    # Under key resume an attempt already recorded is skipped and such a
    # directory is set aside and named before that attempt runs again, since
    # it is the only record of what an attempt that spent credit did; without
    # the key both are refused, since moving a directory is the user's decision.
    pending: list[tuple[int, Selected, Arm]] = []
    skipped = 0
    for attempt, entry, arm in triples:
        record = record_path(settings.out, entry.task.name, arm.name, attempt)
        leftover = attempt_path(settings.out, entry.task.name, arm.name, attempt)
        if record.exists():
            if not document.resume:
                return refuse(f"a record already exists: {record}")
            skipped += 1
            print(f"cross harness: attempt {attempt}, {entry.task.name}, {arm.name} is already recorded and is skipped: {record}", file=sys.stderr, flush=True)
            continue
        if leftover.exists():
            if not document.resume:
                return refuse(f"an attempt directory already exists without a record: {leftover}; remove it or choose another out")
            try:
                aside = set_aside(leftover)
            except OSError as exc:
                return refuse(f"the attempt directory {leftover} could not be set aside: {exc}")
            print(f"cross harness: the attempt directory {leftover} holds no record and is set aside at {aside} before the attempt runs again", file=sys.stderr, flush=True)
        pending.append((attempt, entry, arm))
    settings.out.mkdir(parents=True, exist_ok=True)
    try:
        provenance = provenance_of(settings, needs_codex)
    except ValueError as exc:
        return refuse(str(exc))
    run_file = run_file_path(settings.out)
    try:
        foe_canary = plant_foe_canary(document.foe_config_dir, settings.canaries[FOE_CONFIG_CANARY])
    except (ValueError, OSError) as exc:
        return refuse(str(exc))
    write_json(
        run_file,
        {
            "schema_version": SCHEMA_VERSION,
            "document": document.to_dict(),
            "settings": settings.to_dict(),
            "provenance": provenance,
            "canaries": {
                CODEX_CONFIG_CANARY: {"sentence": settings.canaries[CODEX_CONFIG_CANARY], "placement": f"{codex_arm.CONFIG_CANARY_NAME} in the fresh CODEX_HOME of every Codex attempt"},
                FOE_CONFIG_CANARY: {"sentence": settings.canaries[FOE_CONFIG_CANARY], "path": str(foe_canary), "placement": f"{CANARY_FILE} in foe's configuration directory {document.foe_config_dir}, removed once the attempts have ended"},
            },
            "arms": [arm.name for arm in arms],
            "tasks": [entry.task.name for entry in tasks],
            "budgets": {entry.task.name: effective_budget(settings, entry.task) for entry in tasks},
            "tool_roots": {entry.task.name: tool_roots(settings, entry.task) for entry in tasks},
            "plan": plan(settings, tasks, arms, document),
        },
    )

    faults = 0
    try:
        for attempt, entry, arm in pending:
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
    finally:
        # The canary lies in the user's configuration directory, so it is removed however the attempts ended.
        remove_foe_canary(foe_canary, settings.canaries[FOE_CONFIG_CANARY])
    # `attempts` counts what this run launched, so that a rerun over a full
    # output directory does not read as a run that measured something.
    print(json.dumps({"records": str(settings.out / RECORDS_DIR), "run": str(run_file), "attempts": len(pending), "planned": len(triples), "skipped": skipped, "infrastructure_failures": faults}))
    return DEPLOYMENT_FAULT if faults else EVALUATED


if __name__ == "__main__":
    sys.exit(main())
