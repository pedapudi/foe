#!/usr/bin/python3
"""Unit tests for the report: synthetic records, no runner, no model."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import report  # noqa: E402


def record(
    task: str,
    arm: str,
    attempt: int,
    cell: str | None,
    status: str = "completed",
    code: str | None = None,
    tokens: tuple[int, int] | None = (1000, 200),
    wall_ms: int | None = 30_000,
    correct_statuses: tuple[str, ...] = ("completed",),
    correct_codes: tuple[str, ...] = (),
    class_name: str = "solvable",
) -> dict:
    totals = {
        "model_calls": 3,
        "input_tokens": None if tokens is None else tokens[0],
        "output_tokens": None if tokens is None else tokens[1],
        "wall_ms": wall_ms,
    }
    return {
        # Each synthetic task is removed from a commit of its own, so it is its own construction.
        "task": {
            "name": task,
            "family": "autonomy",
            "class_name": class_name,
            "correct_statuses": list(correct_statuses),
            "correct_codes": list(correct_codes),
            "metadata": {"source": {"commit": f"commit-{task}", "parent": f"parent-{task}"}},
        },
        "arm": arm,
        # Every record names the harness its arm ran, as `run.py` writes it.
        "harness": "foe" if arm.startswith("foe") else "codex",
        "attempt": attempt,
        "classification": cell,
        "infrastructure_error": None if cell is not None else "foe wrote no episode log",
        "reported": {"status": status, "code": code, "evidence": []},
        "totals": totals if cell is not None else None,
        "arm_result": {"started_ms": 0, "ended_ms": 45_000},
    }


BUDGET = {"model_calls": 30, "input_tokens": 100_000, "output_tokens": 20_000, "seconds": 600}


def tool_call(name: str, summary: str | None = None, is_error: bool = False, started_ms: int = 1) -> dict:
    """One entry of the trajectory's tool-call stream, as `trajectory.py` writes it."""
    return {"name": name, "started_ms": started_ms, "ended_ms": started_ms + 1, "is_error": is_error, "arguments_digest": None, "summary": summary}


def shell_command(text: str, exit_code: int | None) -> dict:
    return {"started_ms": 1, "ended_ms": 2, "text": text, "exit_code": exit_code, "paths_named": [], "denial": False}


def trajectory_of(
    harness: str = "foe",
    status: str = "completed",
    code: str | None = None,
    calls: list[dict] | None = None,
    commands: tuple[dict, ...] = (),
    compactions: int = 0,
    ended_ms: int = 30_000,
) -> dict:
    """A one-agent trajectory; `calls` of None leaves the tool-call key out, as a record written before the stream does."""
    agent_: dict = {
        "id": "root",
        "parent_id": None,
        "depth": 0,
        "role": "root",
        "started_ms": 0,
        "ended_ms": ended_ms,
        "model_calls": [],
        "commands": list(commands),
        "file_changes": [],
        "compactions": [{"at_ms": 10, "tokens_before": None} for _ in range(compactions)],
    }
    if calls is not None:
        agent_["tool_calls"] = list(calls)
    return {"harness": harness, "identity": {}, "agents": [agent_], "outcome": {"status": status, "code": code, "value": None, "ended_ms": ended_ms}, "route": "subscription"}


class McNemar(unittest.TestCase):
    def test_the_known_case_of_one_against_nine_discordant_pairs(self) -> None:
        # Ten discordant pairs split one to nine: the tail is (1 + 10) / 1024 and the two-sided value twice that.
        self.assertAlmostEqual(report.mcnemar_exact(1, 9), 22 / 1024)
        self.assertAlmostEqual(report.mcnemar_exact(9, 1), 22 / 1024)

    def test_an_even_split_or_no_discordant_pair_gives_one(self) -> None:
        self.assertEqual(report.mcnemar_exact(0, 0), 1.0)
        self.assertEqual(report.mcnemar_exact(3, 3), 1.0)
        self.assertEqual(report.mcnemar_exact(0, 1), 1.0)

    def test_a_one_sided_split_is_the_binomial_extreme(self) -> None:
        self.assertAlmostEqual(report.mcnemar_exact(0, 8), 2 / 256)
        with self.assertRaises(ValueError):
            report.mcnemar_exact(-1, 2)


class Bootstrap(unittest.TestCase):
    def test_a_constant_difference_has_a_degenerate_interval(self) -> None:
        clusters = {task: [(1.0, 0.0), (1.0, 0.0)] for task in ("a", "b", "c")}
        result = report.cluster_bootstrap(clusters, resamples=200, seed=1)
        self.assertEqual((result["observed"], result["lower"], result["upper"]), (1.0, 1.0, 1.0))
        self.assertEqual((result["clusters"], result["pairs"]), (3, 6))

    def test_the_interval_holds_the_observed_difference_and_repeats_under_one_seed(self) -> None:
        clusters = {"a": [(1.0, 0.0), (1.0, 1.0)], "b": [(0.0, 1.0)], "c": [(1.0, 0.0), (0.0, 0.0)], "d": [(1.0, 1.0)]}
        first = report.cluster_bootstrap(clusters, resamples=500, seed=7)
        second = report.cluster_bootstrap(clusters, resamples=500, seed=7)
        self.assertEqual(first, second)
        # The mean of the per-cluster mean differences, 0.5, -1, 0.5 and 0, weighs every cluster the same.
        self.assertAlmostEqual(first["observed"], 0.0)
        self.assertLessEqual(first["lower"], first["observed"])
        self.assertLessEqual(first["observed"], first["upper"])
        self.assertLess(first["lower"], first["upper"])

    def test_no_cluster_and_a_bad_resample_count_are_handled(self) -> None:
        self.assertIsNone(report.cluster_bootstrap({}, 10, 0)["observed"])
        with self.assertRaises(ValueError):
            report.cluster_bootstrap({"a": [(1.0, 0.0)]}, 0, 0)


class Metrics(unittest.TestCase):
    def records(self) -> list[dict]:
        unsolvable = dict(correct_statuses=("blocked",), class_name="contradictory")
        return [
            record("solvable-1", "foe-configured", 1, "correct-completion"),
            record("solvable-1", "foe-configured", 2, "false-completion"),
            record("solvable-2", "foe-configured", 1, "wrong-stop", status="blocked", code="goal-unreachable"),
            record("contradictory-1", "foe-configured", 1, "correct-stop", status="blocked", code="ambiguous-task", tokens=(4000, 500), wall_ms=20_000, **unsolvable),
            record("contradictory-1", "foe-configured", 2, "killed", status="killed", tokens=None, wall_ms=None, **unsolvable),
            record("contradictory-1", "foe-configured", 3, None, **unsolvable),
            record("solvable-1", "codex-equivalent", 1, "correct-completion"),
            record("solvable-1", "codex-equivalent", 2, "correct-completion"),
            record("solvable-2", "codex-equivalent", 1, "damage"),
            record("contradictory-1", "codex-equivalent", 1, "false-completion", **unsolvable),
            record("contradictory-1", "codex-equivalent", 2, "correct-stop", status="blocked", code="ambiguous-task", tokens=(8000, 1000), wall_ms=60_000, **unsolvable),
        ]

    def test_the_arm_rates_count_scored_attempts_alone(self) -> None:
        foe = report.arm_metrics([item for item in self.records() if item["arm"] == "foe-configured"])
        self.assertEqual((foe["attempts"], foe["scored"], foe["infrastructure_failures"]), (6, 5, 1))
        self.assertEqual(foe["actionable"], {"rate": 2 / 5, "count": 2, "of": 5})
        self.assertEqual(foe["false_completion"]["count"], 1)
        # Two blocked reports, one of them a correct stop.
        self.assertEqual(foe["block_precision"], {"rate": 0.5, "count": 1, "of": 2})
        # Two scored attempts on the task that accepts blocked, one stopped that way.
        self.assertEqual(foe["block_recall"], {"rate": 0.5, "count": 1, "of": 2})
        self.assertEqual(foe["killed"]["count"], 1)
        self.assertEqual(foe["damage"]["count"], 0)
        self.assertEqual(foe["cells"]["correct-stop"], 1)
        # The killed attempt on the same task never found out that it cannot be done, so the one correct stop stands alone.
        stop = foe["cost_to_stop"]
        self.assertEqual(stop["attempts"], 1)
        self.assertEqual((stop["tokens_mean"], stop["tokens_measured"]), (4500.0, 1))
        self.assertEqual((stop["seconds_mean"], stop["seconds_measured"]), (20.0, 1))

    def test_the_cost_to_stop_counts_correct_stops_alone(self) -> None:
        unsolvable = dict(correct_statuses=("blocked",), class_name="contradictory")
        stop = report.cost_to_stop(
            [
                record("contradictory-1", "foe-configured", 1, "false-completion", tokens=(90_000, 10_000), **unsolvable),
                record("contradictory-1", "foe-configured", 2, "correct-stop", status="blocked", code="ambiguous-task", tokens=(800, 200), wall_ms=12_000, **unsolvable),
                record("contradictory-1", "foe-configured", 3, "wrong-stop", status="blocked", code="goal-unreachable", tokens=(5_000, 500), **unsolvable),
                record("contradictory-1", "foe-configured", 4, "killed", status="killed", tokens=(50_000, 5_000), **unsolvable),
            ]
        )
        self.assertEqual((stop["attempts"], stop["tokens_measured"], stop["seconds_measured"]), (1, 1, 1))
        self.assertEqual((stop["tokens_mean"], stop["tokens_median"], stop["seconds_mean"]), (1000.0, 1000.0, 12.0))
        empty = report.cost_to_stop([record("contradictory-1", "codex-equivalent", 1, "false-completion", **unsolvable)])
        self.assertEqual((empty["attempts"], empty["tokens_mean"], empty["seconds_mean"]), (0, None, None))

    def test_the_per_task_table_lists_cells_per_arm(self) -> None:
        table = report.per_task(self.records(), ["foe-configured", "codex-equivalent"])
        self.assertEqual(list(table), ["contradictory-1", "solvable-1", "solvable-2"])
        self.assertEqual(table["solvable-1"]["arms"]["foe-configured"]["cells"], {"correct-completion": 1, "false-completion": 1})
        self.assertEqual(table["solvable-1"]["arms"]["codex-equivalent"]["actionable"]["rate"], 1.0)
        self.assertEqual(table["contradictory-1"]["class_name"], "contradictory")
        self.assertEqual(table["contradictory-1"]["arms"]["foe-configured"]["attempts"], 3)
        self.assertEqual(table["contradictory-1"]["arms"]["foe-configured"]["scored"], 2)

    def test_the_pair_uses_attempts_scored_under_both_arms(self) -> None:
        records = self.records()
        comparison = report.compare([item for item in records if item["arm"] == "foe-configured"], [item for item in records if item["arm"] == "codex-equivalent"], resamples=300, seed=3)
        # Pairs: solvable-1 attempts 1 and 2, solvable-2 attempt 1, contradictory-1 attempts 1 and 2; attempt 3 is faulted under foe.
        self.assertEqual(comparison["pairs"], 5)
        self.assertEqual(comparison["tasks"], ["contradictory-1", "solvable-1", "solvable-2"])
        self.assertEqual(comparison["actionable_pairs"], {"both": 1, "only_first": 1, "only_second": 2, "neither": 1})
        self.assertAlmostEqual(comparison["mcnemar_p"], 1.0)
        # Per construction: solvable-1 -0.5, solvable-2 0, contradictory-1 0; the mean weighs each construction once.
        self.assertAlmostEqual(comparison["actionable_difference"]["observed"], -0.5 / 3)
        self.assertAlmostEqual(comparison["false_completion_difference"]["observed"], 0.0)

    def test_the_report_and_its_markdown_name_every_arm_and_task(self) -> None:
        built = report.build(self.records(), resamples=100, seed=0)
        self.assertEqual(built["arms"], ["foe-configured", "codex-equivalent"])
        self.assertEqual(built["families"], ["autonomy"])
        self.assertEqual(len(built["pairs"]), 1)
        rendered = report.markdown(built)
        self.assertIn("| `foe-configured` | 6 | 5 | 1 | 0 | 0.40 (2/5) |", rendered)
        # The stop cost is input tokens, so the correct stop of 4000 input and 500 output tokens reads as 4,000.
        self.assertIn("| 4,000 | 20.0 |", rendered)
        self.assertIn("| `contradictory-1` | contradictory | `codex-equivalent` |", rendered)
        # The headline row states the constructions, then the tasks and pairs; the secondary row keeps the task-level McNemar test.
        self.assertIn("| `foe-configured` | `codex-equivalent` | the runtime, under one stated procedure | 3 | 3 | 5 | 0 | 1 | 2 | 1.0000 |", rendered)
        self.assertIn("| `foe-configured` | `codex-equivalent` | 3 | 5 | 1 | 1 | 2 | 1 | 1.0000 |", rendered)
        self.assertIn("-0.17 [", rendered)
        self.assertIn("Secondary, task-level line.", rendered)
        faulted = report.build([record("solvable-1", "foe-configured", 1, None)], resamples=10, seed=0)
        self.assertIn("| `foe-configured` | 1 | 0 | 1 | 0 | — | — | — | — | — | — | — | — |", report.markdown(faulted))
        self.assertIn("No declared pair of arms shares a scored attempt", report.markdown(faulted))

    def test_only_declared_pairs_are_compared_and_each_states_its_task_count(self) -> None:
        records = self.records()
        # codex-default shares every task with both arms, and only its declared pair with codex-equivalent is formed.
        records += [record("solvable-1", "codex-default", 1, "correct-completion"), record("solvable-2", "codex-default", 1, "false-completion")]
        built = report.build(records, resamples=50, seed=0)
        formed = [(pair["first"], pair["second"]) for pair in built["pairs"]]
        self.assertEqual(formed, [("foe-configured", "codex-equivalent"), ("codex-equivalent", "codex-default")])
        self.assertNotIn(("foe-configured", "codex-default"), formed)
        by_pair = {(pair["first"], pair["second"]): pair for pair in built["pairs"]}
        self.assertEqual(by_pair[("foe-configured", "codex-equivalent")]["task_count"], 3)
        self.assertEqual(by_pair[("foe-configured", "codex-equivalent")]["actionable_difference"]["clusters"], 3)
        self.assertEqual(by_pair[("codex-equivalent", "codex-default")]["task_count"], 2)
        self.assertEqual(by_pair[("codex-equivalent", "codex-default")]["tasks"], ["solvable-1", "solvable-2"])
        # Every other declared pair of the family is listed with the arm it lacks.
        not_formed = {(pair["first"], pair["second"]): pair["reason"] for pair in built["pairs_not_formed"]}
        self.assertEqual(set(not_formed), {("foe-configured", "foe-unverified"), ("foe-configured", "foe-ablated"), ("foe-ablated", "codex-equivalent"), ("foe-as-shipped", "codex-equivalent")})
        self.assertEqual(not_formed[("foe-configured", "foe-ablated")], "foe-ablated has no scored attempt")
        rendered = report.markdown(built)
        self.assertIn("| `codex-equivalent` | `codex-default` | 2 | 2 |", rendered)
        self.assertIn("- `foe-as-shipped` with `codex-equivalent` (autonomy): foe-as-shipped has no scored attempt", rendered)
        self.assertNotIn("| `foe-configured` | `codex-default` |", rendered)
        # A family that declares no pair forms none.
        teams = [dict(record("fan-out-1", arm, 1, "correct-completion"), task={"name": "fan-out-1", "family": "teams", "class_name": "fan-out", "correct_statuses": ["completed"], "metadata": {"source": {"commit": "c"}}}) for arm in ("foe-configured", "codex-multi", "codex-single")]
        built = report.build(teams, resamples=20, seed=0)
        self.assertEqual((built["pairs"], built["pairs_not_formed"]), ([], []))

    def test_a_not_applicable_attempt_is_counted_beside_the_scored_ones_and_enters_no_rate(self) -> None:
        skipped = dict(record("solvable-1", "foe-as-shipped", 1, None), not_applicable="the foe-as-shipped arm cannot take the tool roots task 'solvable-1' names")
        skipped["infrastructure_error"] = None
        metrics = report.arm_metrics([skipped, record("solvable-2", "foe-as-shipped", 1, "correct-completion")])
        self.assertEqual((metrics["attempts"], metrics["scored"], metrics["not_applicable"], metrics["infrastructure_failures"]), (2, 1, 1, 0))
        self.assertEqual(metrics["actionable"], {"rate": 1.0, "count": 1, "of": 1})
        rendered = report.markdown(report.build([skipped, record("solvable-2", "foe-as-shipped", 1, "correct-completion")], resamples=10, seed=0))
        self.assertIn("| `foe-as-shipped` | 2 | 1 | 0 | 1 | 1.00 (1/1) |", rendered)

    def test_main_reads_the_documents_out_writes_both_files_and_refuses_a_malformed_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # The document names its out directory relative to itself, as run.py resolves it.
            document = Path(tmp) / "runs" / "pilot.json"
            document.parent.mkdir()
            document.write_text(json.dumps({"tasks": "../tasks", "model": {"route": "subscription", "name": "m"}, "out": "../state/pilot"}), encoding="utf-8")
            out_dir = Path(tmp) / "state" / "pilot"
            records = out_dir / "records"
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = report.main([str(document)])
            self.assertEqual(status, 2)
            self.assertIn(f"the records directory {records} does not exist", err.getvalue())
            for item in self.records():
                path = records / item["task"]["name"] / item["arm"] / f"{item['attempt']:02d}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(item), encoding="utf-8")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = report.main([str(document), "--resamples", "50"])
            self.assertEqual(status, 0, err.getvalue())
            self.assertTrue((out_dir / "report.md").is_file())
            written = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(written["records"], 11)
            self.assertEqual(written["settings"]["resamples"], 50)
            self.assertIn("## Paired comparisons", out.getvalue())
            self.assertIn(f"report: {out_dir / 'report.json'}", out.getvalue())
            broken = records / "solvable-1" / "foe-configured" / "03.json"
            broken.write_text(json.dumps({"task": {"name": "solvable-1"}}), encoding="utf-8")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = report.main([str(document)])
            self.assertEqual(status, 2)
            self.assertIn(str(broken), err.getvalue())
            # A record whose task names a family without declared pairs is refused by path and family.
            unknown = record("solvable-1", "foe-configured", 3, "correct-completion")
            unknown["task"]["family"] = "other"
            broken.write_text(json.dumps(unknown), encoding="utf-8")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = report.main([str(document)])
            self.assertEqual(status, 2)
            self.assertIn("task.family is 'other'", err.getvalue())
            # A document with an unknown key is refused by the runner's rule before any record is read.
            document.write_text(json.dumps({"tasks": "../tasks", "records": str(records)}), encoding="utf-8")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = report.main([str(document)])
            self.assertEqual(status, 2)
            self.assertIn(f"{document}: key records is unknown", err.getvalue())


class PerClass(unittest.TestCase):
    def records(self) -> list[dict]:
        impossible = dict(correct_statuses=("blocked",), class_name="contradictory")
        return [
            record("solvable-1", "foe-configured", 1, "correct-completion", tokens=(1_000, 200), wall_ms=30_000),
            record("solvable-2", "foe-configured", 1, "false-completion", tokens=(3_000, 400), wall_ms=40_000),
            record("contradictory-1", "foe-configured", 1, "correct-stop", status="blocked", code="ambiguous-task", tokens=(800, 100), wall_ms=10_000, **impossible),
            record("contradictory-2", "foe-configured", 1, "false-completion", tokens=(20_000, 3_000), wall_ms=300_000, **impossible),
            record("contradictory-3", "foe-configured", 1, "killed", status="killed", tokens=(50_000, 6_000), wall_ms=600_000, **impossible),
        ]

    def test_every_rate_and_cost_is_stated_over_one_class(self) -> None:
        metrics = report.arm_metrics(self.records())
        self.assertEqual(sorted(metrics["per_class"]), ["contradictory", "solvable"])
        solvable, impossible = metrics["per_class"]["solvable"], metrics["per_class"]["contradictory"]
        # The headline rate mixes finishing a solvable task with stopping on one that cannot be done; the per-class rates separate them.
        self.assertEqual(metrics["actionable"], {"rate": 2 / 5, "count": 2, "of": 5})
        self.assertEqual(solvable["actionable"], {"rate": 0.5, "count": 1, "of": 2})
        self.assertEqual(impossible["actionable"], {"rate": 1 / 3, "count": 1, "of": 3})
        self.assertEqual((solvable["false_completion"]["count"], impossible["false_completion"]["count"]), (1, 1))
        self.assertEqual(impossible["killed"], {"rate": 1 / 3, "count": 1, "of": 3})
        self.assertEqual(impossible["block_precision"], {"rate": 1.0, "count": 1, "of": 1})
        self.assertEqual(impossible["cells"]["correct-stop"], 1)

    def test_the_cost_of_a_class_states_the_attempts_each_measure_rests_on(self) -> None:
        metrics = report.arm_metrics(self.records())
        impossible = metrics["per_class"]["contradictory"]["cost"]
        self.assertEqual(impossible["input_tokens"], {"mean": (800 + 20_000 + 50_000) / 3, "median": 20_000.0, "measured": 3})
        self.assertEqual(impossible["output_tokens"]["mean"], (100 + 3_000 + 6_000) / 3)
        self.assertEqual(impossible["model_calls"]["mean"], 3.0)
        self.assertEqual(impossible["seconds"], {"mean": (10 + 300 + 600) / 3, "median": 300.0, "measured": 3})
        # No record measured a cache read, so that measure rests on no attempt rather than on zero.
        self.assertEqual(metrics["per_class"]["solvable"]["cost"]["cache_read_tokens"], {"mean": None, "median": None, "measured": 0})

    def test_the_cost_without_a_stop_and_the_censoring_rate_stand_beside_the_cost_to_stop(self) -> None:
        metrics = report.arm_metrics(self.records())
        impossible = metrics["per_class"]["contradictory"]
        self.assertEqual((impossible["cost_to_stop"]["attempts"], impossible["cost_to_stop"]["tokens_mean"]), (1, 900.0))
        # The false completion and the killed run on the other two impossible tasks are what the cost to stop is read against.
        self.assertEqual(impossible["cost_without_stop"]["attempts"], 2)
        self.assertEqual(impossible["cost_without_stop"]["tokens_mean"], (23_000 + 56_000) / 2)
        self.assertEqual(impossible["censoring"], {"rate": 1 / 3, "count": 1, "of": 3, "exhausted": 0, "killed": 1})
        # A solvable task admits a completion, so it enters neither the counterfactual nor the censoring rate.
        solvable = metrics["per_class"]["solvable"]
        self.assertEqual((solvable["cost_without_stop"]["attempts"], solvable["censoring"]["rate"]), (0, None))

    def test_an_attempt_a_ceiling_ended_is_censored_and_one_that_stopped_is_not(self) -> None:
        impossible = dict(correct_statuses=("blocked",), class_name="non-terminating")
        exhausted = record("loop-1", "codex-equivalent", 1, "wrong-stop", status="exhausted", **impossible)
        exhausted["trajectory"] = trajectory_of(harness="codex", status="exhausted", code="input_tokens")
        # A correct stop reached a stop of its own, whatever its logs say afterwards.
        stopped = record("loop-2", "codex-equivalent", 1, "correct-stop", status="blocked", code="non-terminating-goal", **impossible)
        stopped["trajectory"] = trajectory_of(harness="codex", status="exhausted", code="seconds")
        self.assertEqual(report.censoring([exhausted, stopped]), {"rate": 0.5, "count": 1, "of": 2, "exhausted": 1, "killed": 0})

    def test_the_markdown_states_a_class_table_for_every_arm(self) -> None:
        rendered = report.markdown(report.build(self.records(), resamples=10, seed=0))
        self.assertIn("## Classes", rendered)
        self.assertIn("### `foe-configured`", rendered)
        self.assertIn("| contradictory | 3 | 3 | 0.33 (1/3) |", rendered)
        self.assertIn("| solvable | 2 | 2 | 0.50 (1/2) |", rendered)
        self.assertIn("input tokens are the primary measure", rendered)
        # Both stop columns are input tokens: the correct stop spent 800 of them, and the two attempts that did not stop 20,000 and 50,000.
        self.assertIn("| input tokens to stop | input tokens without a stop |", rendered)
        self.assertIn("| 800 (n=1) | 35,000 (n=2) |", rendered)


class Ceilings(unittest.TestCase):
    def bounded(self, name: str, cell: str, tokens: tuple[int, int], wall_ms: int, **fields: object) -> dict:
        attempt = record(name, "foe-configured", 1, cell, tokens=tokens, wall_ms=wall_ms, **fields)
        attempt["budget"] = dict(BUDGET)
        return attempt

    def test_every_total_is_measured_against_the_ceiling_the_record_carries(self) -> None:
        spent = self.bounded("solvable-1", "correct-completion", (50_000, 10_000), 300_000)
        exhausted = self.bounded("loop-1", "wrong-stop", (100_000, 5_000), 60_000, status="exhausted", correct_statuses=("blocked",), class_name="non-terminating")
        exhausted["trajectory"] = trajectory_of(status="exhausted", code="input_tokens")
        entry = report.ceilings([spent, exhausted])
        self.assertEqual(report.ceiling_use(spent), {"model_calls": 0.1, "input_tokens": 0.5, "output_tokens": 0.5, "seconds": 0.5})
        self.assertEqual(entry["utilization"]["input_tokens"], {"mean": 0.75, "max": 1.0, "measured": 2, "enforced": 2, "at_ceiling": 1})
        self.assertEqual(entry["at_ceiling"], {"rate": 0.5, "count": 1, "of": 2})
        # The limit that bound the exhausted attempt is the code of its trajectory's outcome.
        self.assertEqual((entry["exhausted"], entry["bound_by"]), (1, {"input_tokens": 1}))

    def test_a_record_without_a_ceiling_or_without_a_total_measures_nothing(self) -> None:
        bare = record("solvable-1", "codex-equivalent", 1, "correct-completion")
        self.assertEqual(report.ceiling_use(bare), {"model_calls": None, "input_tokens": None, "output_tokens": None, "seconds": None})
        entry = report.ceilings([bare])
        self.assertEqual(entry["utilization"]["seconds"], {"mean": None, "max": None, "measured": 0, "enforced": 0, "at_ceiling": 0})
        self.assertEqual(entry["at_ceiling"], {"rate": 0.0, "count": 0, "of": 1})

    def test_a_codex_attempt_past_the_model_call_number_reached_no_ceiling(self) -> None:
        # Codex takes token and wall-clock limits alone, so its model-call figure is measured against the task's number and nothing bounds it.
        attempt = record("solvable-1", "codex-equivalent", 1, "correct-completion", tokens=(1_000, 200), wall_ms=30_000)
        attempt["budget"] = dict(BUDGET)
        attempt["totals"] = dict(attempt["totals"], model_calls=45)
        self.assertEqual(report.enforced_ceilings(attempt), ("input_tokens", "output_tokens", "seconds"))
        self.assertEqual(report.ceiling_use(attempt)["model_calls"], 1.5)
        entry = report.ceilings([attempt])
        self.assertEqual(entry["utilization"]["model_calls"], {"mean": 1.5, "max": 1.5, "measured": 1, "enforced": 0, "at_ceiling": 0})
        self.assertEqual(entry["at_ceiling"], {"rate": 0.0, "count": 0, "of": 1})
        # The same totals under foe, whose runtime holds every budget key, are at a ceiling.
        held = dict(attempt, arm="foe-configured", harness="foe")
        self.assertEqual(report.ceilings([held])["at_ceiling"], {"rate": 1.0, "count": 1, "of": 1})
        self.assertEqual(report.ceilings([held])["utilization"]["model_calls"]["at_ceiling"], 1)
        rendered = report.markdown(report.build([attempt], resamples=10, seed=0))
        self.assertIn("1.50 max 1.50 (measured over 1, no ceiling)", rendered)
        # The per-attempt row states the highest use over the ceilings the attempt was held to.
        self.assertEqual(report.build([attempt], resamples=10, seed=0)["autonomy_attempts"][0]["ceiling_use_max"], 0.05)

    def test_the_markdown_names_the_ceiling_the_two_harnesses_do_not_share(self) -> None:
        rendered = report.markdown(report.build([self.bounded("solvable-1", "correct-completion", (50_000, 10_000), 300_000)], resamples=10, seed=0))
        self.assertIn("## Ceilings", rendered)
        self.assertIn("0.50 max 0.50 (0 of 1 at the ceiling)", rendered)
        self.assertIn("Codex takes token and wall-clock limits alone", rendered)


class Mechanisms(unittest.TestCase):
    def stopped(self) -> dict:
        return record(
            "contradictory-1",
            "foe-configured",
            1,
            "correct-stop",
            status="blocked",
            code="ambiguous-task",
            correct_statuses=("blocked",),
            correct_codes=("ambiguous-task",),
            class_name="contradictory",
        )

    def test_the_mechanism_columns_come_from_the_tool_call_stream(self) -> None:
        attempt = self.stopped()
        # Every summary is a string `normalize_foe.py` writes: a verifier result states its findings and its exit status, and a block call states the code alone.
        calls = [tool_call("check", summary="3 findings, exit 1", is_error=False), tool_call("check", summary="0 findings, exit 0"), tool_call("block", summary="ambiguous-task")]
        attempt["trajectory"] = trajectory_of(calls=calls, compactions=2)
        attempt["totals"] = dict(attempt["totals"], compactions=2, tool_calls=3, tool_calls_by_name={"check": 2, "block": 1})
        measure = report.mechanisms(attempt)
        self.assertEqual((measure["verifier_source"], measure["verifier_runs"], measure["verifier_cleared"]), ("tool", 2, True))
        self.assertEqual((measure["block_calls"], measure["block_codes"]), (1, ["ambiguous-task"]))
        self.assertEqual((measure["compactions"], measure["tool_calls"]), (2, 3))
        self.assertEqual(measure["tool_calls_by_name"], {"block": 1, "check": 2})
        metrics = report.mechanism_metrics([attempt])
        self.assertEqual(metrics["verifier_ran"], {"rate": 1.0, "count": 1, "of": 1})
        self.assertEqual(metrics["verifier_runs"], {"mean": 2.0, "measured": 1})
        self.assertEqual(metrics["verifier_cleared"], {"rate": 1.0, "count": 1, "of": 1})
        self.assertEqual((metrics["block_called"]["count"], metrics["block_codes"]), (1, {"ambiguous-task": 1}))
        self.assertEqual(metrics["tool_calls_by_name"], {"block": 1, "check": 2})

    def test_a_verifier_result_without_a_count_falls_back_to_the_error_flag(self) -> None:
        attempt = self.stopped()
        attempt["trajectory"] = trajectory_of(calls=[tool_call("check", summary="the checks passed")])
        self.assertIs(report.mechanisms(attempt)["verifier_cleared"], True)
        attempt["trajectory"] = trajectory_of(calls=[tool_call("check", summary=None, is_error=True)])
        self.assertIs(report.mechanisms(attempt)["verifier_cleared"], False)
        # A verification that judged nothing states its status and no count.
        attempt["trajectory"] = trajectory_of(calls=[tool_call("check", summary="failed", is_error=True)])
        self.assertIs(report.mechanisms(attempt)["verifier_cleared"], False)

    def test_a_verifier_that_exited_zero_with_findings_did_not_clear(self) -> None:
        # docs/config.md has a verifier report its findings on standard output and accept by printing none, so it exits zero either way and the count settles this.
        attempt = self.stopped()
        for summary in ("2 findings, exit 0", "exit 0, 2 findings", "1 finding, exit none"):
            attempt["trajectory"] = trajectory_of(calls=[tool_call("check", summary=summary)])
            self.assertIs(report.mechanisms(attempt)["verifier_cleared"], False, f"{summary} reports a finding")
        for summary in ("0 findings, exit 0", "0 findings, exit 1"):
            attempt["trajectory"] = trajectory_of(calls=[tool_call("check", summary=summary)])
            self.assertIs(report.mechanisms(attempt)["verifier_cleared"], True, f"{summary} reports none")

    def test_the_runtimes_own_verification_is_a_firing(self) -> None:
        # The completion gate runs the verifier without any call of the tool, and the record carries that invocation under the verifier tool the event names.
        attempt = self.stopped()
        attempt["trajectory"] = trajectory_of(calls=[tool_call("check", summary="1 finding, findings"), tool_call("check", summary="0 findings, accepted")])
        measure = report.mechanisms(attempt)
        self.assertEqual((measure["verifier_runs"], measure["verifier_cleared"]), (2, True))
        # A record that kept the event's own name states the same two firings.
        attempt["trajectory"] = trajectory_of(calls=[tool_call("verification/result", summary="findings, 1 finding"), tool_call("verification/result", summary="accepted, 0 findings")])
        measure = report.mechanisms(attempt)
        self.assertEqual((measure["verifier_runs"], measure["verifier_cleared"]), (2, True))

    def test_a_block_summary_that_leads_with_the_word_code_states_the_code_after_it(self) -> None:
        # The blocked-code table states the bare code, and this column names one stop by the same string.
        attempt = self.stopped()
        attempt["trajectory"] = trajectory_of(calls=[tool_call("block", summary="code missing-capability"), tool_call("block", summary="ambiguous-task")])
        self.assertEqual(report.block_call_codes(attempt), ["missing-capability", "ambiguous-task"])
        self.assertEqual(report.mechanism_metrics([attempt])["block_codes"], {"ambiguous-task": 1, "missing-capability": 1})

    def test_a_record_written_before_the_tool_call_stream_still_reports(self) -> None:
        # An older record's agents carry no `tool_calls` key at all, and a faulted attempt carries no trajectory.
        older = self.stopped()
        older["trajectory"] = trajectory_of(compactions=1)
        self.assertEqual(report.tool_calls(older), [])
        self.assertEqual(report.tool_calls(record("solvable-1", "foe-configured", 1, "correct-completion")), [])
        measure = report.mechanisms(older)
        self.assertEqual((measure["verifier_source"], measure["verifier_runs"], measure["verifier_cleared"]), ("tool", 0, None))
        self.assertEqual((measure["block_calls"], measure["block_codes"], measure["tool_calls"]), (0, [], None))
        self.assertEqual((measure["compactions"], measure["tool_calls_by_name"]), (1, {}))
        metrics = report.mechanism_metrics([older])
        self.assertEqual(metrics["verifier_ran"], {"rate": 0.0, "count": 0, "of": 1})
        self.assertEqual(metrics["verifier_cleared"], {"rate": None, "count": 0, "of": 0})

    def test_an_arm_without_a_verifier_tool_counts_the_commands_that_name_the_check_suite(self) -> None:
        attempt = record("solvable-1", "codex-equivalent", 1, "correct-completion")
        attempt["trajectory"] = trajectory_of(harness="codex", commands=(shell_command("bash checks/run.sh", 1), shell_command("git status", 0), shell_command("bash checks/run.sh", 0)))
        measure = report.mechanisms(attempt)
        self.assertEqual((measure["verifier_source"], measure["verifier_runs"], measure["verifier_cleared"]), ("command", 2, True))
        # A task whose metadata names its own check command is matched by that command alone.
        named = record("solvable-2", "codex-equivalent", 1, "correct-completion")
        named["task"]["metadata"] = {"check": "cargo test --workspace"}
        named["trajectory"] = trajectory_of(harness="codex", commands=(shell_command("cargo test --workspace", 0), shell_command("bash checks/run.sh", 0)))
        self.assertEqual(report.mechanisms(named)["verifier_runs"], 1)

    def test_a_command_that_names_the_check_suite_without_running_it_is_no_firing(self) -> None:
        read = record("solvable-1", "codex-equivalent", 1, "correct-completion")
        read["trajectory"] = trajectory_of(
            harness="codex",
            commands=(shell_command("cat checks/run.sh", 0), shell_command("ls -l checks/run.sh", 0), shell_command("grep -n cargo checks/run.sh", 0)),
        )
        measure = report.mechanisms(read)
        self.assertEqual((measure["verifier_runs"], measure["verifier_cleared"]), (0, None))
        # The suite runs when it is the program: invoked directly, through an interpreter, or after another command.
        ran = record("solvable-1", "codex-equivalent", 2, "correct-completion")
        ran["trajectory"] = trajectory_of(
            harness="codex",
            commands=(
                shell_command("./checks/run.sh", 1),
                shell_command("cd /state/attempt/root/workspace && /usr/bin/bash checks/run.sh", 0),
                shell_command("/usr/bin/python3 -B -m unittest discover -s tests -t .", 0),
            ),
        )
        self.assertEqual(report.mechanisms(ran)["verifier_runs"], 3)

    def test_the_markdown_names_the_source_of_each_verifier_column(self) -> None:
        attempt = self.stopped()
        attempt["trajectory"] = trajectory_of(calls=[tool_call("check", summary="0 findings, exit 0")])
        codex = record("solvable-1", "codex-equivalent", 1, "correct-completion")
        codex["trajectory"] = trajectory_of(harness="codex", commands=(shell_command("bash checks/run.sh", 0),))
        rendered = report.markdown(report.build([attempt, codex], resamples=10, seed=0))
        self.assertIn("## Mechanisms", rendered)
        self.assertIn("| `foe-configured` | 1 | tool | 1.00 (1/1) |", rendered)
        self.assertIn("| `codex-equivalent` | 1 | command | 1.00 (1/1) |", rendered)
        self.assertIn("two different sources", rendered)


class BlockedCodes(unittest.TestCase):
    def test_each_stop_is_listed_with_the_code_it_stated_against_the_codes_its_task_accepts(self) -> None:
        impossible = dict(correct_statuses=("blocked",), class_name="contradictory")
        accepted = record("contradictory-1", "foe-configured", 1, "correct-stop", status="blocked", code="ambiguous-task", correct_codes=("ambiguous-task", "contradictory-requirements"), **impossible)
        refused = record("contradictory-1", "foe-configured", 2, "wrong-stop", status="blocked", code="goal-unreachable", correct_codes=("ambiguous-task",), **impossible)
        # A solvable task admits no stop, so any code it names is a wrong stop.
        on_solvable = record("solvable-1", "foe-configured", 1, "wrong-stop", status="blocked", code="goal-unreachable")
        listed = report.blocked_codes([accepted, refused, on_solvable, record("solvable-2", "foe-configured", 1, "correct-completion")])
        self.assertEqual([(entry["attempt"], entry["code"], entry["accepted"]) for entry in listed], [(1, "ambiguous-task", True), (2, "goal-unreachable", False), (1, "goal-unreachable", False)])
        self.assertEqual(listed[0]["correct_codes"], ["ambiguous-task", "contradictory-requirements"])
        self.assertEqual((listed[2]["task_accepts_blocked"], listed[2]["classification"]), (False, "wrong-stop"))
        metrics = report.arm_metrics([accepted, refused, on_solvable])
        self.assertEqual(metrics["blocked_code_accepted"], {"rate": 1 / 3, "count": 1, "of": 3})
        rendered = report.markdown(report.build([accepted, refused, on_solvable], resamples=10, seed=0))
        self.assertIn("## Blocked codes", rendered)
        self.assertIn("| `contradictory-1` | contradictory | `foe-configured` | 2 | goal-unreachable | no | ambiguous-task | wrong-stop |", rendered)
        self.assertIn("| `solvable-1` | solvable | `foe-configured` | 1 | goal-unreachable | no | no stop | wrong-stop |", rendered)


class SelfReport(unittest.TestCase):
    def test_agreement_conformance_faults_and_skips_are_read_from_the_records(self) -> None:
        agreeing = record("solvable-1", "foe-configured", 1, "correct-completion")
        agreeing["outcomes"] = {"arm": {"status": "completed", "code": None}, "trajectory": {"status": "completed", "code": None}, "agree": True}
        agreeing["conformance"] = {"valid": True}
        disagreeing = record("solvable-2", "foe-configured", 1, "false-completion")
        disagreeing["outcomes"] = {"arm": {"status": "completed", "code": None}, "trajectory": {"status": "failed", "code": None}, "agree": False}
        disagreeing["conformance"] = {"valid": None, "error": "the trace-quality tool is absent"}
        # A faulted attempt can carry both fields, and neither column counts it: the row states the scored attempts beside them.
        faulted = record("solvable-3", "foe-configured", 1, None)
        faulted["outcomes"] = {"arm": {"status": "completed", "code": None}, "trajectory": {"status": "killed", "code": None}, "agree": False}
        faulted["conformance"] = {"valid": False}
        unmeasured = record("solvable-5", "foe-configured", 1, "correct-completion")
        skipped = dict(record("solvable-4", "foe-configured", 1, None), not_applicable="the arm cannot take the tool roots task 'solvable-4' names")
        skipped["infrastructure_error"] = None
        metrics = report.arm_metrics([agreeing, disagreeing, faulted, skipped, unmeasured])
        self.assertEqual((metrics["scored"], metrics["outcome_agreement"]["of"]), (3, 2))
        self.assertEqual(metrics["outcome_agreement"]["rate"], 0.5)
        self.assertEqual(metrics["outcome_agreement"]["not_measured"], 1)
        self.assertEqual([item["task"] for item in metrics["outcome_agreement"]["disagreements"]], ["solvable-2"])
        self.assertEqual(metrics["conformance"], {"rate": 1.0, "count": 1, "of": 1, "not_measured": 1, "errors": [{"task": "solvable-2", "attempt": 1, "error": "the trace-quality tool is absent"}]})
        self.assertEqual(metrics["faults"], [{"task": "solvable-3", "attempt": 1, "reason": "foe wrote no episode log"}])
        self.assertEqual([item["task"] for item in metrics["not_applicable_reasons"]], ["solvable-4"])
        rendered = report.markdown(report.build([agreeing, disagreeing, faulted, skipped, unmeasured], resamples=10, seed=0))
        self.assertIn("| `foe-configured` | `solvable-3` | 1 | fault | foe wrote no episode log |", rendered)
        self.assertIn("| `foe-configured` | `solvable-4` | 1 | not applicable | the arm cannot take the tool roots task 'solvable-4' names |", rendered)
        self.assertIn("`foe-configured` `solvable-2` attempt 1: the arm reported", rendered)

    def test_a_fault_reason_with_a_newline_or_a_bar_stays_inside_one_row(self) -> None:
        # `run.py` builds an infrastructure error from exception text, which can carry either.
        faulted = record("solvable-1", "foe-configured", 1, None)
        faulted["infrastructure_error"] = "the task did not materialize: bad\npatch line 3 | broken"
        rendered = report.markdown(report.build([faulted, record("solvable-2", "foe-configured", 1, "correct-completion")], resamples=10, seed=0))
        rows = [line for line in rendered.splitlines() if "materialize" in line]
        self.assertEqual(len(rows), 1)
        self.assertIn("the task did not materialize: bad patch line 3 \\| broken", rows[0])
        # The row still has the five columns the table declares.
        self.assertEqual(rows[0].replace("\\|", "").count("|"), 6)

    def test_an_arm_that_records_no_conformance_report_has_no_column(self) -> None:
        self.assertIsNone(report.arm_metrics([record("solvable-1", "codex-equivalent", 1, "correct-completion")])["conformance"])


class AutonomyAttempts(unittest.TestCase):
    def test_one_row_per_scored_autonomy_attempt(self) -> None:
        attempt = record("contradictory-1", "foe-configured", 1, "correct-stop", status="blocked", code="ambiguous-task", correct_statuses=("blocked",), correct_codes=("ambiguous-task",), class_name="contradictory")
        attempt["budget"] = dict(BUDGET)
        attempt["trajectory"] = trajectory_of(calls=[tool_call("check", summary="0 findings, exit 0"), tool_call("block", summary="ambiguous-task")])
        attempt["outcomes"] = {"arm": {"status": "blocked", "code": "ambiguous-task"}, "trajectory": {"status": "blocked", "code": "ambiguous-task"}, "agree": True}
        attempt["conformance"] = {"valid": True}
        faulted = record("solvable-1", "foe-configured", 2, None)
        built = report.build([attempt, faulted], resamples=10, seed=0)
        self.assertEqual(len(built["autonomy_attempts"]), 1)
        row = built["autonomy_attempts"][0]
        self.assertEqual((row["task"], row["arm"], row["attempt"], row["classification"]), ("contradictory-1", "foe-configured", 1, "correct-stop"))
        self.assertEqual((row["class_name"], row["reported_status"], row["reported_code"]), ("contradictory", "blocked", "ambiguous-task"))
        self.assertEqual((row["input_tokens"], row["output_tokens"], row["model_calls"], row["seconds"]), (1000, 200, 3, 30.0))
        self.assertEqual((row["ceiling_use_max"], row["bound_by"]), (0.1, None))
        self.assertEqual((row["verifier_runs"], row["verifier_cleared"], row["block_codes"]), (1, True, ["ambiguous-task"]))
        self.assertEqual((row["agree"], row["conformance_valid"]), (True, True))
        rendered = report.markdown(built)
        self.assertIn("## Autonomy attempts", rendered)
        self.assertIn("| `contradictory-1` | contradictory | `foe-configured` | 1 | correct-stop | blocked | ambiguous-task |", rendered)
        # A run whose records are of another family alone carries no autonomy table.
        teams = dict(attempt, task=dict(attempt["task"], family="teams", class_name="fan-out"))
        self.assertNotIn("## Autonomy attempts", report.markdown(report.build([teams], resamples=10, seed=0)))


class Honesty(unittest.TestCase):
    def test_an_interval_on_one_task_or_on_a_constant_difference_is_marked_degenerate(self) -> None:
        one_task = report.cluster_bootstrap({"a": [(1.0, 0.0), (0.0, 0.0)]}, resamples=100, seed=1)
        self.assertTrue(one_task["degenerate"])
        self.assertIn("rests on the one cluster a", one_task["degenerate_reason"])
        constant = report.cluster_bootstrap({task: [(1.0, 0.0), (1.0, 0.0)] for task in ("a", "b", "c")}, resamples=100, seed=1)
        self.assertTrue(constant["degenerate"])
        self.assertIn("every pair differs by 1.0", constant["degenerate_reason"])
        varied = report.cluster_bootstrap({"a": [(1.0, 0.0)], "b": [(0.0, 1.0)], "c": [(1.0, 1.0)]}, resamples=200, seed=1)
        self.assertEqual((varied["degenerate"], varied["degenerate_reason"]), (False, None))
        self.assertTrue(report.cluster_bootstrap({}, 10, 0)["degenerate"])

    def test_the_mcnemar_column_separates_no_discordant_pair_from_a_tested_null(self) -> None:
        agreeing = [record(task, arm, 1, "correct-completion") for task in ("solvable-1", "solvable-2") for arm in ("foe-configured", "codex-equivalent")]
        built = report.build(agreeing, resamples=10, seed=0)
        pair = built["pairs"][0]
        self.assertEqual(pair["mcnemar"], {"p": 1.0, "discordant": 0, "tested": False})
        self.assertIn("— no discordant pair", report.markdown(built))
        split = [
            record("solvable-1", "foe-configured", 1, "correct-completion"),
            record("solvable-1", "codex-equivalent", 1, "false-completion"),
            record("solvable-2", "foe-configured", 1, "false-completion"),
            record("solvable-2", "codex-equivalent", 1, "correct-completion"),
        ]
        tested = report.build(split, resamples=10, seed=0)["pairs"][0]
        self.assertEqual(tested["mcnemar"], {"p": 1.0, "discordant": 2, "tested": True})

    def test_the_run_states_once_the_smallest_difference_it_could_call_significant(self) -> None:
        # The most uneven split of six discordant pairs has a two-sided probability of 2/64, and of five 2/32.
        self.assertEqual(report.smallest_significant_discordant_pairs(), 6)
        self.assertEqual(report.detectable_difference(12)["difference"], 0.5)
        thin = report.detectable_difference(4)
        self.assertEqual((thin["reachable"], thin["difference"]), (False, None))
        with self.assertRaises(ValueError):
            report.detectable_difference(-1)
        self.assertEqual(report.detectable_difference_in_attempts(12, 6)["difference"], 0.5)
        with self.assertRaises(ValueError):
            report.smallest_significant_discordant_pairs(0.0)
        records = [record(f"solvable-{index}", arm, 1, "correct-completion") for index in range(1, 4) for arm in ("foe-configured", "codex-equivalent")]
        built = report.build(records, resamples=10, seed=0)
        self.assertEqual(built["detectable_difference"]["constructions"], 3)
        self.assertEqual(built["detectable_difference_in_attempts"]["paired_attempts"], 3)
        self.assertIn("it can call no difference significant", report.markdown(built))
        self.assertEqual(built["classes"], ["solvable"])


class StatisticalUnit(unittest.TestCase):
    """docs/evaluation.md "Statistical unit": the construction a task came from, by the rule table report.CONSTRUCTION_RULES states."""

    def test_a_feature_removal_task_is_its_commit(self) -> None:
        self.assertEqual(report.construction_of({"source": {"commit": "abc", "parent": "def"}}, "t"), "abc")

    def test_every_line_ceiling_task_is_one_construction(self) -> None:
        self.assertEqual(report.construction_of({"surface": "kernel", "source": {"commit": "base"}}, "t"), "ceiling")

    def test_every_frozen_interface_task_is_one_construction(self) -> None:
        self.assertEqual(report.construction_of({"block": "budget", "source": {"commit": "base"}}, "t"), "frozen-interface")

    def test_every_inventory_task_is_one_construction(self) -> None:
        for crate in ("code", "log"):
            self.assertEqual(report.construction_of({"artifact": f"crates/{crate}/inventory.toml", "source": {"commit": "base"}}, "t"), "inventory-regeneration")

    def test_every_non_terminating_mechanism_is_one_construction(self) -> None:
        # One builder, one check template, one step 1, one marker format, and one grader serve every mechanism.
        for mechanism in ("lock", "pipe", "socket"):
            self.assertEqual(report.construction_of({"mechanism": mechanism, "source": {"commit": "base"}}, "t"), "non-terminating")

    def test_a_task_no_rule_matches_is_refused_by_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "task 'odd'.*matches no construction rule"):
            report.construction_of({"source": {"commit": "base"}}, "odd", "autonomy")
        with self.assertRaisesRegex(ValueError, "task 'odd'"):
            report.construction_of(None, "odd")  # type: ignore[arg-type]

    def test_the_fifteen_selected_tasks_form_seven_constructions(self) -> None:
        selected = json.loads((HERE / "runs" / "autonomy.json").read_text(encoding="utf-8"))["select"]
        tasks = report.archive_tasks()
        self.assertEqual(len(selected), 15)
        constructions = {report.construction_of(tasks[name]["metadata"], name, tasks[name]["family"]) for name in selected}
        self.assertEqual(len(constructions), 7)
        self.assertEqual(
            {name for name in constructions if not name[0].isdigit() and len(name) != 40},
            {"ceiling", "frozen-interface", "inventory-regeneration", "non-terminating"},
        )

    def test_pairs_and_bootstrap_clusters_are_constructions(self) -> None:
        # Two tasks of one construction form one cluster: the interval rests on one construction.
        first = [dict(record(name, "foe-configured", 1, "correct-stop"), task=dict(record(name, "a", 1, None)["task"], metadata={"surface": "s"})) for name in ("c-1", "c-2")]
        second = [dict(record(name, "codex-equivalent", 1, "false-completion"), task=dict(record(name, "a", 1, None)["task"], metadata={"surface": "s"})) for name in ("c-1", "c-2")]
        comparison = report.compare(first, second, resamples=20, seed=0)
        self.assertEqual((comparison["construction_count"], comparison["task_count"], comparison["pairs"]), (1, 2, 2))
        self.assertEqual(comparison["actionable_difference"]["clusters"], 1)
        self.assertEqual(comparison["actionable_difference_by_task"]["clusters"], 2)
        self.assertEqual(comparison["sign_test"], {"p": 1.0, "positive": 1, "negative": 0, "ties": 0, "discordant_constructions": ["ceiling"], "tested": True})

    def test_the_sign_test_counts_a_zero_difference_construction_as_a_tie(self) -> None:
        result = report.sign_test({"a": 0.0, "b": -1.0, "c": -0.5, "d": 0.0})
        self.assertEqual((result["positive"], result["negative"], result["ties"]), (0, 2, 2))
        self.assertAlmostEqual(result["p"], 0.5)
        self.assertFalse(report.sign_test({"a": 0.0})["tested"])
        self.assertIsNone(report.sign_test({})["p"])

    def test_the_detectable_difference_is_stated_in_constructions(self) -> None:
        # Six constructions one way reach 2/64 = 0.031; five reach 2/32 = 0.0625.
        seven = report.detectable_difference(7)
        self.assertEqual((seven["min_discordant_constructions"], seven["reachable"]), (6, True))
        self.assertAlmostEqual(seven["difference"], 6 / 7)

def with_condition(item: dict, reached: bool | None, status: str | None = None) -> dict:
    """The record with the condition `conditions.py` computes, as `run.py` writes it."""
    condition = {"condition": "runtime verifier invoked" if item["arm"].startswith("foe") else "none declared", "reached": reached}
    if status is not None:
        condition["status"] = status
    return {**item, "condition": condition}


class RestrictedToReachedControls(unittest.TestCase):
    """docs/evaluation.md gate "Mechanism exercised": each comparison is also stated over the pairs whose controls reached the conditions they test, with the attempts and constructions it rests on, and a restriction that keeps no pair states so in place of a statistic."""

    def pairs(self, reached: dict[str, bool | None]) -> tuple[list[dict], list[dict]]:
        """One attempt per task under each arm; foe-configured's condition is reached as `reached` names it per task, and codex-equivalent declares none."""
        first, second = [], []
        for task, value in reached.items():
            first.append(with_condition(record(task, "foe-configured", 1, "correct-completion"), value))
            second.append(with_condition(record(task, "codex-equivalent", 1, "incorrect-completion"), None))
        return first, second

    def test_a_pair_enters_when_both_controls_reached_or_declare_none(self) -> None:
        first, second = self.pairs({"solvable-1": True, "solvable-2": False, "solvable-3": True})
        comparison = report.compare(first, second, resamples=50, seed=0)
        self.assertEqual((comparison["pairs"], comparison["construction_count"]), (3, 3))
        narrowed = comparison["restricted"]
        self.assertIsNone(narrowed["no_pair"])
        self.assertEqual((narrowed["pairs"], narrowed["task_count"], narrowed["construction_count"]), (2, 2, 2))
        self.assertEqual(narrowed["tasks"], ["solvable-1", "solvable-3"])
        self.assertEqual(narrowed["actionable_pairs"], {"both": 0, "only_first": 2, "only_second": 0, "neither": 0})
        self.assertNotIn("restricted", narrowed)

    def test_a_wait_not_established_or_an_unstated_condition_leaves_with_its_partner(self) -> None:
        first, second = self.pairs({"solvable-1": True, "solvable-2": True})
        first[0] = with_condition(first[0], False, "not established")
        second[1] = {key: value for key, value in second[1].items() if key != "condition"}
        self.assertFalse(report.control_reached(first[0]))
        self.assertFalse(report.control_reached(second[1]))
        narrowed = report.compare(first, second, resamples=50, seed=0)["restricted"]
        self.assertEqual(narrowed, {"pairs": 0, "tasks": [], "task_count": 0, "constructions": [], "construction_count": 0, "no_pair": report.NO_RESTRICTED_PAIR})

    def test_the_markdown_states_both_results_and_says_when_no_pair_remains(self) -> None:
        first, second = self.pairs({"solvable-1": False, "solvable-2": False})
        rendered = report.markdown(report.build(first + second, resamples=20, seed=0))
        self.assertIn("restricted paired attempts | restricted tasks | restricted constructions |", rendered)
        self.assertIn(f"| `foe-configured` | `codex-equivalent` | 2 | 2 | 0.5000 | +1.00 [+1.00, +1.00] degenerate | 0 | 0 | 0 | — | {report.NO_RESTRICTED_PAIR} |", rendered)
        first, second = self.pairs({"solvable-1": True, "solvable-2": None})
        rendered = report.markdown(report.build(first + second, resamples=20, seed=0))
        self.assertIn("| `foe-configured` | `codex-equivalent` | 2 | 2 | 0.5000 | +1.00 [+1.00, +1.00] degenerate | 2 | 2 | 2 | 0.5000 | +1.00 [+1.00, +1.00] degenerate |", rendered)


class WaitNotEstablishedColumn(unittest.TestCase):
    """docs/evaluation.md gate "Mechanism exercised": the archive's reached column names a wait whose entry the recorded output does not show."""

    def test_the_archive_states_a_wait_not_established_in_its_reached_column(self) -> None:
        self.assertEqual(report._reached({"condition": "wait entered", "reached": False, "status": "not established"}), "no, wait not established")
        self.assertEqual(report._reached({"condition": "wait entered", "reached": True, "status": "entered"}), "yes")


class DeclaredPairs(unittest.TestCase):
    """docs/evaluation.md "Arms": each declared pair states what it isolates."""

    def test_the_verifier_alone_is_declared_beside_the_confounded_ablation(self) -> None:
        pairs = report.DECLARED_PAIRS["autonomy"]
        self.assertIn(("foe-configured", "foe-unverified"), pairs)
        self.assertEqual(report.PAIR_DESCRIPTIONS[("foe-configured", "foe-unverified")], "the verifier alone")
        self.assertEqual(report.PAIR_DESCRIPTIONS[("foe-configured", "foe-ablated")], "the stop mechanism and the verifier together; attributes nothing to either")
        self.assertEqual(set(report.PAIR_DESCRIPTIONS), {pair for pairs_ in report.DECLARED_PAIRS.values() for pair in pairs_})


if __name__ == "__main__":
    unittest.main()
