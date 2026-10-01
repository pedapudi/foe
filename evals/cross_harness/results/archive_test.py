#!/usr/bin/python3
"""Tests that recompute the recorded results under evals/cross_harness/results from the committed arrays.

The instruments' own tests use synthetic records and read nothing here.
These tests read the committed record arrays, the rescored and condition
files, and the task directories, and check that the report reproduces what
the results documents state.

    /usr/bin/python3 evals/cross_harness/results/archive_test.py
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import report  # noqa: E402

RESULTS = HERE


def archived_autonomy(scoring: int = 1) -> list[dict]:
    """The committed autonomy array as records, with each task's metadata from its task directory and the cell of one scoring version."""
    tasks = report.archive_tasks()
    rescored = {(entry["task"], entry["arm"], int(entry["attempt"])): entry for entry in json.loads((RESULTS / "autonomy-2026-09-13" / "rescored.json").read_text(encoding="utf-8"))}
    records = []
    for entry in json.loads((RESULTS / "autonomy-2026-09-13" / "records.json").read_text(encoding="utf-8")):
        task = tasks[entry["task"]]
        records.append(
            {
                "task": {"name": entry["task"], "family": task["family"], "class_name": task["class_name"], "correct_statuses": task["correct_statuses"], "metadata": task["metadata"]},
                "arm": entry["arm"],
                "attempt": int(entry["attempt"]),
                "classification": rescored[(entry["task"], entry["arm"], int(entry["attempt"]))][f"cell_scoring_{scoring}"],
                "infrastructure_error": None,
                "reported": entry["reported"],
                "totals": entry["totals"],
                "arm_result": {},
            }
        )
    return records


class RecordedComparison(unittest.TestCase):
    """docs/evaluation.md "Statistical unit" and results/autonomy-2026-09-13.md "Status of this record": the autonomy array, counted by construction."""

    def test_foe_configured_against_codex_equivalent_differs_on_the_non_terminating_construction_alone(self) -> None:
        for scoring in (1, 2):
            records = archived_autonomy(scoring)
            comparison = report.compare([r for r in records if r["arm"] == "foe-configured"], [r for r in records if r["arm"] == "codex-equivalent"], resamples=100, seed=0)
            self.assertEqual(comparison["construction_count"], 7)
            self.assertEqual(comparison["sign_test"]["discordant_constructions"], ["non-terminating"])
            self.assertEqual({task for task, values in report.by_task(report.paired([r for r in records if r["arm"] == "foe-configured"], [r for r in records if r["arm"] == "codex-equivalent"])).items() if any(report.is_actionable(a) != report.is_actionable(b) for a, b in values)}, {"unwritten-pipe-context", "waiting-check-suite"})
            self.assertEqual((comparison["sign_test"]["positive"], comparison["sign_test"]["negative"], comparison["sign_test"]["ties"]), (0, 1, 6))
            self.assertAlmostEqual(comparison["sign_test"]["p"], 1.0)
            # Two of the four non-terminating tasks favor codex-equivalent, so that construction differs by -0.5 and the mean over seven by -0.5 / 7.
            self.assertAlmostEqual(comparison["actionable_difference"]["observed"], -0.5 / 7)

    def test_the_thinnest_recorded_comparison_pairs_seven_constructions(self) -> None:
        built = report.build(archived_autonomy(), resamples=10, seed=0)
        self.assertEqual(built["detectable_difference"]["constructions"], 7)
        self.assertIn("over the 7 constructions of the thinnest comparison", report.markdown(built))

    def test_no_non_inferiority_result_is_stated_over_three_solvable_constructions(self) -> None:
        built = report.build(archived_autonomy(), resamples=10, seed=0)
        self.assertEqual(built["non_inferiority"], {"margin": 0.1, "solvable_constructions": 3, "needed": 6, "established": False})
        rendered = report.markdown(built)
        self.assertIn("No non-inferiority result is stated. The solvable tasks form 3 constructions", rendered)
        self.assertNotIn("non-inferior ", rendered)


class Archive(unittest.TestCase):
    """docs/evaluation.md "Scoring versions" and "Statistical unit": `report.py --archive` recomputes every table of the dated campaigns."""

    def test_the_committed_tables_are_what_the_archive_mode_computes(self) -> None:
        committed = (RESULTS / report.ARCHIVE_TABLES).read_text(encoding="utf-8")
        self.assertEqual(report.archive_markdown(RESULTS), committed, "regenerate with: python3 evals/cross_harness/report.py --archive evals/cross_harness/results")

    def test_both_scoring_versions_and_the_condition_stand_side_by_side(self) -> None:
        committed = (RESULTS / report.ARCHIVE_TABLES).read_text(encoding="utf-8")
        self.assertIn("| arm | scoring version | correct-completion |", committed)
        # Version 2 counts two codex-default autonomy stops that changed a preserved path as damage.
        self.assertIn("| codex-default | 1 | 3 | 9 | 1 | 0 | 2 | 0 | 12/15 |", committed)
        self.assertIn("| codex-default | 2 | 3 | 7 | 1 | 0 | 2 | 2 | 10/15 |", committed)
        self.assertIn("| cell, scoring 1 | cell, scoring 2 | condition | reached |", committed)

    def test_every_table_figure_of_the_results_documents_is_a_figure_of_the_tables(self) -> None:
        # The configuration, hypothesis, and failure-taxonomy tables state settings, predictions, and quoted log facts rather than measured results.
        skipped = {"Status of this record", "Conditions", "What ran", "Hypotheses", "Failure taxonomy"}
        computed = [value for value, _ in _numbers((RESULTS / report.ARCHIVE_TABLES).read_text(encoding="utf-8"))]
        for name in ("autonomy-2026-09-13.md", "enforcement-pressure-cases-2026-09-13.md"):
            section = ""
            for line in (RESULTS / name).read_text(encoding="utf-8").splitlines():
                if line.startswith("#"):
                    section = line.lstrip("# ").strip()
                if not line.startswith("|") or section in skipped or set(line) <= set("|-: ") or line.startswith("| first ") or line.startswith("| arm ") or line.startswith("| class ") or line.startswith("| |"):
                    continue
                for value, half in _numbers(line):
                    self.assertTrue(any(abs(item - value) <= half + 1e-9 for item in computed), f"{name}, {section}: {value} in {line}")

    def test_the_archive_refuses_a_rescored_file_that_describes_another_grading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for run_name, paths in report.ARCHIVE_RUNS.items():
                for key, relative in paths.items():
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = RESULTS / relative
                    if source.is_file():
                        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
                    else:
                        array = json.loads((RESULTS / paths["array"]).read_text(encoding="utf-8"))
                        target.write_text(json.dumps({"attempts": [{"task": e["task"], "arm": e["arm"], "attempt": e["attempt"], "condition": "none declared", "reached": None} for e in array]}), encoding="utf-8")
            rescored = root / report.ARCHIVE_RUNS["lean"]["rescored"]
            entries = json.loads(rescored.read_text(encoding="utf-8"))
            entries[0]["cell_scoring_1"] = "damage"
            rescored.write_text(json.dumps(entries), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "has cell_scoring_1 damage where the array holds"):
                report.archive_markdown(root)


class EvidenceManifest(unittest.TestCase):
    """docs/evaluation.md "Evidence archive": the manifest names the archive and its file list by digest, names every summarized record by digest, and reproduces every committed output."""

    def setUp(self) -> None:
        self.manifest = json.loads((RESULTS / "evidence-manifest.json").read_text(encoding="utf-8"))
        self.archive = self.manifest["archive"]

    def test_the_archive_and_its_file_list_are_named_by_size_and_digest(self) -> None:
        self.assertEqual(self.archive["asset"], "cross-harness-evidence-2026-09-13.tar.zst")
        file_list = self.archive["file_list"]
        self.assertEqual(file_list["asset"], self.archive["asset"] + ".files.json")
        for named in (self.archive, file_list):
            self.assertRegex(named["sha256"], "^[0-9a-f]{64}$")
            self.assertIsInstance(named["bytes"], int)
            self.assertGreater(named["bytes"], 0)
        self.assertNotIn("files", self.archive, "the per-file digests live in the file list beside the archive")

    def test_every_summarized_attempt_is_named_once_with_its_record_digest(self) -> None:
        named = [(attempt["run"], attempt["task"], attempt["arm"], int(attempt["attempt"])) for attempt in self.manifest["attempts"]]
        self.assertEqual(len(named), len(set(named)))
        summarized = []
        for run_name, paths in report.ARCHIVE_RUNS.items():
            for entry in json.loads((RESULTS / paths["array"]).read_text(encoding="utf-8")):
                summarized.append((run_name, entry["task"], entry["arm"], int(entry["attempt"])))
        self.assertEqual(sorted(named), sorted(summarized))
        for attempt in self.manifest["attempts"]:
            self.assertEqual(attempt["record"], f"records/{attempt['task']}/{attempt['arm']}/{attempt['attempt']}.json", attempt)
            self.assertRegex(attempt["sha256"], "^[0-9a-f]{64}$", attempt)

    def test_the_commands_reproduce_every_committed_output_of_the_archive_mode(self) -> None:
        commands = "\n".join(self.archive["reproduce"]["commands"])
        for paths in report.ARCHIVE_RUNS.values():
            for key in ("array", "rescored", "conditions"):
                self.assertIn(f"evals/cross_harness/results/{paths[key]}", commands)
        self.assertEqual(sorted(self.archive["runs"]), sorted(report.ARCHIVE_RUNS))
        for run_name in self.archive["runs"]:
            self.assertIn(f'--out "$EVIDENCE/{run_name}"', commands)


_NUMBER = re.compile(r"(?<![\w.])([−-]?\+?\d[\d,]*(?:\.\d+)?)(\s?[kM%×]?)")


def _numbers(text: str) -> list[tuple[float, float]]:
    """Each number in the text with half a unit of its last stated digit, scaled by a k or M suffix."""
    out = []
    for match in _NUMBER.finditer(text):
        clean = match.group(1).replace(",", "").replace("−", "-").replace("+", "")
        scale = {"k": 1e3, "M": 1e6}.get(match.group(2).strip(), 1.0)
        decimals = len(clean.split(".")[1]) if "." in clean else 0
        out.append((abs(float(clean)) * scale, 0.5 * 10**-decimals * scale))
    return out


if __name__ == "__main__":
    unittest.main()
