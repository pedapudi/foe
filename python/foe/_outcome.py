"""Outcomes and log events as Python values.

The shapes follow docs/log-format.md: the four outcome kinds of
`episode/end` and the event envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class Completed:
    """The contract's termination condition was met."""

    value: Any


@dataclass(frozen=True, slots=True)
class Blocked:
    """The agent recognized that it cannot proceed.

    `code` is one of the closed vocabulary in docs/log-format.md "Blocked
    codes".
    """

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class Exhausted:
    """A resource limit was reached.

    `limit` is one of `model_calls`, `input_tokens`, `output_tokens`,
    `context_window`, `seconds`, `depth`, `episodes`, `concurrency`.
    """

    limit: str


@dataclass(frozen=True, slots=True)
class Failed:
    """The runtime could not continue."""

    error: str


Outcome = Completed | Blocked | Exhausted | Failed


def outcome_from_json(data: Mapping[str, Any]) -> Outcome:
    """Parse the `outcome` object of an `episode/end` event."""
    kind = data.get("kind")
    if kind == "completed":
        return Completed(data.get("value"))
    if kind == "blocked":
        return Blocked(str(data["code"]), str(data.get("message", "")))
    if kind == "exhausted":
        return Exhausted(str(data["limit"]))
    if kind == "failed":
        return Failed(str(data.get("error", "")))
    raise ValueError(f"episode/end: unknown outcome kind {kind!r}")


@dataclass(frozen=True, slots=True)
class Event:
    """One log event as foe wrote it to standard output.

    `episode_id` is set when the event was forwarded from a child episode
    and is None for the root episode.
    """

    seq: int
    time: int
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    episode_id: str | None = None

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> Event:
        episode_id = obj.get("episode_id")
        return cls(
            seq=int(obj["seq"]),
            time=int(obj["time"]),
            type=str(obj["type"]),
            data=dict(obj.get("data") or {}),
            episode_id=None if episode_id is None else str(episode_id),
        )
