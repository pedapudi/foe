#!/usr/bin/python3
"""Collect identity-bound trajectory diagnoses for Foe self-improvement."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parent.parent))
from foe_source_identity import evaluated_foe, require_evaluated_foe
from run import read_cases

MAX_DIAGNOSES = 24
MAX_EVIDENCE_BYTES = 64 * 1024
MAX_INPUT_GROWTH_LANDMARKS = 4
EVALUATION_FIELDS = (
    "dataset",
    "label",
    "model",
    "reasoning_effort",
    "service_tier",
    "token_limits",
)


def input_growth_landmarks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep tree endpoints, peak input, and largest within-episode growth."""
    if not rows:
        return []
    previous_by_episode: dict[str, int] = {}
    deltas = []
    for index, row in enumerate(rows):
        episode_id = row.get("episode_id")
        input_tokens = row.get("input_tokens")
        if not isinstance(episode_id, str) or not episode_id:
            raise ValueError(f"trajectory request {index} has no string episode_id")
        if not isinstance(input_tokens, int):
            raise ValueError(f"trajectory request {index} has no integer input_tokens")
        previous = previous_by_episode.get(episode_id)
        deltas.append(0 if previous is None else input_tokens - previous)
        previous_by_episode[episode_id] = input_tokens
    indexes = {
        0,
        len(rows) - 1,
        max(range(len(rows)), key=lambda index: rows[index].get("input_tokens", 0)),
        max(range(len(rows)), key=lambda index: deltas[index]),
    }
    selected = sorted(indexes)[:MAX_INPUT_GROWTH_LANDMARKS]
    return [{**rows[index], "input_growth": deltas[index]} for index in selected]


def evaluation_metadata(manifest: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    """Return the complete execution setting required for causal comparison."""
    answer: dict[str, Any] = {}
    for field in EVALUATION_FIELDS:
        value = manifest.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Terminal-Bench manifest {manifest_path} has no string `{field}`")
        answer[field] = value
    concurrency = manifest.get("concurrency")
    if type(concurrency) is not int or concurrency not in (1, 2):
        raise ValueError(
            f"Terminal-Bench manifest {manifest_path} has invalid `concurrency`"
        )
    requested_workers = manifest.get("requested_workers", concurrency)
    if type(requested_workers) is not int or requested_workers not in (1, 2):
        raise ValueError(
            f"Terminal-Bench manifest {manifest_path} has invalid `requested_workers`"
        )
    answer["concurrency"] = concurrency
    answer["requested_workers"] = requested_workers
    configuration: dict[str, Any] = {
        "service_tier": answer["service_tier"],
        "token_policy": answer["token_limits"],
        "task_execution": {
            "requested_workers": requested_workers,
            "scheduled_concurrency": concurrency,
        },
        "implementation": {
            "model": answer["model"],
            "reasoning_effort": answer["reasoning_effort"],
        }
    }
    optional_stages = {
        "diagnosis": (
            ("model", "diagnosis_model", str),
            ("reasoning_effort", "diagnosis_reasoning_effort", str),
            ("model_calls", "diagnosis_model_calls", int),
        ),
        "unresolved_diagnosis": (
            ("reasoning_effort", "unresolved_diagnosis_reasoning_effort", str),
            ("model_calls", "unresolved_diagnosis_model_calls", int),
        ),
        "independent_audit": (
            ("reasoning_effort", "escalation_reasoning_effort", str),
            ("model_calls", "escalation_model_calls", int),
        ),
    }
    for stage, fields in optional_stages.items():
        values = [manifest.get(source) for _, source, _ in fields]
        if all(value is None for value in values):
            continue
        if any(value is None for value in values):
            raise ValueError(
                f"Terminal-Bench manifest {manifest_path} has incomplete `{stage}` settings"
            )
        stage_value = {}
        for target, source, expected in fields:
            value = manifest.get(source)
            if expected is int:
                valid = type(value) is int and value > 0
            else:
                valid = isinstance(value, expected) and bool(value)
            if not valid:
                raise ValueError(
                    f"Terminal-Bench manifest {manifest_path} has invalid `{source}`"
                )
            stage_value[target] = value
        if stage in ("independent_audit", "unresolved_diagnosis"):
            stage_value["model"] = answer["model"]
        configuration[stage] = stage_value
    checker = manifest.get("completion_checker")
    if checker is not None:
        digest = checker.get("sha256") if isinstance(checker, dict) else None
        if not isinstance(digest, str) or not digest:
            raise ValueError(
                f"Terminal-Bench manifest {manifest_path} has invalid `completion_checker`"
            )
        configuration["completion_verifier"] = {"sha256": digest}
    answer["execution_configuration"] = configuration
    return answer


def request_rows(report: dict[str, Any], usage: dict[str, Any]) -> list[dict[str, Any]]:
    """Return request rows with an episode identity for every schema version."""
    rows = usage.get("per_request", [])
    if not isinstance(rows, list):
        raise ValueError("trajectory usage.per_request is not a list")
    root_identity = report.get("evidence_identity")
    root_episode = root_identity.get("episode_id") if isinstance(root_identity, dict) else None
    answer = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"trajectory request {index} is not an object")
        if isinstance(row.get("episode_id"), str):
            answer.append(row)
        elif report.get("schema_version") == 1 and isinstance(root_episode, str):
            answer.append({**row, "episode_id": root_episode})
        else:
            raise ValueError(f"trajectory request {index} has no string episode_id")
    return answer


def compact_diagnosis(report: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    """Retain causal landmarks and remove request-by-request repetition."""
    usage = report.get("usage") if isinstance(report.get("usage"), dict) else {}
    compact_usage = {key: value for key, value in usage.items() if key != "per_request"}
    answer = {
        key: report.get(key)
        for key in (
            "schema_version",
            "evidence_identity",
            "task",
            "outcome",
            "verifier_reward",
            "trial_error",
            "artifact_outcome_mismatch",
            "verifier_feedback",
            "episodes",
            "verification_timeline",
        )
        if key in report
    }
    answer.update(
        {
            "evaluation": evaluation,
            "usage": compact_usage,
            "input_growth_landmarks": input_growth_landmarks(request_rows(report, usage)),
            "largest_replayed_results": report.get("largest_replayed_results", [])[:3],
            "tool_failures": report.get("tool_failures", [])[:3],
            "repeated_calls": report.get("repeated_calls", [])[:3],
        }
    )
    return answer


def evaluation_summary(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize outcomes by task and complete execution configuration."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for report in reports:
        evaluation = report["evaluation"]
        task = report.get("task")
        configuration = evaluation["execution_configuration"]
        key = (
            task if isinstance(task, str) and task else "unknown",
            json.dumps(configuration, sort_keys=True, separators=(",", ":")),
        )
        group = groups.setdefault(
            key,
            {
                "task": key[0],
                "model": evaluation["model"],
                "reasoning_effort": evaluation["reasoning_effort"],
                "execution_configuration": configuration,
                "attempts": 0,
                "verified_successes": 0,
                "artifact_outcome_mismatches": 0,
                "model_calls": 0,
                "estimated_cost_usd": 0.0,
            },
        )
        group["attempts"] += 1
        reward = report.get("verifier_reward")
        group["verified_successes"] += int(isinstance(reward, (int, float)) and reward > 0)
        group["artifact_outcome_mismatches"] += int(report.get("artifact_outcome_mismatch") is True)
        usage = report.get("usage", {})
        group["model_calls"] += usage.get("model_calls", 0) or 0
        group["estimated_cost_usd"] += usage.get("estimated_cost_usd", 0.0) or 0.0
    return [groups[key] for key in sorted(groups)]


def collect(
    source_root: Path,
    binary: Path,
    run_dirs: list[Path],
    eligible_tasks: set[str],
) -> dict[str, Any]:
    if not run_dirs:
        raise ValueError("at least one retained Terminal-Bench run is required")
    identity = evaluated_foe(source_root, binary)
    reports = []
    runs = []
    for run_dir in run_dirs:
        manifest_path = run_dir / "campaign.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_identity = require_evaluated_foe(
            manifest.get("evaluated_foe"), f"Terminal-Bench manifest {manifest_path}"
        )
        if manifest_identity != identity:
            raise ValueError(
                f"Terminal-Bench manifest {manifest_path} evaluates a different Foe source or binary"
            )
        diagnostic_paths = sorted(run_dir.glob("*/*/agent/foe-diagnostics.json"))
        if not diagnostic_paths:
            raise ValueError(f"Terminal-Bench run has no Foe diagnostics: {run_dir}")
        evaluation = evaluation_metadata(manifest, manifest_path)
        for path in diagnostic_paths:
            report = json.loads(path.read_text(encoding="utf-8"))
            task = report.get("task")
            task_name = task.rsplit("/", 1)[-1] if isinstance(task, str) else None
            if task_name not in eligible_tasks:
                raise ValueError(
                    f"trajectory diagnosis is outside development evidence: {path}"
                )
            evidence = report.get("evidence_identity")
            if not isinstance(evidence, dict) or evidence.get("runtime_build") != identity["runtime_binary"]:
                raise ValueError(f"trajectory diagnosis has a different runtime identity: {path}")
            reports.append(compact_diagnosis(report, evaluation))
            if len(reports) > MAX_DIAGNOSES:
                raise ValueError(f"self-improvement evidence exceeds {MAX_DIAGNOSES} trajectory diagnoses")
        runs.append({**evaluation, "diagnoses": len(diagnostic_paths)})
    answer = {
        "schema_version": 3,
        "evaluated_foe": identity,
        "runs": runs,
        "evaluation_summary": evaluation_summary(reports),
        "trajectory_diagnostics": reports,
    }
    size = len(json.dumps(answer, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if size > MAX_EVIDENCE_BYTES:
        raise ValueError(
            f"self-improvement evidence is {size} bytes; select fewer runs to stay within {MAX_EVIDENCE_BYTES} bytes"
        )
    return answer


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    answer.add_argument("--source-root", type=Path, required=True)
    answer.add_argument("--foe", type=Path, required=True)
    answer.add_argument("--run-dir", type=Path, action="append", required=True)
    answer.add_argument("--cases", type=Path, required=True)
    answer.add_argument("--output", type=Path, required=True)
    return answer


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        _, groups, _, _ = read_cases(args.cases.resolve(strict=True))
        eligible_tasks = set(groups["development"]) | set(groups["capability_search"])
        report = collect(
            args.source_root.resolve(strict=True),
            args.foe.resolve(strict=True),
            [path.resolve(strict=True) for path in args.run_dir],
            eligible_tasks,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"collect diagnostics: {error}", file=sys.stderr)
        return 2
    print(f"Self-improvement evidence: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
