#!/usr/bin/python3
"""Unit tests for the teams task authoring tool: synthetic repositories, a stand-in cargo, no model."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import feature_removal as removal  # noqa: E402
import protocol  # noqa: E402
import teams  # noqa: E402
from protocol import COMPLETED, Reported  # noqa: E402

GIT = ["/usr/bin/git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]

ALPHA_DECLARATION = '#[cfg(test)]\n#[path = "lib_test.rs"]\nmod tests;\n'

# A workspace of four crates and a Python directory. The sweep commit renames
# the joining function of every unit from a dot to a dash, in the same way in
# each; gamma is untouched and uses alpha's `shared_thing`, which is the item
# the rename corruption can use; delta is touched in a test file alone, so it
# is no unit of the fan-out.
PARENT_FILES = {
    "AGENTS.md": "# Working here\n\nRun scripts/loc.sh and cargo test before reporting.\n",
    "scripts/loc.sh": "#!/bin/sh\nexit 0\n",
    "Cargo.toml": '[workspace]\nmembers = ["crates/alpha", "crates/beta", "crates/gamma", "crates/delta"]\n',
    "crates/alpha/Cargo.toml": '[package]\nname = "alpha"\nversion = "0.1.0"\n',
    "crates/alpha/src/lib.rs": (
        "pub fn join_dot(a: &str, b: &str) -> String {\n    format!(\"{a} . {b}\")\n}\n\n"
        "pub fn shared_thing() -> u64 {\n    7\n}\n\n" + ALPHA_DECLARATION
    ),
    "crates/alpha/src/lib_test.rs": "#[test]\nfn joins_with_a_dot() {\n    assert_eq!(crate::join_dot(\"a\", \"b\"), \"a . b\");\n}\n",
    "crates/beta/Cargo.toml": '[package]\nname = "beta"\nversion = "0.1.0"\n',
    "crates/beta/src/lib.rs": "pub fn label_dot(a: &str) -> String {\n    format!(\"{a} .\")\n}\n",
    "crates/beta/tests/integration.rs": "#[test]\nfn labels_with_a_dot() {\n    assert_eq!(beta::label_dot(\"a\"), \"a .\");\n}\n",
    "crates/gamma/Cargo.toml": '[package]\nname = "gamma"\nversion = "0.1.0"\n\n[dependencies]\nalpha = { path = "../alpha" }\n',
    "crates/gamma/src/lib.rs": "pub fn relay() -> u64 {\n    alpha::shared_thing()\n}\n",
    "crates/delta/Cargo.toml": '[package]\nname = "delta"\nversion = "0.1.0"\n',
    "crates/delta/src/lib.rs": "pub fn constant() -> u64 {\n    1\n}\n",
    "crates/delta/tests/fixture.rs": "#[test]\nfn reads_the_dot_fixture() {\n    assert_eq!(delta::constant(), 1);\n}\n",
    "evals/check.py": "def join_dot(a, b):\n    return f\"{a} . {b}\"\n",
    "evals/check_test.py": (
        "import unittest\n\nfrom check import join_dot\n\n\nclass Join(unittest.TestCase):\n"
        "    def test_dot(self):\n        self.assertEqual(join_dot('a', 'b'), 'a . b')\n\n\nif __name__ == '__main__':\n    unittest.main()\n"
    ),
    "examples/README.md": "# Examples\n\nA heading reads `lead . You`.\n",
    "docs/spec.md": "# Specification\n\nThe fields of a heading are separated by a dot, and every display agrees.\n",
}

SWEEP_FILES = {
    "crates/alpha/src/lib.rs": (
        "pub fn join_dash(a: &str, b: &str) -> String {\n    format!(\"{a} - {b}\")\n}\n\n"
        "pub fn shared_thing() -> u64 {\n    7\n}\n\n" + ALPHA_DECLARATION
    ),
    "crates/alpha/src/lib_test.rs": "#[test]\nfn joins_with_a_dash() {\n    assert_eq!(crate::join_dash(\"a\", \"b\"), \"a - b\");\n}\n",
    "crates/beta/src/lib.rs": "pub fn label_dash(a: &str) -> String {\n    format!(\"{a} -\")\n}\n",
    "crates/beta/tests/integration.rs": "#[test]\nfn labels_with_a_dash() {\n    assert_eq!(beta::label_dash(\"a\"), \"a -\");\n}\n",
    "crates/delta/tests/fixture.rs": "#[test]\nfn reads_the_dash_fixture() {\n    assert_eq!(delta::constant(), 1);\n}\n",
    "evals/check.py": "def join_dash(a, b):\n    return f\"{a} - {b}\"\n",
    "evals/check_test.py": (
        "import unittest\n\nfrom check import join_dash\n\n\nclass Join(unittest.TestCase):\n"
        "    def test_dash(self):\n        self.assertEqual(join_dash('a', 'b'), 'a - b')\n\n\nif __name__ == '__main__':\n    unittest.main()\n"
    ),
    "examples/README.md": "# Examples\n\nA heading reads `lead - You`.\n",
    "docs/spec.md": (
        "# Specification\n\nThe fields of a heading are separated by a dash, and every display agrees.\n"
        "The dot is retired from every heading, label, and subject line.\n"
    ),
}
SWEEP_MESSAGE = "Use a dash between the fields of every heading\n\nEvery unit joins its fields the same way.\n"

# A tree for the two surveys: four error messages that name no subject, four
# that do, one attribute with an argument after its literal, which is outside
# the survey, and a configuration document with two uncited keys; the doc
# comment whose first backtick span is a quoted string cites no key.
SURVEY_FILES = {
    "AGENTS.md": "# Working here\n\nEvery error names the key, event, or rule involved.\n",
    "scripts/loc.sh": "#!/bin/sh\nexit 0\n",
    "crates/one/src/lib.rs": (
        "use thiserror::Error;\n\n#[derive(Debug, Error)]\npub enum Fault {\n"
        '    #[error("{key}: {rule}")]\n    Rule { key: String, rule: String },\n'
        '    #[error("{0}")]\n    Plain(String),\n'
        '    #[error("io: {0}")]\n    Io(std::io::Error),\n'
        "    #[error(transparent)]\n    Other(Box<dyn std::error::Error>),\n"
        '    #[error("model.base_url: {url} is unreachable")]\n    Unreachable { url: String },\n'
        '    #[error("the `{name}` tool is unknown, e.g. a typo")]\n    Unknown { name: String },\n'
        '    #[error("line {line}: {source}")]\n    Line { line: u64, source: String },\n'
        '    #[error("e.g. the value is out of range")]\n    Range,\n'
        '    #[error("an argument follows: {}", .0)]\n    WithArgument(String),\n'
        '    #[error("model.base.url: {0}")]\n    ThreeSegments(String),\n'
        "}\n"
    ),
    "crates/one/src/lib_test.rs": (
        '/// docs/config.md `budget`: a declared ceiling of `"unlimited"` leaves the dimension open.\n#[test]\nfn budget_rule() {}\n\n'
        '/// docs/config.md "JSON Schema subset": every keyword is one of the listed.\n#[test]\nfn schema_rule() {}\n\n'
        '/// docs/config.md `"unlimited"` is a value of `task`, and this line cites no key.\n#[test]\nfn quoted_first() {}\n\n'
        '#[error("a test file is never surveyed")]\nfn not_an_error() {}\n'
    ),
    "crates/one/tests/fixtures.rs": '#[error("a file under tests is never surveyed")]\nfn fixture() {}\n',
    "docs/config.md": (
        "# Contract document\n\nThis document defines every key. Here is a `### `name`` mention in prose.\n\n"
        "## Keys\n\n### `name`\n\nString. Required, with at least one character.\n\n"
        "```json\n{ \"name\": \"x\". }\n```\n\n"
        "| field | meaning |\n|---|---|\n| `name` | a sentence in a table. |\n\n"
        "### `budget`\n\nObject. Required.\n\nThe ceiling `\"unlimited\"` is one of `a.b`, or none. Two rules follow.\n\n"
        "- The first rule holds everywhere.\n- The second rule holds, and always!\n\n"
        "### `task`\n\nString. What this episode is to do? It is written into the log\nacross two lines.\n\n"
        "## Errors\n\nEvery error names its key.\n"
    ),
}

# A stand-in for cargo over the workspace layout above. `test -p NAME`
# compiles the named crate and its path dependencies and runs the named
# crate's tests; `test --workspace` compiles every crate and runs every test.
# Compiling a crate fails when one of its sources names `dep::item` for a
# dependency that defines no `item`; a test fails when it names `crate::item`
# or `pkg::item` that the crate does not define. Each test function prints
# one `test tests::<name> ... ok` line. `clippy` fails when a source carries
# `TODO_LINT`. Every other subcommand passes.
FAKE_CARGO = """#!/usr/bin/python3
import pathlib
import re
import sys

root = pathlib.Path.cwd()
args = sys.argv[1:]
command = args[0] if args else ""
crates = {}
for manifest in sorted(root.glob("crates/*/Cargo.toml")):
    text = manifest.read_text()
    name = re.search(r'name = "([^"]+)"', text).group(1)
    deps = set(re.findall(r'path = "\\.\\./([^"]+)"', text))
    crates[manifest.parent.name] = (name, deps)
by_name = {name: directory for directory, (name, _) in crates.items()}


def sources(directory):
    return [path for path in (root / "crates" / directory / "src").glob("*.rs") if not path.name.endswith("_test.rs")]


def defined(directory):
    names = set()
    for source in sources(directory):
        names |= set(re.findall(r"pub (?:const )?(?:fn|struct|enum) (\\w+)", source.read_text()))
    return names


def tests_of(directory):
    found = list((root / "crates" / directory / "tests").glob("*.rs"))
    for source in sources(directory):
        for declared in re.findall(r'#\\[path = "([^"]+)"\\]', source.read_text()):
            if (source.parent / declared).is_file():
                found.append(source.parent / declared)
    return sorted(found)


def closure(start):
    seen, pending = set(), [start]
    while pending:
        directory = pending.pop()
        if directory in seen:
            continue
        seen.add(directory)
        pending.extend(crates[directory][1])
    return seen


if command == "clippy":
    for directory in crates:
        for source in sources(directory):
            if "TODO_LINT" in source.read_text():
                print(f"error: {source.relative_to(root)} carries the TODO_LINT marker")
                sys.exit(1)
    sys.exit(0)
if command != "test":
    sys.exit(0)
targets = [by_name[args[args.index("-p") + 1]]] if "-p" in args else sorted(crates)
scope = set()
for target in targets:
    scope |= closure(target)
for directory in sorted(scope):
    for source in sources(directory):
        for dep, item in re.findall(r"(\\w+)::(\\w+)", source.read_text()):
            if dep in by_name and by_name[dep] in crates[directory][1] and item not in defined(by_name[dep]):
                print(f"error: {source.relative_to(root)} names {dep}::{item}, which {dep} does not define")
                sys.exit(1)
for directory in targets:
    known = defined(directory)
    for test in tests_of(directory):
        text = test.read_text()
        for dep, item in re.findall(r"(\\w+)::(\\w+)", text):
            if (dep == "crate" or dep == crates[directory][0]) and item not in known:
                print(f"{test.relative_to(root)}: {item} is undefined")
                sys.exit(1)
        for name in re.findall(r"#\\[test\\]\\s*fn (\\w+)", text):
            print(f"test tests::{name} ... ok")
"""


def write_files(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if relative.endswith(".sh"):
            path.chmod(0o755)


def commit_all(repo: Path, message: str) -> str:
    subprocess.run([*GIT, "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run([*GIT, "-C", str(repo), "commit", "-q", "-m", message], check=True, capture_output=True)
    return subprocess.run([*GIT, "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def new_repository(root: Path) -> None:
    subprocess.run([*GIT, "-c", "init.defaultBranch=main", "init", "-q", str(root)], check=True, capture_output=True)


def grade_directly(root: Path, candidate: object, arm: str = "arm") -> tuple[list[str], str]:
    """Run a materialized root's grade script the way the protocol does, keeping its standard error."""
    payload = json.dumps({"reported": Reported(COMPLETED, None, "graded").to_dict(), "candidate": candidate, "arm": arm})
    result = subprocess.run([str(root / "grader/grade")], cwd=root / "workspace", input=payload, text=True, capture_output=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line.strip()], result.stderr


class Repositories(unittest.TestCase):
    """A sweep repository with a parent and the sweep commit, and a survey repository with one commit."""

    tmp: str
    sweep_repo: Path
    parent: str
    sweep: str
    survey_repo: Path
    survey_commit: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp(prefix="teams-")
        cls.sweep_repo = Path(cls.tmp) / "sweep"
        cls.sweep_repo.mkdir()
        new_repository(cls.sweep_repo)
        write_files(cls.sweep_repo, PARENT_FILES)
        cls.parent = commit_all(cls.sweep_repo, "Start the repository")
        write_files(cls.sweep_repo, SWEEP_FILES)
        cls.sweep = commit_all(cls.sweep_repo, SWEEP_MESSAGE)
        cls.survey_repo = Path(cls.tmp) / "survey"
        cls.survey_repo.mkdir()
        new_repository(cls.survey_repo)
        write_files(cls.survey_repo, SURVEY_FILES)
        cls.survey_commit = commit_all(cls.survey_repo, "Start the surveyed tree")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def scratch(self) -> Path:
        path = Path(tempfile.mkdtemp(prefix="scratch-", dir=self.tmp))
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def sweep_units(self) -> tuple[list[teams.Unit], dict[str, set[str]]]:
        workspace = self.scratch() / "parent"
        protocol.archive(self.sweep_repo, self.parent, workspace)
        diffs = removal.parse_diff(removal.commit_diff(self.sweep_repo, self.sweep))
        return teams.partition_units(diffs, workspace), teams.crate_dependencies(workspace)


class Units(Repositories):
    def test_a_path_belongs_to_its_crate_or_its_top_level_entry(self) -> None:
        self.assertEqual(teams.unit_name("crates/alpha/src/lib.rs"), "crates/alpha")
        self.assertEqual(teams.unit_name("crates/alpha/tests/integration.rs"), "crates/alpha")
        self.assertEqual(teams.unit_name("docs/spec.md"), "docs")
        self.assertEqual(teams.unit_name("evals/terminal_bench/check_test.py"), "evals")
        self.assertEqual(teams.unit_name("README.md"), "README.md")

    def test_the_sweep_partitions_into_units_with_their_tests_and_verdicts(self) -> None:
        units, graph = self.sweep_units()
        self.assertEqual([unit.name for unit in units], ["crates/alpha", "crates/beta", "docs", "evals", "examples"])
        by_name = {unit.name: unit for unit in units}
        self.assertEqual(by_name["crates/alpha"].package, "alpha")
        self.assertEqual(by_name["crates/alpha"].implementation, ("crates/alpha/src/lib.rs",))
        self.assertEqual(by_name["crates/alpha"].tests, ("crates/alpha/src/lib_test.rs",))
        self.assertEqual(by_name["crates/beta"].tests, ("crates/beta/tests/integration.rs",))
        self.assertEqual(by_name["evals"].python_tests, ("evals/check_test.py",))
        self.assertIsNone(by_name["evals"].package)
        self.assertEqual([unit.name for unit in units if unit.checked], ["crates/alpha", "crates/beta", "evals"])
        self.assertEqual(by_name["docs"].paths, ["docs"])
        self.assertNotIn("crates/delta", by_name, "a crate touched in a test file alone holds nothing for the agent to change")
        self.assertFalse(teams.Unit("crates/zeta", ("crates/zeta/src/a_test.rs",), (), ("crates/zeta/src/a_test.rs",), "zeta").checked)
        self.assertEqual(graph, {"crates/alpha": set(), "crates/beta": set(), "crates/delta": set(), "crates/gamma": {"crates/alpha"}})
        self.assertEqual(teams.closure(graph, ["crates/gamma"]), {"crates/gamma", "crates/alpha"})

    def test_the_reverted_unit_is_the_first_checked_leaf_with_implementation_files(self) -> None:
        units, graph = self.sweep_units()
        self.assertEqual(teams.revert_unit_for(units, graph).name, "crates/alpha")
        # With beta depending on alpha, alpha has a dependent and beta is the leaf.
        self.assertEqual(teams.revert_unit_for(units, {"crates/beta": {"crates/alpha"}}).name, "crates/beta")
        tests_only = [unit for unit in units if unit.name == "docs"] + [teams.Unit("crates/zeta", ("crates/zeta/src/a_test.rs",), (), ("crates/zeta/src/a_test.rs",), "zeta")]
        with self.assertRaises(ValueError) as caught:
            teams.revert_unit_for(tests_only, {})
        self.assertIn("--revert-unit", str(caught.exception))

    def test_interface_paths_are_the_implementation_files_touched_crates_depend_on(self) -> None:
        units, graph = self.sweep_units()
        self.assertEqual(teams.interface_paths(units, graph), [])
        self.assertEqual(teams.interface_paths(units, {"crates/beta": {"crates/alpha"}}), ["crates/alpha/src/lib.rs"])

    def test_the_shared_element_is_used_outside_the_checked_closure_and_by_no_test_of_its_unit(self) -> None:
        units, graph = self.sweep_units()
        shared, reason = teams.shared_element(self.sweep_repo, self.sweep, units, graph)
        self.assertEqual(reason, "")
        assert shared is not None
        self.assertEqual((shared.unit, shared.element, shared.replacement), ("crates/alpha", "shared_thing", "shared_thing_renamed"))
        self.assertEqual(shared.files, ("crates/alpha/src/lib.rs",))
        self.assertEqual(shared.used_by, ("crates/gamma",))
        # With gamma inside the closure of a checked unit, no item qualifies.
        shared, reason = teams.shared_element(self.sweep_repo, self.sweep, units, {"crates/beta": {"crates/gamma"}, "crates/gamma": {"crates/alpha"}})
        self.assertIsNone(shared)
        self.assertIn("crates/gamma", reason)

    def test_the_budget_grows_with_the_checked_units(self) -> None:
        self.assertEqual(teams.budget_for(3), removal.budget_for(3))
        self.assertEqual(teams.budget_for(0), removal.budget_for(1))


class FanOutAuthoring(Repositories):
    def setUp(self) -> None:
        self.out = self.scratch() / "dash-sweep"
        self.authored = teams.author_fan_out(self.sweep_repo, self.sweep, self.out, "dash-sweep")

    def test_task_json_states_the_fan_out_and_its_units(self) -> None:
        task = protocol.load(self.out)
        self.assertEqual((task.family, task.class_name), ("teams", "fan-out"))
        self.assertEqual(task.correct_statuses, frozenset({COMPLETED}))
        self.assertTrue(task.text.startswith("Use a dash between the fields of every heading."))
        self.assertIn("The change applies to 5 units: crates/alpha, crates/beta, docs, evals, examples. Each unit is judged by its own tests.", task.text)
        self.assertNotIn("crates/delta", task.text)
        self.assertNotIn("tests of their own", task.text, "the text does not say which units carry a verdict")
        self.assertIn("cargo test --workspace", task.text)
        self.assertIn("docs/spec.md: the dot is retired from every heading, label, and subject line.", task.text)
        self.assertTrue(task.text.endswith(protocol.CLOSING))
        metadata = task.metadata
        self.assertEqual(metadata["units"], {"crates/alpha": ["crates/alpha"], "crates/beta": ["crates/beta"], "docs": ["docs"], "evals": ["evals"], "examples": ["examples"]})
        self.assertNotIn("crates/delta", metadata["unit_files"])
        self.assertIn("crates/delta/tests/fixture.rs", metadata["hidden_tests"], "the grader restores the test-only crate's test with every other hidden test")
        self.assertEqual(metadata["n"], 5)
        self.assertEqual(metadata["checked_units"], ["crates/alpha", "crates/beta", "evals"])
        self.assertEqual(metadata["unit_tests"]["evals"], ["evals/check_test.py"])
        self.assertEqual(metadata["unit_packages"]["crates/beta"], "beta")
        self.assertEqual(metadata["revert_unit"], "crates/alpha")
        self.assertEqual(metadata["shared_element"]["element"], "shared_thing")
        self.assertEqual(metadata["source"], {"commit": self.sweep, "parent": self.parent, "subject": "Use a dash between the fields of every heading", "repo": str(self.sweep_repo)})
        self.assertTrue(metadata["review"].startswith("pending:"))
        self.assertEqual(task.budget, removal.budget_for(3))
        self.assertEqual(task.protected, teams.PROTECTED)

    def test_the_grader_holds_the_fan_out_grade_and_both_corruptions_and_no_commit_diff(self) -> None:
        grader = self.out / "grader"
        self.assertFalse((grader / "oracle.patch").exists())
        self.assertTrue((grader / "grade.py").read_text(encoding="utf-8").startswith("#!/usr/bin/python3\n\"\"\"Hidden checks for a fan-out task"))
        specification = json.loads((grader / "specification.json").read_text(encoding="utf-8"))
        self.assertEqual([unit["name"] for unit in specification["units"]], ["crates/alpha", "crates/beta", "evals"])
        self.assertEqual(specification["units"][0]["hidden_test_names"], {"crates/alpha/src/lib_test.rs": ["joins_with_a_dash"]})
        self.assertEqual(specification["units"][2]["python_tests"], ["evals/check_test.py"])
        self.assertEqual(sorted(path.name for path in protocol.corruptions(self.out)), ["rename-shared-element", "revert-one-unit"])
        revert = json.loads((grader / "corruptions/revert-one-unit/revert.json").read_text(encoding="utf-8"))
        self.assertEqual(revert, {"unit": "crates/alpha", "restore": ["crates/alpha/src/lib.rs"], "remove": []})
        self.assertEqual((grader / "corruptions/revert-one-unit/parent/crates/alpha/src/lib.rs").read_text(encoding="utf-8"), PARENT_FILES["crates/alpha/src/lib.rs"])
        rename = json.loads((grader / "corruptions/rename-shared-element/rename.json").read_text(encoding="utf-8"))
        self.assertEqual(rename["element"], "shared_thing")
        self.assertFalse((self.out / "workspace").exists(), "the workspace copy is removed once the patch records it")

    def test_the_regenerated_workspace_runs_the_workspace_check(self) -> None:
        root = self.scratch() / "root"
        protocol.materialize(self.out, root)
        checks = (root / "workspace/checks/run.sh").read_text(encoding="utf-8")
        self.assertIn("cargo test --workspace\n", checks)
        self.assertIn("cargo clippy --workspace -- -D warnings\n", checks)
        self.assertEqual((root / "workspace/crates/alpha/src/lib.rs").read_text(encoding="utf-8"), PARENT_FILES["crates/alpha/src/lib.rs"])
        self.assertEqual((root / "workspace/crates/alpha/src/lib_test.rs").read_text(encoding="utf-8"), SWEEP_FILES["crates/alpha/src/lib_test.rs"], "the visible subset")

    def test_a_named_revert_unit_is_taken_and_an_unchecked_one_refused(self) -> None:
        out = self.scratch() / "beta-first"
        authored = teams.author_fan_out(self.sweep_repo, self.sweep, out, "beta-first", revert_unit="crates/beta")
        self.assertEqual(authored.revert_unit, "crates/beta")
        with self.assertRaises(ValueError) as caught:
            teams.author_fan_out(self.sweep_repo, self.sweep, self.scratch() / "docs-first", "docs-first", revert_unit="docs")
        self.assertIn("'docs'", str(caught.exception))


class FanOutGrading(Repositories):
    """The fan-out grade script, driven with the stand-in cargo."""

    def setUp(self) -> None:
        self.out = self.scratch() / "dash-sweep"
        teams.author_fan_out(self.sweep_repo, self.sweep, self.out, "dash-sweep")
        self.scratch_dir = self.scratch()
        cargo = self.scratch_dir / "cargo"
        cargo.write_text(FAKE_CARGO, encoding="utf-8")
        cargo.chmod(0o755)
        self.build = self.scratch_dir / "build"
        (self.out / "grader/host.json").write_text(json.dumps({"cargo": str(cargo), "build_dir": str(self.build)}), encoding="utf-8")

    def solved_root(self, name: str, corruption: str | None = None) -> Path:
        root = self.scratch_dir / name
        protocol.materialize(self.out, root)
        protocol.apply_oracle(root)
        if corruption is not None:
            protocol.apply_corruption(self.out / "grader/corruptions" / corruption, root / "workspace")
        return root

    def test_the_grader_controls_hold(self) -> None:
        controls = {control.name: control for control in protocol.check_grader_controls(self.out, self.scratch_dir / "controls")}
        self.assertEqual(sorted(controls), ["corruption:rename-shared-element", "corruption:revert-one-unit", "oracle", "untouched"])
        for name, control in controls.items():
            self.assertTrue(control.held, f"{name}: {control.findings}")
        untouched = controls["untouched"].findings
        self.assertTrue(any(finding.startswith("unit crates/alpha test:") and "join_dash is undefined" in finding for finding in untouched), untouched)
        self.assertTrue(any(finding.startswith("unit evals evals/check_test.py:") for finding in untouched), untouched)
        self.assertTrue(any("docs/spec.md lacks the sentence" in finding for finding in untouched), untouched)

    def test_the_oracle_passes_every_unit_and_records_the_verdicts(self) -> None:
        root = self.solved_root("oracle")
        self.assertIsNone(teams.read_units(root), "no grade has run")
        findings, stderr = grade_directly(root, None, "policy:oracle")
        self.assertEqual(findings, [])
        self.assertEqual(teams.parse_units(stderr), {"crates/alpha": True, "crates/beta": True, "evals": True})
        self.assertEqual(teams.read_units(root), {"crates/alpha": True, "crates/beta": True, "evals": True}, "the verdicts are read from the root alone")
        written = sorted((self.build / "dash-sweep/grades/policy-oracle").glob("*/logs/units.json"))
        self.assertEqual(len(written), 1)
        self.assertEqual(json.loads(written[0].read_text(encoding="utf-8")), {"crates/alpha": True, "crates/beta": True, "evals": True})
        logs = sorted((self.build / "dash-sweep/grades/policy-oracle").glob("*/logs/*.log"))
        self.assertIn("unit-crates-alpha-test.log", [log.name for log in logs])
        self.assertIn("integration-test.log", [log.name for log in logs])

    def test_reverting_one_unit_fails_that_unit_alone(self) -> None:
        findings, stderr = grade_directly(self.solved_root("reverted", "revert-one-unit"), None)
        self.assertEqual(teams.parse_units(stderr), {"crates/alpha": False, "crates/beta": True, "evals": True})
        self.assertTrue(all(finding.startswith(("unit crates/alpha test:", "integration test:")) for finding in findings), findings)

    def test_renaming_the_shared_element_fails_the_workspace_check_and_no_unit(self) -> None:
        root = self.solved_root("renamed", "rename-shared-element")
        self.assertIn("shared_thing_renamed", (root / "workspace/crates/alpha/src/lib.rs").read_text(encoding="utf-8"))
        findings, stderr = grade_directly(root, None)
        self.assertEqual(teams.parse_units(stderr), {"crates/alpha": True, "crates/beta": True, "evals": True})
        self.assertEqual(len(findings), 1, findings)
        self.assertTrue(findings[0].startswith("integration test:") and "alpha::shared_thing" in findings[0], findings)

    def test_a_failing_python_unit_test_fails_its_unit(self) -> None:
        root = self.solved_root("python")
        (root / "workspace/evals/check.py").write_text(PARENT_FILES["evals/check.py"], encoding="utf-8")
        findings, stderr = grade_directly(root, None)
        self.assertEqual(teams.parse_units(stderr), {"crates/alpha": True, "crates/beta": True, "evals": False})
        self.assertTrue(any(finding.startswith("unit evals evals/check_test.py: `/usr/bin/python3") for finding in findings), findings)


class SurveyScripts(unittest.TestCase):
    """The two survey scripts over the synthetic tree, run as the grader runs them."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="survey-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        write_files(self.tmp, SURVEY_FILES)
        self.scripts = self.tmp / "scripts-under-test"
        self.scripts.mkdir()
        for survey in teams.SURVEYS.values():
            (self.scripts / f"{survey.name}.py").write_text(survey.script, encoding="utf-8")

    def test_the_error_survey_lists_the_messages_that_name_no_subject(self) -> None:
        items = teams.run_survey(self.scripts / "error-messages.py", self.tmp)
        self.assertEqual(
            items,
            [
                {"path": "crates/one/src/lib.rs", "line": 7, "message": "{0}"},
                {"path": "crates/one/src/lib.rs", "line": 9, "message": "io: {0}"},
                {"path": "crates/one/src/lib.rs", "line": 17, "message": "line {line}: {source}"},
                {"path": "crates/one/src/lib.rs", "line": 19, "message": "e.g. the value is out of range"},
            ],
            "an attribute with an argument after its literal is outside the survey, and a three-segment key path names a subject",
        )

    def test_the_rules_survey_lists_the_sentences_of_uncited_keys(self) -> None:
        items = teams.run_survey(self.scripts / "config-rules.py", self.tmp)
        self.assertEqual(
            items,
            [
                {"key": "name", "sentence": "String."},
                {"key": "name", "sentence": "Required, with at least one character."},
                {"key": "task", "sentence": "String."},
                {"key": "task", "sentence": "What this episode is to do?"},
                {"key": "task", "sentence": "It is written into the log across two lines."},
            ],
        )
        everything = teams.run_survey(self.scripts / "config-rules.py", self.tmp, "--all")
        self.assertEqual([(entry["key"], entry["cited"], len(entry["sentences"])) for entry in everything], [("name", False, 2), ("budget", True, 6), ("task", False, 3)])
        budget = next(entry["sentences"] for entry in everything if entry["key"] == "budget")
        self.assertIn("The ceiling `\"unlimited\"` is one of `a.b`, or none.", budget, "a period inside a backtick span does not end a sentence")
        self.assertIn("- The second rule holds, and always!", budget, "a list item is its own paragraph")

    def test_items_to_add_is_the_least_count_that_drops_recall_below_the_threshold(self) -> None:
        self.assertEqual([teams.items_to_add(count) for count in (1, 9, 10, 24, 82)], [1, 2, 2, 3, 10])

    def test_unnaming_a_message_removes_every_way_of_naming_its_subject(self) -> None:
        self.assertEqual(teams.unname_message("model.{key}: required by `provider`"), "model {name}: required by provider")
        self.assertEqual(teams.unname_message("{0}"), "{0}")
        self.assertEqual(teams.unname_message("model.base.url: {0}"), "model base url: {0}", "a key path of three segments is unnamed whole")
        self.assertEqual(teams.unname_message("e.g. the value"), "e.g. the value", "a one-character segment is no key path")

    def test_a_survey_names_the_file_it_cannot_read(self) -> None:
        (self.tmp / "docs/config.md").unlink()
        with self.assertRaises(RuntimeError) as caught:
            teams.run_survey(self.scripts / "config-rules.py", self.tmp)
        self.assertIn("docs/config.md: absent from", str(caught.exception))
        (self.tmp / "crates/one/src/bytes.rs").write_bytes(b'#[error("\xff")]\n')
        with self.assertRaises(RuntimeError) as caught:
            teams.run_survey(self.scripts / "error-messages.py", self.tmp)
        self.assertIn("crates/one/src/bytes.rs: not UTF-8 at byte 9", str(caught.exception))


class SurveyAuthoring(Repositories):
    def setUp(self) -> None:
        self.scratch_dir = self.scratch()
        self.build = self.scratch_dir / "build"
        self.tasks = {}
        for survey in teams.SURVEYS.values():
            out = self.scratch_dir / survey.name
            self.tasks[survey.name] = teams.author_survey(self.survey_repo, survey, out, survey.name)
            (out / "grader/host.json").write_text(json.dumps({"build_dir": str(self.build)}), encoding="utf-8")

    def test_task_json_states_the_survey_its_answer_shape_and_its_provenance(self) -> None:
        for name, authored in self.tasks.items():
            task = protocol.load(authored.directory)
            self.assertEqual((task.family, task.class_name), ("teams", "survey"), name)
            self.assertEqual(task.metadata["source"], {"commit": self.survey_commit, "repo": str(self.survey_repo)})
            self.assertEqual(task.metadata["survey"], name)
            self.assertEqual(task.metadata["returns"]["required"], ["items"])
            self.assertEqual(task.metadata["threshold"], 0.9)
            self.assertTrue(task.metadata["review"].startswith("revised:"))
            self.assertIn("{\"items\": [...]}", task.text)
            self.assertTrue(task.text.endswith(protocol.CLOSING))
            self.assertFalse((authored.directory / "workspace").exists())
            recorded = json.loads((authored.directory / "grader/oracle/candidate.json").read_text(encoding="utf-8"))
            self.assertEqual(recorded, {"items": authored.items})
        self.assertEqual(self.tasks["error-messages"].task.metadata["item_count"], 4)
        self.assertEqual(self.tasks["config-rules"].task.metadata["item_count"], 5)

    def test_the_corruption_edits_recorded_lines_so_the_survey_grows(self) -> None:
        errors = self.tasks["error-messages"]
        self.assertEqual(len(errors.edits), 1)
        self.assertEqual(errors.edits[0]["line"], 5)
        self.assertEqual(errors.edits[0]["new"], '    #[error("{name}: {rule}")]')
        rules = self.tasks["config-rules"]
        self.assertEqual([edit["line"] for edit in rules.edits], [1])
        self.assertIn("the contract document `budget`", rules.edits[0]["new"])
        self.assertEqual(rules.task.metadata["corruption_adds"], 6)

    def test_the_grader_controls_hold_for_both_surveys(self) -> None:
        for name, authored in self.tasks.items():
            controls = {control.name: control for control in protocol.check_grader_controls(authored.directory, self.scratch_dir / f"controls-{name}")}
            self.assertEqual(sorted(controls), ["corruption:unlisted-items", "oracle", "untouched"], name)
            for control_name, control in controls.items():
                self.assertTrue(control.held, f"{name} {control_name}: {control.findings}")
            self.assertTrue(any("expected an object whose key items holds a list" in finding for finding in controls["untouched"].findings))
            self.assertTrue(any(finding.startswith("recall") for finding in controls["corruption:unlisted-items"].findings), controls["corruption:unlisted-items"].findings)

    def test_removing_one_true_item_from_the_answer_lowers_the_recorded_recall(self) -> None:
        authored = self.tasks["error-messages"]
        root = self.scratch_dir / "short"
        protocol.materialize(authored.directory, root)
        findings, stderr = grade_directly(root, {"items": authored.items[1:]})
        measures = teams.parse_measures(stderr)
        assert measures is not None
        self.assertEqual((measures["precision"], measures["recall"], measures["returned"], measures["matched"], measures["true_items"]), (1.0, 0.75, 3, 3, 4))
        self.assertEqual(findings, ["recall 0.750 is below 0.9: 3 of 4 true items were returned"])
        written = sorted((self.build / "error-messages/grades/arm").glob("*/logs/measures.json"))
        self.assertEqual(len(written), 1)
        self.assertEqual(json.loads(written[0].read_text(encoding="utf-8"))["recall"], 0.75)
        self.assertEqual(teams.read_measures(root), measures, "the measures are read from the root alone")

    def test_a_workspace_the_survey_cannot_run_over_is_a_finding_that_names_the_file(self) -> None:
        rules = self.tasks["config-rules"]
        root = self.scratch_dir / "no-document"
        protocol.materialize(rules.directory, root)
        (root / "workspace/docs/config.md").unlink()
        findings, stderr = grade_directly(root, {"items": []})
        self.assertEqual(len(findings), 1, findings)
        self.assertTrue(findings[0].startswith(f"survey.py cannot run over the workspace {root / 'workspace'}: docs/config.md: absent from"), findings)
        self.assertIsNone(teams.parse_measures(stderr))
        self.assertIsNone(teams.read_measures(root))
        errors = self.tasks["error-messages"]
        root = self.scratch_dir / "bytes"
        protocol.materialize(errors.directory, root)
        (root / "workspace/crates/one/src/bytes.rs").write_bytes(b'#[error("\xff")]\n')
        findings, _ = grade_directly(root, {"items": errors.items})
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("crates/one/src/bytes.rs: not UTF-8 at byte 9", findings[0])

    def test_extra_items_lower_precision_and_identity_ignores_message_and_markup(self) -> None:
        errors = self.tasks["error-messages"]
        root = self.scratch_dir / "extra"
        protocol.materialize(errors.directory, root)
        renamed = [{"path": "./" + item["path"], "line": item["line"], "message": "paraphrased"} for item in errors.items]
        findings, stderr = grade_directly(root, {"items": renamed + [{"path": "crates/one/src/lib.rs", "line": 5, "message": "{key}: {rule}"}]})
        self.assertEqual(findings, ["precision 0.800 is below 0.9: 4 of 5 returned items are true"])
        self.assertEqual(teams.parse_measures(stderr)["precision"], 0.8)  # type: ignore[index]
        rules = self.tasks["config-rules"]
        root = self.scratch_dir / "markup"
        protocol.materialize(rules.directory, root)
        loose = [{"key": item["key"], "sentence": item["sentence"].replace("`", "").upper().rstrip(".?") + "  "} for item in rules.items]
        findings, stderr = grade_directly(root, {"items": loose})
        self.assertEqual(findings, [])
        self.assertEqual(teams.parse_measures(stderr)["recall"], 1.0)  # type: ignore[index]

    def test_a_malformed_value_is_named_by_key_and_kind(self) -> None:
        authored = self.tasks["error-messages"]
        root = self.scratch_dir / "malformed"
        protocol.materialize(authored.directory, root)
        findings, _ = grade_directly(root, {"items": [{"path": "crates/one/src/lib.rs", "line": "7"}, "text"]})
        self.assertIn("returned item 0: key line is '7'; expected an integer", findings)
        self.assertIn("returned item 1 is 'text'; expected an object", findings)
        findings, _ = grade_directly(root, [])
        self.assertTrue(findings[0].startswith("the returned value is []; expected an object whose key items holds a list"), findings)

    def test_a_workspace_that_lost_a_recorded_item_is_a_finding(self) -> None:
        authored = self.tasks["error-messages"]
        root = self.scratch_dir / "lost"
        protocol.materialize(authored.directory, root)
        source = root / "workspace/crates/one/src/lib.rs"
        source.write_text(source.read_text(encoding="utf-8").replace('    #[error("{0}")]\n    Plain(String),\n', ""), encoding="utf-8")
        findings, _ = grade_directly(root, {"items": authored.items})
        self.assertTrue(any(finding.startswith("the workspace is surveyed as given, and 2 recorded item(s) are absent") for finding in findings), findings)


class Parsing(unittest.TestCase):
    def test_the_recorded_lines_are_read_back_and_checked(self) -> None:
        self.assertIsNone(teams.parse_units("unit crates/alpha test: exit 0 in 1s\n"))
        self.assertEqual(teams.parse_units("noise\nunits: {\"a\": true, \"b\": false}\n"), {"a": True, "b": False})
        with self.assertRaises(ValueError):
            teams.parse_units('units: {"a": "yes"}')
        self.assertIsNone(teams.parse_measures(""))
        line = teams.MEASURES_PREFIX + json.dumps({"precision": 1.0, "recall": 0.5, "returned": 2, "matched": 2, "true_items": 4, "extra": 1})
        self.assertEqual(teams.parse_measures(line), {"precision": 1.0, "recall": 0.5, "returned": 2, "matched": 2, "true_items": 4})
        with self.assertRaises(ValueError) as caught:
            teams.parse_measures(teams.MEASURES_PREFIX + '{"precision": 1.0}')
        self.assertIn("lacks recall, returned, matched, true_items", str(caught.exception))

    def test_the_recorded_files_are_read_from_the_root_and_checked(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="root-"))
        self.addCleanup(shutil.rmtree, root, True)
        self.assertIsNone(teams.read_units(root))
        self.assertIsNone(teams.read_measures(root))
        (root / "grader").mkdir()
        (root / "grader/units.json").write_text('{"a": true, "b": "yes"}', encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            teams.read_units(root)
        self.assertIn("grader/units.json", str(caught.exception))
        (root / "grader/units.json").write_text('{"a": true}', encoding="utf-8")
        self.assertEqual(teams.read_units(root), {"a": True})
        (root / "grader/measures.json").write_text('{"precision": 1.0}', encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            teams.read_measures(root)
        self.assertIn("lacks recall, returned, matched, true_items", str(caught.exception))


class CommandLine(Repositories):
    def test_fan_out_and_survey_report_what_they_wrote_and_a_bad_commit_exits_2(self) -> None:
        out = self.scratch()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = teams.main(["fan-out", "--repo", str(self.sweep_repo), "--commit", self.sweep, "--out", str(out / "sweep"), "--name", "sweep"])
        self.assertEqual(status, 0, stdout.getvalue())
        self.assertIn("unit crates/alpha: checked, 1 implementation file(s), 1 hidden test(s)", stdout.getvalue())
        self.assertIn("unit docs: unchecked", stdout.getvalue())
        self.assertNotIn("crates/delta", stdout.getvalue())
        self.assertIn("rename corruption: shared_thing in crates/alpha, used by crates/gamma", stdout.getvalue())
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = teams.main(["survey", "--repo", str(self.survey_repo), "--survey", "config-rules", "--out", str(out / "rules"), "--name", "rules"])
        self.assertEqual(status, 0, stdout.getvalue())
        self.assertIn("items: 5; the corruption edits 1 line(s)", stdout.getvalue())
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = teams.main(["fan-out", "--repo", str(self.sweep_repo), "--commit", "0" * 40, "--out", str(out / "bad"), "--name", "bad"])
        self.assertEqual(status, 2)
        self.assertTrue(stderr.getvalue().startswith("teams: git rev-parse"), stderr.getvalue())
        self.assertFalse((out / "bad").exists())

    def test_verify_runs_the_controls_of_a_survey_task(self) -> None:
        out = self.scratch()
        teams.author_survey(self.survey_repo, teams.SURVEYS["error-messages"], out / "errors", "errors")
        (out / "errors/grader/host.json").write_text(json.dumps({"build_dir": str(out / "build")}), encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = teams.main(["verify", "--task", str(out / "errors"), "--scratch", str(out / "scratch"), "--timeout", "60"])
        self.assertEqual(status, 0, stdout.getvalue())
        self.assertIn("corruption:unlisted-items: held (expected to fail, failed)", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
