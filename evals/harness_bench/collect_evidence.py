#!/usr/bin/python3
"""Reduce retained micro and Harness-Bench logs to self-improvement evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return value


def events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def data(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("data")
    return value if isinstance(value, dict) else {}


def log_summary(path: Path) -> dict[str, Any]:
    values = events(path)
    starts = [data(event) for event in values if event.get("type") == "episode/start"]
    ends = [data(event).get("outcome") for event in values if event.get("type") == "episode/end"]
    assistant = [data(event) for event in values if event.get("type") == "assistant/message"]
    return {
        "log": str(path),
        "program": starts[0].get("program", {}).get("name") if starts else None,
        "outcome": ends[-1] if ends else None,
        "model_calls": sum(event.get("type") == "model/request" for event in values),
        "last_assistant_steps": [
            {
                "step": message.get("step"),
                "stop": message.get("stop"),
                "text": str(message.get("text", ""))[:240],
                "tools": [call.get("name") for call in message.get("tool_calls", []) if isinstance(call, dict)],
            }
            for message in assistant[-4:]
        ],
    }


def request_progression(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "step": message.get("step"),
            "input_tokens": usage.get("input"),
            "output_tokens": usage.get("output"),
            "cache_read_tokens": usage.get("cache_read"),
        }
        for event in values
        if event.get("type") == "assistant/message"
        for message in [data(event)]
        for usage in [message.get("usage", {})]
        if isinstance(usage, dict)
    ]


def compact_arguments(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("path"), str):
        path = value["path"]
        marker = path.find("/workspace/")
        return {"path": path[marker + len("/workspace/") :] if marker >= 0 else path}
    if isinstance(value.get("command"), str):
        command = value["command"]
        return {"command": command[:240] + ("..." if len(command) > 240 else "")}
    if isinstance(value.get("args"), list):
        return {"args": [str(item)[:160] for item in value["args"]]}
    if isinstance(value.get("edits"), list):
        return {"edit_count": len(value["edits"])}
    return {key: str(item)[:160] for key, item in value.items()}


def replay_attribution(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    request_steps = [data(event).get("step") for event in values if event.get("type") == "model/request"]
    calls: dict[str, dict[str, Any]] = {}
    for event in values:
        if event.get("type") != "assistant/message":
            continue
        for call in data(event).get("tool_calls", []):
            if isinstance(call, dict) and isinstance(call.get("id"), str):
                calls[call["id"]] = call
    rows = []
    for event in values:
        if event.get("type") != "tool/result":
            continue
        result = data(event)
        step = result.get("step")
        rendered = result.get("rendered")
        rendered_chars = len(rendered) if isinstance(rendered, str) else 0
        later = sum(isinstance(candidate, int) and isinstance(step, int) and candidate > step for candidate in request_steps)
        call = calls.get(result.get("call_id"), {})
        rows.append(
            {
                "step": step,
                "tool": result.get("name") or call.get("name"),
                "arguments": compact_arguments(call.get("args")),
                "rendered_chars": rendered_chars,
                "later_requests": later,
                "replayed_characters": rendered_chars * later,
            }
        )
    return sorted(rows, key=lambda row: row["replayed_characters"], reverse=True)[:8]


def failed_checks(grade: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"id": check.get("id"), "detail": check.get("detail")}
        for check in grade.get("checks", [])
        if isinstance(check, dict) and check.get("pass") is not True
    ]


def collect(micro_report: Path, harness_report: Path) -> dict[str, Any]:
    micro = read_json(micro_report)
    harness = read_json(harness_report)
    micro_rows = []
    for result in micro.get("results", []):
        if not isinstance(result, dict):
            continue
        case = Path(result["artifact_directory"])
        logs = sorted((case / "episode").rglob("episode.jsonl"))
        micro_rows.append(
            {
                "task": result.get("task"),
                "strict_success": result.get("strict_success"),
                "components": result.get("components"),
                "outcome": result.get("outcome"),
                "usage": result.get("usage"),
                "grader_findings": result.get("grader_findings"),
                "mechanism": result.get("mechanism"),
                "logs": [log_summary(path) for path in logs] if result.get("strict_success") is not True else [],
            }
        )
    harness_rows = []
    for result in harness.get("attempts", []):
        if not isinstance(result, dict):
            continue
        log = Path(result["paths"]["episode"]) / "episode.jsonl"
        values = events(log)
        grade = result.get("programmatic_grade", {})
        harness_rows.append(
            {
                "task": result.get("task"),
                "programmatic_score": grade.get("outcome_score", grade.get("score")),
                "failed_checks": failed_checks(grade),
                "outcome": result.get("foe_outcome"),
                "duration_seconds": result.get("duration_seconds"),
                "usage": result.get("usage"),
                "trace_conformant": result.get("trace_conformant"),
                "request_progression": request_progression(values),
                "largest_replayed_tool_results": replay_attribution(values),
            }
        )
    correct_but_incomplete = sum(
        row.get("components", {}).get("artifact_correct") is True
        and row.get("components", {}).get("outcome_correct") is False
        for row in micro_rows
    )
    return {
        "schema_version": 1,
        "purpose": "Evidence for one general Foe runtime improvement. Benchmark-specific rules are excluded from candidate source.",
        "micro": {"aggregate": micro.get("aggregate"), "attempts": micro_rows},
        "harness_bench": {"summary": harness.get("summary"), "attempts": harness_rows},
        "observations": {
            "micro_correct_artifact_without_completed_outcome": correct_but_incomplete,
            "harness_attempts": len(harness_rows),
            "harness_conformant_traces": sum(row.get("trace_conformant") is True for row in harness_rows),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--micro-report", type=Path, required=True)
    parser.add_argument("--harness-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = collect(args.micro_report, args.harness_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
