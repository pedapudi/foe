#!/usr/bin/python3
"""Validate public completion checkers in Terminal-Bench task containers."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from run import HARBOR_VERSION, default_harbor, read_cases


@dataclass(frozen=True)
class VerifierCase:
    task: str
    checker: Path
    oracle: Path
    contract: str


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_verifier_cases(path: Path, dataset: str) -> dict[str, VerifierCase]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("dataset") != dataset:
        raise ValueError("verifier_cases.dataset must equal cases.dataset")
    raw_cases = value.get("cases")
    if not isinstance(raw_cases, dict) or not raw_cases:
        raise ValueError("verifier_cases.cases must be a non-empty object")
    cases = {}
    for task, raw in raw_cases.items():
        if not isinstance(task, str) or not isinstance(raw, dict):
            raise ValueError("every verifier case must be an object")
        checker = raw.get("checker")
        oracle = raw.get("oracle")
        contract = raw.get("contract")
        if not all(isinstance(item, str) and item for item in (checker, oracle, contract)):
            raise ValueError(f"verifier case {task} has incomplete fields")
        checker_path = (path.parent / checker).resolve(strict=True)
        oracle_path = (path.parent / oracle).resolve(strict=True)
        cases[task] = VerifierCase(task, checker_path, oracle_path, contract)
    return cases


def evaluate_control_job(job_dir: Path) -> dict[str, Any]:
    aggregate_path = job_dir / "result.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    stats = aggregate.get("stats")
    if not isinstance(stats, dict):
        raise ValueError(f"control job has no stats: {aggregate_path}")
    if stats.get("n_completed_trials") != 1 or stats.get("n_errored_trials") != 0:
        raise ValueError(f"control job did not complete exactly one trial: {aggregate_path}")
    trial_paths = sorted(
        path for path in job_dir.glob("*/result.json") if path != aggregate_path
    )
    if len(trial_paths) != 1:
        raise ValueError(f"control job has {len(trial_paths)} trial records: {job_dir}")
    trial = json.loads(trial_paths[0].read_text(encoding="utf-8"))
    if trial.get("exception_info") is not None:
        raise ValueError(f"control trial recorded an exception: {trial_paths[0]}")
    agent = trial.get("agent_result")
    metadata = agent.get("metadata") if isinstance(agent, dict) else None
    controls = metadata.get("foe_verifier_controls") if isinstance(metadata, dict) else None
    if not isinstance(controls, dict):
        raise ValueError(f"control trial has no checker controls: {trial_paths[0]}")
    negative = controls.get("negative_control")
    oracle = controls.get("oracle_control")
    negative_findings = negative.get("findings") if isinstance(negative, dict) else None
    if (
        not isinstance(negative, dict)
        or negative.get("accepted") is not False
        or not isinstance(negative_findings, list)
        or not negative_findings
    ):
        raise ValueError(f"negative checker control did not reject: {trial_paths[0]}")
    if not isinstance(oracle, dict) or oracle.get("accepted") is not True:
        raise ValueError(f"oracle checker control did not accept: {trial_paths[0]}")
    verifier = trial.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    if reward != 1.0:
        raise ValueError(f"the untouched Terminal-Bench verifier rejected the oracle: {trial_paths[0]}")
    return {
        "trial": str(trial_paths[0].relative_to(job_dir)),
        "negative_findings": negative_findings,
        "oracle_reward": reward,
    }


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    answer.add_argument("--source-root", type=Path, required=True)
    answer.add_argument("--agent-module", type=Path, required=True)
    answer.add_argument("--cases", type=Path, required=True)
    answer.add_argument("--verifier-cases", type=Path, required=True)
    answer.add_argument("--task", action="append", default=[])
    answer.add_argument("--harbor", type=Path, default=default_harbor())
    answer.add_argument(
        "--jobs-dir",
        type=Path,
        default=Path("target/terminal-bench-verifier-controls"),
    )
    return answer


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        workspace = args.source_root.resolve(strict=True).parent
        agent_module = args.agent_module.resolve(strict=True)
        harbor = args.harbor.resolve(strict=True)
        dataset, _, tasks, _ = read_cases(args.cases.resolve(strict=True))
        cases = read_verifier_cases(args.verifier_cases.resolve(strict=True), dataset)
        unknown = sorted(set(cases) - set(tasks))
        if unknown:
            raise ValueError(
                "verifier cases name unknown tasks: " + ", ".join(unknown)
            )
        selected_names = args.task or list(cases)
        missing = sorted(set(selected_names) - set(cases))
        if missing:
            raise ValueError("unknown verifier cases: " + ", ".join(missing))
        if len(selected_names) != len(set(selected_names)):
            raise ValueError("a verifier case may be selected only once")
        selected = [cases[name] for name in selected_names]
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"verifier controls: {error}", file=sys.stderr)
        return 2

    version = subprocess.run(
        [str(harbor), "--version"], text=True, capture_output=True, check=False
    )
    observed_version = (version.stdout or version.stderr).strip()
    if version.returncode != 0 or HARBOR_VERSION not in observed_version:
        print(
            f"verifier controls: expected Harbor {HARBOR_VERSION}; observed "
            f"{observed_version or 'no version'}",
            file=sys.stderr,
        )
        return 2
    if subprocess.run(
        ["/usr/bin/docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode:
        print("verifier controls: Docker is unavailable to this shell", file=sys.stderr)
        return 2

    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    jobs_root = (
        (workspace / args.jobs_dir).resolve()
        if not args.jobs_dir.is_absolute()
        else args.jobs_dir.resolve()
    )
    run_dir = jobs_root / f"controls-{timestamp}"
    records = []
    for case in selected:
        command = [
            "/usr/bin/env",
            f"PYTHONPATH={agent_module.parent}",
            str(harbor),
            "run",
            "--dataset",
            dataset,
            "--include-task-name",
            f"terminal-bench/{case.task}",
            "--agent",
            f"{agent_module.stem}:VerifierControlAgent",
            "--model",
            "exec/verifier-controls",
            "--n-concurrent",
            "1",
            "--n-attempts",
            "1",
            "--jobs-dir",
            str(run_dir),
            "--job-name",
            case.task,
            "--yes",
            "--agent-kwarg",
            f"checker_file={case.checker}",
            "--agent-kwarg",
            f"oracle_file={case.oracle}",
            "--agent-kwarg",
            f"version=checker-{file_digest(case.checker)}-oracle-{file_digest(case.oracle)}",
        ]
        result = subprocess.run(command, cwd=agent_module.parent, check=False)
        record = {
            "task": case.task,
            "contract": case.contract,
            "checker_sha256": file_digest(case.checker),
            "oracle_sha256": file_digest(case.oracle),
            "harbor_exit_code": result.returncode,
        }
        try:
            record.update(evaluate_control_job(run_dir / case.task))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            record["error"] = str(error)
        records.append(record)
        if result.returncode != 0 or "error" in record:
            break
    report = {
        "schema_version": 1,
        "dataset": dataset,
        "cases": records,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "verifier-controls.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Verifier-control evidence: {report_path}")
    return 0 if len(records) == len(selected) and all("error" not in row for row in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
