#!/usr/bin/python3
"""Unit tests for the label-leakage gate: a fake foe script prints outcome lines, and no model is called."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import label_leakage  # noqa: E402
import protocol  # noqa: E402

EXAMPLES = HERE.parent / "tasks" / "examples"
BUILT_FOE = HERE.parent.parent.parent / "target" / "debug" / "foe"
GIT = ["/usr/bin/git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid"]

# A stand-in for the foe binary. It reads the probe document the command
# names and its answers from a file beside itself: a JSON object whose
# `class` member maps a substring of the task text to the class to return
# and whose `recall` member is the value the recall probe returns, or the
# word `blocked` or `silent`. It prints the outcome line the binary prints.
FAKE_FOE = textwrap.dedent(
    """\
    #!/usr/bin/python3
    import json, os, sys
    args = sys.argv[1:]
    here = os.path.dirname(os.path.abspath(__file__))
    answers = json.loads(open(os.path.join(here, "answers.json"), encoding="utf-8").read())
    config = json.load(open(args[args.index("--config") + 1], encoding="utf-8"))
    log_dir = args[args.index("--log-dir") + 1]
    assert args[args.index("--viewer") + 1] == "off"
    assert config["tools"] == ["block"], config["tools"]
    assert "returns" in config["done_when"]
    episode = os.path.join(log_dir, "ep_probe")
    os.makedirs(episode)
    open(os.path.join(episode, "episode.jsonl"), "w").close()
    print("foe: log " + episode, file=sys.stderr)
    if answers == "silent":
        sys.exit(1)
    if answers == "blocked":
        print(json.dumps({"kind": "blocked", "code": "ambiguous-task", "message": "The text settles nothing."}))
        sys.exit(2)
    if config["name"].endswith("recall"):
        value = answers["recall"]
    else:
        value = {"class_name": next(name for marker, name in answers["class"].items() if marker in config["task"]), "reason": "the listing"}
    print(json.dumps({"kind": "completed", "value": value}))
    """
)


def synthetic_repository(root: Path) -> tuple[Path, str]:
    """A repository holding a tree in one commit, and that commit."""
    repo = root / "foe-tree-repo"
    repo.mkdir()
    subprocess.run([*GIT, "-c", "init.defaultBranch=main", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "src").mkdir()
    (repo / "src" / "lib.rs").write_text("pub fn one() -> u32 { 1 }\n", encoding="utf-8")
    (repo / "checks").mkdir()
    (repo / "checks" / "run.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    subprocess.run([*GIT, "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run([*GIT, "-C", str(repo), "commit", "-q", "-m", "Start the tree"], check=True, capture_output=True)
    commit = subprocess.run([*GIT, "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    return repo, commit


class Gate(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="label-leakage-test-")
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.foe = self.bin / "fake-foe"
        self.foe.write_text(FAKE_FOE, encoding="utf-8")
        self.foe.chmod(0o755)
        self.answer({"class": {"greeting.py": "solvable", "Make the check pass": "contradictory"}, "recall": {"repository": "foe-tree-repo", "feature": "the check", "confidence": "recognized"}})
        self.out = self.root / "out"
        self.tasks = self.root / "tasks"
        shutil.copytree(EXAMPLES / "hello-solvable", self.tasks / "hello-solvable")
        self.repo, self.commit = synthetic_repository(self.root)
        self.recipe_task = self.tasks / "synthetic-recipe"
        task = protocol.Task(
            name="synthetic-recipe",
            family="autonomy",
            class_name="contradictory",
            text=protocol.autonomy_text("Make the check pass while keeping src/lib.rs as it is."),
            correct_statuses=frozenset({"blocked"}),
            correct_codes=frozenset({"goal-unreachable"}),
            budget={"model_calls": 4, "input_tokens": 1000, "output_tokens": 500, "seconds": 60},
            protected=("checks",),
            metadata={"source": {"commit": self.commit, "repo": str(self.repo), "subject": "Start the tree"}},
        )
        protocol.save(task, self.recipe_task)
        grade = self.recipe_task / "grader" / "grade"
        grade.parent.mkdir(parents=True)
        grade.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        grade.chmod(0o755)
        (self.recipe_task / "grader" / "workspace.patch").write_text("", encoding="utf-8")
        self.document = self.root / "gate.json"
        self.document.write_text(json.dumps({"tasks": str(self.tasks), "model": {"route": "subscription", "name": "fixture-model"}, "harnesses": {"foe": str(self.foe)}, "out": str(self.out)}), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def answer(self, answers: Any) -> None:
        (self.bin / "answers.json").write_text(json.dumps(answers), encoding="utf-8")

    def main(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = label_leakage.main([str(self.document), *extra])
        return status, out.getvalue(), err.getvalue()

    def test_without_confirmation_the_call_count_is_printed_and_nothing_runs(self) -> None:
        status, out, _ = self.main()
        self.assertEqual(status, label_leakage.NOTHING_LAUNCHED)
        self.assertIn("3 model calls over 2 tasks, one class probe each and a recall probe for the 1 authored from a feature commit, calling fixture-model over the subscription route", out)
        self.assertFalse(self.out.exists())

    def test_the_probes_run_and_the_verdict_confusion_and_recall_are_printed_and_written(self) -> None:
        status, out, err = self.main("--confirm-spend")
        self.assertEqual(status, label_leakage.FAILS, err)
        self.assertIn("label non-leakage gate, autonomy family, 2 tasks", out)
        self.assertIn("accuracy 100 percent (2 of 2); chance 25 percent over the 4 classes the probe offers, of which 2 are present, balanced", out)
        self.assertIn("the plan's threshold is 40 percent on 4 balanced classes, chance plus 15 percent; at the same margin over chance for the 4 classes offered the threshold is 40 percent", out)
        self.assertIn("verdict: FAILS, the class is recoverable from the text and the listing", out)
        self.assertIn("synthetic-recipe: repository 'foe-tree-repo' (names the repository: true 'foe-tree-repo'), feature 'the check' (recognized); true feature 'Start the tree'", out)
        written = json.loads((self.out / label_leakage.RESULT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(written["calls"], 3)
        self.assertEqual(written["confusion"]["solvable"], {"solvable": 1, "contradictory": 0, "missing-capability": 0, "non-terminating": 0, "none": 0})
        self.assertEqual(written["confusion"]["contradictory"]["contradictory"], 1)
        self.assertEqual(written["verdict"]["fails"], True)
        self.assertEqual([item["predicted"] for item in written["results"]], ["solvable", "contradictory"])
        self.assertEqual(written["results"][1]["listing_files"], 2)
        self.assertEqual(written["recall"][0]["true"], {"repository": "foe-tree-repo", "commit": self.commit, "feature": "Start the tree"})
        self.assertTrue(written["recall"][0]["repository_named"])
        # The class probe shows the task text and the listing, admits only the family's classes, and holds no tool beyond `block`.
        config = json.loads(Path(written["results"][1]["config"]).read_text(encoding="utf-8"))
        self.assertEqual(config["tools"], ["block"])
        self.assertEqual(config["done_when"]["returns"]["properties"]["class_name"]["enum"], list(protocol.CLASSES["autonomy"]))
        self.assertTrue(config["task"].startswith("Make the check pass while keeping src/lib.rs as it is."))
        self.assertTrue(config["task"].endswith("File listing of the workspace, one path per line:\nchecks/run.sh\nsrc/lib.rs"))
        self.assertEqual(config["model"], {"provider": "openai-codex", "model": "fixture-model", "reasoning_effort": "medium"})
        self.assertEqual(config["budget"], label_leakage.PROBE_BUDGET)
        # The recall probe shows the text alone.
        recall = json.loads(Path(written["recall"][0]["config"]).read_text(encoding="utf-8"))
        self.assertNotIn("File listing", recall["task"])
        self.assertEqual(sorted(recall["done_when"]["returns"]["properties"]), ["confidence", "feature", "repository"])
        # The regenerated workspace was removed once listed, and the task directory holds no copy.
        self.assertFalse((self.recipe_task / "workspace").exists())
        self.assertEqual(sorted(path.name for path in self.recipe_task.iterdir()), ["grader", "task.json"])

    def test_a_probe_that_blocks_or_prints_nothing_counts_as_no_prediction(self) -> None:
        self.answer("blocked")
        status, out, err = self.main("--confirm-spend")
        self.assertEqual(status, label_leakage.PASSES, err)
        self.assertIn("accuracy 0 percent (0 of 2)", out)
        self.assertIn("verdict: passes", out)
        self.assertIn("synthetic-recipe: no answer (the probe ended blocked: The text settles nothing.)", out)
        written = json.loads((self.out / label_leakage.RESULT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(written["confusion"]["solvable"]["none"], 1)
        self.assertEqual(written["results"][0]["fault"], "the probe ended blocked: The text settles nothing.")
        shutil.rmtree(self.out)
        self.answer("silent")
        status, _, err = self.main("--confirm-spend")
        self.assertEqual(status, label_leakage.PASSES, err)
        written = json.loads((self.out / label_leakage.RESULT_FILE).read_text(encoding="utf-8"))
        self.assertTrue(written["results"][0]["fault"].startswith("no outcome line; exit status 1"))
        self.assertIsNone(written["recall"][0]["answer"])

    def test_the_verdict_applies_the_plan_margin_over_chance_for_the_classes_the_probe_offers(self) -> None:
        autonomy, teams = protocol.CLASSES["autonomy"], protocol.CLASSES["teams"]
        four = [("solvable", "solvable"), ("contradictory", "solvable"), ("missing-capability", "solvable"), ("non-terminating", "solvable")]
        verdict = label_leakage.verdict_of(autonomy, four)
        self.assertEqual((verdict["accuracy"], verdict["chance"], verdict["threshold"], verdict["fails"], verdict["balanced"]), (0.25, 0.25, 0.4, False, True))
        self.assertEqual(verdict["classes_offered"], list(autonomy))
        # Chance is set by the four classes the probe offers, so a selection of one class, or of two, keeps the plan's 40 percent threshold.
        one = label_leakage.verdict_of(autonomy, [("solvable", "solvable")] * 2)
        self.assertEqual((one["accuracy"], one["chance"], one["threshold"], one["fails"], one["classes_present"]), (1.0, 0.25, 0.4, True, ["solvable"]))
        two = label_leakage.verdict_of(autonomy, [("solvable", "solvable"), ("contradictory", "solvable")])
        self.assertEqual((two["accuracy"], two["threshold"], two["fails"]), (0.5, 0.4, True))
        three = [("fan-out", "fan-out"), ("survey", "survey"), ("coherent", "fan-out"), ("coherent", "coherent")]
        verdict = label_leakage.verdict_of(teams, three)
        self.assertAlmostEqual(verdict["threshold"], 1 / 3 + 0.15)
        self.assertEqual((verdict["accuracy"], verdict["fails"], verdict["balanced"], verdict["per_class"]), (0.75, True, False, {"coherent": 2, "fan-out": 1, "survey": 1}))
        self.assertEqual(label_leakage.verdict_of(autonomy, [])["fails"], False)
        table = label_leakage.confusion(("a", "b"), [("a", "b"), ("a", "other"), ("b", "b")])
        self.assertEqual(table, {"a": {"a": 0, "b": 1, "none": 1}, "b": {"a": 0, "b": 1, "none": 0}})

    def test_the_listing_leaves_out_generated_directories_and_reads_a_kept_copy(self) -> None:
        workspace = self.tasks / "hello-solvable" / "workspace"
        for name in protocol.GENERATED_DIRECTORIES:
            (workspace / name).mkdir()
            (workspace / name / "junk").write_text("", encoding="utf-8")
        listing = label_leakage.workspace_listing(self.tasks / "hello-solvable")
        self.assertEqual(listing, ["AGENTS.md", "src/__init__.py", "src/greeting.py", "tests/__init__.py", "tests/test_greeting.py"])
        self.assertEqual(label_leakage.workspace_listing(self.recipe_task), ["checks/run.sh", "src/lib.rs"])
        self.assertIsNone(label_leakage.true_source(protocol.load(self.tasks / "hello-solvable"), self.tasks / "hello-solvable"))

    def test_only_a_task_recording_a_feature_commit_gets_the_recall_probe(self) -> None:
        """A constructed recipe records its base commit alone; a task authored from a feature records the commit's subject and parent."""
        task = protocol.load(self.recipe_task)
        self.assertEqual(label_leakage.true_source(task, self.recipe_task), {"repository": "foe-tree-repo", "commit": self.commit, "feature": "Start the tree"})
        constructed = protocol.Task.from_dict({**task.to_dict(), "metadata": {"source": {"commit": self.commit, "repo": str(self.repo)}}})
        self.assertIsNone(label_leakage.true_source(constructed, self.recipe_task))
        by_parent = protocol.Task.from_dict({**task.to_dict(), "metadata": {"source": {"commit": self.commit, "repo": str(self.repo), "parent": "0" * 40}}})
        self.assertEqual(label_leakage.true_source(by_parent, self.recipe_task), {"repository": "foe-tree-repo", "commit": self.commit, "feature": None})
        protocol.save(constructed, self.recipe_task)
        status, out, _ = self.main()
        self.assertEqual(status, label_leakage.NOTHING_LAUNCHED)
        self.assertIn("2 model calls over 2 tasks, one class probe each and a recall probe for the 0 authored from a feature commit", out)

    def test_a_document_the_runner_refuses_is_refused_here_by_the_same_key(self) -> None:
        self.document.write_text(json.dumps({"tasks": str(self.tasks), "model": {"route": "subscription", "name": "m"}, "harnesses": {"foe": str(self.root / "absent")}}), encoding="utf-8")
        status, _, err = self.main()
        self.assertEqual(status, label_leakage.NOTHING_LAUNCHED)
        self.assertIn("key harnesses.foe names", err)

    @unittest.skipUnless(BUILT_FOE.is_file() and os.access(BUILT_FOE, os.X_OK), f"{BUILT_FOE} is absent; build it with cargo build -p foe")
    def test_the_built_binary_accepts_both_probe_documents(self) -> None:
        """`foe plan` resolves a document without running it, so the check spends nothing."""
        import foe_arm  # noqa: PLC0415

        task = protocol.load(self.tasks / "hello-solvable")
        route = foe_arm.ModelRoute("compatible-http", "fixture-model", "http://127.0.0.1:9/v1")
        read_root = self.root / "empty"
        read_root.mkdir()
        for name, document in (("class", label_leakage.class_probe(task, ["AGENTS.md"], read_root, route, "medium")), ("recall", label_leakage.recall_probe(task, read_root, route, "medium"))):
            path = self.root / f"{name}.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            completed = subprocess.run([str(BUILT_FOE), "plan", "--config", str(path)], capture_output=True, text=True, timeout=120, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("completion  typed return", completed.stdout)


if __name__ == "__main__":
    unittest.main()
