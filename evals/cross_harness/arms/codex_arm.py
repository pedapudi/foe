#!/usr/bin/python3
"""Run one task under Codex CLI and report the outcome it wrote.

This module is the Codex arm of the cross-harness evaluation. It starts
`codex exec` headless in the workspace, under one sandbox mode, one model,
and one reasoning effort, with a JSON output schema that makes the final
message a typed report, and it returns the same `ArmResult` the foe arm
returns.

Codex reports its outcome in the last agent message, which `-o` writes to
`artifacts/last.txt`. The default schema asks for a `status` of `completed`
or `blocked`, a `code` from the three blocked codes a foe model reports
itself, and `evidence` as a list of sentences. A caller may pass another
schema; the arm still reads `status`, `code`, and `evidence` from the
message, and the whole message is the candidate.

Codex enforces no budget of its own, so the run is placed under the budget
watcher in `codex_budget_watcher.py`, which sums the usage the session files
report and terminates the process group when a limit is crossed. A run the
watcher stopped is reported `exhausted` with the crossed dimension as its
code, which is the fact a foe episode states in the same case. A run that
ended by a signal without the watcher's decision is `killed`, and a run
that ended without a readable last message is `failed`.

Codex locates its credential and session files by `CODEX_HOME` and offers
no flag for it, so the arm creates a fresh directory under the artifacts,
copies the caller's credential file into it as `auth.json`, and passes that
directory as the child's `CODEX_HOME`. The copy is removed as soon as the
process has exited, before anything reads the run's output, unless the
caller sets `keep_credential`; the record states whether it was removed.
Nothing in this module reads, prints, or logs the credential's contents.
The child inherits the arm's own environment otherwise, so that the binary
finds its shared libraries and certificates; this module reads no
environment value itself. The record names the directory, every command
line, the event stream, and every session file the run wrote.

A caller may give a config canary, one sentence the arm writes into the
fresh `CODEX_HOME` as `config.toml` under the `developer_instructions` key,
the user configuration file that `--ignore-user-config` states it does not
load. Codex loads `AGENTS.md` from `CODEX_HOME` as global instructions in
every session, and `--ignore-rules` covers execution-policy `.rules` files
alone, so neither is a place Codex must not load. The sentence reaches no
request unless Codex read the file, so the isolation gate of the evaluation
searches the recorded requests for it; the record names the file.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

CROSS_HARNESS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CROSS_HARNESS))
sys.path.insert(0, str(CROSS_HARNESS / "arms"))

import codex_budget_watcher  # noqa: E402
from foe_arm import BLOCKED, COMPLETED, EXHAUSTED, FAILED, KILLED, ArmResult, now_ms, reported, stderr_tail  # noqa: E402

HARNESS = "codex"

SANDBOX_MODES: tuple[str, ...] = ("read-only", "workspace-write", "danger-full-access")

# The blocked codes a model reports itself under foe, from
# docs/log-format.md "Blocked codes"; the runtime-detected codes have no
# counterpart a Codex run could report.
BLOCKED_CODES: tuple[str, ...] = ("goal-unreachable", "ambiguous-task", "missing-capability")

# The schema the final message satisfies. Every property is required and
# `code` admits null, which is the form a strict structured output accepts.
DEFAULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": [COMPLETED, BLOCKED]},
        "code": {"type": ["string", "null"], "enum": [*BLOCKED_CODES, None]},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "code", "evidence"],
    "additionalProperties": False,
}

CODEX_HOME_NAME, CREDENTIAL_NAME = "codex-home", "auth.json"
# The user configuration file under CODEX_HOME that `--ignore-user-config`
# keeps Codex from loading, and the key of it that would reach a request as
# a developer message if the file were loaded.
CONFIG_CANARY_NAME, CONFIG_CANARY_KEY = "config.toml", "developer_instructions"
SCHEMA_NAME, LAST_NAME, EVENTS_NAME, STDERR_NAME = "schema.json", "last.txt", "events.jsonl", "stderr.txt"


@dataclass(frozen=True)
class CodexSpec:
    """Everything one Codex run needs.

    `limits` is what the budget watcher enforces, keyed by its DIMENSIONS.
    `credential_source` is the `auth.json` a Codex login wrote, copied into
    the fresh `CODEX_HOME`. `max_threads` bounds concurrent child agents when
    `agents_enabled` is true and is otherwise unused. `model_providers` maps
    a provider name to its Codex configuration keys, for a run against a
    compatible server; the one name given becomes the run's provider.
    `environment` is the base environment of the child; the arm's own
    environment when None. `keep_credential` leaves the credential copy in
    `CODEX_HOME` after the run; the copy is removed otherwise.
    `config_canary` is the sentence written into `CODEX_HOME` as
    CONFIG_CANARY_NAME before the run; nothing is written when None.
    """

    arm_name: str
    codex: Path
    task: str
    workspace: Path
    artifacts: Path
    sandbox: str
    model: str
    reasoning_effort: str
    credential_source: Path
    limits: Mapping[str, int | float]
    agents_enabled: bool = False
    max_threads: int | None = None
    output_schema: dict[str, Any] | None = None
    model_providers: Mapping[str, Mapping[str, Any]] | None = None
    environment: Mapping[str, str] | None = None
    keep_credential: bool = False
    config_canary: str | None = None

    def __post_init__(self) -> None:
        if self.sandbox not in SANDBOX_MODES:
            raise ValueError(f"spec sandbox is {self.sandbox!r}; expected one of {', '.join(SANDBOX_MODES)}")
        if not self.model:
            raise ValueError("spec model is empty")
        if not self.reasoning_effort:
            raise ValueError("spec reasoning_effort is empty")
        if not self.task.strip():
            raise ValueError("spec task is empty")
        if self.max_threads is not None and (isinstance(self.max_threads, bool) or not isinstance(self.max_threads, int) or self.max_threads <= 0):
            raise ValueError(f"spec max_threads is {self.max_threads!r}; expected a positive integer or None")
        if self.model_providers is not None and len(self.model_providers) != 1:
            raise ValueError(f"spec model_providers names {len(self.model_providers)} providers; a run uses exactly one")
        if self.config_canary is not None and not self.config_canary.strip():
            raise ValueError("spec config_canary is empty; the canary is one sentence, or None to plant none")
        codex_budget_watcher.check_limits(self.limits)

    @property
    def schema(self) -> dict[str, Any]:
        return DEFAULT_SCHEMA if self.output_schema is None else self.output_schema


def toml_value(value: Any, key: str) -> str:
    """The TOML literal a `-c key=value` override carries for a Python value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        # A JSON string literal with non-ASCII characters kept literal is a
        # TOML basic string: both escape the quotation mark, the backslash,
        # and the control characters below U+0020 the same way, and TOML
        # rejects the surrogate pairs that ASCII-only JSON would write for a
        # character outside the Basic Multilingual Plane. U+007F is the one
        # character TOML requires escaped and JSON leaves literal.
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007F")
    raise ValueError(f"override {key} is {type(value).__name__}; expected a string, number, or boolean")


def command_line(spec: CodexSpec, schema: Path, last: Path) -> list[str]:
    """The `codex exec` command the run executes; see the module docstring."""
    command = [
        str(spec.codex),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--ignore-rules",
        "--ignore-user-config",
        "-s",
        spec.sandbox,
        "-m",
        spec.model,
        "-c",
        f"model_reasoning_effort={toml_value(spec.reasoning_effort, 'model_reasoning_effort')}",
        "-c",
        'approval_policy="never"',
        "-C",
        str(spec.workspace),
        "-o",
        str(last),
        "--output-schema",
        str(schema),
    ]
    if not spec.agents_enabled:
        command += ["-c", "agents.enabled=false"]
    elif spec.max_threads is not None:
        command += ["-c", f"agents.max_concurrent_threads_per_session={spec.max_threads}"]
    if spec.model_providers:
        ((name, options),) = spec.model_providers.items()
        for key, value in options.items():
            command += ["-c", f"model_providers.{name}.{key}={toml_value(value, f'model_providers.{name}.{key}')}"]
        command += ["-c", f"model_provider={toml_value(name, 'model_provider')}"]
    # The separator keeps a task text that starts with a dash from being
    # read as an option.
    command += ["--", spec.task]
    return command


def parse_events(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """The JSON Lines events `--json` wrote, and the lines that were not events."""
    events: list[dict[str, Any]] = []
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            problems.append(f"{EVENTS_NAME}:{number}: {exc}")
            continue
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            events.append(value)
        else:
            problems.append(f"{EVENTS_NAME}:{number}: not an event object")
    return events, problems


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The thread, the turn count, the summed usage, every error the event stream reports, and the problems reading it.

    `errors` are what the stream itself reported; `problems` are events of a
    documented type whose content the arm could not read, such as a usage
    field that is not an integer. A problem leaves the summary partial and
    is recorded, because the run has finished by the time it is found.
    """
    usage = codex_budget_watcher.empty_usage()
    thread_id = None
    turns = 0
    errors: list[str] = []
    problems: list[str] = []
    items: dict[str, int] = {}
    for event in events:
        kind = event["type"]
        if kind == "thread.started" and isinstance(event.get("thread_id"), str):
            thread_id = event["thread_id"]
        elif kind == "turn.completed":
            turns += 1
            if isinstance(event.get("usage"), dict):
                try:
                    codex_budget_watcher.add_usage(usage, codex_budget_watcher.parse_usage(event["usage"], f"{EVENTS_NAME} turn.completed"))
                except ValueError as exc:
                    problems.append(str(exc))
        elif kind == "turn.failed":
            turns += 1
            error = event.get("error")
            errors.append(str(error.get("message") if isinstance(error, dict) else error))
        elif kind == "error":
            # A transport failure the stream reports outside any item, such
            # as a reconnection attempt.
            errors.append(str(event.get("message") or event))
        elif kind == "item.completed" and isinstance(event.get("item"), dict):
            item_type = str(event["item"].get("type"))
            items[item_type] = items.get(item_type, 0) + 1
            if item_type == "error":
                errors.append(str(event["item"].get("message") or event["item"]))
    return {"thread_id": thread_id, "turns": turns, "usage": usage, "errors": errors, "items": items, "problems": problems}


def read_last_message(last: Path, schema: dict[str, Any]) -> tuple[dict[str, Any] | None, Any, list[str]]:
    """The reported outcome and candidate the last message holds, and the problems found reading it.

    The reported outcome is None when the file is absent or is not a JSON
    object. A status outside the schema's enumeration is reported as
    `failed`, whatever word the message used, because the statuses the
    schema omits are decided by the watcher or the arm. A code outside a
    blocked status, or outside the schema's enumeration of codes, is
    dropped. Each such case is recorded as a problem, and the message
    itself is the candidate, so that a grader sees what the model said.
    """
    if not last.is_file():
        return None, None, [f"{last} is absent; the run wrote no last message"]
    text = last.read_text(encoding="utf-8")
    try:
        message = json.loads(text)
    except ValueError as exc:
        return None, text, [f"{last}: the last message is not JSON: {exc}"]
    if not isinstance(message, dict):
        return None, message, [f"{last}: the last message is a {type(message).__name__} rather than an object"]
    problems: list[str] = []
    status = message.get("status")
    allowed = schema.get("properties", {}).get("status", {}).get("enum") or [COMPLETED, BLOCKED]
    if status not in allowed:
        problems.append(f"{last}: status is {status!r}; the schema allows {', '.join(map(str, allowed))}")
        status = FAILED
    code = message.get("code")
    codes = schema.get("properties", {}).get("code", {}).get("enum")
    if code is not None and status != BLOCKED:
        problems.append(f"{last}: code {code!r} is set while the status is {status!r} rather than {BLOCKED!r}")
        code = None
    elif code is not None and codes is not None and code not in codes:
        problems.append(f"{last}: code {code!r} is outside the codes the schema allows: {', '.join(map(str, codes))}")
        code = None
    if code is not None:
        code = str(code)
    raw = message.get("evidence")
    if isinstance(raw, list):
        evidence = [str(item) for item in raw]
    elif raw is None:
        evidence = []
    else:
        problems.append(f"{last}: evidence is a {type(raw).__name__} rather than a list")
        evidence = [str(raw)]
    return reported(str(status), code, evidence), message, problems


def interpret(
    stop: codex_budget_watcher.Stop | None,
    exit_status: int | None,
    last: Path,
    schema: dict[str, Any],
    summary: dict[str, Any],
    stderr: str,
) -> tuple[dict[str, Any], Any, list[str]]:
    """The reported outcome, the candidate, and the problems, for how the run ended.

    The watcher's stop takes precedence over anything the run wrote, because
    a message written after the budget was crossed is not an outcome the
    budget allowed. Then a signal is `killed`, a readable last message is
    what it says, and anything else is `failed` with the stream's errors
    and the end of standard error as evidence.
    """
    if stop is not None:
        return reported(EXHAUSTED, stop.dimension, [stop.reason]), None, []
    if exit_status is not None and exit_status < 0:
        return reported(KILLED, None, [f"the run ended by signal {-exit_status}"]), None, []
    outcome, candidate, problems = read_last_message(last, schema)
    if outcome is not None:
        return outcome, candidate, problems
    evidence = list(problems)
    evidence.append(f"exit status {exit_status}")
    evidence.extend(summary["errors"])
    tail = stderr_tail(stderr)
    if tail:
        evidence.append(tail)
    return reported(FAILED, None, evidence), candidate, problems


def prepare_home(spec: CodexSpec) -> Path:
    """A fresh `CODEX_HOME` under the artifacts holding the credential file, and the config canary when the spec gives one."""
    source = Path(spec.credential_source)
    if not source.is_file():
        raise FileNotFoundError(f"credential source {source} is not a file")
    home = spec.artifacts / CODEX_HOME_NAME
    if home.exists():
        raise FileExistsError(f"{home} exists; a run needs a fresh CODEX_HOME, so give each run its own artifacts directory")
    home.mkdir(parents=True)
    shutil.copyfile(source, home / CREDENTIAL_NAME)
    (home / CREDENTIAL_NAME).chmod(0o600)
    if spec.config_canary is not None:
        (home / CONFIG_CANARY_NAME).write_text(config_canary_text(spec.config_canary), encoding="utf-8")
    return home


def config_canary_text(sentence: str) -> str:
    """The `config.toml` that carries the canary sentence as developer instructions."""
    return (
        "# Planted by the cross-harness evaluation. The run passes --ignore-user-config, so this file must never be loaded;\n"
        "# a model request that carries the sentence below shows that it was.\n"
        f"{CONFIG_CANARY_KEY} = {toml_value(sentence.strip(), CONFIG_CANARY_KEY)}\n"
    )


def remove_credential(home: Path) -> Path:
    """Remove the credential copy from `CODEX_HOME` and return its path.

    A copy Codex rewrote during the run, as a token refresh does, is
    removed the same way; a copy already absent leaves nothing to remove.
    A copy that cannot be removed is an error naming the path, because a
    credential left under the artifacts stays readable by whoever reads
    them.
    """
    path = home / CREDENTIAL_NAME
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise OSError(f"the credential copy {path} could not be removed: {exc}") from exc
    return path


def session_files(home: Path) -> list[str]:
    sessions = home / "sessions"
    if not sessions.is_dir():
        return []
    return [str(path) for path in sorted(sessions.rglob("*.jsonl")) if path.is_file()]


def run(spec: CodexSpec) -> ArmResult:
    """Run the task under Codex CLI and return what it reported; see the module docstring."""
    codex = Path(spec.codex)
    if not os.access(codex, os.X_OK):
        raise FileNotFoundError(f"codex binary {codex} is not an executable file")
    if not Path(spec.workspace).is_dir():
        raise FileNotFoundError(f"workspace {spec.workspace} is not a directory")
    spec.artifacts.mkdir(parents=True, exist_ok=True)
    home = prepare_home(spec)
    schema = spec.artifacts / SCHEMA_NAME
    schema.write_text(json.dumps(spec.schema, indent=2) + "\n", encoding="utf-8")
    last = spec.artifacts / LAST_NAME
    # A last message from an earlier run in the same artifacts directory
    # would otherwise be read as this run's outcome when this run writes none.
    last.unlink(missing_ok=True)
    command = command_line(spec, schema, last)
    # The child receives the arm's environment unchanged except for
    # CODEX_HOME; the arm reads none of its values.
    environment = dict(os.environ if spec.environment is None else spec.environment)
    environment["CODEX_HOME"] = str(home)

    started_ms = now_ms()
    try:
        exit_status, stop, out, err = codex_budget_watcher.run_with_watcher(command, home, spec.limits, cwd=spec.workspace, env=environment)
    finally:
        # The process has exited, or the watcher failed and terminated it;
        # either way the credential copy is removed before anything else.
        if not spec.keep_credential:
            remove_credential(home)
    ended_ms = now_ms()

    stdout, stderr = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    (spec.artifacts / EVENTS_NAME).write_text(stdout, encoding="utf-8")
    (spec.artifacts / STDERR_NAME).write_text(stderr, encoding="utf-8")
    events, event_problems = parse_events(stdout)
    summary = summarize_events(events)
    result, candidate, problems = interpret(stop, exit_status, last, spec.schema, summary, stderr)
    record = {
        "harness": HARNESS,
        "commands": [command],
        "cwd": str(spec.workspace),
        "codex_home": str(home),
        "credential_source": str(spec.credential_source),
        "credential_copy": str(home / CREDENTIAL_NAME),
        "credential_removed": not spec.keep_credential,
        "config_canary_file": None if spec.config_canary is None else str(home / CONFIG_CANARY_NAME),
        "schema": str(schema),
        "last_message": str(last),
        "events": str(spec.artifacts / EVENTS_NAME),
        "stderr": str(spec.artifacts / STDERR_NAME),
        "session_files": session_files(home),
        "sandbox": spec.sandbox,
        "model": spec.model,
        "reasoning_effort": spec.reasoning_effort,
        "agents_enabled": spec.agents_enabled,
        "max_threads": spec.max_threads if spec.agents_enabled else None,
        "limits": dict(spec.limits),
        "stop": None if stop is None else stop.to_dict(),
        "thread_id": summary["thread_id"],
        "turns": summary["turns"],
        "usage": summary["usage"],
        "items": summary["items"],
        "errors": summary["errors"],
        "problems": event_problems + summary["problems"] + problems,
        "exit_status": exit_status,
    }
    return ArmResult(spec.arm_name, HARNESS, started_ms, ended_ms, exit_status, result, candidate, spec.artifacts, record)
