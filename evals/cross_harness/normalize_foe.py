#!/usr/bin/python3
"""Reduce a foe episode log tree to the cross-harness trajectory schema.

`normalize` reads the root episode's `episode.jsonl` and, recursively, the
log of every child episode under `children/`, and returns one `Trajectory`
as `trajectory.py` defines it. docs/log-format.md specifies every event
this module reads; the mapping is:

- one `ModelCall` per `model/request`, closed by the `assistant/message`
  that answers the same request id, which supplies the usage;
- one `Command` per `bash` call and per `session` start opened in an
  `assistant/message` or a `tool/inner-call`. A `bash` command is closed by
  its `tool/result`; a session command by the first `poll` or `stop` result
  that reports the process group ended. The closing result supplies the
  exit code and the possible permission denial the tool inferred;
- one `FileChange` per successful `edit` result, `create` when the call's
  single edit had an empty `old_text` and `edit` otherwise;
- one `Compaction` per `compaction/end` with `ok` true, carrying the
  projected token count of the matching `compaction/start`;
- one `ToolCall` per tool call an `assistant/message` or a
  `tool/inner-call` opens, closed by the `tool/result` with its call id,
  which supplies the end and the error flag. The arguments reach the record
  as a digest alone. The summary carries the one fact the comparison reads
  from that tool: the code a `block` call named, the finding count and exit
  status of a call to the verifier, and the bare path a `read`, `grep`, or
  `edit` call acted on. A summary states the finding count before any other
  number, so that a reader taking the first integer of a summary reads the
  count rather than an exit status, and a summary that carries a path
  carries the path alone, because a reader compares it against a directory
  prefix;
- one `ToolCall` named `verification/result` per `verification/result`
  event, whose summary is the finding count and the status. The runtime
  writes that event for every authoritative verifier invocation and the
  model never sees it, so it is the record of whether the verifier
  accepted; a reader counting verifier firings counts that name beside
  `VERIFIER_TOOL`, because the completion gate fires the verifier without
  any call of the tool;
- the outcome from `episode/end`, with the error text of a `failed`
  outcome as its value, and `killed` when the log has none, because a log
  that stops without that event was cut short;
- the identity from `episode/start`: the runtime build, the contract
  fingerprint, the sandbox, and the model block the contract or the first
  request header names, with its options other than credential paths.

The reader accepts log format version 3 alone and refuses a log whose first
event states another version. A log whose last line has no terminating
newline and is not JSON was cut short between two appends; the reader
drops that line, and the outcome is `killed` because no `episode/end`
follows it.

`trace_conformance` runs `evals/trace_quality.py` over the same tree and
returns its parsed report, which the comparison states for the foe arm
alone since nothing equivalent exists for the other harness. The run is
bounded by `TRACE_QUALITY_TIMEOUT_SECONDS`, so one tree the script cannot
finish reading stops that attempt's conformance column rather than the run.

    /usr/bin/python3 evals/cross_harness/normalize_foe.py EPISODE_DIR [--conformance] [--pretty]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
EVALS = HERE.parent
for directory in (HERE, HERE / "contracts"):
    sys.path.insert(0, str(directory))

import graphs  # noqa: E402
from trajectory import Agent, Command, Compaction, FileChange, ModelCall, Outcome, ToolCall, Trajectory, arguments_digest  # noqa: E402

LOG_NAME = "episode.jsonl"
CHILDREN_DIR = "children"
TRACE_QUALITY = EVALS / "trace_quality.py"
PYTHON = "/usr/bin/python3"

# How long `trace_quality.py` may run over one episode tree. The script
# reads the logs of a finished run, which takes seconds, so a run that
# reaches this bound is not making progress, and the attempt records a
# conformance report it could not obtain rather than holding the whole
# evaluation open.
TRACE_QUALITY_TIMEOUT_SECONDS = 300

# The name this evaluation's foe documents give the executable verifier,
# both in `tool_defs` and in `done_when.verify`; contracts/graphs.py writes
# it, and this module reads it from there so that renaming it there renames
# it here. A call the model issues to that name is a verification the model
# asked for, and its result states the findings and the exit status.
VERIFIER_TOOL = graphs.CHECK

# The name recorded for a `verification/result` event. The runtime writes
# one per authoritative verifier invocation, whatever tool the contract
# named, so the recorded name is the event's rather than the tool's, and a
# reader counting verifier firings counts this name beside the tool's.
VERIFICATION_NAME = "verification/result"

# The tool by which the model stops a run with a stated code.
BLOCK_TOOL = "block"

# The coding tools whose one relevant argument is the path they act on.
# Each takes it under the key `path`, per docs/tools.md.
PATH_TOOLS = ("read", "grep", "edit")

# The `provider` values a foe contract's model block may carry that reach the
# model through a vendor subscription. The strings are foe configuration
# identifiers from docs/models.md, matched literally against the log. Every
# other provider is an API endpoint, which the schema calls the compatible
# route.
SUBSCRIPTION_PROVIDERS = ("openai-codex",)

# The model-block options from docs/models.md that name a credential file on
# the host that ran the episode. They identify no model behaviour, so the
# identity omits them and keeps every other option.
CREDENTIAL_OPTIONS = ("api_key_file", "token_file", "credentials_file")

# The log format version this module reads, per docs/log-format.md. A first
# event that states no version is a version 3 log.
LOG_VERSION = 3

OUTCOME_KINDS = {"completed", "blocked", "exhausted", "failed"}


def read_events(log: Path) -> list[dict[str, Any]]:
    """The events of one log in file order.

    The first event's stated format version is checked before any later
    line is parsed, and a version other than `LOG_VERSION` is refused with
    an error naming both. A last line that has no terminating newline and is
    not JSON is the residue of an append the process did not finish, so it
    is dropped. The error names the log and the line for any other line that
    is not a JSON object, and names the log for a first event that is not
    `episode/start`.
    """
    try:
        text = log.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{log}: the log cannot be read: {exc}") from exc
    lines = text.splitlines()
    last_line_complete = text.endswith("\n")
    events: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            if number == len(lines) and not last_line_complete:
                break
            raise ValueError(f"{log}:{number}: the line is not JSON: {exc.msg}") from exc
        if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
            raise ValueError(f"{log}:{number}: the event is not an object with a data object")
        if not events:
            _check_version(log, number, event)
        for key in ("seq", "time"):
            if isinstance(event.get(key), bool) or not isinstance(event.get(key), int):
                raise ValueError(f"{log}:{number}: {key} is not an integer")
        if not isinstance(event.get("type"), str):
            raise ValueError(f"{log}:{number}: type is not a string")
        events.append(event)
    if not events or events[0]["type"] != "episode/start":
        raise ValueError(f"{log}: the first event is not episode/start")
    return events


def _check_version(log: Path, number: int, first: dict[str, Any]) -> None:
    if "version" not in first:
        return
    stated = first["version"]
    if isinstance(stated, bool) or not isinstance(stated, int):
        raise ValueError(f"{log}:{number}: version is not an integer")
    if stated != LOG_VERSION:
        raise ValueError(f"{log}:{number}: the log states format version {stated}, and this reader reads version {LOG_VERSION}")


def route_for(provider: str | None) -> str:
    return "subscription" if provider in SUBSCRIPTION_PROVIDERS else "compatible"


def _model_named(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The model block of the contract, or of the first request header when the contract names none."""
    contract = events[0]["data"].get("contract")
    model = contract.get("model") if isinstance(contract, dict) else None
    if isinstance(model, dict):
        return model
    for event in events:
        if event["type"] == "request/header" and isinstance(event["data"].get("model"), dict):
            return event["data"]["model"]
    return {}


def identity_of(events: list[dict[str, Any]]) -> dict[str, Any]:
    """What ran, as `episode/start` and the first request header record it."""
    start = events[0]["data"]
    runtime = start.get("runtime") if isinstance(start.get("runtime"), dict) else {}
    sandbox = start.get("sandbox") if isinstance(start.get("sandbox"), dict) else {}
    contract = start.get("contract") if isinstance(start.get("contract"), dict) else {}
    model = _model_named(events)
    options = {key: value for key, value in model.items() if key not in ("provider", "model") and key not in CREDENTIAL_OPTIONS}
    return {
        "episode_id": start.get("id"),
        "runtime_version": runtime.get("version"),
        "runtime_build": runtime.get("build"),
        "contract_name": contract.get("name"),
        "contract_fingerprint": start.get("contract_fingerprint"),
        "model_provider": model.get("provider"),
        "model": model.get("model"),
        "reasoning_effort": model.get("reasoning_effort"),
        "model_options": options,
        "sandbox_mode": sandbox.get("mode"),
        "landlock_abi": sandbox.get("landlock_abi"),
    }


def outcome_of(events: list[dict[str, Any]], log: Path) -> Outcome:
    """The outcome the last `episode/end` states, or `killed` when the log has none."""
    end = events[-1]
    if end["type"] != "episode/end":
        return Outcome(status="killed")
    outcome = end["data"].get("outcome")
    if not isinstance(outcome, dict) or outcome.get("kind") not in OUTCOME_KINDS:
        raise ValueError(f"{log}: episode/end at seq {end['seq']}: outcome.kind is not one of {', '.join(sorted(OUTCOME_KINDS))}")
    kind = outcome["kind"]
    code = None
    value = None
    if kind == "blocked":
        code = outcome.get("code")
    elif kind == "exhausted":
        code = outcome.get("limit")
    elif kind == "completed":
        value = outcome.get("value")
    elif kind == "failed":
        value = outcome.get("error")
    return Outcome(status=kind, code=code, value=value, ended_ms=end["time"])


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usage(data: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None, None, None
    return _int_or_none(usage.get("input")), _int_or_none(usage.get("output")), _int_or_none(usage.get("cache_read"))


def model_calls_of(events: list[dict[str, Any]]) -> list[ModelCall]:
    """One call per `model/request`, in log order, with the usage of the message that answered it.

    A summarization request, whose id starts with `cmp_`, is a model call
    like any other because the budget charges it. A request that no message
    answered, because it failed or the log was cut short, keeps no end and
    no usage. A retried request id names the latest request with that id.
    """
    calls: list[ModelCall] = []
    open_by_id: dict[str, ModelCall] = {}
    for event in events:
        data = event["data"]
        if event["type"] == "model/request":
            call = ModelCall(seq=event["seq"], started_ms=event["time"], ended_ms=None, input_tokens=None, output_tokens=None, cache_read_tokens=None, reasoning_tokens=None)
            calls.append(call)
            open_by_id[str(data.get("request_id"))] = call
        elif event["type"] == "assistant/message":
            call = open_by_id.pop(str(data.get("request_id")), None)
            if call is None:
                continue
            call.ended_ms = event["time"]
            call.input_tokens, call.output_tokens, call.cache_read_tokens = _usage(data)
    return calls


def _opened_calls(event: dict[str, Any]) -> list[dict[str, Any]]:
    """The tool calls one event opens: every call of a message, or the one inner call."""
    data = event["data"]
    if event["type"] == "assistant/message":
        calls = data.get("tool_calls")
        return [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []
    if event["type"] == "tool/inner-call":
        return [data]
    return []


def _is_denial(data: dict[str, Any]) -> bool:
    value = data.get("value")
    if isinstance(value, dict) and value.get("permission_denial") == "possible":
        return True
    failure = data.get("failure")
    return bool(data.get("is_error")) and isinstance(failure, dict) and failure.get("code") == "capability-denied"


def _args_of(call: dict[str, Any]) -> dict[str, Any]:
    return call.get("args") if isinstance(call.get("args"), dict) else {}


def commands_and_changes(events: list[dict[str, Any]]) -> tuple[list[Command], list[FileChange]]:
    """Every `bash` command, every `session` start, and every successful `edit`, each in the order its call was opened.

    A command starts when its result's `duration_ms` says it did, and at the
    opening event when the result carries none or never came. A `bash`
    command ends with its result. A session command ends with the first
    `poll` or `stop` result for its session id whose value does not state
    `alive` true, which is the result that carries the leader's exit code
    and the denial inference; a session that no such result closed has no
    end and no exit code, which is the state of a session the episode's
    settlement stopped, because the settlement result names no call.
    """
    opened: dict[str, tuple[dict[str, Any], int]] = {}
    commands: list[Command] = []
    command_by_id: dict[str, Command] = {}
    session_by_id: dict[str, Command] = {}
    changes: list[FileChange] = []
    for event in events:
        for call in _opened_calls(event):
            call_id = str(call.get("id") if event["type"] == "assistant/message" else call.get("call_id"))
            opened[call_id] = (call, event["time"])
            args = _args_of(call)
            if call.get("name") == "bash" or (call.get("name") == "session" and args.get("action") == "start"):
                command = Command.from_text(event["time"], None, str(args.get("command", "")), None)
                commands.append(command)
                command_by_id[call_id] = command
        if event["type"] != "tool/result":
            continue
        data = event["data"]
        call_id = str(data.get("call_id"))
        call, opened_ms = opened.get(call_id, ({}, event["time"]))
        args = _args_of(call)
        value = data.get("value") if isinstance(data.get("value"), dict) else {}
        command = command_by_id.pop(call_id, None)
        if command is not None:
            duration = _int_or_none(data.get("duration_ms"))
            command.started_ms = max(opened_ms, event["time"] - duration) if duration is not None else opened_ms
            if data.get("name") == "session" and not data.get("is_error"):
                session_by_id[str(value.get("session"))] = command
            else:
                command.ended_ms = event["time"]
                command.exit_code = None if data.get("is_error") else _int_or_none(value.get("exit_code"))
                command.denial = _is_denial(data)
        elif data.get("name") == "session" and args.get("action") in ("poll", "stop") and not data.get("is_error") and value.get("alive") is not True:
            command = session_by_id.pop(str(args.get("session")), None)
            if command is not None:
                command.ended_ms = event["time"]
                command.exit_code = _int_or_none(value.get("exit_code"))
                command.denial = _is_denial(data)
        if data.get("name") == "edit" and not data.get("is_error"):
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            edits = args.get("edits") if isinstance(args.get("edits"), list) else []
            created = len(edits) == 1 and isinstance(edits[0], dict) and edits[0].get("old_text") == ""
            path = value.get("path") if isinstance(value.get("path"), str) else args.get("path")
            if isinstance(path, str):
                changes.append(FileChange(path=path, kind="create" if created else "edit", at_ms=event["time"], via="tool"))
    return commands, changes


def _findings_phrase(count: int) -> str:
    return "1 finding" if count == 1 else f"{count} findings"


def _printed_findings(value: dict[str, Any]) -> int:
    """How many findings a verifier call printed.

    docs/config.md states the convention every verifier follows: one
    finding per line on standard output, and an empty output is acceptance.
    A blank line states no finding and is not counted.
    """
    stdout = value.get("stdout")
    if not isinstance(stdout, str):
        return 0
    return sum(1 for line in stdout.splitlines() if line.strip())


def _summary_from_args(name: str, args: dict[str, Any]) -> str | None:
    """The short fact a call's own arguments state, for the tools this evaluation reads.

    A `block` summary is the code alone and a path tool's summary is the
    path alone, because a reader takes the whole summary as the value of
    the one field it reads from that tool.
    """
    if name == BLOCK_TOOL and isinstance(args.get("code"), str):
        return args["code"]
    if name in PATH_TOOLS and isinstance(args.get("path"), str):
        return args["path"]
    return None


def _summary_from_result(name: str, value: dict[str, Any]) -> str | None:
    """The short fact a call's result states, which replaces the one its arguments stated.

    A `block` result restates the code, a path tool's result names the path
    the tool resolved, and a verifier result carries the findings the
    executable printed and its exit status. The finding count leads: the
    documented verifier convention holds the exit status at zero whether or
    not findings were printed, so a reader that takes the first integer of
    the summary as the count reads acceptance from a leading exit status. A
    result that carries none of these, such
    as an error result whose value is empty, states nothing short and leaves
    the argument summary in place.
    """
    if name == BLOCK_TOOL and isinstance(value.get("code"), str):
        return value["code"]
    if name in PATH_TOOLS and isinstance(value.get("path"), str):
        return value["path"]
    if name == VERIFIER_TOOL and "exit_code" in value:
        exit_code = _int_or_none(value.get("exit_code"))
        status = "none" if exit_code is None else str(exit_code)
        return f"{_findings_phrase(_printed_findings(value))}, exit {status}"
    return None


def _verification_call(event: dict[str, Any]) -> ToolCall:
    """The runtime's own verifier invocation as a tool call.

    The record carries the event's name rather than the tool's, so that the
    invocations the runtime made on its own stay distinct from the calls the
    model issued; a reader counting verifier firings counts both names, as
    `report.py` does.

    The summary leads with the finding count, as a verifier call's does,
    and states the status after it. A `failed` verification judged nothing,
    and its summary is the status alone, so that a reader finding no count
    falls back to the error flag rather than reading the empty finding list
    as acceptance.

    The event names no start, so the start is its `duration_ms` before it,
    or the event itself when the duration is absent. `candidate_sha256` is
    the digest of the canonical JSON of the candidate the verifier judged,
    which is what this record states as the arguments digest.
    """
    data = event["data"]
    duration = _int_or_none(data.get("duration_ms"))
    findings = data.get("findings")
    status = data.get("status") if isinstance(data.get("status"), str) else "unknown"
    digest = data.get("candidate_sha256")
    return ToolCall(
        name=VERIFICATION_NAME,
        started_ms=event["time"] - duration if duration is not None else event["time"],
        ended_ms=event["time"],
        is_error=status == "failed",
        arguments_digest=digest if isinstance(digest, str) else None,
        summary=status if status == "failed" else f"{_findings_phrase(len(findings) if isinstance(findings, list) else 0)}, {status}",
    )


def tool_calls_of(events: list[dict[str, Any]]) -> list[ToolCall]:
    """Every tool call the log opens and every verification it records, in event order.

    A call opened by an `assistant/message` or a `tool/inner-call` is
    closed by the `tool/result` that carries its call id, which supplies
    the end time and the error flag; a call that no result closed keeps no
    end, because nothing judged it. The arguments reach the record as
    `arguments_digest` alone, and a call whose `args` is absent or is not
    an object carries no digest, because its arguments are unknown rather
    than empty.
    """
    calls: list[ToolCall] = []
    open_by_id: dict[str, ToolCall] = {}
    for event in events:
        for call in _opened_calls(event):
            call_id = str(call.get("id") if event["type"] == "assistant/message" else call.get("call_id"))
            name = str(call.get("name"))
            args = call.get("args")
            record = ToolCall(
                name=name,
                started_ms=event["time"],
                ended_ms=None,
                is_error=False,
                arguments_digest=arguments_digest(args) if isinstance(args, dict) else None,
                summary=_summary_from_args(name, args if isinstance(args, dict) else {}),
            )
            calls.append(record)
            open_by_id[call_id] = record
        if event["type"] == "verification/result":
            calls.append(_verification_call(event))
            continue
        if event["type"] != "tool/result":
            continue
        data = event["data"]
        record = open_by_id.pop(str(data.get("call_id")), None)
        if record is None:
            continue
        record.ended_ms = event["time"]
        record.is_error = bool(data.get("is_error"))
        value = data.get("value") if isinstance(data.get("value"), dict) else {}
        summary = _summary_from_result(record.name, value)
        if summary is not None:
            record.summary = summary
    return calls


def compactions_of(events: list[dict[str, Any]]) -> list[Compaction]:
    """One record per completed compaction, with the projection that triggered it."""
    projected_by_step: dict[Any, int | None] = {}
    compactions: list[Compaction] = []
    for event in events:
        data = event["data"]
        if event["type"] == "compaction/start":
            projected_by_step[data.get("step")] = _int_or_none(data.get("projected_tokens"))
        elif event["type"] == "compaction/end" and data.get("ok") is True:
            compactions.append(Compaction(at_ms=event["time"], tokens_before=projected_by_step.get(data.get("step"))))
    return compactions


def agent_of(events: list[dict[str, Any]], depth: int, role: str) -> Agent:
    start = events[0]
    ended = events[-1]["time"] if events[-1]["type"] == "episode/end" else None
    commands, changes = commands_and_changes(events)
    return Agent(
        id=str(start["data"].get("id")),
        parent_id=start["data"].get("parent_id") if isinstance(start["data"].get("parent_id"), str) else None,
        depth=depth,
        role=role,
        started_ms=start["time"],
        ended_ms=ended,
        model_calls=model_calls_of(events),
        commands=commands,
        file_changes=changes,
        compactions=compactions_of(events),
        tool_calls=tool_calls_of(events),
    )


def _child_logs(episode_dir: Path) -> list[Path]:
    children = episode_dir / CHILDREN_DIR
    if not children.is_dir():
        return []
    logs = []
    try:
        entries = sorted(children.iterdir())
        for child in entries:
            if not child.is_dir():
                continue
            log = child / LOG_NAME
            if not log.is_file():
                raise ValueError(f"{child}: the child episode directory has no {LOG_NAME}")
            logs.append(log)
    except OSError as exc:
        raise ValueError(f"{children}: the children directory cannot be listed: {exc}") from exc
    return logs


def _read_episode(episode_dir: Path) -> list[dict[str, Any]]:
    log = episode_dir / LOG_NAME
    if not log.is_file():
        raise ValueError(f"{episode_dir}: the episode directory has no {LOG_NAME}")
    return read_events(log)


def _collect(episode_dir: Path, events: list[dict[str, Any]], depth: int, parent_id: str | None, agents: list[Agent]) -> None:
    """Append the agent of the events read from `episode_dir` and of every descendant.

    Children follow their parent in the order they started. Each child log
    is read once, here, and its events are passed down. A child log whose
    `parent_id` differs from the directory that holds it names a misplaced
    log, which is refused rather than attributed to the wrong parent.
    """
    start = events[0]["data"]
    if parent_id is not None and start.get("parent_id") != parent_id:
        raise ValueError(f"{episode_dir / LOG_NAME}: episode/start.parent_id is {start.get('parent_id')!r}, and the directory holding it belongs to {parent_id!r}")
    contract = start.get("contract") if isinstance(start.get("contract"), dict) else {}
    role = "root" if depth == 0 else str(contract.get("name") or "child")
    agent = agent_of(events, depth, role)
    agents.append(agent)
    children = [(_read_episode(log.parent), log) for log in _child_logs(episode_dir)]
    for child_events, child_log in sorted(children, key=lambda item: (item[0][0]["time"], str(item[1]))):
        _collect(child_log.parent, child_events, depth + 1, agent.id, agents)


def normalize(episode_dir: Path, route: str | None = None) -> Trajectory:
    """The trajectory of the episode recorded under `episode_dir` and its descendants.

    `route` overrides the route derived from the model provider, for a run
    whose log names a provider the derivation does not settle.
    """
    episode_dir = Path(episode_dir)
    agents: list[Agent] = []
    events = _read_episode(episode_dir)
    _collect(episode_dir, events, 0, None, agents)
    identity = identity_of(events)
    return Trajectory(
        harness="foe",
        identity=identity,
        agents=agents,
        outcome=outcome_of(events, episode_dir / LOG_NAME),
        route=route if route is not None else route_for(identity["model_provider"]),
    )


def trace_conformance(episode_dir: Path, trace_quality: Path = TRACE_QUALITY, timeout_seconds: int = TRACE_QUALITY_TIMEOUT_SECONDS) -> dict[str, Any]:
    """The report `trace_quality.py` prints for the tree under `episode_dir`.

    The script exits 0 for a conformant tree and 1 for one with violations;
    both print a report, and the report's `valid` field says which. Any
    other exit, or output that is not a JSON object, is an error naming the
    script and what it wrote on standard error.

    A script still running after `timeout_seconds` is stopped, and the
    return is `valid` None with an `error` naming the script, the tree, and
    the bound: the shape a caller records for a conformance report it could
    not obtain. The bound exists so that one unreadable tree costs its own
    attempt's conformance column rather than the whole run.
    """
    command = [PYTHON, str(trace_quality), str(episode_dir)]
    bound = "1 second" if timeout_seconds == 1 else f"{timeout_seconds} seconds"
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return {"valid": None, "error": f"{trace_quality}: read no report for {episode_dir} within {bound}, and the script was stopped"}
    if completed.returncode not in (0, 1):
        raise RuntimeError(f"{trace_quality}: exited {completed.returncode} on {episode_dir}: {completed.stderr.strip()}")
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{trace_quality}: printed no JSON report for {episode_dir}: {exc.msg}; stderr: {completed.stderr.strip()}") from exc
    if not isinstance(report, dict) or "valid" not in report:
        raise RuntimeError(f"{trace_quality}: the report for {episode_dir} is not an object with a valid field")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("episode_dir", type=Path, help="the root episode directory, holding episode.jsonl and children/")
    parser.add_argument("--route", choices=("subscription", "compatible"), default=None, help="the route to record instead of the one the model provider implies")
    parser.add_argument("--conformance", action="store_true", help="add the trace_quality report under the conformance key")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON")
    args = parser.parse_args(argv)
    try:
        trajectory = normalize(args.episode_dir, args.route)
        document: dict[str, Any] = {"trajectory": trajectory.to_dict()}
        if args.conformance:
            document["conformance"] = trace_conformance(args.episode_dir)
    except (ValueError, RuntimeError) as exc:
        print(f"normalize foe: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(document, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
