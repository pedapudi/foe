#!/usr/bin/python3
"""Unit tests for the trajectory schema: no binary, no model, no harness."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trajectory as schema  # noqa: E402


def sample() -> schema.Trajectory:
    """A two-agent run that exercises every record type and every optional field."""
    root = schema.Agent(
        id="root",
        parent_id=None,
        depth=0,
        role="root",
        started_ms=1000,
        ended_ms=9000,
        model_calls=[
            schema.ModelCall(seq=3, started_ms=1100, ended_ms=1500, input_tokens=120, output_tokens=40, cache_read_tokens=100, reasoning_tokens=None),
            schema.ModelCall(seq=9, started_ms=6000, ended_ms=6400, input_tokens=300, output_tokens=60, cache_read_tokens=0, reasoning_tokens=12),
        ],
        commands=[
            schema.Command.from_text(1600, 1700, "cat /etc/hostname > notes/host.txt", 0),
            schema.Command.from_text(1800, None, "rm -rf /home/u/outside/secret.txt", 1, denial=True),
        ],
        file_changes=[schema.FileChange(path="/w/src/a.py", kind="edit", at_ms=2000, via="tool")],
        compactions=[schema.Compaction(at_ms=5000, tokens_before=45000)],
        tool_calls=[
            schema.ToolCall(name="bash", started_ms=1600, ended_ms=1700, is_error=False, arguments_digest=schema.arguments_digest({"command": "cat /etc/hostname"}), summary=None),
            schema.ToolCall(name="check", started_ms=7000, ended_ms=None, is_error=False, arguments_digest=None, summary=None),
        ],
    )
    worker = schema.Agent(
        id="child-1",
        parent_id="root",
        depth=1,
        role="node:implement",
        started_ms=2500,
        ended_ms=None,
        model_calls=[schema.ModelCall(seq=None, started_ms=2600, ended_ms=None, input_tokens=50, output_tokens=5, cache_read_tokens=None, reasoning_tokens=None)],
        commands=[],
        file_changes=[schema.FileChange(path="/w/src/b.py", kind="create", at_ms=2700, via="tool")],
        compactions=[],
        tool_calls=[schema.ToolCall(name="edit", started_ms=2650, ended_ms=2700, is_error=True, arguments_digest=schema.arguments_digest({"path": "/w/src/b.py"}), summary="/w/src/b.py")],
    )
    return schema.Trajectory(
        harness="foe",
        identity={"build": "99aa0930", "config_digest": "sha256:abc", "model": "m", "reasoning_effort": "high", "route": "compatible"},
        agents=[root, worker],
        outcome=schema.Outcome(status="completed", code=None, value={"summary": "done", "files": ["/w/src/a.py"]}, ended_ms=9000),
        route="compatible",
    )


class RoundTrip(unittest.TestCase):
    def test_every_field_survives_json_and_from_dict(self) -> None:
        original = sample()
        text = json.dumps(original.to_dict(), sort_keys=True)
        restored = schema.Trajectory.from_dict(json.loads(text))
        self.assertEqual(restored, original)
        self.assertEqual(json.dumps(restored.to_dict(), sort_keys=True), text)

    def test_to_dict_holds_plain_json_values_only(self) -> None:
        def walk(value: object) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    self.assertIsInstance(key, str)
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
            else:
                self.assertIsInstance(value, (str, int, float, bool, type(None)), repr(value))

        walk(sample().to_dict())

    def test_to_dict_copies_lists_so_the_record_is_not_shared(self) -> None:
        original = sample()
        data = original.to_dict()
        data["agents"][0]["commands"][0]["paths_named"].append("/elsewhere")
        data["identity"]["model"] = "other"
        self.assertNotIn("/elsewhere", original.agents[0].commands[0].paths_named)
        self.assertEqual(original.identity["model"], "m")

    def test_a_blocked_outcome_keeps_its_code_and_an_exhausted_one_its_limit(self) -> None:
        for outcome in (
            schema.Outcome(status="blocked", code="grant.write", value=None, ended_ms=5),
            schema.Outcome(status="exhausted", code="model_calls", value=None, ended_ms=None),
            schema.Outcome(status="killed"),
        ):
            self.assertEqual(schema.Outcome.from_dict(outcome.to_dict()), outcome)


class Validation(unittest.TestCase):
    def test_an_enumerated_field_outside_its_choices_names_the_key(self) -> None:
        data = sample().to_dict()
        data["agents"][0]["file_changes"][0]["kind"] = "rename"
        with self.assertRaises(ValueError) as caught:
            schema.Trajectory.from_dict(data)
        self.assertIn("trajectory.agents[0].file_changes[0].kind", str(caught.exception))
        self.assertIn("edit, create, delete, unknown", str(caught.exception))

    def test_the_harness_route_via_and_status_are_checked(self) -> None:
        cases = [
            (("harness",), "other", "foe, codex"),
            (("route",), "direct", "subscription, compatible"),
            (("outcome", "status"), "done", "completed, blocked, exhausted, failed, killed"),
            (("agents", 1, "file_changes", 0, "via"), "guess", "tool, shell"),
        ]
        for path, value, choices in cases:
            data = sample().to_dict()
            target = data
            for step in path[:-1]:
                target = target[step]
            target[path[-1]] = value
            with self.assertRaises(ValueError, msg=path) as caught:
                schema.Trajectory.from_dict(data)
            self.assertIn(choices, str(caught.exception))
            self.assertIn(str(path[-1]), str(caught.exception))

    def test_a_missing_required_key_is_named(self) -> None:
        data = sample().to_dict()
        del data["agents"][1]["started_ms"]
        with self.assertRaises(ValueError) as caught:
            schema.Trajectory.from_dict(data)
        self.assertEqual(str(caught.exception), "trajectory.agents[1].started_ms: the key is required")

    def test_a_count_that_is_not_an_integer_is_refused(self) -> None:
        data = sample().to_dict()
        data["agents"][0]["model_calls"][0]["input_tokens"] = "120"
        with self.assertRaises(ValueError) as caught:
            schema.Trajectory.from_dict(data)
        self.assertIn("trajectory.agents[0].model_calls[0].input_tokens", str(caught.exception))
        data = sample().to_dict()
        data["agents"][0]["depth"] = True
        with self.assertRaises(ValueError):
            schema.Trajectory.from_dict(data)

    def test_two_agents_with_one_id_are_refused(self) -> None:
        data = sample().to_dict()
        data["agents"][1]["id"] = "root"
        with self.assertRaises(ValueError) as caught:
            schema.Trajectory.from_dict(data)
        self.assertIn("'root'", str(caught.exception))

    def test_absent_lists_read_as_empty(self) -> None:
        agent = schema.Agent.from_dict({"id": "a", "parent_id": None, "depth": 0, "role": "root", "started_ms": 0, "ended_ms": None})
        self.assertEqual((agent.model_calls, agent.commands, agent.file_changes, agent.compactions, agent.tool_calls), ([], [], [], [], []))

    def test_a_record_written_before_tool_calls_existed_reads_as_no_tool_calls(self) -> None:
        older = sample().to_dict()
        for agent in older["agents"]:
            del agent["tool_calls"]
        restored = schema.Trajectory.from_dict(older)
        self.assertEqual([agent.tool_calls for agent in restored.agents], [[], []])
        totals = restored.totals()
        self.assertEqual((totals["tool_calls"], totals["tool_calls_by_name"]), (0, {}))
        self.assertEqual(totals["commands"], 2, "the rest of the older record is read as before")

    def test_a_tool_call_field_of_the_wrong_type_names_the_key(self) -> None:
        for key, value in (("arguments_digest", 7), ("summary", []), ("is_error", "yes"), ("name", None), ("started_ms", "7000"), ("started_ms", True), ("ended_ms", "8000")):
            data = sample().to_dict()
            data["agents"][0]["tool_calls"][1][key] = value
            with self.assertRaises(ValueError, msg=key) as caught:
                schema.Trajectory.from_dict(data)
            self.assertIn(f"trajectory.agents[0].tool_calls[1].{key}", str(caught.exception))

    def test_a_tool_call_without_a_start_is_refused_and_one_without_a_digest_is_read(self) -> None:
        data = sample().to_dict()
        del data["agents"][0]["tool_calls"][1]["started_ms"]
        with self.assertRaises(ValueError) as caught:
            schema.Trajectory.from_dict(data)
        self.assertEqual(str(caught.exception), "trajectory.agents[0].tool_calls[1].started_ms: the key is required")
        call = schema.ToolCall.from_dict({"name": "read", "started_ms": 10, "ended_ms": None})
        self.assertEqual((call.arguments_digest, call.summary, call.is_error), (None, None, False), "a record that states no arguments carries no digest")

    def test_lookup_by_id_names_a_missing_agent(self) -> None:
        run = sample()
        self.assertIs(run.agent("child-1"), run.agents[1])
        with self.assertRaises(KeyError) as caught:
            run.agent("nobody")
        self.assertIn("'nobody'", str(caught.exception))


class Totals(unittest.TestCase):
    def test_the_counts_fold_the_whole_tree(self) -> None:
        totals = sample().totals()
        self.assertEqual(totals["model_calls"], 3)
        self.assertEqual(totals["responses_with_usage"], 3)
        self.assertEqual(totals["input_tokens"], 470)
        self.assertEqual(totals["output_tokens"], 105)
        self.assertIsNone(totals["cache_read_tokens"], "the child's call reported no cache count")
        self.assertIsNone(totals["reasoning_tokens"])
        self.assertEqual(totals["agents"], 2)
        self.assertEqual(totals["max_depth"], 1)
        self.assertEqual(totals["wall_ms"], 8000)
        self.assertEqual(totals["commands"], 2)
        self.assertEqual(totals["denials"], 1)
        self.assertEqual(totals["file_changes"], 2)
        self.assertEqual(totals["compactions"], 1)
        self.assertEqual(totals["tool_calls"], 3)
        self.assertEqual(totals["tool_calls_by_name"], {"bash": 1, "check": 1, "edit": 1})
        self.assertEqual(list(totals["tool_calls_by_name"]), ["bash", "check", "edit"], "the counts are ordered by name")

    def test_a_call_without_usage_makes_the_token_totals_none(self) -> None:
        run = sample()
        run.agents[1].model_calls[0].input_tokens = None
        totals = run.totals()
        self.assertEqual(totals["model_calls"], 3)
        self.assertEqual(totals["responses_with_usage"], 2)
        self.assertIsNone(totals["input_tokens"])
        self.assertEqual(totals["output_tokens"], 105)

    def test_wall_time_ends_at_the_latest_agent_or_outcome_end(self) -> None:
        run = sample()
        run.agents[1].ended_ms = 12000
        self.assertEqual(run.totals()["wall_ms"], 11000)
        run.agents[1].ended_ms = None
        run.agents[0].ended_ms = None
        run.outcome.ended_ms = None
        self.assertIsNone(run.totals()["wall_ms"])

    def test_an_empty_run_totals_to_zero(self) -> None:
        empty = schema.Trajectory("codex", {}, [], schema.Outcome("failed"), "subscription")
        totals = empty.totals()
        self.assertEqual(totals["model_calls"], 0)
        self.assertEqual(totals["input_tokens"], 0)
        self.assertEqual(totals["max_depth"], 0)
        self.assertIsNone(totals["wall_ms"])
        self.assertEqual(json.loads(json.dumps(totals)), totals)


class ArgumentsDigest(unittest.TestCase):
    def test_equal_arguments_share_a_digest_whatever_the_key_order(self) -> None:
        first = schema.arguments_digest({"path": "/w/a.py", "edits": [{"old_text": "x", "new_text": "y"}]})
        second = schema.arguments_digest({"edits": [{"new_text": "y", "old_text": "x"}], "path": "/w/a.py"})
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("sha256:"))
        self.assertEqual(len(first), len("sha256:") + 64)

    def test_different_arguments_differ_and_no_argument_content_appears(self) -> None:
        secret = schema.arguments_digest({"path": "/home/u/outside/secret.txt"})
        self.assertNotEqual(secret, schema.arguments_digest({"path": "/w/a.py"}))
        self.assertNotIn("secret", secret)
        self.assertNotEqual(schema.arguments_digest({}), schema.arguments_digest([]))

    def test_a_value_that_is_not_an_object_also_digests(self) -> None:
        self.assertNotEqual(schema.arguments_digest(["bash", "-lc", "make"]), schema.arguments_digest(None))


class PathsInCommand(unittest.TestCase):
    def test_absolute_and_relative_paths_are_found_in_order_once_each(self) -> None:
        text = "cp /w/src/a.py ./build/a.py && cat ../notes/x.md /w/src/a.py > out/log.txt; ls ~/bin"
        self.assertEqual(schema.paths_in_command(text), ["/w/src/a.py", "./build/a.py", "../notes/x.md", "out/log.txt", "~/bin"])

    def test_bare_words_urls_and_variables_are_not_paths(self) -> None:
        self.assertEqual(schema.paths_in_command("ls -la README.md"), [])
        self.assertEqual(schema.paths_in_command("curl https://example.com/a/b"), [])
        self.assertEqual(schema.paths_in_command("cd $HOME/work"), [])
        self.assertEqual(schema.paths_in_command("echo a/b/c"), ["a/b/c"])

    def test_redirections_assignments_and_sentence_ends_are_handled(self) -> None:
        self.assertEqual(schema.paths_in_command("make 2>/dev/null --out=/tmp/x"), ["/dev/null", "/tmp/x"])
        self.assertEqual(schema.paths_in_command("Wrote /w/src/a.py."), ["/w/src/a.py"])
        self.assertEqual(schema.paths_in_command("ls ../"), ["../"])
        self.assertEqual(schema.paths_in_command('grep -r "x" "/w/src" \'./tests\''), ["/w/src", "./tests"])

    def test_from_text_fills_the_named_paths(self) -> None:
        command = schema.Command.from_text(1, 2, "python3 evals/run.py --out /tmp/o", 0)
        self.assertEqual(command.paths_named, ["evals/run.py", "/tmp/o"])
        self.assertFalse(command.denial)


class ShellWriteAttribution(unittest.TestCase):
    def run_with_commands(self) -> schema.Trajectory:
        root = schema.Agent("root", None, 0, "root", 0, 10000, commands=[schema.Command.from_text(1000, 2000, "make", 0)])
        worker = schema.Agent("w", "root", 1, "worker", 500, 10000, commands=[schema.Command.from_text(1500, 3000, "make test", 0)])
        return schema.Trajectory("foe", {}, [root, worker], schema.Outcome("completed", ended_ms=10000), "compatible")

    def test_a_file_written_inside_one_interval_goes_to_that_agent(self) -> None:
        run = self.run_with_commands()
        before = {"/w/a": 100, "/w/b": 100}
        after = {"/w/a": 100, "/w/b": 1200, "/w/c": 2500}
        self.assertEqual(schema.attribute_shell_writes(run, before, after), 2)
        self.assertEqual(run.agents[0].file_changes, [schema.FileChange("/w/b", "edit", 1200, "shell")])
        self.assertEqual(run.agents[1].file_changes, [schema.FileChange("/w/c", "create", 2500, "shell")])

    def test_a_file_inside_two_overlapping_intervals_goes_to_both(self) -> None:
        run = self.run_with_commands()
        self.assertEqual(schema.attribute_shell_writes(run, {}, {"/w/x": 1800}), 2)
        self.assertEqual([agent.file_changes[0].path for agent in run.agents], ["/w/x", "/w/x"])

    def test_a_file_outside_every_interval_is_left_alone(self) -> None:
        run = self.run_with_commands()
        self.assertEqual(schema.attribute_shell_writes(run, {"/w/gone": 1}, {"/w/late": 5000, "/w/early": 10}), 0)
        self.assertEqual(sum(len(agent.file_changes) for agent in run.agents), 0)

    def test_an_open_command_runs_to_its_agent_end_or_without_bound(self) -> None:
        run = self.run_with_commands()
        run.agents[0].commands[0].ended_ms = None
        self.assertEqual(schema.attribute_shell_writes(run, {}, {"/w/x": 9000, "/w/y": 11000}), 1)
        self.assertEqual(run.agents[0].file_changes[0].path, "/w/x")
        run = self.run_with_commands()
        run.agents[0].commands[0].ended_ms = None
        run.agents[0].ended_ms = None
        self.assertEqual(schema.attribute_shell_writes(run, {}, {"/w/y": 11000}), 1)

    def test_one_agent_records_a_file_once_across_its_own_overlapping_commands(self) -> None:
        run = self.run_with_commands()
        run.agents[0].commands.append(schema.Command.from_text(1100, 1900, "make again", 0))
        self.assertEqual(schema.attribute_shell_writes(run, {}, {"/w/x": 1200}), 1)
        self.assertEqual(len(run.agents[0].file_changes), 1)

    def test_attributed_changes_round_trip_with_the_rest(self) -> None:
        run = self.run_with_commands()
        schema.attribute_shell_writes(run, {}, {"/w/x": 1800})
        self.assertEqual(schema.Trajectory.from_dict(json.loads(json.dumps(run.to_dict()))), run)




MARKER = "checks/run.sh step 2 waiting on socket"
TASK = {"wait_marker": MARKER, "mechanism": "socket"}


def invocation(output: str | None, source: str = "ep_root seq 9, check call") -> dict[str, str | None]:
    return {"source": source, "output": output}


class WaitEntry(unittest.TestCase):
    """docs/evaluation.md gate "Mechanism exercised": an attempt entered the wait only when the marker line its task's suite prints before the wait is in the output the harness recorded for an invocation of the suite."""

    def test_the_names_it_restates_are_the_ones_the_task_tree_uses(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "tasks"))
        import constructions

        self.assertEqual(schema.WAIT_MARKER, constructions.WAIT_MARKER)
        self.assertEqual(schema.RUN_SCRIPT, constructions.RUN_SCRIPT.as_posix())
        self.assertEqual(constructions.wait_marker("socket"), MARKER)

    def test_an_invocation_runs_the_suite_and_a_mention_does_not(self) -> None:
        invocations = [
            "checks/run.sh",
            "./checks/run.sh",
            "/usr/bin/bash checks/run.sh",
            "cargo fmt --all && checks/run.sh",
            "CARGO_TARGET_DIR=.check-tmp ./checks/run.sh",
            "set +e\n/usr/bin/timeout --kill-after=1s 3s /usr/bin/bash checks/run.sh\nstatus=$?",
            "bash -lc 'checks/run.sh'",
            "/w/checks/run.sh 2>&1 | tail -n 40",
        ]
        for text in invocations:
            self.assertTrue(schema.invokes_run_script(text), text)
        mentions = ["sed -n '1,200p' checks/run.sh", "git diff -- checks/run.sh", "cat checks/run.sh checks/wait_for_lock.py", "ls -l checks/run.sh", "checks/run.sh.bak"]
        for text in mentions:
            self.assertFalse(schema.invokes_run_script(text), text)

    def test_the_marker_in_a_recorded_output_establishes_entry(self) -> None:
        output = f"step 1: line ceilings\r\n{MARKER}\r\n^C"
        entered = schema.wait_entry([invocation(""), invocation(output, "session s.jsonl item exec-1")], TASK)
        self.assertEqual((entered["entered"], entered["status"]), (True, schema.ENTERED))
        self.assertEqual(entered["evidence"][1], f"session s.jsonl item exec-1: the recorded output holds {MARKER!r}")

    def test_a_long_killed_invocation_without_the_marker_is_not_established(self) -> None:
        """Duration and exit status establish nothing: a check killed after two minutes whose recorded output is empty did not show the wait."""
        entered = schema.wait_entry([invocation(""), invocation("step 1: line ceilings\nstep 2: wait for the reply of the loopback service")], TASK)
        self.assertEqual((entered["entered"], entered["status"]), (False, schema.NOT_ESTABLISHED))
        self.assertTrue(all("does not hold" in line for line in entered["evidence"]), entered["evidence"])

    def test_the_marker_inside_a_line_of_the_script_source_does_not_count(self) -> None:
        source = f'echo "step 1"\necho "{MARKER}" >&2\nexec python3 checks/wait_for_reply.py inf'
        self.assertFalse(schema.wait_entry([invocation(source)], TASK)["entered"])

    def test_no_recorded_output_and_no_invocation_are_not_established(self) -> None:
        unrecorded = schema.wait_entry([invocation(None)], TASK)
        self.assertEqual(unrecorded["status"], schema.NOT_ESTABLISHED)
        self.assertEqual(unrecorded["evidence"], ["ep_root seq 9, check call: the harness recorded no output"])
        nothing = schema.wait_entry([], TASK)
        self.assertEqual(nothing, {"entered": False, "status": schema.NOT_ESTABLISHED, "evidence": ["no harness record holds an invocation of checks/run.sh"]})

    def test_a_task_that_states_no_marker_is_not_established(self) -> None:
        """A task whose metadata states no wait_marker names no line to find, so even the line its mechanism would give establishes nothing."""
        entered = schema.wait_entry([invocation(MARKER)], {"mechanism": "socket"})
        self.assertEqual((entered["entered"], entered["status"]), (False, schema.NOT_ESTABLISHED))
        self.assertEqual(entered["evidence"][0], "the task states no wait_marker, so no recorded output can show the wait")
        self.assertIn("recorded 38 characters of output", entered["evidence"][1])

    def test_the_marker_may_be_given_as_the_line_itself(self) -> None:
        pipe = "checks/run.sh step 2 waiting on pipe"
        self.assertTrue(schema.wait_entry([invocation(pipe)], pipe)["entered"])
        self.assertFalse(schema.wait_entry([invocation(MARKER)], pipe)["entered"])

if __name__ == "__main__":
    unittest.main()
