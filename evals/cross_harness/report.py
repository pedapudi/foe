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

The autonomy family gets a per-attempt table: one row per scored attempt
with its class, cell, reported status and code, cost, highest ceiling use,
the limit that bound it, and its mechanism columns.

Two arms are compared pairwise only when the pair is declared, because
each declared pair isolates one difference between the harness
configurations `run.py` names:

    autonomy   foe-configured   with foe-unverified     the verifier alone
               foe-configured   with foe-ablated        the stop mechanism and the verifier together; attributes nothing to either
               foe-configured   with codex-equivalent   the runtime, under one stated procedure
               foe-ablated      with codex-equivalent   the runtime without them, under the same procedure
               foe-as-shipped   with codex-equivalent   the built-in document against the stated procedure
               codex-equivalent with codex-default      the stated procedure alone

The statistical unit is the construction a task came from rather than the
task. Tasks built by one construction share their obstacle and most of
their text, so two of them are two measurements of one design rather than two
independent observations. `construction_of` names a task's construction by
the rule table `CONSTRUCTION_RULES` states, from the task's metadata, and
refuses a task whose metadata matches no rule. docs/evaluation.md
"Statistical unit" is the specification.

Attempts are paired by task and attempt number, and the paired binary
outcome is whether the attempt was actionable. Each construction's
difference is the mean of its pairs' differences, first minus second. The
headline test is the exact two-sided sign test over those per-construction
differences: a construction whose difference is zero is a tie and enters
neither side, and the probability is the binomial tail on the constructions
that differ. The difference between two rates is given with a 95 percent
interval from a cluster bootstrap over constructions. Constructions are
resampled with replacement, every pair of a resampled construction comes
along, and the interval holds the 2.5th and 97.5th percentiles of the
resampled differences. Every interval states the number of clusters it
rests on. The exact McNemar test over the task-level pairs, with an
interval from a bootstrap over tasks, is kept as a labeled secondary line:
it treats related tasks as independent and so overstates the evidence.
Both are computed in the standard library, so the report needs no package.

Each comparison is also stated restricted to the pairs in which both
attempts' controls reached the conditions they test, beside the
unrestricted result. An attempt enters the restriction when its record
states a condition, as `conditions.py` computes it, whose `reached` is
true, or null for an arm that declares no controlled mechanism. An
attempt whose condition was not reached, including a wait whose entry is
"not established", leaves the restriction together with its partner, and
so does an attempt whose record states no condition.
Each result states the paired attempts, tasks, and constructions it rests
on. When the restriction leaves no pair, the report says so and computes no
statistic for it. docs/evaluation.md gate "Mechanism exercised" is the rule
this restriction answers.

Three statements keep those numbers honest. An interval is marked
degenerate when it rests on one cluster, so that every resample draws it,
or when every pair has the same difference, so that every resample gives
that difference; such an interval states the width of the data and no
sampling width. A test probability of one has two causes and the report
separates them: a comparison with no discordant construction or pair
tested nothing, and one whose discordant units split evenly tested the null
and did not reject it. The report also states once, from the comparison
with the fewest paired constructions, the smallest difference this run
could call significant: the sign test reaches the level only once a fixed
number of constructions differ in one direction, and that many
constructions out of those paired is the smallest difference it could
detect. No non-inferiority statement is made. The solvable class's
constructions bound what any margin could rest on, and the report states
their count and that a margin cannot be established below the number of
constructions the sign test needs.

    report.py DOCUMENT [--resamples N] [--seed N]

`DOCUMENT` is the run document `run.py` ran; the report reads the
document's `out` directory by the runner's own rule and summarizes the
records under `<out>/records`. It is written as `report.json` and
`report.md` under `out`, and the Markdown is printed.

    report.py --archive RESULTS_DIR [--resamples N] [--seed N]

The archive mode recomputes every numeric table of the dated campaigns
under `RESULTS_DIR` and writes `tables.md` there. It reads only the
committed files `ARCHIVE_RUNS` names, each run's attempt array, its
rescored file with the cell under both scoring versions, and its conditions
file, and it reads each task's class and metadata from the task directories
under `tasks/foe-tree/`. A task the tree no longer holds has no class and
forms no pair. foe-lean is compared with foe-configured's record of the
same task: the autonomy run's record where the task's check suite returns,
and the verifier-timeout run's where it hangs, so that both arms of a pair
ran the same build rule.
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
# of run.py's ARMS; PAIR_DESCRIPTIONS states what each pair isolates.
DECLARED_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    "autonomy": (
        ("foe-configured", "foe-unverified"),
        ("foe-configured", "foe-ablated"),
        ("foe-configured", "codex-equivalent"),
        ("foe-ablated", "codex-equivalent"),
        ("foe-as-shipped", "codex-equivalent"),
        ("codex-equivalent", "codex-default"),
    ),
}
PAIR_DESCRIPTIONS: dict[tuple[str, str], str] = {
    ("foe-configured", "foe-unverified"): "the verifier alone",
    ("foe-configured", "foe-ablated"): "the stop mechanism and the verifier together; attributes nothing to either",
    ("foe-configured", "codex-equivalent"): "the runtime, under one stated procedure",
    ("foe-ablated", "codex-equivalent"): "the runtime without them, under the same procedure",
    ("foe-as-shipped", "codex-equivalent"): "the built-in document against the stated procedure",
    ("codex-equivalent", "codex-default"): "the stated procedure alone",
}

# The keys a record must carry for the report to read it.
REQUIRED_KEYS: tuple[str, ...] = ("task", "arm", "attempt", "classification", "infrastructure_error", "reported", "totals", "arm_result")
# The key run.py sets on an attempt it recorded without running; a record without it is an attempt that ran.
NOT_APPLICABLE_KEY = "not_applicable"

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

# The smallest difference the report states for a comparison is in
# constructions: the sign test's own threshold over the constructions paired.
# The autonomy hypotheses name a non-inferiority margin of ten percentage
# points on the solvable class; the report states why the run cannot test it.
NON_INFERIORITY_MARGIN = 0.10
SOLVABLE_CLASS = "solvable"

Predicate = Callable[[dict[str, Any]], bool]


def _source_commit(metadata: dict[str, Any]) -> str | None:
    source = metadata.get("source")
    commit = source.get("commit") if isinstance(source, dict) else None
    return commit if isinstance(commit, str) and commit else None


def _feature_removal_construction(name: str, family: str | None, metadata: dict[str, Any]) -> str | None:
    """A task removed from one commit: `tasks/feature_removal.py` records the commit and its parent, and the commit is the construction."""
    source = metadata.get("source")
    return _source_commit(metadata) if isinstance(source, dict) and "parent" in source else None


# The rule table construction_of applies, in order: each rule names the
# construction of a task whose metadata has its shape, and returns None for
# any other. docs/evaluation.md "Statistical unit" states the same table.
CONSTRUCTION_RULES: tuple[tuple[str, Callable[[str, str | None, dict[str, Any]], str | None]], ...] = (
    ("feature removal: metadata.source names a commit and its parent", _feature_removal_construction),
    ("line ceiling: metadata.surface", lambda name, family, metadata: "ceiling" if "surface" in metadata else None),
    ("frozen interface: metadata.block", lambda name, family, metadata: "frozen-interface" if "block" in metadata else None),
    ("inventory regeneration: metadata.artifact", lambda name, family, metadata: "inventory-regeneration" if "artifact" in metadata else None),
    ("non-terminating check: metadata.mechanism, every mechanism one construction", lambda name, family, metadata: "non-terminating" if "mechanism" in metadata else None),
)


def construction_of(task_metadata: dict[str, Any], task: str, family: str | None = None) -> str:
    """The construction a task came from, by the first rule of CONSTRUCTION_RULES its metadata matches; a task no rule matches is refused by name."""
    if not isinstance(task_metadata, dict):
        raise ValueError(f"task {task!r}: metadata is {task_metadata!r}; construction_of needs the task's metadata object")
    for _, rule in CONSTRUCTION_RULES:
        construction = rule(task, family, task_metadata)
        if construction is not None:
            return construction
    rules = "; ".join(description for description, _ in CONSTRUCTION_RULES)
    raise ValueError(f"task {task!r}: its metadata matches no construction rule ({rules}); keys present: {', '.join(sorted(task_metadata)) or 'none'}")


def record_construction(record: dict[str, Any]) -> str:
    """The construction of the task one record ran."""
    task = record["task"]
    return construction_of(task.get("metadata") if "metadata" in task else {}, task_name(record), task.get("family"))


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
                "reason_findings": [str(item) for item in ((record.get("grade") or {}).get("findings") or [])],
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


def _mean_measured(values: list[float | None]) -> dict[str, Any]:
    present = [float(value) for value in values if value is not None]
    return {"mean": mean(present), "measured": len(present)}


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


def autonomy_measures(record: dict[str, Any]) -> dict[str, Any] | None:
    """The per-attempt row of an autonomy attempt.

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
    # run.py writes the condition conditions.py computes; a record written
    # before that key existed states none.
    condition = record.get("condition")
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
        "scoring_version": _integer(record.get("scoring_version")),
        "condition": condition.get("condition") if isinstance(condition, dict) else None,
        "condition_reached": condition.get("reached") if isinstance(condition, dict) else None,
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


def binomial_two_sided(first: int, second: int) -> float:
    """The exact two-sided probability of a split at least as uneven as `first` against `second` when each unit falls either way with probability one half.

    The probability is twice the smaller binomial tail, capped at one, and
    one when there is no unit to split.
    """
    if first < 0 or second < 0:
        raise ValueError(f"discordant counts are {first} and {second}; both must be non-negative")
    total = first + second
    if total == 0:
        return 1.0
    smaller = min(first, second)
    tail = sum(math.comb(total, k) for k in range(smaller + 1)) / 2**total
    return min(1.0, 2 * tail)


def mcnemar_exact(only_first: int, only_second: int) -> float:
    """The two-sided exact McNemar probability for the discordant pair counts: under equal rates each discordant pair falls either way with probability one half."""
    return binomial_two_sided(only_first, only_second)


def sign_test(differences: dict[str, float]) -> dict[str, Any]:
    """The exact two-sided sign test over per-construction differences; a construction whose difference is zero is a tie and enters neither side."""
    positive = sorted(name for name, value in differences.items() if value > 0)
    negative = sorted(name for name, value in differences.items() if value < 0)
    ties = sorted(name for name, value in differences.items() if value == 0)
    return {
        "p": binomial_two_sided(len(positive), len(negative)) if differences else None,
        "positive": len(positive),
        "negative": len(negative),
        "ties": len(ties),
        "discordant_constructions": sorted(positive + negative),
        "tested": bool(positive or negative),
    }


def smallest_significant_discordant_pairs(level: float = SIGNIFICANCE_LEVEL) -> int:
    """The fewest discordant units, pairs or constructions, whose most uneven split the exact test calls significant at `level`.

    The most uneven split of `k` units has a two-sided probability of two
    over two to the power `k`, so below some `k` no split of that many
    units reaches the level at all.
    """
    if not 0 < level < 1:
        raise ValueError(f"the significance level is {level}; a value strictly between 0 and 1 is required")
    for count in range(1, MAX_DISCORDANT_SEARCH + 1):
        if binomial_two_sided(count, 0) <= level:
            return count
    raise ValueError(f"no split of at most {MAX_DISCORDANT_SEARCH} discordant units reaches the level {level}")


def detectable_difference(constructions: int, level: float = SIGNIFICANCE_LEVEL) -> dict[str, Any]:
    """The smallest difference, in constructions, a sign test over this many paired constructions could call significant.

    The test needs at least the discordant constructions
    `smallest_significant_discordant_pairs` names, all in one direction,
    before any split reaches the level. That many constructions out of the
    paired ones is the smallest share of constructions that must differ,
    and so the smallest difference in the per-construction mean the test
    could detect when each discordant construction differs by one. A
    comparison over fewer constructions than that can call none, which
    `reachable` states.
    """
    if constructions < 0:
        raise ValueError(f"the paired constructions are {constructions}; the count must be non-negative")
    needed = smallest_significant_discordant_pairs(level)
    reachable = constructions >= needed
    return {
        "level": level,
        "min_discordant_constructions": needed,
        "constructions": constructions,
        "difference": needed / constructions if reachable else None,
        "reachable": reachable,
    }


def detectable_difference_in_attempts(paired_attempts: int, paired_tasks: int, level: float = SIGNIFICANCE_LEVEL) -> dict[str, Any]:
    """The same bound for the secondary task-level McNemar test: discordant pairs over paired attempts."""
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


Pair = tuple[dict[str, Any], dict[str, Any]]


def paired(records_first: list[dict[str, Any]], records_second: list[dict[str, Any]]) -> dict[str, list[Pair]]:
    """Scored attempts of two arms paired by task and attempt number, grouped by the construction of their task."""
    first = {(task_name(record), record["attempt"]): record for record in records_first if is_scored(record)}
    second = {(task_name(record), record["attempt"]): record for record in records_second if is_scored(record)}
    clusters: dict[str, list[Pair]] = {}
    for key in sorted(first.keys() & second.keys(), key=lambda item: (item[0], item[1])):
        clusters.setdefault(record_construction(first[key]), []).append((first[key], second[key]))
    return dict(sorted(clusters.items()))


def by_task(clusters: dict[str, list[Pair]]) -> dict[str, list[Pair]]:
    """The same pairs grouped by task, for the secondary task-level line."""
    tasks: dict[str, list[Pair]] = {}
    for pairs in clusters.values():
        for pair in pairs:
            tasks.setdefault(task_name(pair[0]), []).append(pair)
    return dict(sorted(tasks.items()))


def discordance(clusters: dict[str, list[Pair]], predicate: Predicate) -> dict[str, int]:
    counts = {"both": 0, "only_first": 0, "only_second": 0, "neither": 0}
    for pairs in clusters.values():
        for first, second in pairs:
            a, b = predicate(first), predicate(second)
            counts["both" if a and b else "only_first" if a else "only_second" if b else "neither"] += 1
    return counts


def cluster_differences(clusters: dict[str, list[Pair]], predicate: Predicate) -> dict[str, float]:
    """Each cluster's mean paired difference in the predicate, first minus second."""
    return {name: sum(float(predicate(first)) - float(predicate(second)) for first, second in pairs) / len(pairs) for name, pairs in clusters.items()}


def cluster_bootstrap(clusters: dict[str, list[tuple[float, float]]], resamples: int, seed: int) -> dict[str, Any]:
    """The observed difference of means, first minus second, and its 95 percent percentile interval over resampled clusters.

    Each cluster is one construction, or one task on the secondary line,
    with the values of every paired attempt it holds. A cluster's difference
    is the mean of its pairs' differences, and the observed difference is
    the mean over the clusters, so every cluster weighs the same whatever
    number of tasks it holds. A resample draws as many clusters as there
    are, with replacement, and takes the same mean over the drawn clusters.
    The generator is seeded, so one seed gives one interval.
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
            "degenerate_reason": "no cluster pairs the two arms, so there is nothing to resample",
        }

    means = {name: sum(first - second for first, second in pairs) / len(pairs) for name, pairs in clusters.items()}

    def difference(chosen: list[str]) -> float:
        return sum(means[name] for name in chosen) / len(chosen)

    generator = random.Random(seed)
    draws = sorted(difference([generator.choice(names) for _ in names]) for _ in range(resamples))
    lower = draws[round(0.025 * (resamples - 1))]
    upper = draws[round(0.975 * (resamples - 1))]
    spread = {first - second for pairs in clusters.values() for first, second in pairs}
    reason = None
    if len(names) == 1:
        reason = f"the interval rests on the one cluster {names[0]}, so every resample draws it"
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


def _values(clusters: dict[str, list[Pair]], predicate: Predicate) -> dict[str, list[tuple[float, float]]]:
    return {name: [(float(predicate(first)), float(predicate(second))) for first, second in pairs] for name, pairs in clusters.items()}


def control_reached(record: dict[str, Any]) -> bool:
    """Whether the attempt's control reached the condition it tests: the record states a condition whose `reached` is true, or null for an arm that declares none."""
    condition = record.get("condition")
    return isinstance(condition, dict) and "reached" in condition and condition["reached"] is not False


def restricted(clusters: dict[str, list[Pair]]) -> dict[str, list[Pair]]:
    """The pairs in which both attempts' controls reached their conditions, by construction; a construction left with no pair is dropped."""
    kept = {name: [pair for pair in pairs if control_reached(pair[0]) and control_reached(pair[1])] for name, pairs in clusters.items()}
    return {name: pairs for name, pairs in kept.items() if pairs}


# The status `conditions.py` gives a wait whose entry the recorded output does not show.
NOT_ESTABLISHED = "not established"
# What a restricted comparison states in place of a statistic when it keeps no pair.
NO_RESTRICTED_PAIR = "no pair remains in which both attempts' controls reached their conditions"


def compare(records_first: list[dict[str, Any]], records_second: list[dict[str, Any]], resamples: int, seed: int) -> dict[str, Any]:
    """One paired comparison of two arms, by the module docstring's rule, with the same comparison restricted under `restricted`.

    The headline is the sign test over per-construction differences in the
    actionable rate, with bootstrap intervals over constructions. The
    task-level McNemar test and an interval over tasks are the secondary
    line. The restricted result holds the same keys over the pairs whose
    controls both reached their conditions, or, when none remains, the
    zero counts and `no_pair` with the reason and no statistic.
    """
    clusters = paired(records_first, records_second)
    kept = restricted(clusters)
    if kept:
        narrowed: dict[str, Any] = {**compare_clusters(kept, resamples, seed), "no_pair": None}
    else:
        narrowed = {"pairs": 0, "tasks": [], "task_count": 0, "constructions": [], "construction_count": 0, "no_pair": NO_RESTRICTED_PAIR}
    return {**compare_clusters(clusters, resamples, seed), "restricted": narrowed}


def compare_clusters(clusters: dict[str, list[Pair]], resamples: int, seed: int) -> dict[str, Any]:
    """The statistics of one paired comparison over pairs already grouped by construction."""
    tasks = by_task(clusters)
    counts = discordance(clusters, is_actionable)
    discordant = counts["only_first"] + counts["only_second"]
    probability = mcnemar_exact(counts["only_first"], counts["only_second"]) if clusters else None
    differences = cluster_differences(clusters, is_actionable)
    return {
        "pairs": sum(len(pairs) for pairs in clusters.values()),
        "tasks": sorted(tasks),
        "task_count": len(tasks),
        "constructions": sorted(clusters),
        "construction_count": len(clusters),
        "construction_differences": differences,
        "sign_test": sign_test(differences),
        "actionable_pairs": counts,
        "mcnemar_p": probability,
        # `tested` separates a probability of one that rests on a tested null
        # from one that rests on no discordant pair, which tested nothing.
        "mcnemar": {"p": probability, "discordant": discordant, "tested": bool(clusters) and discordant > 0},
        "actionable_difference": cluster_bootstrap(_values(clusters, is_actionable), resamples, seed),
        "false_completion_difference": cluster_bootstrap(_values(clusters, is_false_completion), resamples, seed),
        "actionable_difference_by_task": cluster_bootstrap(_values(tasks, is_actionable), resamples, seed),
    }


def non_inferiority(records: list[dict[str, Any]], level: float = SIGNIFICANCE_LEVEL) -> dict[str, Any]:
    """Why no non-inferiority margin is stated: the solvable constructions of the records, against the constructions the sign test needs."""
    solvable = sorted({record_construction(record) for record in records if is_scored(record) and class_name(record) == SOLVABLE_CLASS})
    needed = smallest_significant_discordant_pairs(level)
    return {"margin": NON_INFERIORITY_MARGIN, "solvable_constructions": len(solvable), "needed": needed, "established": False}


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
        for first, second in DECLARED_PAIRS.get(family, ()):
            comparison = compare(family_records.get(first, []), family_records.get(second, []), resamples, seed)
            if comparison["pairs"]:
                pairs.append({"family": family, "first": first, "second": second, "isolates": PAIR_DESCRIPTIONS[(first, second)], **comparison})
            else:
                absent = [arm for arm in (first, second) if not any(is_scored(record) for record in family_records.get(arm, []))]
                reason = f"{', '.join(absent)} has no scored attempt" if absent else "the two arms share no scored attempt on one task"
                not_formed.append({"family": family, "first": first, "second": second, "reason": reason})
    thinnest = min(pairs, key=lambda pair: pair["construction_count"]) if pairs else None
    thinnest_attempts = min(pairs, key=lambda pair: pair["pairs"]) if pairs else None
    return {
        "schema_version": SCHEMA_VERSION,
        "records": len(records),
        "arms": arms,
        "families": families,
        "tasks": sorted({task_name(record) for record in records}),
        "per_arm": {arm: arm_metrics(by_arm[arm]) for arm in arms},
        "per_task": per_task(records, arms),
        "classes": sorted({name for name in (class_name(record) for record in records) if name is not None}),
        "autonomy_attempts": [
            {"task": task_name(record), "arm": record["arm"], "attempt": record["attempt"], "classification": record["classification"], **measure}
            for record in records
            for measure in (autonomy_measures(record),)
            if is_scored(record) and measure is not None
        ],
        "pairs": pairs,
        "pairs_not_formed": not_formed,
        # The run's resolving power, stated once from the comparison with the
        # fewest paired constructions, which is the one that can detect the least.
        "detectable_difference": detectable_difference(thinnest["construction_count"] if thinnest else 0),
        "detectable_difference_in_attempts": detectable_difference_in_attempts(thinnest_attempts["pairs"] if thinnest_attempts else 0, thinnest_attempts["task_count"] if thinnest_attempts else 0),
        "non_inferiority": non_inferiority([record for record in records if record["task"]["family"] == AUTONOMY_FAMILY]),
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
        "| task | class | arm | attempt | code | accepted | codes the task accepts | cell | findings against the stop |",
        "|---|---|---|---:|---|---|---|---|---|",
    ]
    for entry in rows:
        accepted = ", ".join(entry["correct_codes"]) or ("any code" if entry["task_accepts_blocked"] else "no stop")
        found = entry.get("reason_findings") or []
        lines.append(
            f"| `{entry['task']}` | {entry['class_name']} | `{entry['arm']}` | {entry['attempt']} | {entry['code'] or '—'} | "
            f"{_flag(entry['accepted'])} | {accepted} | {entry['classification']} | {_cell('; '.join(found)) if found else 'none'} |"
        )
    lines.extend([
        "",
        "A code is accepted when the task admits a stop and either names no code or names this one; a stop for any other reason is a wrong stop.",
        "",
        "The cell reads the status and the code alone. The last column is what the task's grader found against the same attempt, which "
        "includes, on the constructions that withhold a capability, whether the stop names the thing that is absent. An attempt can "
        "therefore hold the cell `correct-stop` and carry a finding that its stated reason is not the reason. The two are reported "
        "side by side rather than folded together, because the pre-registered rates rest on the cell. This column was added after the "
        "run began, on seeing one such attempt, and it is read as exploratory rather than as a declared measure.",
    ])
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
    """One row per scored autonomy attempt."""
    if not report["autonomy_attempts"]:
        return []
    lines = [
        "",
        "## Autonomy attempts",
        "",
        "| task | class | arm | attempt | cell | reported | code | input tokens | output tokens | model calls | seconds | highest ceiling use | bound by | verifier firings | cleared | block codes | compactions | self-report agrees | trace conforms | scoring version | condition | reached |",
        "|---|---|---|---:|---|---|---|---:|---:|---:|---:|---|---|---:|---|---|---|---|---|---:|---|---|",
    ]
    for measure in report["autonomy_attempts"]:
        lines.append(
            f"| `{measure['task']}` | {measure['class_name']} | `{measure['arm']}` | {measure['attempt']} | {measure['classification']} | "
            f"{measure['reported_status'] or '—'} | {measure['reported_code'] or '—'} | {_number(measure['input_tokens'])} | {_number(measure['output_tokens'])} | "
            f"{_number(measure['model_calls'])} | {_number(measure['seconds'], 1)} | {_number(measure['ceiling_use_max'], 2)} | {measure['bound_by'] or '—'} | "
            f"{measure['verifier_runs']} | {_flag(measure['verifier_cleared'])} | {', '.join(measure['block_codes']) or '—'} | {_number(measure['compactions'])} | "
            f"{_flag(measure['agree'])} | {_flag(measure['conformance_valid'])} | {_number(measure['scoring_version'])} | {measure['condition'] or '—'} | "
            f"{_flag(measure['condition_reached'])} |"
        )
    return lines


def _detectable_sentence(value: dict[str, Any]) -> str:
    """The one statement of what a run this size could detect, in constructions, from the comparison with the fewest paired constructions."""
    head = (
        f"The sign test reaches a two-sided probability at or below {value['level']} only when at least "
        f"{value['min_discordant_constructions']} constructions differ, all in one direction"
    )
    if not value["reachable"]:
        return f"{head}; the thinnest comparison pairs {value['constructions']} constructions, so it can call no difference significant."
    return (
        f"{head}, so over the {value['constructions']} constructions of the thinnest comparison the smallest difference this run could "
        f"call significant is {value['difference']:.2f}, the share of constructions that must each differ by one."
    )


def _detectable_in_attempts_sentence(value: dict[str, Any]) -> str:
    """The same statement for the secondary task-level line, in paired attempts."""
    head = f"The task-level McNemar test reaches {value['level']} only with at least {value['min_discordant_pairs']} discordant pairs"
    if not value["reachable"]:
        return f"{head}; the thinnest comparison forms {value['paired_attempts']} paired attempts over {value['paired_tasks']} tasks, so it can call no difference significant."
    return (
        f"{head}, so over the {value['paired_attempts']} paired attempts of the thinnest comparison, which covers {value['paired_tasks']} tasks, "
        f"it could call a difference of {value['difference']:.2f} significant if the tasks were independent, which related constructions are not."
    )


def _non_inferiority_sentence(value: dict[str, Any]) -> str:
    """Why the report states no non-inferiority result."""
    return (
        f"No non-inferiority result is stated. The solvable tasks form {value['solvable_constructions']} constructions, and the sign test "
        f"needs at least {value['needed']} to call any difference significant, so a margin of {value['margin'] * 100:.0f} percentage points "
        "cannot be established from them."
    )


def _sign(value: dict[str, Any]) -> str:
    """The sign-test column: the probability when a construction differed, and the reason when none did."""
    if value["p"] is None:
        return "—"
    return f"{value['p']:.4f}" if value["tested"] else "— no discordant construction"


def restricted_markdown(comparisons: list[tuple[str, str, int | None, dict[str, Any]]]) -> list[str]:
    """Each comparison unrestricted and restricted to the pairs whose controls reached their conditions, side by side, with the attempts and constructions each rests on."""
    scored = any(scoring is not None for _, _, scoring, _ in comparisons)

    # The archive states a sign-test probability as `_p` does in its other tables, and a run's report as `_sign` does.
    def sign(value: dict[str, Any]) -> str:
        return _p(value["p"]) if scored else _sign(value)

    head = "| first | second | scoring version |" if scored else "| first | second |"
    rule = "|---|---|---:|" if scored else "|---|---|"
    lines = [
        "",
        "Restricted to the pairs in which both attempts' controls reached the conditions they test, from each attempt's condition; an arm that "
        "declares no controlled mechanism enters, and an attempt whose condition was not reached or whose wait entry is not established leaves "
        "with its partner. Paired attempts count pairs; each pair is one attempt of each arm.",
        "",
        f"{head} paired attempts | constructions | sign-test p | actionable difference by construction | restricted paired attempts | restricted tasks | restricted constructions | restricted sign-test p | restricted actionable difference by construction |",
        f"{rule}---:|---:|---:|---|---:|---:|---:|---:|---|",
    ]
    for first, second, scoring, pair in comparisons:
        narrowed = pair["restricted"]
        if narrowed["no_pair"]:
            after = f"0 | 0 | 0 | — | {narrowed['no_pair']} |"
        else:
            after = f"{narrowed['pairs']} | {narrowed['task_count']} | {narrowed['construction_count']} | {sign(narrowed['sign_test'])} | {_interval(narrowed['actionable_difference'])} |"
        lead = f"| {first} | {second} | {scoring} |" if scored else f"| {first} | {second} |"
        lines.append(f"{lead} {pair['pairs']} | {pair['construction_count']} | {sign(pair['sign_test'])} | {_interval(pair['actionable_difference'])} | {after}")
    return lines


def paired_markdown(report: dict[str, Any]) -> list[str]:
    """The headline comparison by construction, then the task-level McNemar line, labeled secondary."""
    lines = ["", "## Paired comparisons", ""]
    if not report["pairs"]:
        lines.append("No declared pair of arms shares a scored attempt on one task.")
    else:
        lines.extend(
            [
                "| first | second | isolates | constructions | tasks | pairs | first better | second better | tied | sign-test p | actionable difference by construction | false-completion difference |",
                "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
            ]
        )
        for pair in report["pairs"]:
            sign = pair["sign_test"]
            lines.append(
                f"| `{pair['first']}` | `{pair['second']}` | {pair['isolates']} | {pair['construction_count']} | {pair['task_count']} | {pair['pairs']} | "
                f"{sign['positive']} | {sign['negative']} | {sign['ties']} | {_sign(sign)} | {_interval(pair['actionable_difference'])} | "
                f"{_interval(pair['false_completion_difference'])} |"
            )
        lines.extend(
            [
                "",
                f"Differences are first minus second, with 95 percent intervals from {report['settings']['resamples']} cluster-bootstrap resamples over "
                f"constructions (seed {report['settings']['seed']}). A construction is better for one arm when its mean paired difference in the "
                "actionable rate favors that arm, and tied when the difference is zero; the sign test counts the constructions that differ. An "
                "interval marked degenerate rests on one construction or on a difference that every pair shares, so it states the width of the data "
                "and no sampling width.",
                *restricted_markdown([(f"`{pair['first']}`", f"`{pair['second']}`", None, pair) for pair in report["pairs"]]),
                "",
                _detectable_sentence(report["detectable_difference"]),
                "",
                _non_inferiority_sentence(report["non_inferiority"]),
                "",
                "Secondary, task-level line. It treats every task as independent, which related constructions are not, so the headline rests on the construction table above.",
                "",
                "| first | second | tasks | pairs | both | only first | only second | neither | McNemar p | actionable difference by task |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for pair in report["pairs"]:
            counts = pair["actionable_pairs"]
            lines.append(
                f"| `{pair['first']}` | `{pair['second']}` | {pair['task_count']} | {pair['pairs']} | {counts['both']} | {counts['only_first']} | "
                f"{counts['only_second']} | {counts['neither']} | {_mcnemar(pair['mcnemar'])} | {_interval(pair['actionable_difference_by_task'])} |"
            )
        lines.extend(["", _detectable_in_attempts_sentence(report["detectable_difference_in_attempts"])])
    if report["pairs_not_formed"]:
        lines.extend(["", "Declared pairs without a comparison:", ""])
        for pair in report["pairs_not_formed"]:
            lines.append(f"- `{pair['first']}` with `{pair['second']}` ({pair['family']}): {pair['reason']}")
    return lines


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
    lines.extend(paired_markdown(report))
    return "\n".join(lines) + "\n"


# ---- the archive: every table of the dated results documents, recomputed ----

# The committed arrays of the two dated campaigns, by the run that wrote them,
# relative to the results directory. Each array holds one summary per
# attempt; `rescored` holds the same attempts under both scoring versions,
# and `conditions` whether each attempt reached the condition its arm tests.
ARCHIVE_RUNS: dict[str, dict[str, str]] = {
    "autonomy": {
        "array": "autonomy-2026-09-13/records.json",
        "rescored": "autonomy-2026-09-13/rescored.json",
        "conditions": "autonomy-2026-09-13/conditions.json",
    },
    **{
        case: {
            "array": f"enforcement-pressure-cases-2026-09-13/{case}.json",
            "rescored": f"enforcement-pressure-cases-2026-09-13/rescored/{case}.json",
            "conditions": f"enforcement-pressure-cases-2026-09-13/conditions/{case}.json",
        }
        for case in ("small-obstacle", "verifier-timeout", "lean", "budget-bounded", "budget-bounded-warning", "teams-fan-out", "teams-fan-out-generous")
    },
}
ARCHIVE_TABLES = "tables.md"
# Where the archive reads each task's class and metadata: the task
# directories the tree holds. A task the tree no longer holds, such as a
# small-obstacle or teams task, has no class there and pairs with nothing.
ARCHIVE_TASKS = HERE / "tasks" / "foe-tree"
# The order arms are listed in, which is the order the results documents use.
ARM_ORDER: tuple[str, ...] = ("foe-lean", "foe-configured", "foe-undivided", "foe-unverified", "codex-equivalent", "codex-default", "codex-single", "codex-multi", "foe-ablated")
IMPOSSIBLE_CLASSES: tuple[str, ...] = ("contradictory", "missing-capability", "non-terminating")
SCORINGS: tuple[int, ...] = (1, 2)
# The price of a cached input token relative to an uncached one, at which the
# autonomy results document weighs the two.
CACHE_PRICE = 0.1
# The run whose foe-configured records stand beside foe-lean for the tasks
# whose check suite hangs, and the class those tasks hold.
HANGING_RUN, HANGING_CLASS = "verifier-timeout", "non-terminating"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{path} is absent; the archive needs it") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not JSON: {exc}") from exc


def _attempt_key(entry: dict[str, Any]) -> tuple[str, str, int]:
    return str(entry["task"]), str(entry["arm"]), int(entry["attempt"])


def archive_tasks(tasks_dir: Path = ARCHIVE_TASKS) -> dict[str, dict[str, Any]]:
    """Each task directory's `task.json`, by task name."""
    return {path.parent.name: _read_json(path) for path in sorted(tasks_dir.glob("*/task.json"))}


def load_archive(results: Path, tasks: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Every archived attempt as a record the report reads, by run, with both scoring versions' cells and its condition.

    The array states the cell the run computed, which is scoring version 1;
    the rescored file must state the same cell as `cell_scoring_1`, or the
    two files describe two gradings and the archive is refused.
    """
    runs: dict[str, list[dict[str, Any]]] = {}
    for run_name, paths in ARCHIVE_RUNS.items():
        array = _read_json(results / paths["array"])
        rescored = {_attempt_key(entry): entry for entry in _read_json(results / paths["rescored"])}
        # A conditions file is the object `conditions.py` summarizes a run into; its `attempts` list holds one entry per attempt.
        conditions_file = _read_json(results / paths["conditions"])
        if not isinstance(conditions_file, dict) or not isinstance(conditions_file.get("attempts"), list):
            raise ValueError(f"{results / paths['conditions']}: key attempts is absent or not a list")
        conditions = {_attempt_key(entry): entry for entry in conditions_file["attempts"]}
        records = []
        for entry in array:
            key = _attempt_key(entry)
            if key not in rescored:
                raise ValueError(f"{results / paths['rescored']}: no entry for task {key[0]}, arm {key[1]}, attempt {key[2]}")
            if rescored[key]["cell_scoring_1"] != entry["cell"]:
                raise ValueError(f"{results / paths['rescored']}: task {key[0]}, arm {key[1]}, attempt {key[2]} has cell_scoring_1 {rescored[key]['cell_scoring_1']} where the array holds {entry['cell']}")
            if key not in conditions:
                raise ValueError(f"{results / paths['conditions']}: no entry for task {key[0]}, arm {key[1]}, attempt {key[2]}")
            task = tasks.get(key[0], {})
            records.append(
                {
                    "task": {
                        "name": key[0],
                        "family": task.get("family"),
                        "class_name": task.get("class_name"),
                        "correct_statuses": task.get("correct_statuses", []),
                        "metadata": task.get("metadata", {}),
                    },
                    "run": run_name,
                    "arm": key[1],
                    "attempt": key[2],
                    "cells": {1: rescored[key]["cell_scoring_1"], 2: rescored[key]["cell_scoring_2"]},
                    "classification": entry["cell"],
                    "condition": {name: conditions[key].get(name) for name in ("condition", "reached", "status")},
                    "reported": entry.get("reported") or {},
                    "totals": entry.get("totals") or {},
                    "grade": entry.get("grade") or {},
                    "infrastructure_error": None,
                    "arm_result": {},
                }
            )
        runs[run_name] = records
    return runs


def scored_as(records: list[dict[str, Any]], scoring: int) -> list[dict[str, Any]]:
    """The records with each classification set to the cell the scoring version gives."""
    return [dict(record, classification=record["cells"][scoring]) for record in records]


def _arms(records: list[dict[str, Any]]) -> list[str]:
    present = {record["arm"] for record in records}
    return [arm for arm in ARM_ORDER if arm in present] + sorted(present - set(ARM_ORDER))


def _total(records: list[dict[str, Any]], key: str) -> int | None:
    values = [_integer(record["totals"].get(key)) for record in records]
    return None if not values or any(value is None for value in values) else sum(value for value in values if value is not None)


def _seconds(records: list[dict[str, Any]]) -> float | None:
    values = [_integer(record["totals"].get("wall_ms")) for record in records]
    return None if not values or any(value is None for value in values) else sum(value for value in values if value is not None) / 1000


def spend(records: list[dict[str, Any]]) -> dict[str, float | None]:
    """The summed spend of a group of attempts: calls, input, cached and uncached input, output, and seconds, None where an attempt measured nothing."""
    inputs, cached = _total(records, "input_tokens"), _total(records, "cache_read_tokens")
    return {
        "attempts": len(records),
        "calls": _total(records, "model_calls"),
        "input": inputs,
        "cached": cached,
        "uncached": None if inputs is None or cached is None else inputs - cached,
        "output": _total(records, "output_tokens"),
        "seconds": _seconds(records),
    }


def _per(value: float | None, count: int) -> float | None:
    return None if value is None or not count else value / count


def _ratio(first: float | None, second: float | None) -> float | None:
    return None if first is None or not second else first / second


def _f(value: float | None, digits: int, scale: float = 1.0, suffix: str = "") -> str:
    return "—" if value is None else f"{value / scale:,.{digits}f}{suffix}"


def _mean_spend_cells(records: list[dict[str, Any]]) -> str:
    """The mean-per-attempt cost cells: calls, input, uncached input, cache share, output, seconds."""
    total = spend(records)
    count = total["attempts"]
    return (
        f"{_f(_per(total['calls'], count), 1)} | {_f(_per(total['input'], count), 1, 1000, 'k')} | {_f(_per(total['uncached'], count), 1, 1000, 'k')} | "
        f"{_f(None if not total['input'] or total['cached'] is None else 100 * total['cached'] / total['input'], 1, suffix='%')} | "
        f"{_f(_per(total['output'], count), 2, 1000, 'k')} | {_f(_per(total['seconds'], count), 1)}"
    )


def _weighted(total: dict[str, float | None]) -> float | None:
    """Uncached input plus cached input at CACHE_PRICE, the input a price weighs."""
    return None if total["uncached"] is None or total["cached"] is None else total["uncached"] + CACHE_PRICE * total["cached"]


def _total_spend_cells(total: dict[str, float | None]) -> str:
    """The summed cost cells: calls, input, uncached input, cache share, output, seconds, input per call, and price-weighted input."""
    weighted = _weighted(total)
    share = None if not total["input"] or total["cached"] is None else 100 * total["cached"] / total["input"]
    return (
        f"{_f(total['calls'], 0)} | {_f(total['input'], 3, 1e6, 'M')} | {_f(total['uncached'], 3, 1e6, 'M')} | {_f(share, 1, suffix='%')} | "
        f"{_f(total['output'], 1, 1000, 'k')} | {_f(total['seconds'], 1)} | {_f(_ratio(total['input'], total['calls']), 1, 1000, 'k')} | {_f(weighted, 3, 1e6, 'M')}"
    )


def _cells_table(title: str, records: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        f"### {title}",
        "",
        "| arm | scoring version | correct-completion | correct-stop | wrong-stop | killed | false-completion | damage | actionable |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for arm in _arms(records):
        group = [record for record in records if record["arm"] == arm]
        for scoring in SCORINGS:
            cells = [record["cells"][scoring] for record in group]
            counts = " | ".join(str(cells.count(cell)) for cell in protocol.SEVERITY)
            actionable = sum(cell in ACTIONABLE_CELLS for cell in cells)
            lines.append(f"| {arm} | {scoring} | {counts} | {actionable}/{len(cells)} |")
    return lines


def _impossible_table(title: str, records: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        f"### {title}",
        "",
        "| arm | scoring version | impossible tasks stopped correctly | share | " + " | ".join(IMPOSSIBLE_CLASSES) + " |",
        "|---|---:|---|---:|" + "---:|" * len(IMPOSSIBLE_CLASSES),
    ]
    for arm in _arms(records):
        group = [record for record in records if record["arm"] == arm and class_name(record) in IMPOSSIBLE_CLASSES]
        for scoring in SCORINGS:
            stopped = [record for record in group if record["cells"][scoring] == protocol.CORRECT_STOP]
            by_class = " | ".join(str(sum(1 for record in stopped if class_name(record) == name)) for name in IMPOSSIBLE_CLASSES)
            share = _f(100 * len(stopped) / len(group) if group else None, 1, suffix="%")
            lines.append(f"| {arm} | {scoring} | {len(stopped)} of {len(group)} | {share} | {by_class} |")
    return lines


def _p(value: float | None) -> str:
    return "—" if value is None else f"{value:.6f}"


def _pairs_tables(title: str, comparisons: list[tuple[str, str, int, dict[str, Any]]]) -> list[str]:
    """One comparison per declared pair and scoring version, by construction and then by task."""
    lines = [
        "",
        f"### {title}: by construction",
        "",
        "| first | second | scoring version | constructions | first better | second better | tied | discordant constructions | sign-test p | actionable difference by construction |",
        "|---|---|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    for first, second, scoring, pair in comparisons:
        sign = pair["sign_test"]
        lines.append(
            f"| {first} | {second} | {scoring} | {pair['construction_count']} | {sign['positive']} | {sign['negative']} | {sign['ties']} | "
            f"{', '.join(sign['discordant_constructions']) or 'none'} | {_p(sign['p'])} | {_interval(pair['actionable_difference'])} |"
        )
    lines.extend(
        [
            "",
            f"### {title}: by task, the secondary line",
            "",
            "| first | second | scoring version | tasks | pairs | both | only first | only second | neither | McNemar p | actionable difference by task |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for first, second, scoring, pair in comparisons:
        counts = pair["actionable_pairs"]
        lines.append(
            f"| {first} | {second} | {scoring} | {pair['task_count']} | {pair['pairs']} | {counts['both']} | {counts['only_first']} | {counts['only_second']} | "
            f"{counts['neither']} | {_p(pair['mcnemar']['p'])} | {_interval(pair['actionable_difference_by_task'])} |"
        )
    lines.extend(["", f"### {title}: restricted to pairs whose controls reached their conditions"])
    lines.extend(restricted_markdown(comparisons))
    return lines


def _compare_all(pairs: list[tuple[str, str]], records: list[dict[str, Any]], resamples: int, seed: int) -> list[tuple[str, str, int, dict[str, Any]]]:
    out = []
    for first, second in pairs:
        for scoring in SCORINGS:
            scored = scored_as(records, scoring)
            out.append((first, second, scoring, compare([r for r in scored if r["arm"] == first], [r for r in scored if r["arm"] == second], resamples, seed)))
    return out


def _cost_by_class_table(title: str, records: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        f"### {title}",
        "",
        "Means per attempt. Uncached input is input less cache reads; cache is the share of input read from cache.",
        "",
        "| class | arm | attempts | calls | input | uncached input | cache | output | seconds |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    classes = sorted({class_name(record) or "unrecorded" for record in records}, key=lambda name: (name != SOLVABLE_CLASS, name))
    for name in classes:
        for arm in _arms(records):
            group = [record for record in records if record["arm"] == arm and (class_name(record) or "unrecorded") == name]
            if group:
                lines.append(f"| {name} | {arm} | {len(group)} | {_mean_spend_cells(group)} |")
    return lines


def _totals_table(title: str, groups: list[tuple[str, list[dict[str, Any]]]]) -> list[str]:
    lines = [
        "",
        f"### {title}",
        "",
        f"Sums over the attempts. Input per call is input over calls; price-weighted input counts a cached token at {CACHE_PRICE} of an uncached one.",
        "",
        "| arms | attempts | calls | input | uncached input | cache | output | seconds | input per call | price-weighted input |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, group in groups:
        lines.append(f"| {name} | {len(group)} | {_total_spend_cells(spend(group))} |")
    return lines


def _ratio_table(title: str, rows: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]]) -> list[str]:
    lines = [
        "",
        f"### {title}",
        "",
        "Each ratio is the first group's sum over the second's.",
        "",
        "| groups | calls | input | uncached input | output | seconds | price-weighted input |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, first, second in rows:
        a, b = spend(first), spend(second)
        a["weighted"], b["weighted"] = _weighted(a), _weighted(b)
        lines.append(" | ".join([f"| {name}"] + [_f(_ratio(a[key], b[key]), 3) for key in ("calls", "input", "uncached", "output", "seconds", "weighted")]) + " |")
    return lines


def _reached(condition: dict[str, Any]) -> str:
    """The reached column: yes, no, or the wait's status where its entry is not established."""
    if condition.get("status") == NOT_ESTABLISHED:
        return f"no, wait {NOT_ESTABLISHED}"
    return _flag(condition["reached"])


def _attempts_table(title: str, records: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        f"### {title}",
        "",
        "| run | task | arm | attempt | cell, scoring 1 | cell, scoring 2 | condition | reached | reported | code | agents | calls | input | uncached input | output | seconds | runtime verifications | check calls | block calls |",
        "|---|---|---|---:|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for record in records:
        totals = record["totals"]
        by_name = totals.get("tool_calls_by_name") or {}
        one = spend([record])
        condition = record["condition"]
        lines.append(
            f"| {record['run']} | {task_name(record)} | {record['arm']} | {record['attempt']} | {record['cells'][1]} | {record['cells'][2]} | "
            f"{condition['condition'] or '—'} | {_reached(condition)} | {reported_status(record) or '—'} | {record['reported'].get('code') or '—'} | "
            f"{_f(_integer(totals.get('agents')), 0)} | {_f(one['calls'], 0)} | {_f(one['input'], 0)} | {_f(one['uncached'], 0)} | {_f(one['output'], 0)} | "
            f"{_f(one['seconds'], 1)} | {by_name.get(VERIFICATION_EVENT, 0)} | {by_name.get(VERIFIER_TOOL, 0)} | {by_name.get(BLOCK_TOOL, 0)} |"
        )
    return lines


def _mechanisms_table(title: str, records: list[dict[str, Any]]) -> list[str]:
    """How many attempts of each arm recorded a runtime verification, a check call, and a block call, from each attempt's tool-call counts."""
    lines = [
        "",
        f"### {title}",
        "",
        "| arm | attempts | with a runtime verification | with a check call | with a block call | reported codes of the blocked attempts |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for arm in _arms(records):
        group = [record for record in records if record["arm"] == arm]
        names = [record["totals"].get("tool_calls_by_name") or {} for record in group]
        codes: dict[str, int] = {}
        for record in group:
            if reported_status(record) == protocol.BLOCKED:
                code = str(record["reported"].get("code"))
                codes[code] = codes.get(code, 0) + 1
        lines.append(
            f"| {arm} | {len(group)} | {sum(1 for item in names if item.get(VERIFICATION_EVENT))} | {sum(1 for item in names if item.get(VERIFIER_TOOL))} | "
            f"{sum(1 for item in names if item.get(BLOCK_TOOL))} | {_counts(dict(sorted(codes.items())))} |"
        )
    return lines


def _units_table(title: str, records: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        f"### {title}",
        "",
        "| arm | task | reported | code | cell, scoring 1 | cell, scoring 2 | units passing | agents | calls |",
        "|---|---|---|---|---|---|---|---:|---:|",
    ]
    for arm in _arms(records):
        for record in sorted((record for record in records if record["arm"] == arm), key=task_name):
            units = record["grade"].get("units")
            passing = "—" if not isinstance(units, dict) else f"{sum(1 for value in units.values() if value)} of {len(units)}"
            lines.append(
                f"| {arm} | {task_name(record)} | {reported_status(record) or '—'} | {record['reported'].get('code') or '—'} | {record['cells'][1]} | "
                f"{record['cells'][2]} | {passing} | {_f(_integer(record['totals'].get('agents')), 0)} | {_f(_integer(record['totals'].get('model_calls')), 0)} |"
            )
    return lines


def lean_baseline(runs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """foe-configured's record for each task foe-lean ran: the autonomy run's where the task's check suite returns, and the verifier-timeout run's where it hangs."""
    tasks = {task_name(record) for record in runs["lean"]}
    chosen = []
    for record in runs["autonomy"] + runs[HANGING_RUN]:
        if record["arm"] != "foe-configured" or task_name(record) not in tasks:
            continue
        hangs = class_name(record) == HANGING_CLASS
        if (record["run"] == HANGING_RUN) == hangs:
            chosen.append(record)
    return chosen


def archive_markdown(results: Path, tasks: dict[str, dict[str, Any]] | None = None, resamples: int = DEFAULT_RESAMPLES, seed: int = DEFAULT_SEED) -> str:
    """Every numeric table of the dated results documents, recomputed from the committed arrays, the rescored files, and the conditions files."""
    tasks = archive_tasks() if tasks is None else tasks
    runs = load_archive(results, tasks)
    autonomy = runs["autonomy"]
    lean = runs["lean"]
    baseline = lean_baseline(runs)
    lean_pair = baseline + lean
    codex_equivalent = [record for record in autonomy if record["arm"] == "codex-equivalent"]
    everything = [record for records in runs.values() for record in records]
    autonomy_pairs = [pair for pair in DECLARED_PAIRS[AUTONOMY_FAMILY] if {pair[0], pair[1]} <= {record["arm"] for record in autonomy}]
    constructions = sorted({record_construction(record) for record in autonomy})
    lines = [
        "# Tables of the dated campaigns",
        "",
        "`report.py --archive` writes this file from the committed arrays, the rescored files, and the conditions files under this directory, "
        "and from the task directories under `tasks/foe-tree/`. Every figure of `autonomy-2026-09-13.md` and `enforcement-pressure-cases-2026-09-13.md` that "
        "a table states is one of the figures below. Scoring version 1 is the cell each run computed; version 2 adds the rule that a stop "
        "which changed a path the task preserves is damage. Seconds are each attempt's recorded wall clock.",
        "",
        f"The fifteen autonomy tasks form {len(constructions)} constructions: {', '.join(constructions)}.",
        "",
        "## Cells",
    ]
    lines += _cells_table("Autonomy run", autonomy)
    for case in ("small-obstacle", "verifier-timeout", "lean", "budget-bounded", "budget-bounded-warning", "teams-fan-out", "teams-fan-out-generous"):
        lines += _cells_table(f"Enforcement-pressure cases, {case}", runs[case])
    lines += ["", "## Impossible tasks by class"]
    lines += _impossible_table("Autonomy run", autonomy)
    lines += _impossible_table("foe-lean against foe-configured's records on the same tasks", lean_pair)
    lines += ["", "## Paired comparisons"]
    lines += _pairs_tables("Autonomy run, declared pairs", _compare_all(autonomy_pairs, autonomy, resamples, seed))
    lines += _pairs_tables("foe-configured against foe-lean", _compare_all([("foe-configured", "foe-lean")], lean_pair, resamples, seed))
    by_construction = detectable_difference(len(constructions))
    by_attempts = detectable_difference_in_attempts(len({task_name(record) for record in autonomy}), len({task_name(record) for record in autonomy}))
    lines += [
        "",
        f"Intervals come from {resamples} cluster-bootstrap resamples, seed {seed}. The sign test needs {by_construction['min_discordant_constructions']} "
        f"constructions differing in one direction; over {by_construction['constructions']} constructions the smallest detectable difference is "
        f"{_f(by_construction['difference'], 2)}. The task-level McNemar test needs {by_attempts['min_discordant_pairs']} discordant pairs; over "
        f"{by_attempts['paired_attempts']} paired attempts that is a difference of {_f(by_attempts['difference'], 2)}.",
        "",
        _non_inferiority_sentence(non_inferiority(scored_as(autonomy, 2))),
    ]
    lines += ["", "## Cost"]
    lines += _cost_by_class_table("Autonomy run, per class", autonomy)
    lines += _totals_table("Autonomy run, totals per arm", [(arm, [r for r in autonomy if r["arm"] == arm]) for arm in _arms(autonomy)] + [("codex-equivalent and codex-default", [r for r in autonomy if r["arm"].startswith("codex-")])])
    lines += _ratio_table(
        "Autonomy run, foe-configured over codex-equivalent",
        [("all tasks", [r for r in autonomy if r["arm"] == "foe-configured"], codex_equivalent)]
        + [(name, [r for r in autonomy if r["arm"] == "foe-configured" and class_name(r) == name], [r for r in codex_equivalent if class_name(r) == name]) for name in (SOLVABLE_CLASS,) + IMPOSSIBLE_CLASSES],
    )
    lines += _cost_by_class_table("Small obstacle, per arm", runs["small-obstacle"])
    lines += _ratio_table("Small obstacle, foe-lean over codex-equivalent", [("foe-lean over codex-equivalent", [r for r in runs["small-obstacle"] if r["arm"] == "foe-lean"], [r for r in runs["small-obstacle"] if r["arm"] == "codex-equivalent"])])
    lines += ["", "## Lean ratios"]
    lines += _totals_table("foe-configured's records and foe-lean, totals over the fifteen tasks", [("foe-configured", baseline), ("foe-lean", lean), ("codex-equivalent", codex_equivalent)])
    lines += _ratio_table(
        "foe-lean over foe-configured, and over codex-equivalent",
        [
            ("foe-lean over foe-configured, all tasks", lean, baseline),
            ("foe-lean over foe-configured, solvable tasks", [r for r in lean if class_name(r) == SOLVABLE_CLASS], [r for r in baseline if class_name(r) == SOLVABLE_CLASS]),
            ("foe-lean over codex-equivalent, all tasks", lean, codex_equivalent),
        ],
    )
    lines += ["", "## Budget-bounded outcomes"]
    lines += _attempts_table("Budget-bounded runs, per attempt", runs["budget-bounded"] + runs["budget-bounded-warning"])
    lines += ["", "## Teams units passing"]
    lines += _units_table("Teams fan-out at the tasks' ceiling", runs["teams-fan-out"])
    lines += _units_table("Teams fan-out at the doubled ceiling", runs["teams-fan-out-generous"])
    lines += ["", "## Mechanisms"]
    lines += _mechanisms_table("Autonomy run, from each attempt's tool-call counts", autonomy)
    lines += _mechanisms_table("Verifier-timeout run", runs["verifier-timeout"])
    lines += ["", "## Every attempt"]
    lines += _attempts_table("Every archived attempt", everything)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("document", type=Path, nargs="?", help="the run document run.py ran; the report reads its out directory")
    parser.add_argument("--archive", type=Path, help=f"a results directory: recompute every table of the dated campaigns from its committed files into {ARCHIVE_TABLES}")
    parser.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES, help=f"bootstrap resamples; default {DEFAULT_RESAMPLES}")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"the bootstrap generator's seed; default {DEFAULT_SEED}")
    args = parser.parse_args(argv)
    if (args.document is None) == (args.archive is None):
        parser.error("name either a run document or --archive RESULTS_DIR")
    if args.archive is not None:
        try:
            rendered = archive_markdown(args.archive, resamples=args.resamples, seed=args.seed)
        except (ValueError, FileNotFoundError) as exc:
            print(f"cross harness report: {exc}", file=sys.stderr)
            return 2
        (args.archive / ARCHIVE_TABLES).write_text(rendered, encoding="utf-8")
        print(f"tables: {args.archive / ARCHIVE_TABLES}")
        return 0
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
