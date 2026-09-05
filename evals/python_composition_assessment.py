#!/usr/bin/python3
"""Compare the Python composition tool with two simpler coding configurations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from foe_build import evaluated_foe
from run_micro_evals import (
    TOKEN_FIELDS,
    assistant_calls,
    episode_logs,
    event_data,
    infrastructure_fault,
    read_events,
    root_outcome,
    run_grader,
    usage,
    write_json,
)
from trace_quality import evaluate


EVALUATED = 0
DEPLOYMENT_FAULT = 1
NOTHING_LAUNCHED = 2
QUALITY_COMPONENTS = ("artifact_correct", "outcome_correct", "trace_conformant", "within_budget")


@dataclass(frozen=True)
class Configuration:
    name: str
    add_python: bool = False
    add_shell_guidance: bool = False


@dataclass(frozen=True)
class Task:
    name: str
    task_set: str
    purpose: str
    model_calls: int
    input_tokens: int
    output_tokens: int
    seconds: int
    materialize: Callable[[Path, Path], dict[str, Any]]
    config: Callable[[Path, dict[str, Any], dict[str, Any]], dict[str, Any]]
    oracle: Callable[[Path, dict[str, Any]], None]


CONFIGURATIONS = (
    Configuration("ordinary-coding-tools"),
    Configuration("shell-output-narrowing", add_shell_guidance=True),
    Configuration("python-tool-composition", add_python=True),
)


def _write(root: Path, relative: str, content: str, executable: bool = False) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    if executable:
        path.chmod(0o755)
    return path


def _candidate_grader(expected: dict[str, Any], expected_files: dict[str, str]) -> str:
    return f'''#!/usr/bin/python3
import hashlib
import json
import pathlib
import sys

root = pathlib.Path.cwd()
findings = []
try:
    candidate = json.load(sys.stdin)
except Exception as error:
    candidate = None
    findings.append(f"the returned value is not JSON: {{error}}")
expected = {expected!r}
if candidate != expected:
    findings.append(f"the returned value is {{candidate!r}}; expected {{expected!r}}")
expected_files = {expected_files!r}
for relative, digest in expected_files.items():
    path = root / relative
    if not path.is_file():
        findings.append(f"{{relative}} is missing")
    elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        findings.append(f"{{relative}} changed")
print("\\n".join(findings))
'''


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_schema(properties: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _observation_batches(seed: int, names: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    categories = ("priority", "standard", "deferred", "standard")
    batches: dict[str, list[dict[str, Any]]] = {}
    for shard_index, name in enumerate(names):
        rows = []
        for row_index in range(32):
            quantity = 20 + ((row_index * 37 + shard_index * 53 + seed) % 181)
            rows.append(
                {
                    "record": f"{name}-{row_index + 1:03d}",
                    "category": categories[(row_index + shard_index + seed) % len(categories)],
                    "accepted": (row_index * 3 + shard_index + seed) % 7 != 0,
                    "quantity": quantity,
                    "annotation": f"retained observation {seed:02d}-{shard_index:02d}-{row_index:03d} " + "x" * 44,
                }
            )
        batches[name] = rows
    return batches


def _winning_batch(batches: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    totals = {
        name: sum(row["quantity"] for row in rows if row["accepted"] and row["category"] == "priority")
        for name, rows in batches.items()
    }
    winner = max(totals, key=lambda name: (totals[name], name))
    count = sum(row["accepted"] and row["category"] == "priority" for row in batches[winner])
    return {"shard": winner, "priority_quantity": totals[winner], "record_count": count}


def _batch_tool(batches: dict[str, list[dict[str, Any]]]) -> str:
    return f'''#!/usr/bin/python3
import json
import sys

batches = {batches!r}
if len(sys.argv) != 2 or sys.argv[1] not in batches:
    print(json.dumps({{"error": "give one declared shard name"}}))
    raise SystemExit(2)
name = sys.argv[1]
print(json.dumps({{"shard": name, "observations": batches[name]}}, separators=(",", ":")))
'''


def _batch_fixture(
    workspace: Path,
    grader: Path,
    *,
    seed: int,
    names: tuple[str, ...],
) -> dict[str, Any]:
    batches = _observation_batches(seed, names)
    expected = _winning_batch(batches)
    readme = _write(
        workspace,
        "README.md",
        """# Observation batches

Each observation names a category, acceptance state, and quantity. The assigned
task defines the aggregate to return. Repository files must remain unchanged.
""",
    )
    tool = _write(grader, "observation_batch", _batch_tool(batches), executable=True)
    grade = _write(
        grader,
        "grade",
        _candidate_grader(expected, {"README.md": _sha256(readme)}),
        executable=True,
    )
    return {"grade": grade, "tool": tool, "oracle": expected, "shards": names}


def _development_batch_materialize(workspace: Path, grader: Path) -> dict[str, Any]:
    return _batch_fixture(
        workspace,
        grader,
        seed=17,
        names=("cedar", "elm", "fir", "maple", "oak"),
    )


def _held_back_batch_materialize(workspace: Path, grader: Path) -> dict[str, Any]:
    return _batch_fixture(
        workspace,
        grader,
        seed=43,
        names=("amber", "cobalt", "indigo", "ochre", "silver", "violet"),
    )


def _batch_config(workspace: Path, metadata: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    shards = ", ".join(metadata["shards"])
    return _base_config(
        workspace,
        model,
        task=(
            f"Inspect every observation batch named here: {shards}. Select the shard with the greatest sum of "
            "quantity among observations whose category is priority and whose accepted field is true. Return the "
            "shard, that quantity, and the number of contributing observations. Preserve repository files."
        ),
        returns=_json_schema(
            {
                "shard": {"type": "string"},
                "priority_quantity": {"type": "integer"},
                "record_count": {"type": "integer"},
            }
        ),
        tool_defs={
            "observation_batch": {
                "exec": str(metadata["tool"]),
                "description": (
                    "Return the complete observation batch for one shard. "
                    f"Call with args containing one of: {shards}."
                ),
            }
        },
    )


def _batch_oracle(_workspace: Path, _metadata: dict[str, Any]) -> None:
    return None


def _charge_rows() -> tuple[dict[str, str], dict[str, Any]]:
    customers = ("acorn", "birch", "cinder", "dune", "ember", "flint", "grove")
    files: dict[str, str] = {}
    totals = {name: [0, 0] for name in customers}
    for file_index in range(12):
        lines = ["charge_id,customer,status,cents"]
        for row_index in range(45):
            customer = customers[(row_index * 5 + file_index * 3) % len(customers)]
            status = "completed" if (row_index + file_index * 2) % 6 not in (0, 1) else "reversed"
            cents = 125 + ((row_index * 271 + file_index * 389) % 8000)
            lines.append(f"ch_{file_index:02d}_{row_index:03d},{customer},{status},{cents}")
            if status == "completed":
                totals[customer][0] += cents
                totals[customer][1] += 1
        files[f"records/part-{file_index + 1:02d}.csv"] = "\n".join(lines) + "\n"
    winner = max(totals, key=lambda name: (totals[name][0], name))
    expected = {"customer": winner, "completed_cents": totals[winner][0], "record_count": totals[winner][1]}
    return files, expected


def _charge_materialize(workspace: Path, grader: Path) -> dict[str, Any]:
    files, expected = _charge_rows()
    digests = {}
    for relative, content in files.items():
        path = _write(workspace, relative, content)
        digests[relative] = _sha256(path)
    grade = _write(grader, "grade", _candidate_grader(expected, digests), executable=True)
    return {"grade": grade, "oracle": expected}


def _charge_config(workspace: Path, _metadata: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    return _base_config(
        workspace,
        model,
        task=(
            "Across records/*.csv, find the customer with the greatest sum of cents over completed charges. "
            "Return the customer, completed cent total, and contributing record count. Preserve repository files."
        ),
        returns=_json_schema(
            {
                "customer": {"type": "string"},
                "completed_cents": {"type": "integer"},
                "record_count": {"type": "integer"},
            }
        ),
        execute=["/usr/bin/awk", "/usr/bin/head", "/usr/bin/python3", "/usr/bin/sort", "/usr/bin/tail"],
    )


def _charge_oracle(_workspace: Path, _metadata: dict[str, Any]) -> None:
    return None


VISIBLE_BATCH_TEST = '''from src.batches import batches

assert batches([], 3) == []
assert batches([1, 2, 3], 3) == [[1, 2, 3]]
assert batches([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
'''


HIDDEN_BATCH_GRADE = '''#!/usr/bin/python3
import pathlib
import sys

sys.dont_write_bytecode = True
root = pathlib.Path.cwd()
findings = []
try:
    sys.path.insert(0, str(root))
    from src.batches import batches
    cases = [
        ((range(7), 3), [[0, 1, 2], [3, 4, 5], [6]]),
        (((item for item in "abcd"), 2), [["a", "b"], ["c", "d"]]),
        (([1], 4), [[1]]),
    ]
    for (items, size), expected in cases:
        observed = batches(items, size)
        if observed != expected:
            findings.append(f"batches returned {observed!r}; expected {expected!r}")
    for size in (0, -1):
        try:
            batches([1], size)
            findings.append(f"batches accepted size {size}")
        except ValueError:
            pass
except Exception as error:
    findings.append(f"loading or calling src/batches.py failed: {error}")
print("\\n".join(findings))
'''


VISIBLE_CHECK = '''#!/usr/bin/python3
import pathlib
import subprocess

result = subprocess.run(
    ["/usr/bin/python3", "-B", "test_batches.py"],
    cwd=pathlib.Path.cwd(),
    text=True,
    capture_output=True,
    check=False,
)
if result.returncode != 0:
    print("the visible test failed")
    print(result.stdout.strip())
    print(result.stderr.strip())
'''


def _repair_materialize(workspace: Path, grader: Path) -> dict[str, Any]:
    _write(
        workspace,
        "src/batches.py",
        '''"""Partition finite iterables into lists."""


def batches(items, size):
    """Return consecutive lists containing at most size items."""
    if size <= 0:
        raise ValueError("size must be positive")
    values = list(items)
    return [values[index:index + size] for index in range(0, len(values), size + 1)]''',
    )
    _write(workspace, "src/__init__.py", "")
    _write(workspace, "test_batches.py", VISIBLE_BATCH_TEST)
    check = _write(grader, "check", VISIBLE_CHECK, executable=True)
    grade = _write(grader, "grade", HIDDEN_BATCH_GRADE, executable=True)
    return {"check": check, "grade": grade, "oracle": None}


def _repair_config(workspace: Path, metadata: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    check = Path(metadata["check"])
    return _base_config(
        workspace,
        model,
        task=(
            "Fix src/batches.py so batches partitions every finite iterable into consecutive lists of at most "
            "the requested positive size. A non-positive size must raise ValueError. Preserve the public function "
            "and make the visible tests pass."
        ),
        verify="check",
        tool_defs={
            "check": {
                "exec": str(check),
                "description": (
                    "Run the visible repository test. Print findings on failure and print nothing on success."
                ),
            }
        },
        execute=["/usr/bin/python3"],
    )


def _repair_oracle(workspace: Path, _metadata: dict[str, Any]) -> None:
    _write(
        workspace,
        "src/batches.py",
        '''"""Partition finite iterables into lists."""


def batches(items, size):
    """Return consecutive lists containing at most size items."""
    if size <= 0:
        raise ValueError("size must be positive")
    values = list(items)
    return [values[index:index + size] for index in range(0, len(values), size)]''',
    )


TASKS = (
    Task(
        "shard-priority-total",
        "development",
        "Confirm that the model selects and successfully uses registry composition.",
        4,
        32000,
        3000,
        180,
        _development_batch_materialize,
        _batch_config,
        _batch_oracle,
    ),
    Task(
        "regional-capacity-selection",
        "holdout",
        "Aggregate several configured-tool results that a shell cannot obtain directly.",
        4,
        36000,
        3000,
        180,
        _held_back_batch_materialize,
        _batch_config,
        _batch_oracle,
    ),
    Task(
        "completed-charge-leader",
        "holdout",
        "Aggregate repository records that a narrow shell command can handle.",
        4,
        24000,
        3000,
        180,
        _charge_materialize,
        _charge_config,
        _charge_oracle,
    ),
    Task(
        "batch-partition-repair",
        "holdout",
        "Measure quality and fixed request cost on an ordinary code repair.",
        5,
        24000,
        5000,
        180,
        _repair_materialize,
        _repair_config,
        _repair_oracle,
    ),
)


def _base_config(
    workspace: Path,
    model: dict[str, Any],
    *,
    task: str,
    returns: dict[str, Any] | None = None,
    verify: str | None = None,
    tool_defs: dict[str, Any] | None = None,
    execute: list[str] | None = None,
) -> dict[str, Any]:
    done_when: dict[str, Any]
    if returns is not None:
        done_when = {"returns": returns}
    elif verify is not None:
        done_when = {"verify": verify, "retries": 1}
    else:
        raise ValueError("a completion rule is required")
    return {
        "version": 4,
        "name": "python-composition-assessment",
        "instructions": {
            "10-role": "You are a coding agent working in a small repository.",
            "20-completion": (
                "Make the smallest complete change when a change is required. Run relevant checks and complete "
                "only when the requested result is supported by observed evidence."
            ),
            "30-executables": "Use absolute paths when a shell command starts an executable.",
        },
        "tools": ["read", "grep", "edit", "bash", *(tool_defs or {})],
        "tool_defs": tool_defs or {},
        "grants": {
            "read": [str(workspace)],
            "write": [str(workspace)],
            "execute": execute or ["/usr/bin/python3"],
        },
        "budget": {},
        "done_when": done_when,
        "model": model,
        "sandbox": {"mode": "required"},
        "task": task,
    }


def task_by_name(name: str) -> Task:
    for task in TASKS:
        if task.name == name:
            return task
    raise KeyError(name)


def configuration_by_name(name: str) -> Configuration:
    for configuration in CONFIGURATIONS:
        if configuration.name == name:
            return configuration
    raise KeyError(name)


def prepare_config(
    task: Task,
    configuration: Configuration,
    workspace: Path,
    metadata: dict[str, Any],
    model: dict[str, Any],
) -> dict[str, Any]:
    shaped = task.config(workspace, metadata, model)
    shaped["name"] = f"{task.name}-{configuration.name}"
    shaped["budget"] = {
        "model_calls": task.model_calls,
        "input_tokens": task.input_tokens,
        "output_tokens": task.output_tokens,
        "seconds": task.seconds,
    }
    if configuration.add_python:
        shaped["tools"].insert(4, "python")
    if configuration.add_shell_guidance:
        shaped["instructions"]["40-shell-output"] = (
            "When several shell operations can answer a question, combine them in one command. End the command "
            "with an operation that emits only the evidence needed for the next decision."
        )
    return shaped


def _canonical_bytes(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def mechanism_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    calls = assistant_calls(events)
    inner = [event_data(event) for event in events if event.get("type") == "tool/inner-call"]
    inner_ids = {item.get("call_id") for item in inner}
    results = [event_data(event) for event in events if event.get("type") == "tool/result"]
    inner_results = [result for result in results if result.get("call_id") in inner_ids]
    python_ids = {call.get("id") for call in calls if call.get("name") == "python"}
    python_results = [result for result in results if result.get("call_id") in python_ids]
    python_sources = [
        call.get("args", {}).get("source")
        for call in calls
        if call.get("name") == "python" and isinstance(call.get("args"), dict)
    ]
    python_source_bytes = sum(len(source.encode("utf-8")) for source in python_sources if isinstance(source, str))
    inner_rendered_bytes = sum(
        len(result.get("rendered", "").encode("utf-8"))
        for result in inner_results
        if isinstance(result.get("rendered", ""), str)
    )
    outer_python_rendered_bytes = sum(
        len(result.get("rendered", "").encode("utf-8"))
        for result in python_results
        if isinstance(result.get("rendered", ""), str)
    )
    by_tool: dict[str, int] = {}
    for item in inner:
        name = item.get("name")
        if isinstance(name, str):
            by_tool[name] = by_tool.get(name, 0) + 1
    return {
        "top_level_calls": len(calls),
        "top_level_tools": sorted({call.get("name") for call in calls if isinstance(call.get("name"), str)}),
        "python_calls": len(python_ids),
        "shell_calls": sum(call.get("name") == "bash" for call in calls),
        "inner_calls": len(inner),
        "inner_errors": sum(result.get("is_error") is True for result in inner_results),
        "inner_calls_by_tool": dict(sorted(by_tool.items())),
        "python_source_bytes": python_source_bytes,
        "inner_canonical_bytes": sum(_canonical_bytes(result.get("value")) for result in inner_results),
        "inner_rendered_bytes": inner_rendered_bytes,
        "outer_python_rendered_bytes": outer_python_rendered_bytes,
        "top_level_rendered_bytes": sum(
            len(result.get("rendered", "").encode("utf-8"))
            for result in results
            if result.get("call_id") not in inner_ids and isinstance(result.get("rendered", ""), str)
        ),
    }


def fixture_digest(workspace: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in workspace.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(workspace).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _redaction_values(model: dict[str, Any]) -> list[str]:
    return sorted(
        {value for value in model.values() if isinstance(value, str) and len(value) >= 4},
        key=len,
        reverse=True,
    )


def redact_text(text: str, values: Iterable[str]) -> str:
    for value in values:
        text = text.replace(value, "<model-route-redacted>")
    return text


def redact_value(value: Any, redactions: Iterable[str]) -> Any:
    if isinstance(value, str):
        return redact_text(value, redactions)
    if isinstance(value, list):
        return [redact_value(item, redactions) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item, redactions) for key, item in value.items()}
    return value


def run_attempt(
    binary: Path,
    run_root: Path,
    task: Task,
    configuration: Configuration,
    model: dict[str, Any],
    attempt: int,
    scheduled_index: int,
) -> dict[str, Any]:
    case = run_root / task.task_set / f"attempt-{attempt:02d}" / task.name / configuration.name
    workspace = case / "workspace"
    grader = case / "grader"
    log_dir = case / "episode"
    workspace.mkdir(parents=True)
    grader.mkdir()
    metadata = task.materialize(workspace, grader)
    before = fixture_digest(workspace)
    grade = Path(metadata["grade"]).resolve()
    config = prepare_config(task, configuration, workspace.resolve(), metadata, model)
    config_path = case / "config.json"
    write_json(config_path, config)

    started = time.monotonic()
    infrastructure_error = None
    try:
        completed = subprocess.run(
            [str(binary), "--config", str(config_path), "--log-dir", str(log_dir), "--headless"],
            text=True,
            capture_output=True,
            timeout=task.seconds + 30,
            check=False,
        )
        process = {
            "exit_code": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except subprocess.TimeoutExpired as error:
        infrastructure_error = f"foe exceeded the runner deadline of {task.seconds + 30} seconds"
        process = {
            "exit_code": None,
            "stdout": (error.stdout or "").strip() if isinstance(error.stdout, str) else "",
            "stderr": (error.stderr or "").strip() if isinstance(error.stderr, str) else "",
        }
    except OSError as error:
        infrastructure_error = f"foe could not be launched: {error}"
        process = {"exit_code": None, "stdout": "", "stderr": ""}
    duration = time.monotonic() - started

    logs = episode_logs(log_dir)
    if infrastructure_error is None:
        infrastructure_error = infrastructure_fault(log_dir, logs, process)
    outcome = root_outcome(log_dir)
    candidate = outcome.get("value") if outcome.get("kind") == "completed" else None
    artifact_correct, grader_findings, grader_stderr = run_grader(grade, workspace, candidate)
    trace = evaluate([log_dir]) if logs else {"valid": False, "violations": [{"message": "episode log is absent"}]}
    measured = usage(logs)
    within_budget = (
        measured["usage_reported"]
        and measured["model_calls"] <= task.model_calls
        and measured["input_tokens"] <= task.input_tokens
        and measured["output_tokens"] <= task.output_tokens
    )
    components = {
        "artifact_correct": artifact_correct,
        "outcome_correct": outcome.get("kind") == "completed",
        "trace_conformant": trace.get("valid") is True,
        "within_budget": within_budget,
    }
    events = read_events(log_dir / "episode.jsonl")
    messages = [event_data(event) for event in events if event.get("type") == "assistant/message"]
    starts = [event_data(event) for event in events if event.get("type") == "episode/start"]
    redactions = _redaction_values(model)
    return {
        "task": task.name,
        "task_set": task.task_set,
        "purpose": task.purpose,
        "configuration": configuration.name,
        "attempt": attempt,
        "scheduled_index": scheduled_index,
        "strict_success": all(components.values()) and infrastructure_error is None,
        "components": components,
        "outcome_kind": outcome.get("kind", "missing"),
        "usage": measured,
        "first_request_input_tokens": (
            messages[0].get("usage", {}).get("input")
            if messages and isinstance(messages[0].get("usage"), dict)
            else None
        ),
        "duration_seconds": round(duration, 3),
        "mechanism": mechanism_metrics(events),
        "fixture_before": before,
        "fixture_after": fixture_digest(workspace),
        "grader_findings": [redact_text(item, redactions) for item in grader_findings],
        "grader_stderr": redact_text(grader_stderr.strip(), redactions),
        "trace_violations": redact_value(trace.get("violations", []), redactions),
        "infrastructure_error": (
            redact_text(infrastructure_error, redactions) if isinstance(infrastructure_error, str) else None
        ),
        "process": {
            "exit_code": process["exit_code"],
            "stdout": redact_text(process["stdout"], redactions),
            "stderr": redact_text(process["stderr"], redactions),
        },
        "contract_fingerprint": starts[0].get("contract_fingerprint") if starts else None,
        "runtime": starts[0].get("runtime") if starts else None,
        "sandbox": starts[0].get("sandbox") if starts else None,
        "artifact_directory": str(case),
    }


def schedule(tasks: tuple[Task, ...], attempts: int) -> list[tuple[int, Task, Configuration]]:
    scheduled = []
    for attempt in range(1, attempts + 1):
        for task_index, task in enumerate(tasks):
            offset = (attempt - 1 + task_index) % len(CONFIGURATIONS)
            order = CONFIGURATIONS[offset:] + CONFIGURATIONS[:offset]
            scheduled.extend((attempt, task, configuration) for configuration in order)
    return scheduled


def _median(values: list[float | int]) -> float | None:
    return statistics.median(values) if values else None


def aggregate_configuration(results: list[dict[str, Any]], configuration: str) -> dict[str, Any]:
    selected = [result for result in results if result["configuration"] == configuration]
    measured = [result for result in selected if result["usage"]["usage_reported"]]
    successful = [result for result in selected if result["strict_success"]]
    totals: dict[str, Any] = {
        "model_calls": sum(result["usage"]["model_calls"] for result in selected),
        "attempts_with_reported_usage": len(measured),
    }
    for field in TOKEN_FIELDS:
        totals[field] = sum(result["usage"][field] for result in measured) if measured else None
    strict_count = len(successful)
    input_per_success = None
    total_per_success = None
    if len(measured) == len(selected) and strict_count:
        input_per_success = totals["input_tokens"] / strict_count
        total_per_success = totals["total_tokens"] / strict_count
    task_results = {}
    for task in TASKS:
        matching = [result for result in selected if result["task"] == task.name]
        if matching:
            task_results[task.name] = {
                "attempts": len(matching),
                "strict_successes": sum(result["strict_success"] for result in matching),
                "input_tokens": sum(
                    result["usage"]["input_tokens"]
                    for result in matching
                    if result["usage"]["input_tokens"] is not None
                ),
                "python_activations": sum(result["mechanism"]["python_calls"] > 0 for result in matching),
                "shell_activations": sum(result["mechanism"]["shell_calls"] > 0 for result in matching),
            }
    return {
        "attempts": len(selected),
        "strict_successes": strict_count,
        "strict_success_rate": strict_count / len(selected) if selected else None,
        "infrastructure_failures": sum(result["infrastructure_error"] is not None for result in selected),
        "usage": totals,
        "input_tokens_per_strict_success": input_per_success,
        "total_tokens_per_strict_success": total_per_success,
        "successful_attempt_median_input_tokens": _median(
            [result["usage"]["input_tokens"] for result in successful if result["usage"]["input_tokens"] is not None]
        ),
        "successful_attempt_median_duration_seconds": _median(
            [result["duration_seconds"] for result in successful]
        ),
        "first_request_median_input_tokens": _median(
            [
                result["first_request_input_tokens"]
                for result in selected
                if result["first_request_input_tokens"] is not None
            ]
        ),
        "python_activations": sum(result["mechanism"]["python_calls"] > 0 for result in selected),
        "shell_activations": sum(result["mechanism"]["shell_calls"] > 0 for result in selected),
        "task_results": task_results,
    }


def recommendation(results: list[dict[str, Any]], holdout_attempts: int) -> dict[str, Any]:
    holdout_tasks = tuple(task for task in TASKS if task.task_set == "holdout")
    expected = len(holdout_tasks) * holdout_attempts
    by_configuration = {
        configuration.name: aggregate_configuration(
            [result for result in results if result["task_set"] == "holdout"],
            configuration.name,
        )
        for configuration in CONFIGURATIONS
    }
    findings = []
    if holdout_attempts < 3:
        findings.append("The held-back comparison has fewer than three attempts per task.")
    if any(report["attempts"] != expected for report in by_configuration.values()):
        findings.append("The held-back comparison is incomplete.")
    python_report = by_configuration["python-tool-composition"]
    simpler = [by_configuration["ordinary-coding-tools"], by_configuration["shell-output-narrowing"]]
    if python_report["strict_successes"] != expected:
        findings.append("The Python composition configuration did not pass every held-back attempt.")
    for task in holdout_tasks:
        python_successes = python_report["task_results"].get(task.name, {}).get("strict_successes", 0)
        simpler_best = max(report["task_results"].get(task.name, {}).get("strict_successes", 0) for report in simpler)
        if python_successes < simpler_best:
            findings.append(f"Python composition reduced task quality on {task.name}.")
    if any(report["usage"]["attempts_with_reported_usage"] != expected for report in by_configuration.values()):
        findings.append("Provider usage is incomplete for at least one held-back configuration.")
    composition_task = python_report["task_results"].get("regional-capacity-selection", {})
    if composition_task.get("python_activations") != holdout_attempts:
        findings.append("Python composition did not activate on every configured-tool aggregation attempt.")

    python_cost = python_report["total_tokens_per_strict_success"]
    simpler_costs = [
        report["total_tokens_per_strict_success"]
        for report in simpler
        if report["total_tokens_per_strict_success"] is not None
    ]
    quality_advantage = python_report["strict_successes"] > max(report["strict_successes"] for report in simpler)
    efficiency_ratio = python_cost / min(simpler_costs) if python_cost is not None and simpler_costs else None
    efficiency_advantage = efficiency_ratio is not None and efficiency_ratio <= 0.90
    if not quality_advantage and not efficiency_advantage:
        findings.append(
            "Python composition added neither a strict-success gain nor a 10 percent total-token reduction "
            "against the lower-token simpler configuration."
        )
    include = not findings and (quality_advantage or efficiency_advantage)
    return {
        "include_python_by_default": include,
        "quality_advantage": quality_advantage,
        "total_token_ratio_against_lower_token_simpler_configuration": efficiency_ratio,
        "material_total_token_advantage": efficiency_advantage,
        "findings": findings,
    }


def development_gate(results: list[dict[str, Any]], attempts: int) -> tuple[bool, list[str]]:
    selected = [
        result
        for result in results
        if result["task_set"] == "development" and result["configuration"] == "python-tool-composition"
    ]
    findings = []
    if len(selected) != attempts:
        findings.append("The development activation run is incomplete.")
    if not all(result["strict_success"] for result in selected):
        findings.append("The Python composition configuration failed a development activation attempt.")
    if not all(
        result["mechanism"]["python_calls"] > 0 and result["mechanism"]["inner_calls"] >= 2
        for result in selected
    ):
        findings.append("The Python tool did not compose at least two inner calls in every development attempt.")
    return not findings, findings


def spending_plan(development_attempts: int, holdout_attempts: int) -> str:
    rows = []
    total_calls = total_input = total_output = total_seconds = 0
    for task in TASKS:
        attempts = development_attempts if task.task_set == "development" else holdout_attempts
        multiplier = attempts * len(CONFIGURATIONS)
        calls = task.model_calls * multiplier
        input_tokens = task.input_tokens * multiplier
        output_tokens = task.output_tokens * multiplier
        seconds = task.seconds * multiplier
        rows.append((task.name, calls, input_tokens, output_tokens))
        total_calls += calls
        total_input += input_tokens
        total_output += output_tokens
        total_seconds += seconds
    lines = [
        "This assessment calls the configured model endpoint and spends real credit.",
        "Largest spend it can incur:",
        "",
        f"  {'model calls':>11}  {'input':>9}  {'output':>8}  task",
    ]
    lines.extend(
        f"  {calls:>11}  {input_tokens:>9,}  {output_tokens:>8,}  {name}"
        for name, calls, input_tokens, output_tokens in rows
    )
    lines.extend(
        [
            f"  {total_calls:>11}  {total_input:>9,}  {total_output:>8,}  every scheduled attempt",
            "",
            f"The cumulative wall-clock allowance is {total_seconds:,} seconds.",
            "Held-back tasks are skipped if the development activation gate fails.",
            "No attempt was launched. Add --confirm-spend to launch them.",
        ]
    )
    return "\n".join(lines)


def _model_config(path: Path) -> tuple[dict[str, Any], str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("provider"), str)
        or not isinstance(value.get("model"), str)
    ):
        raise ValueError("the model configuration must be an object with provider and model strings")
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return value, "sha256:" + hashlib.sha256(canonical).hexdigest()


def _selected_tasks(task_set: str) -> tuple[Task, ...]:
    return tuple(task for task in TASKS if task.task_set == task_set)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--foe", type=Path, required=True, help="Path to the built foe binary.")
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="Path inside the clean source checkout associated with the evaluated binary.",
    )
    parser.add_argument(
        "--model-config",
        type=Path,
        required=True,
        help="JSON file containing the model block. The report retains only its SHA-256 digest.",
    )
    parser.add_argument("--development-attempts", type=int, default=2)
    parser.add_argument("--holdout-attempts", type=int, default=3)
    parser.add_argument("--confirm-spend", action="store_true")
    parser.add_argument("--keep", type=Path, help="Keep configurations, workspaces, logs, and report here.")
    args = parser.parse_args()

    def refuse(message: str) -> int:
        print(message, file=sys.stderr)
        return NOTHING_LAUNCHED

    if not 1 <= args.development_attempts <= 3 or not 1 <= args.holdout_attempts <= 3:
        return refuse("attempt counts must be between 1 and 3")
    binary = args.foe.resolve()
    if not binary.is_file():
        return refuse(f"foe binary does not exist: {binary}")
    try:
        model, model_digest = _model_config(args.model_config)
        evaluated = evaluated_foe(args.source_root, binary)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return refuse(str(error))
    if not args.confirm_spend:
        print(spending_plan(args.development_attempts, args.holdout_attempts))
        return NOTHING_LAUNCHED

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.keep is None:
        temporary = tempfile.TemporaryDirectory(prefix="foe-python-composition-")
        run_root = Path(temporary.name)
    else:
        keep = args.keep
        workspace = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
        if not keep.is_absolute() and workspace:
            keep = Path(workspace) / keep
        run_root = keep.resolve()
        if run_root.exists() and any(run_root.iterdir()):
            return refuse(f"assessment output directory is not empty: {run_root}")
        run_root.mkdir(parents=True, exist_ok=True)

    results = []
    scheduled_index = 0
    for attempt, task, configuration in schedule(_selected_tasks("development"), args.development_attempts):
        scheduled_index += 1
        print(
            f"composition assessment: {task.task_set}, attempt {attempt}, {task.name}, {configuration.name}",
            file=sys.stderr,
            flush=True,
        )
        results.append(run_attempt(binary, run_root, task, configuration, model, attempt, scheduled_index))
    development_passed, development_findings = development_gate(results, args.development_attempts)
    if development_passed:
        for attempt, task, configuration in schedule(_selected_tasks("holdout"), args.holdout_attempts):
            scheduled_index += 1
            print(
                f"composition assessment: {task.task_set}, attempt {attempt}, {task.name}, {configuration.name}",
                file=sys.stderr,
                flush=True,
            )
            results.append(run_attempt(binary, run_root, task, configuration, model, attempt, scheduled_index))

    report = {
        "schema_version": 1,
        "evaluation": "python-composition-default-adoption",
        "evaluated_foe": evaluated,
        "model_config_sha256": model_digest,
        "development_attempts_per_task": args.development_attempts,
        "holdout_attempts_per_task": args.holdout_attempts,
        "development_gate": {"passed": development_passed, "findings": development_findings},
        "holdout_skipped": not development_passed,
        "configuration_aggregates": {
            configuration.name: aggregate_configuration(results, configuration.name)
            for configuration in CONFIGURATIONS
        },
        "recommendation": (
            recommendation(results, args.holdout_attempts)
            if development_passed
            else {"include_python_by_default": False, "findings": development_findings}
        ),
        "results": results,
    }
    redacted_report = redact_value(report, _redaction_values(model))
    if args.keep is not None:
        redacted_report["artifact_directory"] = str(run_root)
        write_json(run_root / "report.json", redacted_report)
    print(json.dumps(redacted_report, indent=2, sort_keys=True))
    infrastructure_failed = any(result["infrastructure_error"] is not None for result in results)
    if temporary is not None:
        temporary.cleanup()
    return DEPLOYMENT_FAULT if infrastructure_failed else EVALUATED


if __name__ == "__main__":
    raise SystemExit(main())
