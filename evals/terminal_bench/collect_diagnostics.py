#!/usr/bin/python3
"""Collect identity-bound trajectory diagnoses for Foe self-improvement."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parent.parent))
from foe_source_identity import evaluated_foe, require_evaluated_foe
from trajectory_corpus import load_manifest, read_object

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


def development_tasks(cases: Path) -> set[str]:
    """Return task names admitted into self-improvement evidence."""
    value = json.loads(cases.read_text(encoding="utf-8"))
    groups = value.get("groups") if isinstance(value, dict) else None
    if not isinstance(groups, dict):
        raise ValueError(f"Terminal-Bench cases file has no `groups` object: {cases}")
    answer: set[str] = set()
    for group in ("development", "capability_search"):
        tasks = groups.get(group)
        if not isinstance(tasks, list) or not all(
            isinstance(task, str) and task for task in tasks
        ):
            raise ValueError(
                f"Terminal-Bench cases file has no string `{group}` task list: {cases}"
            )
        answer.update(tasks)
    return answer


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
        reward = eligible_trial_reward(report)
        if reward is None:
            continue
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
        group["verified_successes"] += int(reward > 0)
        group["artifact_outcome_mismatches"] += int(report.get("artifact_outcome_mismatch") is True)
        usage = report.get("usage", {})
        group["model_calls"] += usage.get("model_calls", 0) or 0
        group["estimated_cost_usd"] += usage.get("estimated_cost_usd", 0.0) or 0.0
    return [groups[key] for key in sorted(groups)]


def eligible_trial_reward(report: dict[str, Any]) -> int | float | None:
    """Return a numeric reward for a trial without an infrastructure error."""
    if report.get("trial_error") is not None:
        return None
    reward = report.get("verifier_reward")
    return reward if type(reward) in (int, float) else None


def collect(
    source_root: Path,
    binary: Path,
    run_dirs: list[Path],
    eligible_tasks: set[str],
) -> dict[str, Any]:
    if not run_dirs:
        raise ValueError("at least one retained Terminal-Bench run is required")
    identity = evaluated_foe(source_root, binary)
    documents = []
    for run_dir in run_dirs:
        manifest_path = run_dir / "campaign.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        diagnostic_paths = sorted(run_dir.glob("*/*/agent/foe-diagnostics.json"))
        documents.append(
            (
                manifest_path,
                manifest,
                [
                    (path, json.loads(path.read_text(encoding="utf-8")))
                    for path in diagnostic_paths
                ],
            )
        )
    return collect_documents(identity, documents, eligible_tasks)


def collect_documents(
    identity: dict[str, str],
    documents: list[tuple[Path, dict[str, Any], list[tuple[Path, dict[str, Any]]]]],
    eligible_tasks: set[str],
) -> dict[str, Any]:
    """Build one bounded report from verified trajectory documents."""
    reports = []
    runs = []
    for manifest_path, manifest, diagnostics in documents:
        manifest_identity = require_evaluated_foe(
            manifest.get("evaluated_foe"), f"Terminal-Bench manifest {manifest_path}"
        )
        if manifest_identity != identity:
            raise ValueError(
                f"Terminal-Bench manifest {manifest_path} evaluates a different Foe source or binary"
            )
        if not diagnostics:
            raise ValueError(
                f"Terminal-Bench run has no Foe diagnostics: {manifest_path.parent}"
            )
        evaluation = evaluation_metadata(manifest, manifest_path)
        for path, report in diagnostics:
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
        runs.append({**evaluation, "diagnoses": len(diagnostics)})
    answer = {
        "schema_version": 4,
        "evaluated_foe": identity,
        "runs": runs,
        "evaluation_summary": evaluation_summary(reports),
        "trajectory_diagnostics": reports,
    }
    size = len(encoded_evidence(answer).encode("utf-8"))
    if size > MAX_EVIDENCE_BYTES:
        raise ValueError(
            f"self-improvement evidence is {size} bytes; select fewer runs to stay within {MAX_EVIDENCE_BYTES} bytes"
        )
    return answer


def encoded_evidence(report: dict[str, Any]) -> str:
    """Encode one diagnostic report in its recorded representation."""
    return json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"


def collect_from_corpus(
    corpus_manifest: Path,
    cases: Path,
    expected_identity: dict[str, str],
) -> dict[str, Any]:
    """Collect diagnostics from an immutable trajectory corpus."""
    manifest, corpus_root = load_manifest(corpus_manifest)
    if read_object(corpus_root, manifest.get("cases")) != cases.read_bytes():
        raise ValueError("trajectory corpus was selected by a different cases file")
    identity = require_evaluated_foe(
        manifest.get("evaluated_foe"), f"trajectory corpus {corpus_manifest}"
    )
    if identity != expected_identity:
        raise ValueError("trajectory corpus evaluates a different Foe source or binary")
    runs = manifest.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError(f"trajectory corpus has no runs: {corpus_manifest}")
    documents = []
    for index, run in enumerate(runs):
        entries = run.get("files") if isinstance(run, dict) else None
        if not isinstance(entries, list):
            raise ValueError(f"trajectory corpus run {index} has no files")
        campaign_entries = [
            entry for entry in entries if entry.get("path") == "campaign.json"
        ]
        if len(campaign_entries) != 1:
            raise ValueError(f"trajectory corpus run {index} has no campaign.json")
        diagnostics = []
        for entry in entries:
            name = entry.get("path")
            if isinstance(name, str) and name.endswith(
                "/agent/foe-diagnostics.json"
            ):
                diagnostics.append(
                    (
                        Path(f"corpus-run-{index}") / name,
                        json.loads(read_object(corpus_root, entry)),
                    )
                )
        documents.append(
            (
                Path(f"corpus-run-{index}/campaign.json"),
                json.loads(read_object(corpus_root, campaign_entries[0])),
                diagnostics,
            )
        )
    return collect_documents(identity, documents, development_tasks(cases))


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    answer.add_argument("--source-root", type=Path)
    answer.add_argument("--foe", type=Path)
    answer.add_argument("--run-dir", type=Path, action="append")
    answer.add_argument("--corpus", type=Path)
    answer.add_argument("--expected-source-tree")
    answer.add_argument("--expected-runtime-binary")
    answer.add_argument("--expected-report-sha256")
    answer.add_argument("--cases", type=Path, required=True)
    answer.add_argument("--output", type=Path)
    return answer


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        cases = args.cases.resolve(strict=True)
        if args.corpus is not None:
            if args.run_dir or args.source_root or args.foe:
                raise ValueError(
                    "--corpus cannot be combined with --run-dir, --source-root, or --foe"
                )
            if not args.expected_source_tree or not args.expected_runtime_binary:
                raise ValueError(
                    "--corpus requires --expected-source-tree and --expected-runtime-binary"
                )
            report = collect_from_corpus(
                args.corpus.resolve(strict=True),
                cases,
                {
                    "source_tree": args.expected_source_tree,
                    "runtime_binary": args.expected_runtime_binary,
                },
            )
        else:
            if not args.run_dir or args.source_root is None or args.foe is None:
                raise ValueError(
                    "run-directory collection requires --run-dir, --source-root, and --foe"
                )
            report = collect(
                args.source_root.resolve(strict=True),
                args.foe.resolve(strict=True),
                [path.resolve(strict=True) for path in args.run_dir],
                development_tasks(cases),
            )
        encoded = encoded_evidence(report)
        if args.expected_report_sha256 is not None:
            expected = args.expected_report_sha256.removeprefix("sha256:")
            observed = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            if expected != observed:
                raise ValueError(
                    "collected diagnostics differ from the preflight report digest"
                )
        if args.output is None:
            sys.stdout.write(encoded)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"collect diagnostics: {error}", file=sys.stderr)
        return 2
    if args.output is not None:
        print(f"Self-improvement evidence: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
