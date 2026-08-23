#!/usr/bin/python3
"""Collect identity-bound trajectory diagnoses for Foe self-improvement."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parent.parent / "harness_bench"))
from foe_source_identity import evaluated_foe, require_evaluated_foe

MAX_DIAGNOSES = 24
MAX_EVIDENCE_BYTES = 64 * 1024


def input_growth_landmarks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the request rows that locate the start, largest growth, peak, and end."""
    if not rows:
        return []
    deltas = [row.get("input_tokens", 0) - rows[index - 1].get("input_tokens", 0) for index, row in enumerate(rows)]
    indexes = {
        0,
        len(rows) - 1,
        max(range(len(rows)), key=lambda index: rows[index].get("input_tokens", 0)),
        max(range(len(rows)), key=lambda index: deltas[index]),
    }
    return [{**rows[index], "input_growth": deltas[index]} for index in sorted(indexes)]


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
            "episodes",
        )
        if key in report
    }
    answer.update(
        {
            "evaluation": evaluation,
            "usage": compact_usage,
            "input_growth_landmarks": input_growth_landmarks(usage.get("per_request", [])),
            "largest_replayed_results": report.get("largest_replayed_results", [])[:3],
            "tool_failures": report.get("tool_failures", [])[:3],
            "repeated_calls": report.get("repeated_calls", [])[:3],
        }
    )
    return answer


def evaluation_summary(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize outcomes by task, model, and reasoning setting."""
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for report in reports:
        evaluation = report["evaluation"]
        key = (report.get("task", "unknown"), evaluation["model"], evaluation["reasoning_effort"])
        group = groups.setdefault(
            key,
            {
                "task": key[0],
                "model": key[1],
                "reasoning_effort": key[2],
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
        evaluation = {
            "dataset": manifest.get("dataset"),
            "label": manifest.get("label"),
            "model": manifest.get("model"),
            "reasoning_effort": manifest.get("reasoning_effort"),
            "token_limits": manifest.get("token_limits"),
        }
        for path in diagnostic_paths:
            report = json.loads(path.read_text(encoding="utf-8"))
            evidence = report.get("evidence_identity")
            if not isinstance(evidence, dict) or evidence.get("runtime_build") != identity["runtime_binary"]:
                raise ValueError(f"trajectory diagnosis has a different runtime identity: {path}")
            reports.append(compact_diagnosis(report, evaluation))
            if len(reports) > MAX_DIAGNOSES:
                raise ValueError(f"self-improvement evidence exceeds {MAX_DIAGNOSES} trajectory diagnoses")
        runs.append(
            {
                "dataset": manifest.get("dataset"),
                "label": manifest.get("label"),
                "model": manifest.get("model"),
                "reasoning_effort": manifest.get("reasoning_effort"),
                "token_limits": manifest.get("token_limits"),
                "diagnoses": len(diagnostic_paths),
            }
        )
    answer = {
        "schema_version": 2,
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
    answer.add_argument("--output", type=Path, required=True)
    return answer


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = collect(
            args.source_root.resolve(strict=True),
            args.foe.resolve(strict=True),
            [path.resolve(strict=True) for path in args.run_dir],
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
