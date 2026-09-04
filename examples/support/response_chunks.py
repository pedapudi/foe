#!/usr/bin/python3
"""Build chunks for deterministic responses supplied by an example host."""

import json
from typing import Any

USAGE = {"input": 0, "output": 0, "cache_read": 0}
Chunks = list[dict[str, Any]]


def emit(chunks: Chunks, chunk: dict[str, Any]) -> None:
    """Append one chunk to a response."""
    chunks.append(chunk)


def text(chunks: Chunks, delta: str) -> None:
    """Append assistant text."""
    emit(chunks, {"kind": "text", "delta": delta})


def call(chunks: Chunks, call_id: str, name: str, args: dict) -> None:
    """Append one complete tool call."""
    emit(chunks, {"kind": "tool_call_start", "id": call_id, "name": name})
    emit(chunks, {"kind": "tool_call_delta", "id": call_id, "delta": json.dumps(args)})
    emit(chunks, {"kind": "tool_call_end", "id": call_id})


def done(chunks: Chunks, stop: str, usage: dict | None = None) -> None:
    """Append the terminal chunk for one answer."""
    emit(chunks, {"kind": "done", "stop": stop, "usage": usage or USAGE})


def error(chunks: Chunks, message: str, retryable: bool = False) -> None:
    """Append an error that ends one answer."""
    emit(chunks, {"kind": "error", "message": message, "retryable": retryable})


def step(request: dict) -> int:
    """How many answers this episode has already received, counting from zero."""
    return sum(1 for message in request["messages"] if message["role"] == "assistant")


def tool_names(request: dict) -> set:
    """The names of the tools offered to this request."""
    return {tool["name"] for tool in request["tools"]}
