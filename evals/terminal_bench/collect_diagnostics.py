#!/usr/bin/python3
"""Collect identity-bound trajectory diagnoses for Foe self-improvement."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parent.parent / "harness_bench"))
from foe_source_identity import evaluated_foe, require_evaluated_foe
from run import read_cases
from trajectory_diagnostics import require_confined_regular_file, trial_facts

MAX_DIAGNOSES = 12
# The evidence enters a diagnosis child through one root workflow result.
# Core exposes at most 50,000 rendered characters from that result, including
# its status framing, and a child cannot retrieve bytes from its parent log.
MAX_EVIDENCE_BYTES = 48 * 1024
MAX_INPUT_GROWTH_LANDMARKS = 4
MAX_OUTCOME_TEXT = 2_000
MAX_COMPLETION_SUMMARY = 180
MAX_COMPLETION_PATHS = 6
MAX_COMPLETION_PATH = 120
MAX_COMPLETION_OBSERVATIONS = 4
MAX_COMPLETION_OBSERVATION = 180
MAX_RESULT_SUBJECT = 180
MAX_TERMINAL_TIMELINES = 2
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


def bounded_strings(value: Any, count: int, length: int) -> list[str]:
    """Return a bounded list of strings from one completion field."""
    if not isinstance(value, list):
        return []
    return [item[:length] for item in value if isinstance(item, str)][:count]


def diagnostic_outcome(outcome: Any, include_completion_claim: bool = False) -> dict[str, Any] | None:
    """Keep typed status, actionable failures, and selected untrusted completion evidence."""
    if not isinstance(outcome, dict):
        return None
    kind = outcome.get("kind")
    if not isinstance(kind, str) or not kind:
        return None
    answer: dict[str, Any] = {"kind": kind}
    for key in ("code", "limit", "message", "error"):
        value = outcome.get(key)
        if isinstance(value, str):
            answer[key] = value[:MAX_OUTCOME_TEXT]
    value = outcome.get("value")
    if include_completion_claim and kind == "completed" and isinstance(value, dict):
        claim = {
            "summary": (
                value.get("summary", "")[:MAX_COMPLETION_SUMMARY]
                if isinstance(value.get("summary"), str)
                else ""
            ),
            "changed_paths": bounded_strings(
                value.get("changed_paths"), MAX_COMPLETION_PATHS, MAX_COMPLETION_PATH
            ),
            "validation": bounded_strings(
                value.get("validation"),
                MAX_COMPLETION_OBSERVATIONS,
                MAX_COMPLETION_OBSERVATION,
            ),
            "unresolved_risks": bounded_strings(
                value.get("unresolved_risks"),
                MAX_COMPLETION_OBSERVATIONS,
                MAX_COMPLETION_OBSERVATION,
            ),
        }
        answer["untrusted_completion_claim"] = claim
    return answer


def compact_result(row: Any, *, replay: bool = False) -> dict[str, Any] | None:
    """Keep a result locator, its outcome, and optional replay measurements."""
    if not isinstance(row, dict):
        return None
    answer = {
        key: row[key]
        for key in ("episode_id", "seq", "step", "tool", "exit_code")
        if row.get(key) is not None
    }
    subject = row.get("subject")
    if isinstance(subject, str) and subject:
        answer["subject"] = subject[:MAX_RESULT_SUBJECT]
    for key in ("is_error", "timed_out", "truncated"):
        if row.get(key) is True:
            answer[key] = True
    if replay:
        for key in ("rendered_characters", "replayed_characters", "replayed_requests"):
            if isinstance(row.get(key), int):
                answer[key] = row[key]
    return answer


def compact_terminal_timelines(
    value: Any,
    terminal_outcome: Any,
) -> list[dict[str, Any]]:
    """Keep result evidence from the episode that supplied the root outcome."""
    if not isinstance(value, list):
        return []
    entries = [entry for entry in value if isinstance(entry, dict)]
    selected = [
        entry
        for entry in entries
        if entry.get("outcome") == terminal_outcome and entry.get("results")
    ]
    if not selected:
        selected = [entry for entry in entries if entry.get("results")][-1:]
    answer = []
    for entry in selected[:MAX_TERMINAL_TIMELINES]:
        results = [
            compact
            for row in entry.get("results", [])
            if (compact := compact_result(row)) is not None
        ]
        answer.append(
            {
                "episode_id": entry.get("episode_id"),
                "last_edit_seq": entry.get("last_edit_seq"),
                "results": results,
                "omitted_results": entry.get("omitted_results", 0),
                "outcome": diagnostic_outcome(entry.get("outcome")),
            }
        )
    return answer


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
    built_in_workflow = manifest.get("built_in_workflow")
    if not isinstance(built_in_workflow, bool):
        raise ValueError(
            f"Terminal-Bench manifest {manifest_path} has no boolean `built_in_workflow`"
        )
    configuration["built_in_workflow"] = built_in_workflow
    if built_in_workflow:
        audit_effort = manifest.get("built_in_audit_reasoning_effort")
        if audit_effort not in ("low", "medium", "high", "xhigh"):
            raise ValueError(
                f"Terminal-Bench manifest {manifest_path} has invalid "
                "`built_in_audit_reasoning_effort`"
            )
        configuration["built_in_terminal_audit"] = {
            "model": answer["model"],
            "reasoning_effort": audit_effort,
            "model_calls": 60,
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
            "largest_replayed_results": [
                compact
                for row in report.get("largest_replayed_results", [])[:3]
                if (compact := compact_result(row, replay=True)) is not None
            ],
            "tool_failures": [
                compact
                for row in report.get("tool_failures", [])[:3]
                if (compact := compact_result(row, replay=True)) is not None
            ],
            "repeated_calls": report.get("repeated_calls", [])[:3],
        }
    )
    mismatch = report.get("artifact_outcome_mismatch") is True
    terminal_outcome = answer.get("outcome")
    if "outcome" in answer:
        answer["outcome"] = diagnostic_outcome(answer["outcome"], mismatch)
    answer["episodes"] = [
        {
            **episode,
            "outcome": diagnostic_outcome(episode.get("outcome")),
        }
        for episode in answer.get("episodes", [])
        if isinstance(episode, dict)
    ]
    answer["verification_timeline"] = compact_terminal_timelines(
        answer.get("verification_timeline"), terminal_outcome
    )
    return answer


def eligible_trial_reward(report: dict[str, Any]) -> int | float | None:
    """Return a numeric reward only for a trial without an infrastructure error."""
    if report.get("trial_error") is not None:
        return None
    reward = report.get("verifier_reward")
    return reward if type(reward) in (int, float) else None


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


def repeated_failure_contrasts(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return task-specific failure profiles with repeated failures and a success."""
    successes_by_task: dict[str, set[str]] = {}
    failures: dict[tuple[str, str], dict[str, Any]] = {}
    for report in reports:
        reward = eligible_trial_reward(report)
        if reward is None:
            continue
        task = report.get("task")
        identity = report.get("evidence_identity")
        episode_id = identity.get("episode_id") if isinstance(identity, dict) else None
        if not isinstance(task, str) or not isinstance(episode_id, str):
            continue
        if reward > 0:
            successes_by_task.setdefault(task, set()).add(episode_id)
            continue
        outcome = report.get("outcome")
        outcome_profile = {
            key: outcome[key]
            for key in ("kind", "code", "limit")
            if isinstance(outcome, dict) and isinstance(outcome.get(key), str)
        }
        feedback = report.get("verifier_feedback")
        feedback = feedback if isinstance(feedback, dict) else {}
        counts = feedback.get("failure_evidence_counts")
        failures_value = feedback.get("failures")
        if (
            not isinstance(counts, dict)
            or set(counts)
            != {
                "total_failed_tests",
                "retained_failed_tests",
                "omitted_failed_tests",
                "unlocated_failed_tests",
                "ambiguous_failed_tests",
            }
            or not all(type(value) is int and value >= 0 for value in counts.values())
            or counts["total_failed_tests"] == 0
            or counts["retained_failed_tests"] != counts["total_failed_tests"]
            or counts["omitted_failed_tests"] != 0
            or counts["unlocated_failed_tests"] != 0
            or counts["ambiguous_failed_tests"] != 0
            or not isinstance(failures_value, list)
            or len(failures_value) != counts["retained_failed_tests"]
        ):
            continue
        checks = []
        failure_loci = []
        for failure in failures_value:
            if not isinstance(failure, dict):
                continue
            name = failure.get("name")
            failure_class = (
                failure.get("failure_class")
                or failure.get("raw_status")
                or failure.get("status")
            )
            check = {}
            if isinstance(name, str):
                check["name"] = name
            if isinstance(failure_class, str):
                check["failure_class"] = failure_class
            checks.append(check)
            locus = failure.get("locus")
            if (
                isinstance(name, str)
                and isinstance(failure_class, str)
                and isinstance(locus, dict)
                and isinstance(locus.get("locus_sha256"), str)
            ):
                failure_loci.append(
                    {
                        "name": name,
                        "failure_class": failure_class,
                        **{
                            key: locus[key]
                            for key in (
                                "locus_sha256",
                                "location",
                                "assertion",
                                "observed_assertion",
                                "message",
                            )
                            if isinstance(locus.get(key), str)
                        },
                    }
                )
        if (
            len(failure_loci) != counts["total_failed_tests"]
            or len({locus["locus_sha256"] for locus in failure_loci})
            != len(failure_loci)
        ):
            continue
        profile = {
            "outcome": outcome_profile,
            "artifact_outcome_mismatch": report.get("artifact_outcome_mismatch") is True,
            "failed_verifier_checks": sorted(
                checks,
                key=lambda check: (check.get("name", ""), check.get("failure_class", "")),
            ),
        }
        key = (task, json.dumps(profile, sort_keys=True, separators=(",", ":")))
        group = failures.setdefault(
            key,
            {
                "task": task,
                "failure_profile": profile,
                "failed_attempts": {},
            },
        )
        report_sha256 = feedback.get("sha256")
        attempt = {
            "episode_id": episode_id,
            "verifier_report_sha256": (
                report_sha256 if isinstance(report_sha256, str) else None
            ),
            "failure_evidence_counts": counts,
            "failure_loci": sorted(
                failure_loci,
                key=lambda locus: (
                    locus.get("name", ""),
                    locus.get("location", ""),
                    locus.get("locus_sha256", ""),
                ),
            ),
        }
        previous = group["failed_attempts"].get(episode_id)
        if previous is not None and previous != attempt:
            raise ValueError(
                f"episode {episode_id} has inconsistent verifier failure evidence"
            )
        group["failed_attempts"][episode_id] = attempt

    answer = []
    for key in sorted(failures):
        group = failures[key]
        failed_attempts = [
            group["failed_attempts"][episode]
            for episode in sorted(group["failed_attempts"])
        ]
        successful_episode_ids = sorted(successes_by_task.get(group["task"], set()))
        if len(failed_attempts) < 2 or not successful_episode_ids:
            continue
        contrast = {
            "task": group["task"],
            "failure_profile": group["failure_profile"],
            "failed_attempts": failed_attempts,
            "successful_episode_ids": successful_episode_ids,
        }
        contrast["contrast_sha256"] = "sha256:" + hashlib.sha256(
            json.dumps(
                contrast,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        answer.append(contrast)
    return answer


def encoded_evidence(report: dict[str, Any]) -> str:
    """Serialize the exact compact bytes the evidence tool returns."""
    return json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"


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
        run_dir = run_dir.resolve(strict=True)
        manifest_path = run_dir / "campaign.json"
        manifest_path = require_confined_regular_file(
            manifest_path, run_dir, "Terminal-Bench campaign manifest"
        )
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
            path = require_confined_regular_file(
                path, run_dir, "Foe trajectory diagnosis"
            )
            report = json.loads(path.read_text(encoding="utf-8"))
            trial_result = path.parent.parent / "result.json"
            facts = trial_facts(trial_result, artifact_root=run_dir)
            identity_value = report.get("evidence_identity")
            prior_feedback = report.get("verifier_feedback")
            current_feedback = facts["verifier_feedback"]
            prior_digest = (
                prior_feedback.get("sha256")
                if isinstance(prior_feedback, dict)
                else None
            )
            current_digest = (
                current_feedback.get("sha256")
                if isinstance(current_feedback, dict)
                else None
            )
            expected = {
                "task": report.get("task"),
                "task_checksum": (
                    identity_value.get("task_checksum")
                    if isinstance(identity_value, dict)
                    else None
                ),
                "reward": report.get("verifier_reward"),
                "error": report.get("trial_error"),
                "verifier_report_sha256": prior_digest,
            }
            observed = {
                "task": facts["task"],
                "task_checksum": facts["task_checksum"],
                "reward": facts["reward"],
                "error": facts["error"],
                "verifier_report_sha256": current_digest,
            }
            if expected != observed:
                raise ValueError(
                    f"trajectory diagnosis does not match its retained result or verifier report: {path}"
                )
            report["verifier_feedback"] = current_feedback
            task = report.get("task")
            task_name = task.rsplit("/", 1)[-1] if isinstance(task, str) else None
            if task_name not in eligible_tasks:
                raise ValueError(
                    f"trajectory diagnosis is outside self-improvement evidence: {path}"
                )
            evidence = report.get("evidence_identity")
            if not isinstance(evidence, dict) or evidence.get("runtime_build") != identity["runtime_binary"]:
                raise ValueError(f"trajectory diagnosis has a different runtime identity: {path}")
            reports.append(compact_diagnosis(report, evaluation))
            if len(reports) > MAX_DIAGNOSES:
                raise ValueError(f"self-improvement evidence exceeds {MAX_DIAGNOSES} trajectory diagnoses")
        runs.append({**evaluation, "diagnoses": len(diagnostic_paths)})
    answer = {
        "schema_version": 6,
        "evaluated_foe": identity,
        "runs": runs,
        "evaluation_summary": evaluation_summary(reports),
        "repeated_failure_contrasts": repeated_failure_contrasts(reports),
        "trajectory_diagnostics": reports,
    }
    size = len(encoded_evidence(answer).encode("utf-8"))
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
        eligible_tasks = set(groups["self_improvement_evidence"])
        report = collect(
            args.source_root.resolve(strict=True),
            args.foe.resolve(strict=True),
            [path.resolve(strict=True) for path in args.run_dir],
            eligible_tasks,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded_evidence(report), encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"collect diagnostics: {error}", file=sys.stderr)
        return 2
    print(f"Self-improvement evidence: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
