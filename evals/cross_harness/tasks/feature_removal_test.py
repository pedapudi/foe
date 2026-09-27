#!/usr/bin/python3
"""Unit tests for the feature-removal authoring tool: a synthetic repository, no cargo, no model."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import feature_removal as removal  # noqa: E402
import protocol  # noqa: E402
from protocol import COMPLETED, Reported  # noqa: E402

GIT = ["/usr/bin/git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]

# A test file under src/ compiles only through the `#[path]` declaration in
# the implementation file beside it, as in the tree the tool is written for.
ALPHA_DECLARATION = '#[cfg(test)]\n#[path = "lib_test.rs"]\nmod tests;\n'
BETA_DECLARATION = '#[cfg(test)]\n#[path = "extra_test.rs"]\nmod extra_tests;\n'

PARENT_FILES = {
    "AGENTS.md": "# Working here\n\nRun scripts/loc.sh and cargo test before reporting.\n",
    "scripts/loc.sh": "#!/bin/sh\nexit 0\n",
    "Cargo.toml": '[workspace]\nmembers = ["crates/alpha", "crates/beta"]\n',
    "crates/alpha/Cargo.toml": '[package]\nname = "alpha"\nversion = "0.1.0"\n',
    "crates/alpha/src/lib.rs": "pub fn helper(value: u64) -> u64 {\n    value\n}\n\n" + ALPHA_DECLARATION,
    "crates/alpha/src/lib_test.rs": "#[test]\nfn helper_returns_its_input() {\n    assert_eq!(crate::helper(3), 3);\n}\n",
    "crates/beta/Cargo.toml": '[package]\nname = "beta"\nversion = "0.1.0"\n',
    "crates/beta/src/lib.rs": "pub fn relay(value: u64) -> u64 {\n    value\n}\n",
    "crates/beta/tests/integration.rs": "#[test]\nfn relay_passes_through() {\n    assert_eq!(beta::relay(3), 3);\n}\n",
    "docs/spec.md": (
        "# Specification\n\nThe alpha crate offers a helper. It returns the input unchanged, and callers\nrely on that.\n"
    ),
    "docs/notes.md": "# Notes\n\nThe gargle step is planned for a later change.\n",
}

FEATURE_FILES = {
    "crates/alpha/src/lib.rs": (
        "pub fn helper(value: u64) -> u64 {\n    value\n}\n\npub fn frobnicate(value: u64) -> u64 {\n    value * 2\n}\n\n" + ALPHA_DECLARATION
    ),
    "crates/alpha/src/lib_test.rs": (
        "#[test]\nfn helper_returns_its_input() {\n    assert_eq!(crate::helper(3), 3);\n}\n\n"
        "#[test]\nfn frobnicate_doubles() {\n    assert_eq!(crate::frobnicate(3), 6);\n}\n"
    ),
    "crates/beta/src/lib.rs": "pub fn relay(value: u64) -> u64 {\n    alpha::frobnicate(value)\n}\n\n" + BETA_DECLARATION,
    "crates/beta/src/extra_test.rs": "#[test]\nfn relay_doubles_through_alpha() {\n    assert_eq!(alpha::frobnicate(2), 4);\n}\n",
    "crates/beta/tests/integration.rs": "#[test]\nfn relay_passes_through() {\n    assert_eq!(beta::relay(3), 6);\n}\n",
    "docs/spec.md": (
        "# Specification\n\nThe alpha crate offers a helper. It returns the input unchanged, and callers\n"
        "rely on that. The `frobnicate` function doubles its argument, and the beta\n"
        "crate calls it for every value it receives. A `feature/start` event marks\nthe call.\n"
    ),
}
FEATURE_MESSAGE = (
    "Double every value through alpha (#7)\n\nThe beta crate relays through frobnicate.\n\n"
    "Reviewed-by: A Reviewer <reviewer@example.invalid>\nChange-Id: I0123456789\n"
)

TRACED_FILES = {
    "crates/alpha/src/lib.rs": (
        "pub const fn helper(value: u64) -> u64 {\n    value\n}\n\npub fn frobnicate(value: u64) -> u64 {\n    value * 2\n}\n\n"
        "pub fn gargle(value: u64) -> u64 {\n    value + 1\n}\n\n" + ALPHA_DECLARATION
    ),
    "crates/alpha/src/lib_test.rs": FEATURE_FILES["crates/alpha/src/lib_test.rs"]
    + "\n#[test]\nfn gargle_increments() {\n    assert_eq!(crate::gargle(3), 4);\n}\n",
}

# A stand-in for cargo. `cargo test` runs every file under `crates/*/tests/`
# and every `src/*_test.rs` file that a `#[path]` attribute in an
# implementation file declares; it fails when a test names a function under
# `crate::` or `alpha::` that no implementation file defines, and otherwise
# prints one `test tests::<name> ... ok` line per test function. When a file
# named `pause` sits beside the script, `cargo test` also writes a marker into
# its working directory, waits a second, and fails if the marker vanished.
# `cargo clippy` fails when an implementation file carries `TODO_LINT`.
# Every other subcommand passes.
FAKE_CARGO = """#!/usr/bin/python3
import os
import pathlib
import re
import sys
import time

root = pathlib.Path.cwd()
command = sys.argv[1:2]
sources = [path for path in root.glob("crates/*/src/*.rs") if not path.name.endswith("_test.rs")]
if command == ["clippy"]:
    for source in sources:
        if "TODO_LINT" in source.read_text():
            print(f"error: {source.relative_to(root)} carries the TODO_LINT marker")
            sys.exit(1)
    sys.exit(0)
if command != ["test"]:
    sys.exit(0)
if (pathlib.Path(__file__).resolve().parent / "pause").exists():
    marker = root / f".grading-{os.getpid()}"
    marker.write_text("")
    time.sleep(1)
    if not marker.exists():
        print(f"{marker} vanished while the tests ran")
        sys.exit(1)
defined = set()
tests = list(root.glob("crates/*/tests/*.rs"))
for source in sources:
    text = source.read_text()
    defined |= set(re.findall(r"pub (?:const )?fn (\\w+)", text))
    for declared in re.findall(r'#\\[path = "([^"]+)"\\]', text):
        if (source.parent / declared).is_file():
            tests.append(source.parent / declared)
for test in sorted(tests):
    text = test.read_text()
    for name in re.findall(r"(?:crate|alpha)::(\\w+)", text):
        if name not in defined:
            print(f"{test.relative_to(root)}: {name} is undefined")
            sys.exit(1)
    for name in re.findall(r"#\\[test\\]\\s*fn (\\w+)", text):
        print(f"test tests::{name} ... ok")
"""

# A grade script that starts a grandchild and then outlives any timeout; the
# grandchild's process id lands in the file the script is given.
LINGERING_GRADE = """#!/bin/sh
sleep 60 &
echo $! > "{pid_file}"
sleep 60
"""


def write_files(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if relative.endswith(".sh"):
            path.chmod(0o755)


def replace_in(path: Path, source: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert source in text, f"{path} lacks {source!r}"
    path.write_text(text.replace(source, replacement), encoding="utf-8")


def snapshot(workspace: Path) -> dict[str, tuple[bytes, bool]]:
    """Every file under a workspace: its bytes and whether it is executable."""
    return {
        path.relative_to(workspace).as_posix(): (path.read_bytes(), os.access(path, os.X_OK))
        for path in sorted(workspace.rglob("*"))
        if path.is_file()
    }


def commit_all(repo: Path, message: str) -> str:
    subprocess.run([*GIT, "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run([*GIT, "-C", str(repo), "commit", "-q", "-m", message], check=True, capture_output=True)
    return subprocess.run([*GIT, "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


class Repository(unittest.TestCase):
    """A repository with five commits: a parent, the feature, a traced feature, a deletion, and a non-UTF-8 document.

    The repository's configuration drops the `a/` and `b/` diff prefixes, as
    a developer's global configuration may, so every diff the tool reads
    here is one whose prefixes the tool asked for itself.
    """

    tmp: str
    repo: Path
    parent: str
    feature: str
    traced: str
    deletion: str
    latin1: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp(prefix="feature-removal-")
        cls.repo = Path(cls.tmp) / "repo"
        cls.repo.mkdir()
        subprocess.run([*GIT, "-c", "init.defaultBranch=main", "init", "-q", str(cls.repo)], check=True, capture_output=True)
        for key, value in (("diff.noprefix", "true"), ("diff.mnemonicPrefix", "true")):
            subprocess.run([*GIT, "-C", str(cls.repo), "config", key, value], check=True, capture_output=True)
        write_files(cls.repo, PARENT_FILES)
        cls.parent = commit_all(cls.repo, "Start the repository")
        write_files(cls.repo, FEATURE_FILES)
        cls.feature = commit_all(cls.repo, FEATURE_MESSAGE)
        write_files(cls.repo, TRACED_FILES)
        cls.traced = commit_all(cls.repo, "Add the gargle step")
        (cls.repo / "crates/beta/src/extra_test.rs").unlink()
        cls.deletion = commit_all(cls.repo, "Drop the extra test")
        (cls.repo / "docs/latin.md").write_bytes(b"# Caf\xe9\n\nA document in latin-1.\n")
        cls.latin1 = commit_all(cls.repo, "Add a latin-1 document")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def scratch(self) -> Path:
        path = Path(tempfile.mkdtemp(prefix="scratch-", dir=self.tmp))
        self.addCleanup(shutil.rmtree, path, True)
        return path


class DiffPartition(Repository):
    def test_test_paths_are_named_by_suffix_or_a_tests_directory(self) -> None:
        for path in ("crates/a/src/x_test.rs", "crates/a/tests/it.rs", "tests/x.rs", "evals/x_test.py"):
            self.assertTrue(removal.is_test_path(path), path)
        for path in ("crates/a/src/lib.rs", "docs/tests.md", "evals/run_tests.py", "crates/a/src/testing.rs"):
            self.assertFalse(removal.is_test_path(path), path)

    def test_the_diff_carries_the_prefixes_the_parser_reads_whatever_git_is_configured_to_print(self) -> None:
        configured = subprocess.run(
            ["/usr/bin/git", "-C", str(self.repo), "show", "--format=", "-p", self.feature], check=True, capture_output=True, text=True
        ).stdout
        self.assertIn("diff --git crates/alpha/src/lib.rs crates/alpha/src/lib.rs\n", configured)
        self.assertIn("diff --git a/crates/alpha/src/lib.rs b/crates/alpha/src/lib.rs\n", removal.commit_diff(self.repo, self.feature))

    def test_the_feature_diff_splits_into_tests_and_implementation(self) -> None:
        diffs = removal.parse_diff(removal.commit_diff(self.repo, self.feature))
        tests, implementation = removal.partition(diffs)
        self.assertEqual(
            [diff.path for diff in tests],
            ["crates/alpha/src/lib_test.rs", "crates/beta/src/extra_test.rs", "crates/beta/tests/integration.rs"],
        )
        self.assertEqual([diff.path for diff in implementation], ["crates/alpha/src/lib.rs", "crates/beta/src/lib.rs", "docs/spec.md"])
        by_path = {diff.path: diff for diff in diffs}
        self.assertEqual(by_path["crates/beta/src/extra_test.rs"].status, removal.ADDED)
        self.assertEqual(by_path["crates/alpha/src/lib.rs"].status, removal.MODIFIED)
        self.assertEqual(by_path["crates/alpha/src/lib.rs"].crate, "crates/alpha")
        self.assertIsNone(by_path["docs/spec.md"].crate)
        self.assertIn("pub fn frobnicate(value: u64) -> u64 {", by_path["crates/alpha/src/lib.rs"].added)
        self.assertEqual(by_path["crates/beta/src/lib.rs"].removed, ["    value"])
        self.assertEqual(removal.crates_touched(diffs), ["crates/alpha", "crates/beta"])

    def test_a_deletion_is_recorded_and_a_rename_is_refused(self) -> None:
        diffs = removal.parse_diff(removal.commit_diff(self.repo, self.deletion))
        self.assertEqual([(diff.path, diff.status) for diff in diffs], [("crates/beta/src/extra_test.rs", removal.DELETED)])
        rename = "diff --git a/old.rs b/new.rs\nsimilarity index 100%\nrename from old.rs\nrename to new.rs\n"
        with self.assertRaises(ValueError) as caught:
            removal.parse_diff(rename)
        self.assertIn("old.rs", str(caught.exception))

    def test_a_diff_that_is_not_utf8_names_the_commit_and_the_file(self) -> None:
        with self.assertRaises(ValueError) as caught:
            removal.commit_diff(self.repo, self.latin1)
        self.assertIn(self.latin1, str(caught.exception))
        self.assertIn("docs/latin.md", str(caught.exception))
        self.assertIn("UTF-8", str(caught.exception))

    def test_added_identifiers_come_from_definitions_and_document_event_names(self) -> None:
        _, implementation = removal.partition(removal.parse_diff(removal.commit_diff(self.repo, self.feature)))
        self.assertEqual(removal.added_identifiers(implementation), ["extra_tests", "feature/start", "frobnicate"])
        # A name a removed line also defines existed before the commit.
        _, traced = removal.partition(removal.parse_diff(removal.commit_diff(self.repo, self.traced)))
        self.assertEqual(removal.added_identifiers(traced), ["gargle"])

    def test_the_commit_message_loses_its_issue_suffix_and_its_trailers(self) -> None:
        self.assertEqual(removal.commit_message(self.repo, self.feature), ("Double every value through alpha", "The beta crate relays through frobnicate."))
        self.assertEqual(removal.commit_message(self.repo, self.traced), ("Add the gargle step", ""))

    def test_test_names_are_read_from_every_test_attribute_form(self) -> None:
        source = (
            "#[test]\nfn plain() {}\n"
            "#[tokio::test(flavor = \"multi_thread\", worker_threads = 2)]\nasync fn threaded() {}\n"
            "#[test]\n#[should_panic]\nfn panics() {}\n"
            "#[tokio::test]\npub(crate) async fn shared() {}\n"
            "fn not_a_test() {}\n"
        )
        self.assertEqual(removal.test_names(source), ["plain", "threaded", "panics", "shared"])


class Scrub(Repository):
    def test_traces_name_every_line_that_holds_an_identifier_as_a_word(self) -> None:
        workspace = self.scratch()
        write_files(workspace, {"a.rs": "fn frobnicate_all() {}\nlet x = frobnicate(1);\n", "b.md": "frobnicate\n", "c.bin": "x"})
        (workspace / "c.bin").write_bytes(b"\xff\xfe frobnicate")
        self.assertEqual(removal.traces(workspace, ["frobnicate", "absent"]), {"frobnicate": ["a.rs:2", "b.md:1"]})

    def test_authoring_fails_on_an_unexplained_trace_and_records_an_allowed_one(self) -> None:
        out = self.scratch() / "task"
        with self.assertRaises(ValueError) as caught:
            removal.author(self.repo, self.traced, out, "gargle", "solvable")
        self.assertIn("gargle", str(caught.exception))
        self.assertIn("docs/notes.md:3", str(caught.exception))
        self.assertIn("--allow-traces", str(caught.exception))
        self.assertFalse(out.exists(), "a failed authoring leaves no partial task directory")
        authored = removal.author(self.repo, self.traced, out, "gargle", "solvable", allow_traces=["gargle"])
        self.assertEqual(authored.task.metadata["allowed_traces"], ["gargle"])
        self.assertEqual(authored.task.metadata["identifiers"], ["gargle"])


class Specification(unittest.TestCase):
    def test_sentences_are_complete_normalized_and_cut_at_unchanged_neighbours(self) -> None:
        diff = removal.FileDiff(
            "docs/spec.md",
            removal.MODIFIED,
            (
                ("@", "@@ -1,4 +1,6 @@"),
                (" ", "# Specification"),
                (" ", ""),
                (" ", "The alpha crate offers a helper. It returns the input unchanged, and callers"),
                ("-", "rely on that."),
                ("+", "rely on that. The `frobnicate` function doubles its argument, and the beta"),
                ("+", "crate calls it for every value it receives. A `feature/start` event marks"),
                ("+", "the call. Short."),
                ("+", "| `model_calls` | integer or `\"unlimited\"` | the ceiling on requests, which the run enforces |"),
            ),
            "",
        )
        self.assertEqual(
            removal.specification_sentences([diff]),
            {
                "docs/spec.md": [
                    "the frobnicate function doubles its argument, and the beta crate calls it for every value it receives.",
                    "a feature/start event marks the call.",
                    "the ceiling on requests, which the run enforces",
                ]
            },
        )

    def test_a_block_that_continues_into_an_unchanged_line_drops_its_open_end(self) -> None:
        diff = removal.FileDiff(
            "docs/spec.md",
            removal.MODIFIED,
            (
                ("@", "@@ -1,2 +1,3 @@"),
                (" ", "A complete sentence stands before the change."),
                ("+", "The added sentence is whole and long enough to count. The tail runs on into"),
                (" ", "the unchanged line that follows it."),
            ),
            "",
        )
        self.assertEqual(removal.specification_sentences([diff]), {"docs/spec.md": ["the added sentence is whole and long enough to count."]})

    def test_a_sentence_the_removed_lines_already_hold_is_not_required(self) -> None:
        diff = removal.FileDiff(
            "docs/config.md",
            removal.MODIFIED,
            (
                ("@", "@@ -1,2 +1,2 @@"),
                ("-", "| `model_calls` | integer | maximum model requests, including retries; the last request receives a warning |"),
                ("+", "| `model_calls` | integer or `\"unlimited\"` | maximum model requests, including retries; the last request receives a warning. "
                      "`\"unlimited\"` declares no ceiling on this dimension. |"),
                ("-", "A sentence the commit re-wraps across two lines"),
                ("-", "stays where it was, without a period"),
                ("+", "A sentence the commit re-wraps"),
                ("+", "across two lines stays where it was, without a period"),
            ),
            "",
        )
        self.assertEqual(removal.specification_sentences([diff]), {"docs/config.md": ['"unlimited" declares no ceiling on this dimension.']})

    def test_missing_sentences_name_the_document_and_the_sentence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            write_files(workspace, {"docs/spec.md": "The **added** sentence is whole\nand long enough to count.\n"})
            sentences = {"docs/spec.md": ["the added sentence is whole and long enough to count.", "another sentence that is absent."], "docs/gone.md": ["x"]}
            self.assertEqual(
                removal.missing_sentences(workspace, sentences),
                ["docs/spec.md lacks the sentence: another sentence that is absent.", "docs/gone.md is absent from the workspace"],
            )


class Authoring(Repository):
    """The task directory with its workspace copy kept, so that the copy can be inspected beside the recipe."""

    def setUp(self) -> None:
        self.out = self.scratch() / "double-values"
        self.authored = removal.author(self.repo, self.feature, self.out, "double-values", "solvable", keep_workspace=True)
        self.task = protocol.load(self.out)

    def test_task_json_states_the_task_and_its_provenance(self) -> None:
        self.assertEqual(self.task.name, "double-values")
        self.assertEqual((self.task.family, self.task.class_name), ("autonomy", "solvable"))
        self.assertTrue(self.task.text.startswith("Double every value through alpha.\n\nThe beta crate relays through frobnicate.\n\nThe documents"))
        self.assertNotIn("Reviewed-by", self.task.text)
        self.assertTrue(self.task.text.endswith(protocol.CLOSING))
        self.assertIn("- docs/spec.md: the frobnicate function doubles its argument, and the beta crate calls it for every value it receives.", self.task.text)
        self.assertIn("checks/run.sh runs the checks this task is judged on: the tests of alpha, beta,", self.task.text)
        self.assertEqual(self.task.correct_statuses, frozenset({"completed"}))
        self.assertEqual(self.task.budget, removal.budget_for(2))
        self.assertEqual(self.task.protected, removal.PROTECTED)
        metadata = self.task.metadata
        # The task directory lies outside the repository, so the source names it.
        self.assertEqual(
            metadata["source"],
            {"commit": self.feature, "parent": self.parent, "subject": "Double every value through alpha", "repo": str(self.repo.resolve())},
        )
        self.assertEqual((metadata["crate_count"], metadata["coupling"]), (2, 1))
        self.assertEqual(metadata["crates"], ["crates/alpha", "crates/beta"])
        self.assertEqual(metadata["packages"], ["alpha", "beta"])
        self.assertEqual(metadata["visible_tests"], ["crates/alpha/src/lib_test.rs", "crates/beta/src/extra_test.rs"])
        self.assertEqual(metadata["hidden_tests"], ["crates/alpha/src/lib_test.rs", "crates/beta/src/extra_test.rs", "crates/beta/tests/integration.rs"])
        self.assertTrue(metadata["review"].startswith("pending"))

    def test_the_workspace_is_the_parent_tree_with_the_visible_subset_added(self) -> None:
        workspace = self.out / "workspace"
        self.assertEqual((workspace / "crates/alpha/src/lib.rs").read_text(encoding="utf-8"), PARENT_FILES["crates/alpha/src/lib.rs"])
        self.assertEqual((workspace / "docs/spec.md").read_text(encoding="utf-8"), PARENT_FILES["docs/spec.md"])
        self.assertEqual((workspace / "crates/beta/tests/integration.rs").read_text(encoding="utf-8"), PARENT_FILES["crates/beta/tests/integration.rs"])
        self.assertEqual((workspace / "crates/alpha/src/lib_test.rs").read_text(encoding="utf-8"), FEATURE_FILES["crates/alpha/src/lib_test.rs"])
        self.assertEqual((workspace / "crates/beta/src/extra_test.rs").read_text(encoding="utf-8"), FEATURE_FILES["crates/beta/src/extra_test.rs"])
        self.assertTrue(os.access(workspace / "scripts/loc.sh", os.X_OK), "git archive keeps the executable bit")
        checks = workspace / "checks/run.sh"
        self.assertTrue(os.access(checks, os.X_OK))
        self.assertIn("cargo test -p alpha -p beta\n", checks.read_text(encoding="utf-8"))
        self.assertIn("cargo clippy -p alpha -p beta -- -D warnings\n", checks.read_text(encoding="utf-8"))
        self.assertFalse((workspace / ".git").exists())

    def test_the_grader_holds_the_oracle_the_hidden_tests_and_one_corruption(self) -> None:
        grader = self.out / "grader"
        self.assertEqual((grader / "oracle.patch").read_text(encoding="utf-8"), removal.commit_diff(self.repo, self.feature))
        for relative, content in FEATURE_FILES.items():
            self.assertEqual((grader / "oracle/workspace" / relative).read_text(encoding="utf-8"), content, relative)
        hidden = sorted(path.relative_to(grader / "tests").as_posix() for path in (grader / "tests").rglob("*") if path.is_file())
        self.assertEqual(hidden, self.task.metadata["hidden_tests"])
        self.assertTrue(os.access(grader / "grade", os.X_OK))
        self.assertTrue(os.access(grader / "grade.py", os.X_OK))
        specification = json.loads((grader / "specification.json").read_text(encoding="utf-8"))
        self.assertEqual(specification["task"], "double-values")
        self.assertEqual(specification["packages"], ["alpha", "beta"])
        self.assertEqual(
            specification["hidden_test_names"],
            {
                "crates/alpha/src/lib_test.rs": ["helper_returns_its_input", "frobnicate_doubles"],
                "crates/beta/src/extra_test.rs": ["relay_doubles_through_alpha"],
                "crates/beta/tests/integration.rs": ["relay_passes_through"],
            },
        )
        self.assertEqual(list(specification["sentences"]), ["docs/spec.md"])
        revert = json.loads((grader / "corruptions/revert-one-crate/revert.json").read_text(encoding="utf-8"))
        self.assertEqual(revert, {"crate": "crates/alpha", "restore": ["crates/alpha/src/lib.rs"], "remove": []})
        self.assertEqual(
            (grader / "corruptions/revert-one-crate/parent/crates/alpha/src/lib.rs").read_text(encoding="utf-8"),
            PARENT_FILES["crates/alpha/src/lib.rs"],
        )
        protected = json.loads((grader / "protected.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(protected["workspace"]), ["AGENTS.md", "checks/run.sh", "scripts/loc.sh"])
        self.assertIn("grader/grade.py", protected["outside"])

    def test_the_second_authoring_into_the_same_directory_is_refused(self) -> None:
        with self.assertRaises(FileExistsError):
            removal.author(self.repo, self.feature, self.out, "double-values", "solvable")

    def test_the_recipe_regenerates_the_workspace_and_the_copy_is_removed_by_default(self) -> None:
        recipe = self.scratch() / "recipe"
        removal.author(self.repo, self.feature, recipe, "double-values", "solvable")
        self.assertFalse((recipe / "workspace").exists(), "the copy is removed once the patch records it")
        patch = (recipe / "grader" / "workspace.patch").read_text(encoding="utf-8")
        self.assertIn("diff --git a/checks/run.sh b/checks/run.sh\nnew file mode 100755\n", patch)
        self.assertIn("diff --git a/crates/beta/src/extra_test.rs b/crates/beta/src/extra_test.rs\nnew file mode 100644\n", patch)
        self.assertIn("+fn frobnicate_doubles() {\n", patch)
        self.assertNotIn("frobnicate(value: u64)", patch, "the implementation stays the parent's, so the patch carries no oracle line")
        self.assertEqual((self.out / "grader" / "workspace.patch").read_text(encoding="utf-8"), patch)
        protected = json.loads((recipe / "grader" / "protected.json").read_text(encoding="utf-8"))
        self.assertIn("grader/workspace.patch", protected["outside"])
        root = self.scratch() / "root"
        protocol.materialize(recipe, root)
        self.assertEqual(snapshot(root / "workspace"), snapshot(self.out / "workspace"))
        self.assertEqual(protocol.damage(root), [])


class Grading(Repository):
    """The generated grade script, driven with the stand-in cargo."""

    def setUp(self) -> None:
        self.out = self.scratch() / "double-values"
        removal.author(self.repo, self.feature, self.out, "double-values", "solvable")
        self.scratch_dir = self.scratch()
        self.cargo = self.scratch_dir / "cargo"
        self.cargo.write_text(FAKE_CARGO, encoding="utf-8")
        self.cargo.chmod(0o755)
        self.build = self.scratch_dir / "build"
        (self.out / "grader/host.json").write_text(json.dumps({"cargo": str(self.cargo), "build_dir": str(self.build)}), encoding="utf-8")

    def solved_root(self, name: str) -> Path:
        root = self.scratch_dir / name
        protocol.materialize(self.out, root)
        protocol.apply_oracle(root)
        return root

    def grade(self, root: Path, arm: str = "arm", timeout: int = 60) -> protocol.GradeResult:
        return removal.grade_with_timeout(root, Reported(COMPLETED, None, "graded"), None, arm, timeout)

    def test_the_grader_controls_hold(self) -> None:
        results = removal.verify(self.out, self.scratch_dir / "controls", timeout=60)
        by_name = {timed.control.name: timed.control for timed in results}
        self.assertEqual(sorted(by_name), ["corruption:revert-one-crate", "oracle", "untouched"])
        for name, control in by_name.items():
            self.assertTrue(control.held, f"{name}: {control.findings}")
        self.assertTrue(any("frobnicate is undefined" in finding for finding in by_name["untouched"].findings), by_name["untouched"].findings)
        self.assertTrue(any("docs/spec.md lacks the sentence" in finding for finding in by_name["untouched"].findings))
        self.assertEqual(by_name["oracle"].findings, [])
        self.assertTrue(any("frobnicate is undefined" in finding for finding in by_name["corruption:revert-one-crate"].findings))
        logs = sorted((self.build / "double-values/grades/control-oracle").glob("*/logs/test.log"))
        self.assertEqual(len(logs), 1, "the grade keeps each command's output under the build, by arm and grade")
        self.assertIn("test tests::frobnicate_doubles ... ok", logs[0].read_text(encoding="utf-8"))
        self.assertEqual(list((self.build / "double-values/grades/control-oracle").glob("*/copy")), [], "the copy is removed after the grade")

    def test_a_weakened_visible_test_does_not_decide_the_grade(self) -> None:
        root = self.solved_root("weakened")
        workspace = root / "workspace"
        (workspace / "crates/alpha/src/lib.rs").write_text(PARENT_FILES["crates/alpha/src/lib.rs"], encoding="utf-8")
        replace_in(workspace / "crates/alpha/src/lib_test.rs", "    assert_eq!(crate::frobnicate(3), 6);\n", "")
        replace_in(workspace / "crates/beta/src/extra_test.rs", "    assert_eq!(alpha::frobnicate(2), 4);\n", "")
        result = self.grade(root)
        self.assertFalse(result.passed)
        self.assertTrue(any("crates/alpha/src/lib_test.rs: frobnicate is undefined" in finding for finding in result.findings), result.findings)

    def test_a_hidden_test_file_that_never_compiles_fails_the_grade(self) -> None:
        root = self.solved_root("undeclared")
        replace_in(root / "workspace/crates/beta/src/lib.rs", BETA_DECLARATION, "")
        result = self.grade(root)
        self.assertFalse(result.passed)
        self.assertEqual(len(result.findings), 1, result.findings)
        self.assertIn("hidden test crates/beta/src/extra_test.rs: relay_doubles_through_alpha did not run", result.findings[0])

    def test_a_clippy_failure_fails_the_grade(self) -> None:
        root = self.solved_root("linted")
        with (root / "workspace/crates/beta/src/lib.rs").open("a", encoding="utf-8") as source:
            source.write("// TODO_LINT\n")
        result = self.grade(root)
        self.assertFalse(result.passed)
        self.assertEqual(len(result.findings), 1, result.findings)
        self.assertTrue(result.findings[0].startswith("clippy: "), result.findings[0])
        self.assertIn("TODO_LINT marker", result.findings[0])

    def test_two_grades_of_one_task_at_once_keep_their_own_copies(self) -> None:
        (self.scratch_dir / "pause").write_text("")
        roots = [self.solved_root("left"), self.solved_root("right")]
        results: dict[str, protocol.GradeResult] = {}

        def grade(root: Path) -> None:
            results[root.name] = self.grade(root, arm="same-arm")

        threads = [threading.Thread(target=grade, args=(root,)) for root in roots]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        for name, result in results.items():
            self.assertTrue(result.passed, f"{name}: {result.findings}")
        self.assertEqual(len(list((self.build / "double-values/grades/same-arm").glob("*/logs/test.log"))), 2)

    def test_the_grade_script_names_the_working_directory_rule_when_run_elsewhere(self) -> None:
        root = self.solved_root("misplaced")
        payload = json.dumps({"arm": "misplaced", "reported": {"status": COMPLETED, "code": None, "evidence": ""}, "candidate": None})
        result = subprocess.run([str(root / "grader/grade")], cwd=root, input=payload, text=True, capture_output=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"the grade script runs from the workspace, and {root} holds no Cargo.toml", result.stdout)
        self.assertFalse((self.build / "double-values/grades").exists(), "nothing is copied from a directory that is not the workspace")

    def test_a_timed_out_grade_script_takes_its_process_group_with_it(self) -> None:
        root = self.solved_root("lingering")
        pid_file = self.scratch_dir / "grandchild.pid"
        script = root / "grader/grade"
        script.write_text(LINGERING_GRADE.format(pid_file=pid_file), encoding="utf-8")
        started = time.monotonic()
        result = self.grade(root, timeout=1)
        self.assertLess(time.monotonic() - started, 30)
        self.assertFalse(result.passed)
        self.assertIn("ran past 1s and its process group was killed", result.findings[0])
        grandchild = int(pid_file.read_text(encoding="utf-8").strip())
        for _ in range(50):
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail(f"the grandchild {grandchild} outlived the killed grade script")


class Classes(Repository):
    def test_a_coherent_task_belongs_to_the_teams_family(self) -> None:
        out = self.scratch() / "coherent"
        authored = removal.author(self.repo, self.feature, out, "coherent-values", "coherent")
        self.assertEqual((authored.task.family, authored.task.class_name), ("teams", "coherent"))
        with self.assertRaises(ValueError):
            removal.author(self.repo, self.feature, self.scratch() / "x", "x", "survey")

    def test_a_commit_that_deletes_a_file_is_refused_by_path(self) -> None:
        with self.assertRaises(ValueError) as caught:
            removal.author(self.repo, self.deletion, self.scratch() / "deletion", "deletion", "solvable")
        self.assertIn("crates/beta/src/extra_test.rs", str(caught.exception))


class CommandLine(Repository):
    def test_author_reports_what_it_wrote_and_a_bad_commit_exits_2(self) -> None:
        out = self.scratch() / "cli"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = removal.main(["author", "--repo", str(self.repo), "--commit", self.feature, "--out", str(out), "--name", "cli-task"])
        self.assertEqual(status, 0)
        self.assertIn("crates: crates/alpha, crates/beta (count=2, coupling=1)", stdout.getvalue())
        self.assertFalse((out / "workspace").exists())
        self.assertTrue((out / "grader" / "workspace.patch").is_file())
        kept = self.scratch() / "kept"
        with contextlib.redirect_stdout(io.StringIO()):
            status = removal.main(["author", "--repo", str(self.repo), "--commit", self.feature, "--out", str(kept), "--name", "kept", "--keep-workspace"])
        self.assertEqual(status, 0)
        self.assertTrue((kept / "workspace" / "checks" / "run.sh").is_file())
        self.assertTrue((kept / "grader" / "workspace.patch").is_file())
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = removal.main(["author", "--repo", str(self.repo), "--commit", "no-such-commit", "--out", str(out / "x"), "--name", "x"])
        self.assertEqual(status, 2)
        self.assertIn("no-such-commit", stderr.getvalue())

    def test_a_commit_whose_diff_is_not_utf8_exits_2_naming_the_file(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = removal.main(["author", "--repo", str(self.repo), "--commit", self.latin1, "--out", str(self.scratch() / "latin"), "--name", "latin"])
        self.assertEqual(status, 2)
        self.assertIn("docs/latin.md", stderr.getvalue())


class Budget(unittest.TestCase):
    def test_the_budget_grows_with_the_crates_touched(self) -> None:
        one, two = removal.budget_for(1), removal.budget_for(2)
        self.assertEqual(sorted(one), sorted(protocol.BUDGET_KEYS))
        for key in protocol.BUDGET_KEYS:
            self.assertGreater(two[key], one[key], key)

    def test_the_whole_grade_outlasts_the_commands_it_runs(self) -> None:
        self.assertGreater(removal.GRADE_TIMEOUT_SECONDS, removal.GRADE_COMMAND_SECONDS)
        self.assertIn(f"COMMAND_SECONDS = {removal.GRADE_COMMAND_SECONDS}\n", removal.GRADE_SCRIPT)


if __name__ == "__main__":
    unittest.main()
