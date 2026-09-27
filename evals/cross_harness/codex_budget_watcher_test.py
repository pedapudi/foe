#!/usr/bin/python3
"""Unit tests for the Codex budget watcher: a scripted stand-in for Codex, no model, no credential."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_budget_watcher as watcher_module  # noqa: E402
from codex_budget_watcher import Ledger, Stop, Watcher, check_limits, run_with_watcher  # noqa: E402

PYTHON = "/usr/bin/python3"

# The stand-in for `codex exec`: it takes a CODEX_HOME and a plan of steps,
# writes session files of the documented shape under CODEX_HOME/sessions as
# the steps say, and sleeps between them. Each step is a list whose first
# element names it.
FAKE_CODEX = r'''#!/usr/bin/python3
import json, os, signal, subprocess, sys, time
from pathlib import Path

home = Path(sys.argv[1])
plan = json.loads(sys.argv[2])
day = home / "sessions" / "2026" / "09" / "11"
day.mkdir(parents=True, exist_ok=True)
ordinals = {}
children = []


def path(name):
    return day / f"rollout-2026-09-11T09-00-00-{name}.jsonl"


def append(name, record):
    ordinal = ordinals.get(name, 0)
    if ordinal == 0:
        meta = {"timestamp": "2026-09-11T09:00:00.000Z", "ordinal": 0, "type": "session_meta",
                "payload": {"id": name, "cli_version": "0.153.4", "model_provider": "test", "source": "cli"}}
        with path(name).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(meta) + "\n")
        ordinal = 1
    record = {"timestamp": "2026-09-11T09:00:01.000Z", "ordinal": ordinal, **record}
    with path(name).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
        handle.flush()
    ordinals[name] = ordinal + 1


def usage(input_tokens, output_tokens):
    return {"input_tokens": input_tokens, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
            "output_tokens": output_tokens, "reasoning_output_tokens": 0, "total_tokens": input_tokens + output_tokens}


for step in plan:
    kind, args = step[0], step[1:]
    if kind == "sleep":
        time.sleep(args[0])
    elif kind == "usage":
        name, input_tokens, output_tokens, response_id = args
        append(name, {"type": "token_usage_record", "payload": {"thread_id": name, "response_id": response_id,
                                                               "usage": usage(input_tokens, output_tokens)}})
    elif kind == "count":
        name, input_tokens, output_tokens = args
        append(name, {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": usage(input_tokens, output_tokens), "last_token_usage": usage(input_tokens, output_tokens),
            "model_context_window": 258400}, "rate_limits": None}})
    elif kind == "ignore_term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    elif kind == "report_term":
        signal.signal(signal.SIGTERM, lambda *_: print("SIGTERM", flush=True))
    elif kind == "spawn_sleep":
        children.append(subprocess.Popen(["/bin/sleep", str(args[0])]))
    elif kind == "print":
        print(args[0], flush=True)
    elif kind == "exit":
        sys.exit(args[0])
    else:
        raise SystemExit(f"unknown step {kind}")
'''


def write_fake_codex(root: Path) -> Path:
    script = root / "fake_codex.py"
    script.write_text(FAKE_CODEX, encoding="utf-8")
    script.chmod(0o755)
    return script


def session_line(record: dict) -> str:
    return json.dumps({"timestamp": "2026-09-11T09:00:01.000Z", "ordinal": 1, **record}) + "\n"


def usage_record(response_id: str, input_tokens: int, output_tokens: int) -> str:
    usage = {"input_tokens": input_tokens, "cached_input_tokens": 3, "cache_write_input_tokens": 0, "output_tokens": output_tokens, "reasoning_output_tokens": 1}
    return session_line({"type": "token_usage_record", "payload": {"response_id": response_id, "usage": usage}})


def count_message(input_tokens: int, output_tokens: int) -> str:
    last = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    return session_line({"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": last, "last_token_usage": last}}})


class LedgerAccounting(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.day = self.home / "sessions" / "2026" / "09" / "11"
        self.day.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_per_response_records_win_over_running_counts_in_the_same_file(self) -> None:
        (self.day / "rollout-a.jsonl").write_text(
            usage_record("r1", 100, 10) + count_message(100, 10) + usage_record("r2", 200, 20) + count_message(200, 20), encoding="utf-8"
        )
        ledger = Ledger(self.home)
        ledger.refresh()
        self.assertEqual(ledger.totals()["input_tokens"], 300)
        self.assertEqual(ledger.totals()["output_tokens"], 30)
        self.assertEqual(ledger.totals()["cached_input_tokens"], 6)
        self.assertEqual(ledger.responses(), 2)

    def test_a_file_without_per_response_records_falls_back_to_its_counts(self) -> None:
        (self.day / "rollout-parent.jsonl").write_text(usage_record("r1", 100, 10), encoding="utf-8")
        (self.day / "rollout-child.jsonl").write_text(
            count_message(50, 5)
            + session_line({"type": "event_msg", "payload": {"type": "token_count", "info": None}})
            + count_message(60, 6),
            encoding="utf-8",
        )
        ledger = Ledger(self.home)
        ledger.refresh()
        self.assertEqual(ledger.totals()["input_tokens"], 210)
        self.assertEqual(ledger.totals()["output_tokens"], 21)
        self.assertEqual(ledger.responses(), 3)
        self.assertEqual(ledger.problems, [])

    def test_one_response_in_two_files_is_counted_once(self) -> None:
        (self.day / "rollout-a.jsonl").write_text(usage_record("shared", 100, 10), encoding="utf-8")
        (self.day / "rollout-b.jsonl").write_text(usage_record("shared", 100, 10) + usage_record("own", 1, 1), encoding="utf-8")
        ledger = Ledger(self.home)
        ledger.refresh()
        self.assertEqual(ledger.totals()["input_tokens"], 101)
        self.assertEqual(ledger.responses(), 2)

    def test_growth_and_new_files_are_read_across_refreshes_including_a_split_line(self) -> None:
        ledger = Ledger(self.home)
        ledger.refresh()
        self.assertEqual(ledger.totals()["input_tokens"], 0, "no file yet")
        path = self.day / "rollout-a.jsonl"
        whole = usage_record("r1", 100, 10)
        with path.open("w", encoding="utf-8") as handle:
            handle.write(whole[:20])
        ledger.refresh()
        self.assertEqual(ledger.totals()["input_tokens"], 0, "an unterminated line waits")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(whole[20:])
        ledger.refresh()
        self.assertEqual(ledger.totals()["input_tokens"], 100)
        later = self.day / "rollout-b.jsonl"
        later.write_text(usage_record("r2", 5, 5), encoding="utf-8")
        ledger.refresh()
        self.assertEqual(ledger.totals()["input_tokens"], 105)
        self.assertEqual(ledger.problems, [])

    def test_a_malformed_line_is_named_and_the_rest_still_counts(self) -> None:
        path = self.day / "rollout-a.jsonl"
        path.write_text("{not json\n" + usage_record("r1", 7, 7) + session_line({"type": "token_usage_record", "payload": {"response_id": "r2", "usage": {"input_tokens": "9"}}}), encoding="utf-8")
        ledger = Ledger(self.home)
        ledger.refresh()
        self.assertEqual(ledger.totals()["input_tokens"], 7)
        self.assertEqual(len(ledger.problems), 2)
        self.assertTrue(ledger.problems[0].startswith(f"{path}:1:"), ledger.problems[0])
        self.assertIn(f"{path}:3: usage field input_tokens is '9'", ledger.problems[1])

    def test_a_file_that_shrank_is_summed_again_from_zero(self) -> None:
        path = self.day / "rollout-a.jsonl"
        path.write_text(count_message(100, 10) + count_message(100, 10), encoding="utf-8")
        ledger = Ledger(self.home)
        ledger.refresh()
        self.assertEqual(ledger.totals()["input_tokens"], 200)
        path.write_text(count_message(100, 10), encoding="utf-8")
        ledger.refresh()
        self.assertEqual(ledger.totals()["input_tokens"], 100)
        self.assertEqual(ledger.responses(), 1)
        self.assertEqual(len(ledger.problems), 1)
        self.assertIn("shrank from", ledger.problems[0])

    def test_a_record_keyed_by_its_line_is_counted_once_across_a_shrink(self) -> None:
        path = self.day / "rollout-a.jsonl"
        keyed_by_line = session_line({"type": "token_usage_record", "payload": {"usage": {"input_tokens": 5}}})
        path.write_text(keyed_by_line + keyed_by_line, encoding="utf-8")
        ledger = Ledger(self.home)
        ledger.refresh()
        self.assertEqual(ledger.totals()["input_tokens"], 10, "two records without a response id are two responses")
        path.write_text(keyed_by_line, encoding="utf-8")
        ledger.refresh()
        self.assertEqual(ledger.totals()["input_tokens"], 10)
        self.assertEqual(ledger.responses(), 2)


class Limits(unittest.TestCase):
    def test_unknown_keys_and_non_positive_values_are_named(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown key 'tokens'"):
            check_limits({"tokens": 1})
        with self.assertRaisesRegex(ValueError, "output_tokens is 0"):
            check_limits({"output_tokens": 0})
        with self.assertRaisesRegex(ValueError, "input_tokens is 1.5 rather than an integer"):
            check_limits({"input_tokens": 1.5})
        self.assertEqual(check_limits({"seconds": 2.5, "input_tokens": 10}), {"seconds": 2.5, "input_tokens": 10})

    def test_the_watcher_refuses_a_process_that_does_not_lead_its_group(self) -> None:
        process = subprocess.Popen([PYTHON, "-c", "import time; time.sleep(30)"])
        try:
            with self.assertRaisesRegex(ValueError, "start_new_session=True"):
                Watcher(Path("/nonexistent"), {"seconds": 1}, process)
        finally:
            process.kill()
            process.wait()

    def test_run_with_watcher_refuses_a_conflicting_codex_home(self) -> None:
        with self.assertRaisesRegex(ValueError, "CODEX_HOME"):
            run_with_watcher(["/bin/true"], Path("/a"), {}, Path("/"), {"CODEX_HOME": "/b"})

    def test_run_with_watcher_validates_before_it_starts_the_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = {"PATH": "/usr/bin:/bin"}
            with self.assertRaisesRegex(ValueError, "unknown key 'tokens'"):
                run_with_watcher(["/bin/sleep", "4471"], home, {"tokens": 1}, home, env)
            with self.assertRaisesRegex(ValueError, "poll_interval is 0"):
                run_with_watcher(["/bin/sleep", "4471"], home, {"seconds": 1}, home, env, poll_interval=0)
            with self.assertRaisesRegex(ValueError, "grace_seconds is -1"):
                run_with_watcher(["/bin/sleep", "4471"], home, {"seconds": 1}, home, env, grace_seconds=-1)
            with self.assertRaisesRegex(ValueError, "ledger reads"):
                run_with_watcher(["/bin/sleep", "4471"], home, {"seconds": 1}, home, env, ledger=Ledger(home / "other"))
        self.assertEqual(subprocess.run(["/usr/bin/pgrep", "-f", "^/bin/sleep 4471$"], capture_output=True, check=False).returncode, 1, "no sleep was started")


class Runs(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "codex-home"
        self.script = write_fake_codex(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def command(self, plan: list) -> list[str]:
        return [PYTHON, str(self.script), str(self.home), json.dumps(plan)]

    def run_plan(self, plan: list, limits: dict, **kwargs) -> tuple:
        started = time.monotonic()
        result = run_with_watcher(self.command(plan), self.home, limits, self.root, {"PATH": "/usr/bin:/bin"}, poll_interval=0.05, **kwargs)
        self.assertLess(time.monotonic() - started, 15, "the run ended within the test's patience")
        return result

    def test_a_run_under_every_limit_completes_with_its_output_and_no_stop(self) -> None:
        plan = [
            ["usage", "parent", 100, 10, "r1"],
            ["count", "parent", 100, 10],
            ["sleep", 0.1],
            ["count", "child", 50, 5],
            ["usage", "parent", 200, 20, "r2"],
            ["print", "done"],
            ["exit", 0],
        ]
        status, stop, stdout, stderr = self.run_plan(plan, {"input_tokens": 1000, "output_tokens": 1000, "seconds": 10})
        self.assertEqual((status, stop, stdout, stderr), (0, None, b"done\n", b""))
        ledger = Ledger(self.home)
        ledger.refresh()
        self.assertEqual(ledger.totals()["input_tokens"], 350)
        self.assertEqual(ledger.totals()["output_tokens"], 35)

    def test_a_failing_run_keeps_its_exit_status(self) -> None:
        status, stop, _, _ = self.run_plan([["exit", 3]], {"seconds": 10})
        self.assertEqual((status, stop), (3, None))

    def test_a_crossing_by_the_final_records_of_an_exited_run_is_no_stop(self) -> None:
        # The record crosses the limit and the process exits before the
        # watcher's next pass, which is one poll interval away.
        plan = [["usage", "parent", 10, 5000, "r1"], ["sleep", 0.1], ["exit", 0]]
        ledger = Ledger(self.home)
        status, stop, _, _ = run_with_watcher(self.command(plan), self.home, {"output_tokens": 1000}, self.root, {"PATH": "/usr/bin:/bin"}, poll_interval=0.5, ledger=ledger)
        self.assertEqual((status, stop), (0, None))
        self.assertEqual(ledger.totals()["output_tokens"], 5000, "the caller's ledger holds the usage of the run")

    def test_a_direct_poll_after_the_process_exited_records_no_stop(self) -> None:
        plan = [["usage", "parent", 10, 5000, "r1"], ["exit", 0]]
        process = subprocess.Popen(self.command(plan), cwd=self.root, env={"PATH": "/usr/bin:/bin", "CODEX_HOME": str(self.home)}, start_new_session=True)
        process.wait()
        watcher = Watcher(self.home, {"output_tokens": 100, "seconds": 0.001}, process)
        time.sleep(0.01)
        self.assertIsNone(watcher.poll())
        self.assertIsNone(watcher.stop)
        self.assertEqual(watcher.usage()["output_tokens"], 5000)
        self.assertEqual(process.returncode, 0)

    def test_a_watcher_failure_terminates_the_run_and_is_raised(self) -> None:
        class FailingLedger(Ledger):
            def refresh(self) -> None:
                raise OSError("the session directory is unreadable")

        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "the budget watcher for /bin/sleep failed .* the session directory is unreadable"):
            run_with_watcher(["/bin/sleep", "4463"], self.home, {"seconds": 30}, self.root, {"PATH": "/usr/bin:/bin"}, poll_interval=0.05, ledger=FailingLedger(self.home))
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(subprocess.run(["/usr/bin/pgrep", "-f", "^/bin/sleep 4463$"], capture_output=True, check=False).returncode, 1, "the sleep died with the watcher")

    @unittest.skipIf(os.geteuid() == 0, "file permissions do not bind the superuser")
    def test_an_unreadable_session_file_fails_the_run_rather_than_the_budget(self) -> None:
        day = self.home / "sessions" / "2026" / "09" / "11"
        day.mkdir(parents=True)
        unreadable = day / "rollout-a.jsonl"
        unreadable.write_text(usage_record("r1", 1, 1), encoding="utf-8")
        unreadable.chmod(0)
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(RuntimeError, str(unreadable)):
                run_with_watcher(["/bin/sleep", "4457"], self.home, {"seconds": 0.5}, self.root, {"PATH": "/usr/bin:/bin"}, poll_interval=0.05)
        finally:
            unreadable.chmod(0o644)
        self.assertLess(time.monotonic() - started, 5, "the run ended with the watcher rather than with the sleep")
        self.assertEqual(subprocess.run(["/usr/bin/pgrep", "-f", "^/bin/sleep 4457$"], capture_output=True, check=False).returncode, 1)

    def test_crossing_the_output_token_limit_stops_the_run_and_its_children(self) -> None:
        plan = [
            ["spawn_sleep", 2971],
            ["usage", "parent", 10, 600, "r1"],
            ["sleep", 0.2],
            ["usage", "child", 10, 600, "r2"],
            ["print", "still running"],
            ["sleep", 30],
        ]
        started = time.monotonic()
        status, stop, stdout, _ = self.run_plan(plan, {"input_tokens": 100000, "output_tokens": 1000, "seconds": 10})
        self.assertIsNone(status)
        self.assertIsInstance(stop, Stop)
        assert stop is not None
        self.assertEqual((stop.dimension, stop.observed, stop.limit), ("output_tokens", 1200, 1000))
        self.assertIn("output_tokens reached 1200 tokens against a limit of 1000 tokens", stop.reason)
        self.assertLess(time.monotonic() - started, 5, "the stop came from the token limit rather than the wall clock")
        self.assertGreater(stop.at_ms, 1_700_000_000_000, "at_ms is on the epoch clock")
        self.assertEqual(stop.to_dict()["dimension"], "output_tokens")
        self.assertEqual(stdout, b"still running\n")
        self.assertEqual(subprocess.run(["/usr/bin/pgrep", "-f", "^/bin/sleep 2971$"], capture_output=True, check=False).returncode, 1, "the spawned sleep died with the group")

    def test_crossing_the_input_token_limit_names_that_dimension(self) -> None:
        plan = [["count", "parent", 700, 1], ["count", "parent", 700, 1], ["sleep", 30]]
        status, stop, _, _ = self.run_plan(plan, {"input_tokens": 1000, "output_tokens": 1000, "seconds": 10})
        self.assertIsNone(status)
        assert stop is not None
        self.assertEqual((stop.dimension, stop.observed), ("input_tokens", 1400))

    def test_exceeding_the_wall_clock_stops_the_run(self) -> None:
        started = time.monotonic()
        status, stop, _, _ = self.run_plan([["sleep", 30]], {"input_tokens": 1000, "seconds": 0.5})
        self.assertIsNone(status)
        assert stop is not None
        self.assertEqual((stop.dimension, stop.limit), ("seconds", 0.5))
        self.assertGreater(stop.observed, 0.5)
        self.assertLess(time.monotonic() - started, 5)

    def test_a_process_that_ignores_sigterm_is_killed_after_the_grace_period(self) -> None:
        started = time.monotonic()
        status, stop, _, _ = self.run_plan([["ignore_term"], ["sleep", 30]], {"seconds": 0.3}, grace_seconds=0.3)
        self.assertIsNone(status)
        assert stop is not None
        self.assertEqual(stop.dimension, "seconds")
        self.assertLess(time.monotonic() - started, 5)

    def test_termination_sends_sigterm_first_and_sigkill_after_the_grace_period(self) -> None:
        plan = [["report_term"], ["print", "ready"], ["sleep", 30]]
        process = subprocess.Popen(self.command(plan), cwd=self.root, env={"PATH": "/usr/bin:/bin", "CODEX_HOME": str(self.home)}, stdout=subprocess.PIPE, start_new_session=True)
        assert process.stdout is not None
        try:
            self.assertEqual(process.stdout.readline(), b"ready\n", "the handler is installed before the watcher acts")
            watcher = Watcher(self.home, {"seconds": 0.001}, process, grace_seconds=0.4)
            time.sleep(0.01)
            terminating = time.monotonic()
            self.assertIsNotNone(watcher.poll())
            self.assertGreaterEqual(time.monotonic() - terminating, 0.4, "SIGKILL waits for the grace period")
            self.assertEqual(process.returncode, -signal.SIGKILL)
            self.assertEqual(process.stdout.read(), b"SIGTERM\n", "SIGTERM reached the process first")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            process.stdout.close()

    def test_the_watcher_polls_a_process_directly(self) -> None:
        plan = [["usage", "parent", 1, 5000, "r1"], ["sleep", 30]]
        process = subprocess.Popen(self.command(plan), cwd=self.root, env={"PATH": "/usr/bin:/bin", "CODEX_HOME": str(self.home)}, start_new_session=True)
        try:
            watcher = Watcher(self.home, {"output_tokens": 100}, process, poll_interval=0.05, grace_seconds=1)
            deadline = time.monotonic() + 10
            while watcher.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertIsNotNone(watcher.stop)
            self.assertEqual(watcher.usage()["output_tokens"], 5000)
            self.assertEqual(process.returncode, -signal.SIGTERM)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


class Module(unittest.TestCase):
    def test_the_module_reads_no_environment_variable(self) -> None:
        source = Path(watcher_module.__file__).read_text(encoding="utf-8")
        for forbidden in ("os.environ", "os.getenv"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
