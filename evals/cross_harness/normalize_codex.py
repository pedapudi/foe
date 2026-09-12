#!/usr/bin/python3
"""One Codex CLI run as a trajectory.

A headless Codex run leaves three kinds of record. Every thread writes a
session file under `CODEX_HOME/sessions/YYYY/MM/DD/`, one JSON Lines record
per line, and a child agent writes its own file whose `session_meta` names
the parent thread. `codex exec --json` prints an event stream on stdout,
which the runner saves to a file; its first event names the root thread.
`codex exec -o FILE` writes the final agent message to a file. `normalize`
reads the three and returns the `trajectory.Trajectory` the cross-harness
report scores.

The session files are the source of every per-agent fact, because each of
their records carries a timestamp and the event stream carries none. A
model call is one `token_usage_record`; its end is the record's timestamp
and its start is the previous call's end, or the session start for the
first call. A shell command is one completed `CommandExecution` item, with
the interval the item event records. A file change is one entry of a
completed `FileChange` item. A compaction is one `compacted` record. A tool
call is one completed `CommandExecution`, `McpToolCall`, or `CollabToolCall`
item: the shell, a tool reached over the Model Context Protocol, and a tool
of the collaboration mode. Codex records a file edit as a `FileChange` item
and reading and searching as parts of a turn, so those actions reach the
trajectory as file changes or not at all and never as tool calls. The
tool-call count of this arm therefore covers a narrower population than the
foe arm's, which records every call the model issued, and a comparison of
the two counts reads `tool_calls_by_name` to say which calls each count
holds. The event stream supplies the root thread id and any `turn.failed`
reason.

Codex has no verifier tool and no tool by which the model reports a
blocking condition. The foe evidence those produce, which is whether a
runtime-run verifier fired and accepted and which code a block call named,
therefore has no counterpart here, and a cross-harness comparison of
verification rests on the shell commands each run chose to execute.

The runner may stop a run before Codex exits, for a token or wall-clock
limit. A session file that stop cut in the middle of a record keeps every
record before the cut; the cut line is dropped and the file is listed in
the identity under `truncated_session_files`. Session files of other runs
under the same home are read only as far as their first record, so a
damaged file of another thread does not stop this run's normalization.

The outcome combines the exit status the runner observed with the final
message. An exit status of None means the runner's budget watcher stopped
the run, which is `exhausted` with the crossed limit as its code. A negative
status is a signal the watcher did not send, which is `killed` naming the
signal. Any other nonzero exit is `failed` and names the turn failure or the
exit status. On exit 0 a final message that parses as a JSON object with a
`status` key is the typed result an output schema produced, and its
`status`, `code`, and `evidence` fields fill the outcome; any other final
message means `completed` with the message as the value.

Every time is a millisecond count since the Unix epoch, which is the clock
the session records use.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trajectory  # noqa: E402

# Diagnostics a refused access produces under the Codex sandbox: the
# kernel's EACCES and EPERM texts and a read-only mount. A match counts
# only together with a nonzero exit, because command output is free text:
# a passing test suite or a document about sandboxing carries the same
# phrases while the command itself succeeded.
DENIAL = re.compile(r"Permission denied|Operation not permitted|Read-only file system")

# The change types a `FileChange` item uses, mapped to the schema's kinds.
CHANGE_KINDS = {"add": "create", "update": "edit", "delete": "delete"}

# The completed item types that record one call to a tool, mapped to the
# name the schema records when the item names no tool of its own. A command
# execution is always recorded under the one name, so that a report counts
# every shell command of a run together.
TOOL_CALL_ITEMS = {"CommandExecution": "command_execution", "McpToolCall": "mcp_tool_call", "CollabToolCall": "collab_tool_call"}


def timestamp_ms(text: Any, where: str) -> int:
    """The epoch milliseconds of an ISO 8601 timestamp such as `2026-09-11T16:13:09.148Z`."""
    if not isinstance(text, str):
        raise ValueError(f"{where}: {text!r} is not a timestamp string")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{where}: {text!r} is not an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return round(parsed.timestamp() * 1000)


@dataclass
class SessionHead:
    """What the first record of a session file states: the thread and its parent."""

    path: Path
    thread_id: str
    parent_id: str | None
    depth: int
    agent_path: str | None
    meta: dict[str, Any]


@dataclass
class Session:
    """One session file: the thread it records and its parsed records.

    `truncated` is true when the file's last line was cut before its
    newline and did not parse, which is what a run stopped mid-write leaves
    behind; that line is not among `records`.
    """

    path: Path
    thread_id: str
    parent_id: str | None
    depth: int
    agent_path: str | None
    meta: dict[str, Any]
    records: list[dict[str, Any]]
    truncated: bool = False

    @property
    def head(self) -> SessionHead:
        return SessionHead(self.path, self.thread_id, self.parent_id, self.depth, self.agent_path, self.meta)

    @property
    def started_ms(self) -> int:
        return timestamp_ms(self.records[0].get("timestamp"), f"{self.path}:1 timestamp")

    @property
    def ended_ms(self) -> int:
        return timestamp_ms(self.records[-1].get("timestamp"), f"{self.path}:{len(self.records)} timestamp")


def _parse_records(path: Path, text: str, first_only: bool) -> tuple[list[dict[str, Any]], bool]:
    """The records of a session file's text, and whether a cut final line was dropped.

    A line is cut when it is the last one, has no terminating newline, and
    is not JSON; a record's `payload` must be an object or null so that
    every reader can index it.
    """
    lines = text.splitlines()
    unterminated = bool(text) and not text.endswith("\n")
    records: list[dict[str, Any]] = []
    truncated = False
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            if unterminated and number == len(lines):
                truncated = True
                break
            raise ValueError(f"{path}:{number}: the line is not JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{number}: the record is not an object")
        if record.get("payload") is not None and not isinstance(record["payload"], dict):
            raise ValueError(f"{path}:{number}: payload {record['payload']!r} is not an object")
        records.append(record)
        if first_only:
            break
    return records, truncated


def _head_of(path: Path, record: dict[str, Any]) -> SessionHead:
    if record.get("type") != "session_meta":
        raise ValueError(f"{path}: the first record is not a session_meta")
    meta = record.get("payload") or {}
    thread_id = meta.get("id")
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError(f"{path}: session_meta.payload.id is missing")
    parent_id, depth, agent_path = None, 0, None
    source = meta.get("source")
    spawn = (source.get("subagent") or {}).get("thread_spawn") if isinstance(source, dict) else None
    if isinstance(spawn, dict):
        parent_id = spawn.get("parent_thread_id")
        if not isinstance(parent_id, str) or not parent_id:
            raise ValueError(f"{path}: session_meta.payload.source.subagent.thread_spawn.parent_thread_id is missing")
        depth = spawn.get("depth", 1)
        if isinstance(depth, bool) or not isinstance(depth, int):
            raise ValueError(f"{path}: session_meta.payload.source.subagent.thread_spawn.depth {depth!r} is not an integer")
        agent_path = spawn.get("agent_path") if isinstance(spawn.get("agent_path"), str) else None
    return SessionHead(path, thread_id, parent_id, depth, agent_path, meta)


def read_session(path: Path) -> Session:
    """Parse one session file; its first record is the `session_meta`."""
    records, truncated = _parse_records(path, path.read_text(encoding="utf-8"), first_only=False)
    if not records:
        raise ValueError(f"{path}: the first record is not a session_meta")
    head = _head_of(path, records[0])
    return Session(path, head.thread_id, head.parent_id, head.depth, head.agent_path, head.meta, records, truncated)


def session_head(path: Path) -> SessionHead | None:
    """The first record of a session file, or None when the file holds no complete record."""
    records, _ = _parse_records(path, path.read_text(encoding="utf-8"), first_only=True)
    if not records:
        return None
    return _head_of(path, records[0])


def sessions_under(codex_home: Path) -> list[SessionHead]:
    """The head of every session file under `codex_home/sessions` that holds one, in path order."""
    root = codex_home / "sessions"
    if not root.is_dir():
        raise FileNotFoundError(f"{root}: no sessions directory; the run wrote no session file")
    heads = []
    for path in sorted(root.rglob("*.jsonl")):
        head = session_head(path)
        if head is not None:
            heads.append(head)
    return heads


def run_sessions(heads: list[SessionHead], root_thread_id: str, codex_home: Path) -> list[Session]:
    """The root thread's session and every descendant, fully parsed, parents before children.

    A descendant is linked through `parent_thread_id`. A session under the
    same home that the root did not spawn belongs to another run and is
    left out. Two files for one thread, or a thread that lists itself or an
    ancestor as its parent, are errors that name the file.
    """
    by_id: dict[str, SessionHead] = {}
    for head in heads:
        if head.thread_id in by_id:
            raise ValueError(f"{head.path}: the thread {head.thread_id!r} is also recorded by {by_id[head.thread_id].path}")
        by_id[head.thread_id] = head
    if root_thread_id not in by_id:
        raise ValueError(f"{codex_home / 'sessions'}: no session file records the thread {root_thread_id!r}")
    children: dict[str, list[SessionHead]] = {}
    for head in heads:
        if head.parent_id is not None:
            children.setdefault(head.parent_id, []).append(head)
    parsed: dict[str, Session] = {}

    def session_of(head: SessionHead) -> Session:
        if head.thread_id not in parsed:
            parsed[head.thread_id] = read_session(head.path)
        return parsed[head.thread_id]

    ordered: list[Session] = []
    seen: set[str] = set()
    queue = [by_id[root_thread_id]]
    while queue:
        head = queue.pop(0)
        if head.thread_id in seen:
            raise ValueError(f"{head.path}: the thread {head.thread_id!r} is linked as its own descendant through parent_thread_id")
        seen.add(head.thread_id)
        ordered.append(session_of(head))
        descendants = [session_of(child) for child in children.get(head.thread_id, [])]
        queue.extend(child.head for child in sorted(descendants, key=lambda child: child.started_ms))
    return ordered


def _usage(usage: Any) -> dict[str, int | None]:
    usage = usage if isinstance(usage, dict) else {}

    def count(key: str) -> int | None:
        value = usage.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return {
        "input_tokens": count("input_tokens"),
        "output_tokens": count("output_tokens"),
        "cache_read_tokens": count("cached_input_tokens"),
        "reasoning_tokens": count("reasoning_output_tokens"),
    }


def _total_tokens(usage: Any) -> int | None:
    total = usage.get("total_tokens") if isinstance(usage, dict) else None
    return total if isinstance(total, int) and not isinstance(total, bool) else None


def command_text(command: Any) -> str:
    """The command as one line: the script of a `bash -lc SCRIPT` form, else the joined argument vector."""
    if isinstance(command, str):
        return command
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        return json.dumps(command)
    if len(command) == 3 and command[1] in ("-lc", "-c"):
        return command[2]
    return shlex.join(command)


def model_calls(session: Session) -> list[trajectory.ModelCall]:
    """One call per `token_usage_record`, or per `token_count` event when the file has no usage records.

    The call's `seq` is the record's ordinal, so a report can cite the
    line the count came from.
    """
    records = [(record, (record.get("payload") or {}).get("usage")) for record in session.records if record.get("type") == "token_usage_record"]
    if not records:
        records = [
            (record, ((record.get("payload") or {}).get("info") or {}).get("last_token_usage"))
            for record in session.records
            if record.get("type") == "event_msg" and (record.get("payload") or {}).get("type") == "token_count"
        ]
    calls: list[trajectory.ModelCall] = []
    previous_end = session.started_ms
    for record, usage in records:
        ended = timestamp_ms(record.get("timestamp"), f"{session.path} ordinal {record.get('ordinal')} timestamp")
        calls.append(trajectory.ModelCall(seq=record.get("ordinal") if isinstance(record.get("ordinal"), int) else None, started_ms=previous_end, ended_ms=ended, **_usage(usage)))
        previous_end = ended
    return calls


def _completed_items(session: Session, item_types: tuple[str, ...]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    out = []
    for record in session.records:
        payload = record.get("payload") or {}
        if record.get("type") != "event_msg" or payload.get("type") != "item_completed":
            continue
        item = payload.get("item") or {}
        if item.get("type") in item_types:
            out.append((payload, item))
    return out


def _interval(payload: dict[str, Any], record_where: str) -> tuple[int, int | None]:
    started = payload.get("started_at_ms")
    if isinstance(started, bool) or not isinstance(started, int):
        raise ValueError(f"{record_where}: started_at_ms {started!r} is not an integer")
    ended = payload.get("completed_at_ms")
    return started, ended if isinstance(ended, int) and not isinstance(ended, bool) else None


def commands(session: Session) -> list[trajectory.Command]:
    """One command per completed `CommandExecution` item, with the interval its event records.

    The denial flag needs both a nonzero exit and a refusal diagnostic in
    the output; a command that exited 0 succeeded whatever its output says.
    """
    out = []
    for payload, item in _completed_items(session, ("CommandExecution",)):
        started, ended = _interval(payload, f"{session.path} item {item.get('id')}")
        output = item.get("aggregated_output")
        if not isinstance(output, str):
            output = str(item.get("stdout") or "") + str(item.get("stderr") or "")
        exit_code = item.get("exit_code")
        exit_code = exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else None
        out.append(
            trajectory.Command.from_text(
                started,
                ended,
                command_text(item.get("command")),
                exit_code,
                denial=exit_code != 0 and DENIAL.search(output) is not None,
            )
        )
    return out


def file_changes(session: Session) -> list[trajectory.FileChange]:
    """One change per path of each completed `FileChange` item whose status is `completed`.

    An item whose status is `failed` applied nothing, so it records no change.
    """
    out = []
    for payload, item in _completed_items(session, ("FileChange",)):
        if item.get("status") != "completed":
            continue
        started, ended = _interval(payload, f"{session.path} item {item.get('id')}")
        changes = item.get("changes")
        if not isinstance(changes, dict):
            raise ValueError(f"{session.path} item {item.get('id')}: changes {changes!r} is not an object keyed by path")
        for path, change in changes.items():
            kind = CHANGE_KINDS.get((change or {}).get("type") if isinstance(change, dict) else None, "unknown")
            out.append(trajectory.FileChange(path=path, kind=kind, at_ms=ended if ended is not None else started, via="tool"))
    return out


def _call_name(item: dict[str, Any]) -> str:
    """The name a call item is recorded under.

    An MCP call is named by its server and its tool, and a collaboration
    call by its tool. An item that names no tool, and every command
    execution, is recorded under the name `TOOL_CALL_ITEMS` gives its item
    type, so that a report counts the shell commands of a run together.
    """
    default = TOOL_CALL_ITEMS[item["type"]]
    if item["type"] == "CommandExecution":
        return default
    tool = item.get("tool") if isinstance(item.get("tool"), str) else item.get("name")
    if not isinstance(tool, str) or not tool:
        return default
    server = item.get("server")
    return f"{server}/{tool}" if isinstance(server, str) and server else tool


def _call_arguments(item: dict[str, Any]) -> Any:
    """What the item records as the call's arguments: the command vector and the working directory of a command execution, and the `arguments` of any other call."""
    if item["type"] == "CommandExecution":
        return {"command": item.get("command"), "cwd": item.get("cwd")}
    return item.get("arguments")


def _exit_code(item: dict[str, Any]) -> int | None:
    code = item.get("exit_code")
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def tool_calls(session: Session) -> list[trajectory.ToolCall]:
    """One call per completed command execution, MCP tool call, and collaboration tool call, in record order.

    A call is an error when the harness reports that the call itself did
    not run to completion: a status of `failed` for a call that is not a
    command execution, and for a command execution a status of `failed`
    with no exit status, which is a process that never ran. Codex marks a
    command that exited nonzero as failed, and that exit is a result, so
    it reaches the summary and leaves the error flag false; that is the
    meaning `trajectory.ToolCall` states and the meaning the foe arm
    records, so the flag compares across the two arms. The summary of a
    command execution is its exit status and of any other call the status
    the item states. The arguments reach the record as a digest alone, and
    an item that records no arguments carries no digest.
    """
    out = []
    for payload, item in _completed_items(session, tuple(TOOL_CALL_ITEMS)):
        started, ended = _interval(payload, f"{session.path} item {item.get('id')}")
        status = item.get("status")
        exit_code = _exit_code(item)
        if item["type"] == "CommandExecution":
            summary = f"exit {exit_code if exit_code is not None else 'none'}"
            is_error = status == "failed" and exit_code is None
        else:
            summary = f"status {status}" if isinstance(status, str) else None
            is_error = status == "failed"
        arguments = _call_arguments(item)
        out.append(
            trajectory.ToolCall(
                name=_call_name(item),
                started_ms=started,
                ended_ms=ended,
                is_error=is_error,
                arguments_digest=trajectory.arguments_digest(arguments) if arguments is not None else None,
                summary=summary,
            )
        )
    return out


def compactions(session: Session) -> list[trajectory.Compaction]:
    """One compaction per `compacted` record.

    `tokens_before` is the total of the usage the record carries in
    `latest_token_usage_record`, or, when that field is null, the total of
    the last model response recorded before it, which is the context size
    the response was made with.
    """
    out = []
    last_total: int | None = None
    for record in session.records:
        payload = record.get("payload") or {}
        kind = record.get("type")
        if kind == "token_usage_record":
            last_total = _total_tokens(payload.get("usage"))
        elif kind == "event_msg" and payload.get("type") == "token_count":
            last_total = _total_tokens((payload.get("info") or {}).get("last_token_usage"))
        elif kind == "compacted":
            at = timestamp_ms(record.get("timestamp"), f"{session.path} ordinal {record.get('ordinal')} timestamp")
            carried = _total_tokens((payload.get("latest_token_usage_record") or {}).get("usage"))
            out.append(trajectory.Compaction(at_ms=at, tokens_before=carried if carried is not None else last_total))
    return out


def agent_from_session(session: Session) -> trajectory.Agent:
    role = "root" if session.parent_id is None else (session.agent_path or "/child").rstrip("/").rsplit("/", 1)[-1] or "child"
    return trajectory.Agent(
        id=session.thread_id,
        parent_id=session.parent_id,
        depth=session.depth,
        role=role,
        started_ms=session.started_ms,
        ended_ms=session.ended_ms,
        model_calls=model_calls(session),
        commands=commands(session),
        file_changes=file_changes(session),
        compactions=compactions(session),
        tool_calls=tool_calls(session),
    )


@dataclass
class EventStream:
    """What the saved `codex exec --json` output states about the run.

    `problems` lists the lines that were not event objects, by file and
    line; such a line is recorded and skipped, as the arm skips it.
    """

    thread_id: str
    turns_completed: int
    failures: list[str]
    problems: list[str] = field(default_factory=list)


def read_events(events_path: Path) -> EventStream:
    thread_id = None
    turns = 0
    failures: list[str] = []
    problems: list[str] = []
    for number, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"{events_path}:{number}: the line is not JSON: {exc}")
            continue
        if not isinstance(event, dict):
            problems.append(f"{events_path}:{number}: the line is not an event object")
            continue
        kind = event.get("type")
        if kind == "thread.started":
            if not isinstance(event.get("thread_id"), str):
                raise ValueError(f"{events_path}:{number}: thread.started has no thread_id")
            thread_id = event["thread_id"]
        elif kind == "turn.completed":
            turns += 1
        elif kind == "turn.failed":
            error = event.get("error")
            message = error.get("message") if isinstance(error, dict) else error
            failures.append(str(message) if message is not None else "turn.failed without an error message")
        elif kind == "error":
            failures.append(str(event.get("message") or "error event without a message"))
    if thread_id is None:
        raise ValueError(f"{events_path}: no thread.started event names the root thread")
    return EventStream(thread_id, turns, failures, problems)


def outcome(output_path: Path | None, exit_status: int | None, limit: str | None, failures: list[str], ended_ms: int | None) -> trajectory.Outcome:
    """The outcome the exit status, the crossed limit, the turn failures, and the final message determine."""
    if exit_status is None:
        return trajectory.Outcome("exhausted", code=limit, ended_ms=ended_ms)
    if exit_status < 0:
        return trajectory.Outcome("killed", code=f"signal {-exit_status}", ended_ms=ended_ms)
    if exit_status != 0:
        code = failures[0] if failures else f"exit status {exit_status}"
        return trajectory.Outcome("failed", code=code, ended_ms=ended_ms)
    if failures:
        return trajectory.Outcome("failed", code=failures[0], ended_ms=ended_ms)
    if output_path is None:
        return trajectory.Outcome("completed", ended_ms=ended_ms)
    if not output_path.is_file():
        raise FileNotFoundError(f"{output_path}: the run exited 0 but wrote no final message file")
    text = output_path.read_text(encoding="utf-8")
    typed: Any = None
    try:
        typed = json.loads(text)
    except json.JSONDecodeError:
        typed = None
    if isinstance(typed, dict) and "status" in typed:
        status = typed["status"]
        if status not in trajectory.OUTCOME_STATUSES:
            raise ValueError(f"{output_path}: status {status!r} is not one of {', '.join(trajectory.OUTCOME_STATUSES)}")
        code = typed.get("code")
        if code is not None and not isinstance(code, str):
            raise ValueError(f"{output_path}: code {code!r} is not a string or null")
        return trajectory.Outcome(status, code=code, value=typed.get("evidence"), ended_ms=ended_ms)
    return trajectory.Outcome("completed", value=text.rstrip("\n"), ended_ms=ended_ms)


def identity(root: Session, sessions: list[Session], codex_home: Path, events: EventStream) -> dict[str, Any]:
    """What ran, as the root session records it."""
    context = next((record.get("payload") or {} for record in root.records if record.get("type") == "turn_context"), {})
    settings = (context.get("collaboration_mode") or {}).get("settings") or {}
    sandbox = context.get("sandbox_policy")
    return {
        "cli_version": root.meta.get("cli_version"),
        "model": context.get("model") or settings.get("model"),
        "model_provider": root.meta.get("model_provider"),
        "reasoning_effort": settings.get("reasoning_effort") or context.get("effort"),
        "sandbox_policy": sandbox.get("type") if isinstance(sandbox, dict) else sandbox,
        "approval_policy": context.get("approval_policy"),
        "cwd": root.meta.get("cwd"),
        "thread_id": root.thread_id,
        "codex_home": str(codex_home),
        "session_files": [str(session.path.relative_to(codex_home)) for session in sessions],
        "truncated_session_files": [str(session.path.relative_to(codex_home)) for session in sessions if session.truncated],
        "turns_completed": events.turns_completed,
        "event_problems": list(events.problems),
    }


def normalize(codex_home: Path, events_path: Path, output_path: Path | None, exit_status: int | None, route: str, limit: str | None = None) -> trajectory.Trajectory:
    """The trajectory of the run whose files are under `codex_home`, whose event stream is at `events_path`, and whose final message is at `output_path`.

    `exit_status` is the status `codex exec` exited with, or None when the
    runner's budget watcher stopped it; `limit` then names the dimension
    that crossed its bound, such as `output_tokens` or `seconds`. `route`
    is the route the runner reached the model through, one of
    `trajectory.ROUTES`.
    """
    if route not in trajectory.ROUTES:
        raise ValueError(f"route {route!r} is not one of {', '.join(trajectory.ROUTES)}")
    events = read_events(events_path)
    sessions = run_sessions(sessions_under(codex_home), events.thread_id, codex_home)
    agents = [agent_from_session(session) for session in sessions]
    ended = max(agent.ended_ms for agent in agents if agent.ended_ms is not None)
    return trajectory.Trajectory(
        harness="codex",
        identity=identity(sessions[0], sessions, codex_home, events),
        agents=agents,
        outcome=outcome(output_path, exit_status, limit, events.failures, ended),
        route=route,
    )


def parse_exit_status(text: str) -> int | None:
    if text == "stopped":
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--exit-status {text!r} is neither an integer nor 'stopped'") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--codex-home", required=True, type=Path, help="the CODEX_HOME the run used; its sessions/ tree is read")
    parser.add_argument("--events", required=True, type=Path, help="the saved stdout of codex exec --json")
    parser.add_argument("--last-message", type=Path, default=None, help="the file codex exec -o wrote")
    parser.add_argument("--exit-status", required=True, type=parse_exit_status, help="the exit status of codex exec, or 'stopped' when the budget watcher ended the run")
    parser.add_argument("--limit", default=None, help="the limit dimension the budget watcher reported when it stopped the run")
    parser.add_argument("--route", choices=trajectory.ROUTES, required=True, help="the route the run reached the model through")
    parser.add_argument("--out", type=Path, default=None, help="where the trajectory JSON is written; stdout when omitted")
    args = parser.parse_args(argv)
    try:
        result = normalize(args.codex_home.resolve(), args.events, args.last_message, args.exit_status, args.route, args.limit)
    except (ValueError, FileNotFoundError) as exc:
        print(f"normalize codex: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(result.to_dict(), indent=2)
    if args.out is None:
        print(rendered)
    else:
        args.out.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
