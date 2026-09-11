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

    report.py --records DIR [--out DIR] [--resamples N] [--seed N]

The report is written as `report.json` and `report.md` under `--out`, by
default the parent of the records directory, and the Markdown is printed.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent / "tasks"))

import protocol  # noqa: E402

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

Predicate = Callable[[dict[str, Any]], bool]


def load_records(records_dir: Path) -> list[dict[str, Any]]:
    """Every record under the directory, in path order; errors name the file and the missing key."""
    if not records_dir.is_dir():
        raise FileNotFoundError(f"--records {records_dir} is not a directory")
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
        "per_arm": {arm: arm_metrics(by_arm[arm]) for arm in arms},
        "per_task": per_task(records, arms),
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
    parser.add_argument("--records", required=True, type=Path, help="the records directory run.py wrote")
    parser.add_argument("--out", type=Path, default=None, help="where report.json and report.md are written; the records directory's parent when omitted")
    parser.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES, help=f"bootstrap resamples; default {DEFAULT_RESAMPLES}")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"the bootstrap generator's seed; default {DEFAULT_SEED}")
    args = parser.parse_args(argv)
    try:
        records = load_records(args.records.resolve())
        if not records:
            raise ValueError(f"no record under {args.records.resolve()}")
        report = build(records, args.resamples, args.seed)
    except (ValueError, FileNotFoundError) as exc:
        print(f"cross harness report: {exc}", file=sys.stderr)
        return 2
    out = (args.out or args.records.resolve().parent).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rendered = markdown(report)
    (out / "report.md").write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    print(f"\nreport: {out / 'report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
