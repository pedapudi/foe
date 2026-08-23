#!/usr/bin/python3
"""Run a small, retained Terminal-Bench 2.1 evaluation with Foe."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trajectory_diagnostics import diagnose_episode


HARBOR_VERSION = "0.22.0"
DEFAULT_MODEL = "openai-codex/gpt-5.6-sol"
SAFE_LABEL = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True)
class Task:
    name: str
    model_calls: int
    expected_input_tokens: int
    expected_output_tokens: int
    seconds: int


@dataclass(frozen=True)
class Pricing:
    source: str
    input_per_million: float
    cached_input_per_million: float
    output_per_million: float
    long_context_threshold: int
    long_context_input_multiplier: float
    long_context_output_multiplier: float

    def agent_kwargs(self) -> dict[str, float | int]:
        return {
            key: value
            for key, value in self.__dict__.items()
            if key != "source"
        }

    def expected_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_per_million
            + output_tokens * self.output_per_million
        ) / 1_000_000


def read_cases(
    path: Path,
) -> tuple[str, dict[str, tuple[str, ...]], dict[str, Task], dict[str, Pricing]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    dataset = value.get("dataset")
    raw_groups = value.get("groups")
    raw_tasks = value.get("tasks")
    raw_pricing = value.get("pricing")
    if not isinstance(dataset, str) or "@" not in dataset:
        raise ValueError("cases.dataset must pin a dataset revision")
    if not all(isinstance(item, dict) for item in (raw_groups, raw_tasks, raw_pricing)):
        raise ValueError("cases.groups, cases.tasks, and cases.pricing must be objects")
    pricing: dict[str, Pricing] = {}
    for model, raw in raw_pricing.items():
        if not isinstance(model, str) or not isinstance(raw, dict):
            raise ValueError("every cases.pricing entry must be an object")
        try:
            price = Pricing(**raw)
        except TypeError as error:
            raise ValueError(f"cases.pricing.{model} has invalid fields") from error
        numeric = tuple(
            value for key, value in price.__dict__.items() if key != "source"
        )
        if not isinstance(price.source, str) or not price.source.startswith("https://"):
            raise ValueError(f"cases.pricing.{model}.source must be an HTTPS URL")
        if any(not isinstance(item, (int, float)) or item <= 0 for item in numeric):
            raise ValueError(f"cases.pricing.{model} rates must be positive numbers")
        pricing[model] = price
    tasks: dict[str, Task] = {}
    for name, limits in raw_tasks.items():
        if not isinstance(name, str) or not isinstance(limits, dict):
            raise ValueError("every cases.tasks entry must be an object")
        task = Task(
            name=name,
            model_calls=limits.get("model_calls"),
            expected_input_tokens=limits.get("expected_input_tokens"),
            expected_output_tokens=limits.get("expected_output_tokens"),
            seconds=limits.get("seconds"),
        )
        limits = (
            task.model_calls,
            task.expected_input_tokens,
            task.expected_output_tokens,
            task.seconds,
        )
        if any(not isinstance(value, int) or value <= 0 for value in limits):
            raise ValueError(f"cases.tasks.{name} limits must be positive integers")
        tasks[name] = task
    groups: dict[str, tuple[str, ...]] = {}
    for group, names in raw_groups.items():
        if not isinstance(names, list) or not all(
            isinstance(name, str) and name in tasks for name in names
        ):
            raise ValueError(f"cases.groups.{group} must name configured tasks")
        groups[group] = tuple(names)
    protected = (
        "development",
        "capability_search",
        "confirmation",
        "calibration",
        "calibration_holdout",
    )
    for index, left in enumerate(protected):
        for right in protected[index + 1 :]:
            overlap = set(groups.get(left, ())) & set(groups.get(right, ()))
            if overlap:
                names = ", ".join(sorted(overlap))
                raise ValueError(f"{left} and {right} tasks overlap: {names}")
    return dataset, groups, tasks, pricing


def default_harbor() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local" / "bin" / "harbor"


def default_credential() -> Path:
    return (
        Path(pwd.getpwuid(os.getuid()).pw_dir)
        / ".config"
        / "foe"
        / "credentials"
        / "openai-codex.json"
    )


def default_credential_state() -> Path:
    return (
        Path(pwd.getpwuid(os.getuid()).pw_dir)
        / ".cache"
        / "foe"
        / "terminal-bench"
        / "openai-codex.json"
    )


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def source_tree(path: Path) -> str:
    """Return the Git tree object for a checkout with no source changes."""

    def git(*arguments: str) -> str:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(path.parent), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (
                result.stderr.strip()
                or result.stdout.strip()
                or f"exit {result.returncode}"
            )
            raise ValueError(
                f"cannot identify Foe source: git {' '.join(arguments)}: {detail}"
            )
        return result.stdout.strip()

    root = Path(git("rev-parse", "--show-toplevel")).resolve()
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise ValueError(f"Foe source tree is not clean at {root}:\n{status}")
    object_format = git("rev-parse", "--show-object-format")
    tree = git("rev-parse", "HEAD^{tree}")
    return f"git-tree-{object_format}:{tree}"


def initialize_credential_state(source: Path, state: Path) -> None:
    contents = source.read_bytes()
    value = json.loads(contents)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"credential file must contain a non-empty JSON object: {source}")
    state.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=state.parent,
        prefix=f".{state.name}.",
        delete=False,
    ) as target:
        temporary = Path(target.name)
        target.write(contents)
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, state)
    finally:
        temporary.unlink(missing_ok=True)


def lock_credential_state(state: Path):
    """Prevent concurrent campaigns from racing an OAuth token refresh."""
    state.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state.with_name(state.name + ".lock")
    lock = lock_path.open("a+", encoding="utf-8")
    os.chmod(lock_path, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        lock.close()
        raise ValueError(f"another Terminal-Bench run holds {lock_path}") from error
    return lock


def harbor_command(
    *,
    harbor: Path,
    dataset: str,
    task: Task,
    attempts: int,
    jobs_dir: Path,
    agent_module: Path,
    trace_evaluator: Path,
    foe: Path,
    credential_state: Path,
    model: str,
    reasoning_effort: str,
    diagnosis_model: str | None,
    diagnosis_reasoning_effort: str,
    diagnosis_model_calls: int,
    diagnosis_pricing: Pricing | None,
    runtime_digest: str,
    pricing: Pricing,
    hard_token_limits: bool = False,
    install_only: bool = False,
) -> list[str]:
    kwargs = {
        "foe_binary": foe,
        "credential_file": credential_state,
        "trace_evaluator": trace_evaluator,
        "model_calls": task.model_calls,
        "seconds": task.seconds,
        "reasoning_effort": reasoning_effort,
        "version": f"sha256:{runtime_digest}",
        **pricing.agent_kwargs(),
    }
    if hard_token_limits:
        kwargs["input_tokens"] = task.expected_input_tokens
        kwargs["output_tokens"] = task.expected_output_tokens
    if diagnosis_model is not None:
        kwargs["diagnosis_model"] = diagnosis_model
        kwargs["diagnosis_reasoning_effort"] = diagnosis_reasoning_effort
        kwargs["diagnosis_model_calls"] = diagnosis_model_calls
        if diagnosis_pricing is None:
            raise ValueError("diagnosis model pricing is required")
        kwargs.update({f"diagnosis_{key}": value for key, value in diagnosis_pricing.agent_kwargs().items()})
    command = [
        "/usr/bin/env",
        f"PYTHONPATH={agent_module.parent}",
        str(harbor),
        "run",
        "--dataset",
        dataset,
        "--include-task-name",
        f"terminal-bench/{task.name}",
        "--agent",
        f"{agent_module.stem}:FoeAgent",
        "--model",
        model,
        "--n-concurrent",
        "1",
        "--n-attempts",
        str(attempts),
        "--jobs-dir",
        str(jobs_dir),
        "--job-name",
        task.name,
        "--yes",
    ]
    for key, value in kwargs.items():
        command.extend(("--agent-kwarg", f"{key}={value}"))
    if install_only:
        command.append("--install-only")
    return command


def read_job_result(path: Path) -> dict[str, Any]:
    """Read the Harbor counts that its process exit status does not represent."""
    value = json.loads(path.read_text(encoding="utf-8"))
    stats = value.get("stats") if isinstance(value, dict) else None
    if not isinstance(stats, dict):
        raise ValueError(f"Harbor result has no stats object: {path}")
    keys = ("n_completed_trials", "n_errored_trials")
    if not all(isinstance(stats.get(key), int) for key in keys):
        raise ValueError(f"Harbor result has incomplete trial counts: {path}")
    total = value.get("n_total_trials")
    if not isinstance(total, int):
        raise ValueError(f"Harbor result has no total trial count: {path}")
    result: dict[str, Any] = {**{key: stats[key] for key in keys}, "n_total_trials": total}
    for key in ("n_input_tokens", "n_cache_tokens", "n_output_tokens"):
        if stats.get(key) is None or isinstance(stats.get(key), int):
            result[key] = stats.get(key)
    if stats.get("cost_usd") is None or isinstance(stats.get("cost_usd"), (int, float)):
        result["estimated_cost_usd"] = stats.get("cost_usd")
    return result


def write_job_diagnostics(job_dir: Path) -> list[str]:
    """Write one verifier-aware Foe diagnosis beside each retained episode."""
    written = []
    for episode_dir in sorted(job_dir.glob("*/agent/foe-episode")):
        trial_dir = episode_dir.parent.parent
        trial_result = trial_dir / "result.json"
        report = diagnose_episode(
            episode_dir,
            trial_result=trial_result if trial_result.is_file() else None,
        )
        output = episode_dir.parent / "foe-diagnostics.json"
        output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(str(output.relative_to(job_dir)))
    return written


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    answer.add_argument("--foe", type=Path, required=True)
    answer.add_argument("--source-root", type=Path, required=True)
    answer.add_argument("--agent-module", type=Path, required=True)
    answer.add_argument("--trace-evaluator", type=Path, required=True)
    answer.add_argument("--cases", type=Path, required=True)
    answer.add_argument("--group", default="smoke")
    answer.add_argument("--task", action="append", default=[])
    answer.add_argument("--attempts", type=int, default=1)
    answer.add_argument("--model", default=DEFAULT_MODEL)
    answer.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "xhigh"),
        default="low",
    )
    answer.add_argument("--diagnosis-model")
    answer.add_argument(
        "--diagnosis-reasoning-effort",
        choices=("low", "medium", "high", "xhigh"),
        default="high",
    )
    answer.add_argument("--diagnosis-model-calls", type=int, default=6)
    answer.add_argument("--label", default="baseline")
    answer.add_argument("--jobs-dir", type=Path, default=Path("target/terminal-bench-jobs"))
    answer.add_argument("--harbor", type=Path, default=default_harbor())
    answer.add_argument("--credential-file", type=Path, default=default_credential())
    answer.add_argument(
        "--credential-state",
        type=Path,
        default=default_credential_state(),
    )
    answer.add_argument("--install-only", action="store_true")
    answer.add_argument(
        "--hard-token-limits",
        action="store_true",
        help="enforce the planning token estimates as Foe allowances",
    )
    answer.add_argument("--confirm-spend", action="store_true")
    return answer


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        dataset, groups, tasks, pricing = read_cases(args.cases.resolve())
        if args.group not in groups:
            raise ValueError(f"unknown task group: {args.group}")
        selected_names = args.task or list(groups[args.group])
        unknown = sorted(set(selected_names) - set(tasks))
        if unknown:
            raise ValueError(f"unknown tasks: {', '.join(unknown)}")
        if len(selected_names) != len(set(selected_names)):
            raise ValueError("a task may be selected only once")
        if not 1 <= args.attempts <= 3:
            raise ValueError("--attempts must be between 1 and 3")
        if not SAFE_LABEL.fullmatch(args.label):
            raise ValueError(
                "--label must contain lowercase letters, digits, periods, "
                "underscores, or hyphens"
            )
        if not args.model.startswith("openai-codex/") or args.model == "openai-codex/":
            raise ValueError("--model must name an openai-codex model")
        if args.model not in pricing:
            raise ValueError(f"cases.pricing has no entry for {args.model}")
        if args.diagnosis_model is not None:
            if not args.diagnosis_model.startswith("openai-codex/") or args.diagnosis_model == "openai-codex/":
                raise ValueError("--diagnosis-model must name an openai-codex model")
            if args.diagnosis_model not in pricing:
                raise ValueError(f"cases.pricing has no entry for {args.diagnosis_model}")
            if args.diagnosis_model_calls < 2:
                raise ValueError("--diagnosis-model-calls must be at least two")
        foe = args.foe.resolve(strict=True)
        source_root = args.source_root.resolve(strict=True)
        agent_module = args.agent_module.resolve(strict=True)
        trace_evaluator = args.trace_evaluator.resolve(strict=True)
        harbor = args.harbor.resolve(strict=True)
        credential = args.credential_file.resolve()
        workspace = source_root.parent
        jobs_dir = (
            (workspace / args.jobs_dir).resolve()
            if not args.jobs_dir.is_absolute()
            else args.jobs_dir.resolve()
        )
        credential_state = (
            (workspace / args.credential_state).resolve()
            if not args.credential_state.is_absolute()
            else args.credential_state.resolve()
        )
        if credential_state == credential:
            raise ValueError("--credential-state must differ from the Foe login file")
        if credential_state.is_relative_to(jobs_dir):
            raise ValueError("--credential-state must remain outside --jobs-dir")
        selected = [tasks[name] for name in selected_names]
        if args.diagnosis_model is not None and any(args.diagnosis_model_calls >= task.model_calls for task in selected):
            raise ValueError("--diagnosis-model-calls must be below every selected task model-call allowance")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"terminal-bench eval: {error}", file=sys.stderr)
        return 2

    runtime_digest = digest(foe)
    total_calls = sum(task.model_calls for task in selected) * args.attempts
    selected_pricing = pricing[args.model]
    total_input = sum(task.expected_input_tokens for task in selected) * args.attempts
    total_output = sum(task.expected_output_tokens for task in selected) * args.attempts
    total_expected_cost = selected_pricing.expected_cost(total_input, total_output)
    print(f"dataset       {dataset}")
    print(f"model         {args.model} reasoning_effort={args.reasoning_effort}")
    if args.diagnosis_model is not None:
        print(
            f"diagnosis     {args.diagnosis_model} "
            f"reasoning_effort={args.diagnosis_reasoning_effort} "
            f"calls={args.diagnosis_model_calls}"
        )
    print(f"foe           sha256:{runtime_digest}")
    print(f"attempts      {args.attempts} per task; concurrency 1")
    print("planning      calls      input     output  est. cost  seconds  task")
    for task in selected:
        task_input = task.expected_input_tokens * args.attempts
        task_output = task.expected_output_tokens * args.attempts
        task_cost = selected_pricing.expected_cost(task_input, task_output)
        print(
            f"              {task.model_calls * args.attempts:>5}  "
            f"{task_input:>9,}  {task_output:>9,}  ${task_cost:>8.2f}  "
            f"{task.seconds:>7}  {task.name}"
        )
    print(
        f"total         {total_calls:>5}  {total_input:>9,}  "
        f"{total_output:>9,}  ${total_expected_cost:>8.2f}"
    )
    token_policy = "hard allowances" if args.hard_token_limits else "measurement only"
    print(f"token limits  {token_policy}")
    if args.install_only:
        print("Installation compatibility check selected; no model requests will be made.")
    elif not args.confirm_spend:
        print("No model requests were made. Add --confirm-spend after reviewing the maximum.")
        return 0

    evaluated_source: str | None = None
    try:
        evaluated_source = source_tree(source_root)
    except ValueError as error:
        if not args.install_only:
            print(f"terminal-bench eval: {error}", file=sys.stderr)
            return 2

    version = subprocess.run(
        [str(harbor), "--version"],
        text=True,
        capture_output=True,
        check=False,
    )
    observed_version = (version.stdout or version.stderr).strip()
    if version.returncode != 0 or HARBOR_VERSION not in observed_version:
        observed = observed_version or "no version"
        print(
            f"terminal-bench eval: expected Harbor {HARBOR_VERSION}; observed {observed}",
            file=sys.stderr,
        )
        return 2
    docker = subprocess.run(
        ["/usr/bin/docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if docker.returncode != 0:
        print(
            "terminal-bench eval: Docker is unavailable to this shell; start a "
            "login shell after joining the docker group",
            file=sys.stderr,
        )
        return 2
    try:
        credential_lock = lock_credential_state(credential_state)
    except (OSError, ValueError) as error:
        print(f"terminal-bench eval: {error}", file=sys.stderr)
        return 2
    if not credential_state.exists():
        try:
            initialize_credential_state(credential, credential_state)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"terminal-bench eval: {error}", file=sys.stderr)
            return 2

    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = jobs_dir / f"{args.label}-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    for task in selected:
        command = harbor_command(
            harbor=harbor,
            dataset=dataset,
            task=task,
            attempts=args.attempts,
            jobs_dir=run_dir,
            agent_module=agent_module,
            trace_evaluator=trace_evaluator,
            foe=foe,
            credential_state=credential_state,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            diagnosis_model=args.diagnosis_model,
            diagnosis_reasoning_effort=args.diagnosis_reasoning_effort,
            diagnosis_model_calls=args.diagnosis_model_calls,
            diagnosis_pricing=pricing.get(args.diagnosis_model) if args.diagnosis_model else None,
            runtime_digest=runtime_digest,
            pricing=selected_pricing,
            hard_token_limits=args.hard_token_limits,
            install_only=args.install_only,
        )
        result = subprocess.run(command, cwd=agent_module.parent, check=False)
        job_result_path = run_dir / task.name / "result.json"
        record: dict[str, Any] = {
            "task": task.name,
            "harbor_exit_code": result.returncode,
            "result": str(job_result_path.relative_to(run_dir)),
        }
        try:
            record.update(read_job_result(job_result_path))
            record["diagnostics"] = write_job_diagnostics(run_dir / task.name)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            record["result_error"] = str(error)
        records.append(record)
        if result.returncode != 0 or record.get("n_errored_trials", 1) > 0:
            break
    report = {
        "schema_version": 1,
        "dataset": dataset,
        "label": args.label,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "diagnosis_model": args.diagnosis_model,
        "diagnosis_reasoning_effort": args.diagnosis_reasoning_effort if args.diagnosis_model else None,
        "diagnosis_model_calls": args.diagnosis_model_calls if args.diagnosis_model else None,
        "attempts": args.attempts,
        "concurrency": 1,
        "pricing": selected_pricing.__dict__,
        "diagnosis_pricing": pricing[args.diagnosis_model].__dict__ if args.diagnosis_model else None,
        "planning_estimated_cost_usd": total_expected_cost,
        "token_limits": "hard" if args.hard_token_limits else "measurement_only",
        "install_only": args.install_only,
        "foe_sha256": runtime_digest,
        "evaluated_foe": (
            {"source_tree": evaluated_source, "runtime_binary": f"sha256:{runtime_digest}"}
            if evaluated_source is not None
            else None
        ),
        "tasks": [task.__dict__ for task in selected],
        "jobs": records,
    }
    (run_dir / "campaign.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Terminal-Bench evidence: {run_dir}")
    completed = len(records) == len(selected) and all(
        row["harbor_exit_code"] == 0
        and row.get("n_errored_trials") == 0
        and "result_error" not in row
        for row in records
    )
    return 0 if completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
