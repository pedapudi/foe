"""JSON Schema derived from Python type annotations.

The derivation covers the annotations docs/sdk.md lists: `str`, `int`,
`float`, `bool`, `list[T]`, `dict` and `dict[str, T]`, `Optional[T]`,
`Literal`, `Any`, dataclasses, and `TypedDict` classes. Any other
annotation is an error that names the annotation, so that a tool with an
unsupported signature fails at import rather than at the first call.
"""

from __future__ import annotations

import dataclasses
import inspect
import types
import typing
from pathlib import Path
from typing import Any, Mapping

JsonSchema = dict[str, Any]


def _is_typed_dict(annotation: Any) -> bool:
    return isinstance(annotation, type) and typing.is_typeddict(annotation)


def _own_docstring(cls: type) -> str | None:
    """The first line of a class docstring the author wrote.

    Dataclasses synthesize a docstring of the form `Name(field: type, ...)`
    when the author gave none; that text is not a description.
    """
    doc = cls.__doc__
    if not doc:
        return None
    first = doc.strip().splitlines()[0].strip()
    if first.startswith(cls.__name__ + "("):
        return None
    return first or None


def schema_for(annotation: Any) -> JsonSchema:
    """The JSON Schema for one annotation."""
    if annotation is Any:
        return {}
    if annotation is str:
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is type(None):
        return {"type": "null"}
    if annotation is list:
        return {"type": "array"}
    if annotation is dict:
        return {"type": "object"}

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin is typing.Union or origin is types.UnionType:
        members = [schema_for(a) for a in args]
        return {"anyOf": members}
    if origin is typing.Literal:
        return {"enum": list(args)}
    if origin is list:
        return {"type": "array", "items": schema_for(args[0])}
    if origin is dict:
        if args and args[0] is not str:
            raise TypeError(f"unsupported annotation {annotation!r}: dict keys must be str")
        if not args:
            return {"type": "object"}
        return {"type": "object", "additionalProperties": schema_for(args[1])}
    if origin is typing.Annotated:
        return schema_for(args[0])

    if _is_typed_dict(annotation):
        return _object_schema(annotation, typing.get_type_hints(annotation), set(annotation.__required_keys__))
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        hints = typing.get_type_hints(annotation)
        required = {
            f.name
            for f in dataclasses.fields(annotation)
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
        }
        hints = {f.name: hints[f.name] for f in dataclasses.fields(annotation)}
        return _object_schema(annotation, hints, required)

    raise TypeError(f"unsupported annotation {annotation!r}")


def _object_schema(cls: type, hints: Mapping[str, Any], required: set[str]) -> JsonSchema:
    schema: JsonSchema = {"type": "object"}
    description = _own_docstring(cls)
    if description:
        schema["description"] = description
    schema["properties"] = {name: schema_for(hint) for name, hint in hints.items()}
    ordered_required = [name for name in hints if name in required]
    if ordered_required:
        schema["required"] = ordered_required
    schema["additionalProperties"] = False
    return schema


def params_schema(fn: Any, skip: set[str]) -> JsonSchema:
    """The JSON Schema for a function's arguments, excluding `skip`.

    Parameters without a default are required. Every remaining parameter
    must carry an annotation.
    """
    hints = typing.get_type_hints(fn)
    signature = inspect.signature(fn)
    properties: dict[str, JsonSchema] = {}
    required: list[str] = []
    for name, param in signature.parameters.items():
        if name in skip:
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            raise TypeError(f"{fn.__name__}: parameter *{name} is not supported on a tool")
        if name not in hints:
            raise TypeError(f"{fn.__name__}: parameter {name!r} has no annotation")
        properties[name] = schema_for(hints[name])
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema: JsonSchema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    schema["additionalProperties"] = False
    return schema


def to_jsonable(value: Any) -> Any:
    """Convert a tool's return value to JSON-compatible data.

    Dataclass instances become objects, paths become strings, and tuples
    and sets become lists. Everything else passes through unchanged.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    return value
