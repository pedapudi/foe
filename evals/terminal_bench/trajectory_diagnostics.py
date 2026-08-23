#!/usr/bin/python3
"""Produce a bounded, typed diagnosis from one retained Foe trajectory."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_events(log_dir: Path) -> list[dict[str, Any]]:
    path = log_dir / "episode.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"Foe episode log does not exist: {path}")
    events = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        events.append(value)
    return events


def data(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("data")
    return value if isinstance(value, dict) else {}


def request_contains_call(request: dict[str, Any], call_id: str) -> bool:
    for message in data(request).get("messages", []):
        if (
            isinstance(message, dict)
            and message.get("role") == "tool"
            and message.get("call_id") == call_id
        ):
            return True
    return False


def trial_facts(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "task": None,
            "task_checksum": None,
            "reward": None,
            "error": None,
            "estimated_cost_usd": None,
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    verifier = value.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    error = value.get("exception_info")
    agent_result = value.get("agent_result")
    estimated_cost = (
        agent_result.get("cost_usd") if isinstance(agent_result, dict) else None
    )
    return {
        "task": value.get("task_name"),
        "task_checksum": value.get("task_checksum"),
        "reward": reward if isinstance(reward, (int, float)) else None,
        "error": error,
        "estimated_cost_usd": (
            estimated_cost if isinstance(estimated_cost, (int, float)) else None
        ),
    }


def diagnose_episode(
    log_dir: Path,
    *,
    trial_result: Path | None = None,
    top_results: int = 8,
) -> dict[str, Any]:
    """Return evidence that lets an optimizer locate costly or failed behavior."""
    if top_results <= 0:
        raise ValueError("top_results must be positive")
    events = read_events(log_dir)
    starts = [event for event in events if event.get("type") == "episode/start"]
    if len(starts) != 1:
        raise ValueError("root episode log must contain one episode/start event")
    start = data(starts[0])
    requests = [event for event in events if event.get("type") == "model/request"]
    messages = [event for event in events if event.get("type") == "assistant/message"]
    results = [event for event in events if event.get("type") == "tool/result"]

    usage_staircase = []
    for event in messages:
        item = data(event)
        usage = item.get("usage")
        if not isinstance(usage, dict):
            continue
        if not all(isinstance(usage.get(key), int) for key in ("input", "output", "cache_read")):
            continue
        usage_staircase.append(
            {
                "seq": event.get("seq"),
                "step": item.get("step"),
                "input_tokens": usage["input"],
                "cache_read_tokens": usage["cache_read"],
                "output_tokens": usage["output"],
            }
        )

    calls: dict[str, dict[str, Any]] = {}
    call_counts: Counter[tuple[str, str]] = Counter()
    for event in messages:
        for call in data(event).get("tool_calls", []):
            if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                continue
            calls[call["id"]] = call
            key = (
                str(call.get("name")),
                json.dumps(call.get("args"), sort_keys=True, separators=(",", ":")),
            )
            call_counts[key] += 1

    result_rows = []
    failures = []
    for event in results:
        item = data(event)
        rendered = item.get("rendered") if isinstance(item.get("rendered"), str) else ""
        call_id = item.get("call_id")
        replayed = (
            sum(request_contains_call(request, call_id) for request in requests)
            if isinstance(call_id, str)
            else 0
        )
        value = item.get("value") if isinstance(item.get("value"), dict) else {}
        row = {
            "seq": event.get("seq"),
            "step": item.get("step"),
            "call_id": call_id,
            "tool": item.get("name"),
            "subject": item.get("subject"),
            "rendered_characters": len(rendered),
            "canonical_characters": len(json.dumps(item.get("value"), sort_keys=True)),
            "replayed_requests": replayed,
            "replayed_characters": replayed * len(rendered),
            "is_error": bool(item.get("is_error")),
            "exit_code": value.get("exit_code"),
            "timed_out": bool(value.get("timed_out")),
            "truncated": bool(value.get("truncated")),
        }
        result_rows.append(row)
        if row["is_error"] or row["timed_out"] or (
            isinstance(row["exit_code"], int) and row["exit_code"] != 0
        ):
            failures.append(row)

    ends = [event for event in events if event.get("type") == "episode/end"]
    outcome = data(ends[-1]).get("outcome") if ends else None
    facts = trial_facts(trial_result)
    outcome_kind = outcome.get("kind") if isinstance(outcome, dict) else None
    reward = facts["reward"]
    mismatch = None
    if reward is not None and outcome_kind is not None:
        mismatch = (reward == 1.0) != (outcome_kind == "completed")

    repeated_calls = [
        {"tool": key[0], "args": json.loads(key[1]), "count": count}
        for key, count in call_counts.most_common()
        if count > 1
    ][:top_results]
    runtime = start.get("runtime") if isinstance(start.get("runtime"), dict) else {}
    return {
        "schema_version": 1,
        "evidence_identity": {
            "program_identity": start.get("identity"),
            "runtime_build": runtime.get("build"),
            "episode_id": start.get("id"),
            "task_checksum": facts["task_checksum"],
        },
        "task": facts["task"],
        "outcome": outcome,
        "verifier_reward": reward,
        "trial_error": facts["error"],
        "artifact_outcome_mismatch": mismatch,
        "usage": {
            "model_calls": len(requests),
            "tool_results": len(results),
            "input_tokens": sum(row["input_tokens"] for row in usage_staircase),
            "cache_read_tokens": sum(row["cache_read_tokens"] for row in usage_staircase),
            "output_tokens": sum(row["output_tokens"] for row in usage_staircase),
            "estimated_cost_usd": facts["estimated_cost_usd"],
            "per_request": usage_staircase,
        },
        "largest_replayed_results": sorted(
            result_rows,
            key=lambda row: (row["replayed_characters"], row["rendered_characters"]),
            reverse=True,
        )[:top_results],
        "tool_failures": failures[:top_results],
        "repeated_calls": repeated_calls,
    }


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    answer.add_argument("episode", type=Path)
    answer.add_argument("--trial-result", type=Path)
    answer.add_argument("--top-results", type=int, default=8)
    return answer


def main() -> int:
    args = parser().parse_args()
    try:
        report = diagnose_episode(
            args.episode,
            trial_result=args.trial_result,
            top_results=args.top_results,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"trajectory diagnostics: {error}", file=__import__("sys").stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
