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
    working_directory: str,
    *,
    model_calls: int,
    input_tokens: int | None,
    output_tokens: int | None,
    seconds: int,
    reasoning_effort: str,
) -> dict[str, Any]:
    """Build the recorded Foe program used for one Terminal-Bench trial."""
    if "/" not in model_name:
        raise ValueError("model must have the form provider/model")
    provider, model = model_name.split("/", 1)
    if not provider or not model:
        raise ValueError("model must have the form provider/model")
    limits = {"model_calls": model_calls, "seconds": seconds}
    optional_limits = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if any(not isinstance(value, int) or value <= 0 for value in limits.values()):
        raise ValueError("model call and time allowances must be positive integers")
    if any(
        value is not None and (not isinstance(value, int) or value <= 0)
        for value in optional_limits.values()
    ):
        raise ValueError("token allowances must be positive integers when present")
    if not working_directory.startswith("/"):
        raise ValueError("working directory must be an absolute path")
    limits.update({key: value for key, value in optional_limits.items() if value is not None})
    return {
        "version": 2,
        "name": "terminal-bench-coding",
        "instructions": {"role": CODING_INSTRUCTION},
        "tools": ["read", "grep", "edit", "bash"],
        "grants": {"read": [working_directory, "/"], "write": ["/"]},
        "budget": limits,
        "model": {
            "provider": provider,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "token_file": credential_path,
        },
        "sandbox": {"mode": "off"},
        "task": instruction,
    }


def estimate_usage_cost(
    usages: list[dict[str, int]],
    *,
    input_per_million: float,
    cached_input_per_million: float,
    output_per_million: float,
    long_context_threshold: int,
    long_context_input_multiplier: float,
    long_context_output_multiplier: float,
) -> float:
    """Estimate route cost request by request from provider-reported usage."""
    total = 0.0
    for usage in usages:
        input_tokens = usage["input"]
        cached_tokens = max(0, min(usage["cache_read"], input_tokens))
        uncached_tokens = input_tokens - cached_tokens
        long_request = input_tokens > long_context_threshold
        input_multiplier = long_context_input_multiplier if long_request else 1.0
        output_multiplier = long_context_output_multiplier if long_request else 1.0
        total += input_multiplier * (
            uncached_tokens * input_per_million
            + cached_tokens * cached_input_per_million
        ) / 1_000_000
        total += (
            output_multiplier * usage["output"] * output_per_million / 1_000_000
        )
    return total


def read_episode_summary(
    log_dir: Path,
    pricing: dict[str, float | int] | None = None,
) -> dict[str, Any]:
    """Measure usage and read the root outcome from a retained episode tree."""
    root_path = log_dir / "episode.jsonl"
    if not root_path.is_file():
        raise FileNotFoundError(f"Foe episode log does not exist: {root_path}")
    paths = sorted(log_dir.rglob("episode.jsonl"))
    calls = 0
    tool_calls = 0
    messages: list[dict[str, Any]] = []
    outcome: dict[str, Any] | None = None
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
    usages: list[dict[str, int]] = []
    measured = 0
    for message in messages:
        item = message.get("usage")
        if not isinstance(item, dict) or not all(isinstance(item.get(key), int) for key in totals):
            continue
        measured += 1
        usages.append({key: item[key] for key in totals})
        for key in totals:
            totals[key] += item[key]
    complete = bool(messages) and measured == len(messages)
    estimated_cost = None
    if complete and pricing is not None:
        estimated_cost = estimate_usage_cost(usages, **pricing)
    return {
        "model_calls": calls,
        "tool_calls": tool_calls,
        "model_responses": len(messages),
        "responses_with_usage": measured,
        "usage_reported": complete,
        "input_tokens": totals["input"] if complete else None,
        "output_tokens": totals["output"] if complete else None,
        "cache_read_tokens": totals["cache_read"] if complete else None,
        "estimated_cost_usd": estimated_cost,
        "outcome": outcome,
    }


def replace_credential_state(downloaded: Path, state: Path) -> None:
    """Validate and atomically install a refreshed private credential copy."""
    value = json.loads(downloaded.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value:
        raise ValueError("refreshed credential must be a non-empty JSON object")
    os.chmod(downloaded, 0o600)
    os.replace(downloaded, state)
