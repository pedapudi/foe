#!/usr/bin/python3
"""Read episode logs into the facts the recorded-cost reports fold over.

Every fact here comes from a field `docs/log-format.md` specifies, so a
report built on this module states what the runtime recorded rather than
what a reimplementation of a tool would produce.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

# Tool calls this analysis counts as repository search. `read` is search
# when the model is locating something and retrieval when it already knows
# the path. The log does not separate the two, so both appear under their
# own tool name and are never summed into one figure called search.
SEARCH_TOOLS = ("grep", "read")

# Command heads that search a tree from `bash`. A shell search costs the
# same wall clock as a `grep` call and a content index would not serve it,
# so a report that omits it understates how much searching an episode does.
SEARCH_COMMANDS = ("grep", "rg", "egrep", "fgrep", "ripgrep", "find", "ag", "fd", "ack", "locate")

SEGMENT_BREAKS = re.compile(r"[&|;\n]")

# Characters that make a grep pattern a regular expression rather than a
# fixed string. The `literal` argument states the distinction outright;
# without it the pattern text is the only evidence.
REGEX_METACHARACTERS = set(".*+?[](){}|^$\\")


class Episode:
    """One episode log, with the fields the reports read already extracted.

    A seeded log opens with events copied from another episode, closed by
    `seed/end`. Those events record the seeding episode's work and are
    excluded from every count here, so summing a run's episodes counts each
    tool call once.
    """

    def __init__(self, path: Path, events: list[dict[str, Any]]) -> None:
        self.path = path
        self.events = events
        self.id = ""
        self.parent_id = None
        self.task = ""
        self.calls: list[dict[str, Any]] = []
        self.usage = {"input": 0, "output": 0, "cache_read": 0}
        self.model_calls = 0
        self.outcome: dict[str, Any] = {}
        self.node_firings: list[dict[str, Any]] = []
        self.branches: list[dict[str, Any]] = []
        self.seeded_through = seed_boundary(events)
        live = [event for event in events if event["seq"] > self.seeded_through]
        self.start_ms = live[0]["time"] if live else (events[0]["time"] if events else 0)
        self.end_ms = events[-1]["time"] if events else 0
        self._load()

    def _load(self) -> None:
        args_by_call: dict[str, dict[str, Any]] = {}
        step_by_call: dict[str, int] = {}
        starts: dict[tuple[str, int], dict[str, Any]] = {}
        for event in self.events:
            kind = event.get("type")
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            copied = event["seq"] <= self.seeded_through
            if kind == "assistant/message":
                for call in data.get("tool_calls") or []:
                    args_by_call[call["id"]] = call.get("args") or {}
                    step_by_call[call["id"]] = int(data.get("step", 0) or 0)
            if copied and kind != "episode/start":
                continue
            if kind == "episode/start":
                self.id = data.get("id", "")
                self.parent_id = data.get("parent_id")
                self.task = data.get("task", "")
            elif kind == "episode/end":
                self.outcome = data.get("outcome") or {}
            elif kind == "assistant/message":
                self.model_calls += 1
                usage = data.get("usage") or {}
                for field in self.usage:
                    self.usage[field] += int(usage.get(field, 0) or 0)
                for call in data.get("tool_calls") or []:
                    args_by_call[call["id"]] = call.get("args") or {}
                    step_by_call[call["id"]] = int(data.get("step", 0) or 0)
            elif kind == "tool/result":
                call_id = data.get("call_id", "")
                value = spilled_value(self.path.parent, data)
                self.calls.append(
                    {
                        "seq": event["seq"],
                        "time": event["time"],
                        "step": step_by_call.get(call_id, int(data.get("step", 0) or 0)),
                        "name": data.get("name", ""),
                        "call_id": call_id,
                        "args": args_by_call.get(call_id, {}),
                        "duration_ms": int(data.get("duration_ms", 0) or 0),
                        "is_error": bool(data.get("is_error")),
                        "synthetic": bool(data.get("synthetic")),
                        "subject": data.get("subject") or "",
                        "rendered_chars": len(data.get("rendered") or ""),
                        "value": value,
                    }
                )
            elif kind == "workflow/node-start":
                starts[(data.get("node", ""), int(data.get("fire", 0)))] = data
            elif kind == "workflow/node-end":
                key = (data.get("node", ""), int(data.get("fire", 0)))
                start = starts.get(key, {})
                self.node_firings.append(
                    {
                        "node": data.get("node", ""),
                        "fire": int(data.get("fire", 0)),
                        "child_id": start.get("child_id"),
                        "duration_ms": int(data.get("duration_ms", 0) or 0),
                        "error": data.get("error"),
                        "rendered_chars": len(data.get("rendered") or ""),
                    }
                )
            elif kind == "workflow/branch":
                self.branches.append({"node": data.get("node", ""), "label": data.get("label", "")})

    @property
    def wall_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def tool_calls(self, name: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["name"] == name and not call["synthetic"]]


def spilled_value(episode_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    """The canonical result of one `tool/result`, following a spill locator.

    A result too large to inline is stored under the episode's `spill/`
    directory and `value` holds only the locator. Reading the stored file
    keeps a broad search's match counts and hit paths available, which is
    exactly where they would otherwise be missing.
    """
    value = data.get("value") if isinstance(data.get("value"), dict) else {}
    name = data.get("spill")
    if not name:
        return value
    stored_path = episode_dir / "spill" / str(name)
    if not stored_path.is_file():
        return value
    stored = json.loads(stored_path.read_text(encoding="utf-8"))
    return stored if isinstance(stored, dict) else value


def seed_boundary(events: list[dict[str, Any]]) -> int:
    """The `seq` of `seed/end`, or -1 when the log copied no prefix.

    Every event at or before this sequence was copied from the seeding
    episode's log, which records the same tool calls under its own
    sequences.
    """
    for event in events:
        if event.get("type") == "seed/end":
            return int(event["seq"])
    return -1


def read_events(path: Path) -> list[dict[str, Any]]:
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            events.append(value)
    return events


def load_episodes(root: Path) -> list[Episode]:
    """Every episode log under `root`, parents before their children."""
    return [Episode(path, read_events(path)) for path in sorted(root.rglob("episode.jsonl"))]


def run_of(path: Path, root: Path) -> str:
    """The top-level episode directory `path` belongs to.

    A workflow's nodes are child episodes under the root episode's
    `children/`, so the first path component after `root` names one whole
    run.
    """
    relative = path.relative_to(root)
    return relative.parts[0] if relative.parts else str(root)


def command_segments(command: str) -> list[str]:
    return [segment for segment in SEGMENT_BREAKS.split(command) if segment.strip()]


def search_command_heads(command: str) -> list[str]:
    """Every search program named anywhere in one shell command line.

    A search counts wherever it appears, including after a pipe and inside
    a loop body, because the traversal happens either way.
    """
    found = []
    for segment in command_segments(command):
        for token in segment.split():
            head = token.rsplit("/", 1)[-1].strip("()'\"`{}")
            if head in SEARCH_COMMANDS:
                found.append(head)
    return found


def pattern_shape(args: dict[str, Any]) -> dict[str, Any]:
    """The properties of one grep call that decide what it costs to serve."""
    pattern = str(args.get("pattern", ""))
    declared_literal = bool(args.get("literal"))
    return {
        "pattern": pattern,
        "length": len(pattern),
        "literal": declared_literal or not (set(pattern) & REGEX_METACHARACTERS),
        "declared_literal": declared_literal,
        "regex_metacharacters": sorted(set(pattern) & REGEX_METACHARACTERS),
        "ignore_case": bool(args.get("ignore_case")),
        "path_restricted": args.get("path") is not None,
        "glob_restricted": args.get("glob") is not None,
        "context": int(args.get("context", 0) or 0),
        "scope": str(args.get("path", "")),
        "glob": str(args.get("glob", "")),
    }


def normalized_grep(args: dict[str, Any]) -> str:
    """One grep call's identity for repeat counting.

    Every argument that changes the result appears, in a fixed order, so
    two calls share an identity exactly when a cache keyed on the call
    could serve the second from the first.
    """
    shape = pattern_shape(args)
    fields = ("pattern", "scope", "glob", "ignore_case", "declared_literal", "context")
    return "\x1f".join(str(shape[field]) for field in fields)


def is_refinement(earlier: str, later: str) -> bool:
    """Whether `later` narrows or widens `earlier`.

    One pattern containing the other is the only relation readable from the
    text alone. It catches adding a qualifier to a pattern and dropping one.
    It misses a reformulation that shares no substring with what it
    replaces.
    """
    if earlier == later:
        return False
    return earlier in later or later in earlier


def percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return float(ordered[index])


def median(values: Iterable[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0
