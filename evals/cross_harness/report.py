#!/usr/bin/python3
"""Summarize the records `run.py` wrote: rates per arm, cells per task, and paired comparisons.

Every record names one task, one arm, one attempt, and the confusion cell
`tasks/protocol.py` placed the graded run in, or an infrastructure fault in
place of a cell. An attempt with a cell is scored; a faulted attempt is
counted beside the scored ones and enters no rate.

An attempt is actionable when its cell is `correct-completion` or
`correct-stop`: the arm either finished the task or stopped with a true
report, and in both cases the caller can act on what it was told. The
per-arm table states, over scored attempts:

- the actionable rate;
- the false-completion rate, the share of attempts reported complete that
  the grader rejected or that completion could not answer;
- block precision, the share of attempts reported `blocked` whose stop was
  correct, and block recall, the share of attempts on tasks that accept
  `blocked` which stopped that way with an accepted code;
- the killed rate and the damage rate;
- the cost to stop: the tokens and seconds of the attempts classified
  `correct-stop`, which are the attempts that found out that their task
  cannot be done and said so.

An attempt recorded as not applicable, which `run.py` writes when an arm
cannot take a task's tool roots, is counted beside the scored and faulted
ones and enters no rate.

For a record of the teams family the report adds, from the record's
trajectory and grader result, the measures defined below; they score the
claims docs/evaluation.md states for the family, and this module is where
each measure is defined. The agents of a trajectory are split into workers
and the lead: under foe a worker is an agent running the worker contract
that `contracts/graphs.py` declares, and under Codex every agent below the
root is a worker; every other agent is part of the lead. A task's metadata
names each unit's workspace-relative paths under `units`, as `{name:
[path]}`, and the shared interface's paths under `interface_paths`. The
grader result a record carries is `passed`, `findings`, and `damage`, and
a grade script's only channel into it is its findings, printed one per
line; so a fan-out grader, as `tasks/teams.py` writes it, heads every
finding that judges one unit with `unit <name>` followed by a colon or a
space, and a finding without that head judges the whole change. The report
reads each unit's verdict from the findings: a unit passes when no finding
names it. Every unit's verdict is null when the grade did not judge the
units, which a finding that begins `the grade script` or `cargo is absent`
reports: the script did not run, crashed, ran out of time, or ran without
cargo. Every measure that needs a fact the record lacks is null, and the
per-arm mean states how many attempts it rests on.

- unit pass fraction: the units that passed over the units graded;
- uniformity: the share of units on the majority side of the verdict, so
  1 when every unit passed or every unit failed;
- integration pass: whether the whole grader passed;
- full success: whether the attempt's cell is `correct-completion`;
- makespan: the run's wall seconds;
- tokens per success, per arm: the tokens of every scored attempt over the
  attempts that fully succeeded;
- coordination overhead: the lead's tokens over the run's tokens;
- interface churn: file changes to the interface paths after the first
  worker started;
- rework: the files a worker wrote that a lead agent wrote again at or
  after the worker's change;
- concurrency achieved: the mean number of live workers over the run's
  span, the sum of the workers' live spans divided by the run's span;
- report fidelity: the share of the units the lead reports done that pass.
  The lead's claim is read from the record's candidate: a delegation
  report lists `units` with an `outcome` each, and a unit is reported done
  when its outcome is `completed`; a change report lists `changed_paths`,
  and a unit is reported done when a listed path lies under it;
- defects: a file two agents wrote while both were live, which is a
  later write to the file before the earlier writer had ended; a unit no
  agent wrote, judged over a run that wrote at least one file, since a run
  that wrote nothing declined the task or was cut short as a whole and its
  cell states that; and a unit two distinct workers wrote. A file one
  agent wrote after another had ended is churn or rework and no defect,
  whichever agent made the later write;
- division rate, per arm: the share of attempts on `coherent` controls in
  which a worker ran.

Two arms are compared pairwise only when the pair is declared for the
family, because each declared pair isolates one difference between the
harness configurations `run.py` names:

    autonomy   foe-configured   with foe-ablated        the block tool and the verifiers
               foe-configured   with codex-equivalent   the runtime, under one stated procedure
               foe-ablated      with codex-equivalent   the runtime without them, under the same procedure
               foe-as-shipped   with codex-equivalent   the built-in document against the stated procedure
               codex-equivalent with codex-default      the stated procedure alone
    teams      foe-configured   with foe-undivided      the divide path
               foe-configured   with foe-sequential     concurrency
               foe-configured   with codex-multi        the runtime, under delegation
               codex-single     with codex-multi        delegation alone

Attempts are paired by task and attempt number, and the paired binary
outcome is whether the attempt was actionable. The exact McNemar test gives
the two-sided probability, under equal marginal rates, of a split at least
as uneven as the observed one. The split is between the attempts only the
first arm won and the attempts only the second arm won, and the
probability is the binomial tail on those discordant pairs. The difference
between two rates is given with a 95 percent interval from a cluster
bootstrap over tasks. Tasks are resampled with replacement, every attempt
of a resampled task comes along, and the interval holds the 2.5th and
97.5th percentiles of the resampled differences. Every interval states the
number of tasks it rests on, which is the number of clusters resampled.
Both are computed in the standard library, so the report needs no package.

    report.py DOCUMENT [--resamples N] [--seed N]

`DOCUMENT` is the run document `run.py` ran; the report reads the
document's `out` directory by the runner's own rule and summarizes the
records under `<out>/records`. It is written as `report.json` and
`report.md` under `out`, and the Markdown is printed.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
for directory in (HERE, HERE / "tasks", HERE / "contracts"):
    sys.path.insert(0, str(directory))

import graphs  # noqa: E402
import protocol  # noqa: E402
import run  # noqa: E402

SCHEMA_VERSION = 1
DEFAULT_RESAMPLES = 2000
DEFAULT_SEED = 0

ACTIONABLE_CELLS: tuple[str, ...] = (protocol.CORRECT_COMPLETION, protocol.CORRECT_STOP)

# The pairs of arms compared per family, as (first, second), naming the arms
# of run.py's ARMS; the module docstring states what each pair isolates.
DECLARED_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    "autonomy": (
        ("foe-configured", "foe-ablated"),
        ("foe-configured", "codex-equivalent"),
        ("foe-ablated", "codex-equivalent"),
        ("foe-as-shipped", "codex-equivalent"),
        ("codex-equivalent", "codex-default"),
    ),
    "teams": (
        ("foe-configured", "foe-undivided"),
        ("foe-configured", "foe-sequential"),
        ("foe-configured", "codex-multi"),
        ("codex-single", "codex-multi"),
    ),
}

# The keys a record must carry for the report to read it.
REQUIRED_KEYS: tuple[str, ...] = ("task", "arm", "attempt", "classification", "infrastructure_error", "reported", "totals", "arm_result")
# The key run.py sets on an attempt it recorded without running; a record without it is an attempt that ran.
NOT_APPLICABLE_KEY = "not_applicable"

TEAMS_FAMILY = "teams"
# The class of teams task a team is expected to decline to divide.
COHERENT_CLASS = "coherent"
# The head a fan-out grade script gives a finding that judges one unit: this
# prefix, the unit's name, and a colon or a space; the grade result of a
# record carries the findings and nothing else the script printed.
UNIT_FINDING_PREFIX = "unit "
# The heads of the findings that report that a grade did not judge the
# units: `tasks/protocol.py` and `tasks/feature_removal.py` write the first
# when the script did not run, crashed, or ran out of time, and the fan-out
# grade script writes both when it runs without cargo or off the workspace.
GRADE_NOT_RUN_PREFIXES: tuple[str, ...] = ("the grade script", "cargo is absent")
# The task metadata keys a teams task carries: each unit's workspace-relative paths, and the shared interface's paths.
METADATA_UNITS = "units"
METADATA_INTERFACE_PATHS = "interface_paths"
# The outcome a delegation report gives a unit whose board task completed.
UNIT_COMPLETED = "completed"

Predicate = Callable[[dict[str, Any]], bool]


def load_records(records_dir: Path) -> list[dict[str, Any]]:
    """Every record under the directory, in path order; errors name the file and the missing key."""
    if not records_dir.is_dir():
        raise FileNotFoundError(f"the records directory {records_dir} does not exist; run.py writes it under the document's out")
    records: list[dict[str, Any]] = []
    for path in sorted(records_dir.rglob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: not JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path}: the record is not an object")
        missing = [key for key in REQUIRED_KEYS if key not in record]
        if missing:
            raise ValueError(f"{path}: keys {', '.join(missing)} are absent")
        if not isinstance(record["task"], dict) or "name" not in record["task"] or "correct_statuses" not in record["task"]:
            raise ValueError(f"{path}: task lacks name or correct_statuses")
        family = record["task"].get("family")
        if family not in DECLARED_PAIRS:
            raise ValueError(f"{path}: task.family is {family!r}; the families with declared pairs are {', '.join(DECLARED_PAIRS)}")
        record["_path"] = str(path)
        records.append(record)
    return records


def task_name(record: dict[str, Any]) -> str:
    return str(record["task"]["name"])


def is_scored(record: dict[str, Any]) -> bool:
    return record["classification"] is not None


def is_not_applicable(record: dict[str, Any]) -> bool:
    return record.get(NOT_APPLICABLE_KEY) is not None


def is_actionable(record: dict[str, Any]) -> bool:
    return record["classification"] in ACTIONABLE_CELLS


def is_false_completion(record: dict[str, Any]) -> bool:
    return record["classification"] == protocol.FALSE_COMPLETION


def reported_status(record: dict[str, Any]) -> str | None:
    reported = record.get("reported")
    return str(reported["status"]) if isinstance(reported, dict) and "status" in reported else None


def accepts_blocked(record: dict[str, Any]) -> bool:
    return protocol.BLOCKED in record["task"]["correct_statuses"]


def cost(record: dict[str, Any]) -> tuple[int | None, float | None]:
    """The input plus output tokens and the wall seconds of a run, None where the run measured neither."""
    totals = record.get("totals") or {}
    tokens = None
    if isinstance(totals.get("input_tokens"), int) and isinstance(totals.get("output_tokens"), int):
        tokens = totals["input_tokens"] + totals["output_tokens"]
    seconds = None
    if isinstance(totals.get("wall_ms"), int):
        seconds = totals["wall_ms"] / 1000
    else:
        result = record.get("arm_result") or {}
        if isinstance(result.get("started_ms"), int) and isinstance(result.get("ended_ms"), int):
            seconds = (result["ended_ms"] - result["started_ms"]) / 1000
    return tokens, seconds


def rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {"rate": numerator / denominator if denominator else None, "count": numerator, "of": denominator}


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def cell_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = {cell: 0 for cell in protocol.SEVERITY}
    for record in records:
        if record["classification"] is not None:
            counts[record["classification"]] = counts.get(record["classification"], 0) + 1
    return counts


def is_correct_stop(record: dict[str, Any]) -> bool:
    return record["classification"] == protocol.CORRECT_STOP


def cost_to_stop(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Tokens and seconds over the attempts classified `correct-stop`.

    A false completion, a killed run, or a wrong stop on the same task
    never found out that the task cannot be done, so it enters no mean.
    """
    stopped = [record for record in records if is_correct_stop(record)]
    tokens = [float(value) for value, _ in (cost(record) for record in stopped) if value is not None]
    seconds = [value for _, value in (cost(record) for record in stopped) if value is not None]
    return {
        "attempts": len(stopped),
        "tokens_mean": mean(tokens),
        "tokens_median": median(tokens),
        "tokens_measured": len(tokens),
        "seconds_mean": mean(seconds),
        "seconds_median": median(seconds),
        "seconds_measured": len(seconds),
    }


def arm_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The per-arm rates the module docstring defines, over one arm's records."""
    scored = [record for record in records if is_scored(record)]
    skipped = [record for record in records if is_not_applicable(record)]
    blocked = [record for record in scored if reported_status(record) == protocol.BLOCKED]
    accepting = [record for record in scored if accepts_blocked(record)]
    return {
        "attempts": len(records),
        "scored": len(scored),
        "not_applicable": len(skipped),
        "infrastructure_failures": len(records) - len(scored) - len(skipped),
        "cells": cell_counts(scored),
        "actionable": rate(sum(is_actionable(record) for record in scored), len(scored)),
        "false_completion": rate(sum(is_false_completion(record) for record in scored), len(scored)),
        "block_precision": rate(sum(record["classification"] == protocol.CORRECT_STOP for record in blocked), len(blocked)),
        "block_recall": rate(sum(reported_status(record) == protocol.BLOCKED and record["classification"] == protocol.CORRECT_STOP for record in accepting), len(accepting)),
        "killed": rate(sum(record["classification"] == protocol.KILLED for record in scored), len(scored)),
        "damage": rate(sum(record["classification"] == protocol.DAMAGE for record in scored), len(scored)),
        "cost_to_stop": cost_to_stop(scored),
    }


def workspace_relative(path: str, workspace: str | None) -> str:
    """`path` relative to the workspace when it lies below it; a leading `./` is dropped either way."""
    if workspace:
        prefix = workspace.rstrip("/") + "/"
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path[2:] if path.startswith("./") else path


def lies_under(path: str, entries: list[str]) -> bool:
    """Whether a workspace-relative path equals one of the entries or lies below an entry that names a directory."""
    return any(path == entry or path.startswith(entry.rstrip("/") + "/") for entry in entries)


def _metadata_paths(record: dict[str, Any], key: str) -> list[str] | None:
    """The list of workspace-relative paths the task metadata names under `key`, or None when the key is absent."""
    value = (record["task"].get("metadata") or {}).get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{record.get('_path')}: task.metadata.{key} is {value!r}; expected a list of workspace-relative paths")
    return list(value)


def task_units(record: dict[str, Any]) -> dict[str, list[str]] | None:
    """Each unit's workspace-relative paths from the task metadata, or None when the metadata names no units."""
    value = (record["task"].get("metadata") or {}).get(METADATA_UNITS)
    if value is None:
        return None
    if not isinstance(value, dict) or not all(isinstance(paths, list) and paths and all(isinstance(p, str) and p for p in paths) for paths in value.values()):
        raise ValueError(f"{record.get('_path')}: task.metadata.{METADATA_UNITS} is {value!r}; expected an object of unit name to a non-empty list of workspace-relative paths")
    return {str(name): list(paths) for name, paths in value.items()}


def finding_names_unit(finding: str, unit: str) -> bool:
    """Whether a finding is headed `unit <name>` for this unit, with a colon or a space after the name."""
    head = UNIT_FINDING_PREFIX + unit
    return finding.startswith(head) and (len(finding) == len(head) or finding[len(head)] in ": ")


def grade_units(record: dict[str, Any]) -> dict[str, bool] | None:
    """Each unit's verdict read from the grade findings by the module docstring's rule, or None when the record settles none.

    None when the task names no units, the record carries no grade, or a
    finding reports that the grade did not judge the units.
    """
    grade = record.get("grade")
    units = task_units(record)
    if not isinstance(grade, dict) or units is None:
        return None
    findings = grade.get("findings")
    if not isinstance(findings, list) or not all(isinstance(finding, str) for finding in findings):
        raise ValueError(f"{record.get('_path')}: grade.findings is {findings!r}; expected a list of strings")
    if any(finding.startswith(prefix) for finding in findings for prefix in GRADE_NOT_RUN_PREFIXES):
        return None
    return {name: not any(finding_names_unit(finding, name) for finding in findings) for name in units}


def is_worker(agent: dict[str, Any], harness: str) -> bool:
    """Under foe a worker runs the worker contract graphs.py declares; under Codex every agent below the root is a worker."""
    return agent["role"] == graphs.WORKER if harness == "foe" else agent["depth"] > 0


def agent_tokens(agent: dict[str, Any]) -> int | None:
    """The input plus output tokens of one agent's model calls, None when any call lacks either count."""
    total = 0
    for call in agent.get("model_calls", []):
        if not isinstance(call.get("input_tokens"), int) or not isinstance(call.get("output_tokens"), int):
            return None
        total += call["input_tokens"] + call["output_tokens"]
    return total


def run_span(trajectory_: dict[str, Any]) -> tuple[int, int] | None:
    """The run's start and end: the earliest agent start and the latest agent or outcome end, None when nothing ended."""
    agents = trajectory_.get("agents", [])
    starts = [agent["started_ms"] for agent in agents]
    ends = [agent["ended_ms"] for agent in agents if isinstance(agent.get("ended_ms"), int)]
    outcome_end = (trajectory_.get("outcome") or {}).get("ended_ms")
    if isinstance(outcome_end, int):
        ends.append(outcome_end)
    if not starts or not ends:
        return None
    return min(starts), max(ends)


def reported_done_units(record: dict[str, Any], units: dict[str, list[str]] | None) -> list[str] | None:
    """The units the lead reports done, read from the candidate by the module docstring's rule; None when the candidate states none."""
    candidate = record.get("candidate")
    if not isinstance(candidate, dict):
        return None
    listed = candidate.get("units")
    if isinstance(listed, list) and all(isinstance(item, dict) and "unit" in item and "outcome" in item for item in listed):
        return sorted({str(item["unit"]) for item in listed if item["outcome"] == UNIT_COMPLETED})
    changed = candidate.get("changed_paths")
    if isinstance(changed, list) and units is not None:
        workspace = (record.get("paths") or {}).get("workspace")
        relative = [workspace_relative(path, workspace) for path in changed if isinstance(path, str)]
        return sorted(name for name, paths in units.items() if any(lies_under(path, paths) for path in relative))
    return None


def teams_measures(record: dict[str, Any]) -> dict[str, Any] | None:
    """The per-attempt teams measures the module docstring defines, or None for a record outside the family or without a trajectory."""
    if record["task"].get("family") != TEAMS_FAMILY or not isinstance(record.get("trajectory"), dict):
        return None
    trajectory_ = record["trajectory"]
    harness = str(trajectory_.get("harness"))
    agents = trajectory_.get("agents", [])
    workspace = (record.get("paths") or {}).get("workspace")
    workers = [agent for agent in agents if is_worker(agent, harness)]
    leads = [agent for agent in agents if not is_worker(agent, harness)]
    units = task_units(record)
    interface = _metadata_paths(record, METADATA_INTERFACE_PATHS)
    verdicts = grade_units(record)
    tokens, seconds = cost(record)

    def changes(group: list[dict[str, Any]]) -> list[tuple[str, int, str]]:
        out: list[tuple[str, int, str]] = []
        for agent in group:
            for change in agent.get("file_changes", []):
                relative = workspace_relative(change["path"], workspace)
                # Every path-based measure attributes a change to a unit or the
                # interface by its workspace-relative path; a change the record's
                # workspace does not contain would otherwise count silently as
                # a write to no unit.
                if relative.startswith("/"):
                    raise ValueError(f"{record.get('_path')}: agent {agent['id']} changed {change['path']}, which does not lie under the record's workspace {workspace!r}")
                out.append((relative, change["at_ms"], agent["id"]))
        return out

    worker_changes = changes(workers)
    lead_changes = changes(leads)
    all_changes = worker_changes + lead_changes

    pass_fraction = uniformity = None
    if verdicts:
        passed = sum(verdicts.values())
        pass_fraction = passed / len(verdicts)
        uniformity = max(passed, len(verdicts) - passed) / len(verdicts)

    first_worker = min((worker["started_ms"] for worker in workers), default=None)
    churn = None
    if interface is not None and first_worker is not None:
        churn = sum(1 for path, at, _ in all_changes if lies_under(path, interface) and at > first_worker)

    rework = sorted({path for path, at, _ in worker_changes if any(lead_path == path and lead_at >= at for lead_path, lead_at, _ in lead_changes)})

    span = run_span(trajectory_)
    concurrency = None
    if span is not None and span[1] > span[0]:
        live = sum(min(worker["ended_ms"] if isinstance(worker.get("ended_ms"), int) else span[1], span[1]) - max(worker["started_ms"], span[0]) for worker in workers)
        concurrency = live / (span[1] - span[0])

    lead_tokens = [agent_tokens(agent) for agent in leads]
    overhead = None
    if tokens and None not in lead_tokens:
        overhead = sum(value for value in lead_tokens if value is not None) / tokens

    reported_done = reported_done_units(record, units)
    fidelity = None
    if reported_done is not None and verdicts is not None:
        passing = [unit for unit in reported_done if verdicts.get(unit)]
        fidelity = {"reported_done": reported_done, "passing": passing, **rate(len(passing), len(reported_done))}

    ends = {agent["id"]: agent["ended_ms"] if isinstance(agent.get("ended_ms"), int) else None for agent in agents}
    writes: dict[str, list[tuple[int, str]]] = {}
    for path, at, agent_id in all_changes:
        writes.setdefault(path, []).append((at, agent_id))
    shared_files = sorted(path for path, items in writes.items() if _written_while_both_live(items, ends))
    never_written = written_twice = None
    if units is not None:
        if all_changes:
            never_written = sorted(name for name, paths in units.items() if not any(lies_under(path, paths) for path, _, _ in all_changes))
        written_twice = sorted(name for name, paths in units.items() if len({agent_id for path, _, agent_id in worker_changes if lies_under(path, paths)}) > 1)
    defects = {
        "files_written_by_two_agents": shared_files,
        "units_never_written": never_written,
        "units_written_twice": written_twice,
        "count": len(shared_files) + len(never_written or []) + len(written_twice or []),
    }
    return {
        "workers": len(workers),
        "divided": bool(workers),
        "units": None if verdicts is None else {"graded": len(verdicts), "passed": sum(verdicts.values())},
        "unit_pass_fraction": pass_fraction,
        "uniformity": uniformity,
        "integration_pass": record["grade"]["passed"] if isinstance(record.get("grade"), dict) else None,
        "full_success": record["classification"] == protocol.CORRECT_COMPLETION,
        "makespan_seconds": seconds,
        "tokens": tokens,
        "coordination_overhead": overhead,
        "interface_churn": churn,
        "rework": rework,
        "concurrency": concurrency,
        "report_fidelity": fidelity,
        "defects": defects,
    }


def _written_while_both_live(writes: list[tuple[int, str]], ends: dict[str, int | None]) -> bool:
    """Whether two writes by distinct agents fall while both writers are live.

    The writes are one file's, each an instant and the writing agent. The
    later of two writes falls while the earlier writer is live when it
    precedes that writer's end; a writer without an end is live throughout.
    The test is the same whichever agent made the later write, so a lead
    that spans the run and a node that starts after a worker are measured
    alike.
    """
    ordered = sorted(writes)
    for index, (earlier_at, earlier_agent) in enumerate(ordered):
        earlier_end = ends[earlier_agent]
        for later_at, later_agent in ordered[index + 1 :]:
            if later_agent != earlier_agent and (earlier_end is None or later_at < earlier_end):
                return True
    return False


def _mean_measured(values: list[float | None]) -> dict[str, Any]:
    present = [float(value) for value in values if value is not None]
    return {"mean": mean(present), "measured": len(present)}


def teams_metrics(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The per-arm teams measures over one arm's scored teams attempts, or None when the arm has none."""
    scored = [record for record in records if is_scored(record) and record["task"].get("family") == TEAMS_FAMILY]
    if not scored:
        return None
    measured = [(record, teams_measures(record)) for record in scored]
    measures = [measure for _, measure in measured if measure is not None]
    successes = [record for record, measure in measured if measure is not None and measure["full_success"]]
    tokens = [measure["tokens"] for measure in measures if measure["tokens"] is not None]
    coherent = [measure for record, measure in measured if measure is not None and record["task"].get("class_name") == COHERENT_CLASS]
    return {
        "attempts": len(scored),
        "measured": len(measures),
        "full_success": rate(len(successes), len(measures)),
        "integration_pass": rate(sum(measure["integration_pass"] is True for measure in measures), len(measures)),
        "unit_pass_fraction": _mean_measured([measure["unit_pass_fraction"] for measure in measures]),
        "uniformity": _mean_measured([measure["uniformity"] for measure in measures]),
        "makespan_seconds": _mean_measured([measure["makespan_seconds"] for measure in measures]),
        "tokens_per_success": {"value": sum(tokens) / len(successes) if successes and tokens else None, "tokens_measured": len(tokens), "successes": len(successes)},
        "coordination_overhead": _mean_measured([measure["coordination_overhead"] for measure in measures]),
        "interface_churn": _mean_measured([measure["interface_churn"] for measure in measures]),
        "rework": _mean_measured([len(measure["rework"]) for measure in measures]),
        "concurrency": _mean_measured([measure["concurrency"] for measure in measures]),
        "report_fidelity": _mean_measured([measure["report_fidelity"]["rate"] if measure["report_fidelity"] else None for measure in measures]),
        "defects": rate(sum(measure["defects"]["count"] > 0 for measure in measures), len(measures)),
        "defects_mean": _mean_measured([measure["defects"]["count"] for measure in measures]),
        "division_on_coherent": rate(sum(measure["divided"] for measure in coherent), len(coherent)),
    }


def per_task(records: list[dict[str, Any]], arms: list[str]) -> dict[str, dict[str, Any]]:
    """For each task, the class and, per arm, the cells its attempts fell into with their mean cost."""
    tasks: dict[str, dict[str, Any]] = {}
    for record in records:
        name = task_name(record)
        entry = tasks.setdefault(name, {"class_name": record["task"].get("class_name"), "family": record["task"].get("family"), "arms": {}})
        entry["arms"].setdefault(record["arm"], []).append(record)
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(tasks):
        entry = tasks[name]
        by_arm: dict[str, Any] = {}
        for arm in arms:
            group = entry["arms"].get(arm, [])
            if not group:
                continue
            scored = [record for record in group if is_scored(record)]
            tokens = [float(value) for value, _ in (cost(record) for record in scored) if value is not None]
            seconds = [value for _, value in (cost(record) for record in scored) if value is not None]
            by_arm[arm] = {
                "attempts": len(group),
                "scored": len(scored),
                "cells": {cell: count for cell, count in cell_counts(scored).items() if count},
                "actionable": rate(sum(is_actionable(record) for record in scored), len(scored)),
                "tokens_mean": mean(tokens),
                "seconds_mean": mean(seconds),
            }
        out[name] = {"class_name": entry["class_name"], "family": entry["family"], "arms": by_arm}
    return out


def mcnemar_exact(only_first: int, only_second: int) -> float:
    """The two-sided exact McNemar probability for the discordant pair counts.

    Under the hypothesis of equal rates each discordant pair falls either
    way with probability one half, so the count on one side is binomial.
    The probability is twice the smaller tail, capped at one, and one when
    there is no discordant pair.
    """
    if only_first < 0 or only_second < 0:
        raise ValueError(f"discordant counts are {only_first} and {only_second}; both must be non-negative")
    total = only_first + only_second
    if total == 0:
        return 1.0
    smaller = min(only_first, only_second)
    tail = sum(math.comb(total, k) for k in range(smaller + 1)) / 2**total
    return min(1.0, 2 * tail)


def paired(records_first: list[dict[str, Any]], records_second: list[dict[str, Any]]) -> dict[str, list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Scored attempts of two arms paired by task and attempt number, grouped by task."""
    first = {(task_name(record), record["attempt"]): record for record in records_first if is_scored(record)}
    second = {(task_name(record), record["attempt"]): record for record in records_second if is_scored(record)}
    clusters: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for key in sorted(first.keys() & second.keys(), key=lambda item: (item[0], item[1])):
        clusters.setdefault(key[0], []).append((first[key], second[key]))
    return clusters


def discordance(clusters: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]], predicate: Predicate) -> dict[str, int]:
    counts = {"both": 0, "only_first": 0, "only_second": 0, "neither": 0}
    for pairs in clusters.values():
        for first, second in pairs:
            a, b = predicate(first), predicate(second)
            counts["both" if a and b else "only_first" if a else "only_second" if b else "neither"] += 1
    return counts


def cluster_bootstrap(clusters: dict[str, list[tuple[float, float]]], resamples: int, seed: int) -> dict[str, Any]:
    """The observed difference of means, first minus second, and its 95 percent percentile interval over resampled clusters.

    Each cluster is a task with the values of every paired attempt. A
    resample draws as many clusters as there are, with replacement, and
    pools every attempt of the drawn clusters. The generator is seeded, so
    one seed gives one interval.
    """
    if resamples < 1:
        raise ValueError(f"resamples is {resamples}; at least 1 is required")
    names = sorted(clusters)
    if not names:
        return {"observed": None, "lower": None, "upper": None, "clusters": 0, "pairs": 0, "resamples": resamples, "seed": seed}

    def difference(chosen: list[str]) -> float:
        firsts = [first for name in chosen for first, _ in clusters[name]]
        seconds = [second for name in chosen for _, second in clusters[name]]
        return sum(firsts) / len(firsts) - sum(seconds) / len(seconds)

    generator = random.Random(seed)
    draws = sorted(difference([generator.choice(names) for _ in names]) for _ in range(resamples))
    lower = draws[round(0.025 * (resamples - 1))]
    upper = draws[round(0.975 * (resamples - 1))]
    return {
        "observed": difference(names),
        "lower": lower,
        "upper": upper,
        "clusters": len(names),
        "pairs": sum(len(pairs) for pairs in clusters.values()),
        "resamples": resamples,
        "seed": seed,
    }


def compare(records_first: list[dict[str, Any]], records_second: list[dict[str, Any]], resamples: int, seed: int) -> dict[str, Any]:
    """One paired comparison of two arms: the McNemar test on actionable outcomes and bootstrap intervals on two rate differences."""
    clusters = paired(records_first, records_second)
    counts = discordance(clusters, is_actionable)
    values = {
        name: {task: [(float(predicate(first)), float(predicate(second))) for first, second in pairs] for task, pairs in clusters.items()}
        for name, predicate in (("actionable", is_actionable), ("false_completion", is_false_completion))
    }
    return {
        "pairs": sum(len(pairs) for pairs in clusters.values()),
        "tasks": sorted(clusters),
        "task_count": len(clusters),
        "actionable_pairs": counts,
        "mcnemar_p": mcnemar_exact(counts["only_first"], counts["only_second"]) if clusters else None,
        "actionable_difference": cluster_bootstrap(values["actionable"], resamples, seed),
        "false_completion_difference": cluster_bootstrap(values["false_completion"], resamples, seed),
    }


def build(records: list[dict[str, Any]], resamples: int = DEFAULT_RESAMPLES, seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """The whole report as one JSON document."""
    arms: list[str] = []
    for record in records:
        if record["arm"] not in arms:
            arms.append(record["arm"])
    by_arm = {arm: [record for record in records if record["arm"] == arm] for arm in arms}
    families: list[str] = []
    for record in records:
        if record["task"]["family"] not in families:
            families.append(record["task"]["family"])
    pairs = []
    not_formed = []
    for family in families:
        family_records = {arm: [record for record in by_arm[arm] if record["task"]["family"] == family] for arm in arms}
        for first, second in DECLARED_PAIRS[family]:
            comparison = compare(family_records.get(first, []), family_records.get(second, []), resamples, seed)
            if comparison["pairs"]:
                pairs.append({"family": family, "first": first, "second": second, **comparison})
            else:
                absent = [arm for arm in (first, second) if not any(is_scored(record) for record in family_records.get(arm, []))]
                reason = f"{', '.join(absent)} has no scored attempt" if absent else "the two arms share no scored attempt on one task"
                not_formed.append({"family": family, "first": first, "second": second, "reason": reason})
    return {
        "schema_version": SCHEMA_VERSION,
        "records": len(records),
        "arms": arms,
        "families": families,
        "tasks": sorted({task_name(record) for record in records}),
        "per_arm": {arm: {**arm_metrics(by_arm[arm]), "teams": teams_metrics(by_arm[arm])} for arm in arms},
        "per_task": per_task(records, arms),
        "teams_attempts": [
            {"task": task_name(record), "arm": record["arm"], "attempt": record["attempt"], "classification": record["classification"], **measure}
            for record in records
            for measure in (teams_measures(record),)
            if is_scored(record) and measure is not None
        ],
        "pairs": pairs,
        "pairs_not_formed": not_formed,
        "settings": {"resamples": resamples, "seed": seed},
    }


def _rate(value: dict[str, Any]) -> str:
    return "—" if value["rate"] is None else f"{value['rate']:.2f} ({value['count']}/{value['of']})"


def _number(value: float | None, digits: int = 0) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _interval(value: dict[str, Any]) -> str:
    if value["observed"] is None:
        return "—"
    return f"{value['observed']:+.2f} [{value['lower']:+.2f}, {value['upper']:+.2f}]"


def _mean(value: dict[str, Any], digits: int = 2) -> str:
    return "—" if value["mean"] is None else f"{value['mean']:.{digits}f} (n={value['measured']})"


def teams_markdown(report: dict[str, Any]) -> list[str]:
    """The teams section: one row per arm with the family's measures, then one row per scored attempt; empty without a teams record."""
    arms = {arm: metrics["teams"] for arm, metrics in report["per_arm"].items() if metrics.get("teams") is not None}
    if not arms:
        return []
    lines = [
        "",
        "## Teams",
        "",
        "| arm | attempts | full success | integration pass | unit pass | uniformity | makespan s | tokens per success | coordination | interface churn | rework | concurrency | fidelity | defects | divided coherent |",
        "|---|---:|---|---|---|---|---|---:|---|---|---|---|---|---|---|",
    ]
    for arm, teams in arms.items():
        per_success = teams["tokens_per_success"]
        lines.append(
            f"| `{arm}` | {teams['attempts']} | {_rate(teams['full_success'])} | {_rate(teams['integration_pass'])} | {_mean(teams['unit_pass_fraction'])} | "
            f"{_mean(teams['uniformity'])} | {_mean(teams['makespan_seconds'], 1)} | {_number(per_success['value'])} | {_mean(teams['coordination_overhead'])} | "
            f"{_mean(teams['interface_churn'])} | {_mean(teams['rework'])} | {_mean(teams['concurrency'])} | {_mean(teams['report_fidelity'])} | "
            f"{_rate(teams['defects'])} | {_rate(teams['division_on_coherent'])} |"
        )
    lines.extend(
        [
            "",
            "Means state in parentheses the attempts they rest on; tokens per success are the tokens of every scored attempt over the attempts that fully succeeded.",
            "",
            "| task | arm | attempt | cell | workers | unit pass | churn | rework | concurrency | fidelity | defects |",
            "|---|---|---:|---|---:|---|---|---:|---|---|---:|",
        ]
    )
    for measure in report["teams_attempts"]:
        fidelity = measure["report_fidelity"]
        lines.append(
            f"| `{measure['task']}` | `{measure['arm']}` | {measure['attempt']} | {measure['classification']} | {measure['workers']} | "
            f"{_number(measure['unit_pass_fraction'], 2)} | {_number(measure['interface_churn'])} | {len(measure['rework'])} | {_number(measure['concurrency'], 2)} | "
            f"{'—' if fidelity is None else _rate(fidelity)} | {measure['defects']['count']} |"
        )
    return lines


def markdown(report: dict[str, Any]) -> str:
    """The report as Markdown tables: one row per arm, one per task and arm, one per pair."""
    lines = [
        "## Arms",
        "",
        "| arm | attempts | scored | faults | not applicable | actionable | false completion | block precision | block recall | killed | damage | tokens to stop | seconds to stop |",
        "|---|---:|---:|---:|---:|---|---|---|---|---|---|---:|---:|",
    ]
    for arm, metrics in report["per_arm"].items():
        stop = metrics["cost_to_stop"]
        lines.append(
            f"| `{arm}` | {metrics['attempts']} | {metrics['scored']} | {metrics['infrastructure_failures']} | {metrics['not_applicable']} | {_rate(metrics['actionable'])} | "
            f"{_rate(metrics['false_completion'])} | {_rate(metrics['block_precision'])} | {_rate(metrics['block_recall'])} | {_rate(metrics['killed'])} | "
            f"{_rate(metrics['damage'])} | {_number(stop['tokens_mean'])} | {_number(stop['seconds_mean'], 1)} |"
        )
    lines.extend(["", "## Tasks", "", "| task | class | arm | attempts | cells | actionable | mean tokens | mean seconds |", "|---|---|---|---:|---|---|---:|---:|"])
    for task, entry in report["per_task"].items():
        for arm, metrics in entry["arms"].items():
            cells = ", ".join(f"{cell} ×{count}" for cell, count in metrics["cells"].items()) or "—"
            lines.append(f"| `{task}` | {entry['class_name']} | `{arm}` | {metrics['attempts']} | {cells} | {_rate(metrics['actionable'])} | {_number(metrics['tokens_mean'])} | {_number(metrics['seconds_mean'], 1)} |")
    lines.extend(teams_markdown(report))
    lines.extend(["", "## Paired comparisons", ""])
    if not report["pairs"]:
        lines.append("No declared pair of arms shares a scored attempt on one task.")
    else:
        lines.extend(
            [
                "| first | second | tasks | pairs | both | only first | only second | neither | McNemar p | actionable difference | false-completion difference |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
            ]
        )
        for pair in report["pairs"]:
            counts = pair["actionable_pairs"]
            lines.append(
                f"| `{pair['first']}` | `{pair['second']}` | {pair['task_count']} | {pair['pairs']} | {counts['both']} | {counts['only_first']} | {counts['only_second']} | {counts['neither']} | "
                f"{pair['mcnemar_p']:.4f} | {_interval(pair['actionable_difference'])} | {_interval(pair['false_completion_difference'])} |"
            )
        lines.extend(
            [
                "",
                f"Differences are first minus second, with 95 percent intervals from {report['settings']['resamples']} cluster-bootstrap resamples over tasks (seed {report['settings']['seed']}). "
                "Each interval rests on the tasks its row counts: those are the clusters the bootstrap resamples.",
            ]
        )
    if report["pairs_not_formed"]:
        lines.extend(["", "Declared pairs without a comparison:", ""])
        for pair in report["pairs_not_formed"]:
            lines.append(f"- `{pair['first']}` with `{pair['second']}` ({pair['family']}): {pair['reason']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("document", type=Path, help="the run document run.py ran; the report reads its out directory")
    parser.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES, help=f"bootstrap resamples; default {DEFAULT_RESAMPLES}")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"the bootstrap generator's seed; default {DEFAULT_SEED}")
    args = parser.parse_args(argv)
    try:
        out = run.document_out(run.read_document(args.document))
        records_dir = out / run.RECORDS_DIR
        records = load_records(records_dir)
        if not records:
            raise ValueError(f"no record under {records_dir}")
        report = build(records, args.resamples, args.seed)
    except (ValueError, FileNotFoundError) as exc:
        print(f"cross harness report: {exc}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rendered = markdown(report)
    (out / "report.md").write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    print(f"\nreport: {out / 'report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
