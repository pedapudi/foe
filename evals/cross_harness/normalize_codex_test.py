#!/usr/bin/python3
"""Unit tests for the Codex session normalizer: fixtures only, no codex binary, no model."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import normalize_codex as normalizer  # noqa: E402
import trajectory  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "codex"
SINGLE_REPLY = FIXTURES / "single_reply"
SINGLE_REPLY_ROLLOUT = SINGLE_REPLY / "home" / "sessions" / "2026" / "09" / "11" / "rollout-2026-09-11T09-13-09-01a0913e-868c-7903-bd92-5813cb46712c.jsonl"

# A fixed clock for the synthetic sessions: epoch milliseconds and the
# matching ISO 8601 text the session records carry.
BASE_MS = 1789143189000

# The route every synthetic run is normalized under; the runner supplies
# it, and the normalizer records it unchanged.
ROUTE = "subscription"


def iso(ms: int) -> str:
    seconds, millis = divmod(ms, 1000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{millis:03d}Z"


def record(ms: int, ordinal: int, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"timestamp": iso(ms), "ordinal": ordinal, "type": kind, "payload": payload}


def usage(input_tokens: int, output_tokens: int, cached: int = 0, reasoning: int = 0) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": 0,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning,
        "total_tokens": input_tokens + output_tokens,
    }


def meta(thread_id: str, ms: int, parent: str | None = None, depth: int = 0, agent_path: str | None = None, provider: str = "test-provider") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": thread_id,
        "id": thread_id,
        "timestamp": iso(ms),
        "cwd": "/w",
        "originator": "codex_exec",
        "cli_version": "0.153.4",
        "source": "exec",
        "model_provider": provider,
    }
    if parent is not None:
        payload["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": depth, "agent_path": agent_path, "agent_nickname": None, "agent_role": None}}}
    return record(ms, 0, "session_meta", payload)


def turn_context(ms: int, ordinal: int, model: str = "test-model", effort: str = "low", sandbox: str = "read-only") -> dict[str, Any]:
    return record(
        ms,
        ordinal,
        "turn_context",
        {
            "turn_id": "turn-1",
            "cwd": "/w",
            "approval_policy": "never",
            "sandbox_policy": {"type": sandbox},
            "model": model,
            "collaboration_mode": {"mode": "default", "settings": {"model": model, "reasoning_effort": effort}},
            "effort": effort,
        },
    )


def item_completed(started: int, ended: int, ordinal: int, item: dict[str, Any], thread_id: str = "root") -> dict[str, Any]:
    return record(ended, ordinal, "event_msg", {"type": "item_completed", "thread_id": thread_id, "turn_id": "turn-1", "item": item, "started_at_ms": started, "completed_at_ms": ended})


def command_item(command: list[str], exit_code: int, output: str, identifier: str = "exec-1") -> dict[str, Any]:
    """A completed command whose `stdout` differs from its `aggregated_output`, so a reader of the wrong field is caught."""
    return {"type": "CommandExecution", "id": identifier, "command": command, "cwd": "file:///w", "status": "completed" if exit_code == 0 else "failed", "stdout": "stdout only\n", "stderr": "", "aggregated_output": output, "exit_code": exit_code}


def write_session(home: Path, day: str, name: str, records: list[dict[str, Any]]) -> Path:
    directory = home / "sessions" / day
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"rollout-{name}.jsonl"
    path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    return path


def write_events(path: Path, thread_id: str, events: list[dict[str, Any]]) -> Path:
    lines = [{"type": "thread.started", "thread_id": thread_id}, {"type": "turn.started"}, *events]
    path.write_text("\n".join(json.dumps(event) for event in lines) + "\n", encoding="utf-8")
    return path


def fixture_model() -> str:
    """The model the fixture's own turn_context names, so no test states a model identifier."""
    for line in SINGLE_REPLY_ROLLOUT.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry["type"] == "turn_context":
            return entry["payload"]["model"]
    raise AssertionError(f"{SINGLE_REPLY_ROLLOUT}: no turn_context record")


class RealFixture(unittest.TestCase):
    """The recorded run of `codex exec` asked to reply with one word."""

    def test_the_single_reply_run_is_one_agent_with_one_model_call(self) -> None:
        result = normalizer.normalize(SINGLE_REPLY / "home", SINGLE_REPLY / "events.jsonl", SINGLE_REPLY / "last.txt", 0, ROUTE)
        self.assertEqual(result.harness, "codex")
        self.assertEqual(result.route, ROUTE)
        self.assertEqual(len(result.agents), 1)
        root = result.agents[0]
        self.assertEqual((root.id, root.parent_id, root.depth, root.role), ("01a0913e-868c-7903-bd92-5813cb46712c", None, 0, "root"))
        self.assertEqual(root.started_ms, normalizer.timestamp_ms("2026-09-11T16:13:09.148Z", "test"))
        self.assertEqual(root.ended_ms, normalizer.timestamp_ms("2026-09-11T16:13:13.279Z", "test"))
        self.assertEqual(len(root.model_calls), 1)
        call = root.model_calls[0]
        self.assertEqual((call.seq, call.input_tokens, call.output_tokens, call.cache_read_tokens, call.reasoning_tokens), (12, 14660, 5, 11136, 0))
        self.assertEqual(call.started_ms, root.started_ms)
        self.assertEqual(call.ended_ms, normalizer.timestamp_ms("2026-09-11T16:13:13.276Z", "test"))
        self.assertEqual((root.commands, root.file_changes, root.compactions, root.tool_calls), ([], [], [], []))
        self.assertEqual(result.outcome, trajectory.Outcome("completed", value="ok", ended_ms=root.ended_ms))

    def test_the_identity_names_the_version_model_effort_and_sandbox(self) -> None:
        result = normalizer.normalize(SINGLE_REPLY / "home", SINGLE_REPLY / "events.jsonl", SINGLE_REPLY / "last.txt", 0, ROUTE)
        identity = result.identity
        self.assertEqual(identity["cli_version"], "0.153.4")
        self.assertEqual(identity["model"], fixture_model())
        self.assertEqual(identity["reasoning_effort"], "low")
        self.assertEqual(identity["sandbox_policy"], "read-only")
        self.assertEqual(identity["approval_policy"], "never")
        self.assertEqual(identity["thread_id"], "01a0913e-868c-7903-bd92-5813cb46712c")
        self.assertEqual(identity["codex_home"], str(SINGLE_REPLY / "home"))
        self.assertEqual(identity["session_files"], [str(SINGLE_REPLY_ROLLOUT.relative_to(SINGLE_REPLY / "home"))])
        self.assertEqual(identity["truncated_session_files"], [])
        self.assertEqual(identity["turns_completed"], 1)
        self.assertEqual(identity["event_problems"], [])

    def test_the_trajectory_round_trips_through_json_and_totals(self) -> None:
        result = normalizer.normalize(SINGLE_REPLY / "home", SINGLE_REPLY / "events.jsonl", SINGLE_REPLY / "last.txt", 0, ROUTE)
        again = trajectory.Trajectory.from_dict(json.loads(json.dumps(result.to_dict())))
        self.assertEqual(again, result)
        totals = result.totals()
        self.assertEqual((totals["model_calls"], totals["responses_with_usage"], totals["input_tokens"], totals["output_tokens"]), (1, 1, 14660, 5))
        self.assertEqual(totals["wall_ms"], 4131)
        self.assertEqual((totals["agents"], totals["max_depth"], totals["commands"], totals["denials"]), (1, 0, 0, 0))
        self.assertEqual((totals["tool_calls"], totals["tool_calls_by_name"]), (0, {}), "the recorded run called no tool")

    def test_the_fixture_carries_no_credential_or_account_detail(self) -> None:
        # A plugin locator of the form `name@marketplace` has no dotted domain
        # after the `@`, so the pattern matches an email address only.
        email = re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}")
        for path in SINGLE_REPLY.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                for key in ("id_token", "access_token", "refresh_token", "account_id"):
                    self.assertNotIn(key, text, f"{path} names {key}")
                self.assertIsNone(email.search(text), f"{path} carries an email address")
                self.assertNotIn("/home/", text.replace("/codex-fixture/home/", ""), f"{path} carries a user's home directory")
                self.assertNotIn('"plan_type":"', text, f"{path} carries the account's plan type")
                self.assertNotIn("America/", text, f"{path} carries the developer's timezone")


class Timestamps(unittest.TestCase):
    def test_iso_timestamps_become_epoch_milliseconds(self) -> None:
        self.assertEqual(normalizer.timestamp_ms("1970-01-01T00:00:01.500Z", "t"), 1500)
        self.assertEqual(normalizer.timestamp_ms("2026-09-11T16:13:09.148Z", "t"), 1789143189148)
        self.assertEqual(normalizer.timestamp_ms("2026-09-11T16:13:09.148+00:00", "t"), 1789143189148)

    def test_a_bad_timestamp_names_where_it_was_read(self) -> None:
        with self.assertRaises(ValueError) as caught:
            normalizer.timestamp_ms("yesterday", "/s.jsonl:3 timestamp")
        self.assertIn("/s.jsonl:3 timestamp", str(caught.exception))
        with self.assertRaises(ValueError):
            normalizer.timestamp_ms(None, "t")


class CommandText(unittest.TestCase):
    def test_a_shell_wrapper_yields_its_script_and_other_vectors_are_joined(self) -> None:
        self.assertEqual(normalizer.command_text(["/bin/bash", "-lc", "cat a/b.txt | wc"]), "cat a/b.txt | wc")
        self.assertEqual(normalizer.command_text(["/bin/sh", "-c", "ls"]), "ls")
        self.assertEqual(normalizer.command_text(["cat", "a b.txt"]), "cat 'a b.txt'")
        self.assertEqual(normalizer.command_text("ls -l"), "ls -l")


class SyntheticRuns(unittest.TestCase):
    """Sessions of the documented shape covering children, commands, changes, and compactions."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.events = Path(self.tmp.name) / "events.jsonl"
        self.last = Path(self.tmp.name) / "last.txt"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def team_run(self) -> None:
        t = BASE_MS
        root = [
            meta("root", t),
            record(t, 1, "event_msg", {"type": "task_started", "turn_id": "turn-1"}),
            turn_context(t + 10, 2),
            record(t + 2000, 3, "token_usage_record", {"thread_id": "root", "response_id": "r1", "usage": usage(100, 10, cached=40, reasoning=3)}),
            item_completed(t + 2100, t + 2600, 4, command_item(["/bin/bash", "-lc", "cat /w/src/a.py && echo x > /outside/f"], 1, "cat ok\nbash: /outside/f: Permission denied\n")),
            item_completed(t + 2700, t + 2800, 5, {"type": "SubAgentActivity", "id": "call-1", "kind": "started", "agent_thread_id": "child", "agent_path": "/root/worker_a"}),
            record(t + 5000, 6, "token_usage_record", {"thread_id": "root", "response_id": "r2", "usage": usage(200, 20)}),
            item_completed(t + 5100, t + 5300, 7, {"type": "FileChange", "id": "fc-1", "status": "completed", "changes": {"/w/src/a.py": {"type": "update"}, "/w/src/b.py": {"type": "add"}, "/w/old.txt": {"type": "delete"}, "/w/odd": {"type": "rename"}}}),
            item_completed(t + 5400, t + 5500, 8, {"type": "FileChange", "id": "fc-2", "status": "failed", "changes": {"/w/src/c.py": {"type": "add"}}}),
            # The CLI writes `latest_token_usage_record` as null, so the
            # context size comes from the usage record before the compaction.
            record(t + 6000, 9, "compacted", {"message": "", "latest_token_usage_record": None, "replacement_history": []}),
            record(t + 7000, 10, "token_usage_record", {"thread_id": "root", "response_id": "r3", "usage": usage(50, 5)}),
            record(t + 7100, 11, "event_msg", {"type": "task_complete", "turn_id": "turn-1", "last_agent_message": "done", "duration_ms": 7100}),
        ]
        child = [
            meta("child", t + 2800, parent="root", depth=1, agent_path="/root/worker_a"),
            turn_context(t + 2810, 1),
            item_completed(t + 3000, t + 3200, 2, command_item(["/bin/bash", "-lc", "python3 ./build.py"], 0, "built\n", "exec-c"), thread_id="child"),
            record(t + 4000, 3, "token_usage_record", {"thread_id": "child", "response_id": "c1", "usage": usage(300, 30)}),
        ]
        grandchild = [
            meta("grandchild", t + 3300, parent="child", depth=2, agent_path="/root/worker_a/helper"),
            record(t + 3500, 1, "token_usage_record", {"thread_id": "grandchild", "response_id": "g1", "usage": usage(10, 1)}),
        ]
        stranger = [meta("stranger", t + 100), record(t + 200, 1, "token_usage_record", {"thread_id": "stranger", "response_id": "s1", "usage": usage(1, 1)})]
        write_session(self.home, "2026/09/11", "a-root", root)
        write_session(self.home, "2026/09/11", "b-child", child)
        write_session(self.home, "2026/09/11", "c-grandchild", grandchild)
        write_session(self.home, "2026/09/11", "d-stranger", stranger)
        write_events(self.events, "root", [{"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "done"}}, {"type": "turn.completed", "usage": usage(350, 35)}])
        self.last.write_text("done\n", encoding="utf-8")

    def single_run(self, records: list[dict[str, Any]], name: str = "root") -> None:
        write_session(self.home, "2026/09/11", name, [meta("root", BASE_MS), *records])
        write_events(self.events, "root", [{"type": "turn.completed", "usage": usage(1, 1)}])
        self.last.write_text("ok", encoding="utf-8")

    def test_the_root_and_its_descendants_form_the_agent_tree_and_a_stranger_is_left_out(self) -> None:
        self.team_run()
        result = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        self.assertEqual([(a.id, a.parent_id, a.depth, a.role) for a in result.agents], [("root", None, 0, "root"), ("child", "root", 1, "worker_a"), ("grandchild", "child", 2, "helper")])
        self.assertEqual(result.identity["session_files"], ["sessions/2026/09/11/rollout-a-root.jsonl", "sessions/2026/09/11/rollout-b-child.jsonl", "sessions/2026/09/11/rollout-c-grandchild.jsonl"])
        self.assertEqual(result.totals()["agents"], 3)
        self.assertEqual(result.totals()["max_depth"], 2)

    def test_model_calls_span_from_the_previous_call_to_their_own_record(self) -> None:
        self.team_run()
        root = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).agent("root")
        self.assertEqual([(c.seq, c.started_ms, c.ended_ms) for c in root.model_calls], [(3, BASE_MS, BASE_MS + 2000), (6, BASE_MS + 2000, BASE_MS + 5000), (10, BASE_MS + 5000, BASE_MS + 7000)])
        self.assertEqual((root.model_calls[0].input_tokens, root.model_calls[0].output_tokens, root.model_calls[0].cache_read_tokens, root.model_calls[0].reasoning_tokens), (100, 10, 40, 3))
        self.assertTrue(all(call.has_usage for call in root.model_calls))

    def test_a_child_agent_first_model_call_starts_at_the_child_session_start(self) -> None:
        self.team_run()
        result = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        [call] = result.agent("child").model_calls
        self.assertEqual((call.started_ms, call.ended_ms), (BASE_MS + 2800, BASE_MS + 4000))
        [call] = result.agent("grandchild").model_calls
        self.assertEqual((call.started_ms, call.ended_ms), (BASE_MS + 3300, BASE_MS + 3500))

    def test_commands_carry_their_interval_text_exit_code_paths_and_denial(self) -> None:
        self.team_run()
        result = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        [denied] = result.agent("root").commands
        self.assertEqual((denied.started_ms, denied.ended_ms, denied.exit_code, denied.denial), (BASE_MS + 2100, BASE_MS + 2600, 1, True))
        self.assertEqual(denied.text, "cat /w/src/a.py && echo x > /outside/f")
        self.assertEqual(denied.paths_named, ["/w/src/a.py", "/outside/f"])
        [built] = result.agent("child").commands
        self.assertEqual((built.exit_code, built.denial, built.paths_named), (0, False, ["./build.py"]))
        self.assertEqual(result.totals()["denials"], 1)

    def test_a_refusal_phrase_in_the_output_of_a_command_that_exited_zero_is_not_a_denial(self) -> None:
        output = "test grants::permission_denied_is_reported ... ok\nbash: /x: Permission denied\nOperation not permitted\n"
        self.single_run([item_completed(BASE_MS + 100, BASE_MS + 200, 1, command_item(["/bin/bash", "-lc", "cargo test"], 0, output))])
        result = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        [command] = result.agent("root").commands
        self.assertEqual((command.exit_code, command.denial), (0, False))
        self.assertEqual(result.totals()["denials"], 0)

    def test_a_refusal_phrase_with_a_missing_exit_code_is_a_denial(self) -> None:
        item = command_item(["/bin/bash", "-lc", "touch /x"], 1, "touch: /x: Permission denied\n")
        del item["exit_code"]
        self.single_run([item_completed(BASE_MS + 100, BASE_MS + 200, 1, item)])
        [command] = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).agent("root").commands
        self.assertEqual((command.exit_code, command.denial), (None, True))

    def test_a_command_without_aggregated_output_is_read_from_stdout_and_stderr(self) -> None:
        item = command_item(["/bin/bash", "-lc", "cat /etc/shadow"], 1, "")
        del item["aggregated_output"]
        item["stdout"], item["stderr"] = "", "cat: /etc/shadow: Permission denied\n"
        self.single_run([item_completed(BASE_MS + 100, BASE_MS + 200, 1, item)])
        [command] = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).agent("root").commands
        self.assertTrue(command.denial)

    def test_a_command_whose_start_is_not_an_integer_names_the_item(self) -> None:
        event = item_completed(BASE_MS + 100, BASE_MS + 200, 1, command_item(["ls"], 0, ""))
        event["payload"]["started_at_ms"] = "soon"
        self.single_run([event])
        with self.assertRaises(ValueError) as caught:
            normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        self.assertIn("item exec-1", str(caught.exception))
        self.assertIn("started_at_ms", str(caught.exception))

    def test_file_changes_map_the_item_types_and_skip_a_failed_item(self) -> None:
        self.team_run()
        root = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).agent("root")
        self.assertEqual(
            [(c.path, c.kind, c.at_ms, c.via) for c in root.file_changes],
            [("/w/src/a.py", "edit", BASE_MS + 5300, "tool"), ("/w/src/b.py", "create", BASE_MS + 5300, "tool"), ("/w/old.txt", "delete", BASE_MS + 5300, "tool"), ("/w/odd", "unknown", BASE_MS + 5300, "tool")],
        )

    def test_a_file_change_whose_changes_are_not_an_object_names_the_item(self) -> None:
        self.single_run([item_completed(BASE_MS + 100, BASE_MS + 200, 1, {"type": "FileChange", "id": "fc-x", "status": "completed", "changes": ["/w/a"]})])
        with self.assertRaises(ValueError) as caught:
            normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        self.assertIn("item fc-x", str(caught.exception))
        self.assertIn("changes", str(caught.exception))

    def test_a_compaction_records_its_time_and_the_context_size_before_it(self) -> None:
        self.team_run()
        root = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).agent("root")
        self.assertEqual(root.compactions, [trajectory.Compaction(at_ms=BASE_MS + 6000, tokens_before=220)])

    def test_a_compaction_that_carries_its_own_usage_uses_that_total(self) -> None:
        self.single_run(
            [
                record(BASE_MS + 100, 1, "token_usage_record", {"thread_id": "root", "response_id": "r1", "usage": usage(100, 10)}),
                record(BASE_MS + 200, 2, "compacted", {"message": "", "latest_token_usage_record": {"usage": usage(500, 50)}, "replacement_history": []}),
                record(BASE_MS + 300, 3, "compacted", {"message": "", "latest_token_usage_record": None, "replacement_history": []}),
            ]
        )
        root = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).agent("root")
        self.assertEqual([c.tokens_before for c in root.compactions], [550, 110])

    def test_a_compaction_before_any_usage_has_no_context_size(self) -> None:
        self.single_run([record(BASE_MS + 200, 1, "compacted", {"message": "", "latest_token_usage_record": None, "replacement_history": []})])
        root = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).agent("root")
        self.assertEqual(root.compactions, [trajectory.Compaction(at_ms=BASE_MS + 200, tokens_before=None)])

    def test_a_command_execution_is_also_a_tool_call_with_its_exit_status(self) -> None:
        self.team_run()
        result = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        [denied] = result.agent("root").tool_calls
        self.assertEqual(
            (denied.name, denied.started_ms, denied.ended_ms, denied.is_error),
            ("command_execution", BASE_MS + 2100, BASE_MS + 2600, False),
            "a command that ran and exited nonzero is a result, which is what the foe arm records for the same failure",
        )
        self.assertEqual(denied.summary, "exit 1")
        self.assertEqual(denied.arguments_digest, trajectory.arguments_digest({"command": ["/bin/bash", "-lc", "cat /w/src/a.py && echo x > /outside/f"], "cwd": "file:///w"}))
        self.assertNotIn("/outside/f", json.dumps(denied.to_dict()), "the command reaches the tool call as a digest alone")
        [built] = result.agent("child").tool_calls
        self.assertEqual((built.name, built.summary, built.is_error), ("command_execution", "exit 0", False))
        self.assertEqual(result.totals()["tool_calls_by_name"], {"command_execution": 2})
        self.assertEqual(result.agent("grandchild").tool_calls, [])

    def test_a_command_that_never_ran_is_an_error_and_one_that_exited_nonzero_is_not(self) -> None:
        ran = command_item(["/bin/bash", "-lc", "pytest"], 1, "1 failed\n", "exec-ran")
        never = command_item(["/bin/bash", "-lc", "missing-binary"], 1, "", "exec-never")
        del never["exit_code"]
        self.single_run([item_completed(BASE_MS + 100, BASE_MS + 200, 1, ran), item_completed(BASE_MS + 300, BASE_MS + 400, 2, never)])
        failed_suite, unstarted = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).agent("root").tool_calls
        self.assertEqual((failed_suite.summary, failed_suite.is_error), ("exit 1", False))
        self.assertEqual((unstarted.summary, unstarted.is_error), ("exit none", True), "an item that states failed and no exit status names a process that never ran")

    def test_an_exit_status_on_an_item_that_is_no_command_leaves_the_error_flag_to_the_status(self) -> None:
        self.single_run([item_completed(BASE_MS + 100, BASE_MS + 200, 1, {"type": "McpToolCall", "id": "m", "tool": "t", "status": "completed", "exit_code": 2})])
        [call] = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).agent("root").tool_calls
        self.assertEqual((call.name, call.summary, call.is_error), ("t", "status completed", False))

    def test_a_command_without_an_exit_code_states_that_it_has_none(self) -> None:
        item = command_item(["/bin/bash", "-lc", "sleep 1"], 0, "")
        del item["exit_code"]
        self.single_run([item_completed(BASE_MS + 100, BASE_MS + 200, 1, item)])
        [call] = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).agent("root").tool_calls
        self.assertEqual((call.summary, call.is_error), ("exit none", False))

    def test_an_mcp_and_a_collaboration_call_are_recorded_under_the_tool_they_name(self) -> None:
        self.single_run(
            [
                item_completed(BASE_MS + 100, BASE_MS + 200, 1, {"type": "McpToolCall", "id": "mcp-1", "server": "docs", "tool": "search", "arguments": {"query": "sandbox"}, "status": "completed"}),
                item_completed(BASE_MS + 300, BASE_MS + 400, 2, {"type": "CollabToolCall", "id": "cb-1", "tool": "request_review", "arguments": {"summary": "ready"}, "status": "failed"}),
                item_completed(BASE_MS + 500, BASE_MS + 600, 3, {"type": "McpToolCall", "id": "mcp-2", "status": "completed"}),
            ]
        )
        result = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        search, review, unnamed = result.agent("root").tool_calls
        self.assertEqual((search.name, search.summary, search.is_error), ("docs/search", "status completed", False))
        self.assertEqual(search.arguments_digest, trajectory.arguments_digest({"query": "sandbox"}))
        self.assertNotIn("sandbox", json.dumps(search.to_dict()))
        self.assertEqual((review.name, review.summary, review.is_error), ("request_review", "status failed", True))
        self.assertEqual((review.started_ms, review.ended_ms), (BASE_MS + 300, BASE_MS + 400))
        self.assertEqual((unnamed.name, unnamed.arguments_digest), ("mcp_tool_call", None), "an item that records no arguments carries no digest")
        self.assertEqual(result.totals()["tool_calls_by_name"], {"docs/search": 1, "mcp_tool_call": 1, "request_review": 1})
        self.assertEqual(result.totals()["tool_calls"], 3)

    def test_an_item_that_records_no_tool_call_is_left_out(self) -> None:
        self.single_run(
            [
                item_completed(BASE_MS + 100, BASE_MS + 200, 1, {"type": "FileChange", "id": "fc-1", "status": "completed", "changes": {"/w/a.py": {"type": "add"}}}),
                item_completed(BASE_MS + 300, BASE_MS + 400, 2, {"type": "AgentMessage", "id": "am-1", "text": "done"}),
            ]
        )
        result = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        self.assertEqual(result.agent("root").tool_calls, [])
        self.assertEqual(result.agent("root").file_changes[0].path, "/w/a.py")

    def test_the_outcome_ends_when_the_last_agent_record_does(self) -> None:
        self.team_run()
        result = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        self.assertEqual(result.outcome, trajectory.Outcome("completed", value="done", ended_ms=BASE_MS + 7100))
        self.assertEqual(result.totals()["wall_ms"], 7100)

    def test_the_result_round_trips_through_the_schema(self) -> None:
        self.team_run()
        result = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        self.assertEqual(trajectory.Trajectory.from_dict(json.loads(json.dumps(result.to_dict()))), result)

    def test_token_count_events_stand_in_when_no_usage_record_exists(self) -> None:
        t = BASE_MS
        self.single_run(
            [
                record(t + 500, 1, "event_msg", {"type": "token_count", "info": {"total_token_usage": usage(7, 2), "last_token_usage": usage(7, 2, cached=4)}}),
                record(t + 900, 2, "event_msg", {"type": "token_count", "info": {"total_token_usage": usage(20, 5), "last_token_usage": usage(13, 3)}}),
            ]
        )
        calls = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).agent("root").model_calls
        self.assertEqual([(c.seq, c.started_ms, c.ended_ms, c.input_tokens, c.output_tokens, c.cache_read_tokens) for c in calls], [(1, t, t + 500, 7, 2, 4), (2, t + 500, t + 900, 13, 3, 0)])

    def test_the_route_is_what_the_runner_passes(self) -> None:
        self.single_run([turn_context(BASE_MS + 1, 1)])
        self.assertEqual(normalizer.normalize(self.home, self.events, self.last, 0, "compatible").route, "compatible")
        self.assertEqual(normalizer.normalize(self.home, self.events, self.last, 0, "subscription").route, "subscription")
        with self.assertRaises(ValueError) as caught:
            normalizer.normalize(self.home, self.events, self.last, 0, "direct")
        self.assertIn("'direct'", str(caught.exception))

    def test_a_session_cut_mid_record_keeps_the_records_before_the_cut(self) -> None:
        self.team_run()
        path = self.home / "sessions" / "2026" / "09" / "11" / "rollout-b-child.jsonl"
        text = path.read_text(encoding="utf-8")
        path.write_text(text[:-40], encoding="utf-8")
        result = normalizer.normalize(self.home, self.events, self.last, None, ROUTE, limit="output_tokens")
        self.assertEqual(result.outcome.status, "exhausted")
        child = result.agent("child")
        self.assertEqual(len(child.commands), 1)
        self.assertEqual(child.model_calls, [])
        self.assertEqual(child.ended_ms, BASE_MS + 3200)
        self.assertEqual(result.identity["truncated_session_files"], ["sessions/2026/09/11/rollout-b-child.jsonl"])

    def test_the_real_fixture_cut_mid_record_still_normalizes(self) -> None:
        home = Path(self.tmp.name) / "cut"
        shutil.copytree(SINGLE_REPLY / "home", home)
        path = home / SINGLE_REPLY_ROLLOUT.relative_to(SINGLE_REPLY / "home")
        path.write_bytes(path.read_bytes()[:-40])
        result = normalizer.normalize(home, SINGLE_REPLY / "events.jsonl", None, None, ROUTE)
        self.assertEqual(result.outcome.status, "exhausted")
        self.assertEqual(len(result.agents[0].model_calls), 1)

    def test_a_damaged_or_empty_file_of_another_thread_does_not_stop_the_run(self) -> None:
        self.team_run()
        day = self.home / "sessions" / "2026" / "09" / "11"
        (day / "rollout-empty.jsonl").write_text("", encoding="utf-8")
        (day / "rollout-cut-head.jsonl").write_text('{"timestamp":"2026-09-11T16:13', encoding="utf-8")
        (day / "rollout-broken.jsonl").write_text(json.dumps(meta("other", BASE_MS)) + "\n{not json\n", encoding="utf-8")
        result = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        self.assertEqual(result.totals()["agents"], 3)

    def test_a_thread_linked_as_its_own_ancestor_is_an_error_naming_the_file(self) -> None:
        cyclic = meta("root", BASE_MS)
        cyclic["payload"]["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": "root", "depth": 1}}}
        path = write_session(self.home, "2026/09/11", "root", [cyclic, turn_context(BASE_MS + 1, 1)])
        write_events(self.events, "root", [])
        with self.assertRaises(ValueError) as caught:
            normalizer.normalize(self.home, self.events, None, 0, ROUTE)
        self.assertIn(str(path), str(caught.exception))
        self.assertIn("'root'", str(caught.exception))

    def test_two_files_for_one_thread_are_an_error_naming_both(self) -> None:
        first = write_session(self.home, "2026/09/11", "a", [meta("root", BASE_MS)])
        second = write_session(self.home, "2026/09/11", "b", [meta("root", BASE_MS)])
        write_events(self.events, "root", [])
        with self.assertRaises(ValueError) as caught:
            normalizer.normalize(self.home, self.events, None, 0, ROUTE)
        self.assertIn(str(first), str(caught.exception))
        self.assertIn(str(second), str(caught.exception))

    def test_a_line_of_the_event_stream_that_is_not_an_event_is_recorded_and_skipped(self) -> None:
        self.single_run([turn_context(BASE_MS + 1, 1)])
        text = self.events.read_text(encoding="utf-8")
        self.events.write_text("warning: plain text\n" + text + "[1, 2]\n", encoding="utf-8")
        result = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        self.assertEqual(result.outcome.status, "completed")
        problems = result.identity["event_problems"]
        self.assertEqual(len(problems), 2)
        self.assertIn(f"{self.events}:1", problems[0])
        self.assertIn(f"{self.events}:5", problems[1])


class Outcomes(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.events = Path(self.tmp.name) / "events.jsonl"
        self.last = Path(self.tmp.name) / "last.txt"
        write_session(self.home, "2026/09/11", "root", [meta("root", BASE_MS), turn_context(BASE_MS + 1, 1)])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_typed_final_message_fills_status_code_and_evidence(self) -> None:
        write_events(self.events, "root", [{"type": "turn.completed", "usage": usage(1, 1)}])
        self.last.write_text(json.dumps({"status": "blocked", "code": "tests-fail", "evidence": {"failing": 2}}), encoding="utf-8")
        outcome = normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).outcome
        self.assertEqual((outcome.status, outcome.code, outcome.value, outcome.ended_ms), ("blocked", "tests-fail", {"failing": 2}, BASE_MS + 1))

    def test_a_typed_status_outside_the_schema_names_the_file(self) -> None:
        write_events(self.events, "root", [{"type": "turn.completed", "usage": usage(1, 1)}])
        self.last.write_text(json.dumps({"status": "maybe"}), encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        self.assertIn(str(self.last), str(caught.exception))
        self.assertIn("'maybe'", str(caught.exception))

    def test_a_plain_final_message_is_the_completed_value(self) -> None:
        write_events(self.events, "root", [{"type": "turn.completed", "usage": usage(1, 1)}])
        self.last.write_text("[1, 2]\n", encoding="utf-8")
        self.assertEqual(normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).outcome.value, "[1, 2]")

    def test_no_final_message_path_means_completed_without_a_value(self) -> None:
        write_events(self.events, "root", [{"type": "turn.completed", "usage": usage(1, 1)}])
        self.assertEqual(normalizer.normalize(self.home, self.events, None, 0, ROUTE).outcome, trajectory.Outcome("completed", ended_ms=BASE_MS + 1))

    def test_a_missing_final_message_file_on_exit_zero_is_an_error_naming_it(self) -> None:
        write_events(self.events, "root", [{"type": "turn.completed", "usage": usage(1, 1)}])
        with self.assertRaises(FileNotFoundError) as caught:
            normalizer.normalize(self.home, self.events, self.last, 0, ROUTE)
        self.assertIn(str(self.last), str(caught.exception))

    def test_a_run_the_watcher_stopped_is_exhausted_with_the_limit_whatever_the_files_say(self) -> None:
        write_events(self.events, "root", [{"type": "turn.completed", "usage": usage(1, 1)}])
        self.last.write_text("done", encoding="utf-8")
        self.assertEqual(normalizer.normalize(self.home, self.events, self.last, None, ROUTE, limit="output_tokens").outcome, trajectory.Outcome("exhausted", code="output_tokens", ended_ms=BASE_MS + 1))
        self.assertEqual(normalizer.normalize(self.home, self.events, self.last, None, ROUTE).outcome, trajectory.Outcome("exhausted", ended_ms=BASE_MS + 1))

    def test_a_run_ended_by_a_signal_is_killed_and_names_the_signal(self) -> None:
        write_events(self.events, "root", [])
        self.assertEqual(normalizer.normalize(self.home, self.events, self.last, -9, ROUTE).outcome, trajectory.Outcome("killed", code="signal 9", ended_ms=BASE_MS + 1))

    def test_a_nonzero_exit_is_failed_and_names_the_turn_failure_or_the_status(self) -> None:
        write_events(self.events, "root", [{"type": "turn.failed", "error": {"message": "context window exceeded"}}])
        self.assertEqual(normalizer.normalize(self.home, self.events, self.last, 1, ROUTE).outcome.code, "context window exceeded")
        write_events(self.events, "root", [])
        self.assertEqual(normalizer.normalize(self.home, self.events, self.last, 3, ROUTE).outcome.code, "exit status 3")

    def test_a_turn_failure_on_exit_zero_is_still_failed(self) -> None:
        write_events(self.events, "root", [{"type": "turn.failed", "error": {"message": "stream closed"}}])
        self.last.write_text("partial", encoding="utf-8")
        self.assertEqual(normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).outcome, trajectory.Outcome("failed", code="stream closed", ended_ms=BASE_MS + 1))

    def test_an_error_event_on_exit_zero_is_failed_with_its_message(self) -> None:
        write_events(self.events, "root", [{"type": "error", "message": "rate limited"}])
        self.last.write_text("partial", encoding="utf-8")
        self.assertEqual(normalizer.normalize(self.home, self.events, self.last, 0, ROUTE).outcome, trajectory.Outcome("failed", code="rate limited", ended_ms=BASE_MS + 1))


class Errors(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.events = Path(self.tmp.name) / "events.jsonl"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_no_sessions_directory_names_it(self) -> None:
        write_events(self.events, "root", [])
        with self.assertRaises(FileNotFoundError) as caught:
            normalizer.normalize(self.home, self.events, None, 0, ROUTE)
        self.assertIn(str(self.home / "sessions"), str(caught.exception))

    def test_a_missing_root_session_names_the_thread_and_the_directory(self) -> None:
        write_session(self.home, "2026/09/11", "other", [meta("other", BASE_MS)])
        write_events(self.events, "root", [])
        with self.assertRaises(ValueError) as caught:
            normalizer.normalize(self.home, self.events, None, 0, ROUTE)
        self.assertIn("'root'", str(caught.exception))
        self.assertIn(str(self.home / "sessions"), str(caught.exception))

    def test_an_event_stream_without_thread_started_names_the_file(self) -> None:
        write_session(self.home, "2026/09/11", "root", [meta("root", BASE_MS)])
        self.events.write_text('{"type": "turn.started"}\n', encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            normalizer.normalize(self.home, self.events, None, 0, ROUTE)
        self.assertIn(str(self.events), str(caught.exception))

    def test_a_session_whose_first_record_is_not_session_meta_names_the_file(self) -> None:
        path = write_session(self.home, "2026/09/11", "root", [record(BASE_MS, 0, "turn_context", {})])
        with self.assertRaises(ValueError) as caught:
            normalizer.read_session(path)
        self.assertIn(str(path), str(caught.exception))
        self.assertIn("session_meta", str(caught.exception))

    def test_a_terminated_line_that_is_not_json_names_the_file_and_line(self) -> None:
        path = self.home / "sessions" / "2026" / "09" / "11" / "rollout-x.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(meta("root", BASE_MS)) + "\n{not json\n", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            normalizer.read_session(path)
        self.assertIn(f"{path}:2", str(caught.exception))

    def test_an_unterminated_final_line_that_is_not_json_is_dropped_as_a_cut(self) -> None:
        path = self.home / "sessions" / "2026" / "09" / "11" / "rollout-x.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(meta("root", BASE_MS)) + "\n" + json.dumps(turn_context(BASE_MS + 1, 1))[:-7], encoding="utf-8")
        session = normalizer.read_session(path)
        self.assertEqual((len(session.records), session.truncated), (1, True))

    def test_a_record_whose_payload_is_not_an_object_names_the_file_and_line(self) -> None:
        path = write_session(self.home, "2026/09/11", "root", [meta("root", BASE_MS), {"timestamp": iso(BASE_MS), "ordinal": 1, "type": "turn_context", "payload": "x"}])
        with self.assertRaises(ValueError) as caught:
            normalizer.read_session(path)
        self.assertIn(f"{path}:2", str(caught.exception))
        self.assertIn("payload", str(caught.exception))

    def test_a_child_without_a_parent_id_names_the_key(self) -> None:
        bad = meta("child", BASE_MS)
        bad["payload"]["source"] = {"subagent": {"thread_spawn": {"depth": 1}}}
        path = write_session(self.home, "2026/09/11", "child", [bad])
        with self.assertRaises(ValueError) as caught:
            normalizer.read_session(path)
        self.assertIn("thread_spawn.parent_thread_id", str(caught.exception))


class CommandLine(unittest.TestCase):
    def test_main_writes_the_trajectory_for_the_real_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "trajectory.json"
            status = normalizer.main(["--codex-home", str(SINGLE_REPLY / "home"), "--events", str(SINGLE_REPLY / "events.jsonl"), "--last-message", str(SINGLE_REPLY / "last.txt"), "--exit-status", "0", "--route", ROUTE, "--out", str(out)])
            self.assertEqual(status, 0)
            written = trajectory.Trajectory.from_dict(json.loads(out.read_text(encoding="utf-8")))
            self.assertEqual(written.outcome.value, "ok")
            self.assertEqual(written.totals()["model_calls"], 1)

    def test_main_records_a_stopped_run_as_exhausted_with_the_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "trajectory.json"
            status = normalizer.main(["--codex-home", str(SINGLE_REPLY / "home"), "--events", str(SINGLE_REPLY / "events.jsonl"), "--exit-status", "stopped", "--limit", "seconds", "--route", ROUTE, "--out", str(out)])
            self.assertEqual(status, 0)
            written = trajectory.Trajectory.from_dict(json.loads(out.read_text(encoding="utf-8")))
            self.assertEqual((written.outcome.status, written.outcome.code), ("exhausted", "seconds"))

    def test_main_reports_a_missing_run_on_stderr_and_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = normalizer.main(["--codex-home", tmp, "--events", str(SINGLE_REPLY / "events.jsonl"), "--exit-status", "stopped", "--route", ROUTE])
            self.assertEqual(status, 2)
            self.assertIn(str(Path(tmp) / "sessions"), stderr.getvalue())

    def test_main_reports_a_non_object_payload_on_stderr_and_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            shutil.copytree(SINGLE_REPLY / "home", home)
            path = home / SINGLE_REPLY_ROLLOUT.relative_to(SINGLE_REPLY / "home")
            lines = path.read_text(encoding="utf-8").splitlines()
            second = json.loads(lines[1])
            second["payload"] = "x"
            lines[1] = json.dumps(second)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = normalizer.main(["--codex-home", str(home), "--events", str(SINGLE_REPLY / "events.jsonl"), "--exit-status", "0", "--route", ROUTE])
            self.assertEqual(status, 2)
            self.assertIn(f"{path}:2", stderr.getvalue())

    def test_the_exit_status_flag_accepts_stopped_and_integers_only(self) -> None:
        self.assertIsNone(normalizer.parse_exit_status("stopped"))
        self.assertEqual(normalizer.parse_exit_status("137"), 137)
        with self.assertRaises(argparse.ArgumentTypeError):
            normalizer.parse_exit_status("crashed")


if __name__ == "__main__":
    unittest.main()
