#!/usr/bin/python3
"""Pure helpers for the Foe Harbor adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


CODING_INSTRUCTION = (
    "You are a coding agent working in the current directory, which is the root "
    "of every relative path. Make the requested change, then verify it by running "
    "the relevant build or tests before you finish."
)


def build_program(
    instruction: str,
    model_name: str,
    credential_path: str,
    *,
    model_calls: int,
    input_tokens: int,
    output_tokens: int,
    seconds: int,
    reasoning_effort: str,
) -> dict[str, Any]:
    """Build the recorded Foe program used for one Terminal-Bench trial."""
    if "/" not in model_name:
        raise ValueError("model must have the form provider/model")
    provider, model = model_name.split("/", 1)
    if not provider or not model:
        raise ValueError("model must have the form provider/model")
    limits = {
        "model_calls": model_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "seconds": seconds,
    }
    if any(not isinstance(value, int) or value <= 0 for value in limits.values()):
        raise ValueError("every Foe allowance must be a positive integer")
    return {
        "version": 2,
        "name": "terminal-bench-coding",
        "instructions": {"role": CODING_INSTRUCTION},
        "tools": ["read", "grep", "edit", "bash"],
        "grants": {"read": ["/"], "write": ["/"]},
        "budget": limits,
        "model": {
            "provider": provider,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "api_key_file": credential_path,
        },
        "sandbox": {"mode": "off"},
        "task": instruction,
    }


def read_episode_summary(log_dir: Path) -> dict[str, Any]:
    """Measure usage and read the root outcome from a retained episode tree."""
    paths = sorted(log_dir.rglob("episode.jsonl")) if log_dir.is_dir() else []
    calls = 0
    tool_calls = 0
    messages: list[dict[str, Any]] = []
    outcome: dict[str, Any] | None = None
    root_path = log_dir / "episode.jsonl"
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            event_type = event.get("type")
            if event_type == "model/request":
                calls += 1
            elif event_type == "tool/result":
                tool_calls += 1
            elif event_type == "assistant/message":
                messages.append(data)
            elif path == root_path and event_type == "episode/end":
                value = data.get("outcome")
                if isinstance(value, dict):
                    outcome = value

    totals = {"input": 0, "output": 0, "cache_read": 0}
    measured = 0
    for message in messages:
        item = message.get("usage")
        if not isinstance(item, dict) or not all(isinstance(item.get(key), int) for key in totals):
            continue
        measured += 1
        for key in totals:
            totals[key] += item[key]
    complete = bool(messages) and measured == len(messages)
    return {
        "model_calls": calls,
        "tool_calls": tool_calls,
        "model_responses": len(messages),
        "responses_with_usage": measured,
        "usage_reported": complete,
        "input_tokens": totals["input"] if complete else None,
        "output_tokens": totals["output"] if complete else None,
        "cache_read_tokens": totals["cache_read"] if complete else None,
        "outcome": outcome,
    }


def replace_credential_state(downloaded: Path, state: Path) -> None:
    """Validate and atomically install a refreshed private credential copy."""
    value = json.loads(downloaded.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value:
        raise ValueError("refreshed credential must be a non-empty JSON object")
    os.chmod(downloaded, 0o600)
    os.replace(downloaded, state)
