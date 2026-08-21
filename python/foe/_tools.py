"""Host tools: Python callables the model may invoke through the binary.

A host tool has a specification, which the configuration document carries
and the model sees, and an implementation, which runs in the host process
when a `host/tool-call` event arrives. The specification is derived from the
function: its name, the first line of its docstring, a JSON Schema for its
annotated parameters, and an effect chosen by the capability handles it
asks for.
"""

from __future__ import annotations

import asyncio
import inspect
import typing
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Sequence, overload

from ._capabilities import Exec, PathLike, ReadFS, WriteFS
from ._schema import JsonSchema, params_schema, to_jsonable

Effect = Literal["pure", "reads", "writes", "execs"]

_EFFECT_RANK: dict[str, int] = {"pure": 0, "reads": 1, "writes": 2, "execs": 3}
_CAPABILITY_EFFECT: dict[type, Effect] = {ReadFS: "reads", WriteFS: "writes", Exec: "execs"}


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """What identity hashes and what the model sees. See docs/design.md "Tools"."""

    name: str
    description: str
    instruction: str | None
    params: JsonSchema
    effect: Effect

    def to_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"description": self.description}
        if self.instruction is not None:
            entry["instruction"] = self.instruction
        entry["params"] = self.params
        entry["effect"] = self.effect
        return entry


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a host tool returns over the protocol.

    `value` is the canonical result and is always recorded. `rendered` is the
    text the model sees; when None the runtime renders `value` compactly.
    """

    value: Any
    rendered: str | None = None
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class Capabilities:
    """The handles a call may receive, built per call from the grants."""

    read_roots: Sequence[PathLike] = ()
    write_roots: Sequence[PathLike] = ()
    executables: Sequence[PathLike] = ()


class HostTool:
    """A Python function registered as a tool. Constructed by `@foe.tool`."""

    def __init__(
        self,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        instruction: str | None = None,
    ) -> None:
        self.fn = fn
        hints = typing.get_type_hints(fn)
        self._capability_params: dict[str, type] = {
            param: hint for param, hint in hints.items() if param != "return" and hint in _CAPABILITY_EFFECT
        }
        effect: Effect = "pure"
        for hint in self._capability_params.values():
            candidate = _CAPABILITY_EFFECT[hint]
            if _EFFECT_RANK[candidate] > _EFFECT_RANK[effect]:
                effect = candidate
        resolved_description = description if description is not None else _first_doc_line(fn)
        if not resolved_description:
            raise TypeError(f"{fn.__name__}: a tool needs a docstring or an explicit description")
        self.spec = ToolSpec(
            name=name or fn.__name__,
            description=resolved_description,
            instruction=instruction,
            params=params_schema(fn, set(self._capability_params)),
            effect=effect,
        )
        self._render: Callable[[Any], str] | None = None
        self.__name__ = self.spec.name
        self.__doc__ = fn.__doc__

    @property
    def name(self) -> str:
        return self.spec.name

    def render(self, fn: Callable[[Any], str]) -> Callable[[Any], str]:
        """Register the function from the canonical value to the text the model sees."""
        self._render = fn
        return fn

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Call the underlying function directly, without capability construction."""
        return self.fn(*args, **kwargs)

    def __repr__(self) -> str:
        return f"HostTool({self.spec.name!r}, effect={self.spec.effect!r})"

    async def invoke(self, args: Mapping[str, Any], capabilities: Capabilities) -> ToolResult:
        """Run the tool for one `host/tool-call`.

        Synchronous functions run in a worker thread so that concurrent calls
        do not block the protocol loop. An exception becomes an error result
        whose value is `{"error": message}`, matching the runtime's own form.
        """
        kwargs: dict[str, Any] = dict(args)
        for param, hint in self._capability_params.items():
            if hint is ReadFS:
                kwargs[param] = ReadFS(capabilities.read_roots)
            elif hint is WriteFS:
                kwargs[param] = WriteFS(capabilities.write_roots)
            else:
                kwargs[param] = Exec(capabilities.executables)
        try:
            if inspect.iscoroutinefunction(self.fn):
                raw = await self.fn(**kwargs)
            else:
                raw = await asyncio.to_thread(self.fn, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - every failure is reported to the model as data
            message = f"{type(exc).__name__}: {exc}"
            return ToolResult(value={"error": message}, rendered=message, is_error=True)
        if isinstance(raw, ToolResult):
            return raw
        value = to_jsonable(raw)
        rendered = self._render(value) if self._render is not None else None
        return ToolResult(value=value, rendered=rendered)


def _first_doc_line(fn: Callable[..., Any]) -> str | None:
    doc = inspect.getdoc(fn)
    if not doc:
        return None
    return doc.strip().splitlines()[0].strip() or None


@overload
def tool(fn: Callable[..., Any], /) -> HostTool: ...


@overload
def tool(
    fn: None = None,
    /,
    *,
    name: str | None = None,
    description: str | None = None,
    instruction: str | None = None,
) -> Callable[[Callable[..., Any]], HostTool]: ...


def tool(
    fn: Callable[..., Any] | None = None,
    /,
    *,
    name: str | None = None,
    description: str | None = None,
    instruction: str | None = None,
) -> HostTool | Callable[[Callable[..., Any]], HostTool]:
    """Register a function as a host tool.

    Used bare, `@foe.tool`, or with keyword arguments,
    `@foe.tool(name=..., description=..., instruction=...)`. `instruction` is
    appended to the system prompt after the instructions, in `tools` order.
    """

    def wrap(target: Callable[..., Any]) -> HostTool:
        return HostTool(target, name=name, description=description, instruction=instruction)

    if fn is not None:
        return wrap(fn)
    return wrap
