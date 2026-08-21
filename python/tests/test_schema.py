"""Schema derivation for every supported annotation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional, TypedDict

import pytest

import foe


@dataclass
class Point:
    x: int
    y: int = 0


class Span(TypedDict, total=False):
    start: int
    end: int


class Named(TypedDict):
    name: str


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (str, {"type": "string"}),
        (int, {"type": "integer"}),
        (float, {"type": "number"}),
        (bool, {"type": "boolean"}),
        (Any, {}),
        (list, {"type": "array"}),
        (list[str], {"type": "array", "items": {"type": "string"}}),
        (list[list[int]], {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}}),
        (dict, {"type": "object"}),
        (dict[str, Any], {"type": "object", "additionalProperties": {}}),
        (dict[str, float], {"type": "object", "additionalProperties": {"type": "number"}}),
        (Optional[str], {"anyOf": [{"type": "string"}, {"type": "null"}]}),
        (int | None, {"anyOf": [{"type": "integer"}, {"type": "null"}]}),
        (Literal["a", "b"], {"enum": ["a", "b"]}),
        (
            Point,
            {
                "type": "object",
                "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                "required": ["x"],
                "additionalProperties": False,
            },
        ),
        (
            Span,
            {
                "type": "object",
                "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}},
                "additionalProperties": False,
            },
        ),
        (
            Named,
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
    ],
)
def test_schema_for(annotation: Any, expected: dict[str, Any]) -> None:
    assert foe.schema_for(annotation) == expected


def test_unsupported_annotation_names_itself() -> None:
    with pytest.raises(TypeError, match="unsupported annotation"):
        foe.schema_for(set[int])


def test_tool_params_capabilities_and_effect() -> None:
    @foe.tool
    def sample(a: str, b: int = 3, *, c: Point | None = None, fs: foe.ReadFS, out: foe.WriteFS) -> None:
        """Sample tool.

        More detail that is not part of the description.
        """

    assert sample.spec.description == "Sample tool."
    assert sample.spec.effect == "writes"
    assert sample.spec.params == {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "integer"},
            "c": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                        "required": ["x"],
                        "additionalProperties": False,
                    },
                    {"type": "null"},
                ]
            },
        },
        "required": ["a"],
        "additionalProperties": False,
    }


def test_tool_effect_from_each_capability() -> None:
    @foe.tool
    def pure(a: str) -> str:
        """Pure."""
        return a

    @foe.tool
    def reads(a: str, fs: foe.ReadFS) -> str:
        """Reads."""
        return a

    @foe.tool
    def execs(a: str, fs: foe.ReadFS, ex: foe.Exec) -> str:
        """Execs."""
        return a

    assert (pure.spec.effect, reads.spec.effect, execs.spec.effect) == ("pure", "reads", "execs")


def test_tool_decorator_with_arguments() -> None:
    @foe.tool(name="count_refs", description="Count references.", instruction="Call it once.")
    def anything(symbol: str) -> int:
        return 0

    assert anything.spec.name == "count_refs"
    assert anything.spec.to_dict() == {
        "description": "Count references.",
        "instruction": "Call it once.",
        "params": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
        "effect": "pure",
    }


def test_tool_without_description_is_an_error() -> None:
    with pytest.raises(TypeError, match="needs a docstring"):

        @foe.tool
        def silent(a: str) -> str:
            return a


def test_tool_parameter_without_annotation_is_an_error() -> None:
    with pytest.raises(TypeError, match="has no annotation"):

        @foe.tool
        def loose(a) -> str:  # type: ignore[no-untyped-def]
            """Loose."""
            return ""
