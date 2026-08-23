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
    diagnosis_model_name: str | None = None,
    diagnosis_reasoning_effort: str = "high",
    diagnosis_model_calls: int = 6,
    escalation_reasoning_effort: str | None = None,
    escalation_model_calls: int = 0,
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
    program = {
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
    if diagnosis_model_name is None:
        return program
    if "/" not in diagnosis_model_name:
        raise ValueError("diagnosis model must have the form provider/model")
    diagnosis_provider, diagnosis_model = diagnosis_model_name.split("/", 1)
    if not diagnosis_provider or not diagnosis_model:
        raise ValueError("diagnosis model must have the form provider/model")
    if not 2 <= diagnosis_model_calls < model_calls:
        raise ValueError("diagnosis model calls must be at least two and below the total model-call allowance")
    if escalation_reasoning_effort is None and escalation_model_calls != 0:
        raise ValueError("escalation model calls require an escalation reasoning effort")
    if escalation_reasoning_effort is not None and escalation_model_calls < 2:
        raise ValueError("escalation model calls must be at least two")
    if diagnosis_model_calls + escalation_model_calls >= model_calls:
        raise ValueError("diagnosis and escalation calls must leave at least one implementation call")
    diagnosis_seconds = min(180, max(60, seconds // 4))
    working_seconds = seconds - diagnosis_seconds
    escalation_seconds = working_seconds // 2 if escalation_reasoning_effort is not None else 0
    implementation_seconds = working_seconds - escalation_seconds
    implementation_calls = model_calls - diagnosis_model_calls - escalation_model_calls
    investigation_calls = diagnosis_model_calls - 1
    shared_grants = {"read": [working_directory, "/"], "write": ["/"]}
    diagnosis_schema = {
        "type": "object",
        "properties": {
            "constraints": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "observations": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "implementation_steps": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "verification_steps": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "risks": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        },
        "required": ["constraints", "observations", "implementation_steps", "verification_steps", "risks"],
        "additionalProperties": False,
    }
    program["budget"].update({"max_episodes": 3, "max_concurrent": 1})
    program["workflow"] = {
        "nodes": {
            "diagnose-task": {
                "model": {
                    "name": "diagnose-coding-task",
                    "instructions": {
                        "role": (
                            "Analyze the task and repository without implementing the task. "
                            "Use read and grep for focused evidence collection. Identify constraints, "
                            "implementation steps, verification steps, and failure risks. "
                            f"Use no more than {investigation_calls} request(s) for inspection. "
                            "On the final request, call return with the best supported diagnosis, "
                            "including uncertainty under risks. Keep the return concise."
                        )
                    },
                    "tools": ["read", "grep"],
                    "grants": {"read": [working_directory, "/"]},
                    "budget": {"model_calls": diagnosis_model_calls, "seconds": diagnosis_seconds},
                    "done_when": {"returns": diagnosis_schema},
                    "model": {
                        "provider": diagnosis_provider,
                        "model": diagnosis_model,
                        "reasoning_effort": diagnosis_reasoning_effort,
                        "token_file": credential_path,
                    },
                },
                "follows": ["task"],
            },
            "implement-task": {
                "model": {
                    "name": "implement-diagnosed-task",
                    "instructions": {
                        "role": "Implement the task using the typed diagnosis as advice. Confirm its claims against the repository. Make the requested change, run the strongest available verification after the final change, and leave files and services in the state the task requires."
                    },
                    "tools": ["read", "grep", "edit", "bash"],
                    "grants": shared_grants,
                    "budget": {
                        "model_calls": implementation_calls,
                        "seconds": implementation_seconds,
                    },
                    "model": {
                        "provider": provider,
                        "model": model,
                        "reasoning_effort": reasoning_effort,
                        "token_file": credential_path,
                    },
                },
                "follows": ["task", "diagnose-task"],
                "terminal": escalation_reasoning_effort is None,
            },
        },
        "recovery": {"enabled": False},
    }
    if escalation_reasoning_effort is not None:
        program["budget"]["max_episodes"] = 4
        program["workflow"]["nodes"]["audit-and-repair-task"] = {
            "model": {
                "name": "audit-and-repair-task",
                "instructions": {
                    "role": (
                        "Audit the existing implementation produced by another coding episode. "
                        "Treat its completion claim as unverified. Inspect the current workspace, "
                        "run representative behavioral tests, and repair every defect you find. "
                        "Finish with the task-required files and services in their required state."
                    )
                },
                "tools": ["read", "grep", "edit", "bash"],
                "grants": shared_grants,
                "budget": {
                    "model_calls": escalation_model_calls,
                    "seconds": escalation_seconds,
                },
                "model": {
                    "provider": provider,
                    "model": model,
                    "reasoning_effort": escalation_reasoning_effort,
                    "token_file": credential_path,
                },
            },
            "follows": ["task", "implement-task"],
            "terminal": True,
        }
    return program


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
    pricing: dict[str, float | int] | dict[str, dict[str, float | int]] | None = None,
) -> dict[str, Any]:
    """Measure usage and read the root outcome from a retained episode tree."""
    root_path = log_dir / "episode.jsonl"
    if not root_path.is_file():
        raise FileNotFoundError(f"Foe episode log does not exist: {root_path}")
    paths = sorted(log_dir.rglob("episode.jsonl"))
    calls = 0
    tool_calls = 0
    messages: list[tuple[str | None, dict[str, Any]]] = []
    outcome: dict[str, Any] | None = None
    for path in paths:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        starts = [event for event in events if event.get("type") == "episode/start"]
        route = None
        if starts:
            start_data = starts[0].get("data") if isinstance(starts[0].get("data"), dict) else {}
            program = start_data.get("program") if isinstance(start_data.get("program"), dict) else {}
            model = program.get("model") if isinstance(program.get("model"), dict) else {}
            if isinstance(model.get("provider"), str) and isinstance(model.get("model"), str):
                route = f"{model['provider']}/{model['model']}"
        for event in events:
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            event_type = event.get("type")
            if event_type == "model/request":
                calls += 1
            elif event_type == "tool/result":
                tool_calls += 1
            elif event_type == "assistant/message":
                messages.append((route, data))
            elif path == root_path and event_type == "episode/end":
                value = data.get("outcome")
                if isinstance(value, dict):
                    outcome = value

    totals = {"input": 0, "output": 0, "cache_read": 0}
    usages: list[dict[str, int]] = []
    measured = 0
    priced_usages: list[tuple[str | None, dict[str, int]]] = []
    for route, message in messages:
        item = message.get("usage")
        if not isinstance(item, dict) or not all(isinstance(item.get(key), int) for key in totals):
            continue
        measured += 1
        usage = {key: item[key] for key in totals}
        usages.append(usage)
        priced_usages.append((route, usage))
        for key in totals:
            totals[key] += item[key]
    complete = bool(messages) and measured == len(messages)
    estimated_cost = None
    if complete and pricing is not None:
        if "input_per_million" in pricing:
            estimated_cost = estimate_usage_cost(usages, **pricing)
        elif all(route in pricing for route, _ in priced_usages):
            estimated_cost = sum(
                estimate_usage_cost([usage], **pricing[route])
                for route, usage in priced_usages
                if route is not None
            )
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
