"""The program: a configuration document without a task.

`Program` validates what it can before any process starts, emits the
document docs/config.md specifies, asks the binary for the program's
identity, and runs episodes through the host protocol.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ._capabilities import PathLike
from ._errors import BinaryError, ConfigError
from ._host import EventCallback, Handle, Transport, start_config
from ._outcome import Outcome
from ._schema import JsonSchema, schema_for
from ._tools import HostTool

BUILTIN_TOOLS: frozenset[str] = frozenset(
    {"read", "grep", "edit", "bash", "block", "spawn", "wait", "steer", "notify", "send", "team"}
)

# The task does not participate in identity, so the placeholder written for
# `foe plan` has no effect on the hash it reports.
_IDENTITY_TASK = "identity"


def _absolute(key: str, path: PathLike) -> str:
    text = os.fspath(path)
    if not os.path.isabs(text):
        raise ConfigError(f"{key}: {text!r} is not an absolute path")
    return text


@dataclass(frozen=True, slots=True)
class Grants:
    """What the episode may reach. Every path is absolute."""

    read: Sequence[PathLike]
    write: Sequence[PathLike] = ()
    execute: Sequence[PathLike] = ()
    spawn: Sequence[str] = ()

    def to_dict(self) -> dict[str, Any]:
        if not self.read:
            raise ConfigError("grants.read: at least one directory is required")
        out: dict[str, Any] = {"read": [_absolute("grants.read", p) for p in self.read]}
        if self.write:
            out["write"] = [_absolute("grants.write", p) for p in self.write]
        if self.execute:
            out["execute"] = [_absolute("grants.execute", p) for p in self.execute]
        if self.spawn:
            out["spawn"] = list(self.spawn)
        return out


@dataclass(frozen=True, slots=True)
class Budget:
    """Limits for the episode and every child below it.

    A field left None takes the runtime's default, which docs/config.md
    states; the document then omits the key.
    """

    model_calls: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    seconds: int | None = None
    max_depth: int | None = None
    max_episodes: int | None = None
    max_concurrent: int | None = None
    loop_threshold: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"model_calls": self.model_calls}
        for key in (
            "input_tokens",
            "output_tokens",
            "seconds",
            "max_depth",
            "max_episodes",
            "max_concurrent",
            "loop_threshold",
        ):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


@dataclass(frozen=True, slots=True)
class ToolDef:
    """A configured executable the model may invoke. See docs/config.md `tool_defs`."""

    exec: PathLike
    description: str
    instruction: str | None = None
    network: bool = False
    timeout_seconds: int | None = None
    cwd: PathLike | None = None

    def to_dict(self, key: str) -> dict[str, Any]:
        out: dict[str, Any] = {"exec": _absolute(f"{key}.exec", self.exec), "description": self.description}
        if self.instruction is not None:
            out["instruction"] = self.instruction
        if self.network:
            out["network"] = True
        if self.timeout_seconds is not None:
            out["timeout_seconds"] = self.timeout_seconds
        if self.cwd is not None:
            out["cwd"] = _absolute(f"{key}.cwd", self.cwd)
        return out


def _returns_schema(source: type | Mapping[str, Any]) -> JsonSchema:
    if isinstance(source, Mapping):
        return dict(source)
    return schema_for(source)


@dataclass(frozen=True, slots=True)
class Returns:
    """Complete when the model calls the synthesized `return` tool with a conforming value.

    `schema` is a dataclass, a `TypedDict` class, or a JSON Schema object.
    """

    schema: type | Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"returns": _returns_schema(self.schema)}


@dataclass(frozen=True, slots=True)
class Verified:
    """Complete when the model finishes and `verify` returns no findings.

    `verify` is a host tool or the name of a tool in the program. A host
    tool given here and absent from `tools` is appended to the program's
    tools. Findings are fed back for up to `retries` further attempts. When
    `returns` is given the verifier checks the returned value.
    """

    verify: HostTool | str
    retries: int = 2
    returns: type | Mapping[str, Any] | None = None

    @property
    def verify_name(self) -> str:
        return self.verify if isinstance(self.verify, str) else self.verify.name

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"verify": self.verify_name, "retries": self.retries}
        if self.returns is not None:
            out["returns"] = _returns_schema(self.returns)
        return out


DoneWhen = Verified | Returns


class Program:
    """A configuration document without its task.

    Construction validates tool names, effects against grants, and paths,
    and raises `ConfigError` naming the key and the rule. A program that
    constructs can be serialized with `to_json`, hashed with `identity`, and
    run with `run` or `start`.
    """

    def __init__(
        self,
        *,
        name: str,
        instructions: Mapping[str, str],
        tools: Sequence[str | HostTool],
        grants: Grants,
        budget: Budget,
        tool_defs: Mapping[str, ToolDef] | None = None,
        done_when: DoneWhen | None = None,
        programs: Mapping[str, Program] | None = None,
        sandbox: str | None = None,
    ) -> None:
        if not name:
            raise ConfigError("name: must not be empty")
        if not instructions:
            raise ConfigError("instructions: at least one section is required")
        if not tools:
            raise ConfigError("tools: at least one tool is required")
        if sandbox is not None and sandbox not in ("best-effort", "required", "off"):
            raise ConfigError(f"sandbox.mode: {sandbox!r} is not one of best-effort, required, off")
        self.name = name
        self.instructions: dict[str, str] = dict(instructions)
        self.grants = grants
        self.budget = budget
        self.tool_defs: dict[str, ToolDef] = dict(tool_defs or {})
        self.done_when = done_when
        self.programs: dict[str, Program] = dict(programs or {})
        self.sandbox = sandbox

        listed: list[str | HostTool] = list(tools)
        if isinstance(done_when, Verified) and isinstance(done_when.verify, HostTool):
            if done_when.verify not in listed:
                listed.append(done_when.verify)

        self.tools: list[str] = []
        self.host_tools: dict[str, HostTool] = {}
        for entry in listed:
            tool_name = entry if isinstance(entry, str) else entry.name
            if tool_name in self.tools:
                raise ConfigError(f"tools: {tool_name!r} is listed twice")
            if isinstance(entry, HostTool):
                self._check_host_tool(entry)
                self.host_tools[tool_name] = entry
            elif tool_name in BUILTIN_TOOLS:
                self._check_builtin(tool_name)
            elif tool_name not in self.tool_defs:
                raise ConfigError(f"tools: {tool_name!r} names no built-in tool, tool_defs entry, or host tool")
            self.tools.append(tool_name)

        for def_name in self.tool_defs:
            if def_name in BUILTIN_TOOLS:
                raise ConfigError(f"tool_defs.{def_name}: collides with the built-in tool of the same name")

        if isinstance(done_when, Verified) and done_when.verify_name not in self.tools:
            raise ConfigError(f"done_when.verify: {done_when.verify_name!r} is not a tool in tools")
        for child in grants.spawn:
            if child not in self.programs:
                raise ConfigError(f"grants.spawn: {child!r} is not a key of programs")

    def _check_host_tool(self, tool: HostTool) -> None:
        tool_name = tool.name
        if tool_name in BUILTIN_TOOLS:
            raise ConfigError(f"tools: host tool {tool_name!r} collides with the built-in tool of the same name")
        if tool_name in self.tool_defs:
            raise ConfigError(f"tools: host tool {tool_name!r} collides with the tool_defs entry of the same name")
        effect = tool.spec.effect
        if effect == "writes" and not self.grants.write:
            raise ConfigError(f"tools: {tool_name!r} declares effect writes and grants.write is empty")
        if effect == "execs" and not self.tool_defs:
            raise ConfigError(f"tools: {tool_name!r} declares effect execs and tool_defs is empty")

    def _check_builtin(self, tool_name: str) -> None:
        if tool_name == "edit" and not self.grants.write:
            raise ConfigError("tools: 'edit' declares effect writes and grants.write is empty")
        if tool_name == "spawn" and not self.grants.spawn:
            raise ConfigError("tools: 'spawn' declares effect spawns and grants.spawn is empty")

    # ---- serialization ---------------------------------------------------

    def to_dict(self, task: str | None = None, *, child: bool = False) -> dict[str, Any]:
        """The configuration document as a dict.

        Without `task` the result is the program alone. A child program omits
        `version` and `sandbox`, which are inherited.
        """
        doc: dict[str, Any] = {}
        if not child:
            doc["version"] = 2
        doc["name"] = self.name
        doc["instructions"] = {k: self.instructions[k] for k in sorted(self.instructions)}
        doc["tools"] = list(self.tools)
        if self.tool_defs:
            doc["tool_defs"] = {k: d.to_dict(f"tool_defs.{k}") for k, d in sorted(self.tool_defs.items())}
        if self.host_tools:
            doc["host_tools"] = {k: t.spec.to_dict() for k, t in sorted(self.host_tools.items())}
        doc["grants"] = self.grants.to_dict()
        doc["budget"] = self.budget.to_dict()
        if self.done_when is not None:
            doc["done_when"] = self.done_when.to_dict()
        if self.sandbox is not None and not child:
            doc["sandbox"] = {"mode": self.sandbox}
        if self.programs:
            doc["programs"] = {k: p.to_dict(child=True) for k, p in sorted(self.programs.items())}
        if task is not None:
            doc["task"] = task
        return doc

    def to_json(self, task: str | None = None, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(task), indent=indent, ensure_ascii=False)

    def all_host_tools(self) -> dict[str, HostTool]:
        """Host tools of this program and every child program, by name."""
        tools = dict(self.host_tools)
        for child in self.programs.values():
            tools.update(child.all_host_tools())
        return tools

    # ---- identity -----------------------------------------------------------

    def identity(self, binary: PathLike) -> str:
        """The program's identity, as `foe plan --json` reports it.

        The binary reads the document and the files it names by absolute
        path, executes nothing, and opens no socket.
        """
        with tempfile.TemporaryDirectory(prefix="foe-plan-") as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(self.to_json(_IDENTITY_TASK), encoding="utf-8")
            try:
                completed = subprocess.run(
                    [os.fspath(binary), "plan", "--json", "--config", str(config_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError as exc:
                raise BinaryError(f"{os.fspath(binary)}: {exc}") from exc
        if completed.returncode != 0:
            raise BinaryError(f"foe plan exited with code {completed.returncode}: {completed.stderr.strip()}")
        try:
            plan = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise BinaryError(f"foe plan --json printed something other than JSON: {exc}") from exc
        if not isinstance(plan, dict) or not isinstance(plan.get("identity"), str):
            raise BinaryError("foe plan --json printed no 'identity' string")
        return str(plan["identity"])

    # ---- running --------------------------------------------------------------

    async def start(
        self,
        task: str,
        *,
        transport: Transport,
        binary: PathLike,
        log_dir: PathLike,
        on_event: EventCallback | None = None,
        max_output_tokens: int | None = None,
    ) -> Handle:
        """Launch an episode and return a handle to steer, cancel, or await it."""
        return await start_config(
            self.to_dict(task),
            transport=transport,
            binary=binary,
            log_dir=log_dir,
            tools=self.all_host_tools().values(),
            on_event=on_event,
            max_output_tokens=max_output_tokens,
        )

    async def run(
        self,
        task: str,
        *,
        transport: Transport,
        binary: PathLike,
        log_dir: PathLike,
        on_event: EventCallback | None = None,
        max_output_tokens: int | None = None,
    ) -> Outcome:
        """Run an episode to its outcome."""
        handle = await self.start(
            task,
            transport=transport,
            binary=binary,
            log_dir=log_dir,
            on_event=on_event,
            max_output_tokens=max_output_tokens,
        )
        return await handle.wait()


Verifier = Callable[..., Any]
