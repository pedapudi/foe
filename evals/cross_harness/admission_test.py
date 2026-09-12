#!/usr/bin/python3
"""Unit tests for the admission check: recorded cargo output, fabricated episode logs, no cargo and no sandbox."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import admission  # noqa: E402

# One crate suite as cargo prints it when tests that exercise sandboxing
# fail inside a sandbox: the verdict lines, the captured output of each
# failure, the indented failure list, and the summary.
SANDBOXED_RUN = """\
   Compiling foe-core v0.1.0 (/scratch/root/workspace/crates/core)
    Finished `test` profile [unoptimized + debuginfo] target(s) in 17.42s
     Running unittests src/lib.rs (target/debug/deps/foe_core-1a2b3c4d)

running 189 tests
test budget::tests::seconds_elapse_on_the_wall_clock ... ok
test budget::tests::restoration_retains_spend_and_elapsed_allowance_without_double_charging ... ok
test sandbox::tests::a_denied_read_names_the_path ... FAILED
test sandbox::tests::a_granted_write_succeeds ... FAILED
test session::tests::a_session_serves_a_granted_bind_port_across_calls ... FAILED
test exec::tests::a_captured_elf_runs_after_its_source_is_replaced_under_landlock ... ok

failures:

---- sandbox::tests::a_denied_read_names_the_path stdout ----
thread 'sandbox::tests::a_denied_read_names_the_path' panicked at crates/core/src/sandbox_test.rs:41:5

---- sandbox::tests::a_granted_write_succeeds stdout ----
thread 'sandbox::tests::a_granted_write_succeeds' panicked at crates/core/src/sandbox_test.rs:73:5

---- session::tests::a_session_serves_a_granted_bind_port_across_calls stdout ----
thread 'session::tests::a_session_serves_a_granted_bind_port_across_calls' panicked at crates/core/src/session_test.rs:12:5

failures:
    sandbox::tests::a_denied_read_names_the_path
    sandbox::tests::a_granted_write_succeeds
    session::tests::a_session_serves_a_granted_bind_port_across_calls

test result: FAILED. 186 passed; 3 failed; 0 ignored; 0 measured; 0 filtered out; finished in 2.31s

error: test failed, to rerun pass `-p foe-core --lib`
"""

# The same suite outside a sandbox, with the task's own test failing because
# the feature under test was removed.
UNSOLVED_RUN = """\
running 189 tests
test budget::tests::seconds_elapse_on_the_wall_clock ... ok
test budget::tests::restoration_retains_spend_and_elapsed_allowance_without_double_charging ... FAILED
test sandbox::tests::a_denied_read_names_the_path ... ok

failures:
    budget::tests::restoration_retains_spend_and_elapsed_allowance_without_double_charging

test result: FAILED. 188 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 2.10s
"""

PASSING_RUN = """\
running 3 tests
test budget::tests::seconds_elapse_on_the_wall_clock ... ok
test budget::tests::a_grant_of_zero_on_any_dimension_names_that_limit ... ignored
test sandbox::tests::a_denied_read_names_the_path ... ok

test result: ok. 2 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.01s
"""

ONE_CRATE_SCRIPT = """\
#!/bin/sh
# The visible subset of the checks this task is judged on.
set -eu
cd "$(dirname "$0")/.."
cargo test -p foe-core
cargo clippy -p foe-core -- -D warnings
scripts/loc.sh
"""

WHOLE_WORKSPACE_SCRIPT = """\
#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
cargo test --workspace
cargo clippy --workspace -- -D warnings
scripts/loc.sh
"""

NO_SUITE_SCRIPT = """\
#!/usr/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "step 1: line ceilings"
/usr/bin/bash scripts/loc.sh
echo "step 2: cargo check"
cargo check --workspace --quiet
"""

HIDDEN_TEST_SOURCE = """\
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn restoration_retains_spend_and_elapsed_allowance_without_double_charging() {}

    #[test]
    #[should_panic]
    fn a_grant_of_zero_on_any_dimension_names_that_limit() {}

    fn not_a_test() {}
}
"""


def measurement(environment: str, exit_status: int, output: str = "", note: str | None = None, seconds: float = 1.0) -> admission.Measurement:
    return admission.Measurement(
        environment,
        exit_status,
        seconds,
        admission.parse_failing_tests(output),
        admission.parse_test_names(output),
        output,
        note,
    )


def report_with(host: str, foe: str, codex: str, hidden: list[str], crates: list[str]) -> admission.TaskReport:
    report = admission.TaskReport("charge-setup-time-once", Path("/tasks/charge-setup-time-once"), "autonomy", "solvable", True)
    report.hidden_tests = hidden
    report.crates = crates
    report.measurements = {
        admission.HOST: measurement(admission.HOST, 0 if "FAILED" not in host else 101, host),
        admission.FOE: measurement(admission.FOE, 0 if "FAILED" not in foe else 101, foe),
        admission.CODEX: measurement(admission.CODEX, 0 if "FAILED" not in codex else 101, codex),
    }
    admission.verdict_of(report)
    return report


class FailureParsing(unittest.TestCase):
    def test_a_sandboxed_run_yields_every_failing_name_once(self) -> None:
        self.assertEqual(
            admission.parse_failing_tests(SANDBOXED_RUN),
            [
                "sandbox::tests::a_denied_read_names_the_path",
                "sandbox::tests::a_granted_write_succeeds",
                "session::tests::a_session_serves_a_granted_bind_port_across_calls",
            ],
        )

    def test_a_passing_run_names_no_failure(self) -> None:
        self.assertEqual(admission.parse_failing_tests(PASSING_RUN), [])

    def test_the_inventory_holds_every_test_a_verdict_was_reported_for(self) -> None:
        self.assertEqual(
            admission.parse_test_names(PASSING_RUN),
            [
                "budget::tests::seconds_elapse_on_the_wall_clock",
                "budget::tests::a_grant_of_zero_on_any_dimension_names_that_limit",
                "sandbox::tests::a_denied_read_names_the_path",
            ],
        )

    def test_the_indented_list_alone_is_enough(self) -> None:
        listed = "failures:\n    alpha::tests::one\n    alpha::tests::two\n\ntest result: FAILED. 0 passed; 2 failed;\n"
        self.assertEqual(admission.parse_failing_tests(listed), ["alpha::tests::one", "alpha::tests::two"])

    def test_output_with_no_test_lines_names_nothing(self) -> None:
        self.assertEqual(admission.parse_failing_tests("error[E0433]: failed to resolve\nerror: could not compile\n"), [])
        self.assertEqual(admission.parse_test_names(""), [])


class CratesChecked(unittest.TestCase):
    def test_a_named_package_is_read_from_the_cargo_test_line(self) -> None:
        self.assertEqual(admission.checked_crates(ONE_CRATE_SCRIPT, ["foe-core", "foe-log"]), ["foe-core"])

    def test_several_named_packages_are_all_read(self) -> None:
        script = "cargo test -p foe -p foe-core -p foe-team\ncargo clippy -p foe-code -- -D warnings\n"
        self.assertEqual(admission.checked_crates(script, ["foe-code"]), ["foe", "foe-core", "foe-team"])

    def test_the_whole_workspace_covers_every_package_given(self) -> None:
        self.assertEqual(admission.checked_crates(WHOLE_WORKSPACE_SCRIPT, ["foe-core", "foe-log"]), ["foe-core", "foe-log"])

    def test_a_script_with_no_crate_suite_names_no_crate(self) -> None:
        self.assertEqual(admission.checked_crates(NO_SUITE_SCRIPT, ["foe-core", "foe-log"]), [])

    def test_the_packages_come_from_the_workspace_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            for name in ("core", "log"):
                manifest = workspace / "crates" / name / "Cargo.toml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text(f'[package]\nname = "foe-{name}"\nversion = "0.1.0"\n\n[dependencies]\nname = "wrong"\n', encoding="utf-8")
            (workspace / "crates" / "empty").mkdir()
            self.assertEqual(admission.workspace_packages(workspace), ["foe-core", "foe-log"])


class HiddenTests(unittest.TestCase):
    def task_directory(self, temporary: str, specification: dict | None, source: str | None) -> Path:
        task_dir = Path(temporary) / "a-task"
        (task_dir / "grader").mkdir(parents=True)
        if specification is not None:
            (task_dir / "grader" / "specification.json").write_text(json.dumps(specification), encoding="utf-8")
        if source is not None:
            tests = task_dir / "grader" / "tests" / "crates" / "core" / "src"
            tests.mkdir(parents=True)
            (tests / "budget_test.rs").write_text(source, encoding="utf-8")
        return task_dir

    def test_the_specification_and_the_test_tree_are_both_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_dir = self.task_directory(
                temporary,
                {"hidden_test_names": {"crates/core/src/budget_test.rs": ["seconds_elapse_on_the_wall_clock"]}},
                HIDDEN_TEST_SOURCE,
            )
            self.assertEqual(
                admission.hidden_test_names(task_dir),
                [
                    "a_grant_of_zero_on_any_dimension_names_that_limit",
                    "restoration_retains_spend_and_elapsed_allowance_without_double_charging",
                    "seconds_elapse_on_the_wall_clock",
                ],
            )

    def test_a_task_with_neither_names_no_hidden_test(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(admission.hidden_test_names(self.task_directory(temporary, {"task": "a-task"}, None)), [])

    def test_a_specification_that_is_not_json_names_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_dir = self.task_directory(temporary, {"task": "a-task"}, None)
            (task_dir / "grader" / "specification.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(admission.AdmissionError) as raised:
                admission.hidden_test_names(task_dir)
            self.assertIn("specification.json", str(raised.exception))

    def test_a_specification_of_the_wrong_shape_names_the_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_dir = self.task_directory(temporary, {"hidden_test_names": "one_test"}, None)
            with self.assertRaises(admission.AdmissionError) as raised:
                admission.hidden_test_names(task_dir)
            self.assertIn("hidden_test_names", str(raised.exception))


class HiddenAttribution(unittest.TestCase):
    """The safety assertion: whether a failing test is one the hidden grader relies on."""

    def test_a_failure_among_the_hidden_tests_is_named_as_the_task_s_own(self) -> None:
        own, unrelated = admission.split_by_hidden(
            admission.parse_failing_tests(UNSOLVED_RUN),
            ["restoration_retains_spend_and_elapsed_allowance_without_double_charging", "seconds_elapse_on_the_wall_clock"],
        )
        self.assertEqual(own, ["budget::tests::restoration_retains_spend_and_elapsed_allowance_without_double_charging"])
        self.assertEqual(unrelated, [])

    def test_sandbox_failures_are_unrelated_to_a_budget_task(self) -> None:
        own, unrelated = admission.split_by_hidden(
            admission.parse_failing_tests(SANDBOXED_RUN),
            ["restoration_retains_spend_and_elapsed_allowance_without_double_charging"],
        )
        self.assertEqual(own, [])
        self.assertEqual(len(unrelated), 3)

    def test_a_task_with_no_hidden_tests_attributes_every_failure_as_unrelated(self) -> None:
        own, unrelated = admission.split_by_hidden(["alpha::tests::one"], [])
        self.assertEqual((own, unrelated), ([], ["alpha::tests::one"]))


class Verdicts(unittest.TestCase):
    def test_a_check_that_passes_everywhere_is_admissible(self) -> None:
        report = report_with(PASSING_RUN, PASSING_RUN, PASSING_RUN, [], ["foe-core"])
        self.assertEqual(report.verdict, admission.ADMISSIBLE)
        self.assertEqual(report.reasons, [])

    def test_a_sandbox_only_failure_is_inadmissible_and_named_unrelated(self) -> None:
        report = report_with(PASSING_RUN, SANDBOXED_RUN, SANDBOXED_RUN, ["seconds_elapse_on_the_wall_clock"], ["foe-core"])
        self.assertEqual(report.verdict, admission.INADMISSIBLE)
        self.assertEqual(len(report.reasons), 2)
        self.assertIn("the foe check exited 101", report.reasons[0])
        self.assertIn("0 of the hidden grader's tests and 3 unrelated tests failed", report.reasons[0])

    def test_a_hidden_test_failing_everywhere_is_reported_as_the_task_s_own(self) -> None:
        report = report_with(
            UNSOLVED_RUN,
            UNSOLVED_RUN,
            UNSOLVED_RUN,
            ["restoration_retains_spend_and_elapsed_allowance_without_double_charging"],
            ["foe-core"],
        )
        self.assertEqual(report.verdict, admission.INADMISSIBLE)
        self.assertIn("1 of the hidden grader's tests", report.reasons[0])

    def test_an_unsolved_oracle_is_named_before_the_environments(self) -> None:
        report = admission.TaskReport("a-task", Path("/tasks/a-task"), "autonomy", "non-terminating", False)
        report.measurements = {
            admission.HOST: measurement(admission.HOST, admission.TIMED_OUT_STATUS, "", "the check did not finish within 90 seconds"),
            admission.FOE: measurement(admission.FOE, admission.TIMED_OUT_STATUS, "", "the check did not finish within 90 seconds"),
            admission.CODEX: measurement(admission.CODEX, admission.TIMED_OUT_STATUS, "", "the check did not finish within 90 seconds"),
        }
        admission.verdict_of(report)
        self.assertEqual(report.verdict, admission.INADMISSIBLE)
        self.assertIn("no solved workspace", report.reasons[0])
        self.assertIn("did not finish within 90 seconds", report.reasons[1])


class EpisodeReading(unittest.TestCase):
    def log_with(self, temporary: str, data: dict) -> Path:
        log = Path(temporary) / "episode.jsonl"
        log.write_text(
            json.dumps({"seq": 1, "type": "episode/start", "data": {}}) + "\n"
            + json.dumps({"seq": 2, "type": "tool/result", "data": data}) + "\n",
            encoding="utf-8",
        )
        return log

    def test_the_check_result_supplies_the_exit_status_and_the_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = self.log_with(temporary, {"call_id": "c1", "name": "check", "is_error": False, "value": {"exit_code": 101, "stdout": SANDBOXED_RUN, "stderr": ""}})
            result = admission.read_check_result(log, 900, 12.5, 0)
            self.assertEqual(result.exit_status, 101)
            self.assertEqual(len(result.failing), 3)
            self.assertIsNone(result.note)

    def test_a_timed_out_check_is_recorded_as_unfinished(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = self.log_with(temporary, {"call_id": "c1", "name": "check", "value": {"exit_code": 0, "timed_out": True, "stdout": ""}})
            result = admission.read_check_result(log, 90, 91.0, 0)
            self.assertEqual(result.exit_status, admission.TIMED_OUT_STATUS)
            self.assertIn("within 90 seconds", result.note or "")

    def test_a_tool_error_is_recorded_with_no_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = self.log_with(temporary, {"call_id": "c1", "name": "check", "is_error": True, "failure": {"kind": "permission-denied"}})
            result = admission.read_check_result(log, 900, 1.0, 2)
            self.assertEqual(result.exit_status, admission.NO_RESULT_STATUS)
            self.assertIn("permission-denied", result.note or "")

    def test_a_log_without_a_check_result_names_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "episode.jsonl"
            log.write_text(json.dumps({"seq": 1, "type": "episode/start", "data": {}}) + "\n", encoding="utf-8")
            result = admission.read_check_result(log, 900, 1.0, 3)
            self.assertEqual(result.exit_status, admission.NO_RESULT_STATUS)
            self.assertIn(str(log), result.note or "")

    def test_an_absent_log_names_its_path(self) -> None:
        with self.assertRaises(admission.AdmissionError) as raised:
            admission.read_check_result(Path("/nonexistent/episode.jsonl"), 900, 1.0, 0)
        self.assertIn("/nonexistent/episode.jsonl", str(raised.exception))


class EpisodeDocument(unittest.TestCase):
    def test_the_workspace_is_granted_execute_so_the_build_s_own_binaries_run(self) -> None:
        document = admission.episode_document(Path("/scratch/root/workspace"), Path("/scratch/check"), 900)
        self.assertEqual(document["version"], 4)
        self.assertEqual(document["tools"], ["check"])
        self.assertEqual(document["sandbox"], {"mode": "required"})
        self.assertIn("/scratch/root/workspace", document["grants"]["execute"])
        self.assertEqual(document["grants"]["write"], ["/scratch/root/workspace"])
        self.assertEqual(document["tool_defs"]["check"]["timeout_seconds"], 900)
        self.assertGreater(document["budget"]["seconds"], 900)

    def test_the_wrapper_sets_only_the_four_variables_and_marks_an_absent_command(self) -> None:
        body = admission.wrapper_body(Path("/scratch/root/workspace"))
        self.assertEqual([line.split("=")[0] for line in body.split("\n")[:3]], ["PATH", "LANG", "TMPDIR"])
        self.assertIn("export PATH LANG HOME TMPDIR", body)
        # HOME matches what a bash command of the same episode receives, so a
        # suite whose tests read it runs the same under either arm.
        # The real user's home, so a toolchain manager finds its installation;
        # the workspace would send it looking inside the tree and then to the network.
        self.assertIn(f"HOME='{admission.home_directory()}'", body)
        self.assertNotIn("HOME='/scratch/root/workspace'", body)
        self.assertIn(f"./{admission.CHECK_SUITE} 2>&1", body)
        self.assertIn(admission.CHECK_SUITE_UNAVAILABLE, body)
        # Nothing beyond the four is set: the runtime starts a configured
        # executable with an empty environment and the wrapper states what it needs.
        self.assertNotIn("CARGO_HOME", body)
        self.assertNotIn("RUSTUP_HOME", body)


class ReportShape(unittest.TestCase):
    def reports(self) -> list[admission.TaskReport]:
        return [
            report_with(PASSING_RUN, PASSING_RUN, PASSING_RUN, ["seconds_elapse_on_the_wall_clock"], ["foe-code"]),
            report_with(PASSING_RUN, SANDBOXED_RUN, SANDBOXED_RUN, ["seconds_elapse_on_the_wall_clock"], ["foe-core", "foe-log"]),
        ]

    def test_the_table_carries_a_row_per_task_with_the_crates_and_the_three_environments(self) -> None:
        table = admission.markdown_table(self.reports()).splitlines()
        self.assertEqual(table[0], "| task | class | crates | host | foe | codex | verdict |")
        self.assertEqual(len(table), 4)
        self.assertIn("| foe-code |", table[2])
        self.assertIn("| foe-core, foe-log |", table[3])
        self.assertIn("exit 0, 1s", table[2])
        self.assertIn("0 hidden and 3 unrelated failing", table[3])
        self.assertTrue(table[2].endswith("| admissible |"))
        self.assertTrue(table[3].endswith("| inadmissible |"))

    def test_the_json_report_states_the_counts_the_crates_and_the_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = admission.Settings(Path("/tasks"), (), Path("/bin/true"), Path(temporary) / "out", 900)
            report_path, table_path = admission.write_report(self.reports(), settings, 42.0)
            document = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], admission.SCHEMA_VERSION)
            self.assertEqual(document["command"], "check")
            self.assertEqual(document["counts"], {"admissible": 1, "inadmissible": 1, "changed": 0})
            self.assertEqual(document["seconds_bound"], 900)
            second = document["tasks"][1]
            self.assertEqual(second["crates_checked"], ["foe-core", "foe-log"])
            self.assertEqual(second["verdict"], "inadmissible")
            self.assertEqual(sorted(second["environments"]), ["codex", "foe", "host"])
            self.assertEqual(second["environments"]["foe"]["failing_hidden_tests"], [])
            self.assertEqual(len(second["environments"]["foe"]["failing_unrelated_tests"]), 3)
            self.assertEqual(second["environments"]["host"]["output_tail"], "")
            self.assertIn("test result: FAILED", second["environments"]["foe"]["output_tail"])
            self.assertIn("| task | class | crates |", table_path.read_text(encoding="utf-8"))


class Arguments(unittest.TestCase):
    def test_a_foe_binary_that_is_not_executable_ends_the_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plain = Path(temporary) / "foe"
            plain.write_text("", encoding="utf-8")
            stderr = StringIO()
            with redirect_stderr(stderr):
                status = admission.main(["check", "--tasks", temporary, "--foe", str(plain), "--out", str(Path(temporary) / "out")])
            self.assertEqual(status, 2)
            self.assertIn("is not an executable file", stderr.getvalue())

    def test_a_bound_of_zero_seconds_ends_the_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stderr = StringIO()
            with redirect_stderr(stderr):
                status = admission.main(["check", "--tasks", temporary, "--foe", "/bin/sh", "--seconds", "0", "--out", str(Path(temporary) / "out")])
            self.assertEqual(status, 2)
            self.assertIn("--seconds is 0", stderr.getvalue())

    def test_a_tasks_directory_with_no_task_ends_the_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stderr = StringIO()
            with redirect_stderr(stderr):
                status = admission.main(["check", "--tasks", temporary, "--foe", "/bin/sh", "--out", str(Path(temporary) / "out")])
            self.assertEqual(status, 2)
            self.assertIn("holds no task directory", stderr.getvalue())

    def test_a_named_task_the_directory_lacks_ends_the_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            present = Path(temporary) / "a-task"
            present.mkdir()
            (present / "task.json").write_text("{}", encoding="utf-8")
            settings = admission.Settings(Path(temporary), ("another-task",), Path("/bin/sh"), Path(temporary) / "out", 900)
            with self.assertRaises(admission.AdmissionError) as raised:
                admission.select_tasks(settings)
            self.assertIn("another-task", str(raised.exception))

    def test_the_selection_keeps_the_order_the_caller_named(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            for name in ("b-task", "a-task"):
                directory = Path(temporary) / name
                directory.mkdir()
                (directory / "task.json").write_text("{}", encoding="utf-8")
            settings = admission.Settings(Path(temporary), ("b-task", "a-task"), Path("/bin/sh"), Path(temporary) / "out", 900)
            self.assertEqual([path.name for path in admission.select_tasks(settings)], ["b-task", "a-task"])
            unselected = admission.Settings(Path(temporary), (), Path("/bin/sh"), Path(temporary) / "out", 900)
            self.assertEqual([path.name for path in admission.select_tasks(unselected)], ["a-task", "b-task"])


if __name__ == "__main__":
    unittest.main()
