#!/usr/bin/python3
"""Compare tool composition with two simpler coding configurations."""

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


@dataclass(frozen=True)
class Configuration:
    name: str
    add_composition: bool = False
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
    Configuration("tool-composition", add_composition=True),
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


def _capacity_records(seed: int, regions: tuple[str, ...]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    records = {}
    totals = {region: [0, 0] for region in regions}
    for index in range(15):
        key = f"capacity-{seed:02d}-{index + 1:03d}"
        region = regions[(index * 3 + seed) % len(regions)]
        available = (index * 5 + seed) % 4 != 0
        capacity = 100 + ((index * 131 + seed * 17) % 900)
        annotation = " ".join(
            hashlib.sha256(f"{seed}:{index}:{part}".encode()).hexdigest() for part in range(32)
        )
        records[key] = {
            "key": key,
            "region": region,
            "state": "available" if available else "reserved",
            "capacity": capacity,
            "annotation": annotation,
        }
        if available:
            totals[region][0] += capacity
            totals[region][1] += 1
    winner = max(totals, key=lambda region: (totals[region][0], region))
    expected = {
        "region": winner,
        "available_capacity": totals[winner][0],
        "record_count": totals[winner][1],
    }
    return records, expected


def _catalog_tool(keys: tuple[str, ...]) -> str:
    return f'''#!/usr/bin/python3
import json
import sys

keys = {keys!r}
if len(sys.argv) != 1:
    print(json.dumps({{"error": "region_catalog accepts no arguments"}}))
    raise SystemExit(2)
print(json.dumps({{"keys": keys}}, separators=(",", ":")))
'''


def _capacity_record_tool(records: dict[str, dict[str, Any]]) -> str:
    return f'''#!/usr/bin/python3
import json
import sys

records = {records!r}
if len(sys.argv) != 2 or sys.argv[1] not in records:
    print(json.dumps({{"error": "give one key returned by region_catalog"}}))
    raise SystemExit(2)
print(json.dumps(records[sys.argv[1]], separators=(",", ":")))
'''


def _capacity_fixture(
    workspace: Path,
    grader: Path,
    *,
    seed: int,
    regions: tuple[str, ...],
) -> dict[str, Any]:
    records, expected = _capacity_records(seed, regions)
    readme = _write(
        workspace,
        "README.md",
        """# Regional capacity

The catalog and capacity records are available through configured tools. The
assigned task defines the aggregate to return. Repository files must remain
unchanged.
""",
    )
    catalog = _write(grader, "region_catalog", _catalog_tool(tuple(records)), executable=True)
    record = _write(grader, "capacity_record", _capacity_record_tool(records), executable=True)
    grade = _write(
        grader,
        "grade",
        _candidate_grader(expected, {"README.md": _sha256(readme)}),
        executable=True,
    )
    return {"catalog": catalog, "record": record, "grade": grade, "oracle": expected}


def _capability_control_materialize(workspace: Path, grader: Path) -> dict[str, Any]:
    return _capacity_fixture(
        workspace,
        grader,
        seed=17,
        regions=("central", "coastal", "northern", "southern"),
    )


def _dependent_capacity_materialize(workspace: Path, grader: Path) -> dict[str, Any]:
    return _capacity_fixture(
        workspace,
        grader,
        seed=43,
        regions=("eastern", "mountain", "western", "plains"),
    )


def _capacity_config(
    workspace: Path,
    metadata: dict[str, Any],
    model: dict[str, Any],
    *,
    require_composition: bool,
) -> dict[str, Any]:
    method = (
        "Use one compose_tools call for the lookup. Its source must call region_catalog first, then call "
        "capacity_record for every returned key, and return only the requested aggregate. "
        if require_composition
        else "Use region_catalog to discover every capacity record key, then inspect every named record. "
    )
    return _base_config(
        workspace,
        model,
        task=(
            method
            + "Select the region with the greatest sum of capacity among records whose state is available. Return "
            "the region, that capacity, and the number of contributing records. Preserve repository files."
        ),
        returns=_json_schema(
            {
                "region": {"type": "string"},
                "available_capacity": {"type": "integer"},
                "record_count": {"type": "integer"},
            }
        ),
        tool_defs={
            "region_catalog": {
                "exec": str(metadata["catalog"]),
                "description": "Accept no args. The successful stdout JSON has a keys array for capacity_record.",
            },
            "capacity_record": {
                "exec": str(metadata["record"]),
                "description": (
                    "Accept one key from region_catalog as the sole args entry. The successful stdout is one "
                    "complete capacity record as JSON."
                ),
            },
        },
    )


def _capability_control_config(workspace: Path, metadata: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    return _capacity_config(workspace, metadata, model, require_composition=True)


def _dependent_capacity_config(workspace: Path, metadata: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    return _capacity_config(workspace, metadata, model, require_composition=False)


def _capacity_oracle(_workspace: Path, _metadata: dict[str, Any]) -> None:
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
        "dependent-capacity-control",
        "capability-control",
        "Confirm that explicit composition completes a dependent configured-tool chain.",
        4,
        36000,
        3000,
        180,
        _capability_control_materialize,
        _capability_control_config,
        _capacity_oracle,
    ),
    Task(
        "dependent-capacity-selection",
        "mixed-workload",
        "Measure natural composition on a dependent configured-tool chain.",
        4,
        36000,
        3000,
        180,
        _dependent_capacity_materialize,
        _dependent_capacity_config,
        _capacity_oracle,
    ),
    Task(
        "completed-charge-leader",
        "mixed-workload",
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
        "mixed-workload",
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
        "name": "tool-composition-assessment",
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
    if configuration.add_composition:
        shaped["tools"].insert(4, "compose_tools")
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
    composition_ids = {call.get("id") for call in calls if call.get("name") == "compose_tools"}
    composition_results = [result for result in results if result.get("call_id") in composition_ids]
    composition_sources = [
        call.get("args", {}).get("source")
        for call in calls
        if call.get("name") == "compose_tools" and isinstance(call.get("args"), dict)
    ]
    composition_source_bytes = sum(
        len(source.encode("utf-8")) for source in composition_sources if isinstance(source, str)
    )
    inner_rendered_bytes = sum(
        len(result.get("rendered", "").encode("utf-8"))
        for result in inner_results
        if isinstance(result.get("rendered", ""), str)
    )
    outer_composition_rendered_bytes = sum(
        len(result.get("rendered", "").encode("utf-8"))
        for result in composition_results
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
        "composition_calls": len(composition_ids),
        "shell_calls": sum(call.get("name") == "bash" for call in calls),
        "shell_python_calls": sum(
            call.get("name") == "bash"
            and isinstance(call.get("args"), dict)
            and isinstance(call["args"].get("command"), str)
            and "/usr/bin/python3" in call["args"]["command"]
            for call in calls
        ),
        "inner_calls": len(inner),
        "inner_errors": sum(result.get("is_error") is True for result in inner_results),
        "inner_calls_by_tool": dict(sorted(by_tool.items())),
        "composition_source_bytes": composition_source_bytes,
        "inner_canonical_bytes": sum(_canonical_bytes(result.get("value")) for result in inner_results),
        "inner_rendered_bytes": inner_rendered_bytes,
        "outer_composition_rendered_bytes": outer_composition_rendered_bytes,
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
                "composition_activations": sum(
                    result["mechanism"]["composition_calls"] > 0 for result in matching
                ),
                "shell_activations": sum(result["mechanism"]["shell_calls"] > 0 for result in matching),
                "shell_python_activations": sum(
                    result["mechanism"]["shell_python_calls"] > 0 for result in matching
                ),
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
        "composition_activations": sum(result["mechanism"]["composition_calls"] > 0 for result in selected),
        "shell_activations": sum(result["mechanism"]["shell_calls"] > 0 for result in selected),
        "shell_python_activations": sum(
            result["mechanism"]["shell_python_calls"] > 0 for result in selected
        ),
        "task_results": task_results,
    }


def recommendation(results: list[dict[str, Any]], comparison_attempts: int) -> dict[str, Any]:
    comparison_tasks = tuple(task for task in TASKS if task.task_set == "mixed-workload")
    comparison_results = [result for result in results if result["task_set"] == "mixed-workload"]
    expected = len(comparison_tasks) * comparison_attempts
    by_configuration = {
        configuration.name: aggregate_configuration(comparison_results, configuration.name)
        for configuration in CONFIGURATIONS
    }
    findings = []
    if comparison_attempts < 3:
        findings.append("The mixed-workload comparison has fewer than three attempts per task.")
    if any(report["attempts"] != expected for report in by_configuration.values()):
        findings.append("The mixed-workload comparison is incomplete.")
    composition_report = by_configuration["tool-composition"]
    simpler = [by_configuration["ordinary-coding-tools"], by_configuration["shell-output-narrowing"]]
    if composition_report["strict_successes"] != expected:
        findings.append("The tool-composition configuration did not pass every mixed-workload attempt.")
    for task in comparison_tasks:
        composition_successes = composition_report["task_results"].get(task.name, {}).get("strict_successes", 0)
        simpler_best = max(report["task_results"].get(task.name, {}).get("strict_successes", 0) for report in simpler)
        if composition_successes < simpler_best:
            findings.append(f"Tool composition reduced task quality on {task.name}.")
    if any(report["usage"]["attempts_with_reported_usage"] != expected for report in by_configuration.values()):
        findings.append("Provider usage is incomplete for at least one mixed-workload configuration.")
    dependent_task = composition_report["task_results"].get("dependent-capacity-selection", {})
    natural_activations = dependent_task.get("composition_activations", 0)
    required_natural_activations = comparison_attempts // 2 + 1
    if natural_activations < required_natural_activations:
        findings.append(
            "Tool composition did not activate naturally on a majority of dependent-call attempts."
        )

    composition_cost = composition_report["total_tokens_per_strict_success"]
    simpler_costs = [
        report["total_tokens_per_strict_success"]
        for report in simpler
        if report["total_tokens_per_strict_success"] is not None
    ]
    quality_advantage = composition_report["strict_successes"] > max(
        report["strict_successes"] for report in simpler
    )
    efficiency_ratio = (
        composition_cost / min(simpler_costs) if composition_cost is not None and simpler_costs else None
    )
    efficiency_advantage = efficiency_ratio is not None and efficiency_ratio <= 0.90
    if not quality_advantage and not efficiency_advantage:
        findings.append(
            "Tool composition added neither a strict-success gain nor a 10 percent total-token reduction "
            "against the lower-token simpler configuration."
        )
    include = not findings and (quality_advantage or efficiency_advantage)
    return {
        "include_composition_by_default": include,
        "quality_advantage": quality_advantage,
        "natural_dependent_task_activations": natural_activations,
        "required_natural_dependent_task_activations": required_natural_activations,
        "total_token_ratio_against_lower_token_simpler_configuration": efficiency_ratio,
        "material_total_token_advantage": efficiency_advantage,
        "findings": findings,
    }


def capability_control(results: list[dict[str, Any]], attempts: int) -> tuple[bool, list[str]]:
    selected = [
        result
        for result in results
        if result["task_set"] == "capability-control" and result["configuration"] == "tool-composition"
    ]
    findings = []
    if len(selected) != attempts:
        findings.append("The composition capability control is incomplete.")
    if len(selected) == attempts and not all(result["strict_success"] for result in selected):
        findings.append("The tool-composition configuration failed a capability-control attempt.")
    if len(selected) == attempts and not all(
        result["mechanism"]["composition_calls"] == 1
        and result["mechanism"]["inner_calls_by_tool"].get("region_catalog") == 1
        and result["mechanism"]["inner_calls_by_tool"].get("capacity_record") == 15
        for result in selected
    ):
        findings.append("The composition capability control did not complete its declared dependent tool chain.")
    return not findings, findings


def spending_plan(capability_attempts: int, comparison_attempts: int) -> str:
    rows = []
    total_calls = total_input = total_output = total_seconds = 0
    for task in TASKS:
        if task.task_set == "capability-control":
            multiplier = capability_attempts
        else:
            multiplier = comparison_attempts * len(CONFIGURATIONS)
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
            "Mixed-workload tasks are skipped only if the forced capability control fails.",
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
    parser.add_argument("--capability-attempts", type=int, default=2)
    parser.add_argument("--comparison-attempts", type=int, default=3)
    parser.add_argument("--confirm-spend", action="store_true")
    parser.add_argument("--keep", type=Path, help="Keep configurations, workspaces, logs, and report here.")
    args = parser.parse_args()

    def refuse(message: str) -> int:
        print(message, file=sys.stderr)
        return NOTHING_LAUNCHED

    if not 1 <= args.capability_attempts <= 3 or not 1 <= args.comparison_attempts <= 3:
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
        print(spending_plan(args.capability_attempts, args.comparison_attempts))
        return NOTHING_LAUNCHED

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.keep is None:
        temporary = tempfile.TemporaryDirectory(prefix="foe-tool-composition-")
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
    control_task = _selected_tasks("capability-control")[0]
    composition_configuration = configuration_by_name("tool-composition")
    for attempt in range(1, args.capability_attempts + 1):
        scheduled_index += 1
        print(
            f"composition assessment: {control_task.task_set}, attempt {attempt}, "
            f"{control_task.name}, {composition_configuration.name}",
            file=sys.stderr,
            flush=True,
        )
        results.append(
            run_attempt(
                binary,
                run_root,
                control_task,
                composition_configuration,
                model,
                attempt,
                scheduled_index,
            )
        )
    control_passed, control_findings = capability_control(results, args.capability_attempts)
    if control_passed:
        for attempt, task, configuration in schedule(_selected_tasks("mixed-workload"), args.comparison_attempts):
            scheduled_index += 1
            print(
                f"composition assessment: {task.task_set}, attempt {attempt}, {task.name}, {configuration.name}",
                file=sys.stderr,
                flush=True,
            )
            results.append(run_attempt(binary, run_root, task, configuration, model, attempt, scheduled_index))

    report = {
        "schema_version": 2,
        "evaluation": "tool-composition-default-adoption",
        "evaluated_foe": evaluated,
        "model_config_sha256": model_digest,
        "capability_attempts": args.capability_attempts,
        "comparison_attempts_per_task": args.comparison_attempts,
        "capability_control": {"passed": control_passed, "findings": control_findings},
        "mixed_workload_skipped": not control_passed,
        "configuration_aggregates": {
            configuration.name: aggregate_configuration(
                [result for result in results if result["task_set"] == "mixed-workload"],
                configuration.name,
            )
            for configuration in CONFIGURATIONS
        },
        "recommendation": (
            recommendation(results, args.comparison_attempts)
            if control_passed
            else {"include_composition_by_default": False, "findings": control_findings}
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
