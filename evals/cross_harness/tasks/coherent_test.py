#!/usr/bin/python3
"""Tests for the constructed coherent controls: the edits, the metadata, the text, the one-directory measurement, and the grade script's shape.

Nothing here builds a crate, starts a sandbox, calls a model, or reaches the
network. The tests that need the repository read one file at a time out of a
commit's tree with `git show`, so they cost a process each rather than a
checkout. A test that reads a construction's own base tree reads the commit
the emitted task directory records, and is skipped when that directory is
absent, so the suite states what the recorded tasks rest on rather than what
the branch happens to hold.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coherent  # noqa: E402
import feature_removal as removal  # noqa: E402
import protocol  # noqa: E402
import teams  # noqa: E402

TASK_TREE = Path(__file__).resolve().parent / "foe-tree"


def recorded_commit(name: str) -> str | None:
    """The base commit the emitted task directory records, or None when the directory is absent."""
    path = TASK_TREE / name / protocol.TASK_FILE
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["metadata"][protocol.SOURCE_KEY]["commit"]


class EditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="coherent-edit-"))
        (self.directory / "a.txt").write_text("one\ntwo\none\n", encoding="utf-8")

    def test_an_edit_replaces_its_source(self) -> None:
        coherent.Edit("a.txt", "two", "three").apply(self.directory)
        self.assertEqual((self.directory / "a.txt").read_text(encoding="utf-8"), "one\nthree\none\n")

    def test_an_edit_whose_source_stands_twice_is_refused(self) -> None:
        with self.assertRaises(ValueError) as raised:
            coherent.Edit("a.txt", "one", "four").apply(self.directory)
        self.assertIn("stands 2 times", str(raised.exception))

    def test_an_edit_whose_source_is_absent_is_refused(self) -> None:
        with self.assertRaises(ValueError) as raised:
            coherent.Edit("a.txt", "five", "six").apply(self.directory)
        self.assertIn("stands 0 times", str(raised.exception))

    def test_an_edit_of_an_absent_file_names_the_path(self) -> None:
        with self.assertRaises(FileNotFoundError) as raised:
            coherent.Edit("b.txt", "one", "two").apply(self.directory)
        self.assertIn("b.txt", str(raised.exception))


class CeilingTest(unittest.TestCase):
    def test_a_ceiling_moves_in_the_table_and_in_every_document_that_quotes_it(self) -> None:
        edits = coherent.ceiling_edits("tools     ", "coding tools", (("AGENTS.md", "`code` under CEILING"),), 1900, 1975)
        self.assertEqual([edit.path for edit in edits], ["scripts/loc.sh", "README.md", "AGENTS.md"])
        self.assertEqual(edits[0].old, "tools     | 1900 |")
        self.assertEqual(edits[0].new, "tools     | 1975 |")
        self.assertEqual(edits[1].old, "| coding tools | 1,900 |")
        self.assertEqual(edits[2].new, "`code` under 1,975")

    def test_a_group_ceiling_stands_in_no_readme_table_row(self) -> None:
        edits = coherent.ceiling_edits("tools+team ", None, (), 2765, 2915)
        self.assertEqual([edit.path for edit in edits], ["scripts/loc.sh"])

    def test_a_group_ceiling_never_exceeds_the_surfaces_it_bounds(self) -> None:
        # scripts/loc.sh refuses a group ceiling above the sum of its
        # surfaces' own, so the two raises of the coding-tools fixture agree.
        raised = {edit.old.split("|")[0].strip(): int(edit.new.split("|")[1]) for edit in coherent.TOOLS_CEILING if edit.path == "scripts/loc.sh"}
        self.assertEqual(raised["tools+team"], raised["tools"] + 940)


class ConstructionTest(unittest.TestCase):
    def test_both_constructions_are_defined_and_named_for_what_they_change(self) -> None:
        self.assertEqual(sorted(coherent.CONSTRUCTIONS), ["bounded-result-names-its-bound", "module-cites-its-specification"])
        for name, construction in coherent.CONSTRUCTIONS.items():
            self.assertEqual(construction.name, name)

    def test_every_construction_has_six_to_eight_units_in_distinct_files(self) -> None:
        for construction in coherent.CONSTRUCTIONS.values():
            files = [unit.file for unit in construction.units]
            with self.subTest(task=construction.name):
                self.assertTrue(6 <= len(construction.units) <= 8, f"{construction.name} has {len(construction.units)} units")
                self.assertEqual(len(set(files)), len(files), "two units share one file")
                self.assertEqual(len({unit.test_name for unit in construction.units}), len(files), "two units share one test")

    def test_no_unit_owns_the_shared_interface(self) -> None:
        # The corruptions rest on this: reverting a unit leaves the interface
        # standing, and renaming inside a unit leaves the table unmoved.
        for construction in coherent.CONSTRUCTIONS.values():
            with self.subTest(task=construction.name):
                self.assertTrue(construction.interface_paths)
                for unit in construction.units:
                    self.assertNotIn(unit.file, construction.interface_paths)

    def test_every_unit_file_lies_in_the_construction_s_crate(self) -> None:
        for construction in coherent.CONSTRUCTIONS.values():
            for unit in construction.units:
                with self.subTest(task=construction.name, unit=unit.name):
                    self.assertTrue(unit.file.startswith(construction.crate + "/"))

    def test_the_corruptions_name_units_the_construction_holds(self) -> None:
        for construction in coherent.CONSTRUCTIONS.values():
            names = [unit.name for unit in construction.units]
            with self.subTest(task=construction.name):
                self.assertIn(construction.revert_unit, names)
                self.assertIn(construction.rename_unit, names)
                renamed = next(unit for unit in construction.units if unit.name == construction.rename_unit)
                self.assertEqual(construction.rename.path, renamed.file, "the rename corruption edits its unit's own file")
                reverted = next(unit for unit in construction.units if unit.name == construction.revert_unit)
                self.assertTrue(
                    any(edit.path == reverted.file for edit in construction.oracle_edits),
                    "the reverted unit has no oracle edit to restore",
                )

    def test_every_unit_file_and_test_file_carries_an_oracle_edit_or_a_hidden_test(self) -> None:
        for construction in coherent.CONSTRUCTIONS.values():
            edited = {edit.path for edit in construction.oracle_edits}
            appended = {path for path, _ in construction.test_appends}
            for unit in construction.units:
                with self.subTest(task=construction.name, unit=unit.name):
                    self.assertIn(unit.file, edited)
                    self.assertIn(unit.test_file, appended)

    def test_the_oracle_writes_the_shared_interface(self) -> None:
        for construction in coherent.CONSTRUCTIONS.values():
            edited = {edit.path for edit in construction.oracle_edits}
            with self.subTest(task=construction.name):
                self.assertTrue(set(construction.interface_paths) <= edited)


class TaskTextTest(unittest.TestCase):
    def texts(self) -> list[tuple[str, str]]:
        return [(name, coherent.task_text(construction)) for name, construction in coherent.CONSTRUCTIONS.items()]

    def test_the_text_sits_in_the_band_the_other_classes_sit_in(self) -> None:
        for name, text in self.texts():
            with self.subTest(task=name):
                self.assertTrue(coherent.TEXT_MIN <= len(text) <= coherent.TEXT_MAX, f"{name}: {len(text)} characters")

    def test_the_text_ends_with_the_closing_sentence(self) -> None:
        for name, text in self.texts():
            with self.subTest(task=name):
                self.assertTrue(text.endswith(protocol.CLOSING))

    def test_the_text_names_the_shared_element_and_no_part(self) -> None:
        # How the work divides is what the family measures, so the text names
        # the table every part must agree with and none of the parts.
        for name, construction in coherent.CONSTRUCTIONS.items():
            text = coherent.task_text(construction)
            with self.subTest(task=name):
                for path in construction.interface_paths:
                    self.assertIn(path, text)
                for unit in construction.units:
                    self.assertNotIn(unit.file, text)
                self.assertNotIn("units", text)

    def test_the_change_lies_in_one_directory_which_is_what_makes_the_task_a_control(self) -> None:
        for name, construction in coherent.CONSTRUCTIONS.items():
            with self.subTest(task=name):
                directory = coherent.change_directory(construction)
                self.assertEqual(directory, f"{construction.crate}/src")
                for unit in construction.units:
                    self.assertTrue(unit.file.startswith(directory + "/"), unit.file)

    def test_a_construction_whose_change_spreads_over_two_directories_is_refused(self) -> None:
        construction = replace(
            coherent.BOUNDS,
            oracle_edits=(*coherent.BOUNDS.oracle_edits, coherent.Edit("docs/tools.md", "old", "new")),
        )
        with self.assertRaises(ValueError) as raised:
            coherent.change_directory(construction)
        self.assertIn("lies in 2 directories", str(raised.exception))

    def test_the_text_leaves_the_strategy_to_the_harness(self) -> None:
        # Whether the harness divides the work is what the family measures, so
        # the text may not ask for a division, a worker, or parallel work.
        forbidden = ("divide", "in parallel", "concurrent", "worker", "delegate", "subagent", "spawn", "team")
        for name, text in self.texts():
            for word in forbidden:
                with self.subTest(task=name, word=word):
                    self.assertNotIn(word, text.lower())

    def test_the_text_uses_no_contraction_and_no_rhetorical_question(self) -> None:
        for name, text in self.texts():
            with self.subTest(task=name):
                self.assertNotIn("?", text)
                for contraction in ("n't", "'s the", "it's", "does not not"):
                    self.assertNotIn(contraction, text.lower())


class MetadataTest(unittest.TestCase):
    def metadata(self, construction: coherent.Construction) -> dict:
        return coherent.metadata_of(construction, "0" * 40, {"commit": "0" * 40})

    def test_the_metadata_carries_the_keys_the_report_reads(self) -> None:
        for name, construction in coherent.CONSTRUCTIONS.items():
            metadata = self.metadata(construction)
            with self.subTest(task=name):
                # report.py reads `units` as an object of unit name to a
                # non-empty list of workspace-relative paths, and counts a
                # change under `interface_paths` as interface churn.
                self.assertEqual(sorted(metadata["units"]), sorted(unit.name for unit in construction.units))
                for unit_name, paths in metadata["units"].items():
                    self.assertTrue(paths and all(isinstance(path, str) and path for path in paths), unit_name)
                self.assertEqual(metadata["n"], len(metadata["units"]))
                self.assertEqual(sorted(metadata["unit_files"]), sorted(metadata["units"]))
                self.assertEqual(sorted(metadata["unit_tests"]), sorted(metadata["units"]))
                self.assertEqual(metadata["checked_units"], [unit.name for unit in construction.units])
                self.assertEqual(metadata["interface_paths"], list(construction.interface_paths))

    def test_the_task_is_a_coherent_control_of_the_teams_family_the_protocol_accepts(self) -> None:
        for name, construction in coherent.CONSTRUCTIONS.items():
            task = protocol.Task(
                name=construction.name,
                family=coherent.FAMILY,
                class_name=coherent.CLASS_NAME,
                text=coherent.task_text(construction),
                correct_statuses=frozenset({protocol.COMPLETED}),
                correct_codes=frozenset(),
                budget=dict(coherent.BUDGET),
                protected=coherent.PROTECTED,
                metadata=self.metadata(construction),
            )
            with self.subTest(task=name):
                self.assertEqual(task.family, "teams")
                self.assertEqual(task.class_name, "coherent")
                self.assertEqual(protocol.Task.from_dict(task.to_dict()), task)


class GradeScriptTest(unittest.TestCase):
    def test_the_grade_script_leaves_the_record_teams_reads_back(self) -> None:
        self.assertIn(f'UNITS_FILE = "{teams.UNITS_FILE}"', coherent.GRADE_SCRIPT)
        self.assertIn(f'UNITS_PREFIX = "{teams.UNITS_PREFIX}"', coherent.GRADE_SCRIPT)
        self.assertIn("(GRADER / UNITS_FILE).write_text(recorded", coherent.GRADE_SCRIPT)

    def test_every_finding_that_judges_one_unit_carries_the_head_the_report_reads(self) -> None:
        # report.py reads a unit's verdict from a finding headed `unit <name>`
        # followed by a colon or a space.
        for line in coherent.GRADE_SCRIPT.splitlines():
            if "findings.append(" in line and "unit " in line:
                with self.subTest(line=line.strip()[:60]):
                    self.assertIn('f"unit {unit[\'name\']}:', line)

    def test_a_grade_that_could_not_run_says_so_in_the_words_the_report_knows(self) -> None:
        self.assertIn('findings.append("cargo is absent from PATH and host.json names none")', coherent.GRADE_SCRIPT)

    def test_the_check_suite_runs_the_crate_of_the_construction_alone(self) -> None:
        for name, construction in coherent.CONSTRUCTIONS.items():
            script = coherent.checks_script(construction.package, construction.check_skip, construction.check_skip_reason)
            with self.subTest(task=name):
                self.assertIn(f"cargo test -p {construction.package}", script)
                self.assertIn(f"cargo clippy -p {construction.package} --all-targets -- -D warnings\n", script)
                self.assertIn("scripts/loc.sh\n", script)
                # A crate whose own tests exercise sandboxing fails inside a
                # sandbox, so no check names the whole workspace.
                self.assertNotIn("--workspace", script)

    def test_a_skipped_test_is_left_out_by_name_and_the_reason_stands_beside_it(self) -> None:
        script = coherent.checks_script("foe-code", "inner_call", "a socket a sandbox may deny")
        self.assertIn("cargo test -p foe-code -- --skip inner_call\n", script)
        self.assertIn("# a socket a sandbox may deny\n", script)
        self.assertNotIn("--skip", coherent.checks_script("foe-contract"))

    def test_a_construction_that_skips_a_test_states_why(self) -> None:
        for name, construction in coherent.CONSTRUCTIONS.items():
            with self.subTest(task=name):
                self.assertEqual(bool(construction.check_skip), bool(construction.check_skip_reason))


class BaseTreeTest(unittest.TestCase):
    """Every scripted edit's source stands exactly once in the tree the recorded task rests on."""

    def repo(self) -> Path:
        return protocol.repository_of(Path(__file__))

    def file_at(self, commit: str, path: str) -> str:
        return removal.show_file(self.repo(), commit, path).decode("utf-8")

    def fixture_text(self, commit: str, construction: coherent.Construction, path: str) -> str:
        text = self.file_at(commit, path)
        for edit in (*construction.ceilings, *construction.fixture_edits):
            if edit.path != path:
                continue
            self.assertEqual(text.count(edit.old), 1, f"{path}: the fixture edit's source stands {text.count(edit.old)} times")
            text = text.replace(edit.old, edit.new)
        return text

    def test_every_fixture_edit_applies_to_the_recorded_base_tree(self) -> None:
        for name, construction in coherent.CONSTRUCTIONS.items():
            commit = recorded_commit(name)
            if commit is None:
                self.skipTest(f"{TASK_TREE / name} is absent")
            for path in sorted({edit.path for edit in (*construction.ceilings, *construction.fixture_edits)}):
                with self.subTest(task=name, path=path):
                    self.fixture_text(commit, construction, path)

    def test_every_oracle_edit_applies_to_the_fixture(self) -> None:
        for name, construction in coherent.CONSTRUCTIONS.items():
            commit = recorded_commit(name)
            if commit is None:
                self.skipTest(f"{TASK_TREE / name} is absent")
            solved: dict[str, str] = {}
            for edit in construction.oracle_edits:
                if edit.path not in solved:
                    solved[edit.path] = self.fixture_text(commit, construction, edit.path)
                with self.subTest(task=name, path=edit.path):
                    self.assertEqual(
                        solved[edit.path].count(edit.old), 1, f"{edit.path}: the oracle edit's source stands {solved[edit.path].count(edit.old)} times"
                    )
                solved[edit.path] = solved[edit.path].replace(edit.old, edit.new)
            with self.subTest(task=name, corruption=coherent.RENAME_CORRUPTION):
                renamed = solved[construction.rename.path]
                self.assertEqual(
                    renamed.count(construction.rename.old),
                    1,
                    f"{construction.rename.path}: the rename corruption's source stands {renamed.count(construction.rename.old)} times in the solved unit",
                )

    def test_no_hidden_test_already_stands_in_the_fixture(self) -> None:
        for name, construction in coherent.CONSTRUCTIONS.items():
            commit = recorded_commit(name)
            if commit is None:
                self.skipTest(f"{TASK_TREE / name} is absent")
            for path, appended in construction.test_appends:
                with self.subTest(task=name, path=path):
                    self.assertNotIn(appended.strip(), self.file_at(commit, path))


class TaskDirectoryTest(unittest.TestCase):
    """What the emitted task directories hold, read without materializing them."""

    def directories(self) -> list[Path]:
        return [TASK_TREE / name for name in coherent.CONSTRUCTIONS if (TASK_TREE / name).is_dir()]

    def test_a_task_directory_keeps_no_tree(self) -> None:
        for directory in self.directories():
            with self.subTest(task=directory.name):
                self.assertFalse((directory / protocol.WORKSPACE).exists(), "the workspace copy was kept")
                for generated in protocol.GENERATED_DIRECTORIES:
                    self.assertEqual(list(directory.rglob(generated)), [], f"{generated} stands inside the task directory")

    def test_the_grader_holds_the_two_corruptions_and_the_hidden_tests(self) -> None:
        for directory in self.directories():
            construction = coherent.CONSTRUCTIONS[directory.name]
            with self.subTest(task=directory.name):
                self.assertEqual(
                    [path.name for path in protocol.corruptions(directory)], sorted([coherent.REVERT_CORRUPTION, coherent.RENAME_CORRUPTION])
                )
                for path, _ in construction.test_appends:
                    self.assertTrue((directory / protocol.GRADER / removal.HIDDEN_TESTS / path).is_file(), path)

    def test_the_recorded_specification_names_every_unit_test_and_the_integration_test(self) -> None:
        for directory in self.directories():
            construction = coherent.CONSTRUCTIONS[directory.name]
            specification = json.loads((directory / protocol.GRADER / coherent.SPECIFICATION_FILE).read_text(encoding="utf-8"))
            with self.subTest(task=directory.name):
                self.assertEqual(specification["package"], construction.package)
                self.assertEqual(
                    specification["units"], [{"name": unit.name, "test": unit.test_name} for unit in construction.units]
                )
                self.assertEqual(specification["integration_test"], construction.integration_test)

    def test_the_recorded_task_loads_and_declares_the_control(self) -> None:
        for directory in self.directories():
            task = protocol.load(directory)
            with self.subTest(task=directory.name):
                self.assertEqual((task.family, task.class_name), (coherent.FAMILY, coherent.CLASS_NAME))
                self.assertEqual(task.protected, coherent.PROTECTED)
                self.assertEqual(task.metadata["n"], len(coherent.CONSTRUCTIONS[directory.name].units))


if __name__ == "__main__":
    unittest.main()
