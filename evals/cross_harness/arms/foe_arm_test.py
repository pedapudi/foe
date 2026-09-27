#!/usr/bin/python3
"""Unit tests for the foe arm: a fake foe script stands in for the binary, and no model is called."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import foe_arm  # noqa: E402

# A stand-in for the binary. It behaves as the task text directs: it prints
# the `foe: log PATH` line, writes an episode log, and prints the outcome
# line the running form specifies, or hangs, or exits without an outcome.
# The hanging form starts two children and handles SIGINT as foe does: one
# child stays in the run's process group, one leads a group of its own as a
# tool process does, and SIGINT ends the latter, closes the log, and prints
# a failed outcome. The lingering form leaves a descendant of its own
# session holding the output pipes after it has exited. Both write the
# children's pids to `children.txt` in the episode directory.
FAKE_FOE = textwrap.dedent(
    """\
    #!/usr/bin/python3
    import json, os, signal, subprocess, sys, time
    args = sys.argv[1:]
    config = json.loads(open(args[args.index("--config") + 1], encoding="utf-8").read())
    log_dir = args[args.index("--log-dir") + 1]
    assert args[args.index("--viewer") + 1] == "off"
    task = config["task"]
    episode = os.path.join(log_dir, "ep_fake")
    os.makedirs(episode)
    with open(os.path.join(episode, "episode.jsonl"), "w", encoding="utf-8") as log:
        log.write(json.dumps({"seq": 0, "time": 0, "version": 3, "type": "episode/start", "data": {"task": task}}) + "\\n")
    print("foe: log " + episode, file=sys.stderr, flush=True)
    print("progress line that is not JSON", flush=True)
    if task.startswith("hang"):
        in_group = subprocess.Popen(["sleep", "300"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        own_group = subprocess.Popen(["sleep", "300"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        with open(os.path.join(episode, "children.txt"), "w", encoding="utf-8") as handle:
            handle.write(f"{in_group.pid} {own_group.pid}\\n")
        def interrupted(signum, frame):
            os.killpg(own_group.pid, signal.SIGTERM)
            own_group.wait()
            with open(os.path.join(episode, "episode.jsonl"), "a", encoding="utf-8") as log:
                log.write(json.dumps({"seq": 1, "time": 1, "type": "episode/end", "data": {"outcome": {"kind": "failed", "error": "interrupted by SIGINT"}}}) + "\\n")
            print(json.dumps({"kind": "failed", "error": "interrupted by SIGINT"}), flush=True)
            sys.exit(1)
        signal.signal(signal.SIGINT, interrupted)
        time.sleep(60)
    if task.startswith("linger"):
        holder = subprocess.Popen(["sleep", "300"], stdin=subprocess.DEVNULL, start_new_session=True)
        with open(os.path.join(episode, "children.txt"), "w", encoding="utf-8") as handle:
            handle.write(f"{holder.pid}\\n")
        print(json.dumps({"kind": "completed", "value": {"summary": "done"}}), flush=True)
        sys.exit(0)
    if task.startswith("silent"):
        print("foe: the document is invalid: key tools is absent", file=sys.stderr)
        sys.exit(1)
    outcomes = {
        "completed": ({"kind": "completed", "value": {"summary": "done", "learned": [{"seq": 4, "claim": "The test passes."}, {"seq": 6, "claim": "The lint is clean."}]}}, 0),
        "blocked": ({"kind": "blocked", "code": "goal-unreachable", "message": "The two requirements conflict."}, 2),
        "exhausted": ({"kind": "exhausted", "limit": "model_calls"}, 3),
        "failed": ({"kind": "failed", "error": "the model endpoint refused the request"}, 1),
    }
    outcome, status = outcomes[task.split()[0]]
    print(json.dumps(outcome), flush=True)
    print("foe: view the episode with foe view " + episode, file=sys.stderr)
    sys.exit(status)
    """
)

ROUTE = foe_arm.ModelRoute("compatible-http", "fixture-model", "http://127.0.0.1:9/v1")


def document() -> dict:
    return {
        "version": 4,
        "name": "arm-test",
        "instructions": {"role": "Do the task in {workspace}."},
        "tools": ["read", "edit", "bash"],
        "grants": {"read": ["{workspace}"], "write": ["{workspace}/src"], "execute": ["/usr/bin"]},
        "budget": {"model_calls": 8, "seconds": 60},
        "sandbox": {"mode": "off"},
    }


class Route(unittest.TestCase):
    def test_reasoning_effort_enters_only_the_providers_that_accept_it(self) -> None:
        self.assertEqual(foe_arm.ModelRoute("openai", "m").block("low"), {"provider": "openai", "model": "m", "reasoning_effort": "low"})
        self.assertEqual(foe_arm.ModelRoute("openai-codex", "m").block("xhigh")["reasoning_effort"], "xhigh")
        self.assertEqual(ROUTE.block("low"), {"provider": "compatible-http", "model": "fixture-model", "base_url": "http://127.0.0.1:9/v1"})
        self.assertNotIn("reasoning_effort", foe_arm.ModelRoute("openai", "m").block(None))

    def test_a_compatible_route_needs_its_endpoint(self) -> None:
        with self.assertRaises(ValueError) as caught:
            foe_arm.ModelRoute("compatible-http", "m")
        self.assertIn("base_url", str(caught.exception))


class Document(unittest.TestCase):
    def spec(self, doc: dict, seconds: int = 60) -> foe_arm.FoeSpec:
        return foe_arm.FoeSpec("foe-test", Path("/bin/true"), doc, "Do it.", Path("/ws"), Path("/log"), Path("/art"), ROUTE, seconds, "low")

    def test_the_placeholder_is_replaced_in_every_string(self) -> None:
        prepared, placement = foe_arm.prepare_document(self.spec(document()))
        self.assertEqual(placement, "placeholder")
        self.assertEqual(prepared["grants"], {"read": ["/ws"], "write": ["/ws/src"], "execute": ["/usr/bin"]})
        self.assertEqual(prepared["instructions"]["role"], "Do the task in /ws.")
        self.assertEqual(prepared["task"], "Do it.")
        self.assertEqual(prepared["model"], ROUTE.block("low"))

    def test_the_callers_document_is_left_unchanged(self) -> None:
        original = document()
        foe_arm.prepare_document(self.spec(original))
        self.assertEqual(original, document())

    def test_a_document_without_the_placeholder_receives_the_workspace_in_read_and_write(self) -> None:
        doc = document()
        doc["instructions"] = {"role": "Do the task."}
        doc["grants"] = {"read": ["/usr/share"], "execute": ["/usr/bin"]}
        prepared, placement = foe_arm.prepare_document(self.spec(doc))
        self.assertEqual(placement, "grants")
        self.assertEqual(prepared["grants"]["read"], ["/ws", "/usr/share"])
        self.assertEqual(prepared["grants"]["write"], ["/ws"])

    def test_a_placeholder_outside_the_grants_is_replaced_and_the_grants_still_receive_the_workspace(self) -> None:
        doc = document()
        doc["grants"] = {"read": ["/usr/share"], "write": ["/other"]}
        prepared, placement = foe_arm.prepare_document(self.spec(doc))
        self.assertEqual(placement, "grants")
        self.assertEqual(prepared["grants"], {"read": ["/ws", "/usr/share"], "write": ["/ws", "/other"]})
        self.assertEqual(prepared["instructions"]["role"], "Do the task in /ws.")

    def test_a_grant_that_is_not_a_list_of_strings_is_refused_by_key(self) -> None:
        for kind, value in (("read", "/usr/share"), ("write", ["/a", 3])):
            doc = document()
            doc["grants"][kind] = value
            with self.assertRaises(ValueError) as caught:
                foe_arm.prepare_document(self.spec(doc))
            self.assertIn(f"grants.{kind}", str(caught.exception))

    def test_a_seconds_budget_above_the_cap_is_refused_by_key(self) -> None:
        with self.assertRaises(ValueError) as caught:
            foe_arm.prepare_document(self.spec(document(), seconds=30))
        self.assertIn("budget.seconds", str(caught.exception))

    def test_a_document_without_grants_is_refused_by_key(self) -> None:
        doc = document()
        del doc["grants"]
        with self.assertRaises(ValueError) as caught:
            foe_arm.prepare_document(self.spec(doc))
        self.assertIn("grants", str(caught.exception))

    def test_the_command_line_names_the_config_the_log_dir_and_no_viewer(self) -> None:
        command = foe_arm.command_line(self.spec(document()), Path("/art/config.json"))
        self.assertEqual(command, ["/bin/true", "--config", "/art/config.json", "--log-dir", "/log", "--viewer", "off"])


class Reading(unittest.TestCase):
    def test_the_outcome_line_is_the_last_json_object_with_a_kind(self) -> None:
        stdout = 'noise\n{"other": 1}\n{"kind": "completed", "value": 3}\ntrailing text\n'
        self.assertEqual(foe_arm.outcome_line(stdout), {"kind": "completed", "value": 3})
        self.assertIsNone(foe_arm.outcome_line("nothing here\n{not json\n"))

    def test_learned_claims_are_read_only_from_a_learned_list(self) -> None:
        self.assertEqual(foe_arm.learned_claims({"learned": [{"seq": 1, "claim": "a"}, {"seq": 2}, "x"]}), ["a"])
        self.assertEqual(foe_arm.learned_claims({"learned": "a"}), [])
        self.assertEqual(foe_arm.learned_claims("text"), [])

    def test_every_outcome_kind_maps_to_a_status_a_code_and_evidence(self) -> None:
        completed, candidate = foe_arm.interpret({"kind": "completed", "value": {"learned": [{"seq": 1, "claim": "a"}]}}, False, 0, "", 90)
        self.assertEqual(completed, {"status": "completed", "code": None, "evidence": ["a"]})
        self.assertEqual(candidate, {"learned": [{"seq": 1, "claim": "a"}]})
        blocked, _ = foe_arm.interpret({"kind": "blocked", "code": "ambiguous-task", "message": "two readings"}, False, 2, "", 90)
        self.assertEqual(blocked, {"status": "blocked", "code": "ambiguous-task", "evidence": ["two readings"]})
        unexplained, _ = foe_arm.interpret({"kind": "blocked", "code": "cancelled"}, False, 2, "", 90)
        self.assertEqual(unexplained, {"status": "blocked", "code": "cancelled", "evidence": []})
        exhausted, _ = foe_arm.interpret({"kind": "exhausted", "limit": "seconds"}, False, 3, "", 90)
        self.assertEqual((exhausted["status"], exhausted["code"]), ("exhausted", "seconds"))
        failed, _ = foe_arm.interpret({"kind": "failed", "error": "boom"}, False, 1, "", 90)
        self.assertEqual(failed, {"status": "failed", "code": None, "evidence": ["boom"]})
        unknown, _ = foe_arm.interpret({"kind": "other"}, False, 0, "", 90)
        self.assertEqual(unknown["status"], "failed")
        self.assertIn("other", unknown["evidence"][0])

    def test_a_terminated_run_is_killed_whatever_it_printed(self) -> None:
        killed, candidate = foe_arm.interpret({"kind": "completed", "value": 1}, True, None, "", 90)
        self.assertEqual(killed["status"], "killed")
        self.assertIsNone(candidate)
        self.assertIn("90", killed["evidence"][0])

    def test_a_run_without_an_outcome_line_failed_with_its_stderr(self) -> None:
        failed, _ = foe_arm.interpret(None, False, 1, "foe: key tools is absent\n", 90)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("exit status 1", failed["evidence"][0])
        self.assertEqual(failed["evidence"][1], "foe: key tools is absent")

    def test_reported_refuses_an_unknown_status(self) -> None:
        with self.assertRaises(ValueError):
            foe_arm.reported("done")


def alive(pid: int) -> bool:
    """Whether a process still runs; an exited process not yet reaped counts as gone."""
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except OSError:
        return False
    for line in status.splitlines():
        if line.startswith("State:"):
            return not line.split()[1].startswith("Z")
    return False


class Running(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="foe-arm-test-")
        self.root = Path(self.temporary.name)
        self.binary = self.root / "fake-foe"
        self.binary.write_text(FAKE_FOE, encoding="utf-8")
        self.binary.chmod(0o755)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.margin = foe_arm.TIMEOUT_MARGIN_SECONDS

    def tearDown(self) -> None:
        foe_arm.TIMEOUT_MARGIN_SECONDS = self.margin
        # A child the fake foe started outlives a failing test; end it so
        # that the test process leaves nothing behind.
        for pid in self.children():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.temporary.cleanup()

    def children(self) -> list[int]:
        listing = self.root / "log" / "ep_fake" / "children.txt"
        if not listing.is_file():
            return []
        return [int(field) for field in listing.read_text(encoding="utf-8").split()]

    def spec(self, task: str, seconds: int = 60) -> foe_arm.FoeSpec:
        doc = document()
        doc["budget"]["seconds"] = min(doc["budget"]["seconds"], seconds)
        return foe_arm.FoeSpec("foe-test", self.binary, doc, task, self.workspace, self.root / "log", self.root / "artifacts", ROUTE, seconds, "low")

    def test_a_completed_run_reports_its_learned_claims_and_returns_the_value(self) -> None:
        result = foe_arm.run(self.spec("completed task"))
        self.assertEqual(result.harness, "foe")
        self.assertEqual(result.arm_name, "foe-test")
        self.assertEqual(result.exit_status, 0)
        self.assertEqual(result.reported, {"status": "completed", "code": None, "evidence": ["The test passes.", "The lint is clean."]})
        self.assertEqual(result.candidate["summary"], "done")
        self.assertLessEqual(result.started_ms, result.ended_ms)
        episode = self.root / "log" / "ep_fake"
        self.assertEqual(result.record["episode_dir"], str(episode))
        self.assertTrue(result.record["episode_log_present"])
        self.assertEqual(result.record["commands"], [[str(self.binary), "--config", str(self.root / "artifacts" / "config.json"), "--log-dir", str(self.root / "log"), "--viewer", "off"]])
        self.assertIsNone(result.record["codex_home"])
        written = json.loads((self.root / "artifacts" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(written["task"], "completed task")
        self.assertEqual(written["grants"]["read"], [str(self.workspace)])
        self.assertIn("progress line", (self.root / "artifacts" / "stdout.txt").read_text(encoding="utf-8"))
        self.assertEqual(result.to_dict()["reported"], result.reported)

    def test_a_blocked_run_reports_the_code_and_the_message(self) -> None:
        result = foe_arm.run(self.spec("blocked task"))
        self.assertEqual(result.exit_status, 2)
        self.assertEqual(result.reported, {"status": "blocked", "code": "goal-unreachable", "evidence": ["The two requirements conflict."]})
        self.assertIsNone(result.candidate)

    def test_an_exhausted_run_reports_the_limit_as_its_code(self) -> None:
        result = foe_arm.run(self.spec("exhausted task"))
        self.assertEqual(result.exit_status, 3)
        self.assertEqual((result.reported["status"], result.reported["code"]), ("exhausted", "model_calls"))

    def test_a_run_past_the_cap_is_terminated_and_reported_killed(self) -> None:
        foe_arm.TIMEOUT_MARGIN_SECONDS = 0
        result = foe_arm.run(self.spec("hang forever", seconds=1))
        self.assertEqual(result.reported["status"], "killed")
        self.assertIsNone(result.exit_status)
        self.assertTrue(result.record["killed"])
        self.assertEqual(result.record["timeout_seconds"], 1)
        # The log line arrived before the hang, so the episode directory is known.
        self.assertEqual(result.record["episode_dir"], str(self.root / "log" / "ep_fake"))
        self.assertLess(result.ended_ms - result.started_ms, 20_000)

    def test_termination_starts_with_sigint_and_reaches_the_children(self) -> None:
        foe_arm.TIMEOUT_MARGIN_SECONDS = 0
        result = foe_arm.run(self.spec("hang forever", seconds=1))
        # SIGINT let the fake foe close the log and print an outcome; the
        # arm still reports the termination.
        self.assertEqual(result.reported["status"], "killed")
        self.assertEqual(result.record["outcome"], {"kind": "failed", "error": "interrupted by SIGINT"})
        events = [json.loads(line) for line in (self.root / "log" / "ep_fake" / "episode.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[-1]["type"], "episode/end")
        # The child that stayed in the group received the signal itself; the
        # child in a group of its own was ended by the fake foe's handler.
        in_group, own_group = self.children()
        self.assertFalse(alive(in_group))
        self.assertFalse(alive(own_group))
        self.assertLess(result.ended_ms - result.started_ms, 20_000)

    def test_a_descendant_holding_the_pipes_does_not_hold_the_arm(self) -> None:
        foe_arm.TIMEOUT_MARGIN_SECONDS = 0
        result = foe_arm.run(self.spec("linger after exit", seconds=5))
        self.assertEqual(result.reported["status"], "completed")
        self.assertEqual(result.exit_status, 0)
        self.assertFalse(result.record["killed"])
        self.assertLess(result.ended_ms - result.started_ms, 20_000)
        (holder,) = self.children()
        self.assertTrue(alive(holder))

    def test_a_run_that_prints_no_outcome_failed_with_its_diagnostic(self) -> None:
        result = foe_arm.run(self.spec("silent task"))
        self.assertEqual(result.exit_status, 1)
        self.assertEqual(result.reported["status"], "failed")
        self.assertTrue(any("key tools is absent" in line for line in result.reported["evidence"]))

    def test_a_missing_binary_or_workspace_is_named(self) -> None:
        spec = self.spec("completed task")
        with self.assertRaises(FileNotFoundError) as caught:
            foe_arm.run(foe_arm.FoeSpec(spec.arm_name, self.root / "absent", spec.document, spec.task, spec.workspace, spec.log_dir, spec.artifacts, ROUTE, 60))
        self.assertIn("absent", str(caught.exception))
        with self.assertRaises(FileNotFoundError) as caught:
            foe_arm.run(foe_arm.FoeSpec(spec.arm_name, self.binary, spec.document, spec.task, self.root / "nowhere", spec.log_dir, spec.artifacts, ROUTE, 60))
        self.assertIn("nowhere", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
