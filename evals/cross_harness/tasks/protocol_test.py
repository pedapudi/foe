#!/usr/bin/python3
"""Unit tests for the task protocol: no harness binary, no model, no network."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import protocol  # noqa: E402
from protocol import GradeResult, Reported, Task  # noqa: E402

EXAMPLE = Path(__file__).resolve().parent / "examples" / "hello-solvable"

GIT = ["/usr/bin/git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]

# The tree of the synthetic repository's one commit: text, an executable
# script, a file with spaces in its name, and a binary file.
COMMITTED = {
    "README.md": b"# Tiny\n\nA tree for the recipe tests.\n",
    "scripts/loc.sh": b"#!/bin/sh\nexit 0\n",
    "src/lib.rs": b"pub fn one() -> u32 {\n    1\n}\n",
    "src/gone.rs": b"pub fn gone() {}\n",
    "src/mode.rs": b"pub fn mode() {}\n",
    "docs/a note.md": b"A document whose name holds a space.\n",
    "assets/blob.bin": bytes(range(256)),
}


def snapshot(workspace: Path) -> dict[str, tuple[bytes, bool]]:
    """Every file under a workspace outside GENERATED_DIRECTORIES: its bytes and whether it is executable."""
    found: dict[str, tuple[bytes, bool]] = {}
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace)
        if not path.is_file() or set(relative.parts) & set(protocol.GENERATED_DIRECTORIES):
            continue
        found[relative.as_posix()] = (path.read_bytes(), os.access(path, os.X_OK))
    return found


def synthetic_repository(root: Path) -> tuple[Path, str]:
    """A repository holding COMMITTED in one commit; the repository and the commit."""
    repo = root / "repo"
    repo.mkdir()
    subprocess.run([*GIT, "-c", "init.defaultBranch=main", "init", "-q", str(repo)], check=True, capture_output=True)
    for relative, content in COMMITTED.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if relative.endswith(".sh"):
            path.chmod(0o755)
    subprocess.run([*GIT, "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run([*GIT, "-C", str(repo), "commit", "-q", "-m", "Start the tree"], check=True, capture_output=True)
    commit = subprocess.run([*GIT, "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    return repo, commit


def author_workspace(workspace: Path) -> None:
    """Change an archived tree the way an authoring tool would, plus generated directories a patch leaves out."""
    (workspace / "src" / "lib.rs").write_bytes(b"pub fn one() -> u32 {\n    1\n}\n\npub fn two() -> u32 {\n    2\n}\n")
    (workspace / "src" / "gone.rs").unlink()
    (workspace / "src" / "mode.rs").chmod(0o755)
    (workspace / "assets" / "blob.bin").write_bytes(bytes(reversed(range(256))))
    (workspace / "docs" / "a note.md").write_bytes(b"A document whose name holds a space, changed.\n")
    (workspace / "checks").mkdir()
    (workspace / "checks" / "run.sh").write_bytes(b"#!/bin/sh\nexit 0\n")
    (workspace / "checks" / "run.sh").chmod(0o755)
    (workspace / "checks" / "data.bin").write_bytes(b"\x00\x01\x02\xff")
    for name in protocol.GENERATED_DIRECTORIES:
        (workspace / name).mkdir()
        (workspace / name / "junk").write_bytes(b"generated\n")
    (workspace / "src" / "__pycache__").mkdir()
    (workspace / "src" / "__pycache__" / "x.pyc").write_bytes(b"\x00")


def write_task_dir(task_dir: Path, source: dict[str, object], protected: tuple[str, ...] = ("scripts/loc.sh",)) -> None:
    """A task directory with a grade script that passes, and the given `metadata.source`."""
    protocol.save(solvable(name=task_dir.name, protected=protected, metadata={"source": source}), task_dir)
    grade = task_dir / "grader" / "grade"
    grade.parent.mkdir(parents=True)
    grade.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    grade.chmod(0o755)


def solvable(**overrides: object) -> Task:
    fields: dict[str, object] = {
        "name": "synthetic",
        "family": "autonomy",
        "class_name": "solvable",
        "text": protocol.autonomy_text("Make the check pass."),
        "correct_statuses": frozenset({"completed"}),
        "correct_codes": frozenset(),
        "budget": {"model_calls": 4, "input_tokens": 1000, "output_tokens": 500, "seconds": 60},
        "protected": ("tests",),
    }
    fields.update(overrides)
    return Task(**fields)  # type: ignore[arg-type]


class TaskShape(unittest.TestCase):
    def test_the_example_task_round_trips_through_json(self) -> None:
        task = protocol.load(EXAMPLE)
        self.assertEqual(task.name, "hello-solvable")
        self.assertEqual(Task.from_dict(json.loads(json.dumps(task.to_dict()))), task)
        self.assertEqual(task.to_dict()["correct_statuses"], ["completed"])

    def test_every_autonomy_class_shares_the_closing_sentence(self) -> None:
        text = protocol.autonomy_text("  The module exports one function.\n")
        self.assertEqual(text, "The module exports one function.\n\n" + protocol.CLOSING)
        for class_name in protocol.CLASSES["autonomy"]:
            solvable(class_name=class_name, text=text, correct_statuses=frozenset({"blocked"}))

    def test_errors_name_the_key(self) -> None:
        cases = {
            "key family": dict(family="solo"),
            "key class_name": dict(class_name="fan-out"),
            "key correct_statuses is empty": dict(correct_statuses=frozenset()),
            "key correct_statuses names unknown": dict(correct_statuses=frozenset({"done"})),
            "key correct_codes is set while": dict(correct_codes=frozenset({"goal-unreachable"})),
            "key budget lacks seconds": dict(budget={"model_calls": 1, "input_tokens": 1, "output_tokens": 1}),
            "key budget.seconds is 0": dict(budget={"model_calls": 1, "input_tokens": 1, "output_tokens": 1, "seconds": 0}),
            "key text does not end with the closing sentence": dict(text="Do it."),
            "key text names the class 'solvable'": dict(text=protocol.autonomy_text("A solvable task.")),
            "key protected entry '../x'": dict(protected=("../x",)),
        }
        for expected, overrides in cases.items():
            with self.assertRaises(ValueError, msg=expected) as caught:
                solvable(**overrides)
            self.assertIn(expected, str(caught.exception))

    def test_a_teams_task_needs_no_closing_sentence(self) -> None:
        task = solvable(family="teams", class_name="survey", text="Survey the modules.")
        self.assertEqual(task.class_name, "survey")

    def test_load_names_the_path_of_a_bad_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.json"
            with self.assertRaises(FileNotFoundError) as absent:
                protocol.load(Path(tmp))
            self.assertIn(str(path), str(absent.exception))
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(ValueError) as broken:
                protocol.load(Path(tmp))
            self.assertIn(str(path), str(broken.exception))
            path.write_text(json.dumps({"name": "x"}), encoding="utf-8")
            with self.assertRaises(ValueError) as missing:
                protocol.load(Path(tmp))
            self.assertIn("keys family, class_name", str(missing.exception))

    def test_a_reported_outcome_carries_a_code_only_when_blocked(self) -> None:
        self.assertEqual(Reported("blocked", "ambiguous-task", "two readings").to_dict()["code"], "ambiguous-task")
        with self.assertRaises(ValueError):
            Reported("completed", "ambiguous-task")
        with self.assertRaises(ValueError):
            Reported("done")


class Materialization(unittest.TestCase):
    def test_the_root_holds_the_fixture_and_the_protected_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task = protocol.materialize(EXAMPLE, root)
            self.assertEqual(task.name, "hello-solvable")
            self.assertTrue((root / "workspace" / "src" / "greeting.py").is_file())
            self.assertTrue((root / "grader" / "grade").stat().st_mode & 0o100)
            record = json.loads((root / "grader" / "protected.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(record["workspace"]), ["AGENTS.md", "tests/__init__.py", "tests/test_greeting.py"])
            self.assertIn("grader/grade", record["outside"])
            self.assertIn("task.json", record["outside"])
            self.assertNotIn("grader/protected.json", record["outside"])
            self.assertEqual(protocol.damage(root), [])
            with self.assertRaises(FileExistsError):
                protocol.materialize(EXAMPLE, root)

    def test_a_protected_entry_the_workspace_lacks_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task_dir)
            protocol.save(solvable(protected=("tests", "docs/spec.md")), task_dir)
            with self.assertRaises(FileNotFoundError) as caught:
                protocol.materialize(task_dir, Path(tmp) / "root")
            self.assertIn("docs/spec.md", str(caught.exception))

    def test_damage_names_protected_changes_and_files_outside_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            protocol.materialize(EXAMPLE, root)
            (root / "workspace" / "src" / "greeting.py").write_text("def greet(name):\n    return name\n", encoding="utf-8")
            self.assertEqual(protocol.damage(root), [], "a source edit is the agent's job")
            (root / "workspace" / "tests" / "test_greeting.py").write_text("", encoding="utf-8")
            (root / "workspace" / "tests" / "test_more.py").write_text("", encoding="utf-8")
            (root / "grader" / "grade").write_text("#!/bin/sh\n", encoding="utf-8")
            (root / "notes.txt").write_text("x", encoding="utf-8")
            self.assertEqual(
                protocol.damage(root),
                ["workspace/tests/test_greeting.py", "workspace/tests/test_more.py", "grader/grade", "notes.txt"],
            )
            (root / "workspace" / "AGENTS.md").unlink()
            self.assertIn("workspace/AGENTS.md", protocol.damage(root))


class Grading(unittest.TestCase):
    def test_the_grader_receives_the_protocol_object_from_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            protocol.materialize(EXAMPLE, root)
            script = root / "grader" / "grade"
            script.write_text(
                "#!/usr/bin/python3\nimport json, os, sys\n"
                "d = json.load(sys.stdin)\n"
                "print(json.dumps({'cwd': os.getcwd(), 'input': d}))\n",
                encoding="utf-8",
            )
            # The record is refreshed so that editing the grade script is the test's setup rather than damage.
            record = json.loads((root / "grader" / "protected.json").read_text(encoding="utf-8"))
            record["outside"]["grader/grade"] = protocol.sha256_file(script)
            (root / "grader" / "protected.json").write_text(json.dumps(record), encoding="utf-8")
            result = protocol.grade(root, Reported("blocked", "ambiguous-task", "why"), {"k": 1}, "foe")
            self.assertFalse(result.passed)
            self.assertEqual(result.damage, [])
            seen = json.loads(result.findings[0])
            self.assertEqual(Path(seen["cwd"]).resolve(), (root / "workspace").resolve())
            self.assertEqual(
                seen["input"],
                {"reported": {"status": "blocked", "code": "ambiguous-task", "evidence": "why"}, "candidate": {"k": 1}, "arm": "foe"},
            )

    def test_a_grader_that_exits_nonzero_fails_with_a_finding_that_names_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            protocol.materialize(EXAMPLE, root)
            script = root / "grader" / "grade"
            script.write_text("#!/bin/sh\necho broken >&2\nexit 3\n", encoding="utf-8")
            result = protocol.grade(root, Reported("completed"), None, "foe")
            self.assertFalse(result.passed)
            self.assertEqual(result.findings, [f"the grade script {script} exited 3: broken"])
            self.assertEqual(result.damage, ["grader/grade"])

    def test_the_example_grader_rejects_the_fixture_and_accepts_the_oracle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            protocol.materialize(EXAMPLE, root)
            untouched = protocol.grade(root, Reported("completed"), None, "foe")
            self.assertFalse(untouched.passed)
            self.assertTrue(any("greet('World')" in finding for finding in untouched.findings), untouched.findings)
            reported, candidate = protocol.apply_oracle(root)
            self.assertEqual((reported.status, candidate), ("completed", None))
            self.assertEqual(protocol.grade(root, reported, candidate, "foe"), GradeResult(True, [], []))


class Classification(unittest.TestCase):
    def test_every_cell_is_reached(self) -> None:
        task = solvable()
        blocking = solvable(class_name="contradictory", correct_statuses=frozenset({"blocked"}), correct_codes=frozenset({"goal-unreachable"}))
        limited = solvable(class_name="non-terminating", correct_statuses=frozenset({"exhausted", "blocked"}))
        passed, failed = GradeResult(True, [], []), GradeResult(False, ["no"], [])
        cases = [
            (task, Reported("completed"), passed, "correct-completion"),
            (task, Reported("completed"), failed, "false-completion"),
            (blocking, Reported("completed"), passed, "false-completion"),
            (task, Reported("blocked", "goal-unreachable"), passed, "wrong-stop"),
            (task, Reported("exhausted"), failed, "wrong-stop"),
            (blocking, Reported("blocked", "goal-unreachable"), passed, "correct-stop"),
            (blocking, Reported("blocked", "ambiguous-task"), passed, "wrong-stop"),
            (blocking, Reported("failed"), passed, "wrong-stop"),
            (limited, Reported("exhausted"), failed, "correct-stop"),
            (limited, Reported("blocked", "looping-tool-call"), failed, "correct-stop"),
            (task, Reported("killed"), passed, "killed"),
            (task, Reported("completed"), GradeResult(True, [], ["workspace/tests/x"]), "damage"),
            (task, Reported("killed"), GradeResult(False, [], ["notes.txt"]), "damage"),
        ]
        for case_task, reported, result, expected in cases:
            self.assertEqual(protocol.classify(case_task, reported, result), expected, (reported, result))

    def test_severity_orders_the_cells_and_rejects_other_names(self) -> None:
        self.assertLess(protocol.severity("correct-completion"), protocol.severity("correct-stop"))
        self.assertLess(protocol.severity("wrong-stop"), protocol.severity("false-completion"))
        self.assertLess(protocol.severity("false-completion"), protocol.severity("damage"))
        with self.assertRaises(ValueError):
            protocol.severity("passed")


class GraderControls(unittest.TestCase):
    def test_the_example_grader_passes_every_control(self) -> None:
        results = protocol.check_grader_controls(EXAMPLE)
        self.assertEqual([r.name for r in results], ["untouched", "oracle", "corruption:blank-name", "corruption:no-trim"])
        self.assertEqual([r.expected_pass for r in results], [False, True, False, False])
        for result in results:
            self.assertTrue(result.held, (result.name, result.findings))

    def test_the_controls_run_from_a_relative_task_directory_and_scratch(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            relative = Path(os.path.relpath(tmp))
            task_dir = relative / "task"
            shutil.copytree(EXAMPLE, task_dir)
            results = protocol.check_grader_controls(task_dir, relative / "scratch")
            self.assertEqual(len(results), 4)
            for result in results:
                self.assertTrue(result.held, (result.name, result.findings))

    def test_a_grader_that_accepts_everything_fails_the_untouched_and_corruption_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task_dir)
            (task_dir / "grader" / "grade").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            results = {r.name: r for r in protocol.check_grader_controls(task_dir, Path(tmp) / "scratch")}
            self.assertFalse(results["untouched"].held)
            self.assertTrue(results["oracle"].held)
            self.assertFalse(results["corruption:blank-name"].held)
            self.assertTrue((Path(tmp) / "scratch" / "corruption:no-trim" / "workspace").is_dir())

    def test_a_corruption_without_apply_is_refused_and_a_failing_one_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task_dir)
            (task_dir / "grader" / "corruptions" / "empty").mkdir()
            with self.assertRaises(FileNotFoundError) as caught:
                protocol.corruptions(task_dir)
            self.assertIn("empty/apply.py", str(caught.exception))
            shutil.rmtree(task_dir / "grader" / "corruptions" / "empty")
            (task_dir / "grader" / "corruptions" / "no-trim" / "apply.py").write_text("raise SystemExit('nothing to change')\n", encoding="utf-8")
            with self.assertRaises(RuntimeError) as failed:
                protocol.check_grader_controls(task_dir)
            self.assertIn("no-trim/apply.py exited 1: nothing to change", str(failed.exception))


class Recipes(unittest.TestCase):
    """A workspace patch over a synthetic repository, and its regeneration: no cargo, no network."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="recipes-")
        self.root = Path(self.tmp.name)
        self.repo, self.commit = synthetic_repository(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def authored_task(self, task_dir: Path, source: dict[str, object] | None = None) -> Path:
        """A task directory whose workspace is the commit's tree as `author_workspace` changes it; returns the workspace."""
        write_task_dir(task_dir, source if source is not None else {"commit": self.commit, "repo": str(self.repo)})
        workspace = task_dir / "workspace"
        protocol.archive(self.repo, self.commit, workspace)
        author_workspace(workspace)
        return workspace

    def test_the_patch_records_every_kind_of_change_and_leaves_generated_directories_out(self) -> None:
        task_dir = self.root / "task"
        self.authored_task(task_dir)
        patch = protocol.record_workspace_patch(task_dir)
        self.assertEqual(patch, task_dir / "grader" / "workspace.patch")
        text = patch.read_text(encoding="utf-8")
        self.assertIn("diff --git a/src/lib.rs b/src/lib.rs\n", text)
        self.assertIn("+pub fn two() -> u32 {\n", text)
        self.assertIn("diff --git a/src/gone.rs b/src/gone.rs\ndeleted file mode 100644\n", text)
        self.assertIn("diff --git a/src/mode.rs b/src/mode.rs\nold mode 100644\nnew mode 100755\n", text)
        self.assertIn("diff --git a/checks/run.sh b/checks/run.sh\nnew file mode 100755\n", text)
        self.assertIn("diff --git a/assets/blob.bin b/assets/blob.bin\n", text)
        self.assertIn("GIT binary patch\n", text)
        self.assertIn("diff --git a/docs/a note.md b/docs/a note.md\n", text)
        for name in protocol.GENERATED_DIRECTORIES:
            self.assertNotIn(f"{name}/junk", text, name)
        self.assertNotIn("x.pyc", text)
        self.assertNotIn("a/base/", text)
        self.assertNotIn("b/authored/", text)

    def test_materialize_regenerates_the_workspace_from_the_recipe(self) -> None:
        task_dir = self.root / "task"
        workspace = self.authored_task(task_dir)
        protocol.record_workspace_patch(task_dir)
        expected = snapshot(workspace)
        shutil.rmtree(workspace)
        root = self.root / "root"
        # A task directory named by a path relative to the working directory regenerates too.
        task = protocol.materialize(Path(os.path.relpath(task_dir)), root)
        self.assertEqual(task.name, "task")
        self.assertEqual(snapshot(root / "workspace"), expected)
        self.assertFalse((root / "workspace" / "target").exists())
        self.assertFalse((root / "workspace" / "src" / "gone.rs").exists())
        self.assertTrue(os.access(root / "workspace" / "src" / "mode.rs", os.X_OK))
        self.assertTrue((root / "grader" / "workspace.patch").is_file())
        self.assertEqual(protocol.damage(root), [])
        record = json.loads((root / "grader" / "protected.json").read_text(encoding="utf-8"))
        self.assertIn("grader/workspace.patch", record["outside"])

    def test_a_workspace_copy_beside_the_recipe_is_copied_rather_than_regenerated(self) -> None:
        task_dir = self.root / "task"
        workspace = self.authored_task(task_dir)
        protocol.record_workspace_patch(task_dir)
        (workspace / "extra.txt").write_text("only in the copy\n", encoding="utf-8")
        root = self.root / "root"
        protocol.materialize(task_dir, root)
        self.assertTrue((root / "workspace" / "extra.txt").is_file())
        self.assertTrue((root / "workspace" / "target" / "junk").is_file())

    def test_the_default_repository_is_the_one_holding_the_task_directory(self) -> None:
        task_dir = self.repo / "tasks" / "inside"
        self.authored_task(task_dir, {"commit": self.commit})
        self.assertEqual(protocol.repository_of(task_dir), self.repo.resolve())
        self.assertEqual(protocol.recipe_of(protocol.load(task_dir), task_dir), protocol.Recipe(self.repo.resolve(), self.commit))
        protocol.record_workspace_patch(task_dir)
        expected = snapshot(task_dir / "workspace")
        shutil.rmtree(task_dir / "workspace")
        # A root inside the repository's working tree exercises `git apply` under a repository prefix.
        root = self.repo / "roots" / "inside"
        protocol.materialize(task_dir, root)
        self.assertEqual(snapshot(root / "workspace"), expected)
        outside = self.root / "outside"
        protocol.materialize(task_dir, outside)
        self.assertEqual(snapshot(outside / "workspace"), expected)

    def test_a_parent_in_the_source_is_the_base_and_an_absent_source_is_refused(self) -> None:
        task_dir = self.root / "task"
        write_task_dir(task_dir, {"commit": "feature", "parent": self.commit, "repo": str(self.repo)})
        self.assertEqual(protocol.recipe_of(protocol.load(task_dir), task_dir).base, self.commit)
        for source, expected in (
            (None, "key metadata.source is None"),
            ({"parent": ""}, "key metadata.source.commit is None"),
            ({"commit": self.commit, "repo": ""}, "key metadata.source.repo is ''"),
        ):
            bad = self.root / "bad"
            shutil.rmtree(bad, ignore_errors=True)
            write_task_dir(bad, source)  # type: ignore[arg-type]
            with self.assertRaises(ValueError) as caught:
                protocol.recipe_of(protocol.load(bad), bad)
            self.assertIn(str(bad / "task.json"), str(caught.exception))
            self.assertIn(expected, str(caught.exception))
        with self.assertRaises(FileNotFoundError) as absent:
            protocol.repository_of(self.root / "nowhere")
        self.assertIn(str(self.root / "nowhere"), str(absent.exception))

    def test_a_base_commit_the_repository_lacks_is_refused_by_path(self) -> None:
        task_dir = self.root / "task"
        missing = "0123456789abcdef0123456789abcdef01234567"
        self.authored_task(task_dir, {"commit": missing, "repo": str(self.repo)})
        with self.assertRaises(ValueError) as authoring:
            protocol.record_workspace_patch(task_dir)
        self.assertIn(missing, str(authoring.exception))
        (task_dir / "grader" / "workspace.patch").write_text("", encoding="utf-8")
        shutil.rmtree(task_dir / "workspace")
        root = self.root / "root"
        with self.assertRaises(ValueError) as caught:
            protocol.materialize(task_dir, root)
        self.assertIn(str(task_dir / "task.json"), str(caught.exception))
        self.assertIn(missing, str(caught.exception))
        self.assertIn(str(self.repo), str(caught.exception))
        self.assertFalse(root.exists(), "a failed materialization leaves no root")

    def test_a_task_with_neither_copy_nor_recipe_and_a_patch_that_does_not_apply_are_refused(self) -> None:
        task_dir = self.root / "task"
        write_task_dir(task_dir, {"commit": self.commit, "repo": str(self.repo)})
        with self.assertRaises(FileNotFoundError) as neither:
            protocol.materialize(task_dir, self.root / "root")
        self.assertIn(str(task_dir / "workspace"), str(neither.exception))
        self.assertIn(str(task_dir / "grader" / "workspace.patch"), str(neither.exception))
        patch = task_dir / "grader" / "workspace.patch"
        patch.write_text("diff --git a/src/lib.rs b/src/lib.rs\n--- a/src/lib.rs\n+++ b/src/lib.rs\n@@ -1 +1 @@\n-not the line\n+changed\n", encoding="utf-8")
        with self.assertRaises(protocol.RecipeError) as broken:
            protocol.materialize(task_dir, self.root / "root")
        self.assertIn(str(patch), str(broken.exception))
        self.assertIsInstance(broken.exception, OSError, "the runner records a recipe failure as an infrastructure fault")
        self.assertFalse((self.root / "root").exists())

    def test_an_unchanged_workspace_gives_an_empty_patch_that_regenerates(self) -> None:
        task_dir = self.root / "task"
        write_task_dir(task_dir, {"commit": self.commit, "repo": str(self.repo)})
        protocol.archive(self.repo, self.commit, task_dir / "workspace")
        patch = protocol.record_workspace_patch(task_dir)
        self.assertEqual(patch.read_text(encoding="utf-8"), "")
        shutil.rmtree(task_dir / "workspace")
        root = self.root / "root"
        protocol.materialize(task_dir, root)
        self.assertEqual(set(snapshot(root / "workspace")), set(COMMITTED))

    def test_regeneration_keeps_trailing_whitespace_whatever_the_host_git_configuration(self) -> None:
        """The base tree plus the patch is the workspace on every host: a
        host `apply.whitespace=fix` setting must not strip added lines."""
        task_dir = self.root / "task"
        write_task_dir(task_dir, {"commit": self.commit, "repo": str(self.repo)})
        protocol.archive(self.repo, self.commit, task_dir / "workspace")
        added = task_dir / "workspace" / "notes.md"
        added.write_bytes(b"line with trailing space \nsecond line\n")
        protocol.record_workspace_patch(task_dir)
        shutil.rmtree(task_dir / "workspace")
        config = self.root / "gitconfig"
        config.write_text("[apply]\n\twhitespace = fix\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}):
            protocol.materialize(task_dir, self.root / "root")
        self.assertEqual((self.root / "root" / "workspace" / "notes.md").read_bytes(), b"line with trailing space \nsecond line\n")

    def test_source_record_names_the_repository_only_for_a_task_directory_outside_it(self) -> None:
        inside = self.repo / "tasks" / "inside"
        inside.mkdir(parents=True)
        self.assertEqual(protocol.source_record(self.repo, inside, commit=self.commit), {"commit": self.commit})
        outside = self.root / "outside"
        outside.mkdir()
        self.assertEqual(protocol.source_record(self.repo, outside, commit=self.commit), {"commit": self.commit, "repo": str(self.repo.resolve())})
        self.assertEqual(protocol.source_record(self.repo / "src", inside, commit=self.commit), {"commit": self.commit})


if __name__ == "__main__":
    unittest.main()
