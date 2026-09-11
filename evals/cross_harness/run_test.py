#!/usr/bin/python3
"""Unit tests for the runner: fake foe and codex scripts stand in for the binaries, and no model is called."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run  # noqa: E402

EXAMPLES = Path(__file__).resolve().parent / "tasks" / "examples"
TASK = "hello-solvable"

# The solution the example task's hidden grader accepts.
SOLUTION = textwrap.dedent(
    '''\
    """Greetings for the command-line front end."""


    def greet(name):
        """Return the greeting for a name; an empty name greets a stranger."""
        trimmed = name.strip()
        return "Hello, " + (trimmed or "stranger") + "!"
    '''
)

# A stand-in for the foe binary. It reads its behaviour from a file beside
# itself, accepts both the document form and the built-in form of the running
# command line, writes an episode log in the shape docs/log-format.md fixes
# with one bash call whose interval brackets the file it writes, and prints
# the outcome line. A document that declares the `check` tool has it run
# with an empty environment, as the runtime runs a configured executable,
# and its output recorded as a tool result.
FAKE_FOE = textwrap.dedent(
    """\
    #!/usr/bin/python3
    import json, os, subprocess, sys, time
    args = sys.argv[1:]
    here = os.path.dirname(os.path.abspath(__file__))
    behaviour = open(os.path.join(here, "behaviour.txt"), encoding="utf-8").read().strip()
    config_name = args[args.index("--config") + 1]
    log_dir = args[args.index("--log-dir") + 1]
    assert args[args.index("--viewer") + 1] == "off"
    check_exec = None
    if config_name.startswith("builtin:"):
        task = args[0]
        workspace = os.getcwd()
        provider, _, model = args[args.index("--model") + 1].partition("/")
        contract = {"name": config_name, "model": {"provider": provider, "model": model}, "grants": {"read": [workspace], "write": [workspace]}}
    else:
        config = json.load(open(config_name, encoding="utf-8"))
        task = config["task"]
        workspace = config["grants"]["read"][0]
        contract = {"name": config["name"], "model": config["model"], "grants": config["grants"]}
        check_exec = config.get("tool_defs", {}).get("check", {}).get("exec")
    if behaviour == "silent":
        print("foe: the document is invalid: key tools is absent", file=sys.stderr)
        sys.exit(1)
    episode = os.path.join(log_dir, "ep_fake")
    os.makedirs(episode)
    now = lambda: int(time.time() * 1000)
    events = []
    def event(kind, data, at=None):
        events.append({"seq": len(events), "time": now() if at is None else at, "version": 3, "type": kind, "data": data})
    event("episode/start", {"id": "ep_fake", "parent_id": None, "contract": contract, "contract_fingerprint": "sha256:fake", "task": task,
                            "runtime": {"version": "0.0", "build": "sha256:fake"}, "sandbox": {"mode": "off", "landlock_abi": 0}})
    event("model/request", {"request_id": "rq_1", "step": 1})
    command = "python3 fix.py src/greeting.py"
    tool_calls = [{"id": "call-1", "name": "bash", "args": {"command": command}}]
    if check_exec:
        tool_calls.append({"id": "call-2", "name": "check", "args": {}})
    event("assistant/message", {"request_id": "rq_1", "step": 1, "text": "", "stop": "tool", "usage": {"input": 1200, "output": 80, "cache_read": 0},
                                "tool_calls": tool_calls})
    started = now()
    time.sleep(0.02)
    if behaviour == "completed":
        with open(os.path.join(workspace, "src", "greeting.py"), "w", encoding="utf-8") as handle:
            handle.write(%(solution)r)
    time.sleep(0.02)
    ended = now()
    event("tool/result", {"step": 1, "call_id": "call-1", "name": "bash", "is_error": False, "duration_ms": ended - started,
                          "value": {"command": command, "exit_code": 0, "stdout": "", "stderr": "", "permission_denial": None}}, at=ended)
    if check_exec:
        checked = subprocess.run([check_exec], env={}, capture_output=True, text=True)
        event("tool/result", {"step": 1, "call_id": "call-2", "name": "check", "is_error": False, "duration_ms": 1,
                              "value": {"exit_code": checked.returncode, "stdout": checked.stdout, "stderr": checked.stderr, "timed_out": False}})
    if behaviour == "completed":
        outcome = {"kind": "completed", "value": {"summary": "done", "learned": [{"seq": 3, "claim": "The tests pass."}]}}
        status = 0
    else:
        outcome = {"kind": "blocked", "code": "goal-unreachable", "message": "The requirements conflict."}
        status = 2
    event("episode/end", {"outcome": outcome})
    with open(os.path.join(episode, "episode.jsonl"), "w", encoding="utf-8") as log:
        for item in events:
            log.write(json.dumps(item) + "\\n")
    print("foe: log " + episode, file=sys.stderr, flush=True)
    print(json.dumps(outcome), flush=True)
    sys.exit(status)
    """
) % {"solution": SOLUTION}

# A stand-in for `codex exec --json`. It answers `--version`, reads its
# behaviour from a file beside itself, writes the solution during one
# recorded command, writes one session file of the documented shape under the
# CODEX_HOME it received, prints the event lines, and writes the last message.
# The behaviour `malformed-session` writes a session_meta whose payload is a
# list, a shape the normalizer does not read. The behaviour `exhaust` sleeps
# after writing the session file, whose usage exceeds a small token limit,
# so that the budget watcher stops the run.
FAKE_CODEX = textwrap.dedent(
    """\
    #!/usr/bin/python3
    import json, os, sys, time
    from datetime import datetime, timezone
    args = sys.argv[1:]
    if args == ["--version"]:
        print("codex-cli 0.153.4")
        sys.exit(0)
    here = os.path.dirname(os.path.abspath(__file__))
    behaviour = open(os.path.join(here, "behaviour.txt"), encoding="utf-8").read().strip()
    assert args[0] == "exec" and "--json" in args
    prompt = args[args.index("--") + 1]
    last = args[args.index("-o") + 1]
    workspace = args[args.index("-C") + 1]
    home = os.environ["CODEX_HOME"]
    assert os.path.isfile(os.path.join(home, "auth.json"))
    thread = "01a0913e-868c-7903-bd92-5813cb46712c"
    now = lambda: int(time.time() * 1000)
    stamp = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    t0 = now()
    started = now()
    time.sleep(0.02)
    if behaviour == "completed":
        with open(os.path.join(workspace, "src", "greeting.py"), "w", encoding="utf-8") as handle:
            handle.write(%(solution)r)
    time.sleep(0.02)
    ended = now()
    usage = {"input_tokens": 5000, "cached_input_tokens": 100, "cache_write_input_tokens": 0, "output_tokens": 40, "reasoning_output_tokens": 10}
    meta = {"id": thread, "cli_version": "0.153.4", "model_provider": "openai", "source": "exec", "cwd": workspace}
    records = [
        {"timestamp": stamp(t0), "type": "session_meta", "payload": [1] if behaviour == "malformed-session" else meta},
        {"timestamp": stamp(t0), "type": "turn_context", "payload": {"model": "fixture-model", "sandbox_policy": {"type": "workspace-write"}, "approval_policy": "never", "cwd": workspace}},
        {"timestamp": stamp(ended), "type": "event_msg", "payload": {"type": "item_completed", "started_at_ms": started, "completed_at_ms": ended,
            "item": {"id": "item_0", "type": "CommandExecution", "command": ["bash", "-lc", "python3 fix.py src/greeting.py"], "aggregated_output": "", "exit_code": 0, "status": "completed"}}},
        {"timestamp": stamp(now()), "type": "token_usage_record", "ordinal": 3, "payload": {"thread_id": thread, "response_id": "resp_1", "usage": usage}},
    ]
    sessions = os.path.join(home, "sessions", "2026", "09", "11")
    os.makedirs(sessions)
    with open(os.path.join(sessions, "rollout-2026-09-11T09-13-09-" + thread + ".jsonl"), "w", encoding="utf-8") as session:
        for record in records:
            session.write(json.dumps(record) + "\\n")
        session.flush()
        os.fsync(session.fileno())
    print(json.dumps({"type": "thread.started", "thread_id": thread}), flush=True)
    print(json.dumps({"type": "turn.started"}), flush=True)
    if behaviour == "exhaust":
        time.sleep(60)
    if behaviour == "completed":
        message = {"status": "completed", "code": None, "evidence": ["The visible test passes."]}
    else:
        message = {"status": "blocked", "code": "goal-unreachable", "evidence": ["The requirements conflict."]}
    with open(last, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(message))
    print(json.dumps({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": json.dumps(message)}}), flush=True)
    print(json.dumps({"type": "turn.completed", "usage": usage}), flush=True)
    sys.exit(0)
    """
) % {"solution": SOLUTION}


class Harness(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="cross-harness-run-test-")
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.foe = self.bin / "fake-foe"
        self.foe.write_text(FAKE_FOE, encoding="utf-8")
        self.foe.chmod(0o755)
        self.codex = self.bin / "fake-codex"
        self.codex.write_text(FAKE_CODEX, encoding="utf-8")
        self.codex.chmod(0o755)
        self.credential = self.root / "auth.json"
        self.credential.write_text('{"tokens": {"access_token": "placeholder"}}\n', encoding="utf-8")
        self.out = self.root / "out"
        self.behave("completed")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def behave(self, behaviour: str) -> None:
        (self.bin / "behaviour.txt").write_text(behaviour + "\n", encoding="utf-8")

    def argv(self, *extra: str, arms: str = "foe-configured") -> list[str]:
        return [
            "--foe", str(self.foe), "--codex", str(self.codex), "--credential", str(self.credential),
            "--family", "autonomy", "--tasks", str(EXAMPLES), "--arms", arms, "--attempts", "1",
            "--route", "subscription", "--model", "fixture-model", "--out", str(self.out), *extra,
        ]

    def main(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = run.main(argv)
        return status, out.getvalue(), err.getvalue()

    def record(self, arm: str, attempt: int = 1) -> dict:
        return json.loads(run.record_path(self.out, TASK, arm, attempt).read_text(encoding="utf-8"))

    def tasks_with_metadata(self, metadata: dict) -> Path:
        """A copy of the example task directory whose task carries `metadata`."""
        import shutil

        tasks = self.root / "tasks"
        shutil.copytree(EXAMPLES / TASK, tasks / TASK)
        task_file = tasks / TASK / run.protocol.TASK_FILE
        task = json.loads(task_file.read_text(encoding="utf-8"))
        task["metadata"] = metadata
        task_file.write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
        return tasks


class Planning(Harness):
    def test_without_confirmation_the_plan_names_every_attempt_and_nothing_runs(self) -> None:
        status, out, _ = self.main(self.argv("--attempts", "2", arms="foe-configured,codex-default"))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        for attempt in (1, 2):
            for arm in ("foe-configured", "codex-default"):
                self.assertIn(f"{TASK} / {arm} / {attempt}", out)
        self.assertIn("every planned attempt", out)
        self.assertIn("No attempt was launched", out)
        # Four attempts at eight model calls, 16,000 input tokens, and 4,000 output tokens each.
        self.assertRegex(out, r"\n\s+32\s+64,000\s+16,000\s+1200\s+every planned attempt")
        self.assertFalse(self.out.exists())

    def test_the_arms_rotate_across_attempts(self) -> None:
        arms = list(run.ARMS["autonomy"])
        self.assertEqual([arm.name for arm in run.rotated(arms, 1)], [arm.name for arm in arms])
        self.assertEqual(run.rotated(arms, 2)[0].name, arms[1].name)
        self.assertEqual(run.rotated(arms, len(arms) + 1)[0].name, arms[0].name)
        triples = run.planned([run.Selected(EXAMPLES / TASK, run.protocol.load(EXAMPLES / TASK))], arms[:2], 2)
        self.assertEqual([(attempt, arm.name) for attempt, _, arm in triples], [(1, arms[0].name), (1, arms[1].name), (2, arms[1].name), (2, arms[0].name)])

    def test_bad_arguments_are_refused_by_name(self) -> None:
        status, _, err = self.main(self.argv(arms="foe-configured,codex-multi"))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("codex-multi", err)
        status, _, err = self.main(self.argv("--task", "absent-task"))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("absent-task", err)
        status, _, err = self.main(self.argv("--route", "compatible"))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("--base-url", err)
        status, _, err = self.main(["--foe", str(self.foe), "--family", "autonomy", "--tasks", str(EXAMPLES), "--arms", "codex-default", "--route", "subscription", "--model", "m", "--out", str(self.out)])
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("--codex and --credential", err)
        status, _, err = self.main(self.argv("--tool-root", str(self.root / "absent-root")))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("--tool-root", err)
        self.assertIn("absent-root", err)
        self.assertFalse(self.out.exists())

    def test_an_arms_value_naming_no_arm_is_refused(self) -> None:
        for extra in ((), ("--confirm-spend",)):
            status, _, err = self.main(self.argv(*extra, arms=","))
            self.assertEqual(status, run.NOTHING_LAUNCHED)
            self.assertIn("--arms ',' names no arm", err)
        self.assertFalse(self.out.exists())

    def test_a_budget_override_replaces_one_key_in_the_plan_and_is_refused_by_name_when_malformed(self) -> None:
        status, out, _ = self.main(self.argv("--attempts", "2", "--budget", "input_tokens=1000", "--budget", "seconds=7", arms="foe-configured,codex-default"))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        # Four attempts at eight model calls, 1,000 input tokens, 4,000 output tokens, and 7 seconds each.
        self.assertRegex(out, r"\n\s+8\s+1,000\s+4,000\s+7\s+hello-solvable / foe-configured / 1")
        self.assertRegex(out, r"\n\s+32\s+4,000\s+16,000\s+28\s+every planned attempt")
        self.assertIn("--budget replaces input_tokens=1000, seconds=7", out)
        self.assertIn("model calls are reported per arm", out)
        for bad, fragment in (
            ("input_tokens", "not of the form KEY=VALUE"),
            ("credits=3", "names 'credits', which is not a budget key"),
            ("seconds=many", "seconds='many' is not an integer"),
            ("seconds=0", "seconds=0 is not a positive integer"),
        ):
            status, _, err = self.main(self.argv("--budget", bad))
            self.assertEqual(status, run.NOTHING_LAUNCHED, bad)
            self.assertIn(fragment, err)
        status, _, err = self.main(self.argv("--budget", "seconds=5", "--budget", "seconds=6"))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("names seconds twice", err)
        self.assertEqual(run.parse_budget(None), {})
        self.assertFalse(self.out.exists())

    def test_a_budget_the_documents_refuse_is_refused_before_the_plan_and_before_any_launch(self) -> None:
        """A seconds ceiling with no room for the check timeout is refused
        by the document builders; the runner refuses it up front rather than
        planning attempts that would fault after other arms spent credit."""
        for extra in ((), ("--confirm-spend",)):
            status, out, err = self.main(self.argv(*extra, "--budget", "seconds=1", arms="codex-default,foe-configured"))
            self.assertEqual(status, run.NOTHING_LAUNCHED)
            self.assertIn("hello-solvable: the effective budget is refused: budget.seconds is 1", err)
            self.assertNotIn("every planned attempt", out)
        self.assertFalse(self.out.exists())

    def test_the_plan_records_a_task_with_tool_roots_as_not_applicable_to_the_shipped_arm(self) -> None:
        tools = self.root / "tools"
        tools.mkdir()
        tasks = self.tasks_with_metadata({"tool_roots": [str(tools)]})
        status, out, _ = self.main(self.argv("--tasks", str(tasks), arms="foe-configured,foe-as-shipped"))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("Recorded as not applicable and never launched:", out)
        self.assertIn(f"  {TASK} / foe-as-shipped / 1: the foe-as-shipped arm runs the built-in document builtin:coding, whose grants cannot take the tool roots task '{TASK}' names under metadata.tool_roots: {tools}", out)
        self.assertRegex(out, r"\n\s+8\s+16,000\s+4,000\s+300\s+hello-solvable / foe-configured / 1")
        # The totals count the attempt that runs alone.
        self.assertRegex(out, r"\n\s+8\s+16,000\s+4,000\s+300\s+every planned attempt")

    def test_the_plan_states_the_tool_roots_of_the_document_arms(self) -> None:
        tools = self.root / "tools"
        tools.mkdir()
        status, out, _ = self.main(self.argv("--tool-root", str(tools)))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn(f"  {TASK}: {', '.join([*run.graphs.EXECUTE_ROOTS, str(tools)])}", out)
        self.assertIn("--tool-root", out)
        status, out, _ = self.main(self.argv(arms="codex-default"))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertNotIn("executes:", out)


class Pieces(unittest.TestCase):
    def setUp(self) -> None:
        self.task = run.protocol.load(EXAMPLES / TASK)

    def test_the_check_command_prefers_metadata_then_the_suite_then_unit_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with self.assertRaises(ValueError) as caught:
                run.check_command(self.task, workspace)
            self.assertIn(run.CHECK_SUITE, str(caught.exception))
            (workspace / "tests").mkdir()
            self.assertEqual(run.check_command(self.task, workspace)[:3], ["/usr/bin/python3", "-B", "-m"])
            (workspace / "checks").mkdir()
            (workspace / "checks" / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            self.assertEqual(run.check_command(self.task, workspace), ["/usr/bin/bash", "checks/run.sh"])
            named = run.protocol.Task.from_dict({**self.task.to_dict(), "metadata": {"check": "make check"}})
            self.assertEqual(run.check_command(named, workspace), ["/usr/bin/bash", "-c", "make check"])

    def test_the_check_script_turns_a_failing_command_into_findings_and_exits_zero(self) -> None:
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            script = run.write_check_script(Path(tmp) / "check", workspace, ["/bin/sh", "-c", "echo broken; exit 3"])
            completed = subprocess.run([str(script)], capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout.splitlines()[0], "broken")
            self.assertIn("exited 3", completed.stdout)
            passing = run.write_check_script(Path(tmp) / "pass", workspace, ["/bin/true"])
            self.assertEqual(subprocess.run([str(passing)], capture_output=True, text=True, check=False).stdout, "")

    def test_the_check_script_sets_its_search_path_and_reports_a_command_it_cannot_find(self) -> None:
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            tools = Path(tmp) / "tools"
            tools.mkdir()
            tool = tools / "cross-harness-fixture-tool"
            tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            tool.chmod(0o755)
            command = [tool.name, "--version"]
            # The runtime starts a configured executable with an empty environment, so each run here does too.
            found = run.write_check_script(Path(tmp) / "found", workspace, command, [str(tools), str(tool), str(Path(tmp) / "absent")])
            self.assertIn(f"PATH={run.SYSTEM_SEARCH_PATH}:{tools}\n", found.read_text(encoding="utf-8"))
            self.assertEqual(subprocess.run([str(found)], capture_output=True, text=True, check=False, env={}).stdout, "")
            # The suite runs from the workspace with a scratch directory of its own as TMPDIR, tagged as a cache.
            environment = run.write_check_script(Path(tmp) / "environment", workspace, ["/bin/sh", "-c", 'pwd; echo "$TMPDIR"; echo "$LANG"; touch "$TMPDIR/scratch"; exit 1'])
            printed = subprocess.run([str(environment)], capture_output=True, text=True, check=False, env={}).stdout.splitlines()
            scratch = workspace / run.CHECK_SCRATCH_DIR
            self.assertEqual(printed[:3], [str(workspace), str(scratch), "C.UTF-8"])
            self.assertEqual((scratch / run.CACHE_TAG_FILE).read_text(encoding="utf-8"), run.CACHE_TAG_SIGNATURE + "\n")
            self.assertTrue((scratch / "scratch").is_file())
            self.assertNotIn(str(scratch), run.snapshot(workspace))
            missing = run.write_check_script(Path(tmp) / "missing", workspace, command)
            completed = subprocess.run([str(missing)], capture_output=True, text=True, check=False, env={})
            self.assertEqual(completed.returncode, 0)
            last = completed.stdout.splitlines()[-1]
            self.assertTrue(last.startswith(f"{run.CHECK_SUITE_UNAVAILABLE}: {tool.name} --version exited 127"), last)
            self.assertIn(run.SYSTEM_SEARCH_PATH, last)

    def test_write_roots_follow_metadata_and_otherwise_cover_the_whole_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            self.assertEqual(run.write_roots(self.task, workspace), (True, []))
            (workspace / "crates").mkdir()
            (workspace / "docs").mkdir()
            self.assertEqual(run.write_roots(self.task, workspace), (True, []))
            (workspace / "src").mkdir()
            named = run.protocol.Task.from_dict({**self.task.to_dict(), "metadata": {"write_roots": ["src"]}})
            self.assertEqual(run.write_roots(named, workspace), (False, ["src"]))
            missing = run.protocol.Task.from_dict({**self.task.to_dict(), "metadata": {"write_roots": ["lib"]}})
            with self.assertRaises(ValueError) as caught:
                run.write_roots(missing, workspace)
            self.assertIn("lib", str(caught.exception))

    def test_tool_roots_join_the_system_roots_the_run_and_the_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp) / "tools"
            tools.mkdir()
            settings = run.Settings(Path("/foe"), None, "autonomy", 1, "subscription", None, "m", "low", Path("/out"), None, "chat", 60, Path("/foe"), (str(tools), "/usr/bin"))
            self.assertEqual(run.tool_roots(settings, self.task), [*run.graphs.EXECUTE_ROOTS, str(tools)])
            named = run.protocol.Task.from_dict({**self.task.to_dict(), "metadata": {"tool_roots": [tmp, str(tools)]}})
            self.assertEqual(run.tool_roots(settings, named), [*run.graphs.EXECUTE_ROOTS, str(tools), tmp])
            for bad, fragment in (({"tool_roots": ["tools"]}, "not an absolute path"), ({"tool_roots": [str(Path(tmp) / "absent")]}, "does not exist"), ({"tool_roots": "x"}, "expected a list")):
                with self.assertRaises(ValueError) as caught:
                    run.tool_roots(settings, run.protocol.Task.from_dict({**self.task.to_dict(), "metadata": bad}))
                self.assertIn("tool_roots", str(caught.exception))
                self.assertIn(fragment, str(caught.exception))

    def test_the_snapshot_leaves_out_cache_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "src").mkdir()
            (workspace / "src" / "lib.rs").write_text("", encoding="utf-8")
            (workspace / "target" / "debug" / "deps").mkdir(parents=True)
            (workspace / "target" / run.CACHE_TAG_FILE).write_text("Signature: 8a477f597d28d172789f06886806bc55\n", encoding="utf-8")
            (workspace / "target" / "debug" / "deps" / "lib.rlib").write_text("", encoding="utf-8")
            (workspace / "src" / "__pycache__").mkdir()
            (workspace / "src" / "__pycache__" / "lib.pyc").write_text("", encoding="utf-8")
            self.assertEqual([Path(path).relative_to(workspace).as_posix() for path in run.snapshot(workspace)], ["src/lib.rs"])

    def test_the_check_suite_fault_is_read_from_the_root_or_a_child_log(self) -> None:
        def log(directory: Path, stdout: str | None) -> None:
            directory.mkdir(parents=True)
            events = [{"seq": 0, "time": 1, "version": run.normalize_foe.LOG_VERSION, "type": "episode/start", "data": {"id": directory.name}}]
            if stdout is not None:
                events.append({"seq": 1, "time": 2, "type": "tool/result", "data": {"call_id": "c1", "name": "check", "is_error": False, "value": {"exit_code": 0, "stdout": stdout}}})
            events.append({"seq": len(events), "time": 3, "type": "episode/end", "data": {"outcome": {"kind": "completed", "value": None}}})
            (directory / run.normalize_foe.LOG_NAME).write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            episode = Path(tmp) / "ep"
            log(episode, "tests/test_greeting.py: 1 failed\nthe check suite /usr/bin/bash checks/run.sh exited 1\n")
            log(episode / "children" / "ep_child", None)
            self.assertIsNone(run.check_suite_fault(episode))
            marker = f"{run.CHECK_SUITE_UNAVAILABLE}: /usr/bin/bash checks/run.sh exited 127, so a command it names is absent from the search path /usr/bin"
            log(episode / "children" / "ep_other", f"checks/run.sh: line 4: cargo: command not found\n{marker}\n")
            fault = run.check_suite_fault(episode)
            self.assertIsNotNone(fault)
            self.assertTrue(fault.startswith(marker), fault)
            self.assertIn("ep_other", fault)

    def test_a_document_names_the_workspace_through_the_placeholder_and_carries_the_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "root" / "workspace"
            (workspace / "crates").mkdir(parents=True)
            check = Path(tmp) / "check"
            document = run.foe_document(run.arm_by_name("autonomy", "foe-configured"), self.task, workspace, check, [*run.graphs.EXECUTE_ROOTS, tmp])
            self.assertEqual(document["grants"]["read"], ["{workspace}", tmp])
            self.assertEqual(document["grants"]["write"], ["{workspace}"])
            self.assertEqual(document["grants"]["execute"], [*run.graphs.EXECUTE_ROOTS, tmp, "{workspace}"])
            # Every node's contract reads and executes the tool root too, since its bash runs the compiler.
            for name, node in document["workflow"]["nodes"].items():
                self.assertEqual(node["model"]["grants"]["read"], ["{workspace}", tmp], name)
                self.assertEqual(node["model"]["grants"]["execute"], [*run.graphs.EXECUTE_ROOTS, tmp, "{workspace}"], name)
            plain = run.foe_document(run.arm_by_name("autonomy", "foe-configured"), self.task, workspace, check)
            self.assertEqual(plain["grants"]["read"], ["{workspace}"])
            teams_root = run.foe_document(run.arm_by_name("teams", "foe-configured"), run.protocol.Task.from_dict({**self.task.to_dict(), "family": "teams", "class_name": "coherent"}), workspace, check, [tmp])
            self.assertEqual(teams_root["child_contracts"]["worker"]["grants"]["read"], ["{workspace}", tmp])
            self.assertEqual(document["budget"]["model_calls"], self.task.budget["model_calls"])
            self.assertEqual(document["budget"]["seconds"], self.task.budget["seconds"])
            self.assertNotIn(str(workspace), json.dumps(document))
            narrowed = run.protocol.Task.from_dict({**self.task.to_dict(), "metadata": {"write_roots": ["crates"]}})
            self.assertEqual(run.foe_document(run.arm_by_name("autonomy", "foe-configured"), narrowed, workspace, check)["grants"]["write"], ["{workspace}/crates"])
            ablated = run.foe_document(run.arm_by_name("autonomy", "foe-ablated"), self.task, workspace, check)
            self.assertEqual(ablated["name"], "autonomy-ablated")
            self.assertNotIn("block", ablated["tools"])
            teams_task = run.protocol.Task.from_dict({**self.task.to_dict(), "family": "teams", "class_name": "coherent"})
            sequential = run.foe_document(run.arm_by_name("teams", "foe-sequential"), teams_task, workspace, check)
            self.assertEqual(sequential["name"], "teams-sequential")
            self.assertEqual(sequential["budget"]["max_concurrent"], 1)

    def test_a_normalizer_failure_of_any_kind_is_a_fault_of_the_attempt(self) -> None:
        from unittest import mock

        settings = run.Settings(Path("/foe"), Path("/codex"), "autonomy", 1, "subscription", None, "m", "low", Path("/out"), None, "chat", 60, Path("/foe"))
        record = {"codex_home": "/out/home", "events": "/out/events.jsonl", "last_message": "/out/last.txt"}
        result = run.ArmResult("codex-default", "codex", 0, 1, 0, run.foe_arm.reported("completed"), None, Path("/out"), record)
        for error in (AttributeError("'list' object has no attribute 'get'"), PermissionError(13, "Permission denied", "/out/events.jsonl"), KeyError("usage"), TypeError("int expected")):
            with mock.patch.object(run.normalize_codex, "normalize", side_effect=error):
                trajectory_, conformance, fault = run.normalize_result(settings, result)
            self.assertIsNone(trajectory_)
            self.assertIsNone(conformance)
            self.assertIn("the codex records could not be reduced to a trajectory", fault)
            self.assertIn(type(error).__name__, fault)

    def test_the_effective_budget_applies_the_overrides_and_the_codex_limits_always_carry_seconds(self) -> None:
        plain = run.Settings(Path("/foe"), None, "autonomy", 1, "subscription", None, "m", "low", Path("/out"), None, "chat", 60, Path("/foe"))
        self.assertEqual(run.effective_budget(plain, self.task), self.task.budget)
        overridden = run.Settings(Path("/foe"), None, "autonomy", 1, "subscription", None, "m", "low", Path("/out"), None, "chat", 60, Path("/foe"), (), {"seconds": 9, "model_calls": 2})
        budget = run.effective_budget(overridden, self.task)
        self.assertEqual(budget, {"model_calls": 2, "input_tokens": 16000, "output_tokens": 4000, "seconds": 9})
        self.assertEqual(run.codex_limits(budget), {"input_tokens": 16000, "output_tokens": 4000, "seconds": 9})
        with self.assertRaises(ValueError) as caught:
            run.codex_limits({"input_tokens": 1, "output_tokens": 1})
        self.assertIn("seconds", str(caught.exception))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            document = run.foe_document(run.arm_by_name("autonomy", "foe-configured"), self.task, workspace, Path(tmp) / "check", budget=budget)
            self.assertEqual(document["budget"]["seconds"], 9)
            self.assertEqual(document["budget"]["model_calls"], 2)
        self.assertEqual(run.parse_budget(["model_calls=3", " seconds = 12 "]), {"model_calls": 3, "seconds": 12})

    def test_the_outcomes_are_stated_side_by_side(self) -> None:
        reported = run.foe_arm.reported("exhausted", "input_tokens", ["the limit was crossed"])
        self.assertEqual(run.outcomes_side_by_side(reported, None), {"arm": {"status": "exhausted", "code": "input_tokens"}, "trajectory": None, "agree": None})
        trajectory_ = run.trajectory.Trajectory("codex", {}, [], run.trajectory.Outcome("exhausted", code="input_tokens"), "subscription")
        self.assertEqual(run.outcomes_side_by_side(reported, trajectory_)["agree"], True)
        differing = run.trajectory.Trajectory("codex", {}, [], run.trajectory.Outcome("completed"), "subscription")
        self.assertEqual(run.outcomes_side_by_side(reported, differing), {"arm": {"status": "exhausted", "code": "input_tokens"}, "trajectory": {"status": "completed", "code": None}, "agree": False})

    def test_the_shipped_arm_is_not_applicable_only_to_a_task_naming_tool_roots(self) -> None:
        shipped = run.arm_by_name("autonomy", "foe-as-shipped")
        self.assertIsNone(run.not_applicable(shipped, self.task))
        with tempfile.TemporaryDirectory() as tmp:
            named = run.protocol.Task.from_dict({**self.task.to_dict(), "metadata": {"tool_roots": [tmp]}})
            reason = run.not_applicable(shipped, named)
            self.assertIsNotNone(reason)
            self.assertIn("foe-as-shipped", reason)
            self.assertIn("builtin:coding", reason)
            self.assertIn(tmp, reason)
            for name in ("foe-configured", "foe-ablated", "codex-equivalent", "codex-default"):
                self.assertIsNone(run.not_applicable(run.arm_by_name("autonomy", name), named), name)
            empty = run.protocol.Task.from_dict({**self.task.to_dict(), "metadata": {"tool_roots": []}})
            self.assertIsNone(run.not_applicable(shipped, empty))

    def test_provenance_names_the_commit_and_the_changed_paths_of_a_dirty_tree(self) -> None:
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "checkout"
            checkout.mkdir()
            binary = checkout / "foe"
            binary.write_bytes(b"#!/bin/sh\nexit 0\n")
            git = ["/usr/bin/git", "-C", str(checkout), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
            subprocess.run([*git, "init", "-q"], check=True)
            subprocess.run([*git, "add", "foe"], check=True)
            subprocess.run([*git, "commit", "-q", "-m", "binary"], check=True)
            head = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            digest = run.foe_build.sha256_file(binary)

            clean = run.foe_provenance(checkout, binary)
            self.assertEqual((clean["runtime_binary"], clean["git_head"], clean["dirty"], clean["changed_paths"], clean["source_tree_error"]), (digest, head, False, [], None))
            self.assertTrue(clean["source_tree"].startswith("git-tree-"))

            (checkout / "src").mkdir()
            (checkout / "src" / "lib.rs").write_text("", encoding="utf-8")
            binary.write_bytes(b"#!/bin/sh\nexit 1\n")
            dirty = run.foe_provenance(binary, binary)
            self.assertEqual((dirty["git_head"], dirty["dirty"]), (head, True))
            self.assertEqual(dirty["changed_paths"], ["foe", "src/lib.rs"])
            self.assertIsNone(dirty["source_tree"])
            self.assertEqual(dirty["runtime_binary"], run.foe_build.sha256_file(binary))
            self.assertIn("not clean", dirty["source_tree_error"])

            outside = Path(tmp) / "outside"
            outside.mkdir()
            elsewhere = outside / "foe"
            elsewhere.write_bytes(b"")
            plain = run.foe_provenance(outside, elsewhere)
            self.assertEqual((plain["source_tree"], plain["git_head"], plain["dirty"], plain["changed_paths"]), (None, None, None, []))
            self.assertIsNotNone(plain["source_tree_error"])

    def test_the_reported_outcome_moves_a_non_blocked_code_into_the_evidence(self) -> None:
        exhausted = run.protocol_reported({"status": "exhausted", "code": "model_calls", "evidence": ["the model_calls budget was exhausted"]})
        self.assertEqual((exhausted.status, exhausted.code), ("exhausted", None))
        self.assertTrue(exhausted.evidence.startswith("exhausted: model_calls\n"))
        blocked = run.protocol_reported({"status": "blocked", "code": "ambiguous-task", "evidence": []})
        self.assertEqual(blocked.code, "ambiguous-task")

    def test_codex_prompts_and_providers_follow_the_arm_and_the_route(self) -> None:
        self.assertEqual(run.codex_task_text(run.arm_by_name("autonomy", "codex-default"), self.task), self.task.text)
        self.assertTrue(run.codex_task_text(run.arm_by_name("autonomy", "codex-equivalent"), self.task).endswith(run.PHASES))
        settings = run.Settings(Path("/foe"), Path("/codex"), "autonomy", 1, "compatible", "http://127.0.0.1:9/v1", "m", "low", Path("/out"), None, "chat", 60, Path("/foe"))
        self.assertEqual(run.codex_providers(settings), {"compatible": {"name": "compatible", "base_url": "http://127.0.0.1:9/v1", "wire_api": "chat"}})
        self.assertEqual(run.foe_route(settings).provider, "compatible-http")
        subscription = run.Settings(Path("/foe"), None, "autonomy", 1, "subscription", None, "m", "low", Path("/out"), None, "chat", 60, Path("/foe"))
        self.assertIsNone(run.codex_providers(subscription))
        self.assertEqual(run.foe_route(subscription).provider, "openai-codex")

    def test_the_built_in_command_line_carries_the_task_the_document_and_the_model(self) -> None:
        route = run.foe_arm.ModelRoute("openai-codex", "m")
        command = run.builtin_command_line(Path("/foe"), "Do it.", "builtin:team", Path("/log"), route)
        self.assertEqual(command, ["/foe", "Do it.", "--config", "builtin:team", "--log-dir", "/log", "--viewer", "off", "--model", "openai-codex/m"])


class Running(Harness):
    def test_every_arm_kind_is_run_normalized_graded_and_recorded(self) -> None:
        status, out, err = self.main(self.argv("--confirm-spend", arms="foe-configured,foe-as-shipped,codex-equivalent,codex-default"))
        self.assertEqual(status, run.EVALUATED, err)
        summary = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(summary["attempts"], 4)
        self.assertEqual(summary["infrastructure_failures"], 0)
        settings = json.loads((self.out / run.RUN_FILE).read_text(encoding="utf-8"))
        self.assertEqual(settings["provenance"]["codex_version"], "codex-cli 0.153.4")
        self.assertTrue(settings["provenance"]["foe"]["runtime_binary"].startswith("sha256:"))
        self.assertIsNone(settings["provenance"]["foe"]["source_tree"])

        configured = self.record("foe-configured")
        self.assertEqual(configured["classification"], "correct-completion")
        self.assertIsNone(configured["infrastructure_error"])
        self.assertEqual(configured["reported"]["status"], "completed")
        self.assertTrue(configured["grade"]["passed"], configured["grade"])
        self.assertEqual(configured["grade"]["damage"], [])
        self.assertEqual(configured["totals"]["model_calls"], 1)
        self.assertEqual(configured["totals"]["input_tokens"], 1200)
        self.assertEqual(configured["totals"]["commands"], 1)
        self.assertEqual(configured["shell_writes_attributed"], 1)
        changes = configured["trajectory"]["agents"][0]["file_changes"]
        self.assertEqual([change["via"] for change in changes], ["shell"])
        self.assertTrue(changes[0]["path"].endswith("src/greeting.py"))
        self.assertEqual(changes[0]["kind"], "edit")
        self.assertIn("valid", configured["conformance"])
        self.assertEqual(configured["trajectory"]["route"], "subscription")
        self.assertEqual(configured["trajectory"]["identity"]["model_provider"], "openai-codex")
        config = json.loads(Path(configured["arm_result"]["record"]["config"]).read_text(encoding="utf-8"))
        workspace = configured["paths"]["workspace"]
        self.assertEqual(config["grants"]["read"], [workspace])
        self.assertEqual(config["grants"]["write"], [workspace])
        self.assertEqual(config["tool_defs"]["check"]["exec"], str(Path(configured["paths"]["attempt_dir"]) / run.CHECK_SCRIPT_NAME))
        self.assertEqual(config["model"], {"provider": "openai-codex", "model": "fixture-model", "reasoning_effort": run.DEFAULT_EFFORT})

        shipped = self.record("foe-as-shipped")
        self.assertEqual(shipped["classification"], "correct-completion")
        command = shipped["arm_result"]["record"]["commands"][0]
        self.assertEqual(command[1], shipped["task"]["text"])
        self.assertEqual(command[command.index("--config") + 1], "builtin:coding")
        self.assertEqual(shipped["arm_result"]["record"]["cwd"], shipped["paths"]["workspace"])

        equivalent = self.record("codex-equivalent")
        self.assertEqual(equivalent["classification"], "correct-completion")
        self.assertEqual(equivalent["harness"], "codex")
        prompt = equivalent["arm_result"]["record"]["commands"][0][-1]
        self.assertTrue(prompt.startswith(equivalent["task"]["text"]))
        self.assertTrue(prompt.endswith(run.PHASES))
        self.assertEqual(equivalent["totals"]["model_calls"], 1)
        self.assertEqual(equivalent["totals"]["input_tokens"], 5000)
        self.assertEqual(equivalent["shell_writes_attributed"], 1)
        self.assertIsNone(equivalent["conformance"])
        self.assertTrue(equivalent["arm_result"]["record"]["codex_home"].startswith(equivalent["paths"]["attempt_dir"]))
        # The credential copy is gone once the process has exited, and the record says so.
        self.assertTrue(equivalent["arm_result"]["record"]["credential_removed"])
        self.assertFalse(Path(equivalent["arm_result"]["record"]["credential_copy"]).exists())
        self.assertNotIn("placeholder", json.dumps(equivalent))
        # The watcher's limits carry the seconds ceiling on every Codex attempt.
        self.assertEqual(equivalent["arm_result"]["record"]["limits"], {"input_tokens": 16000, "output_tokens": 4000, "seconds": 300})
        self.assertEqual(equivalent["budget"], equivalent["task"]["budget"])
        self.assertEqual(equivalent["budget_overrides"], {})
        self.assertEqual(equivalent["outcomes"], {"arm": {"status": "completed", "code": None}, "trajectory": {"status": "completed", "code": None}, "agree": True})
        self.assertIsNone(equivalent["not_applicable"])
        self.assertEqual(equivalent["tool_roots"], list(run.graphs.EXECUTE_ROOTS))
        for arm in ("foe-configured", "foe-as-shipped"):
            self.assertEqual(self.record(arm)["provenance"]["foe"]["git_head"], None)
            self.assertEqual(self.record(arm)["outcomes"]["agree"], True, arm)

        default = self.record("codex-default")
        self.assertEqual(default["arm_result"]["record"]["commands"][0][-1], default["task"]["text"])
        self.assertEqual(default["classification"], "correct-completion")

    def test_a_stop_on_a_solvable_task_is_a_wrong_stop(self) -> None:
        self.behave("blocked")
        status, _, err = self.main(self.argv("--confirm-spend", arms="foe-configured,codex-default"))
        self.assertEqual(status, run.EVALUATED, err)
        for arm in ("foe-configured", "codex-default"):
            record = self.record(arm)
            self.assertEqual(record["classification"], "wrong-stop", arm)
            self.assertEqual(record["reported"], {"status": "blocked", "code": "goal-unreachable", "evidence": ["The requirements conflict."]})
            self.assertFalse(record["grade"]["passed"])
            self.assertEqual(record["shell_writes_attributed"], 0)

    def test_a_run_without_a_log_is_a_fault_rather_than_a_score(self) -> None:
        self.behave("silent")
        status, out, err = self.main(self.argv("--confirm-spend"))
        self.assertEqual(status, run.DEPLOYMENT_FAULT)
        self.assertIn("did not evaluate the harness", err)
        record = self.record("foe-configured")
        self.assertIsNone(record["classification"])
        self.assertIn("no episode log", record["infrastructure_error"])
        self.assertEqual(record["reported"]["status"], "failed")
        self.assertIsNone(record["trajectory"])
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["infrastructure_failures"], 1)

    def test_an_existing_record_is_refused_before_anything_runs(self) -> None:
        status, _, err = self.main(self.argv("--confirm-spend"))
        self.assertEqual(status, run.EVALUATED, err)
        status, _, err = self.main(self.argv("--confirm-spend"))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("a record already exists", err)

    def test_a_leftover_attempt_directory_is_refused_by_name(self) -> None:
        import shutil

        status, _, err = self.main(self.argv("--confirm-spend"))
        self.assertEqual(status, run.EVALUATED, err)
        shutil.rmtree(self.out / run.RECORDS_DIR)
        status, _, err = self.main(self.argv("--confirm-spend"))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("an attempt directory already exists without a record", err)
        self.assertIn(str(run.attempt_path(self.out, TASK, "foe-configured", 1)), err)
        self.assertFalse((self.out / run.RECORDS_DIR).exists())

    def test_a_later_run_into_the_same_out_keeps_the_earlier_run_file(self) -> None:
        status, out, err = self.main(self.argv("--confirm-spend"))
        self.assertEqual(status, run.EVALUATED, err)
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["run"], str(self.out / run.RUN_FILE))
        status, out, err = self.main(self.argv("--confirm-spend", arms="foe-as-shipped"))
        self.assertEqual(status, run.EVALUATED, err)
        second = self.out / "run-02.json"
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["run"], str(second))
        self.assertEqual(json.loads((self.out / run.RUN_FILE).read_text(encoding="utf-8"))["arms"], ["foe-configured"])
        self.assertEqual(json.loads(second.read_text(encoding="utf-8"))["arms"], ["foe-as-shipped"])
        self.assertEqual(run.run_file_path(self.out), self.out / "run-03.json")

    def test_a_malformed_codex_session_is_a_fault_of_that_attempt_alone(self) -> None:
        self.behave("malformed-session")
        status, out, err = self.main(self.argv("--confirm-spend", arms="codex-default,foe-configured"))
        self.assertEqual(status, run.DEPLOYMENT_FAULT, err)
        codex = self.record("codex-default")
        self.assertIsNone(codex["classification"])
        self.assertIn("could not be reduced to a trajectory", codex["infrastructure_error"])
        self.assertIn("rollout-", codex["infrastructure_error"])
        # The attempt planned after the faulted one still ran and was scored.
        self.assertEqual(self.record("foe-configured")["classification"], "wrong-stop")
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["infrastructure_failures"], 1)

    def test_a_budget_override_bounds_every_attempt_and_is_recorded(self) -> None:
        status, out, err = self.main(self.argv("--confirm-spend", "--budget", "seconds=7", "--budget", "model_calls=3", arms="foe-configured,foe-as-shipped,codex-default"))
        self.assertEqual(status, run.EVALUATED, err)
        settings = json.loads((self.out / run.RUN_FILE).read_text(encoding="utf-8"))
        self.assertEqual(settings["settings"]["budget_overrides"], {"seconds": 7, "model_calls": 3})
        self.assertEqual(settings["budgets"][TASK], {"model_calls": 3, "input_tokens": 16000, "output_tokens": 4000, "seconds": 7})
        self.assertIn("--budget replaces seconds=7, model_calls=3", settings["plan"])
        for arm in ("foe-configured", "foe-as-shipped", "codex-default"):
            record = self.record(arm)
            self.assertEqual(record["budget"], {"model_calls": 3, "input_tokens": 16000, "output_tokens": 4000, "seconds": 7}, arm)
            self.assertEqual(record["budget_overrides"], {"seconds": 7, "model_calls": 3}, arm)
            self.assertEqual(record["classification"], "correct-completion", arm)
        configured = self.record("foe-configured")
        config = json.loads(Path(configured["arm_result"]["record"]["config"]).read_text(encoding="utf-8"))
        self.assertEqual((config["budget"]["seconds"], config["budget"]["model_calls"]), (7, 3))
        self.assertEqual(configured["arm_result"]["record"]["cap_seconds"], 7)
        self.assertEqual(self.record("foe-as-shipped")["arm_result"]["record"]["cap_seconds"], 7)
        # The Codex watcher receives the overridden seconds ceiling and the task's token ceilings.
        self.assertEqual(self.record("codex-default")["arm_result"]["record"]["limits"], {"input_tokens": 16000, "output_tokens": 4000, "seconds": 7})

    def test_a_codex_run_the_watcher_stopped_normalizes_to_the_crossed_limit(self) -> None:
        self.behave("exhaust")
        status, out, err = self.main(self.argv("--confirm-spend", "--budget", "input_tokens=1000", arms="codex-default"))
        self.assertEqual(status, run.EVALUATED, err)
        record = self.record("codex-default")
        self.assertIsNone(record["infrastructure_error"])
        self.assertEqual((record["reported"]["status"], record["reported"]["code"]), ("exhausted", "input_tokens"))
        self.assertEqual(record["arm_result"]["record"]["stop"]["dimension"], "input_tokens")
        self.assertEqual(record["arm_result"]["record"]["limits"]["input_tokens"], 1000)
        self.assertIsNone(record["arm_result"]["exit_status"])
        # The trajectory's outcome carries the crossed limit as its code, so the two outcomes agree.
        self.assertEqual(record["trajectory"]["outcome"]["status"], "exhausted")
        self.assertEqual(record["trajectory"]["outcome"]["code"], "input_tokens")
        self.assertEqual(record["outcomes"], {"arm": {"status": "exhausted", "code": "input_tokens"}, "trajectory": {"status": "exhausted", "code": "input_tokens"}, "agree": True})
        self.assertEqual(record["classification"], "wrong-stop")
        self.assertTrue(record["arm_result"]["record"]["credential_removed"])
        self.assertFalse(Path(record["arm_result"]["record"]["credential_copy"]).exists())
        self.assertLess(record["ended_ms"] - record["started_ms"], 30_000)

    def test_a_task_naming_tool_roots_records_the_shipped_arm_as_not_applicable_and_grants_them_to_the_document_arm(self) -> None:
        tools = self.root / "tools"
        tools.mkdir()
        extra = self.root / "extra"
        extra.mkdir()
        tasks = self.tasks_with_metadata({"tool_roots": [str(tools)]})
        status, out, err = self.main(self.argv("--confirm-spend", "--tasks", str(tasks), "--tool-root", str(extra), arms="foe-as-shipped,foe-configured"))
        self.assertEqual(status, run.EVALUATED, err)
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["infrastructure_failures"], 0)
        self.assertIn(f"{TASK} under foe-as-shipped is not applicable: the foe-as-shipped arm runs the built-in document builtin:coding", err)
        merged = [*run.graphs.EXECUTE_ROOTS, str(extra), str(tools)]
        settings = json.loads((self.out / run.RUN_FILE).read_text(encoding="utf-8"))
        self.assertEqual(settings["tool_roots"][TASK], merged)

        shipped = self.record("foe-as-shipped")
        self.assertIn(str(tools), shipped["not_applicable"])
        self.assertIn("metadata.tool_roots", shipped["not_applicable"])
        self.assertIsNone(shipped["classification"])
        self.assertIsNone(shipped["infrastructure_error"])
        self.assertIsNone(shipped["arm_result"])
        self.assertIsNone(shipped["grade"])
        self.assertEqual(shipped["tool_roots"], merged)
        self.assertIsNotNone(shipped["ended_ms"])
        self.assertFalse(run.attempt_path(self.out, TASK, "foe-as-shipped", 1).exists())

        configured = self.record("foe-configured")
        self.assertEqual(configured["classification"], "correct-completion")
        self.assertEqual(configured["tool_roots"], merged)
        config = json.loads(Path(configured["arm_result"]["record"]["config"]).read_text(encoding="utf-8"))
        self.assertEqual(config["grants"]["execute"], [*merged, configured["paths"]["workspace"]])
        self.assertEqual(config["grants"]["read"], [configured["paths"]["workspace"], str(extra), str(tools)])

    def test_a_check_suite_that_cannot_run_is_a_fault_until_its_tool_is_granted(self) -> None:
        tool_name = "cross-harness-fixture-checker"
        tasks = self.tasks_with_metadata({"check": f"{tool_name} --version"})
        tools = self.root / "tools"
        tools.mkdir()
        (tools / tool_name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (tools / tool_name).chmod(0o755)

        status, out, err = self.main(self.argv("--confirm-spend", "--tasks", str(tasks)))
        self.assertEqual(status, run.DEPLOYMENT_FAULT, err)
        record = self.record("foe-configured")
        self.assertIsNone(record["classification"])
        fault = record["infrastructure_error"]
        self.assertTrue(fault.startswith(f"{run.CHECK_SUITE_UNAVAILABLE}: /usr/bin/bash -c '{tool_name} --version' exited 127"), fault)
        self.assertIn(f"search path {run.SYSTEM_SEARCH_PATH} (", fault)
        self.assertIsNotNone(record["trajectory"])
        self.assertIn("did not evaluate the harness", err)
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["infrastructure_failures"], 1)

        granted = self.root / "granted"
        status, _, err = self.main(self.argv("--confirm-spend", "--tasks", str(tasks), "--tool-root", str(tools), "--out", str(granted)))
        self.assertEqual(status, run.EVALUATED, err)
        record = json.loads(run.record_path(granted, TASK, "foe-configured", 1).read_text(encoding="utf-8"))
        self.assertEqual(record["classification"], "correct-completion")
        config = json.loads(Path(record["arm_result"]["record"]["config"]).read_text(encoding="utf-8"))
        self.assertEqual(config["grants"]["execute"], [*run.graphs.EXECUTE_ROOTS, str(tools), record["paths"]["workspace"]])
        self.assertEqual(config["grants"]["read"], [record["paths"]["workspace"], str(tools)])
        self.assertEqual(config["grants"]["write"], [record["paths"]["workspace"]])
        self.assertIn(f":{tools}\n", Path(config["tool_defs"]["check"]["exec"]).read_text(encoding="utf-8"))
        settings = json.loads((granted / run.RUN_FILE).read_text(encoding="utf-8"))["settings"]
        self.assertEqual(settings["tool_roots"], [str(tools)])


if __name__ == "__main__":
    unittest.main()
