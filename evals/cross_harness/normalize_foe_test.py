#!/usr/bin/python3
"""Unit tests for the foe log normalizer: recorded fixtures and synthetic logs, no binary."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import normalize_foe  # noqa: E402
import trajectory  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "foe"
FIXTURE_NAMES = ("edit-and-bash", "spawn-child", "blocked", "exhausted", "failed", "compaction")


def fixture(name: str) -> trajectory.Trajectory:
    return normalize_foe.normalize(FIXTURES / name)


def events_of(name: str, relative: str = "episode.jsonl") -> list[dict[str, Any]]:
    return normalize_foe.read_events(FIXTURES / name / relative)


def event(seq: int, time: int, kind: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"seq": seq, "time": time, "type": kind, "data": data}


def start(episode_id: str, parent_id: str | None = None, name: str = "synthetic", time: int = 1000) -> dict[str, Any]:
    return event(
        0,
        time,
        "episode/start",
        {
            "id": episode_id,
            "parent_id": parent_id,
            "contract": {"name": name},
            "contract_fingerprint": "sha256:0",
            "runtime": {"version": "0.2.0", "build": "sha256:1"},
            "sandbox": {"mode": "off", "landlock_abi": 0},
        },
    )


def write_log(directory: Path, events: list[dict[str, Any]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "episode.jsonl"
    log.write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")
    return log


class RecordedFixtures(unittest.TestCase):
    def test_every_fixture_round_trips_through_the_schema(self) -> None:
        for name in FIXTURE_NAMES:
            with self.subTest(name=name):
                normalized = fixture(name)
                again = trajectory.Trajectory.from_dict(normalized.to_dict())
                self.assertEqual(again.to_dict(), normalized.to_dict())
                self.assertEqual(normalized.harness, "foe")
                self.assertEqual(normalized.route, "compatible", "the host-served fixtures name the provider host")
                self.assertEqual(normalized.identity["runtime_version"], "0.2.0")
                self.assertTrue(str(normalized.identity["runtime_build"]).startswith("sha256:"))
                self.assertTrue(str(normalized.identity["contract_fingerprint"]).startswith("sha256:"))
                self.assertEqual(normalized.identity["contract_name"], name)

    def test_commands_carry_exit_codes_paths_and_the_inferred_denial(self) -> None:
        normalized = fixture("edit-and-bash")
        self.assertEqual(normalized.identity["sandbox_mode"], "required")
        [agent] = normalized.agents
        self.assertEqual([command.exit_code for command in agent.commands], [0, 1, 0])
        self.assertEqual([command.denial for command in agent.commands], [False, True, False])
        secret = agent.commands[1]
        self.assertEqual(len(secret.paths_named), 1)
        self.assertTrue(secret.paths_named[0].endswith("/outside/secret.txt"))
        self.assertEqual(agent.commands[2].paths_named, ["src/made.txt"])
        for command in agent.commands:
            self.assertIsNotNone(command.ended_ms)
            self.assertLessEqual(command.started_ms, command.ended_ms)

    def test_a_command_starts_when_its_result_duration_says_and_never_before_its_call(self) -> None:
        events = events_of("edit-and-bash")
        results = {e["data"]["call_id"]: e for e in events if e["type"] == "tool/result"}
        opened = {call["id"]: e["time"] for e in events if e["type"] == "assistant/message" for call in e["data"]["tool_calls"]}
        [agent] = fixture("edit-and-bash").agents
        for command, call_id in zip(agent.commands, ("bash-list", "bash-secret", "bash-write")):
            result = results[call_id]
            self.assertEqual(command.ended_ms, result["time"])
            self.assertEqual(command.started_ms, max(opened[call_id], result["time"] - result["data"]["duration_ms"]))

    def test_edit_results_become_file_changes_with_the_creating_edit_marked(self) -> None:
        [agent] = fixture("edit-and-bash").agents
        self.assertEqual([(Path(change.path).name, change.kind, change.via) for change in agent.file_changes], [("created.py", "create", "tool"), ("existing.py", "edit", "tool")])
        self.assertTrue(all(Path(change.path).is_absolute() for change in agent.file_changes))

    def test_model_calls_pair_requests_with_their_usage(self) -> None:
        normalized = fixture("edit-and-bash")
        [agent] = normalized.agents
        self.assertEqual([call.seq for call in agent.model_calls], [3, 14, 25, 32])
        self.assertTrue(all(call.has_usage for call in agent.model_calls))
        self.assertTrue(all(call.reasoning_tokens is None for call in agent.model_calls), "foe reports no separate reasoning count")
        totals = normalized.totals()
        self.assertEqual(totals["model_calls"], 4)
        self.assertEqual(totals["input_tokens"], 1400 + 1400 + 20 + 20)
        self.assertEqual(totals["output_tokens"], 200 + 200 + 10 + 10)
        self.assertEqual(totals["denials"], 1)
        self.assertEqual(totals["file_changes"], 2)

    def test_a_child_episode_is_an_agent_below_its_parent(self) -> None:
        normalized = fixture("spawn-child")
        root, child = normalized.agents
        self.assertEqual((root.depth, root.role, root.parent_id), (0, "root", None))
        self.assertEqual((child.depth, child.role, child.parent_id), (1, "survey", root.id))
        self.assertGreaterEqual(child.started_ms, root.started_ms)
        self.assertLessEqual(child.ended_ms, root.ended_ms)
        self.assertEqual(normalized.agent(child.id), child)
        release = next(e["data"]["spent"] for e in events_of("spawn-child") if e["type"] == "budget/release")
        self.assertEqual(len(child.model_calls), release["model_calls"])
        self.assertEqual(sum(call.input_tokens for call in child.model_calls), release["input_tokens"])
        self.assertEqual(sum(call.output_tokens for call in child.model_calls), release["output_tokens"])
        self.assertEqual(normalized.totals()["max_depth"], 1)
        self.assertEqual(normalized.totals()["agents"], 2)

    def test_each_outcome_kind_maps_to_its_status_and_code(self) -> None:
        self.assertEqual(fixture("edit-and-bash").outcome.status, "completed")
        self.assertEqual(fixture("edit-and-bash").outcome.value, "Both files are written and the commands ran.")
        blocked = fixture("blocked").outcome
        self.assertEqual((blocked.status, blocked.code, blocked.value), ("blocked", "missing-capability", None))
        exhausted = fixture("exhausted").outcome
        self.assertEqual((exhausted.status, exhausted.code), ("exhausted", "model_calls"))
        failed = fixture("failed")
        self.assertEqual((failed.outcome.status, failed.outcome.code), ("failed", None))
        self.assertEqual(failed.outcome.value, events_of("failed")[-1]["data"]["outcome"]["error"], "a failed outcome carries its error text as the value")
        self.assertIn("failed", str(failed.outcome.value))
        [call] = failed.agents[0].model_calls
        self.assertIsNone(call.ended_ms, "the request that failed was never answered")
        self.assertFalse(call.has_usage)
        self.assertIsNone(failed.totals()["input_tokens"])
        for name in FIXTURE_NAMES:
            end = events_of(name)[-1]
            self.assertEqual(fixture(name).outcome.ended_ms, end["time"], name)

    def test_a_compaction_records_the_projection_that_triggered_it(self) -> None:
        normalized = fixture("compaction")
        [agent] = normalized.agents
        [compaction] = agent.compactions
        self.assertEqual(compaction.tokens_before, 5224)
        self.assertEqual(len(agent.model_calls), 4, "the summarization request counts as a model call")
        self.assertEqual(normalized.totals()["compactions"], 1)


class Route(unittest.TestCase):
    def test_the_subscription_provider_and_every_other(self) -> None:
        self.assertEqual(normalize_foe.route_for("openai-codex"), "subscription")
        for provider in ("compatible-http", "host", None):
            self.assertEqual(normalize_foe.route_for(provider), "compatible", provider)

    def test_an_explicit_route_overrides_the_derived_one(self) -> None:
        self.assertEqual(normalize_foe.normalize(FIXTURES / "blocked", route="subscription").route, "subscription")

    def test_the_contract_model_wins_over_the_header(self) -> None:
        events = [
            start("ep_m"),
            event(1, 1001, "request/header", {"model": {"provider": "host", "model": "host"}}),
        ]
        events[0]["data"]["contract"]["model"] = {"provider": "openai-codex", "model": "m"}
        with tempfile.TemporaryDirectory() as tmp:
            write_log(Path(tmp), events)
            normalized = normalize_foe.normalize(Path(tmp))
        self.assertEqual((normalized.identity["model_provider"], normalized.identity["model"], normalized.route), ("openai-codex", "m", "subscription"))

    def test_the_identity_carries_the_model_options_without_credential_paths(self) -> None:
        events = [start("ep_o"), event(1, 1001, "episode/end", {"outcome": {"kind": "completed", "value": None}})]
        events[0]["data"]["contract"]["model"] = {
            "provider": "openai-codex",
            "model": "m",
            "reasoning_effort": "high",
            "service_tier": "flex",
            "max_output_tokens": 4096,
            "token_file": "/home/someone/.credentials/auth.json",
        }
        with tempfile.TemporaryDirectory() as tmp:
            write_log(Path(tmp), events)
            identity = normalize_foe.normalize(Path(tmp)).identity
        self.assertEqual(identity["reasoning_effort"], "high")
        self.assertEqual(identity["model_options"], {"reasoning_effort": "high", "service_tier": "flex", "max_output_tokens": 4096})
        self.assertNotIn("auth.json", json.dumps(identity))
        self.assertIn("high", json.dumps(identity).split('"model_options"')[0], "the effort is a top-level identity key as well")

    def test_a_model_block_without_options_records_an_empty_option_set(self) -> None:
        identity = fixture("blocked").identity
        self.assertIsNone(identity["reasoning_effort"])
        self.assertEqual(identity["model_options"], {})


class SyntheticLogs(unittest.TestCase):
    def test_a_log_cut_short_inside_a_line_is_killed_and_keeps_the_events_before_the_cut(self) -> None:
        events = [start("ep_c"), event(1, 1001, "model/request", {"request_id": "rq_1", "step": 1})]
        with tempfile.TemporaryDirectory() as tmp:
            log = write_log(Path(tmp), events)
            with log.open("a", encoding="utf-8") as handle:
                handle.write('{"seq": 2, "time": 1002, "type": "assistant/mess')
            normalized = normalize_foe.normalize(Path(tmp))
            self.assertEqual(normalized.outcome.status, "killed")
            [agent] = normalized.agents
            [call] = agent.model_calls
            self.assertEqual((call.seq, call.ended_ms), (1, None))
            self.assertIsNone(agent.ended_ms)
            import contextlib
            import io

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(normalize_foe.main([str(tmp)]), 0)
            self.assertEqual(json.loads(out.getvalue())["trajectory"]["outcome"]["status"], "killed")

    def test_a_session_start_is_a_command_closed_by_the_poll_that_reports_its_exit(self) -> None:
        events = [
            start("ep_s"),
            event(1, 1001, "assistant/message", {"request_id": "rq_1", "tool_calls": [{"id": "s-start", "name": "session", "args": {"action": "start", "command": "cat /private/x"}}]}),
            event(2, 1003, "tool/result", {"call_id": "s-start", "name": "session", "value": {"session": 1, "name": "cat", "command": "cat /private/x", "lifetime": "episode"}, "is_error": False, "duration_ms": 1}),
            event(3, 1004, "assistant/message", {"request_id": "rq_2", "tool_calls": [{"id": "s-poll-1", "name": "session", "args": {"action": "poll", "session": 1}}]}),
            event(4, 1005, "tool/result", {"call_id": "s-poll-1", "name": "session", "value": {"session": 1, "alive": True, "exit_code": None, "permission_denial": None}, "is_error": False}),
            event(5, 1006, "assistant/message", {"request_id": "rq_3", "tool_calls": [{"id": "s-poll-2", "name": "session", "args": {"action": "poll", "session": 1}}]}),
            event(6, 1009, "tool/result", {"call_id": "s-poll-2", "name": "session", "value": {"session": 1, "alive": False, "exit_code": 1, "stderr": "cat: /private/x: Permission denied\n", "permission_denial": "possible"}, "is_error": False}),
            event(7, 1010, "assistant/message", {"request_id": "rq_4", "tool_calls": [{"id": "s-poll-3", "name": "session", "args": {"action": "poll", "session": 1}}]}),
            event(8, 1011, "tool/result", {"call_id": "s-poll-3", "name": "session", "value": {"session": 1, "alive": False, "exit_code": 1, "permission_denial": None}, "is_error": False}),
            event(9, 1012, "episode/end", {"outcome": {"kind": "completed", "value": None}}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            write_log(Path(tmp), events)
            normalized = normalize_foe.normalize(Path(tmp))
        [command] = normalized.agents[0].commands
        self.assertEqual((command.text, command.started_ms, command.ended_ms, command.exit_code, command.denial), ("cat /private/x", 1002, 1009, 1, True))
        self.assertEqual(command.paths_named, ["/private/x"])
        self.assertEqual((normalized.totals()["commands"], normalized.totals()["denials"]), (1, 1))

    def test_a_session_ends_with_stop_or_never_and_a_refused_start_closes_at_once(self) -> None:
        events = [
            start("ep_t"),
            event(1, 1001, "assistant/message", {"request_id": "rq_1", "tool_calls": [
                {"id": "s-a", "name": "session", "args": {"action": "start", "command": "sleep 100"}},
                {"id": "s-b", "name": "session", "args": {"action": "start", "command": "sleep 200"}},
                {"id": "s-c", "name": "session", "args": {"action": "start", "command": "/refused/bin"}},
            ]}),
            event(2, 1002, "tool/result", {"call_id": "s-a", "name": "session", "value": {"session": 1, "lifetime": "episode"}, "is_error": False}),
            event(3, 1003, "tool/result", {"call_id": "s-b", "name": "session", "value": {"session": 2, "lifetime": "episode"}, "is_error": False}),
            event(4, 1004, "tool/result", {"call_id": "s-c", "name": "session", "value": {}, "is_error": True, "failure": {"code": "capability-denied", "message": "refused"}}),
            event(5, 1005, "assistant/message", {"request_id": "rq_2", "tool_calls": [{"id": "s-stop", "name": "session", "args": {"action": "stop", "session": 2}}, {"id": "s-write", "name": "session", "args": {"action": "write", "session": 1, "input": "x"}}]}),
            event(6, 1008, "tool/result", {"call_id": "s-stop", "name": "session", "value": {"session": 2, "exit_code": None, "seconds": 5}, "is_error": False}),
            event(7, 1009, "tool/result", {"call_id": "s-write", "name": "session", "value": {"session": 1, "bytes": 1}, "is_error": False}),
            event(8, 1020, "episode/end", {"outcome": {"kind": "completed", "value": None}}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            write_log(Path(tmp), events)
            [agent] = normalize_foe.normalize(Path(tmp)).agents
        surviving, stopped, refused = agent.commands
        self.assertEqual((surviving.text, surviving.ended_ms, surviving.exit_code, surviving.denial), ("sleep 100", None, None, False))
        self.assertEqual((stopped.text, stopped.ended_ms, stopped.exit_code), ("sleep 200", 1008, None))
        self.assertEqual((refused.text, refused.ended_ms, refused.exit_code, refused.denial), ("/refused/bin", 1004, None, True))

    def test_every_child_log_is_read_once(self) -> None:
        reads: list[Path] = []
        original = normalize_foe.read_events

        def counting(log: Path) -> list[dict[str, Any]]:
            reads.append(log)
            return original(log)

        normalize_foe.read_events = counting
        try:
            normalized = normalize_foe.normalize(FIXTURES / "spawn-child")
        finally:
            normalize_foe.read_events = original
        self.assertEqual(len(normalized.agents), 2)
        self.assertEqual(len(reads), 2)
        self.assertEqual(len(set(reads)), 2)

    def test_a_log_without_an_end_is_killed_and_leaves_its_command_open(self) -> None:
        events = [
            start("ep_k"),
            event(1, 1001, "model/request", {"request_id": "rq_1", "step": 1}),
            event(2, 1002, "assistant/message", {"request_id": "rq_1", "tool_calls": [{"id": "c1", "name": "bash", "args": {"command": "sleep 100"}}], "usage": {"input": 5, "output": 1, "cache_read": 0}}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            write_log(Path(tmp), events)
            normalized = normalize_foe.normalize(Path(tmp))
        self.assertEqual(normalized.outcome.status, "killed")
        self.assertIsNone(normalized.outcome.ended_ms)
        [agent] = normalized.agents
        self.assertIsNone(agent.ended_ms)
        [command] = agent.commands
        self.assertEqual((command.started_ms, command.ended_ms, command.exit_code), (1002, None, None))
        self.assertIsNone(normalized.totals()["wall_ms"])

    def test_a_capability_denied_error_result_is_a_denial_without_an_exit_code(self) -> None:
        events = [
            start("ep_d"),
            event(1, 1001, "assistant/message", {"request_id": "rq_1", "tool_calls": [{"id": "c1", "name": "bash", "args": {"command": "cat /private/x"}}]}),
            event(2, 1010, "tool/result", {"call_id": "c1", "name": "bash", "value": {}, "is_error": True, "failure": {"code": "capability-denied", "message": "refused"}}),
            event(3, 1011, "episode/end", {"outcome": {"kind": "completed", "value": None}}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            write_log(Path(tmp), events)
            [command] = normalize_foe.normalize(Path(tmp)).agents[0].commands
        self.assertTrue(command.denial)
        self.assertIsNone(command.exit_code)
        self.assertEqual(command.ended_ms, 1010)
        self.assertEqual(command.paths_named, ["/private/x"])

    def test_an_inner_bash_call_is_a_command_and_an_older_edit_uses_the_call_path(self) -> None:
        events = [
            start("ep_i"),
            event(1, 1001, "assistant/message", {"request_id": "rq_1", "tool_calls": [{"id": "outer", "name": "compose_tools", "args": {}}, {"id": "e1", "name": "edit", "args": {"path": "src/a.py", "edits": [{"old_text": "x", "new_text": "y"}]}}]}),
            event(2, 1002, "tool/inner-call", {"outer_call_id": "outer", "call_id": "outer_0", "index": 0, "name": "bash", "args": {"command": "ls ./src"}}),
            event(3, 1005, "tool/result", {"call_id": "outer_0", "name": "bash", "value": {"exit_code": 0, "permission_denial": None}, "is_error": False, "duration_ms": 2}),
            event(4, 1006, "tool/result", {"call_id": "outer", "name": "compose_tools", "value": {}, "is_error": False}),
            event(5, 1007, "tool/result", {"call_id": "e1", "name": "edit", "value": {"edits": 1}, "is_error": False}),
            event(6, 1008, "episode/end", {"outcome": {"kind": "completed", "value": None}}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            write_log(Path(tmp), events)
            [agent] = normalize_foe.normalize(Path(tmp)).agents
        [command] = agent.commands
        self.assertEqual((command.text, command.started_ms, command.ended_ms, command.exit_code), ("ls ./src", 1003, 1005, 0))
        self.assertEqual([change.to_dict() for change in agent.file_changes], [{"path": "src/a.py", "kind": "edit", "at_ms": 1007, "via": "tool"}])

    def test_a_failed_edit_and_a_failed_compaction_record_nothing(self) -> None:
        events = [
            start("ep_f"),
            event(1, 1001, "assistant/message", {"request_id": "rq_1", "tool_calls": [{"id": "e1", "name": "edit", "args": {"path": "src/a.py", "edits": []}}]}),
            event(2, 1002, "tool/result", {"call_id": "e1", "name": "edit", "value": {}, "is_error": True, "failure": {"code": "invalid-call"}}),
            event(3, 1003, "compaction/start", {"step": 2, "projected_tokens": 900}),
            event(4, 1004, "compaction/end", {"step": 2, "ok": False, "error": "no summary"}),
            event(5, 1005, "compaction/start", {"step": 3, "projected_tokens": 950}),
            event(6, 1006, "compaction/end", {"step": 3, "ok": True}),
            event(7, 1007, "episode/end", {"outcome": {"kind": "exhausted", "limit": "seconds"}}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            write_log(Path(tmp), events)
            normalized = normalize_foe.normalize(Path(tmp))
        [agent] = normalized.agents
        self.assertEqual(agent.file_changes, [])
        self.assertEqual([compaction.to_dict() for compaction in agent.compactions], [{"at_ms": 1006, "tokens_before": 950}])
        self.assertEqual((normalized.outcome.status, normalized.outcome.code), ("exhausted", "seconds"))

    def test_a_retried_request_id_closes_the_latest_attempt(self) -> None:
        events = [
            start("ep_r"),
            event(1, 1001, "model/request", {"request_id": "rq_1", "step": 1, "attempt": 1}),
            event(2, 1002, "request/retry", {"step": 1, "attempt": 1, "cause": "transport", "delay_ms": 1}),
            event(3, 1003, "model/request", {"request_id": "rq_1", "step": 1, "attempt": 2}),
            event(4, 1004, "assistant/message", {"request_id": "rq_1", "tool_calls": [], "usage": {"input": 7, "output": 3, "cache_read": 2}}),
            event(5, 1005, "episode/end", {"outcome": {"kind": "completed", "value": "ok"}}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            write_log(Path(tmp), events)
            [agent] = normalize_foe.normalize(Path(tmp)).agents
        first, second = agent.model_calls
        self.assertEqual((first.seq, first.ended_ms, first.has_usage), (1, None, False))
        self.assertEqual((second.seq, second.ended_ms, second.input_tokens, second.cache_read_tokens), (3, 1004, 7, 2))

    def test_children_follow_their_parent_in_start_order_at_every_depth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_log(root, [start("ep_root"), event(1, 1100, "episode/end", {"outcome": {"kind": "completed", "value": None}})])
            write_log(root / "children" / "ep_b", [start("ep_b", "ep_root", "later", 1020), event(1, 1030, "episode/end", {"outcome": {"kind": "completed", "value": None}})])
            write_log(root / "children" / "ep_a", [start("ep_a", "ep_root", "earlier", 1010)])
            write_log(root / "children" / "ep_a" / "children" / "ep_c", [start("ep_c", "ep_a", "deep", 1015)])
            normalized = normalize_foe.normalize(root)
        self.assertEqual([(agent.id, agent.parent_id, agent.depth, agent.role) for agent in normalized.agents], [("ep_root", None, 0, "root"), ("ep_a", "ep_root", 1, "earlier"), ("ep_c", "ep_a", 2, "deep"), ("ep_b", "ep_root", 1, "later")])
        self.assertEqual(normalized.totals()["max_depth"], 2)
        self.assertEqual(normalized.totals()["wall_ms"], 100)


class Refusals(unittest.TestCase):
    def test_a_malformed_line_names_the_log_and_the_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = write_log(Path(tmp), [start("ep_x")])
            with log.open("a", encoding="utf-8") as handle:
                handle.write("{not json\n")
            with self.assertRaises(ValueError) as caught:
                normalize_foe.normalize(Path(tmp))
        self.assertIn(f"{log}:2", str(caught.exception))

    def test_a_malformed_last_line_that_ends_with_a_newline_is_still_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = write_log(Path(tmp), [start("ep_x")])
            with log.open("a", encoding="utf-8") as handle:
                handle.write('{"seq": 1, "time": 1001, "type": "assistant/mess\n')
            with self.assertRaises(ValueError) as caught:
                normalize_foe.normalize(Path(tmp))
        self.assertIn(f"{log}:2", str(caught.exception))

    def test_a_first_line_cut_short_leaves_no_start_and_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "episode.jsonl"
            log.write_text('{"seq": 0, "time": 1000, "type": "episode/sta', encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                normalize_foe.normalize(Path(tmp))
        self.assertIn("episode/start", str(caught.exception))

    def test_a_stated_version_this_reader_does_not_read_is_refused_naming_both(self) -> None:
        for stated in (9, 2):
            with self.subTest(stated=stated), tempfile.TemporaryDirectory() as tmp:
                events = [start("ep_v"), event(1, 1001, "episode/end", {"outcome": {"kind": "completed", "value": None}})]
                events[0]["version"] = stated
                log = write_log(Path(tmp), events)
                with self.assertRaises(ValueError) as caught:
                    normalize_foe.normalize(Path(tmp))
                message = str(caught.exception)
                self.assertIn(f"{log}:1", message)
                self.assertIn(f"version {stated}", message)
                self.assertIn(f"version {normalize_foe.LOG_VERSION}", message)
        with tempfile.TemporaryDirectory() as tmp:
            events = [start("ep_w"), event(1, 1001, "episode/end", {"outcome": {"kind": "completed", "value": None}})]
            events[0]["version"] = "3"
            write_log(Path(tmp), events)
            with self.assertRaises(ValueError) as caught:
                normalize_foe.normalize(Path(tmp))
        self.assertIn("version is not an integer", str(caught.exception))

    def test_a_stated_version_3_and_an_absent_version_are_both_read(self) -> None:
        self.assertEqual(events_of("blocked")[0]["version"], 3, "the recorded fixtures state their version")
        with tempfile.TemporaryDirectory() as tmp:
            events = [start("ep_u"), event(1, 1001, "episode/end", {"outcome": {"kind": "completed", "value": None}})]
            events[0]["version"] = 3
            write_log(Path(tmp), events)
            self.assertEqual(normalize_foe.normalize(Path(tmp)).outcome.status, "completed")
            del events[0]["version"]
            write_log(Path(tmp), events)
            self.assertEqual(normalize_foe.normalize(Path(tmp)).outcome.status, "completed")

    @unittest.skipIf(os.geteuid() == 0, "the superuser lists an unreadable directory")
    def test_an_unlistable_children_directory_is_refused_naming_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_log(root, [start("ep_root")])
            children = root / "children"
            children.mkdir()
            children.chmod(0)
            try:
                with self.assertRaises(ValueError) as caught:
                    normalize_foe.normalize(root)
            finally:
                children.chmod(0o755)
        self.assertIn(str(children), str(caught.exception))

    def test_a_log_that_does_not_open_with_episode_start_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = write_log(Path(tmp), [event(0, 1, "inbox/item", {})])
            with self.assertRaises(ValueError) as caught:
                normalize_foe.normalize(Path(tmp))
        self.assertIn(str(log), str(caught.exception))
        self.assertIn("episode/start", str(caught.exception))

    def test_a_missing_log_names_the_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as caught:
                normalize_foe.normalize(Path(tmp))
            self.assertIn(tmp, str(caught.exception))
            write_log(Path(tmp), [start("ep_p")])
            (Path(tmp) / "children" / "ep_empty").mkdir(parents=True)
            with self.assertRaises(ValueError) as caught:
                normalize_foe.normalize(Path(tmp))
        self.assertIn("ep_empty", str(caught.exception))

    def test_a_child_whose_parent_id_differs_from_its_directory_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_log(root, [start("ep_root")])
            write_log(root / "children" / "ep_stray", [start("ep_stray", "ep_other")])
            with self.assertRaises(ValueError) as caught:
                normalize_foe.normalize(root)
        self.assertIn("ep_other", str(caught.exception))
        self.assertIn("ep_root", str(caught.exception))

    def test_an_unknown_outcome_kind_is_refused_with_its_seq(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_log(Path(tmp), [start("ep_o"), event(1, 2, "episode/end", {"outcome": {"kind": "vanished"}})])
            with self.assertRaises(ValueError) as caught:
                normalize_foe.normalize(Path(tmp))
        self.assertIn("seq 1", str(caught.exception))
        self.assertIn("outcome.kind", str(caught.exception))


class Conformance(unittest.TestCase):
    def test_the_trace_quality_report_is_returned_for_a_tree(self) -> None:
        report = normalize_foe.trace_conformance(FIXTURES / "spawn-child")
        self.assertTrue(report["valid"])
        self.assertEqual(report["observations"]["episodes"], 2)
        self.assertEqual(report["observations"]["child_episodes"], 1)
        self.assertIn("hierarchical_budgets", report["metrics"])

    def test_a_report_with_violations_is_still_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_log(Path(tmp), [start("ep_v")])
            report = normalize_foe.trace_conformance(Path(tmp))
        self.assertFalse(report["valid"])
        self.assertTrue(report["violations"])

    def test_a_script_that_fails_or_prints_no_report_is_an_error_naming_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            failing = Path(tmp) / "failing.py"
            failing.write_text("import sys\nsys.stderr.write('no such tree\\n')\nsys.exit(3)\n", encoding="utf-8")
            with self.assertRaises(RuntimeError) as caught:
                normalize_foe.trace_conformance(FIXTURES / "blocked", failing)
            self.assertIn(str(failing), str(caught.exception))
            self.assertIn("no such tree", str(caught.exception))
            silent = Path(tmp) / "silent.py"
            silent.write_text("print('not a report')\n", encoding="utf-8")
            with self.assertRaises(RuntimeError) as caught:
                normalize_foe.trace_conformance(FIXTURES / "blocked", silent)
            self.assertIn(str(silent), str(caught.exception))
            bare = Path(tmp) / "bare.py"
            bare.write_text("print('[]')\n", encoding="utf-8")
            with self.assertRaises(RuntimeError) as caught:
                normalize_foe.trace_conformance(FIXTURES / "blocked", bare)
            self.assertIn("valid", str(caught.exception))


class CommandLine(unittest.TestCase):
    def test_the_command_prints_the_trajectory_and_the_report_on_request(self) -> None:
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = normalize_foe.main([str(FIXTURES / "blocked"), "--conformance", "--route", "subscription"])
        self.assertEqual(status, 0)
        document = json.loads(out.getvalue())
        self.assertEqual(document["trajectory"]["route"], "subscription")
        self.assertEqual(document["trajectory"]["outcome"]["code"], "missing-capability")
        self.assertIn("valid", document["conformance"])

    def test_a_missing_directory_exits_2_with_its_path(self) -> None:
        import contextlib
        import io

        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(err):
            status = normalize_foe.main([str(Path(tmp) / "absent")])
        self.assertEqual(status, 2)
        self.assertIn("absent", err.getvalue())


if __name__ == "__main__":
    unittest.main()
