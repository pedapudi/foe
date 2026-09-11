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

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
    class_name: str = "solvable",
) -> dict:
    totals = {
        "model_calls": 3,
        "input_tokens": None if tokens is None else tokens[0],
        "output_tokens": None if tokens is None else tokens[1],
        "wall_ms": wall_ms,
    }
    return {
        "task": {"name": task, "family": "autonomy", "class_name": class_name, "correct_statuses": list(correct_statuses)},
        "arm": arm,
        "attempt": attempt,
        "classification": cell,
        "infrastructure_error": None if cell is not None else "foe wrote no episode log",
        "reported": {"status": status, "code": code, "evidence": []},
        "totals": totals if cell is not None else None,
        "arm_result": {"started_ms": 0, "ended_ms": 45_000},
    }


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
        self.assertAlmostEqual(first["observed"], 4 / 6 - 3 / 6)
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
        self.assertAlmostEqual(comparison["actionable_difference"]["observed"], 2 / 5 - 3 / 5)
        self.assertAlmostEqual(comparison["false_completion_difference"]["observed"], 0.0)

    def test_the_report_and_its_markdown_name_every_arm_and_task(self) -> None:
        built = report.build(self.records(), resamples=100, seed=0)
        self.assertEqual(built["arms"], ["foe-configured", "codex-equivalent"])
        self.assertEqual(built["families"], ["autonomy"])
        self.assertEqual(len(built["pairs"]), 1)
        rendered = report.markdown(built)
        self.assertIn("| `foe-configured` | 6 | 5 | 1 | 0 | 0.40 (2/5) |", rendered)
        self.assertIn("| `contradictory-1` | contradictory | `codex-equivalent` |", rendered)
        # The pair row states the tasks the interval rests on before the pair count.
        self.assertIn("| `foe-configured` | `codex-equivalent` | 3 | 5 | 1 | 1 | 2 | 1 | 1.0000 |", rendered)
        self.assertIn("-0.20 [", rendered)
        self.assertIn("Each interval rests on the tasks its row counts", rendered)
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
        self.assertEqual(set(not_formed), {("foe-configured", "foe-ablated"), ("foe-ablated", "codex-equivalent"), ("foe-as-shipped", "codex-equivalent")})
        self.assertEqual(not_formed[("foe-configured", "foe-ablated")], "foe-ablated has no scored attempt")
        rendered = report.markdown(built)
        self.assertIn("| `codex-equivalent` | `codex-default` | 2 | 2 |", rendered)
        self.assertIn("- `foe-as-shipped` with `codex-equivalent` (autonomy): foe-as-shipped has no scored attempt", rendered)
        self.assertNotIn("| `foe-configured` | `codex-default` |", rendered)
        # A teams family has its own declared pairs.
        teams = [dict(record("fan-out-1", arm, 1, "correct-completion"), task={"name": "fan-out-1", "family": "teams", "class_name": "fan-out", "correct_statuses": ["completed"]}) for arm in ("foe-configured", "codex-multi", "codex-single")]
        built = report.build(teams, resamples=20, seed=0)
        self.assertEqual([(pair["family"], pair["first"], pair["second"]) for pair in built["pairs"]], [("teams", "foe-configured", "codex-multi"), ("teams", "codex-single", "codex-multi")])

    def test_a_not_applicable_attempt_is_counted_beside_the_scored_ones_and_enters_no_rate(self) -> None:
        skipped = dict(record("solvable-1", "foe-as-shipped", 1, None), not_applicable="the foe-as-shipped arm cannot take the tool roots task 'solvable-1' names")
        skipped["infrastructure_error"] = None
        metrics = report.arm_metrics([skipped, record("solvable-2", "foe-as-shipped", 1, "correct-completion")])
        self.assertEqual((metrics["attempts"], metrics["scored"], metrics["not_applicable"], metrics["infrastructure_failures"]), (2, 1, 1, 0))
        self.assertEqual(metrics["actionable"], {"rate": 1.0, "count": 1, "of": 1})
        rendered = report.markdown(report.build([skipped, record("solvable-2", "foe-as-shipped", 1, "correct-completion")], resamples=10, seed=0))
        self.assertIn("| `foe-as-shipped` | 2 | 1 | 0 | 1 | 1.00 (1/1) |", rendered)

    def test_main_writes_both_files_and_refuses_a_malformed_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            records = Path(tmp) / "records"
            for item in self.records():
                path = records / item["task"]["name"] / item["arm"] / f"{item['attempt']:02d}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(item), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = report.main(["--records", str(records), "--resamples", "50"])
            self.assertEqual(status, 0, err.getvalue())
            self.assertTrue((Path(tmp) / "report.md").is_file())
            written = json.loads((Path(tmp) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(written["records"], 11)
            self.assertEqual(written["settings"]["resamples"], 50)
            self.assertIn("## Paired comparisons", out.getvalue())
            broken = records / "solvable-1" / "foe-configured" / "03.json"
            broken.write_text(json.dumps({"task": {"name": "solvable-1"}}), encoding="utf-8")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = report.main(["--records", str(records)])
            self.assertEqual(status, 2)
            self.assertIn(str(broken), err.getvalue())
            # A record whose task names a family without declared pairs is refused by path and family.
            unknown = record("solvable-1", "foe-configured", 3, "correct-completion")
            unknown["task"]["family"] = "other"
            broken.write_text(json.dumps(unknown), encoding="utf-8")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = report.main(["--records", str(records)])
            self.assertEqual(status, 2)
            self.assertIn("task.family is 'other'", err.getvalue())


if __name__ == "__main__":
    unittest.main()
