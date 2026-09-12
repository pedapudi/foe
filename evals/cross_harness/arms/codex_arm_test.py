#!/usr/bin/python3
"""Unit tests for the Codex arm: a fake codex script stands in for the binary, and no login or network is used."""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_arm  # noqa: E402
import codex_budget_watcher  # noqa: E402

# A stand-in for `codex exec --json`. It reads the options the arm passes,
# prints the documented event lines, writes the last message the prompt
# directs, and writes one session file of the documented shape under the
# CODEX_HOME it received.
FAKE_CODEX = textwrap.dedent(
    """\
    #!/usr/bin/python3
    import json, os, sys, time
    args = sys.argv[1:]
    assert args[0] == "exec" and "--json" in args
    prompt = args[args.index("--") + 1]
    last = args[args.index("-o") + 1]
    schema = json.loads(open(args[args.index("--output-schema") + 1], encoding="utf-8").read())
    workspace = args[args.index("-C") + 1]
    home = os.environ["CODEX_HOME"]
    assert os.path.isfile(os.path.join(home, "auth.json")), "the credential file is absent"
    thread = "01a0913e-868c-7903-bd92-5813cb46712c"
    usage = {"input_tokens": 5000, "cached_input_tokens": 100, "cache_write_input_tokens": 0, "output_tokens": 40, "reasoning_output_tokens": 10}
    sessions = os.path.join(home, "sessions", "2026", "09", "11")
    os.makedirs(sessions)
    with open(os.path.join(sessions, "rollout-2026-09-11T09-13-09-" + thread + ".jsonl"), "w", encoding="utf-8") as session:
        session.write(json.dumps({"timestamp": "2026-09-11T16:13:09.148Z", "type": "session_meta", "payload": {"id": thread, "cli_version": "0.153.4", "model_provider": "fixture", "source": "exec", "cwd": workspace}}) + "\\n")
        session.write(json.dumps({"timestamp": "2026-09-11T16:13:09.200Z", "type": "turn_context", "payload": {"model": "fixture-model", "sandbox_policy": {"type": "workspace-write"}, "approval_policy": "never", "cwd": workspace}}) + "\\n")
        session.write(json.dumps({"timestamp": "2026-09-11T16:13:13.276Z", "type": "token_usage_record", "payload": {"thread_id": thread, "response_id": "resp_1", "usage": usage}}) + "\\n")
        session.flush()
        os.fsync(session.fileno())
    print(json.dumps({"type": "thread.started", "thread_id": thread}), flush=True)
    print(json.dumps({"type": "turn.started"}), flush=True)
    print("Reading additional input from stdin...", file=sys.stderr, flush=True)
    if prompt.startswith("exhaust"):
        time.sleep(60)
    if prompt.startswith("fail"):
        print(json.dumps({"type": "turn.failed", "error": {"message": "the model refused the request"}}), flush=True)
        sys.exit(1)
    print(json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "command_execution", "command": "python3 -m pytest", "aggregated_output": "1 passed", "exit_code": 0, "status": "completed"}}), flush=True)
    messages = {
        "complete": {"status": "completed", "code": None, "evidence": ["The visible test passes.", "The lint is clean."]},
        "block": {"status": "blocked", "code": "missing-capability", "evidence": ["The task needs network access."]},
        "prose": "I finished the task.",
    }
    message = messages[prompt.split()[0]]
    with open(last, "w", encoding="utf-8") as handle:
        handle.write(message if isinstance(message, str) else json.dumps(message))
    print(json.dumps({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": message if isinstance(message, str) else json.dumps(message)}}), flush=True)
    print(json.dumps({"type": "turn.completed", "usage": usage}), flush=True)
    sys.exit(0)
    """
)


class CommandLine(unittest.TestCase):
    def spec(self, **overrides) -> codex_arm.CodexSpec:
        fields = dict(
            arm_name="codex-test",
            codex=Path("/usr/local/bin/codex"),
            task="Fix the parser.",
            workspace=Path("/ws"),
            artifacts=Path("/art"),
            sandbox="workspace-write",
            model="fixture-model",
            reasoning_effort="low",
            credential_source=Path("/cred/auth.json"),
            limits={"input_tokens": 1000, "seconds": 30},
        )
        fields.update(overrides)
        return codex_arm.CodexSpec(**fields)

    def test_the_command_carries_every_documented_option_in_order(self) -> None:
        command = codex_arm.command_line(self.spec(), Path("/art/schema.json"), Path("/art/last.txt"))
        self.assertEqual(
            command,
            [
                "/usr/local/bin/codex", "exec", "--json", "--skip-git-repo-check", "--ignore-rules", "--ignore-user-config",
                "-s", "workspace-write", "-m", "fixture-model", "-c", 'model_reasoning_effort="low"', "-c", 'approval_policy="never"',
                "-C", "/ws", "-o", "/art/last.txt", "--output-schema", "/art/schema.json", "-c", "agents.enabled=false", "--", "Fix the parser.",
            ],
        )

    def test_agents_enabled_with_a_thread_bound_replaces_the_disable(self) -> None:
        command = codex_arm.command_line(self.spec(agents_enabled=True, max_threads=3), Path("/s"), Path("/l"))
        self.assertIn("agents.max_concurrent_threads_per_session=3", command)
        self.assertNotIn("agents.enabled=false", command)
        unbounded = codex_arm.command_line(self.spec(agents_enabled=True), Path("/s"), Path("/l"))
        self.assertFalse(any(part.startswith("agents.") for part in unbounded))

    def test_a_model_provider_override_sets_every_key_and_selects_the_provider(self) -> None:
        providers = {"local": {"name": "Local", "base_url": "http://127.0.0.1:8000/v1", "wire_api": "chat", "supports_websockets": False}}
        command = codex_arm.command_line(self.spec(model_providers=providers), Path("/s"), Path("/l"))
        overrides = [command[index + 1] for index, part in enumerate(command) if part == "-c"]
        self.assertIn('model_providers.local.base_url="http://127.0.0.1:8000/v1"', overrides)
        self.assertIn("model_providers.local.supports_websockets=false", overrides)
        self.assertIn('model_provider="local"', overrides)
        self.assertEqual(overrides[-1], 'model_provider="local"')

    def test_toml_values_are_quoted_by_type(self) -> None:
        self.assertEqual(codex_arm.toml_value('say "hi"', "k"), '"say \\"hi\\""')
        # A character outside the Basic Multilingual Plane stays literal,
        # because a TOML basic string rejects a surrogate pair; U+007F and the
        # characters below U+0020 are escaped, because TOML requires it.
        self.assertEqual(codex_arm.toml_value("\U0001F600", "k"), '"\U0001F600"')
        self.assertEqual(codex_arm.toml_value("a\x7fb\nc", "k"), '"a\\u007Fb\\nc"')
        self.assertEqual(codex_arm.toml_value(True, "k"), "true")
        self.assertEqual(codex_arm.toml_value(7, "k"), "7")
        with self.assertRaises(ValueError) as caught:
            codex_arm.toml_value([1], "model_providers.x.list")
        self.assertIn("model_providers.x.list", str(caught.exception))

    def test_the_spec_refuses_a_wrong_sandbox_two_providers_and_bad_limits(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self.spec(sandbox="open")
        self.assertIn("sandbox", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            self.spec(model_providers={"a": {}, "b": {}})
        self.assertIn("model_providers", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            self.spec(limits={"model_calls": 3})
        self.assertIn("model_calls", str(caught.exception))
        with self.assertRaises(ValueError):
            self.spec(max_threads=0)

    def test_the_default_schema_names_the_two_statuses_and_the_three_codes(self) -> None:
        schema = codex_arm.DEFAULT_SCHEMA
        self.assertEqual(schema["properties"]["status"]["enum"], ["completed", "blocked"])
        self.assertEqual(schema["properties"]["code"]["enum"], ["goal-unreachable", "ambiguous-task", "missing-capability", None])
        self.assertEqual(sorted(schema["required"]), ["code", "evidence", "status"])


class Reading(unittest.TestCase):
    def test_events_are_parsed_and_the_rest_reported(self) -> None:
        text = '{"type": "thread.started", "thread_id": "t1"}\nnot json\n{"no": "type"}\n{"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 1}}\n'
        events, problems = codex_arm.parse_events(text)
        self.assertEqual([event["type"] for event in events], ["thread.started", "turn.completed"])
        self.assertEqual(len(problems), 2)
        self.assertTrue(problems[0].startswith("events.jsonl:2"))

    def test_the_summary_sums_usage_and_collects_errors(self) -> None:
        events = [
            {"type": "thread.started", "thread_id": "t1"},
            {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 1}},
            {"type": "item.completed", "item": {"type": "error", "message": "tool missing"}},
            {"type": "error", "message": "Reconnecting... 1/5"},
            {"type": "turn.failed", "error": {"message": "refused"}},
            {"type": "turn.completed", "usage": {"input_tokens": 4, "cached_input_tokens": 2, "output_tokens": 2}},
        ]
        summary = codex_arm.summarize_events(events)
        self.assertEqual(summary["thread_id"], "t1")
        self.assertEqual(summary["turns"], 3)
        self.assertEqual(summary["usage"]["input_tokens"], 7)
        self.assertEqual(summary["usage"]["cached_input_tokens"], 2)
        self.assertEqual(summary["errors"], ["tool missing", "Reconnecting... 1/5", "refused"])
        self.assertEqual(summary["items"], {"error": 1})
        self.assertEqual(summary["problems"], [])

    def test_an_unreadable_usage_is_a_recorded_problem_rather_than_an_error(self) -> None:
        summary = codex_arm.summarize_events([{"type": "turn.completed", "usage": {"input_tokens": 3.0}}, {"type": "turn.completed", "usage": {"input_tokens": 2}}])
        self.assertEqual(summary["turns"], 2)
        self.assertEqual(summary["usage"]["input_tokens"], 2)
        self.assertEqual(len(summary["problems"]), 1)
        self.assertIn("input_tokens", summary["problems"][0])

    def test_the_last_message_is_read_against_the_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            last = Path(tmp) / "last.txt"
            last.write_text(json.dumps({"status": "blocked", "code": "ambiguous-task", "evidence": ["two readings"]}), encoding="utf-8")
            outcome, candidate, problems = codex_arm.read_last_message(last, codex_arm.DEFAULT_SCHEMA)
            self.assertEqual(outcome, {"status": "blocked", "code": "ambiguous-task", "evidence": ["two readings"]})
            self.assertEqual(candidate["status"], "blocked")
            self.assertEqual(problems, [])

            last.write_text(json.dumps({"status": "completed", "code": "goal-unreachable", "evidence": "done"}), encoding="utf-8")
            outcome, _, problems = codex_arm.read_last_message(last, codex_arm.DEFAULT_SCHEMA)
            self.assertEqual(outcome, {"status": "completed", "code": None, "evidence": ["done"]})
            self.assertEqual(len(problems), 2)

            last.write_text(json.dumps({"status": "finished", "code": None, "evidence": []}), encoding="utf-8")
            outcome, _, problems = codex_arm.read_last_message(last, codex_arm.DEFAULT_SCHEMA)
            self.assertEqual(outcome["status"], "failed")
            self.assertIn("finished", problems[0])

            # A status the schema omits is failed even when it is a status an
            # arm can report, because the watcher decides exhaustion.
            for claimed in ("exhausted", "killed", "failed"):
                last.write_text(json.dumps({"status": claimed, "code": None, "evidence": []}), encoding="utf-8")
                outcome, candidate, problems = codex_arm.read_last_message(last, codex_arm.DEFAULT_SCHEMA)
                self.assertEqual(outcome, {"status": "failed", "code": None, "evidence": []})
                self.assertEqual(candidate["status"], claimed)
                self.assertIn(claimed, problems[0])

            # A code the schema omits, such as one foe detects at runtime, is dropped.
            last.write_text(json.dumps({"status": "blocked", "code": "looping-tool-call", "evidence": []}), encoding="utf-8")
            outcome, _, problems = codex_arm.read_last_message(last, codex_arm.DEFAULT_SCHEMA)
            self.assertEqual(outcome, {"status": "blocked", "code": None, "evidence": []})
            self.assertEqual(len(problems), 1)
            self.assertIn("looping-tool-call", problems[0])

            # A schema without a code enumeration accepts any code on a blocked status.
            open_schema = {"properties": {"status": {"enum": ["completed", "blocked"]}, "code": {"type": ["string", "null"]}}}
            outcome, _, problems = codex_arm.read_last_message(last, open_schema)
            self.assertEqual((outcome["code"], problems), ("looping-tool-call", []))

            last.write_text("I finished.", encoding="utf-8")
            outcome, candidate, problems = codex_arm.read_last_message(last, codex_arm.DEFAULT_SCHEMA)
            self.assertIsNone(outcome)
            self.assertEqual(candidate, "I finished.")
            self.assertIn(str(last), problems[0])

            outcome, candidate, problems = codex_arm.read_last_message(Path(tmp) / "absent.txt", codex_arm.DEFAULT_SCHEMA)
            self.assertIsNone(outcome)
            self.assertIn("absent", problems[0])

    def test_a_watcher_stop_outranks_the_message_and_a_signal_is_killed(self) -> None:
        summary = codex_arm.summarize_events([])
        with tempfile.TemporaryDirectory() as tmp:
            last = Path(tmp) / "last.txt"
            last.write_text(json.dumps({"status": "completed", "code": None, "evidence": []}), encoding="utf-8")
            stop = codex_budget_watcher.Stop("input_tokens reached 5000 tokens against a limit of 1000 tokens after 0.3 s", "input_tokens", 5000, 1000, 0)
            outcome, candidate, _ = codex_arm.interpret(stop, None, last, codex_arm.DEFAULT_SCHEMA, summary, "")
            self.assertEqual(outcome, {"status": "exhausted", "code": "input_tokens", "evidence": [stop.reason]})
            self.assertIsNone(candidate)
            outcome, _, _ = codex_arm.interpret(None, -9, last, codex_arm.DEFAULT_SCHEMA, summary, "")
            self.assertEqual(outcome["status"], "killed")
            self.assertIn("signal 9", outcome["evidence"][0])
            outcome, _, _ = codex_arm.interpret(None, 0, last, codex_arm.DEFAULT_SCHEMA, summary, "")
            self.assertEqual(outcome["status"], "completed")

    def test_a_run_without_a_message_failed_with_every_diagnostic(self) -> None:
        summary = codex_arm.summarize_events([{"type": "turn.failed", "error": {"message": "refused"}}])
        with tempfile.TemporaryDirectory() as tmp:
            outcome, _, _ = codex_arm.interpret(None, 1, Path(tmp) / "last.txt", codex_arm.DEFAULT_SCHEMA, summary, "stderr text\n")
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("exit status 1", outcome["evidence"])
        self.assertIn("refused", outcome["evidence"])
        self.assertEqual(outcome["evidence"][-1], "stderr text")


class Running(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="codex-arm-test-")
        self.root = Path(self.temporary.name)
        self.binary = self.root / "fake-codex"
        self.binary.write_text(FAKE_CODEX, encoding="utf-8")
        self.binary.chmod(0o755)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.credential = self.root / "source-auth.json"
        self.credential.write_text('{"tokens": {"access_token": "placeholder"}}\n', encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def spec(self, task: str, limits: dict | None = None, artifacts: str = "artifacts", keep_credential: bool = False) -> codex_arm.CodexSpec:
        return codex_arm.CodexSpec(
            arm_name="codex-test",
            codex=self.binary,
            task=task,
            workspace=self.workspace,
            artifacts=self.root / artifacts,
            sandbox="workspace-write",
            model="fixture-model",
            reasoning_effort="low",
            credential_source=self.credential,
            limits=limits or {"input_tokens": 100_000, "seconds": 60},
            environment={"PATH": "/usr/bin:/bin"},
            keep_credential=keep_credential,
        )

    def test_a_completed_run_reports_the_message_and_records_the_home_and_sessions(self) -> None:
        result = codex_arm.run(self.spec("complete the task"))
        self.assertEqual(result.harness, "codex")
        self.assertEqual(result.exit_status, 0)
        self.assertEqual(result.reported, {"status": "completed", "code": None, "evidence": ["The visible test passes.", "The lint is clean."]})
        self.assertEqual(result.candidate["status"], "completed")
        artifacts = self.root / "artifacts"
        home = artifacts / "codex-home"
        self.assertEqual(result.record["codex_home"], str(home))
        # The fake codex saw the copy while it ran; the arm removed it once the process had exited.
        self.assertFalse((home / "auth.json").exists())
        self.assertEqual(result.record["credential_copy"], str(home / "auth.json"))
        self.assertTrue(result.record["credential_removed"])
        self.assertEqual(len(result.record["session_files"]), 1)
        self.assertTrue(result.record["session_files"][0].startswith(str(home / "sessions")))
        self.assertEqual(result.record["thread_id"], "01a0913e-868c-7903-bd92-5813cb46712c")
        self.assertEqual(result.record["turns"], 1)
        self.assertEqual(result.record["usage"]["input_tokens"], 5000)
        self.assertEqual(result.record["items"], {"command_execution": 1, "agent_message": 1})
        self.assertIsNone(result.record["stop"])
        self.assertEqual(result.record["problems"], [])
        command = result.record["commands"][0]
        self.assertEqual(command[:2], [str(self.binary), "exec"])
        self.assertEqual(command[-1], "complete the task")
        self.assertIn(str(artifacts / "schema.json"), command)
        self.assertEqual(json.loads((artifacts / "schema.json").read_text(encoding="utf-8")), codex_arm.DEFAULT_SCHEMA)
        events = [json.loads(line) for line in (artifacts / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[0]["type"], "thread.started")
        self.assertEqual(events[-1]["type"], "turn.completed")
        self.assertIn("Reading additional input", (artifacts / "stderr.txt").read_text(encoding="utf-8"))

    def test_the_credential_copy_is_removed_unless_the_caller_keeps_it(self) -> None:
        home = self.root / "artifacts" / "codex-home"
        result = codex_arm.run(self.spec("complete the task"))
        self.assertTrue(result.record["credential_removed"])
        self.assertFalse((home / "auth.json").exists())
        # The rest of the home, the session files among it, stays for normalization.
        self.assertTrue(home.is_dir())
        self.assertTrue(Path(result.record["session_files"][0]).is_file())
        # Nothing the arm wrote holds the credential's contents.
        secret = json.loads(self.credential.read_text(encoding="utf-8"))["tokens"]["access_token"]
        for path in (self.root / "artifacts").rglob("*"):
            if path.is_file():
                self.assertNotIn(secret, path.read_text(encoding="utf-8", errors="replace"), path)
        self.assertNotIn(secret, json.dumps(result.to_dict()))

        kept = codex_arm.run(self.spec("complete the task", artifacts="kept", keep_credential=True))
        self.assertFalse(kept.record["credential_removed"])
        self.assertEqual((self.root / "kept" / "codex-home" / "auth.json").read_text(encoding="utf-8"), self.credential.read_text(encoding="utf-8"))

        # A copy the run itself removed leaves nothing to remove; a copy that cannot be removed is an error naming it.
        self.assertEqual(codex_arm.remove_credential(home), home / "auth.json")
        locked = self.root / "locked"
        (locked / "auth.json").mkdir(parents=True)
        (locked / "auth.json" / "inner").write_text("", encoding="utf-8")
        with self.assertRaises(OSError) as caught:
            codex_arm.remove_credential(locked)
        self.assertIn(str(locked / "auth.json"), str(caught.exception))

    def test_a_blocked_run_reports_the_code(self) -> None:
        result = codex_arm.run(self.spec("block the task"))
        self.assertEqual(result.reported, {"status": "blocked", "code": "missing-capability", "evidence": ["The task needs network access."]})

    def test_a_prose_last_message_is_a_failed_report_with_the_text_as_candidate(self) -> None:
        result = codex_arm.run(self.spec("prose reply"))
        self.assertEqual(result.reported["status"], "failed")
        self.assertEqual(result.candidate, "I finished the task.")
        self.assertTrue(any("not JSON" in line for line in result.record["problems"]))

    def test_a_failed_turn_without_a_message_is_failed_with_its_error(self) -> None:
        result = codex_arm.run(self.spec("fail the task"))
        self.assertEqual(result.exit_status, 1)
        self.assertEqual(result.reported["status"], "failed")
        self.assertIn("the model refused the request", result.reported["evidence"])
        self.assertEqual(result.record["errors"], ["the model refused the request"])

    def test_a_stale_last_message_is_removed_before_the_run(self) -> None:
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        (artifacts / "last.txt").write_text(json.dumps({"status": "completed", "code": None, "evidence": ["from an earlier run"]}), encoding="utf-8")
        result = codex_arm.run(self.spec("fail the task"))
        self.assertEqual(result.reported["status"], "failed")
        self.assertFalse((artifacts / "last.txt").exists())
        self.assertTrue(any("absent" in line for line in result.reported["evidence"]))

    def test_a_run_over_the_token_limit_is_stopped_and_reported_exhausted(self) -> None:
        result = codex_arm.run(self.spec("exhaust the budget", limits={"input_tokens": 1000, "seconds": 60}))
        self.assertEqual((result.reported["status"], result.reported["code"]), ("exhausted", "input_tokens"))
        self.assertIsNone(result.exit_status)
        self.assertEqual(result.record["stop"]["dimension"], "input_tokens")
        self.assertEqual(result.record["stop"]["observed"], 5000)
        self.assertLess(result.ended_ms - result.started_ms, 30_000)
        # A run the watcher stopped has its credential copy removed like any other.
        self.assertTrue(result.record["credential_removed"])
        self.assertFalse((self.root / "artifacts" / "codex-home" / "auth.json").exists())

    def test_a_run_over_the_seconds_limit_is_reported_exhausted_on_seconds(self) -> None:
        result = codex_arm.run(self.spec("exhaust the clock", limits={"input_tokens": 100_000, "seconds": 1}))
        self.assertEqual((result.reported["status"], result.reported["code"]), ("exhausted", "seconds"))
        self.assertLess(result.ended_ms - result.started_ms, 30_000)

    def test_a_home_that_exists_or_a_credential_that_does_not_is_refused_by_path(self) -> None:
        (self.root / "used" / "codex-home").mkdir(parents=True)
        with self.assertRaises(FileExistsError) as caught:
            codex_arm.run(self.spec("complete the task", artifacts="used"))
        self.assertIn(str(self.root / "used" / "codex-home"), str(caught.exception))
        self.credential.unlink()
        with self.assertRaises(FileNotFoundError) as caught:
            codex_arm.run(self.spec("complete the task", artifacts="fresh"))
        self.assertIn(str(self.credential), str(caught.exception))

    def test_a_config_canary_is_written_into_the_home_as_developer_instructions_and_named_by_the_record(self) -> None:
        sentence = "This sentence is the codex config isolation canary 00000000-0000-4000-8000-000000000000; a request that carries it was built from a file the harness must never read."
        spec = codex_arm.CodexSpec(**{**self.spec("complete the task").__dict__, "config_canary": sentence})
        result = codex_arm.run(spec)
        home = self.root / "artifacts" / "codex-home"
        self.assertEqual(result.record["config_canary_file"], str(home / codex_arm.CONFIG_CANARY_NAME))
        # The file is the user configuration `--ignore-user-config` keeps Codex from loading, and the command line passes that flag.
        self.assertEqual(codex_arm.CONFIG_CANARY_NAME, "config.toml")
        self.assertIn("--ignore-user-config", result.record["commands"][0])
        written = (home / "config.toml").read_text(encoding="utf-8")
        self.assertIn(f'\ndeveloper_instructions = "{sentence}"\n', written)
        self.assertTrue(all(line.startswith("#") or line.startswith("developer_instructions = ") for line in written.splitlines()), written)
        # The home holds no global instructions file, which Codex loads in every session whatever the flags.
        self.assertFalse((home / "AGENTS.md").exists())
        # The home keeps the canary after the run, beside the session files, so the gate can confirm it was planted.
        self.assertTrue((home / "config.toml").is_file())
        self.assertFalse((home / "auth.json").exists())
        plain = codex_arm.run(self.spec("complete the task", artifacts="plain"))
        self.assertIsNone(plain.record["config_canary_file"])
        self.assertFalse((self.root / "plain" / "codex-home" / "config.toml").exists())
        with self.assertRaises(ValueError) as caught:
            codex_arm.CodexSpec(**{**self.spec("complete the task").__dict__, "config_canary": "  "})
        self.assertIn("spec config_canary is empty", str(caught.exception))
        # A sentence with a quotation mark or a backslash is escaped as a TOML basic string.
        self.assertEqual(codex_arm.config_canary_text('say "no" \\ once').splitlines()[-1], 'developer_instructions = "say \\"no\\" \\\\ once"')


if __name__ == "__main__":
    unittest.main()
