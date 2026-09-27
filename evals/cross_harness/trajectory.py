#!/usr/bin/python3
"""One trajectory schema for both harnesses.

A trajectory is what one run of a coding harness did, reduced to the facts
the cross-harness comparison scores: which agents ran and how they nest,
every model call with the usage the harness reported, every shell command
with the paths it named, every tool call with a digest of its arguments,
every file change, every compaction, and the outcome. The foe
arm fills it from an episode log tree and the Codex arm from session files
and event streams; the report reads one shape.

Each record is a dataclass with `to_dict` and `from_dict`, so a trajectory
round-trips through JSON without loss. `from_dict` checks every enumerated
field and names the key and the rule in its error. `Trajectory.totals`
folds the tree into the counts a report states. `attribute_shell_writes`
adds the file changes a shell command made, found by comparing workspace
snapshots taken before and after the run against command intervals.

Every time in this schema is a millisecond count on the same clock as the
harness's own records, and every token count is an integer or None when the
harness reported none.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

HARNESSES = ("foe", "codex")
ROUTES = ("subscription", "compatible")
CHANGE_KINDS = ("edit", "create", "delete", "unknown")
CHANGE_VIAS = ("tool", "shell")
OUTCOME_STATUSES = ("completed", "blocked", "exhausted", "failed", "killed")

# A path token in a shell command: an optional `/`, `./`, `../`, or `~/`
# head, then segments of path characters. The lookbehind refuses a start
# inside a word, after a `$` (a variable name), or after `:` or `/` (the
# scheme and authority of a URL), so `https://host/x` yields nothing. Space
# is a separator, so a quoted path that contains one is cut at the space.
_PATH_TOKEN = re.compile(r"(?<![\w/:.~%@+$-])(?:/|\.\.?/|~/)?[\w.~+@%-]+(?:/[\w.~+@%-]*)*")


def paths_in_command(text: str) -> list[str]:
    """The absolute and workspace-relative paths a command names, in order, once each.

    A token counts as a path when it starts with `/`, `./`, `../`, or `~/`,
    or contains a `/` between path characters. A bare word such as `ls` or
    `README.md` is not a path under this rule, because a report that counted
    every word with a dot would name options and hostnames as files. A
    trailing `.` that ends a sentence is dropped.
    """
    found: list[str] = []
    for match in _PATH_TOKEN.finditer(text):
        token = match.group(0)
        if len(token) > 1 and token.endswith(".") and token[-2] not in "./":
            token = token[:-1]
        if "/" not in token:
            continue
        if token not in found:
            found.append(token)
    return found


def arguments_digest(arguments: Any) -> str:
    """`sha256:` followed by the SHA-256 of the canonical JSON of one call's arguments.

    Canonical JSON is the form docs/log-format.md states for a recorded
    digest: compact, object keys sorted, raw UTF-8. Two calls with equal
    arguments therefore carry one digest, and no argument content reaches
    the record, which keeps a path outside the workspace, a credential, or
    a task's own text out of the trajectory.
    """
    text = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_choice(prefix: str, key: str, value: Any, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise ValueError(f"{prefix}.{key}: {value!r} is not one of {', '.join(choices)}")
    return value


def _require(prefix: str, data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ValueError(f"{prefix}.{key}: the key is required")
    return data[key]


def _optional_int(prefix: str, data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{prefix}.{key}: {value!r} is not an integer or null")
    return value


def _int(prefix: str, data: dict[str, Any], key: str) -> int:
    value = _require(prefix, data, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{prefix}.{key}: {value!r} is not an integer")
    return value


def _optional_str(prefix: str, data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{prefix}.{key}: {value!r} is not a string or null")
    return value


def _list(prefix: str, data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"{prefix}.{key}: {value!r} is not a list")
    return value


@dataclass
class ModelCall:
    """One request to the model and the usage the harness reported for it."""

    seq: int | None
    started_ms: int
    ended_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    reasoning_tokens: int | None

    @property
    def has_usage(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "started_ms": self.started_ms,
            "ended_ms": self.ended_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "reasoning_tokens": self.reasoning_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], prefix: str = "model_call") -> ModelCall:
        return cls(
            seq=_optional_int(prefix, data, "seq"),
            started_ms=_int(prefix, data, "started_ms"),
            ended_ms=_optional_int(prefix, data, "ended_ms"),
            input_tokens=_optional_int(prefix, data, "input_tokens"),
            output_tokens=_optional_int(prefix, data, "output_tokens"),
            cache_read_tokens=_optional_int(prefix, data, "cache_read_tokens"),
            reasoning_tokens=_optional_int(prefix, data, "reasoning_tokens"),
        )


@dataclass
class Command:
    """One shell command an agent ran.

    `paths_named` holds what `paths_in_command` found in `text`. `denial` is
    true when the harness marked the command as a possible or typed
    permission denial, so a report can count refusals without re-reading
    diagnostics.
    """

    started_ms: int
    ended_ms: int | None
    text: str
    exit_code: int | None
    paths_named: list[str] = field(default_factory=list)
    denial: bool = False

    @classmethod
    def from_text(cls, started_ms: int, ended_ms: int | None, text: str, exit_code: int | None, denial: bool = False) -> Command:
        """A command whose named paths are extracted from its text."""
        return cls(started_ms, ended_ms, text, exit_code, paths_in_command(text), denial)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_ms": self.started_ms,
            "ended_ms": self.ended_ms,
            "text": self.text,
            "exit_code": self.exit_code,
            "paths_named": list(self.paths_named),
            "denial": self.denial,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], prefix: str = "command") -> Command:
        text = _require(prefix, data, "text")
        if not isinstance(text, str):
            raise ValueError(f"{prefix}.text: {text!r} is not a string")
        denial = data.get("denial", False)
        if not isinstance(denial, bool):
            raise ValueError(f"{prefix}.denial: {denial!r} is not a boolean")
        paths = _list(prefix, data, "paths_named")
        for index, path in enumerate(paths):
            if not isinstance(path, str):
                raise ValueError(f"{prefix}.paths_named[{index}]: {path!r} is not a string")
        return cls(
            started_ms=_int(prefix, data, "started_ms"),
            ended_ms=_optional_int(prefix, data, "ended_ms"),
            text=text,
            exit_code=_optional_int(prefix, data, "exit_code"),
            paths_named=list(paths),
            denial=denial,
        )


@dataclass
class ToolCall:
    """One call to a tool, and what the harness recorded of its result.

    `name` is the tool's own name, such as `bash`, `edit`, or the verifier
    a contract declares. `arguments_digest` holds what the module function
    of that name computes over the arguments the call carried, or None when
    the record states no arguments, so a report tells repeated calls from
    distinct ones while holding no argument content. `summary` is one short
    string carrying the fact a report reads from this tool, such as the code
    a block call named or the findings a verifier reported, and is None when
    the record states nothing short. A summary that carries a count states
    it before any other number, so that a reader taking the first integer
    of a summary reads that count, and a summary that carries a path states
    the path alone, so that a reader comparing it against a directory
    reads a path.
    `is_error` is true when the harness reported the call itself as failing;
    a command that ran and exited nonzero is a result, so both arms leave
    the flag false and record the status in the summary. `ended_ms` is None
    for a call whose result never came, and `is_error` is then false,
    because nothing judged the call. A call that is also a shell command
    appears here and in `Agent.commands`, under the tool's name here and
    with its text there.

    Which calls a harness records here is the harness's own definition of a
    tool call: the foe arm records every call its model issued, including
    reads, searches, edits, and the runtime's own verifier invocations,
    while the Codex arm records shell commands and calls to tools reached
    over a protocol and records an edit as a file change. A comparison of
    two arms' tool-call counts therefore reads
    `Trajectory.totals()["tool_calls_by_name"]` to say which calls each
    count holds.
    """

    name: str
    started_ms: int
    ended_ms: int | None
    is_error: bool = False
    arguments_digest: str | None = None
    summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "started_ms": self.started_ms,
            "ended_ms": self.ended_ms,
            "is_error": self.is_error,
            "arguments_digest": self.arguments_digest,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], prefix: str = "tool_call") -> ToolCall:
        name = _require(prefix, data, "name")
        if not isinstance(name, str):
            raise ValueError(f"{prefix}.name: {name!r} is not a string")
        is_error = data.get("is_error", False)
        if not isinstance(is_error, bool):
            raise ValueError(f"{prefix}.is_error: {is_error!r} is not a boolean")
        return cls(
            name=name,
            started_ms=_int(prefix, data, "started_ms"),
            ended_ms=_optional_int(prefix, data, "ended_ms"),
            is_error=is_error,
            arguments_digest=_optional_str(prefix, data, "arguments_digest"),
            summary=_optional_str(prefix, data, "summary"),
        )


@dataclass
class FileChange:
    """One change to one file, recorded by a file tool or attributed to a command."""

    path: str
    kind: str
    at_ms: int
    via: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "at_ms": self.at_ms, "via": self.via}

    @classmethod
    def from_dict(cls, data: dict[str, Any], prefix: str = "file_change") -> FileChange:
        path = _require(prefix, data, "path")
        if not isinstance(path, str):
            raise ValueError(f"{prefix}.path: {path!r} is not a string")
        return cls(
            path=path,
            kind=_check_choice(prefix, "kind", _require(prefix, data, "kind"), CHANGE_KINDS),
            at_ms=_int(prefix, data, "at_ms"),
            via=_check_choice(prefix, "via", _require(prefix, data, "via"), CHANGE_VIAS),
        )


@dataclass
class Compaction:
    """One replacement of an agent's transcript by a summary."""

    at_ms: int
    tokens_before: int | None

    def to_dict(self) -> dict[str, Any]:
        return {"at_ms": self.at_ms, "tokens_before": self.tokens_before}

    @classmethod
    def from_dict(cls, data: dict[str, Any], prefix: str = "compaction") -> Compaction:
        return cls(at_ms=_int(prefix, data, "at_ms"), tokens_before=_optional_int(prefix, data, "tokens_before"))


@dataclass
class Outcome:
    """How the run ended.

    `code` carries the reason a `blocked` outcome names or the limit an
    `exhausted` one hit. `value` is the typed result of a `completed` run.
    """

    status: str
    code: str | None = None
    value: Any = None
    ended_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "code": self.code, "value": self.value, "ended_ms": self.ended_ms}

    @classmethod
    def from_dict(cls, data: dict[str, Any], prefix: str = "outcome") -> Outcome:
        code = data.get("code")
        if code is not None and not isinstance(code, str):
            raise ValueError(f"{prefix}.code: {code!r} is not a string or null")
        return cls(
            status=_check_choice(prefix, "status", _require(prefix, data, "status"), OUTCOME_STATUSES),
            code=code,
            value=data.get("value"),
            ended_ms=_optional_int(prefix, data, "ended_ms"),
        )


@dataclass
class Agent:
    """One agent of the run: the root or a child it spawned.

    `role` is free text the harness assigns, such as `root`, `worker`, or a
    workflow node name. `depth` is 0 for the root and one more for each
    spawn below it. `tool_calls` holds every tool call, the ones that also
    appear as a command or a file change included, so a report reads the
    whole stream from one list.
    """

    id: str
    parent_id: str | None
    depth: int
    role: str
    started_ms: int
    ended_ms: int | None
    model_calls: list[ModelCall] = field(default_factory=list)
    commands: list[Command] = field(default_factory=list)
    file_changes: list[FileChange] = field(default_factory=list)
    compactions: list[Compaction] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "role": self.role,
            "started_ms": self.started_ms,
            "ended_ms": self.ended_ms,
            "model_calls": [call.to_dict() for call in self.model_calls],
            "commands": [command.to_dict() for command in self.commands],
            "file_changes": [change.to_dict() for change in self.file_changes],
            "compactions": [compaction.to_dict() for compaction in self.compactions],
            "tool_calls": [call.to_dict() for call in self.tool_calls],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], prefix: str = "agent") -> Agent:
        for key in ("id", "role"):
            value = _require(prefix, data, key)
            if not isinstance(value, str):
                raise ValueError(f"{prefix}.{key}: {value!r} is not a string")
        parent_id = data.get("parent_id")
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError(f"{prefix}.parent_id: {parent_id!r} is not a string or null")
        return cls(
            id=data["id"],
            parent_id=parent_id,
            depth=_int(prefix, data, "depth"),
            role=data["role"],
            started_ms=_int(prefix, data, "started_ms"),
            ended_ms=_optional_int(prefix, data, "ended_ms"),
            model_calls=[ModelCall.from_dict(item, f"{prefix}.model_calls[{i}]") for i, item in enumerate(_list(prefix, data, "model_calls"))],
            commands=[Command.from_dict(item, f"{prefix}.commands[{i}]") for i, item in enumerate(_list(prefix, data, "commands"))],
            file_changes=[FileChange.from_dict(item, f"{prefix}.file_changes[{i}]") for i, item in enumerate(_list(prefix, data, "file_changes"))],
            compactions=[Compaction.from_dict(item, f"{prefix}.compactions[{i}]") for i, item in enumerate(_list(prefix, data, "compactions"))],
            tool_calls=[ToolCall.from_dict(item, f"{prefix}.tool_calls[{i}]") for i, item in enumerate(_list(prefix, data, "tool_calls"))],
        )


@dataclass
class Trajectory:
    """One run of one harness.

    `identity` names what ran: the harness version or build hash, the
    digest of its configuration, the model, the reasoning effort, and the
    route. Its keys are free so that each arm records what it can prove.
    `route` states whether the model was reached through the vendor's
    subscription or through a compatible endpoint.
    """

    harness: str
    identity: dict[str, Any]
    agents: list[Agent]
    outcome: Outcome
    route: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "harness": self.harness,
            "identity": dict(self.identity),
            "agents": [agent.to_dict() for agent in self.agents],
            "outcome": self.outcome.to_dict(),
            "route": self.route,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], prefix: str = "trajectory") -> Trajectory:
        identity = data.get("identity", {})
        if not isinstance(identity, dict):
            raise ValueError(f"{prefix}.identity: {identity!r} is not an object")
        outcome = _require(prefix, data, "outcome")
        if not isinstance(outcome, dict):
            raise ValueError(f"{prefix}.outcome: {outcome!r} is not an object")
        agents = [Agent.from_dict(item, f"{prefix}.agents[{i}]") for i, item in enumerate(_list(prefix, data, "agents"))]
        ids = [agent.id for agent in agents]
        if len(set(ids)) != len(ids):
            duplicate = next(agent_id for agent_id in ids if ids.count(agent_id) > 1)
            raise ValueError(f"{prefix}.agents: the id {duplicate!r} names more than one agent")
        return cls(
            harness=_check_choice(prefix, "harness", _require(prefix, data, "harness"), HARNESSES),
            identity=dict(identity),
            agents=agents,
            outcome=Outcome.from_dict(outcome, f"{prefix}.outcome"),
            route=_check_choice(prefix, "route", _require(prefix, data, "route"), ROUTES),
        )

    def agent(self, agent_id: str) -> Agent:
        for agent in self.agents:
            if agent.id == agent_id:
                return agent
        raise KeyError(f"trajectory.agents: no agent has the id {agent_id!r}")

    def totals(self) -> dict[str, Any]:
        """The counts a report states for the whole run.

        A token total is None when any model call lacked that count, so a
        partial sum is never mistaken for a full one; `responses_with_usage`
        says how many calls carried both an input and an output count.
        `wall_ms` runs from the earliest agent start to the latest agent or
        outcome end, and is None when nothing recorded an end.
        `tool_calls_by_name` counts the whole tree's tool calls under each
        tool's own name, ordered by name. `tool_calls` counts what the
        harness that wrote the trajectory records as a tool call, and the
        two harnesses record different populations, as `ToolCall` states, so
        one arm's total is read beside the other's only through the
        per-name counts.
        """
        calls = [call for agent in self.agents for call in agent.model_calls]
        commands = [command for agent in self.agents for command in agent.commands]
        tool_calls = [call for agent in self.agents for call in agent.tool_calls]
        by_name: dict[str, int] = {}
        for call in tool_calls:
            by_name[call.name] = by_name.get(call.name, 0) + 1
        ends = [agent.ended_ms for agent in self.agents if agent.ended_ms is not None]
        if self.outcome.ended_ms is not None:
            ends.append(self.outcome.ended_ms)
        starts = [agent.started_ms for agent in self.agents]
        return {
            "model_calls": len(calls),
            "responses_with_usage": sum(1 for call in calls if call.has_usage),
            "input_tokens": _sum_or_none(call.input_tokens for call in calls),
            "output_tokens": _sum_or_none(call.output_tokens for call in calls),
            "cache_read_tokens": _sum_or_none(call.cache_read_tokens for call in calls),
            "reasoning_tokens": _sum_or_none(call.reasoning_tokens for call in calls),
            "agents": len(self.agents),
            "max_depth": max((agent.depth for agent in self.agents), default=0),
            "wall_ms": (max(ends) - min(starts)) if starts and ends else None,
            "commands": len(commands),
            "denials": sum(1 for command in commands if command.denial),
            "file_changes": sum(len(agent.file_changes) for agent in self.agents),
            "compactions": sum(len(agent.compactions) for agent in self.agents),
            "tool_calls": len(tool_calls),
            "tool_calls_by_name": {name: by_name[name] for name in sorted(by_name)},
        }


def _sum_or_none(values: Any) -> int | None:
    total = 0
    for value in values:
        if value is None:
            return None
        total += value
    return total


def attribute_shell_writes(trajectory: Trajectory, snapshot_before: dict[str, int], snapshot_after: dict[str, int]) -> int:
    """Attribute files a shell command wrote to the agent that ran it, and return how many records were added.

    A snapshot maps each workspace file's path to its modification time in
    milliseconds. A file whose time changed, or that the first snapshot
    lacks, was written during the run; it is attributed to every agent that
    has a command whose interval contains that time, once per agent, as a
    FileChange with `via` "shell" and kind "create" or "edit". A command
    with no end runs to its agent's end, or without bound when the agent has
    none either. A file the second snapshot lacks was deleted at a time no
    snapshot records, so it is attributed to nothing.
    """
    written = {path: at for path, at in snapshot_after.items() if snapshot_before.get(path) != at}
    added = 0
    for agent in trajectory.agents:
        intervals = [(command.started_ms, command.ended_ms if command.ended_ms is not None else agent.ended_ms) for command in agent.commands]
        for path, at in sorted(written.items(), key=lambda item: (item[1], item[0])):
            if not any(start <= at and (end is None or at <= end) for start, end in intervals):
                continue
            kind = "create" if path not in snapshot_before else "edit"
            agent.file_changes.append(FileChange(path=path, kind=kind, at_ms=at, via="shell"))
            added += 1
    return added
