#!/usr/bin/python3
"""Run one task under foe and report the outcome it printed.

An arm is one harness given one task in one workspace. This module is the
foe arm: it writes the execution contract the run uses, starts the foe
binary against a real model route, and reads back the outcome line the
binary prints. `ArmResult`, defined here and shared with the Codex arm, is
the record every arm returns, so the runner grades both harnesses through
one shape.

The contract arrives as a document the caller supplies. The arm fills three
of its keys before writing it to `artifacts/config.json`: `task` receives
the task text, `model` receives the route, and `grants` receives the
workspace. The placeholder `{workspace}` is replaced in every string of the
document. A document whose grants name the placeholder has placed the
workspace itself; a document whose grants name no placeholder receives the
workspace at the head of `grants.read` and `grants.write`, because a coding
task reads and writes the tree it is given. The record states which of the
two happened.

The run has two clocks. The document's own `budget.seconds` is what foe
enforces and reports as `exhausted`. The arm's cap, `seconds` plus a margin,
is the point at which the arm ends the run itself and reports `killed`, so a
run whose runtime hangs cannot hold the evaluation. The margin exists so
that foe's own limit fires first whenever it can. Ending the run starts with
SIGINT, the one signal foe handles: foe cancels the episode, ends every tool
process group, and closes the log with a `failed` outcome. SIGTERM and then
SIGKILL follow for a foe that does not exit; a tool process that foe started
leads a process group of its own, so those two reach it only through foe.

The reported outcome is read from the one JSON line the binary writes to
standard output, which docs/design.md "The command line" specifies, and the
episode directory from the `foe: log PATH` line on standard error. A
completed value's `learned` claims, when the value carries them, are the
evidence the arm reports, because they are the sentences the model offered
in support of completion.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EVALS = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(EVALS))

import foe_build  # noqa: E402

HARNESS = "foe"

# The outcome kinds of docs/log-format.md, and `killed`, which an arm reports
# when the run was ended from outside.
COMPLETED, BLOCKED, EXHAUSTED, FAILED, KILLED = "completed", "blocked", "exhausted", "failed", "killed"
OUTCOME_KINDS: tuple[str, ...] = (COMPLETED, BLOCKED, EXHAUSTED, FAILED)
STATUSES: tuple[str, ...] = (*OUTCOME_KINDS, KILLED)

WORKSPACE_PLACEHOLDER = "{workspace}"

# How long past the cap the arm waits before terminating the run, so that
# foe's own seconds budget settles the episode whenever it can.
TIMEOUT_MARGIN_SECONDS = 30
# How long the run gets to exit after each of SIGINT, SIGTERM, and SIGKILL
# before the next signal follows, and how long an output pipe is awaited
# after the run has exited.
TERMINATION_GRACE_SECONDS = 5.0
TERMINATION_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM, signal.SIGKILL)

# The grant kinds the arm places the workspace in; docs/config.md
# "grants" types each as a list of strings.
WORKSPACE_GRANT_KINDS: tuple[str, ...] = ("read", "write")
# The most characters of standard error an evidence line quotes.
STDERR_TAIL_CHARACTERS = 2000

# The providers whose model block accepts `reasoning_effort`, from
# docs/models.md "Providers".
REASONING_EFFORT_PROVIDERS: tuple[str, ...] = ("openai", "openai-codex")

CONFIG_NAME, STDOUT_NAME, STDERR_NAME = "config.json", "stdout.txt", "stderr.txt"


def now_ms() -> int:
    return int(time.time() * 1000)


def reported(status: str, code: str | None = None, evidence: list[str] | None = None) -> dict[str, Any]:
    """The outcome an arm reports: a status, a code, and the sentences given for it."""
    if status not in STATUSES:
        raise ValueError(f"reported status is {status!r}; expected one of {', '.join(STATUSES)}")
    return {"status": status, "code": code, "evidence": list(evidence or [])}


@dataclass(frozen=True)
class ArmResult:
    """What one arm did with one task.

    `reported` is the outcome the arm reported, as `reported` builds it.
    `candidate` is the value the run returned, or None. `exit_status` is
    None when the arm terminated the run. `record` holds every command line
    the arm ran, the paths it wrote, and the `CODEX_HOME` the child process
    received, which is None for a foe run.
    """

    arm_name: str
    harness: str
    started_ms: int
    ended_ms: int
    exit_status: int | None
    reported: dict[str, Any]
    candidate: Any
    artifacts: Path
    record: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_name": self.arm_name,
            "harness": self.harness,
            "started_ms": self.started_ms,
            "ended_ms": self.ended_ms,
            "exit_status": self.exit_status,
            "reported": dict(self.reported),
            "candidate": self.candidate,
            "artifacts": str(self.artifacts),
            "record": dict(self.record),
        }


@dataclass(frozen=True)
class ModelRoute:
    """The model a run calls: a provider and a model name, with the endpoint for a compatible server."""

    provider: str
    model: str
    base_url: str | None = None

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError("route provider is empty")
        if not self.model:
            raise ValueError("route model is empty")
        if self.provider == "compatible-http" and not self.base_url:
            raise ValueError("route provider compatible-http needs base_url; docs/models.md requires it")

    def block(self, reasoning_effort: str | None) -> dict[str, str]:
        """The `model` block of a configuration document for this route.

        `reasoning_effort` enters the block only for the providers whose
        table row in docs/models.md accepts it; every other provider refuses
        the option by name.
        """
        block = {"provider": self.provider, "model": self.model}
        if self.base_url:
            block["base_url"] = self.base_url
        if reasoning_effort and self.provider in REASONING_EFFORT_PROVIDERS:
            block["reasoning_effort"] = reasoning_effort
        return block


@dataclass(frozen=True)
class FoeSpec:
    """Everything one foe run needs.

    `document` is the execution contract before `task`, `model`, and the
    workspace are filled in; the arm does not modify the caller's object.
    `seconds` is the cap the arm enforces from outside, and the document's
    `budget.seconds`, when present, must not exceed it.
    """

    arm_name: str
    binary: Path
    document: dict[str, Any]
    task: str
    workspace: Path
    log_dir: Path
    artifacts: Path
    route: ModelRoute
    seconds: int
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.seconds, bool) or not isinstance(self.seconds, int) or self.seconds <= 0:
            raise ValueError(f"spec seconds is {self.seconds!r}; the cap is a positive integer")
        if not self.task.strip():
            raise ValueError("spec task is empty")


def _substitute(value: Any, workspace: str) -> tuple[Any, int]:
    """A copy of a JSON value with every placeholder replaced, and how many strings changed."""
    if isinstance(value, str):
        if WORKSPACE_PLACEHOLDER in value:
            return value.replace(WORKSPACE_PLACEHOLDER, workspace), 1
        return value, 0
    if isinstance(value, list):
        items, count = [], 0
        for item in value:
            replaced, changed = _substitute(item, workspace)
            items.append(replaced)
            count += changed
        return items, count
    if isinstance(value, dict):
        entries, count = {}, 0
        for key, item in value.items():
            replaced, changed = _substitute(item, workspace)
            entries[key] = replaced
            count += changed
        return entries, count
    return value, 0


def prepare_document(spec: FoeSpec) -> tuple[dict[str, Any], str]:
    """The document the run uses, and how the workspace entered its grants.

    The second value is `placeholder` when the document's grants named the
    workspace with WORKSPACE_PLACEHOLDER and `grants` when the arm inserted
    it at the head of `grants.read` and `grants.write`. A placeholder outside
    the grants is replaced either way and decides nothing.
    """
    grants = spec.document.get("grants")
    if not isinstance(grants, dict):
        raise ValueError("document key grants is absent or not an object; docs/config.md requires it")
    for kind in WORKSPACE_GRANT_KINDS:
        paths = grants.get(kind)
        if paths is not None and (not isinstance(paths, list) or not all(isinstance(path, str) for path in paths)):
            raise ValueError(f"document key grants.{kind} is {paths!r}; docs/config.md types it as a list of strings")
    workspace = str(spec.workspace)
    _, named_in_grants = _substitute(grants, workspace)
    document, _ = _substitute(spec.document, workspace)
    if named_in_grants:
        placement = "placeholder"
    else:
        placement = "grants"
        for kind in WORKSPACE_GRANT_KINDS:
            paths = list(document["grants"].get(kind) or [])
            if workspace not in paths:
                paths.insert(0, workspace)
            document["grants"][kind] = paths
    budget = document.get("budget")
    if isinstance(budget, dict) and "seconds" in budget:
        declared = budget["seconds"]
        if not isinstance(declared, int) or isinstance(declared, bool) or declared > spec.seconds:
            raise ValueError(f"document key budget.seconds is {declared!r}; it must be an integer at most the cap of {spec.seconds}")
    document["task"] = spec.task
    document["model"] = spec.route.block(spec.reasoning_effort)
    return document, placement


def command_line(spec: FoeSpec, config: Path) -> list[str]:
    return [str(spec.binary), "--config", str(config), "--log-dir", str(spec.log_dir), "--viewer", "off"]


def outcome_line(stdout: str) -> dict[str, Any] | None:
    """The last line of standard output that is a JSON object with `kind`, or None."""
    for line in reversed(stdout.splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            value = json.loads(text)
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get("kind"), str):
            return value
    return None


def learned_claims(value: Any) -> list[str]:
    """The `claim` of every item of a value's `learned` member, when the value has one."""
    if not isinstance(value, dict) or not isinstance(value.get("learned"), list):
        return []
    return [str(item["claim"]) for item in value["learned"] if isinstance(item, dict) and "claim" in item]


def stderr_tail(stderr: str) -> str:
    text = stderr.strip()
    return text[-STDERR_TAIL_CHARACTERS:]


def interpret(outcome: dict[str, Any] | None, killed: bool, exit_status: int | None, stderr: str, cap: int) -> tuple[dict[str, Any], Any]:
    """The reported outcome and candidate for what the run printed.

    A terminated run is `killed` whatever it printed. A run that printed no
    outcome line `failed`, with its exit status and the end of its standard
    error as the evidence. Otherwise the outcome kind is the status: a
    blocked code or exhausted limit is the code, and the evidence is the
    completed value's learned claims, the blocked message, the exhausted
    limit, or the failure text.
    """
    if killed:
        return reported(KILLED, None, [f"the run exceeded the cap of {cap} seconds and was terminated"]), None
    if outcome is None:
        evidence = [f"no outcome line on standard output; exit status {exit_status}"]
        tail = stderr_tail(stderr)
        if tail:
            evidence.append(tail)
        return reported(FAILED, None, evidence), None
    kind = outcome["kind"]
    if kind == COMPLETED:
        value = outcome.get("value")
        return reported(COMPLETED, None, learned_claims(value)), value
    if kind == BLOCKED:
        message = outcome.get("message")
        return reported(BLOCKED, str(outcome.get("code")), [str(message)] if message else []), None
    if kind == EXHAUSTED:
        limit = str(outcome.get("limit"))
        return reported(EXHAUSTED, limit, [f"the {limit} budget was exhausted"]), None
    if kind == FAILED:
        return reported(FAILED, None, [str(outcome.get("error") or "")]), None
    return reported(FAILED, None, [f"outcome kind {kind!r} is not one of {', '.join(OUTCOME_KINDS)}"]), None


def terminate_group(process: subprocess.Popen[bytes]) -> None:
    """End the run: SIGINT, SIGTERM, and SIGKILL to its process group, each after a grace period.

    `process` leads its own process group. SIGINT is first because it is the
    signal foe handles: foe cancels the episode, ends the tool process groups
    it started, which lead groups of their own, and closes the log. The later
    signals end a foe that did not.
    """
    for signum in TERMINATION_SIGNALS:
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue


def _drain(stream: Any, into: list[bytes]) -> None:
    """Append each chunk the pipe delivers, so that output is kept even when the pipe never closes."""
    with stream:
        while chunk := stream.read1(65536):
            into.append(chunk)


def execute(command: list[str], cwd: Path, timeout: float) -> tuple[int | None, bool, bytes, bytes]:
    """Run `command` in a process group of its own and capture what it wrote.

    Returns the exit status, or None when the run was terminated; whether
    it was terminated because `timeout` seconds passed; and the bytes of
    standard output and standard error. The pipes are read by threads
    rather than `communicate`, so that a descendant that left the process
    group and kept a pipe open cannot hold the arm after the process itself
    has exited; what such a descendant writes after the grace period is
    lost.
    """
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    out: list[bytes] = []
    err: list[bytes] = []
    readers = [threading.Thread(target=_drain, args=(process.stdout, out), daemon=True), threading.Thread(target=_drain, args=(process.stderr, err), daemon=True)]
    for reader in readers:
        reader.start()
    killed = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        killed = True
        terminate_group(process)
    # A reader still blocked after the grace period holds the pipe for a
    # descendant that outlived the run; it closes the pipe itself at the end.
    for reader in readers:
        reader.join(timeout=TERMINATION_GRACE_SECONDS)
    return (None if killed else process.returncode), killed, b"".join(out), b"".join(err)


def run(spec: FoeSpec) -> ArmResult:
    """Run the task under foe and return what it reported; see the module docstring."""
    binary = Path(spec.binary)
    if not os.access(binary, os.X_OK):
        raise FileNotFoundError(f"foe binary {binary} is not an executable file")
    if not Path(spec.workspace).is_dir():
        raise FileNotFoundError(f"workspace {spec.workspace} is not a directory")
    spec.artifacts.mkdir(parents=True, exist_ok=True)
    spec.log_dir.mkdir(parents=True, exist_ok=True)
    document, placement = prepare_document(spec)
    config = spec.artifacts / CONFIG_NAME
    config.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    command = command_line(spec, config)
    timeout = spec.seconds + TIMEOUT_MARGIN_SECONDS

    started_ms = now_ms()
    exit_status, killed, out, err = execute(command, spec.workspace, timeout)
    ended_ms = now_ms()

    stdout, stderr = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    (spec.artifacts / STDOUT_NAME).write_text(stdout, encoding="utf-8")
    (spec.artifacts / STDERR_NAME).write_text(stderr, encoding="utf-8")
    episode = foe_build.announced_log_dir(stderr, spec.log_dir)
    outcome = outcome_line(stdout)
    result, candidate = interpret(outcome, killed, exit_status, stderr, timeout)
    record = {
        "harness": HARNESS,
        "commands": [command],
        "cwd": str(spec.workspace),
        "codex_home": None,
        "config": str(config),
        "log_dir": str(spec.log_dir),
        "episode_dir": str(episode),
        "episode_log_present": (episode / "episode.jsonl").is_file(),
        "stdout": str(spec.artifacts / STDOUT_NAME),
        "stderr": str(spec.artifacts / STDERR_NAME),
        "model": document["model"],
        "reasoning_effort_applied": "reasoning_effort" in document["model"],
        "workspace_placement": placement,
        "cap_seconds": spec.seconds,
        "timeout_seconds": timeout,
        "killed": killed,
        "exit_status": exit_status,
        "outcome": outcome,
    }
    return ArmResult(spec.arm_name, HARNESS, started_ms, ended_ms, exit_status, result, candidate, spec.artifacts, record)
