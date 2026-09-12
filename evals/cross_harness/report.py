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
- the cost to stop: the input tokens and seconds of the attempts
  classified `correct-stop`, which are the attempts that found out that
  their task cannot be done and said so. Input tokens carry both stop
  costs, for the reason the paragraph on output tokens below states.

An attempt recorded as not applicable, which `run.py` writes when an arm
cannot take a task's tool roots, is counted beside the scored and faulted
ones and enters no rate.

Every rate above also has a per-class form, keyed by the task's
`class_name`. One rate over every class mixes finishing a solvable task
with stopping on a task that cannot be done. Both are actionable and both
are what the family asks of a harness, so a single number over the two
leaves the reader unable to say which of the two a harness did well. The
per-class block carries the cost of
its class as well: input tokens, output tokens, cache-read tokens, model
calls, and wall seconds, each as a mean and a median over the attempts that
measured it.

Input tokens are the primary cost measure. Output-token accounting is not
yet comparable across the two harnesses, because foe records no separate
reasoning count while Codex reports `reasoning_output_tokens` within its
output count, so the two output totals count different things. The report
states both and reads spend from input tokens until that difference is
settled.

The cost of failing to stop is reported beside the cost to stop: the same
measures over the scored attempts on tasks that admit no completion whose
cell is not `correct-stop`. Those attempts are the counterfactual the cost
to stop needs, because an arm that stops cheaply on the tasks it recognizes
and spends its ceiling on the rest has not stopped cheaply. The censoring
rate beside the two is the share of the attempts on those tasks which a
ceiling or a kill ended rather than a stop of the arm's own: the attempts
the arm reported exhausted or killed, and the attempts whose trajectory
ended exhausted. An attempt classified `correct-stop` stopped on its own
and is never censored.

Ceiling utilization states each scored attempt's totals against the
ceilings its own record carries, for model calls, input tokens, output
tokens, and seconds, as a mean and a maximum per arm with the count of
attempts that reached each ceiling. For an attempt that ended exhausted,
the trajectory outcome's code names the limit that bound it, and the report
counts those codes per arm. Codex takes token and wall-clock limits alone,
so the model-call column is a bound for a foe arm and a measurement for a
Codex arm. An attempt counts as at a ceiling only on the keys its own
harness enforces, so a Codex attempt past the task's model-call number
crossed nothing and enters no such count.

The blocked-code table lists every scored attempt that reported `blocked`
with the code it stated and the codes its task accepts, so a stop for a
reason the task does not admit is visible beside a stop for one it does. A
code is accepted when the task admits `blocked` and either names no code or
names this one.

The mechanism columns come from the trajectory's tool-call stream, and a
record written before that stream existed reads as a record with no tool
call rather than as an error. Under foe a verifier fires in two ways: the
model calls the `check` tool the document declares, and the runtime invokes
the verifier the contract's `done_when` names, which the log records as a
`verification/result` event. A document whose completion gate runs the
verifier fires it without any call of the tool, so both count as firings. A
firing cleared when its result reported no finding, which the summary
states as the count before the word `finding`; the exit status settles
nothing on its own, because docs/config.md has a verifier print its
findings and exit zero all the same. A Codex arm declares no verifier tool
and runs the checks as a shell command, so a firing is one command whose
program is the task's check suite or an interpreter of it, and it cleared
when the command exited zero. A command that reads the suite, such as one
that prints it, fires nothing. The two columns come from two different
sources and the table names the source of each row. The `block` tool is a
foe tool, so the block column measures foe arms, and the code a Codex arm
stated for a blocked run is in the blocked-code table instead. The block
column states the code itself, as the blocked-code table does, so one stop
reads as one string in both tables. The compaction count comes from the
trajectory totals, or from the agents themselves when the totals carry
none.

Three further columns say whether the parts of one record agree. Outcome
agreement aggregates `outcomes.agree`, which is whether the arm's
self-report matches the outcome its own logs carry. Trace conformance is
`conformance.valid`, which `normalize_foe.py` computes and only a foe arm
records. Both rates are taken over the scored attempts the row counts, so
a faulted attempt enters neither. Faults and attempts recorded as not
applicable are listed per arm with the reason each record states and the
attempt it came from, so the strings behind those two counts are read. A
reason is text a run wrote, and the table holds it in one cell whatever it
carries.

The autonomy family gets a per-attempt table, as the teams family does: one
row per scored attempt with its class, cell, reported status and code,
cost, highest ceiling use, the limit that bound it, and its mechanism
columns.

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

Three statements keep those numbers honest. An interval is marked
degenerate when it rests on one task, so that every resample draws the same
cluster, or when every pair has the same difference, so that every resample
gives that difference; such an interval states the width of the data and no
sampling width. A McNemar probability of one has two causes and the report
separates them: a comparison with no discordant pair tested nothing, and a
comparison whose discordant pairs split evenly tested the null and did not
reject it. The report also states once, from the comparison with the fewest
paired attempts, the smallest difference in the actionable rate this run
could call significant. The exact test reaches the level only once a
comparison has at least a fixed number of discordant pairs, which the
report computes from the level itself; that many pairs out of the paired
attempts is the smallest difference the test could call significant, and a
comparison with fewer paired attempts than that can call none.

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
import re
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
for directory in (HERE, HERE / "tasks", HERE / "contracts"):
    sys.path.insert(0, str(directory))

import codex_budget_watcher  # noqa: E402
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

AUTONOMY_FAMILY = "autonomy"
# The totals a cost measure reads, beside the wall seconds `cost` gives.
COST_TOTAL_KEYS: tuple[str, ...] = ("input_tokens", "output_tokens", "cache_read_tokens", "model_calls")
# The ceilings an attempt's record carries, and the one of them measured in seconds.
CEILING_KEYS: tuple[str, ...] = protocol.BUDGET_KEYS
SECONDS_KEY = "seconds"
# The limit the ceiling table names for an exhausted attempt whose records state none.
UNSTATED_LIMIT = "unstated"
# The two harnesses, named as `run.py` and the trajectory schema write them.
FOE_HARNESS = "foe"
CODEX_HARNESS = "codex"
# The ceiling keys each harness's runtime holds an attempt to. foe holds
# every budget key; a Codex arm is held to the dimensions
# `codex_budget_watcher.py` watches, so its model-call figure is measured
# against the task's number and nothing bounds it there.
ENFORCED_CEILINGS: dict[str, tuple[str, ...]] = {FOE_HARNESS: CEILING_KEYS, CODEX_HARNESS: tuple(codex_budget_watcher.DIMENSIONS)}
# The tool a foe document declares as its verifier, and the tool that stops a
# run with a stated code; `contracts/graphs.py` declares both.
VERIFIER_TOOL = graphs.CHECK
# The names one verifier firing reaches the trajectory under. The runtime
# invokes the verifier at the contract's completion gate without any call of
# the tool, and that invocation is a firing as much as a call the model
# issued: it reaches the record under the verifier tool the event names, and
# under the event's own name in a record that kept it. Both are counted, so
# that no firing of the gate is dropped.
VERIFICATION_EVENT = "verification/result"
VERIFIER_NAMES: tuple[str, ...] = (graphs.CHECK, VERIFICATION_EVENT)
BLOCK_TOOL = "block"
# A block call's summary is the code alone, which is the string the
# blocked-code table states. A summary that leads with the word `code`
# states the code after it, and the head is dropped so that the two tables
# name one stop by one string.
BLOCK_SUMMARY_PREFIX = "code "
# Where one arm's verifier evidence comes from: a tool call under foe and a
# shell command under Codex, which is why the column names its source.
VERIFIER_FROM_TOOL, VERIFIER_FROM_COMMAND = "tool", "command"
# The text that names a workspace check suite inside a shell command, for an
# arm that runs the checks as a command. `run.py` builds the check command
# from the task metadata, the workspace check suite, or unit-test discovery,
# and a task whose metadata names its own command is matched by that command.
CHECK_COMMAND_MARKERS: tuple[str, ...] = (run.CHECK_SUITE, f"unittest discover -s {run.TESTS_DIR}")
# What separates one command from the next inside one recorded shell
# invocation, so that each part is judged by its own leading word.
_COMMAND_SEPARATORS = re.compile(r"&&|\|\||;|\||\n")
# The leading words of a command that runs what follows rather than reading
# it: the shells, the interpreters, and the wrappers that hand their
# remaining arguments to another program. A part led by any other word, such
# as `cat` or `grep`, names the check suite and runs nothing.
COMMAND_WRAPPERS: frozenset[str] = frozenset({"bash", "sh", "dash", "ksh", "zsh", "env", "exec", "nohup", "time", "timeout", "stdbuf", "python", "python3"})
# The count of findings a verifier result's summary carries, which is the
# integer the word `finding` follows. A summary also states the exit status
# or the verdict, either of which can carry an integer of its own, so the
# word is what identifies the count.
_SUMMARY_FINDINGS = re.compile(r"(\d+)\s+findings?\b")
# The two-sided probability at or below which a difference is called
# significant, and the bound on the search for the discordant pairs it needs.
SIGNIFICANCE_LEVEL = 0.05
MAX_DISCORDANT_SEARCH = 64

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


def accepts_completion(record: dict[str, Any]) -> bool:
    """Whether the task admits `completed`; a task that admits none can only be stopped."""
    return protocol.COMPLETED in record["task"]["correct_statuses"]


def class_name(record: dict[str, Any]) -> str | None:
    value = record["task"].get("class_name")
    return None if value is None else str(value)


def harness_of(record: dict[str, Any]) -> str | None:
    """The harness the record names, from the record itself or from its trajectory, or None when neither states one."""
    harness = record.get("harness")
    if isinstance(harness, str):
        return harness
    trajectory_ = record.get("trajectory")
    value = trajectory_.get("harness") if isinstance(trajectory_, dict) else None
    return value if isinstance(value, str) else None


def _integer(value: Any) -> int | None:
    """`value` when it is an integer and not a boolean, and None otherwise."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def trajectory_outcome(record: dict[str, Any]) -> dict[str, Any]:
    """The outcome the record's trajectory carries, or an empty object when the record carries no trajectory."""
    trajectory_ = record.get("trajectory")
    outcome = trajectory_.get("outcome") if isinstance(trajectory_, dict) else None
    return outcome if isinstance(outcome, dict) else {}


def ended_exhausted(record: dict[str, Any]) -> bool:
    """Whether a ceiling ended the attempt, by the arm's own report or by the outcome its logs carry."""
    return reported_status(record) == protocol.EXHAUSTED or trajectory_outcome(record).get("status") == protocol.EXHAUSTED


def ended_killed(record: dict[str, Any]) -> bool:
    """Whether the attempt was ended by force, by the arm's report, by its cell, or by the outcome its logs carry."""
    return reported_status(record) == protocol.KILLED or record["classification"] == protocol.KILLED or trajectory_outcome(record).get("status") == protocol.KILLED


def _distribution(values: list[float]) -> dict[str, Any]:
    return {"mean": mean(values), "median": median(values), "measured": len(values)}


def cost_measures(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Every cost of a group of attempts, each over the attempts that measured it.

    Input tokens are the primary measure, for the reason the module
    docstring states. `measured` says how many attempts carried the total a
    measure needs, so a mean over part of the group is never read as a mean
    over the group.
    """
    measures: dict[str, Any] = {"attempts": len(records)}
    for key in COST_TOTAL_KEYS:
        values = [float(value) for value in (_integer((record.get("totals") or {}).get(key)) for record in records) if value is not None]
        measures[key] = _distribution(values)
    measures[SECONDS_KEY] = _distribution([value for _, value in (cost(record) for record in records) if value is not None])
    return measures


def _cost_block(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The tokens and seconds of a group of attempts, with the full cost measures beside them."""
    tokens = [float(value) for value, _ in (cost(record) for record in records) if value is not None]
    seconds = [value for _, value in (cost(record) for record in records) if value is not None]
    return {
        "attempts": len(records),
        "tokens_mean": mean(tokens),
        "tokens_median": median(tokens),
        "tokens_measured": len(tokens),
        "seconds_mean": mean(seconds),
        "seconds_median": median(seconds),
        "seconds_measured": len(seconds),
        "cost": cost_measures(records),
    }


def cost_to_stop(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Tokens and seconds over the attempts classified `correct-stop`.

    A false completion, a killed run, or a wrong stop on the same task
    never found out that the task cannot be done, so it enters no mean.
    """
    return _cost_block([record for record in records if is_correct_stop(record)])


def cost_without_stop(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Tokens and seconds over the scored attempts on tasks that admit no completion which did not stop correctly.

    This is the counterfactual the cost to stop needs: an arm that stops
    cheaply on the tasks it recognizes and spends its ceiling on the rest
    has not stopped cheaply, and only the two figures side by side say which
    of the two happened.
    """
    return _cost_block([record for record in records if is_scored(record) and not accepts_completion(record) and not is_correct_stop(record)])


def censoring(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The share of the scored attempts on tasks that admit no completion which a ceiling or a kill ended.

    A censored attempt never reached a stop of its own, so the cost of
    stopping is unknown for it and the two cost figures beside this rate are
    read against it. An attempt classified `correct-stop` stopped on its
    own and is never censored, whatever its logs say afterwards.
    """
    population = [record for record in records if is_scored(record) and not accepts_completion(record)]
    unstopped = [record for record in population if not is_correct_stop(record)]
    exhausted = [record for record in unstopped if ended_exhausted(record)]
    killed = [record for record in unstopped if ended_killed(record) and not ended_exhausted(record)]
    return {**rate(len(exhausted) + len(killed), len(population)), "exhausted": len(exhausted), "killed": len(killed)}


def ceiling_use(record: dict[str, Any]) -> dict[str, float | None]:
    """Each ceiling key's total over the ceiling the record carries, None where the record states no ceiling or measured no total."""
    budget = record.get("budget") or {}
    totals = record.get("totals") or {}
    _, seconds = cost(record)
    used: dict[str, float | None] = {}
    for key in CEILING_KEYS:
        limit = _integer(budget.get(key))
        spent = seconds if key == SECONDS_KEY else _integer(totals.get(key))
        used[key] = None if limit is None or limit <= 0 or spent is None else spent / limit
    return used


def enforced_ceilings(record: dict[str, Any]) -> tuple[str, ...]:
    """The ceiling keys the record's harness holds the attempt to; every key when the record names no harness."""
    return ENFORCED_CEILINGS.get(harness_of(record) or "", CEILING_KEYS)


def ceilings(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Every scored attempt's totals against its own ceilings, and the limit that bound each exhausted attempt.

    A key is measured for every attempt that carries the total and the
    number, and an attempt reaches a ceiling only on the keys its own
    harness enforces, so a figure past a number nothing enforces is a
    measurement and no bound. `enforced` states how many of the measured
    attempts the key bound.
    """
    scored = [record for record in records if is_scored(record)]
    uses = [ceiling_use(record) for record in scored]
    held = [enforced_ceilings(record) for record in scored]
    utilization: dict[str, Any] = {}
    for key in CEILING_KEYS:
        values = [use[key] for use in uses if use[key] is not None]
        bound = [use[key] for use, keys in zip(uses, held) if use[key] is not None and key in keys]
        utilization[key] = {"mean": mean(values), "max": max(values) if values else None, "measured": len(values), "enforced": len(bound), "at_ceiling": sum(1 for value in bound if value >= 1)}
    bound_by: dict[str, int] = {}
    exhausted = 0
    for record in scored:
        if not ended_exhausted(record):
            continue
        exhausted += 1
        limit = trajectory_outcome(record).get("code") or (record.get("reported") or {}).get("code") or UNSTATED_LIMIT
        bound_by[str(limit)] = bound_by.get(str(limit), 0) + 1
    return {
        "scored": len(scored),
        "utilization": utilization,
        "at_ceiling": rate(sum(1 for use, keys in zip(uses, held) if any(use[key] is not None and use[key] >= 1 for key in keys)), len(scored)),
        "exhausted": exhausted,
        "bound_by": dict(sorted(bound_by.items())),
    }


def blocked_codes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every scored attempt that reported `blocked`, with the code it stated against the codes its task accepts.

    A code is accepted when the task admits `blocked` and either names no
    code or names this one, which is the rule `tasks/protocol.py` classifies
    by; a stop for a reason the task does not admit is a wrong stop and is
    listed here with the reason it gave.
    """
    listed: list[dict[str, Any]] = []
    for record in records:
        if not is_scored(record) or reported_status(record) != protocol.BLOCKED:
            continue
        accepted_codes = sorted(str(code) for code in record["task"].get("correct_codes") or [])
        code = (record.get("reported") or {}).get("code")
        code = None if code is None else str(code)
        listed.append(
            {
                "task": task_name(record),
                "arm": record["arm"],
                "attempt": record["attempt"],
                "class_name": class_name(record),
                "code": code,
                "correct_codes": accepted_codes,
                "task_accepts_blocked": accepts_blocked(record),
                "accepted": accepts_blocked(record) and (not accepted_codes or code in accepted_codes),
                "classification": record["classification"],
            }
        )
    return listed


def tool_calls(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Every tool call the record's trajectory holds; a record written before the stream existed holds none."""
    trajectory_ = record.get("trajectory")
    if not isinstance(trajectory_, dict):
        return []
    return [call for agent in trajectory_.get("agents", []) for call in agent.get("tool_calls", []) if isinstance(call, dict)]


def shell_commands(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Every shell command the record's trajectory holds."""
    trajectory_ = record.get("trajectory")
    if not isinstance(trajectory_, dict):
        return []
    return [command for agent in trajectory_.get("agents", []) for command in agent.get("commands", []) if isinstance(command, dict)]


def check_markers(record: dict[str, Any]) -> list[str]:
    """The text that names one task's check suite inside a shell command."""
    named = (record["task"].get("metadata") or {}).get(run.METADATA_CHECK)
    if isinstance(named, str) and named.strip():
        return [named.strip()]
    return list(CHECK_COMMAND_MARKERS)


def _findings_cleared(summary: Any, is_error: bool) -> bool:
    """Whether one verifier result reported no finding.

    A verifier result's summary states the count of findings before the
    word `finding`, beside the exit status or the verdict, so the word
    identifies the count. docs/config.md has a verifier report its findings
    on standard output and accept by printing none, so such a verifier exits
    zero whether or not it found something, which leaves its exit status
    silent on the question. A summary that states no count leaves the error
    flag as the only evidence.
    """
    if isinstance(summary, str):
        found = _SUMMARY_FINDINGS.search(summary)
        if found is not None:
            return int(found.group(1)) == 0
    return not is_error


def _runs_check_suite(text: str, markers: list[str]) -> bool:
    """Whether one recorded shell invocation runs a check suite rather than naming one.

    The invocation is split at the shell's separators, and one part runs the
    suite when a marker stands where the program stands: at the head of the
    part, after a wrapper such as `bash` or `python3` with its options, or
    as the path the part invokes directly. A part led by any other word,
    such as `cat checks/run.sh`, reads the suite and runs nothing, and
    counting such a part would credit the arm with a verification the run
    never made.
    """
    for part in _COMMAND_SEPARATORS.split(text):
        words = [word.strip("\"'") for word in part.split()]
        index = 0
        while index < len(words) and (words[index].startswith("-") or words[index].isdigit() or Path(words[index]).name in COMMAND_WRAPPERS):
            index += 1
        if index >= len(words):
            continue
        program, remainder = words[index], " ".join(words[index:])
        if any(remainder.startswith(marker) or program == marker or program.endswith("/" + marker) for marker in markers):
            return True
    return False


def verifier_source(record: dict[str, Any]) -> str | None:
    """Where the attempt's verifier evidence comes from, or None when the record names no harness."""
    harness = harness_of(record)
    if harness is None:
        return None
    return VERIFIER_FROM_TOOL if harness == FOE_HARNESS else VERIFIER_FROM_COMMAND


def verifier_evidence(record: dict[str, Any]) -> dict[str, Any]:
    """How many times the verifier fired for one attempt, whether its last firing cleared, and where the evidence came from.

    Under foe a firing is one call of the `check` tool the document
    declares or one invocation by the runtime's own completion gate, which
    the trajectory carries under the event's name; the gate fires the
    verifier without any call of the tool, so both names count. Under Codex
    there is no verifier tool, so a firing is one shell command that runs
    the task's check suite and it cleared when the command exited zero; the
    two counts are therefore two different measurements of the same step.
    """
    source = verifier_source(record)
    cleared: list[bool | None] = []
    if source == VERIFIER_FROM_TOOL:
        cleared = [_findings_cleared(call.get("summary"), bool(call.get("is_error"))) for call in tool_calls(record) if call.get("name") in VERIFIER_NAMES]
    elif source == VERIFIER_FROM_COMMAND:
        markers = check_markers(record)
        for command in shell_commands(record):
            text = command.get("text")
            if not isinstance(text, str) or not _runs_check_suite(text, markers):
                continue
            status = _integer(command.get("exit_code"))
            cleared.append(None if status is None else status == 0)
    return {"source": source, "runs": len(cleared), "cleared": cleared[-1] if cleared else None}


def block_call_codes(record: dict[str, Any]) -> list[str | None]:
    """The code each call of the `block` tool stated, in order; only a foe document declares that tool.

    The code alone is returned, and the word `code` is dropped from a
    summary that leads with it, so that this column and the blocked-code
    table name one stop by one string.
    """
    codes: list[str | None] = []
    for call in tool_calls(record):
        if call.get("name") != BLOCK_TOOL:
            continue
        summary = call.get("summary")
        if not isinstance(summary, str):
            codes.append(None)
            continue
        stated = summary[len(BLOCK_SUMMARY_PREFIX) :] if summary.startswith(BLOCK_SUMMARY_PREFIX) else summary
        codes.append(stated.strip() or None)
    return codes


def compaction_count(record: dict[str, Any]) -> int | None:
    """How many times the attempt replaced a transcript by a summary, from the totals or from the agents themselves."""
    counted = _integer((record.get("totals") or {}).get("compactions"))
    if counted is not None:
        return counted
    trajectory_ = record.get("trajectory")
    if not isinstance(trajectory_, dict):
        return None
    return sum(len(agent.get("compactions", [])) for agent in trajectory_.get("agents", []))


def mechanisms(record: dict[str, Any]) -> dict[str, Any]:
    """The mechanism columns of one attempt, read so that a record without a tool-call stream still reports."""
    evidence = verifier_evidence(record)
    codes = block_call_codes(record)
    totals = record.get("totals") or {}
    by_name = totals.get("tool_calls_by_name")
    return {
        "verifier_source": evidence["source"],
        "verifier_runs": evidence["runs"],
        "verifier_cleared": evidence["cleared"],
        "block_calls": len(codes),
        "block_codes": [code for code in codes if code is not None],
        "compactions": compaction_count(record),
        "tool_calls": _integer(totals.get("tool_calls")),
        "tool_calls_by_name": {str(name): count for name, count in by_name.items()} if isinstance(by_name, dict) else {},
    }


def mechanism_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The mechanism columns over one arm's scored attempts.

    One arm runs one harness, so the arm's verifier source is the source of
    its attempts; it is null for an arm whose records name no harness.
    """
    scored = [record for record in records if is_scored(record)]
    measures = [mechanisms(record) for record in scored]
    sources = [measure["verifier_source"] for measure in measures if measure["verifier_source"] is not None]
    fired = [measure for measure in measures if measure["verifier_runs"]]
    settled = [measure for measure in fired if measure["verifier_cleared"] is not None]
    codes: dict[str, int] = {}
    names: dict[str, int] = {}
    for measure in measures:
        for code in measure["block_codes"]:
            codes[code] = codes.get(code, 0) + 1
        for name, count in measure["tool_calls_by_name"].items():
            value = _integer(count)
            if value is not None:
                names[name] = names.get(name, 0) + value
    return {
        "attempts": len(scored),
        "verifier_source": sources[0] if sources else None,
        "verifier_ran": rate(len(fired), len(scored)),
        "verifier_runs": _mean_measured([float(measure["verifier_runs"]) for measure in measures]),
        "verifier_cleared": rate(sum(1 for measure in settled if measure["verifier_cleared"]), len(settled)),
        "block_called": rate(sum(1 for measure in measures if measure["block_calls"]), len(scored)),
        "block_codes": dict(sorted(codes.items())),
        "compactions": _mean_measured([measure["compactions"] for measure in measures]),
        "tool_calls": _mean_measured([measure["tool_calls"] for measure in measures]),
        "tool_calls_by_name": dict(sorted(names.items())),
    }


def outcome_agreement(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether an arm's self-report matches its own logs, over the attempts whose record settles both.

    `run.py` writes `outcomes.agree` as the comparison of the status and
    code the arm reported with the status and code its trajectory carries,
    and leaves it null while the attempt has no trajectory. The rate is
    taken over the scored attempts, which is the count the Mechanisms row
    states beside it, so a faulted attempt that carries the field enters
    neither the numerator nor the denominator.
    """
    scored = [record for record in records if is_scored(record)]
    settled = [record for record in scored if isinstance((record.get("outcomes") or {}).get("agree"), bool)]
    disagreements = [
        {"task": task_name(record), "attempt": record["attempt"], "arm_outcome": record["outcomes"].get("arm"), "trajectory_outcome": record["outcomes"].get("trajectory")}
        for record in settled
        if not record["outcomes"]["agree"]
    ]
    return {**rate(len(settled) - len(disagreements), len(settled)), "not_measured": len(scored) - len(settled), "disagreements": disagreements}


def conformance_metrics(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The share of the arm's attempts whose trace conformed, or None for an arm that records no conformance report.

    `normalize_foe.py` writes the report and only a foe arm has one, so a
    Codex arm's column is absent rather than zero. An attempt whose
    conformance check itself could not run carries an error in place of the
    verdict and enters no rate. The rate is taken over the scored attempts,
    which is the count the Mechanisms row states beside it.
    """
    reported = [record for record in records if is_scored(record) and isinstance(record.get("conformance"), dict)]
    if not reported:
        return None
    valid = [record for record in reported if isinstance(record["conformance"].get("valid"), bool)]
    errors = [
        {"task": task_name(record), "attempt": record["attempt"], "error": str(record["conformance"].get("error"))}
        for record in reported
        if not isinstance(record["conformance"].get("valid"), bool)
    ]
    return {**rate(sum(1 for record in valid if record["conformance"]["valid"]), len(valid)), "not_measured": len(errors), "errors": errors}


def fault_reasons(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every attempt an infrastructure fault stopped, with the reason its record states."""
    return [{"task": task_name(record), "attempt": record["attempt"], "reason": str(record["infrastructure_error"])} for record in records if record["infrastructure_error"] is not None]


def not_applicable_reasons(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every attempt recorded without running, with the reason its record states."""
    return [{"task": task_name(record), "attempt": record["attempt"], "reason": str(record[NOT_APPLICABLE_KEY])} for record in records if is_not_applicable(record)]


def rates_and_costs(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Every rate and every cost over one set of records, counted over the attempts that were scored.

    The set is one arm's whole record list or the records of one class of
    one arm, and the two forms carry the same keys so that a per-class row
    and an arm row are read the same way.
    """
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
        "cost": cost_measures(scored),
        "cost_to_stop": cost_to_stop(scored),
        "cost_without_stop": cost_without_stop(scored),
        "censoring": censoring(scored),
    }


def arm_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The per-arm rates the module docstring defines, over one arm's records, with a per-class form of each."""
    classes = sorted({name for name in (class_name(record) for record in records) if name is not None})
    blocked = blocked_codes(records)
    return {
        **rates_and_costs(records),
        "harness": next((harness for harness in (harness_of(record) for record in records) if harness is not None), None),
        "per_class": {name: rates_and_costs([record for record in records if class_name(record) == name]) for name in classes},
        "ceilings": ceilings(records),
        "blocked_codes": blocked,
        "blocked_code_accepted": rate(sum(1 for entry in blocked if entry["accepted"]), len(blocked)),
        "mechanisms": mechanism_metrics(records),
        "outcome_agreement": outcome_agreement(records),
        "conformance": conformance_metrics(records),
        "faults": fault_reasons(records),
        "not_applicable_reasons": not_applicable_reasons(records),
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


def autonomy_measures(record: dict[str, Any]) -> dict[str, Any] | None:
    """The per-attempt row of an autonomy attempt, as `teams_measures` gives one for the teams family.

    None for a record outside the family. Every value comes from the record
    itself, so an attempt whose harness recorded no tool call still has a
    row, with its mechanism columns at zero.
    """
    if record["task"].get("family") != AUTONOMY_FAMILY:
        return None
    totals = record.get("totals") or {}
    tokens, seconds = cost(record)
    use = ceiling_use(record)
    # The row's highest use is over the ceilings the attempt was held to, so
    # that a figure past a number nothing enforces never reads as a bound.
    measured = [use[key] for key in enforced_ceilings(record) if use[key] is not None]
    conformance = record.get("conformance")
    return {
        "class_name": class_name(record),
        "reported_status": reported_status(record),
        "reported_code": (record.get("reported") or {}).get("code"),
        "actionable": is_actionable(record),
        "input_tokens": _integer(totals.get("input_tokens")),
        "output_tokens": _integer(totals.get("output_tokens")),
        "cache_read_tokens": _integer(totals.get("cache_read_tokens")),
        "model_calls": _integer(totals.get("model_calls")),
        "tokens": tokens,
        "seconds": seconds,
        "ceiling_use": use,
        "ceiling_use_max": max(measured) if measured else None,
        "bound_by": (trajectory_outcome(record).get("code") or (record.get("reported") or {}).get("code") or UNSTATED_LIMIT) if ended_exhausted(record) else None,
        "agree": (record.get("outcomes") or {}).get("agree"),
        "conformance_valid": conformance.get("valid") if isinstance(conformance, dict) else None,
        **mechanisms(record),
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


def smallest_significant_discordant_pairs(level: float = SIGNIFICANCE_LEVEL) -> int:
    """The fewest discordant pairs whose most uneven split the exact McNemar test calls significant at `level`.

    The most uneven split of `k` discordant pairs has a two-sided
    probability of two over two to the power `k`, so below some `k` no
    split of that many pairs reaches the level at all.
    """
    if not 0 < level < 1:
        raise ValueError(f"the significance level is {level}; a value strictly between 0 and 1 is required")
    for count in range(1, MAX_DISCORDANT_SEARCH + 1):
        if mcnemar_exact(count, 0) <= level:
            return count
    raise ValueError(f"no split of at most {MAX_DISCORDANT_SEARCH} discordant pairs reaches the level {level}")


def detectable_difference(paired_attempts: int, paired_tasks: int, level: float = SIGNIFICANCE_LEVEL) -> dict[str, Any]:
    """The smallest difference in the actionable rate a paired comparison of this size could call significant.

    The comparison needs at least the discordant pairs
    `smallest_significant_discordant_pairs` names before any split reaches
    the level, and that many pairs out of the paired attempts is the
    smallest difference the test could call significant. A comparison with
    fewer paired attempts than that count can call no difference
    significant, which `reachable` states.
    """
    if paired_attempts < 0 or paired_tasks < 0:
        raise ValueError(f"the paired attempts are {paired_attempts} and the paired tasks {paired_tasks}; both must be non-negative")
    needed = smallest_significant_discordant_pairs(level)
    reachable = paired_attempts >= needed
    return {
        "level": level,
        "min_discordant_pairs": needed,
        "paired_attempts": paired_attempts,
        "paired_tasks": paired_tasks,
        "difference": needed / paired_attempts if reachable else None,
        "reachable": reachable,
    }


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
        return {
            "observed": None,
            "lower": None,
            "upper": None,
            "clusters": 0,
            "pairs": 0,
            "resamples": resamples,
            "seed": seed,
            "degenerate": True,
            "degenerate_reason": "no task pairs the two arms, so there is no cluster to resample",
        }

    def difference(chosen: list[str]) -> float:
        firsts = [first for name in chosen for first, _ in clusters[name]]
        seconds = [second for name in chosen for _, second in clusters[name]]
        return sum(firsts) / len(firsts) - sum(seconds) / len(seconds)

    generator = random.Random(seed)
    draws = sorted(difference([generator.choice(names) for _ in names]) for _ in range(resamples))
    lower = draws[round(0.025 * (resamples - 1))]
    upper = draws[round(0.975 * (resamples - 1))]
    spread = {first - second for pairs in clusters.values() for first, second in pairs}
    reason = None
    if len(names) == 1:
        reason = f"the interval rests on the one task {names[0]}, so every resample draws that task"
    elif len(spread) == 1:
        reason = f"every pair differs by {next(iter(spread))}, so every resample gives that difference"
    return {
        "observed": difference(names),
        "lower": lower,
        "upper": upper,
        "clusters": len(names),
        "pairs": sum(len(pairs) for pairs in clusters.values()),
        "resamples": resamples,
        "seed": seed,
        "degenerate": reason is not None,
        "degenerate_reason": reason,
    }


def compare(records_first: list[dict[str, Any]], records_second: list[dict[str, Any]], resamples: int, seed: int) -> dict[str, Any]:
    """One paired comparison of two arms: the McNemar test on actionable outcomes and bootstrap intervals on two rate differences."""
    clusters = paired(records_first, records_second)
    counts = discordance(clusters, is_actionable)
    values = {
        name: {task: [(float(predicate(first)), float(predicate(second))) for first, second in pairs] for task, pairs in clusters.items()}
        for name, predicate in (("actionable", is_actionable), ("false_completion", is_false_completion))
    }
    discordant = counts["only_first"] + counts["only_second"]
    probability = mcnemar_exact(counts["only_first"], counts["only_second"]) if clusters else None
    return {
        "pairs": sum(len(pairs) for pairs in clusters.values()),
        "tasks": sorted(clusters),
        "task_count": len(clusters),
        "actionable_pairs": counts,
        "mcnemar_p": probability,
        # `tested` separates a probability of one that rests on a tested null
        # from one that rests on no discordant pair, which tested nothing.
        "mcnemar": {"p": probability, "discordant": discordant, "tested": bool(clusters) and discordant > 0},
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
    thinnest = min(pairs, key=lambda pair: pair["pairs"]) if pairs else None
    return {
        "schema_version": SCHEMA_VERSION,
        "records": len(records),
        "arms": arms,
        "families": families,
        "tasks": sorted({task_name(record) for record in records}),
        "per_arm": {arm: {**arm_metrics(by_arm[arm]), "teams": teams_metrics(by_arm[arm])} for arm in arms},
        "per_task": per_task(records, arms),
        "classes": sorted({name for name in (class_name(record) for record in records) if name is not None}),
        "autonomy_attempts": [
            {"task": task_name(record), "arm": record["arm"], "attempt": record["attempt"], "classification": record["classification"], **measure}
            for record in records
            for measure in (autonomy_measures(record),)
            if is_scored(record) and measure is not None
        ],
        "teams_attempts": [
            {"task": task_name(record), "arm": record["arm"], "attempt": record["attempt"], "classification": record["classification"], **measure}
            for record in records
            for measure in (teams_measures(record),)
            if is_scored(record) and measure is not None
        ],
        "pairs": pairs,
        "pairs_not_formed": not_formed,
        # The run's resolving power, stated once from the comparison with the
        # fewest paired attempts, which is the one that can detect the least.
        "detectable_difference": detectable_difference(thinnest["pairs"] if thinnest else 0, thinnest["task_count"] if thinnest else 0),
        "settings": {"resamples": resamples, "seed": seed},
    }


def _rate(value: dict[str, Any]) -> str:
    return "—" if value["rate"] is None else f"{value['rate']:.2f} ({value['count']}/{value['of']})"


def _number(value: float | None, digits: int = 0) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _interval(value: dict[str, Any]) -> str:
    if value["observed"] is None:
        return "—"
    marked = " degenerate" if value["degenerate"] else ""
    return f"{value['observed']:+.2f} [{value['lower']:+.2f}, {value['upper']:+.2f}]{marked}"


def _mcnemar(value: dict[str, Any]) -> str:
    """The McNemar column: the probability when a null was tested, and the reason when none was."""
    if value["p"] is None:
        return "—"
    return f"{value['p']:.4f}" if value["tested"] else "— no discordant pair"


def _cost_cell(value: dict[str, Any], digits: int = 0) -> str:
    """One cost column: the mean over the attempts that measured it, with that count."""
    return "—" if value["mean"] is None else f"{value['mean']:,.{digits}f} (n={value['measured']})"


def _use_cell(value: dict[str, Any]) -> str:
    """One ceiling column: the mean utilization, the largest one, and how many attempts reached the ceiling.

    A column no attempt was held to states its measurement and no count,
    because nothing there was a ceiling to reach.
    """
    if value["mean"] is None:
        return "—"
    if not value["enforced"]:
        return f"{value['mean']:.2f} max {value['max']:.2f} (measured over {value['measured']}, no ceiling)"
    return f"{value['mean']:.2f} max {value['max']:.2f} ({value['at_ceiling']} of {value['enforced']} at the ceiling)"


def _cell(value: Any) -> str:
    """One Markdown cell of text a run wrote: a fault reason carries exception text, which can hold a newline or a bar, and neither may break the row."""
    collapsed = " ".join(str(value).split()).replace("|", "\\|")
    return collapsed or "—"


def _flag(value: Any) -> str:
    return "—" if not isinstance(value, bool) else ("yes" if value else "no")


def _counts(value: dict[str, int]) -> str:
    return ", ".join(f"{name} ×{count}" for name, count in value.items()) or "—"


def _mean(value: dict[str, Any], digits: int = 2) -> str:
    return "—" if value["mean"] is None else f"{value['mean']:.{digits}f} (n={value['measured']})"


def classes_markdown(report: dict[str, Any]) -> list[str]:
    """One table per arm with every rate and cost over one class, because a rate over every class mixes opposite demands."""
    lines = [
        "",
        "## Classes",
        "",
        "Every rate of the arms table over one task class. `solvable` asks a harness to finish; every other autonomy class asks it to stop. "
        "Costs are means over the attempts that measured them, and input tokens are the primary measure: foe records no separate reasoning "
        "count while Codex reports its reasoning tokens inside the output count, so the two output totals count different things. The two "
        "stop columns are input tokens for that reason.",
    ]
    for arm, metrics in report["per_arm"].items():
        lines.extend(
            [
                "",
                f"### `{arm}`",
                "",
                "| class | attempts | scored | actionable | false completion | block precision | block recall | killed | damage | input tokens | output tokens | cache read | model calls | seconds | input tokens to stop | input tokens without a stop | censoring |",
                "|---|---:|---:|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for name, entry in metrics["per_class"].items():
            spend = entry["cost"]
            lines.append(
                f"| {name} | {entry['attempts']} | {entry['scored']} | {_rate(entry['actionable'])} | {_rate(entry['false_completion'])} | "
                f"{_rate(entry['block_precision'])} | {_rate(entry['block_recall'])} | {_rate(entry['killed'])} | {_rate(entry['damage'])} | "
                f"{_cost_cell(spend['input_tokens'])} | {_cost_cell(spend['output_tokens'])} | {_cost_cell(spend['cache_read_tokens'])} | "
                f"{_cost_cell(spend['model_calls'], 1)} | {_cost_cell(spend['seconds'], 1)} | {_cost_cell(entry['cost_to_stop']['cost']['input_tokens'])} | "
                f"{_cost_cell(entry['cost_without_stop']['cost']['input_tokens'])} | {_rate(entry['censoring'])} |"
            )
    lines.append("")
    lines.append(
        "Input tokens to stop are the attempts classified `correct-stop`; input tokens without a stop are the attempts on tasks that admit no "
        "completion which did not stop correctly, and the two are read together. Censoring is the share of the attempts on those tasks which a ceiling or "
        "a kill ended rather than a stop of the arm's own."
    )
    return lines


def ceilings_markdown(report: dict[str, Any]) -> list[str]:
    """Each arm's totals against the ceilings its records carry, and the limit that bound its exhausted attempts."""
    lines = [
        "",
        "## Ceilings",
        "",
        "| arm | scored | model calls | input tokens | output tokens | seconds | at a ceiling | exhausted | bound by |",
        "|---|---:|---|---|---|---|---|---:|---|",
    ]
    for arm, metrics in report["per_arm"].items():
        entry = metrics["ceilings"]
        use = entry["utilization"]
        lines.append(
            f"| `{arm}` | {entry['scored']} | {_use_cell(use['model_calls'])} | {_use_cell(use['input_tokens'])} | {_use_cell(use['output_tokens'])} | "
            f"{_use_cell(use['seconds'])} | {_rate(entry['at_ceiling'])} | {entry['exhausted']} | {_counts(entry['bound_by'])} |"
        )
    lines.extend(
        [
            "",
            "Each column is the attempt's total over the ceiling its own record carries. Codex takes token and wall-clock limits alone, so the "
            "model-call column is a bound for a foe arm and a measurement for a Codex arm, and a column no attempt was held to states its "
            "measurement and no count. An attempt is at a ceiling only on the keys its own harness enforces. `bound by` counts the limit each "
            "exhausted attempt's outcome names.",
        ]
    )
    return lines


def mechanisms_markdown(report: dict[str, Any]) -> list[str]:
    """The verifier, block, and compaction columns, with the agreement between each arm's report and its own logs."""
    lines = [
        "",
        "## Mechanisms",
        "",
        "| arm | scored | verifier from | verifier ran | firings | last firing cleared | block called | block codes | compactions | tool calls | self-report agrees | trace conformance |",
        "|---|---:|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, metrics in report["per_arm"].items():
        entry = metrics["mechanisms"]
        conformance = metrics["conformance"]
        lines.append(
            f"| `{arm}` | {entry['attempts']} | {entry['verifier_source'] or '—'} | {_rate(entry['verifier_ran'])} | {_mean(entry['verifier_runs'])} | "
            f"{_rate(entry['verifier_cleared'])} | {_rate(entry['block_called'])} | {_counts(entry['block_codes'])} | {_mean(entry['compactions'])} | "
            f"{_mean(entry['tool_calls'], 1)} | {_rate(metrics['outcome_agreement'])} | {'—' if conformance is None else _rate(conformance)} |"
        )
    lines.extend(
        [
            "",
            "A foe arm's verifier fires as the `check` tool its document declares and as the runtime's own invocation at the completion gate, "
            "and a Codex arm's is a shell command that runs the task's check suite, so the two firing counts come from two different sources and "
            "the `verifier from` column states which. A firing cleared when its result reported no finding, which an exit status does not state. "
            "The `block` tool is a foe tool; the code a Codex arm stated for a blocked run is in the blocked-code table. Trace conformance is the "
            "foe-only column `normalize_foe.py` writes, and an arm that records none is left empty. The two rightmost columns are taken over the "
            "same scored attempts the `scored` column counts.",
        ]
    )
    disagreements = [(arm, item) for arm, metrics in report["per_arm"].items() for item in metrics["outcome_agreement"]["disagreements"]]
    if disagreements:
        lines.extend(["", "Attempts whose self-report and logs disagree:", ""])
        for arm, item in disagreements:
            lines.append(f"- `{arm}` `{item['task']}` attempt {item['attempt']}: the arm reported {_cell(item['arm_outcome'])} and its logs carry {_cell(item['trajectory_outcome'])}")
    return lines


def blocked_markdown(report: dict[str, Any]) -> list[str]:
    """Every attempt that stopped, with the code it stated against the codes its task accepts."""
    rows = [entry for metrics in report["per_arm"].values() for entry in metrics["blocked_codes"]]
    if not rows:
        return []
    lines = [
        "",
        "## Blocked codes",
        "",
        "| task | class | arm | attempt | code | accepted | codes the task accepts | cell |",
        "|---|---|---|---:|---|---|---|---|",
    ]
    for entry in rows:
        accepted = ", ".join(entry["correct_codes"]) or ("any code" if entry["task_accepts_blocked"] else "no stop")
        lines.append(
            f"| `{entry['task']}` | {entry['class_name']} | `{entry['arm']}` | {entry['attempt']} | {entry['code'] or '—'} | "
            f"{_flag(entry['accepted'])} | {accepted} | {entry['classification']} |"
        )
    lines.extend(["", "A code is accepted when the task admits a stop and either names no code or names this one; a stop for any other reason is a wrong stop."])
    return lines


def faults_markdown(report: dict[str, Any]) -> list[str]:
    """The reason behind every attempt that faulted and every attempt an arm could not take."""
    rows = [(arm, "fault", item) for arm, metrics in report["per_arm"].items() for item in metrics["faults"]]
    rows += [(arm, "not applicable", item) for arm, metrics in report["per_arm"].items() for item in metrics["not_applicable_reasons"]]
    if not rows:
        return []
    lines = ["", "## Faults and attempts not applicable", "", "| arm | task | attempt | kind | reason |", "|---|---|---:|---|---|"]
    for arm, kind, item in rows:
        lines.append(f"| `{arm}` | `{item['task']}` | {item['attempt']} | {kind} | {_cell(item['reason'])} |")
    return lines


def autonomy_markdown(report: dict[str, Any]) -> list[str]:
    """One row per scored autonomy attempt, as the teams section gives one per teams attempt."""
    if not report["autonomy_attempts"]:
        return []
    lines = [
        "",
        "## Autonomy attempts",
        "",
        "| task | class | arm | attempt | cell | reported | code | input tokens | output tokens | model calls | seconds | highest ceiling use | bound by | verifier firings | cleared | block codes | compactions | self-report agrees | trace conforms |",
        "|---|---|---|---:|---|---|---|---:|---:|---:|---:|---|---|---:|---|---|---|---|---|",
    ]
    for measure in report["autonomy_attempts"]:
        lines.append(
            f"| `{measure['task']}` | {measure['class_name']} | `{measure['arm']}` | {measure['attempt']} | {measure['classification']} | "
            f"{measure['reported_status'] or '—'} | {measure['reported_code'] or '—'} | {_number(measure['input_tokens'])} | {_number(measure['output_tokens'])} | "
            f"{_number(measure['model_calls'])} | {_number(measure['seconds'], 1)} | {_number(measure['ceiling_use_max'], 2)} | {measure['bound_by'] or '—'} | "
            f"{measure['verifier_runs']} | {_flag(measure['verifier_cleared'])} | {', '.join(measure['block_codes']) or '—'} | {_number(measure['compactions'])} | "
            f"{_flag(measure['agree'])} | {_flag(measure['conformance_valid'])} |"
        )
    return lines


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


def _detectable_sentence(value: dict[str, Any]) -> str:
    """The one statement of what a run this size could detect, from the comparison with the fewest paired attempts."""
    head = f"The exact test reaches a two-sided probability at or below {value['level']} only with at least {value['min_discordant_pairs']} discordant pairs"
    if not value["reachable"]:
        return f"{head}; the thinnest comparison forms {value['paired_attempts']} paired attempts over {value['paired_tasks']} tasks, so it can call no difference significant."
    return (
        f"{head}, so over the {value['paired_attempts']} paired attempts of the thinnest comparison, which covers {value['paired_tasks']} tasks, "
        f"the smallest difference in the actionable rate this run could call significant is {value['difference']:.2f}."
    )


def markdown(report: dict[str, Any]) -> str:
    """The report as Markdown tables: one row per arm, one per task and arm, one per pair."""
    lines = [
        "## Arms",
        "",
        "| arm | attempts | scored | faults | not applicable | actionable | false completion | block precision | block recall | killed | damage | input tokens to stop | seconds to stop |",
        "|---|---:|---:|---:|---:|---|---|---|---|---|---|---:|---:|",
    ]
    for arm, metrics in report["per_arm"].items():
        stop = metrics["cost_to_stop"]
        lines.append(
            f"| `{arm}` | {metrics['attempts']} | {metrics['scored']} | {metrics['infrastructure_failures']} | {metrics['not_applicable']} | {_rate(metrics['actionable'])} | "
            f"{_rate(metrics['false_completion'])} | {_rate(metrics['block_precision'])} | {_rate(metrics['block_recall'])} | {_rate(metrics['killed'])} | "
            f"{_rate(metrics['damage'])} | {_number(stop['cost']['input_tokens']['mean'])} | {_number(stop['seconds_mean'], 1)} |"
        )
    lines.extend(classes_markdown(report))
    lines.extend(ceilings_markdown(report))
    lines.extend(mechanisms_markdown(report))
    lines.extend(blocked_markdown(report))
    lines.extend(faults_markdown(report))
    lines.extend(["", "## Tasks", "", "| task | class | arm | attempts | cells | actionable | mean tokens | mean seconds |", "|---|---|---|---:|---|---|---:|---:|"])
    for task, entry in report["per_task"].items():
        for arm, metrics in entry["arms"].items():
            cells = ", ".join(f"{cell} ×{count}" for cell, count in metrics["cells"].items()) or "—"
            lines.append(f"| `{task}` | {entry['class_name']} | `{arm}` | {metrics['attempts']} | {cells} | {_rate(metrics['actionable'])} | {_number(metrics['tokens_mean'])} | {_number(metrics['seconds_mean'], 1)} |")
    lines.extend(autonomy_markdown(report))
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
                f"{_mcnemar(pair['mcnemar'])} | {_interval(pair['actionable_difference'])} | {_interval(pair['false_completion_difference'])} |"
            )
        lines.extend(
            [
                "",
                f"Differences are first minus second, with 95 percent intervals from {report['settings']['resamples']} cluster-bootstrap resamples over tasks (seed {report['settings']['seed']}). "
                "Each interval rests on the tasks its row counts: those are the clusters the bootstrap resamples. An interval marked degenerate "
                "rests on one task or on a difference that every pair shares, so it states the width of the data and no sampling width. A McNemar "
                "column reading `no discordant pair` tested nothing, and a probability of one beside discordant pairs tested the null and did not "
                "reject it.",
                "",
                _detectable_sentence(report["detectable_difference"]),
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
