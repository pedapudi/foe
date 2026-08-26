#!/usr/bin/python3
"""Run a small, retained Terminal-Bench 2.1 evaluation with Foe."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from source_adoption import (
    complete_source_adoption,
    freeze_source_candidate,
    verify_source_candidate,
)
from trajectory_diagnostics import diagnose_episode
from workflow_candidate import require_matching_run as require_matching_candidate_run
from workflow_candidate import validate as validate_workflow_candidate


HARBOR_VERSION = "0.22.0"
DEFAULT_MODEL = "openai-codex/gpt-5.6-sol"
SAFE_LABEL = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
MIN_AUXILIARY_MODEL_CALLS = 6
BUILTIN_WORKFLOW_MODEL_CALLS = 120
FAST_SERVICE_CREDIT_MULTIPLIER = 2.5
AGENT_TIMEOUT_GRACE_SECONDS = 300
CREDENTIAL_LEASE_STARTUP_SECONDS = 900
CREDENTIAL_REFRESH_MARGIN_MS = 60_000
MIN_PARALLEL_AVAILABLE_MEMORY_MB = 14 * 1024
MIN_RUN_AVAILABLE_MEMORY_MB = 10 * 1024
MIN_FREE_DISK_BYTES = 100 * 1024**3
MAX_PARALLEL_MEMORY_MB = 8 * 1024
MAX_PARALLEL_CPUS = 4
PARALLEL_TASK_MEMORY_EXCLUSION_MB = 8 * 1024
MAX_MEMORY_PRESSURE_AVG10 = 1.0
PROCESS_TERMINATION_SECONDS = 10


class CampaignCancellation(KeyboardInterrupt):
    """A terminal signal that requests a retained campaign shutdown."""

    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(signal.Signals(signum).name)


@dataclass(frozen=True)
class Task:
    name: str
    model_calls: int
    expected_input_tokens: int
    expected_output_tokens: int
    seconds: int
    harbor_agent_seconds: int
    cpus: int
    memory_mb: int


@dataclass(frozen=True)
class HostResources:
    available_memory_mb: int
    free_disk_bytes: int
    swap_out_pages: int
    memory_pressure_avg10: float


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
    raw_agent_timeouts = value.get("harbor_agent_timeouts")
    raw_default_resources = value.get("default_resources")
    if not isinstance(dataset, str) or "@" not in dataset:
        raise ValueError("cases.dataset must pin a dataset revision")
    if not all(
        isinstance(item, dict)
        for item in (
            raw_groups,
            raw_tasks,
            raw_pricing,
            raw_agent_timeouts,
            raw_default_resources,
        )
    ):
        raise ValueError(
            "cases groups, tasks, pricing, agent timeouts, and default resources must be objects"
        )
    default_cpus = raw_default_resources.get("cpus")
    default_memory_mb = raw_default_resources.get("memory_mb")
    if any(
        type(value) is not int or value <= 0
        for value in (default_cpus, default_memory_mb)
    ):
        raise ValueError("cases.default_resources values must be positive integers")
    if set(raw_agent_timeouts) != set(raw_tasks):
        raise ValueError("cases.harbor_agent_timeouts keys must match cases.tasks")
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
            harbor_agent_seconds=raw_agent_timeouts.get(name),
            cpus=limits.get("cpus", default_cpus),
            memory_mb=limits.get("memory_mb", default_memory_mb),
        )
        limits = (
            task.model_calls,
            task.expected_input_tokens,
            task.expected_output_tokens,
            task.seconds,
            task.harbor_agent_seconds,
        )
        if any(not isinstance(value, int) or value <= 0 for value in limits):
            raise ValueError(f"cases.tasks.{name} limits must be positive integers")
        if any(type(value) is not int or value <= 0 for value in (task.cpus, task.memory_mb)):
            raise ValueError(f"cases.tasks.{name} resources must be positive integers")
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


def model_stage_count(
    diagnosis_model: str | None,
    unresolved_diagnosis_reasoning_effort: str | None,
    escalation_reasoning_effort: str | None,
    built_in_workflow: bool = False,
) -> int:
    if built_in_workflow:
        return 2
    return 1 + sum(
        value is not None
        for value in (
            diagnosis_model,
            unresolved_diagnosis_reasoning_effort,
            escalation_reasoning_effort,
        )
    )


def task_agent_timeout_seconds(task: Task, stages: int) -> int:
    return task.seconds * stages + AGENT_TIMEOUT_GRACE_SECONDS


def access_only_lease_requirement_ms(
    tasks: list[Task] | tuple[Task, ...],
    *,
    attempts: int,
    stages: int,
    now_ms: int,
) -> int:
    longest = max(
        attempts
        * (task_agent_timeout_seconds(task, stages) + CREDENTIAL_LEASE_STARTUP_SECONDS)
        for task in tasks
    )
    return now_ms + longest * 1000 + CREDENTIAL_REFRESH_MARGIN_MS


def read_credential_state(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"credential state must be a JSON object: {path}")
    if not isinstance(value.get("access"), str) or not value["access"]:
        raise ValueError(f"credential state has no access token: {path}")
    if not isinstance(value.get("refresh"), str) or not value["refresh"]:
        raise ValueError(f"credential state has no refresh token: {path}")
    if type(value.get("expires")) is not int or value["expires"] <= 0:
        raise ValueError(f"credential state has no positive integer expiry: {path}")
    account_id = value.get("account_id")
    if account_id is not None and (not isinstance(account_id, str) or not account_id):
        raise ValueError(f"credential state has an invalid account id: {path}")
    return value


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as target:
        temporary = Path(target.name)
        json.dump(value, target, indent=2, sort_keys=True)
        target.write("\n")
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as target:
        temporary = Path(target.name)
        json.dump(value, target, indent=2, sort_keys=True)
        target.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def issue_access_only_lease(
    state: dict[str, Any],
    path: Path,
    *,
    required_expiry_ms: int = 0,
) -> None:
    expires = state.get("expires")
    if type(expires) is not int or expires <= required_expiry_ms:
        raise ValueError(
            "access token expiry does not cover the required execution window"
        )
    lease = {key: state[key] for key in ("access", "expires")}
    if "account_id" in state:
        lease["account_id"] = state["account_id"]
    write_private_json(path, lease)
    os.chmod(path, 0o400)


def credential_supports_parallel_tasks(
    state: dict[str, Any],
    tasks: list[Task] | tuple[Task, ...],
    *,
    attempts: int,
    stages: int,
    now_ms: int,
) -> bool:
    required = access_only_lease_requirement_ms(
        tasks,
        attempts=attempts,
        stages=stages,
        now_ms=now_ms,
    )
    return type(state.get("expires")) is int and state["expires"] > required


def host_resources(path: Path) -> HostResources:
    memory = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        name, separator, rest = line.partition(":")
        if separator:
            memory[name] = rest.strip().split()[0]
    available_kb = memory.get("MemAvailable")
    if available_kb is None or not available_kb.isdigit():
        raise ValueError("/proc/meminfo has no numeric MemAvailable value")
    swap_out_pages = None
    for line in Path("/proc/vmstat").read_text(encoding="utf-8").splitlines():
        if line.startswith("pswpout "):
            swap_out_pages = int(line.split()[1])
            break
    if swap_out_pages is None:
        raise ValueError("/proc/vmstat has no pswpout value")
    pressure = None
    for line in Path("/proc/pressure/memory").read_text(encoding="utf-8").splitlines():
        if line.startswith("full "):
            fields = dict(part.split("=", 1) for part in line.split()[1:])
            pressure = float(fields["avg10"])
            break
    if pressure is None:
        raise ValueError("/proc/pressure/memory has no full avg10 value")
    existing = path
    while not existing.exists():
        if existing == existing.parent:
            raise ValueError(f"no existing parent for resource check: {path}")
        existing = existing.parent
    return HostResources(
        available_memory_mb=int(available_kb) // 1024,
        free_disk_bytes=shutil.disk_usage(existing).free,
        swap_out_pages=swap_out_pages,
        memory_pressure_avg10=pressure,
    )


def parallel_resource_fit(tasks: list[Task] | tuple[Task, ...]) -> bool:
    return (
        len(tasks) == 2
        and all(task.memory_mb < PARALLEL_TASK_MEMORY_EXCLUSION_MB for task in tasks)
        and sum(task.memory_mb for task in tasks) <= MAX_PARALLEL_MEMORY_MB
        and sum(task.cpus for task in tasks) <= MAX_PARALLEL_CPUS
    )


def execution_groups(tasks: list[Task], workers: int) -> list[tuple[Task, ...]]:
    if workers == 1:
        return [(task,) for task in tasks]
    groups = []
    index = 0
    while index < len(tasks):
        pair = tuple(tasks[index : index + 2])
        if parallel_resource_fit(pair):
            groups.append(pair)
            index += 2
        else:
            groups.append((tasks[index],))
            index += 1
    return groups


def parallel_host_admission(
    resources: HostResources,
    previous: HostResources | None,
) -> tuple[bool, str | None]:
    if resources.free_disk_bytes < MIN_FREE_DISK_BYTES:
        return False, "free disk is below 100 GiB"
    if resources.available_memory_mb < MIN_PARALLEL_AVAILABLE_MEMORY_MB:
        return False, "available memory is below 14 GiB"
    if resources.memory_pressure_avg10 >= MAX_MEMORY_PRESSURE_AVG10:
        return False, "full memory pressure reached one percent over ten seconds"
    if previous is not None and resources.swap_out_pages > previous.swap_out_pages:
        return False, "the host swapped pages out after the preceding cohort"
    return True, None


def run_host_admission(resources: HostResources) -> tuple[bool, str | None]:
    if resources.free_disk_bytes < MIN_FREE_DISK_BYTES:
        return False, "free disk is below 100 GiB"
    if resources.available_memory_mb < MIN_RUN_AVAILABLE_MEMORY_MB:
        return False, "available memory is below 10 GiB"
    return True, None


def terminate_processes(processes: list[subprocess.Popen[Any]]) -> None:
    running = [process for process in processes if process.poll() is None]
    for process in running:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + PROCESS_TERMINATION_SECONDS
    for process in running:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            pass
    for process in running:
        if process.poll() is not None:
            continue
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            process.wait()


@contextlib.contextmanager
def campaign_signal_handlers():
    """Turn terminal interrupts into exceptions so cleanup can retain evidence."""

    signals = (signal.SIGINT, signal.SIGTERM)
    previous = {signum: signal.getsignal(signum) for signum in signals}

    def cancel(signum: int, _frame: Any) -> None:
        raise CampaignCancellation(signum)

    for signum in signals:
        signal.signal(signum, cancel)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def run_commands(
    commands: list[list[str]],
    *,
    cwd: Path,
    popen_factory: Any = subprocess.Popen,
    process_started: Any = None,
) -> list[int]:
    processes = []
    try:
        for command in commands:
            process = popen_factory(command, cwd=cwd, start_new_session=True)
            processes.append(process)
            if process_started is not None:
                process_started(len(processes))
        return [process.wait() for process in processes]
    except BaseException:
        terminate_processes(processes)
        raise


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


def prepare_campaign_credential(
    source: Path,
    state: Path,
    *,
    install_only: bool,
) -> TextIO | None:
    """Prepare the authoritative credential only for provider-backed work."""
    if install_only:
        return None
    lock = lock_credential_state(state)
    try:
        if not state.exists():
            initialize_credential_state(source, state)
    except Exception:
        lock.close()
        raise
    return lock


def write_provider_free_credential(path: Path) -> None:
    """Write a private empty credential object for one installation worker."""
    path.write_text("{}\n", encoding="utf-8")
    os.chmod(path, 0o400)


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
    credential_mode: str = "mutable",
    model: str,
    reasoning_effort: str,
    service_tier: str = "priority",
    diagnosis_model: str | None,
    diagnosis_reasoning_effort: str,
    diagnosis_model_calls: int,
    diagnosis_pricing: Pricing | None,
    unresolved_diagnosis_reasoning_effort: str | None,
    unresolved_diagnosis_model_calls: int,
    escalation_reasoning_effort: str | None,
    escalation_model_calls: int,
    runtime_digest: str,
    pricing: Pricing,
    completion_checker: Path | None = None,
    built_in_workflow: bool = False,
    hard_token_limits: bool = False,
    install_only: bool = False,
) -> list[str]:
    if built_in_workflow:
        if model != DEFAULT_MODEL:
            raise ValueError(
                f"the built-in evaluation workflow requires model {DEFAULT_MODEL}"
            )
        if reasoning_effort != "low":
            raise ValueError("the built-in workflow requires low primary reasoning")
        if service_tier != "priority":
            raise ValueError("the built-in evaluation workflow requires priority service")
        if any(
            stage_enabled
            for stage_enabled in (
                diagnosis_model is not None,
                unresolved_diagnosis_reasoning_effort is not None,
                escalation_reasoning_effort is not None,
            )
        ):
            raise ValueError(
                "the built-in workflow owns its implementation and audit stages"
            )
        if hard_token_limits:
            raise ValueError("the built-in workflow owns its token allowances")
    model_stages = model_stage_count(
        diagnosis_model,
        unresolved_diagnosis_reasoning_effort,
        escalation_reasoning_effort,
        built_in_workflow,
    )
    agent_timeout_seconds = task_agent_timeout_seconds(task, model_stages)
    agent_timeout_multiplier = agent_timeout_seconds / task.harbor_agent_seconds
    kwargs = {
        "foe_binary": foe,
        "credential_file": credential_state,
        "credential_mode": credential_mode,
        "trace_evaluator": trace_evaluator,
        "model_calls": (
            BUILTIN_WORKFLOW_MODEL_CALLS if built_in_workflow else task.model_calls
        ),
        "seconds": task.seconds,
        "reasoning_effort": reasoning_effort,
        "service_tier": service_tier,
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
    if unresolved_diagnosis_reasoning_effort is not None:
        kwargs["unresolved_diagnosis_reasoning_effort"] = unresolved_diagnosis_reasoning_effort
        kwargs["unresolved_diagnosis_model_calls"] = unresolved_diagnosis_model_calls
    if escalation_reasoning_effort is not None:
        kwargs["escalation_reasoning_effort"] = escalation_reasoning_effort
        kwargs["escalation_model_calls"] = escalation_model_calls
    if completion_checker is not None:
        kwargs["completion_checker"] = completion_checker
    if built_in_workflow:
        kwargs["built_in_workflow"] = "true"
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
        "--agent-timeout-multiplier",
        str(agent_timeout_multiplier),
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


def built_in_program_failures(
    result_path: Path,
    *,
    completion_checker: bool,
) -> list[str]:
    """Validate the resolved built-in workflow recorded by one trial."""
    episode_path = result_path.parent / "agent" / "foe-episode" / "episode.jsonl"
    try:
        with episode_path.open(encoding="utf-8") as source:
            first = source.readline()
        event = json.loads(first)
    except (OSError, json.JSONDecodeError) as error:
        return [f"cannot read the built-in root episode: {error}"]
    data = event.get("data") if isinstance(event, dict) else None
    program = data.get("program") if isinstance(data, dict) else None
    if (
        not isinstance(event, dict)
        or event.get("type") != "episode/start"
        or not isinstance(program, dict)
    ):
        return ["the root log does not begin with a resolved episode/start program"]
    def mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def model_profile(value: Any) -> tuple[Any, ...]:
        model = mapping(value)
        return tuple(
            model.get(key)
            for key in ("provider", "model", "reasoning_effort", "service_tier")
        )

    def sequence(value: Any) -> tuple[Any, ...]:
        return tuple(value) if isinstance(value, list) else ()

    root_model = mapping(program.get("model"))
    nodes = mapping(mapping(program.get("workflow")).get("nodes"))
    implementation = mapping(nodes.get("implement-task"))
    audit = mapping(nodes.get("audit-and-repair-task"))
    implementation_program = mapping(implementation.get("model"))
    audit_program = mapping(audit.get("model"))
    actual = {
        "root.name": program.get("name"),
        "root.model": model_profile(root_model),
        "root.model_calls": mapping(program.get("budget")).get("model_calls"),
        "root.sandbox": mapping(program.get("sandbox")).get("mode"),
        "workflow.nodes": tuple(sorted(nodes)),
        "implementation.follows": sequence(implementation.get("follows")),
        "implementation.terminal": implementation.get("terminal"),
        "implementation.name": implementation_program.get("name"),
        "implementation.model_calls": mapping(
            implementation_program.get("budget")
        ).get("model_calls"),
        "implementation.verify": mapping(
            implementation_program.get("done_when")
        ).get("verify"),
        "audit.follows": sequence(audit.get("follows")),
        "audit.terminal": audit.get("terminal"),
        "audit.name": audit_program.get("name"),
        "audit.model": model_profile(audit_program.get("model")),
        "audit.model_calls": mapping(audit_program.get("budget")).get(
            "model_calls"
        ),
        "audit.verify": mapping(audit_program.get("done_when")).get("verify"),
    }
    sol_low = ("openai-codex", "gpt-5.6-sol", "low", "priority")
    expected = {
        "root.name": "coding",
        "root.model": sol_low,
        "root.model_calls": BUILTIN_WORKFLOW_MODEL_CALLS,
        "root.sandbox": "off",
        "workflow.nodes": ("audit-and-repair-task", "implement-task"),
        "implementation.follows": ("task",),
        "implementation.terminal": False,
        "implementation.name": "implement-task",
        "implementation.model_calls": 60,
        "implementation.verify": None,
        "audit.follows": ("task", "implement-task"),
        "audit.terminal": True,
        "audit.name": "audit-and-repair-task",
        "audit.model": ("openai-codex", "gpt-5.6-sol", "high", "priority"),
        "audit.model_calls": 60,
        "audit.verify": "check" if completion_checker else None,
    }
    return [
        f"built-in profile {key}: expected {expected[key]!r}, recorded {value!r}"
        for key, value in actual.items()
        if value != expected[key]
    ]


def read_job_integrity(
    job_dir: Path,
    *,
    built_in_workflow: bool | None = None,
    completion_checker: bool = False,
) -> dict[str, list[str]]:
    """Record runtime, trace, and resource diagnostics beside task quality."""
    infrastructure_failures = []
    incomplete_resource_measurements = []
    trial_results = sorted(job_dir.glob("*/result.json"))
    if not trial_results:
        raise ValueError(f"Harbor job has no trial results: {job_dir}")
    for path in trial_results:
        value = json.loads(path.read_text(encoding="utf-8"))
        trial = value.get("trial_name")
        if not isinstance(trial, str):
            trial = path.parent.name
        exception = value.get("exception_info")
        if exception is not None:
            infrastructure_failures.append(f"{trial}: Harbor recorded a trial exception")
            continue
        agent_result = value.get("agent_result")
        metadata = agent_result.get("metadata") if isinstance(agent_result, dict) else None
        if not isinstance(metadata, dict):
            infrastructure_failures.append(f"{trial}: Harbor recorded no Foe metadata")
            continue
        outcome = metadata.get("foe_outcome")
        outcome_kind = outcome.get("kind") if isinstance(outcome, dict) else None
        if outcome_kind is None:
            infrastructure_failures.append(f"{trial}: Foe recorded no terminal outcome")
        elif outcome_kind == "failed":
            detail = outcome.get("error") if isinstance(outcome.get("error"), str) else "no error detail"
            infrastructure_failures.append(f"{trial}: Foe runtime failed: {detail}")
        if metadata.get("foe_trace_conformant") is not True:
            infrastructure_failures.append(f"{trial}: Foe trace conformance was not established")
        if (
            built_in_workflow is not None
            and metadata.get("foe_built_in_workflow") is not built_in_workflow
        ):
            infrastructure_failures.append(
                f"{trial}: Foe did not record the requested built-in workflow setting"
            )
        if built_in_workflow:
            infrastructure_failures.extend(
                f"{trial}: {failure}"
                for failure in built_in_program_failures(
                    path,
                    completion_checker=completion_checker,
                )
            )
        if (
            "foe_completion_checker_unchanged" in metadata
            and metadata.get("foe_completion_checker_unchanged") is not True
        ):
            infrastructure_failures.append(
                f"{trial}: the completion checker changed during the trial"
            )
        if (
            metadata.get("foe_credential_mode") == "access_only"
            and metadata.get("foe_credential_unchanged") is not True
        ):
            infrastructure_failures.append(
                f"{trial}: the access-only credential changed during the trial"
            )
        if metadata.get("foe_credential_exposed") is True:
            infrastructure_failures.append(
                f"{trial}: retained Foe artifacts contain a provider credential"
            )
        if metadata.get("foe_usage_reported") is not True:
            missing = metadata.get("foe_unreported_model_calls")
            count = missing if isinstance(missing, int) else "unknown"
            incomplete_resource_measurements.append(f"{trial}: {count} model call(s) lack provider usage")
    return {
        "infrastructure_failures": infrastructure_failures,
        "incomplete_resource_measurements": incomplete_resource_measurements,
        "configuration_claim_valid": not infrastructure_failures,
    }


def campaign_execution_complete(records: list[dict[str, Any]], expected: int) -> bool:
    """Report whether every task produced valid evidence for its configuration."""
    return len(records) == expected and all(
        "result_error" not in row
        and row.get("n_errored_trials") == 0
        and row.get("n_completed_trials") == row.get("n_total_trials")
        and row.get("configuration_claim_valid") is True
        for row in records
    )


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


def utc_timestamp() -> str:
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")


def host_resource_record(resources: HostResources) -> dict[str, int | float]:
    return {
        "available_memory_mb": resources.available_memory_mb,
        "free_disk_bytes": resources.free_disk_bytes,
        "swap_out_pages": resources.swap_out_pages,
        "memory_pressure_avg10": resources.memory_pressure_avg10,
    }


def job_has_out_of_memory_failure(job_dir: Path) -> bool:
    markers = ("oomkilled", "out of memory", "exit code 137", "status 137")
    for path in sorted(job_dir.glob("*/result.json")):
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        if any(marker in text for marker in markers):
            return True
    return False


def task_record(
    *,
    task: Task,
    run_dir: Path,
    harbor_exit_code: int | None,
    install_only: bool,
    built_in_workflow: bool,
    completion_checker: bool,
    worker: int,
    execution_group: str,
    credential_mode: str,
    started_at: str,
    ended_at: str,
    elapsed_seconds: float,
    source_adoption_path: Path | None = None,
    source_preflight: dict[str, Any] | None = None,
    source_checker: Path | None = None,
    source_root: Path | None = None,
    evaluated_source: str | None = None,
    foe: Path | None = None,
) -> dict[str, Any]:
    job_result_path = run_dir / task.name / "result.json"
    record: dict[str, Any] = {
        "task": task.name,
        "harbor_exit_code": harbor_exit_code,
        "result": str(job_result_path.relative_to(run_dir)),
        "worker": worker,
        "execution_group": execution_group,
        "credential_mode": credential_mode,
        "started_at": started_at,
        "ended_at": ended_at,
        "execution_group_elapsed_seconds": elapsed_seconds,
    }
    try:
        record.update(read_job_result(job_result_path))
        record["diagnostics"] = write_job_diagnostics(run_dir / task.name)
        if install_only:
            record["configuration_claim_valid"] = True
        else:
            record.update(
                read_job_integrity(
                    run_dir / task.name,
                    built_in_workflow=built_in_workflow,
                    completion_checker=completion_checker,
                )
            )
            if source_adoption_path is not None:
                if None in (
                    source_checker,
                    source_root,
                    evaluated_source,
                    foe,
                    source_preflight,
                ):
                    raise ValueError("source adoption finalization lacks a trusted checker or evaluated pair")
                adoptions = []
                for plan in sorted((run_dir / task.name).glob("*/agent/foe-plan.json")):
                    trial = plan.parent.parent.name
                    adoptions.append(
                        complete_source_adoption(
                            source_checker,
                            source_adoption_path,
                            source_root,
                            evaluated_source,
                            foe,
                            plan,
                            plan.parent / "foe-episode",
                            run_dir / "source-lineage" / task.name / trial,
                            source_preflight,
                        )
                    )
                if not adoptions:
                    raise ValueError("source adoption found no retained Foe plan")
                record["source_adoptions"] = adoptions
    except (OSError, ValueError, json.JSONDecodeError) as error:
        record["result_error"] = str(error)
        record["configuration_claim_valid"] = False
        if source_adoption_path is not None:
            record["direct_implementation_required"] = True
    return record


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    answer.add_argument("--foe", type=Path, required=True)
    answer.add_argument("--source-root", type=Path, required=True)
    answer.add_argument(
        "--source-checker",
        type=Path,
        help="trusted source evidence and lineage checker; required with --source-adoption",
    )
    answer.add_argument(
        "--controller-root",
        type=Path,
        help="immutable controller checkout; required with --source-adoption",
    )
    answer.add_argument("--agent-module", type=Path, required=True)
    answer.add_argument("--trace-evaluator", type=Path, required=True)
    answer.add_argument("--cases", type=Path, required=True)
    answer.add_argument("--group", default="smoke")
    answer.add_argument("--task", action="append", default=[])
    answer.add_argument("--attempts", type=int, default=1)
    answer.add_argument(
        "--workers",
        type=int,
        choices=(1, 2),
        default=1,
        help="maximum assessed tasks to run at once",
    )
    answer.add_argument("--model", default=DEFAULT_MODEL)
    answer.add_argument("--service-tier", choices=("default", "priority"), default="priority")
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
    answer.add_argument("--diagnosis-model-calls", type=int, default=20)
    answer.add_argument(
        "--unresolved-diagnosis-reasoning-effort",
        choices=("low", "medium", "high", "xhigh"),
    )
    answer.add_argument("--unresolved-diagnosis-model-calls", type=int, default=20)
    answer.add_argument(
        "--escalation-reasoning-effort",
        choices=("low", "medium", "high", "xhigh"),
    )
    answer.add_argument("--escalation-model-calls", type=int, default=0)
    answer.add_argument(
        "--workflow-candidate",
        type=Path,
        help="identity-bound workflow configuration produced by self-improvement",
    )
    answer.add_argument(
        "--source-adoption",
        type=Path,
        help="accepted source-change result or lineage evidence bundle applied to this Foe build",
    )
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
        "--completion-checker",
        type=Path,
        help="read-only checker used by done_when.verify; requires one selected task",
    )
    answer.add_argument(
        "--built-in-workflow",
        action="store_true",
        help="exercise Foe's built-in implementation and terminal-audit workflow",
    )
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
        if args.completion_checker is not None and len(selected_names) != 1:
            raise ValueError("--completion-checker requires exactly one selected task")
        if args.built_in_workflow:
            if args.model != DEFAULT_MODEL:
                raise ValueError(
                    f"--built-in-workflow requires --model {DEFAULT_MODEL}"
                )
            if args.reasoning_effort != "low":
                raise ValueError("--built-in-workflow requires --reasoning-effort low")
            if args.service_tier != "priority":
                raise ValueError("--built-in-workflow requires --service-tier priority")
            if any(
                stage_enabled
                for stage_enabled in (
                    args.diagnosis_model is not None,
                    args.unresolved_diagnosis_reasoning_effort is not None,
                    args.escalation_reasoning_effort is not None,
                    args.workflow_candidate is not None,
                )
            ):
                raise ValueError(
                    "--built-in-workflow cannot be combined with runner-defined model stages"
                )
            if args.hard_token_limits:
                raise ValueError(
                    "--built-in-workflow cannot be combined with --hard-token-limits"
                )
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
            if args.diagnosis_model_calls < MIN_AUXILIARY_MODEL_CALLS:
                raise ValueError(
                    "--diagnosis-model-calls must be at least "
                    f"{MIN_AUXILIARY_MODEL_CALLS}"
                )
        if args.unresolved_diagnosis_reasoning_effort is not None:
            if args.diagnosis_model is None:
                raise ValueError(
                    "--unresolved-diagnosis-reasoning-effort requires --diagnosis-model"
                )
            if args.unresolved_diagnosis_model_calls < MIN_AUXILIARY_MODEL_CALLS:
                raise ValueError(
                    "--unresolved-diagnosis-model-calls must be at least "
                    f"{MIN_AUXILIARY_MODEL_CALLS}"
                )
            if args.escalation_reasoning_effort is not None:
                raise ValueError(
                    "conditional unresolved diagnosis cannot be combined with "
                    "post-implementation escalation"
                )
        if args.escalation_reasoning_effort is None and args.escalation_model_calls != 0:
            raise ValueError("--escalation-model-calls requires --escalation-reasoning-effort")
        if (
            args.escalation_reasoning_effort is not None
            and args.escalation_model_calls < MIN_AUXILIARY_MODEL_CALLS
        ):
            raise ValueError(
                "--escalation-model-calls must be at least "
                f"{MIN_AUXILIARY_MODEL_CALLS}"
            )
        if args.workflow_candidate is not None and (
            args.escalation_reasoning_effort is not None or args.escalation_model_calls != 0
        ):
            raise ValueError(
                "--workflow-candidate cannot be combined with manual escalation settings"
            )
        if args.workflow_candidate is not None and args.install_only:
            raise ValueError("--workflow-candidate cannot be used with --install-only")
        if args.source_adoption is not None and args.install_only:
            raise ValueError("--source-adoption cannot be used with --install-only")
        if args.source_adoption is not None and args.workflow_candidate is not None:
            raise ValueError("--source-adoption and --workflow-candidate evaluate different candidates")
        if args.source_adoption is not None and args.source_checker is None:
            raise ValueError("--source-adoption requires --source-checker")
        if args.source_adoption is not None and args.controller_root is None:
            raise ValueError("--source-adoption requires --controller-root")
        foe = args.foe.resolve(strict=True)
        source_root = args.source_root.resolve(strict=True)
        source_checker = (
            args.source_checker.resolve(strict=True)
            if args.source_checker is not None
            else None
        )
        controller_root = (
            args.controller_root.resolve(strict=True)
            if args.controller_root is not None
            else None
        )
        if controller_root is not None and controller_root.is_file():
            controller_root = controller_root.parent
        agent_module = args.agent_module.resolve(strict=True)
        trace_evaluator = args.trace_evaluator.resolve(strict=True)
        completion_checker = (
            args.completion_checker.resolve(strict=True)
            if args.completion_checker is not None
            else None
        )
        harbor = args.harbor.resolve(strict=True)
        credential = args.credential_file.resolve()
        workspace = source_root.parent
        candidate_repository = source_root if source_root.is_dir() else source_root.parent
        if args.source_adoption is not None:
            assert controller_root is not None
            assert source_checker is not None
            runner_path = Path(__file__).resolve(strict=True)
            if candidate_repository.is_relative_to(
                controller_root
            ) or controller_root.is_relative_to(candidate_repository):
                raise ValueError(
                    "--source-adoption requires a controller checkout separate "
                    "from the candidate source"
                )
            for name, path in (("runner", runner_path), ("source checker", source_checker)):
                if not path.is_relative_to(controller_root):
                    raise ValueError(f"controller {name} is outside --controller-root")
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
        workflow_candidate_path = (
            args.workflow_candidate.resolve(strict=True)
            if args.workflow_candidate is not None
            else None
        )
        source_adoption_path = (
            args.source_adoption.resolve(strict=True)
            if args.source_adoption is not None
            else None
        )
        evaluated_source = None
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"terminal-bench eval: {error}", file=sys.stderr)
        return 2

    runtime_digest = digest(foe)
    controller = (
        {
            "root": str(controller_root),
            "runner": {
                "path": str(Path(__file__).resolve()),
                "sha256": digest(Path(__file__).resolve()),
            },
            "source_checker": {
                "path": str(source_checker),
                "sha256": digest(source_checker),
            },
        }
        if source_adoption_path is not None
        else None
    )
    source_adoption = None
    if source_adoption_path is not None:
        try:
            evaluated_source = source_tree(source_root)
            assert source_checker is not None
            source_adoption = verify_source_candidate(
                source_checker,
                source_adoption_path,
                source_root,
                evaluated_source,
                foe,
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"terminal-bench eval: {error}", file=sys.stderr)
            return 2
    workflow_candidate = None
    if workflow_candidate_path is not None:
        try:
            evaluated_source = source_tree(source_root)
            identity = {
                "source_tree": evaluated_source,
                "runtime_binary": f"sha256:{runtime_digest}",
            }
            workflow_candidate = validate_workflow_candidate(
                json.loads(workflow_candidate_path.read_text(encoding="utf-8")),
                identity,
            )
            audit = require_matching_candidate_run(
                workflow_candidate,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                service_tier=args.service_tier,
                token_policy="hard" if args.hard_token_limits else "measurement_only",
                workflow_ownership=(
                    "foe-built-in" if args.built_in_workflow else "evaluation-runner"
                ),
                completion_governance=(
                    "declared-verifier"
                    if completion_checker is not None
                    else "model-report"
                ),
            )
            args.escalation_reasoning_effort = audit["reasoning_effort"]
            args.escalation_model_calls = audit["model_calls"]
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"terminal-bench eval: {error}", file=sys.stderr)
            return 2
    auxiliary_calls = (
        args.diagnosis_model_calls if args.diagnosis_model is not None else 0
    ) + (
        args.unresolved_diagnosis_model_calls
        if args.unresolved_diagnosis_reasoning_effort is not None
        else 0
    ) + args.escalation_model_calls
    selected_pricing = pricing[args.model]
    diagnosis_pricing = pricing[args.diagnosis_model] if args.diagnosis_model else None
    plans = []
    for task in selected:
        primary_calls = (
            BUILTIN_WORKFLOW_MODEL_CALLS
            if args.built_in_workflow
            else task.model_calls
        )
        primary_fraction = primary_calls / task.model_calls
        primary_input = round(task.expected_input_tokens * primary_fraction)
        primary_output = round(task.expected_output_tokens * primary_fraction)
        diagnosis_fraction = (
            args.diagnosis_model_calls / task.model_calls
            if args.diagnosis_model is not None
            else 0.0
        )
        escalation_fraction = args.escalation_model_calls / task.model_calls
        unresolved_diagnosis_fraction = (
            args.unresolved_diagnosis_model_calls / task.model_calls
            if args.unresolved_diagnosis_reasoning_effort is not None
            else 0.0
        )
        diagnosis_input = round(task.expected_input_tokens * diagnosis_fraction)
        diagnosis_output = round(task.expected_output_tokens * diagnosis_fraction)
        escalation_input = round(task.expected_input_tokens * escalation_fraction)
        escalation_output = round(task.expected_output_tokens * escalation_fraction)
        unresolved_diagnosis_input = round(
            task.expected_input_tokens * unresolved_diagnosis_fraction
        )
        unresolved_diagnosis_output = round(
            task.expected_output_tokens * unresolved_diagnosis_fraction
        )
        expected_input = (
            primary_input
            + diagnosis_input
            + unresolved_diagnosis_input
            + escalation_input
        )
        expected_output = (
            primary_output
            + diagnosis_output
            + unresolved_diagnosis_output
            + escalation_output
        )
        expected_cost = selected_pricing.expected_cost(
            primary_input + unresolved_diagnosis_input + escalation_input,
            primary_output + unresolved_diagnosis_output + escalation_output,
        )
        if diagnosis_pricing is not None:
            expected_cost += diagnosis_pricing.expected_cost(
                diagnosis_input, diagnosis_output
            )
        primary_seconds = task.seconds * (2 if args.built_in_workflow else 1)
        diagnosis_seconds = task.seconds if args.diagnosis_model is not None else 0
        escalation_seconds = task.seconds if args.escalation_reasoning_effort is not None else 0
        unresolved_diagnosis_seconds = (
            task.seconds if args.unresolved_diagnosis_reasoning_effort is not None else 0
        )
        plans.append(
            (
                task,
                primary_calls + auxiliary_calls,
                expected_input,
                expected_output,
                expected_cost,
                primary_seconds
                + diagnosis_seconds
                + unresolved_diagnosis_seconds
                + escalation_seconds,
            )
        )
    total_calls = sum(plan[1] for plan in plans) * args.attempts
    total_input = sum(plan[2] for plan in plans) * args.attempts
    total_output = sum(plan[3] for plan in plans) * args.attempts
    total_expected_cost = sum(plan[4] for plan in plans) * args.attempts
    print(f"dataset       {dataset}")
    print(f"model         {args.model} reasoning_effort={args.reasoning_effort}")
    print(f"service tier  {args.service_tier}")
    if args.diagnosis_model is not None:
        print(
            f"diagnosis     {args.diagnosis_model} "
            f"reasoning_effort={args.diagnosis_reasoning_effort} "
            f"calls={args.diagnosis_model_calls}"
        )
    if args.unresolved_diagnosis_reasoning_effort is not None:
        print(
            f"unresolved    {args.model} "
            f"reasoning_effort={args.unresolved_diagnosis_reasoning_effort} "
            f"calls={args.unresolved_diagnosis_model_calls}; conditional"
        )
    if args.escalation_reasoning_effort is not None:
        print(
            f"escalation    {args.model} "
            f"reasoning_effort={args.escalation_reasoning_effort} "
            f"calls={args.escalation_model_calls}"
        )
    if workflow_candidate is not None:
        print(f"workflow      {workflow_candidate['digest']}")
    elif args.built_in_workflow:
        print("workflow      built-in implementation and terminal audit")
    if source_adoption is not None:
        print(f"source bundle {source_adoption['source_bundle_identity']}")
        print(f"candidate     {source_adoption['source_candidate_identity']}")
    print(f"foe           sha256:{runtime_digest}")
    print(f"attempts      {args.attempts} per task; workers {args.workers}")
    print("planning      calls      input     output  est. cost  seconds  task")
    for task, task_calls, expected_input, expected_output, expected_cost, seconds in plans:
        print(
            f"              {task_calls * args.attempts:>5}  "
            f"{expected_input * args.attempts:>9,}  "
            f"{expected_output * args.attempts:>9,}  "
            f"${expected_cost * args.attempts:>8.2f}  "
            f"{seconds:>7}  {task.name}"
        )
    print(
        f"total         {total_calls:>5}  {total_input:>9,}  "
        f"{total_output:>9,}  ${total_expected_cost:>8.2f}"
    )
    token_policy = "hard allowances" if args.hard_token_limits else "measurement only"
    print(f"token limits  {token_policy}")
    if completion_checker is not None:
        owner = "built-in terminal audit" if args.built_in_workflow else "coding episode"
        print(
            f"completion    {owner} done_when.verify "
            f"sha256:{digest(completion_checker)}"
        )
    if args.service_tier == "priority":
        print(f"Fast credits  {FAST_SERVICE_CREDIT_MULTIPLIER:g}x Standard ChatGPT credits")
    if args.install_only:
        print("Installation compatibility check selected; no model requests will be made.")
    elif not args.confirm_spend:
        print(
            "No model requests were made. Add --confirm-spend after reviewing "
            "the planning estimate."
        )
        return 0

    if evaluated_source is None and not args.install_only:
        try:
            evaluated_source = source_tree(source_root)
        except ValueError as error:
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
    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = jobs_dir / f"{args.label}-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    if source_adoption_path is not None:
        try:
            assert source_checker is not None
            assert evaluated_source is not None
            source_adoption_path, source_adoption = freeze_source_candidate(
                source_checker,
                source_adoption_path,
                source_root,
                evaluated_source,
                foe,
                run_dir / "source-candidate-bundle",
                source_adoption,
            )
            source_adoption = {**source_adoption, "bundle": "source-candidate-bundle"}
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"terminal-bench eval: {error}", file=sys.stderr)
            return 2
    try:
        credential_lock = prepare_campaign_credential(
            credential,
            credential_state,
            install_only=args.install_only,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"terminal-bench eval: {error}", file=sys.stderr)
        return 2
    stages = model_stage_count(
        args.diagnosis_model,
        args.unresolved_diagnosis_reasoning_effort,
        args.escalation_reasoning_effort,
        args.built_in_workflow,
    )
    planned_groups = execution_groups(selected, args.workers)
    records: list[dict[str, Any]] = []
    execution_records: list[dict[str, Any]] = []
    previous_resources: HostResources | None = None
    parallel_disabled_reason: str | None = None
    stopped_reason: str | None = None
    cancelled = False
    execution_number = 0
    started_tasks: dict[str, dict[str, Any]] = {}

    def report_value() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "dataset": dataset,
            "label": args.label,
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "service_tier": args.service_tier,
            "chatgpt_credit_multiplier": (
                FAST_SERVICE_CREDIT_MULTIPLIER
                if args.service_tier == "priority"
                else 1.0
            ),
            "diagnosis_model": args.diagnosis_model,
            "diagnosis_reasoning_effort": (
                args.diagnosis_reasoning_effort if args.diagnosis_model else None
            ),
            "diagnosis_model_calls": (
                args.diagnosis_model_calls if args.diagnosis_model else None
            ),
            "unresolved_diagnosis_reasoning_effort": (
                args.unresolved_diagnosis_reasoning_effort
            ),
            "unresolved_diagnosis_model_calls": (
                args.unresolved_diagnosis_model_calls
                if args.unresolved_diagnosis_reasoning_effort
                else None
            ),
            "escalation_reasoning_effort": args.escalation_reasoning_effort,
            "escalation_model_calls": (
                args.escalation_model_calls
                if args.escalation_reasoning_effort
                else None
            ),
            "attempts": args.attempts,
            "requested_workers": args.workers,
            "concurrency": max(
                (record["processes_started"] for record in execution_records),
                default=1,
            ),
            "resource_policy": {
                "maximum_workers": 2,
                "maximum_parallel_cpus": MAX_PARALLEL_CPUS,
                "maximum_parallel_memory_mb": MAX_PARALLEL_MEMORY_MB,
                "parallel_task_memory_exclusion_mb": (
                    PARALLEL_TASK_MEMORY_EXCLUSION_MB
                ),
                "minimum_parallel_available_memory_mb": (
                    MIN_PARALLEL_AVAILABLE_MEMORY_MB
                ),
                "minimum_run_available_memory_mb": MIN_RUN_AVAILABLE_MEMORY_MB,
                "minimum_free_disk_bytes": MIN_FREE_DISK_BYTES,
                "maximum_memory_pressure_avg10": MAX_MEMORY_PRESSURE_AVG10,
            },
            "pricing": selected_pricing.__dict__,
            "diagnosis_pricing": (
                pricing[args.diagnosis_model].__dict__
                if args.diagnosis_model
                else None
            ),
            "planning_estimated_cost_usd": total_expected_cost,
            "token_limits": (
                "hard" if args.hard_token_limits else "measurement_only"
            ),
            "built_in_workflow": args.built_in_workflow,
            "install_only": args.install_only,
            "credential_policy": (
                "provider_free_installation"
                if args.install_only
                else "isolated_oauth_state"
            ),
            "completion_checker": (
                {
                    "path": str(completion_checker),
                    "sha256": digest(completion_checker),
                }
                if completion_checker is not None
                else None
            ),
            "workflow_candidate": workflow_candidate,
            "source_candidate": source_adoption,
            "controller": controller,
            "source_adoptions": [
                adoption
                for record in records
                for adoption in record.get("source_adoptions", [])
            ],
            "foe_sha256": runtime_digest,
            "evaluated_foe": (
                {
                    "source_tree": evaluated_source,
                    "runtime_binary": f"sha256:{runtime_digest}",
                }
                if evaluated_source is not None
                else None
            ),
            "tasks": [task.__dict__ for task in selected],
            "executions": execution_records,
            "cancelled": cancelled,
            "stopped_reason": stopped_reason,
            "jobs": records,
        }

    def checkpoint() -> None:
        write_json_atomic(run_dir / "campaign.json", report_value())

    def command_for(task: Task, credential_path: Path, credential_mode: str) -> list[str]:
        return harbor_command(
            harbor=harbor,
            dataset=dataset,
            task=task,
            attempts=args.attempts,
            jobs_dir=run_dir,
            agent_module=agent_module,
            trace_evaluator=trace_evaluator,
            foe=foe,
            credential_state=credential_path,
            credential_mode=credential_mode,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            service_tier=args.service_tier,
            diagnosis_model=args.diagnosis_model,
            diagnosis_reasoning_effort=args.diagnosis_reasoning_effort,
            diagnosis_model_calls=args.diagnosis_model_calls,
            diagnosis_pricing=pricing.get(args.diagnosis_model) if args.diagnosis_model else None,
            unresolved_diagnosis_reasoning_effort=args.unresolved_diagnosis_reasoning_effort,
            unresolved_diagnosis_model_calls=args.unresolved_diagnosis_model_calls,
            escalation_reasoning_effort=args.escalation_reasoning_effort,
            escalation_model_calls=args.escalation_model_calls,
            runtime_digest=runtime_digest,
            pricing=selected_pricing,
            completion_checker=completion_checker,
            built_in_workflow=args.built_in_workflow,
            hard_token_limits=args.hard_token_limits,
            install_only=args.install_only,
        )

    execution_failure: Exception | None = None
    try:
        with campaign_signal_handlers():
            with tempfile.TemporaryDirectory(
                prefix=f".{credential_state.name}.leases-",
                dir=run_dir if args.install_only else credential_state.parent,
            ) as lease_directory_text:
                lease_directory = Path(lease_directory_text)
                os.chmod(lease_directory, 0o700)
                for planned_group in planned_groups:
                    resources = host_resources(run_dir)
                    run_allowed, run_reason = run_host_admission(resources)
                    if not run_allowed:
                        stopped_reason = run_reason
                        break
                    parallel_allowed, host_reason = parallel_host_admission(
                        resources,
                        previous_resources,
                    )
                    state = (
                        None
                        if args.install_only
                        else read_credential_state(credential_state)
                    )
                    token_allowed = args.install_only or credential_supports_parallel_tasks(
                        state,
                        planned_group,
                        attempts=args.attempts,
                        stages=stages,
                        now_ms=int(time.time() * 1000),
                    )
                    use_parallel = (
                        args.workers == 2
                        and len(planned_group) == 2
                        and parallel_disabled_reason is None
                        and parallel_allowed
                        and token_allowed
                    )
                    fallback_reason = None
                    if len(planned_group) == 2 and not use_parallel:
                        if parallel_disabled_reason is not None:
                            fallback_reason = parallel_disabled_reason
                        elif not parallel_allowed:
                            fallback_reason = host_reason
                        elif not token_allowed:
                            fallback_reason = (
                                "the access token does not cover the complete "
                                "parallel execution window"
                            )
                        else:
                            fallback_reason = "parallel execution was not requested"
                    actual_groups = (
                        [planned_group]
                        if use_parallel
                        else [(task,) for task in planned_group]
                    )

                    for actual_group in actual_groups:
                        start_resources = host_resources(run_dir)
                        execution_allowed, execution_reason = run_host_admission(
                            start_resources
                        )
                        if not execution_allowed:
                            stopped_reason = execution_reason
                            break
                        execution_number += 1
                        execution_group = f"task-execution-{execution_number:04d}"
                        credential_mode = (
                            "provider_free"
                            if args.install_only
                            else "access_only"
                            if len(actual_group) == 2
                            else "mutable"
                        )
                        credential_paths = []
                        required_expiry_ms = None
                        if credential_mode == "provider_free":
                            for worker, task in enumerate(actual_group, start=1):
                                path = lease_directory / (
                                    f"{execution_group}-worker-{worker}-{task.name}.json"
                                )
                                write_provider_free_credential(path)
                                credential_paths.append(path)
                        elif credential_mode == "access_only":
                            assert state is not None
                            required_expiry_ms = access_only_lease_requirement_ms(
                                actual_group,
                                attempts=args.attempts,
                                stages=stages,
                                now_ms=int(time.time() * 1000),
                            )
                            for worker, task in enumerate(actual_group, start=1):
                                path = lease_directory / (
                                    f"{execution_group}-worker-{worker}-{task.name}.json"
                                )
                                issue_access_only_lease(
                                    state,
                                    path,
                                    required_expiry_ms=required_expiry_ms,
                                )
                                credential_paths.append(path)
                        else:
                            credential_paths.append(credential_state)

                        started_at = utc_timestamp()
                        started = time.monotonic()
                        commands = [
                            command_for(task, path, credential_mode)
                            for task, path in zip(
                                actual_group,
                                credential_paths,
                                strict=True,
                            )
                        ]
                        execution_record = {
                            "execution_group": execution_group,
                            "mode": (
                                "provider_free_installation"
                                if credential_mode == "provider_free"
                                else "parallel_access_only_credentials"
                                if credential_mode == "access_only"
                                else "serial_authoritative_credential"
                            ),
                            "tasks": [task.name for task in actual_group],
                            "workers": len(actual_group),
                            "processes_started": 0,
                            "reserved_cpus": sum(task.cpus for task in actual_group),
                            "reserved_memory_mb": sum(
                                task.memory_mb for task in actual_group
                            ),
                            "fallback_reason": fallback_reason,
                            "credential_required_expiry_ms": required_expiry_ms,
                            "credential_actual_expiry_ms": (
                                state["expires"]
                                if credential_mode == "access_only"
                                else None
                            ),
                            "status": "starting",
                            "started_at": started_at,
                            "ended_at": None,
                            "makespan_seconds": None,
                            "host_resources_start": host_resource_record(
                                start_resources
                            ),
                            "host_resources_end": None,
                        }
                        execution_records.append(execution_record)
                        execution_error = None
                        processes_started = 0

                        def record_process_start(count: int) -> None:
                            nonlocal processes_started
                            processes_started = count
                            execution_record["processes_started"] = count
                            task = actual_group[count - 1]
                            started_tasks[task.name] = {
                                "task": task,
                                "worker": count,
                                "execution_group": execution_group,
                                "credential_mode": credential_mode,
                                "started_at": started_at,
                                "started_monotonic": started,
                            }

                        try:
                            exit_codes: list[int | None] = run_commands(
                                commands,
                                cwd=agent_module.parent,
                                process_started=record_process_start,
                            )
                        except CampaignCancellation as error:
                            exit_codes = [None] * len(actual_group)
                            execution_error = (
                                f"campaign cancelled by {signal.Signals(error.signum).name} "
                                "while Harbor was running"
                            )
                            cancelled = True
                        except KeyboardInterrupt:
                            exit_codes = [None] * len(actual_group)
                            execution_error = (
                                "campaign cancelled by an interrupt while Harbor was running"
                            )
                            cancelled = True
                        except OSError as error:
                            exit_codes = [None] * len(actual_group)
                            execution_error = f"cannot run Harbor: {error}"
                        ended = time.monotonic()
                        ended_at = utc_timestamp()
                        end_resources = host_resources(run_dir)
                        elapsed = ended - started
                        execution_record.update(
                            {
                                "status": (
                                    "cancelled"
                                    if cancelled
                                    else "failed"
                                    if execution_error is not None
                                    else "finished"
                                ),
                                "ended_at": ended_at,
                                "makespan_seconds": elapsed,
                                "host_resources_end": host_resource_record(
                                    end_resources
                                ),
                            }
                        )

                        for worker, (task, exit_code) in enumerate(
                            zip(actual_group, exit_codes, strict=True),
                            start=1,
                        ):
                            if worker > processes_started:
                                record = {
                                    "task": task.name,
                                    "harbor_exit_code": None,
                                    "result": f"{task.name}/result.json",
                                    "worker": worker,
                                    "execution_group": execution_group,
                                    "credential_mode": credential_mode,
                                    "execution_status": "not_started",
                                    "result_error": execution_error
                                    or "Harbor process did not start",
                                }
                            else:
                                record = task_record(
                                    task=task,
                                    run_dir=run_dir,
                                    harbor_exit_code=exit_code,
                                    install_only=args.install_only,
                                    built_in_workflow=args.built_in_workflow,
                                    completion_checker=completion_checker is not None,
                                    worker=worker,
                                    execution_group=execution_group,
                                    credential_mode=credential_mode,
                                    started_at=started_at,
                                    ended_at=ended_at,
                                    elapsed_seconds=elapsed,
                                    source_adoption_path=source_adoption_path,
                                    source_preflight=source_adoption,
                                    source_checker=source_checker,
                                    source_root=source_root,
                                    evaluated_source=evaluated_source,
                                    foe=foe,
                                )
                                record["execution_status"] = "started"
                                if execution_error is not None:
                                    record.setdefault("result_error", execution_error)
                            records.append(record)
                            for failure in record.get(
                                "infrastructure_failures", []
                            ):
                                print(
                                    "terminal-bench eval: trial diagnostic: "
                                    f"{failure}",
                                    file=sys.stderr,
                                )
                            for failure in record.get(
                                "incomplete_resource_measurements", []
                            ):
                                print(
                                    "terminal-bench eval: incomplete resource "
                                    f"measurement: {failure}",
                                    file=sys.stderr,
                                )

                        if (
                            end_resources.swap_out_pages
                            > start_resources.swap_out_pages
                        ):
                            parallel_disabled_reason = (
                                "the host swapped pages out during an execution"
                            )
                        elif (
                            end_resources.memory_pressure_avg10
                            >= MAX_MEMORY_PRESSURE_AVG10
                        ):
                            parallel_disabled_reason = (
                                "full memory pressure reached one percent over ten seconds"
                            )
                        elif any(
                            job_has_out_of_memory_failure(run_dir / task.name)
                            for task in actual_group
                        ):
                            parallel_disabled_reason = (
                                "a task recorded an out-of-memory failure"
                            )
                        previous_resources = end_resources
                        if execution_error is not None or any(
                            "result_error" in row
                            for row in records[-len(actual_group) :]
                        ):
                            stopped_reason = execution_error or (
                                "a Harbor result could not be retained"
                            )
                        checkpoint()
                        if stopped_reason is not None:
                            break
                    if stopped_reason is not None or cancelled:
                        break
    except CampaignCancellation as error:
        cancelled = True
        stopped_reason = (
            f"campaign cancelled by {signal.Signals(error.signum).name}"
        )
    except KeyboardInterrupt:
        cancelled = True
        stopped_reason = "campaign cancelled by an interrupt"
    except Exception as error:
        execution_failure = error
        stopped_reason = f"campaign execution failed: {error}"
    finally:
        if credential_lock is not None:
            credential_lock.close()

    completed_names = {record["task"] for record in records}
    if len(completed_names) != len(selected):
        reason = stopped_reason or "campaign stopped before this task began"
        for task in selected:
            if task.name not in completed_names:
                started = started_tasks.get(task.name)
                if started is None:
                    record = {
                        "task": task.name,
                        "harbor_exit_code": None,
                        "result": f"{task.name}/result.json",
                        "execution_status": "not_started",
                        "result_error": reason,
                    }
                else:
                    ended_at = utc_timestamp()
                    elapsed = time.monotonic() - started["started_monotonic"]
                    record = task_record(
                        task=task,
                        run_dir=run_dir,
                        harbor_exit_code=None,
                        install_only=args.install_only,
                        built_in_workflow=args.built_in_workflow,
                        completion_checker=completion_checker is not None,
                        worker=started["worker"],
                        execution_group=started["execution_group"],
                        credential_mode=started["credential_mode"],
                        started_at=started["started_at"],
                        ended_at=ended_at,
                        elapsed_seconds=elapsed,
                        source_adoption_path=source_adoption_path,
                        source_preflight=source_adoption,
                        source_checker=source_checker,
                        source_root=source_root,
                        evaluated_source=evaluated_source,
                        foe=foe,
                    )
                    record["execution_status"] = "started"
                    record["campaign_stop_reason"] = reason
                records.append(record)
    for execution in execution_records:
        if execution["status"] == "starting":
            execution.update(
                {
                    "status": "cancelled" if cancelled else "failed",
                    "ended_at": utc_timestamp(),
                }
            )
    checkpoint()
    print(f"Terminal-Bench evidence: {run_dir}")
    completed = campaign_execution_complete(records, len(selected))
    if cancelled:
        return 130
    if execution_failure is not None:
        print(f"terminal-bench eval: {execution_failure}", file=sys.stderr)
        return 2
    return 0 if completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
