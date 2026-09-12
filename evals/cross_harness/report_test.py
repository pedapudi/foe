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


WORKSPACE = "/state/attempt/root/workspace"


def agent(
    agent_id: str,
    parent_id: str | None,
    depth: int,
    role: str,
    started_ms: int,
    ended_ms: int | None,
    tokens: tuple[int | None, int | None] = (200, 20),
    changes: tuple[tuple[str, int], ...] = (),
) -> dict:
    """One trajectory agent with one model call and the given file changes, each a workspace-relative path and a time."""
    return {
        "id": agent_id,
        "parent_id": parent_id,
        "depth": depth,
        "role": role,
        "started_ms": started_ms,
        "ended_ms": ended_ms,
        "model_calls": [{"seq": 1, "started_ms": started_ms, "ended_ms": started_ms + 1, "input_tokens": tokens[0], "output_tokens": tokens[1], "cache_read_tokens": None, "reasoning_tokens": None}],
        "commands": [],
        "file_changes": [{"path": f"{WORKSPACE}/{change}", "kind": "edit", "at_ms": at, "via": "tool"} for change, at in changes],
        "compactions": [],
    }


def teams_record(
    task: str,
    arm: str,
    attempt: int,
    cell: str | None,
    agents: list[dict],
    harness: str = "foe",
    class_name: str = "fan-out",
    metadata: dict | None = None,
    grade: dict | None = None,
    candidate: object = None,
    ended_ms: int | None = 10_000,
) -> dict:
    """A teams record whose trajectory holds `agents` and whose totals are summed from their calls."""
    calls = [call for item in agents for call in item["model_calls"]]
    measured = all(isinstance(call["input_tokens"], int) and isinstance(call["output_tokens"], int) for call in calls)
    starts = [item["started_ms"] for item in agents]
    ends = [item["ended_ms"] for item in agents if item["ended_ms"] is not None] + ([ended_ms] if ended_ms is not None else [])
    units = {"alpha": ["crates/alpha"], "beta": ["crates/beta"], "gamma": ["crates/gamma"]}
    metadata = {"units": units, "interface_paths": ["docs/interface.md"]} if metadata is None else metadata
    # The grade of a task with units carries each unit's verdict as `run.py` records it: every unit passes when the grade passes.
    named = metadata.get("units")
    return {
        "task": {"name": task, "family": "teams", "class_name": class_name, "correct_statuses": ["completed"], "metadata": metadata},
        "arm": arm,
        "attempt": attempt,
        "classification": cell,
        "infrastructure_error": None,
        "reported": {"status": "completed", "code": None, "evidence": []},
        "candidate": candidate,
        "paths": {"workspace": WORKSPACE},
        "trajectory": {"harness": harness, "identity": {}, "agents": agents, "outcome": {"status": "completed", "code": None, "value": None, "ended_ms": ended_ms}, "route": "subscription"},
        "totals": {
            "model_calls": len(calls),
            "input_tokens": sum(call["input_tokens"] for call in calls) if measured else None,
            "output_tokens": sum(call["output_tokens"] for call in calls) if measured else None,
            "wall_ms": (max(ends) - min(starts)) if starts and ends else None,
        },
        "grade": {"passed": cell == "correct-completion", "findings": [], "damage": [], "units": None if named is None else {name: cell == "correct-completion" for name in named}} if grade is None else grade,
        "arm_result": {"started_ms": 0, "ended_ms": 12_000},
    }


def divided_team() -> list[dict]:
    """A foe team: the root, four nodes, and two workers, with one shared file, one rework, one interface change after the workers started, and one unit nobody wrote."""
    return [
        agent("root", None, 0, "root", 0, 10_000, tokens=(100, 10)),
        agent("survey", "root", 1, "survey", 100, 1_000),
        agent("interface", "root", 1, "interface", 1_000, 2_000, changes=(("docs/interface.md", 1_500),)),
        agent("delegate", "root", 1, "delegate", 2_000, 7_000),
        agent("w1", "delegate", 2, "worker", 3_000, 6_000, tokens=(500, 50), changes=(("crates/alpha/lib.rs", 4_000),)),
        agent("w2", "delegate", 2, "worker", 3_000, 5_000, tokens=(500, 50), changes=(("crates/beta/lib.rs", 4_000), ("crates/alpha/lib.rs", 4_500))),
        agent("integrate", "root", 1, "integrate", 7_000, 9_000, changes=(("docs/interface.md", 7_500), ("crates/alpha/lib.rs", 8_000))),
    ]


class TeamsMeasures(unittest.TestCase):
    def delegated(self, **overrides: object) -> dict:
        # The grader judged gamma's unit and the workspace check; the units alpha and beta drew no finding.
        grade = {"passed": False, "findings": ["unit gamma test: `cargo test -p gamma` exited 101: test sweep ... FAILED", "integration test: `cargo test --workspace` exited 101: 1 failed"], "damage": [], "units": {"alpha": True, "beta": True, "gamma": False}}
        candidate = {"summary": "Two of three.", "units": [{"unit": "alpha", "outcome": "completed"}, {"unit": "beta", "outcome": "completed"}, {"unit": "gamma", "outcome": "failed"}], "changed_paths": [], "unresolved_risks": []}
        fields: dict = dict(grade=grade, candidate=candidate)
        fields.update(overrides)
        return teams_record("fan-out-1", "foe-configured", 1, "false-completion", divided_team(), **fields)

    def test_every_measure_of_a_divided_run_follows_the_docstring(self) -> None:
        measure = report.teams_measures(self.delegated())
        self.assertEqual((measure["workers"], measure["divided"]), (2, True))
        self.assertEqual(measure["units"], {"graded": 3, "passed": 2})
        self.assertAlmostEqual(measure["unit_pass_fraction"], 2 / 3)
        self.assertAlmostEqual(measure["uniformity"], 2 / 3)
        self.assertEqual((measure["integration_pass"], measure["full_success"]), (False, False))
        self.assertEqual((measure["makespan_seconds"], measure["tokens"]), (10.0, 2090))
        # The lead is the root and the four nodes: 110 + 4 × 220 tokens of 2090.
        self.assertAlmostEqual(measure["coordination_overhead"], 990 / 2090)
        # The interface node's own change precedes the first worker; the integrating node's change follows it.
        self.assertEqual(measure["interface_churn"], 1)
        self.assertEqual(measure["rework"], ["crates/alpha/lib.rs"])
        # Workers live 3000 and 2000 milliseconds of a 10000 millisecond run.
        self.assertAlmostEqual(measure["concurrency"], 0.5)
        self.assertEqual(measure["report_fidelity"], {"reported_done": ["alpha", "beta"], "passing": ["alpha", "beta"], "rate": 1.0, "count": 2, "of": 2})
        self.assertEqual(measure["defects"], {"files_written_by_two_agents": ["crates/alpha/lib.rs"], "units_never_written": ["gamma"], "units_written_twice": ["alpha"], "count": 3})

    def test_unit_verdicts_are_read_from_the_findings_that_name_a_unit(self) -> None:
        # A finding headed `unit <name>` with a colon or a space fails that unit alone; `crates/code` does not name `crates/code_extra`.
        units = {"crates/code": ["crates/code"], "crates/code_extra": ["crates/code_extra"], "docs": ["docs"]}
        metadata = {"units": units, "interface_paths": []}
        findings = ["unit crates/code: hidden test crates/code/src/bash_test.rs: sweeps did not run", "unit docs docs/x_test.py: `/usr/bin/python3 -B docs/x_test.py` exited 1: F", "integration clippy: `cargo clippy` exited 1: warning"]
        verdicts = report.grade_units(self.delegated(metadata=metadata, grade={"passed": False, "findings": findings, "damage": [], "units": {"crates/code": False, "crates/code_extra": True, "docs": False}}))
        self.assertEqual(verdicts, {"crates/code": False, "crates/code_extra": True, "docs": False})
        # No finding at all is every unit passing; a finding on the whole change alone leaves every unit passing.
        self.assertEqual(report.grade_units(self.delegated(metadata=metadata, grade={"passed": True, "findings": [], "damage": [], "units": {name: True for name in units}})), {name: True for name in units})
        self.assertEqual(report.grade_units(self.delegated(metadata=metadata, grade={"passed": False, "findings": ["docs/spec.md lacks the sentence: x"], "damage": [], "units": {name: True for name in units}})), {name: True for name in units})
        # A grade that did not judge the units settles no verdict: the script failed, timed out, exited, or ran without cargo.
        for finding in (
            "the grade script /root/grader/grade failed: it ran past 900s and its process group was killed",
            "the grade script /root/grader/grade exited 1: Traceback",
            "the grade script runs from the workspace, and /w holds no Cargo.toml and crates/ directory",
            "cargo is absent from PATH and host.json names none",
        ):
            self.assertIsNone(report.grade_units(self.delegated(grade={"passed": False, "findings": [finding], "damage": [], "units": None})), finding)
        # A task without units, or a record without a grade, settles none either.
        self.assertIsNone(report.grade_units(self.delegated(metadata={})))
        ungraded = self.delegated()
        del ungraded["grade"]
        self.assertIsNone(report.grade_units(ungraded))
        measure = report.teams_measures(self.delegated(grade={"passed": False, "findings": ["the grade script /root/grader/grade exited 1: Traceback"], "damage": [], "units": None}))
        self.assertIsNone(measure["units"])
        self.assertIsNone(measure["unit_pass_fraction"])
        self.assertIsNone(measure["report_fidelity"])

    def test_a_file_two_agents_wrote_is_a_defect_only_while_both_were_live_whichever_wrote_later(self) -> None:
        # A Codex root that spans the run rewrites a sub-agent's file after the sub-agent ended, which is rework and no defect.
        codex = [
            agent("t1", None, 0, "root", 0, 8_000, changes=(("crates/alpha/lib.rs", 6_000),)),
            agent("t2", "t1", 1, "explorer", 1_000, 5_000, changes=(("crates/alpha/lib.rs", 2_000),)),
        ]
        measure = report.teams_measures(teams_record("fan-out-1", "codex-multi", 1, "correct-completion", codex, harness="codex", ended_ms=8_000))
        self.assertEqual((measure["rework"], measure["defects"]["files_written_by_two_agents"]), (["crates/alpha/lib.rs"], []))
        # A foe integrating node that starts after the worker ended makes the same rewrite and is measured alike.
        foe = [
            agent("root", None, 0, "root", 0, 8_000),
            agent("delegate", "root", 1, "delegate", 500, 5_500),
            agent("w1", "delegate", 2, "worker", 1_000, 5_000, changes=(("crates/alpha/lib.rs", 2_000),)),
            agent("integrate", "root", 1, "integrate", 5_500, 7_000, changes=(("crates/alpha/lib.rs", 6_000),)),
        ]
        measure = report.teams_measures(teams_record("fan-out-1", "foe-configured", 1, "correct-completion", foe, ended_ms=8_000))
        self.assertEqual((measure["rework"], measure["defects"]["files_written_by_two_agents"]), (["crates/alpha/lib.rs"], []))
        # The root writing while the sub-agent is still live is a defect, and so is the sub-agent writing while the root is live, which the root always is.
        while_live = [
            agent("t1", None, 0, "root", 0, 8_000, changes=(("crates/alpha/lib.rs", 3_000),)),
            agent("t2", "t1", 1, "explorer", 1_000, 5_000, changes=(("crates/alpha/lib.rs", 2_000),)),
        ]
        measure = report.teams_measures(teams_record("fan-out-1", "codex-multi", 1, "correct-completion", while_live, harness="codex", ended_ms=8_000))
        self.assertEqual(measure["defects"]["files_written_by_two_agents"], ["crates/alpha/lib.rs"])
        after_root_write = [
            agent("t1", None, 0, "root", 0, 8_000, changes=(("crates/beta/lib.rs", 500),)),
            agent("t2", "t1", 1, "explorer", 1_000, 5_000, changes=(("crates/beta/lib.rs", 2_000),)),
        ]
        measure = report.teams_measures(teams_record("fan-out-1", "codex-multi", 1, "correct-completion", after_root_write, harness="codex", ended_ms=8_000))
        self.assertEqual(measure["defects"]["files_written_by_two_agents"], ["crates/beta/lib.rs"])
        # A writer without an end is live throughout, so a later write by another agent is a defect.
        unended = [agent("root", None, 0, "root", 0, None, changes=(("crates/alpha/lib.rs", 100),)), agent("w1", "root", 2, "worker", 1_000, None, changes=(("crates/alpha/lib.rs", 2_000),))]
        measure = report.teams_measures(teams_record("fan-out-1", "foe-configured", 2, "killed", unended, ended_ms=None))
        self.assertEqual(measure["defects"]["files_written_by_two_agents"], ["crates/alpha/lib.rs"])
        # One agent writing one file twice is no defect.
        twice = [agent("root", None, 0, "root", 0, 4_000, changes=(("crates/alpha/lib.rs", 100), ("crates/alpha/lib.rs", 200)))]
        self.assertEqual(report.teams_measures(teams_record("fan-out-1", "foe-undivided", 1, "correct-completion", twice))["defects"]["files_written_by_two_agents"], [])

    def test_a_run_that_wrote_nothing_has_no_unit_never_written(self) -> None:
        stopped = teams_record("coherent-1", "foe-configured", 1, "correct-stop", [agent("root", None, 0, "root", 0, 4_000)], class_name="coherent")
        measure = report.teams_measures(stopped)
        self.assertEqual(measure["defects"], {"files_written_by_two_agents": [], "units_never_written": None, "units_written_twice": [], "count": 0})
        # One write anywhere makes the unwritten units count.
        wrote_one = teams_record("fan-out-1", "foe-undivided", 1, "false-completion", [agent("root", None, 0, "root", 0, 4_000, changes=(("crates/beta/lib.rs", 100),))])
        self.assertEqual(report.teams_measures(wrote_one)["defects"]["units_never_written"], ["alpha", "gamma"])

    def test_a_change_outside_the_workspace_is_refused_by_path(self) -> None:
        # The workspace the record names is spelled differently from the paths the log recorded, so no change can be attributed to a unit.
        misspelled = self.delegated()
        misspelled["paths"]["workspace"] = "/private" + WORKSPACE
        misspelled["_path"] = "/records/fan-out-1/foe-configured/01.json"
        with self.assertRaises(ValueError) as caught:
            report.teams_measures(misspelled)
        self.assertEqual(
            str(caught.exception),
            f"/records/fan-out-1/foe-configured/01.json: agent w1 changed {WORKSPACE}/crates/alpha/lib.rs, which does not lie under the record's workspace '/private{WORKSPACE}'",
        )
        # A relative path is taken as workspace-relative, with a leading `./` dropped.
        relative = self.delegated()
        relative["trajectory"]["agents"][2]["file_changes"][0]["path"] = "./docs/interface.md"
        self.assertEqual(report.teams_measures(relative)["interface_churn"], 1)

    def test_a_change_report_claims_the_units_its_changed_paths_lie_under(self) -> None:
        candidate = {"summary": "Done.", "changed_paths": [f"{WORKSPACE}/crates/alpha/lib.rs", "./crates/gamma/lib.rs"], "validation": ["check"], "unresolved_risks": [], "learned": []}
        measure = report.teams_measures(self.delegated(candidate=candidate))
        self.assertEqual(measure["report_fidelity"], {"reported_done": ["alpha", "gamma"], "passing": ["alpha"], "rate": 0.5, "count": 1, "of": 2})
        # A candidate that is not an object claims nothing, and a grade that did not judge the units settles no fidelity.
        self.assertIsNone(report.teams_measures(self.delegated(candidate="done"))["report_fidelity"])
        self.assertIsNone(report.teams_measures(self.delegated(grade={"passed": False, "findings": ["cargo is absent from PATH and host.json names none"], "damage": [], "units": None}))["report_fidelity"])

    def test_measures_that_need_an_absent_fact_are_null(self) -> None:
        bare = report.teams_measures(self.delegated(metadata={}, grade={"passed": True, "findings": [], "damage": [], "units": None}, candidate=None))
        self.assertIsNone(bare["units"])
        self.assertIsNone(bare["unit_pass_fraction"])
        self.assertIsNone(bare["interface_churn"])
        self.assertIsNone(bare["report_fidelity"])
        self.assertEqual(bare["defects"], {"files_written_by_two_agents": ["crates/alpha/lib.rs"], "units_never_written": None, "units_written_twice": None, "count": 1})
        self.assertTrue(bare["integration_pass"])
        unmeasured = [agent("root", None, 0, "root", 0, None, tokens=(None, None)), agent("w1", "root", 2, "worker", 100, None, tokens=(None, None))]
        killed = teams_record("fan-out-1", "foe-configured", 2, "killed", unmeasured, ended_ms=None)
        measure = report.teams_measures(killed)
        self.assertIsNone(measure["coordination_overhead"])
        self.assertIsNone(measure["concurrency"])
        # The trajectory measured no wall, so the arm result's own clock stands in, as it does for the cost of any attempt.
        self.assertEqual(measure["makespan_seconds"], 12.0)
        self.assertEqual(measure["workers"], 1)
        # A record outside the family, or one without a trajectory, has no measures.
        self.assertIsNone(report.teams_measures(record("solvable-1", "foe-configured", 1, "correct-completion")))
        self.assertIsNone(report.teams_measures(dict(killed, trajectory=None)))

    def test_a_codex_worker_is_any_agent_below_the_root_and_no_worker_means_no_division(self) -> None:
        codex = [agent("t1", None, 0, "root", 0, 8_000, changes=(("crates/beta/lib.rs", 6_000),)), agent("t2", "t1", 1, "explorer", 1_000, 5_000, changes=(("crates/alpha/lib.rs", 2_000),))]
        measure = report.teams_measures(teams_record("fan-out-1", "codex-multi", 1, "correct-completion", codex, harness="codex", ended_ms=8_000))
        self.assertEqual((measure["workers"], measure["divided"]), (1, True))
        # The one sub-agent lives 4000 milliseconds of an 8000 millisecond run.
        self.assertAlmostEqual(measure["concurrency"], 0.5)
        self.assertEqual(measure["rework"], [])
        alone = report.teams_measures(teams_record("coherent-1", "foe-undivided", 1, "correct-completion", [agent("root", None, 0, "root", 0, 4_000), agent("implement-alone", "root", 1, "implement-alone", 100, 3_000)], class_name="coherent"))
        self.assertEqual((alone["workers"], alone["divided"], alone["concurrency"], alone["interface_churn"]), (0, False, 0.0, None))

    def test_malformed_metadata_and_verdicts_are_refused_by_key(self) -> None:
        bad_units = self.delegated(metadata={"units": {"alpha": "crates/alpha"}})
        bad_units["_path"] = "/records/fan-out-1/foe-configured/01.json"
        with self.assertRaises(ValueError) as caught:
            report.teams_measures(bad_units)
        self.assertIn("/records/fan-out-1/foe-configured/01.json: task.metadata.units is", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            report.teams_measures(self.delegated(metadata={"interface_paths": "docs"}))
        self.assertIn("task.metadata.interface_paths is 'docs'", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            report.teams_measures(self.delegated(grade={"passed": False, "findings": "gamma failed", "damage": []}))
        self.assertIn("grade.findings is 'gamma failed'", str(caught.exception))

    def test_the_arm_measures_average_the_measured_attempts_and_rate_division_on_coherent_controls(self) -> None:
        records = [
            self.delegated(),
            teams_record("fan-out-1", "foe-configured", 2, "correct-completion", divided_team(), grade={"passed": True, "findings": [], "damage": [], "units": {"alpha": True, "beta": True, "gamma": True}}),
            # A coherent control names no units and no interface, as the authored controls do.
            teams_record("coherent-1", "foe-configured", 1, "correct-completion", [agent("root", None, 0, "root", 0, 4_000), agent("implement-alone", "root", 1, "implement-alone", 100, 3_000)], class_name="coherent", metadata={}),
            teams_record("coherent-1", "foe-configured", 2, "correct-completion", divided_team(), class_name="coherent", metadata={}),
            teams_record("coherent-1", "foe-configured", 3, None, divided_team(), class_name="coherent", metadata={}),
        ]
        records[-1]["infrastructure_error"] = "foe wrote no episode log"
        metrics = report.teams_metrics(records)
        self.assertEqual((metrics["attempts"], metrics["measured"]), (4, 4))
        self.assertEqual(metrics["full_success"], {"rate": 0.75, "count": 3, "of": 4})
        self.assertEqual(metrics["integration_pass"]["count"], 3)
        self.assertEqual(metrics["unit_pass_fraction"], {"mean": (2 / 3 + 1.0) / 2, "measured": 2})
        self.assertEqual(metrics["uniformity"], {"mean": (2 / 3 + 1.0) / 2, "measured": 2})
        # Every scored attempt's tokens over the three full successes: three divided runs of 2090 and one alone run of 440.
        self.assertEqual(metrics["tokens_per_success"], {"value": (3 * 2090 + 440) / 3, "tokens_measured": 4, "successes": 3})
        self.assertEqual(metrics["concurrency"], {"mean": (0.5 * 3 + 0.0) / 4, "measured": 4})
        self.assertEqual(metrics["rework"], {"mean": 0.75, "measured": 4})
        self.assertEqual(metrics["report_fidelity"], {"mean": 1.0, "measured": 1})
        # Every divided run shares one file; the fan-out runs also leave gamma unwritten and alpha written twice; the alone run wrote nothing.
        self.assertEqual(metrics["defects"], {"rate": 0.75, "count": 3, "of": 4})
        self.assertEqual(metrics["defects_mean"], {"mean": (3 + 3 + 1 + 0) / 4, "measured": 4})
        self.assertEqual(metrics["division_on_coherent"], {"rate": 0.5, "count": 1, "of": 2})
        self.assertIsNone(report.teams_metrics([record("solvable-1", "foe-configured", 1, "correct-completion")]))

    def test_the_report_carries_the_teams_section_for_teams_records_alone(self) -> None:
        records = [self.delegated(), teams_record("fan-out-1", "foe-undivided", 1, "correct-completion", [agent("root", None, 0, "root", 0, 4_000), agent("implement-alone", "root", 1, "implement-alone", 100, 3_000)])]
        built = report.build(records, resamples=20, seed=0)
        self.assertEqual([(item["task"], item["arm"], item["attempt"], item["workers"]) for item in built["teams_attempts"]], [("fan-out-1", "foe-configured", 1, 2), ("fan-out-1", "foe-undivided", 1, 0)])
        self.assertEqual(built["per_arm"]["foe-configured"]["teams"]["attempts"], 1)
        rendered = report.markdown(built)
        self.assertIn("## Teams", rendered)
        self.assertIn("| `foe-configured` | 1 | 0.00 (0/1) | 0.00 (0/1) | 0.67 (n=1) | 0.67 (n=1) | 10.0 (n=1) | — | 0.47 (n=1) | 1.00 (n=1) | 1.00 (n=1) | 0.50 (n=1) | 1.00 (n=1) | 1.00 (1/1) | — |", rendered)
        self.assertIn("| `fan-out-1` | `foe-configured` | 1 | false-completion | 2 | 0.67 | 1 | 1 | 0.50 | 1.00 (2/2) | 3 |", rendered)
        self.assertIn("| `fan-out-1` | `foe-undivided` | 1 | correct-completion | 0 | 1.00 | — | 0 | 0.00 | — | 0 |", rendered)
        self.assertEqual([(pair["first"], pair["second"]) for pair in built["pairs"]], [("foe-configured", "foe-undivided")])
        autonomy = report.build([record("solvable-1", "foe-configured", 1, "correct-completion")], resamples=10, seed=0)
        self.assertIsNone(autonomy["per_arm"]["foe-configured"]["teams"])
        self.assertEqual(autonomy["teams_attempts"], [])
        self.assertNotIn("## Teams", report.markdown(autonomy))


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


if __name__ == "__main__":
    unittest.main()
