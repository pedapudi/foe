#!/usr/bin/python3
"""Unit tests for the runner: fake foe and codex scripts stand in for the binaries, and no model is called."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run  # noqa: E402

EXAMPLES = Path(__file__).resolve().parent / "tasks" / "examples"
TASK = "hello-solvable"
# A command name no host has on PATH, for the bare-name form of `harnesses.codex`.
ABSENT_COMMAND = "cross-harness-absent-command"

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
    grader_dir = os.path.join(os.path.dirname(workspace), "grader")
    if behaviour == "peek-at-grader":
        command = "cat ../grader/grade " + os.path.join(grader_dir, "oracle", "candidate.json")
        with open(os.path.join(workspace, "peek.txt"), "w", encoding="utf-8") as handle:
            handle.write(str(os.path.exists(grader_dir)) + "\\n" + " ".join(sorted(os.listdir(os.path.dirname(workspace)))))
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
    elif behaviour in ("provider-outage", "recovery-bound"):
        message = ("provider unavailable through 5 attempts at step 1; the remaining seconds budget cannot fund another"
                   if behaviour == "provider-outage" else "node `assess` would fire again beyond its max_fires of 3")
        outcome = {"kind": "blocked", "code": "recovery-exhausted", "message": message}
        status = 2
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
    if behaviour == "provider-error":
        print("codex: stream error: 429 Too Many Requests", file=sys.stderr)
        sys.exit(1)
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

    def document(self, arms: Any = ("foe-configured",), name: str = "run", directory: Path | None = None, **keys: Any) -> Path:
        """A run document written under the scratch directory, or under `directory`.

        The defaults name the fake binaries, the example tasks, the given
        arms, and the scratch output directory; `arms` and `keys` replace
        whole top-level keys as written, so a value of the wrong type
        reaches the runner, and a key set to None is left out.
        """
        content: dict[str, Any] = {
            "tasks": str(EXAMPLES),
            "arms": list(arms) if isinstance(arms, (list, tuple)) else arms,
            "attempts": 1,
            "model": {"route": "subscription", "name": "fixture-model"},
            "harnesses": {"foe": str(self.foe), "codex": str(self.codex), "credential": str(self.credential)},
            "out": str(self.out),
            # The foe canary is planted in this directory; the default is the real ~/.config/foe, which a test leaves untouched.
            "foe_config_dir": str(self.root / "foe-config"),
        }
        for key, value in {"arms": content["arms"], **keys}.items():
            if value is None:
                content.pop(key, None)
            else:
                content[key] = value
        path = (directory or self.root) / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
        return path

    def argv(self, *extra: str, arms: Any = ("foe-configured",), **keys: Any) -> list[str]:
        """The runner's arguments: the path of a document written from `arms` and `keys`, then `extra`."""
        return [str(self.document(arms=arms, **keys)), *extra]

    def main(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = run.main(argv)
        return status, out.getvalue(), err.getvalue()

    def record(self, arm: str, attempt: int = 1) -> dict:
        return json.loads(run.record_path(self.out, TASK, arm, attempt).read_text(encoding="utf-8"))

    def tasks_with_metadata(self, metadata: dict) -> Path:
        """A copy of the example task directory whose task carries `metadata`."""
        tasks = self.root / "tasks"
        # A test that writes the task twice, with different metadata, keeps one copy.
        shutil.copytree(EXAMPLES / TASK, tasks / TASK, dirs_exist_ok=True)
        task_file = tasks / TASK / run.protocol.TASK_FILE
        task = json.loads(task_file.read_text(encoding="utf-8"))
        task["metadata"] = metadata
        task_file.write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
        return tasks

    def assertResolved(self, out: str, label: str, value: str) -> None:
        """The plan header states `value` on the row named `label`."""
        self.assertRegex(out, rf"\n  {re.escape(label)}\s+{re.escape(value)}\n")


class Planning(Harness):
    def test_without_confirmation_the_plan_names_every_attempt_and_nothing_runs(self) -> None:
        status, out, _ = self.main(self.argv(attempts=2, arms=["foe-configured", "codex-default"]))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        for attempt in (1, 2):
            for arm in ("foe-configured", "codex-default"):
                self.assertIn(f"{TASK} / {arm} / {attempt}", out)
        self.assertIn("every planned attempt", out)
        self.assertIn("No attempt was launched", out)
        # Four attempts at eight model calls, 16,000 input tokens, and 4,000 output tokens each.
        self.assertRegex(out, r"\n\s+32\s+64,000\s+16,000\s+1200\s+every planned attempt")
        # The header states every value the document resolved to.
        self.assertIn(f"Run document {self.root / 'run.json'} resolved to:", out)
        self.assertResolved(out, "tasks", str(EXAMPLES))
        self.assertResolved(out, "family", "autonomy")
        self.assertResolved(out, "selected", f"{TASK} (every task under tasks)")
        self.assertResolved(out, "arms", "foe-configured, codex-default")
        self.assertResolved(out, "attempts", "2")
        self.assertResolved(out, "resume", "an existing record or attempt directory is refused")
        self.assertResolved(out, "foe", str(self.foe))
        self.assertResolved(out, "codex", str(self.codex))
        self.assertResolved(out, "credential", str(self.credential))
        self.assertResolved(out, "route", "subscription")
        self.assertResolved(out, "model", "fixture-model")
        self.assertResolved(out, "effort", run.DEFAULT_EFFORT)
        self.assertResolved(out, "out", str(self.out))
        self.assertResolved(out, "tool roots", "none beyond the system roots")
        self.assertResolved(out, "budget", "every task's own budget")
        self.assertResolved(out, "grader timeout", f"{run.DEFAULT_GRADER_TIMEOUT_SECONDS} seconds")
        self.assertResolved(out, "source root", str(self.foe))
        self.assertNotIn("placeholder", out)
        self.assertFalse(self.out.exists())

    def test_the_arms_rotate_across_attempts_and_across_tasks(self) -> None:
        arms = list(run.ARMS["autonomy"])
        self.assertEqual([arm.name for arm in run.rotated(arms, 1, 0)], [arm.name for arm in arms])
        self.assertEqual(run.rotated(arms, 2, 0)[0].name, arms[1].name)
        self.assertEqual(run.rotated(arms, len(arms) + 1, 0)[0].name, arms[0].name)
        # The second task of an attempt starts one arm later than the first.
        self.assertEqual(run.rotated(arms, 1, 1)[0].name, arms[1].name)
        self.assertEqual(run.rotated(arms, 2, 1)[0].name, arms[2].name)
        self.assertEqual(run.rotated(arms, 1, len(arms))[0].name, arms[0].name)
        selected = run.Selected(EXAMPLES / TASK, run.protocol.load(EXAMPLES / TASK))
        triples = run.planned([selected], arms[:2], 2)
        self.assertEqual([(attempt, arm.name) for attempt, _, arm in triples], [(1, arms[0].name), (1, arms[1].name), (2, arms[1].name), (2, arms[0].name)])
        # With one attempt of two tasks, the two tasks start under different arms.
        triples = run.planned([selected, selected], arms[:2], 1)
        self.assertEqual([arm.name for _, _, arm in triples], [arms[0].name, arms[1].name, arms[1].name, arms[0].name])

    def test_bad_values_are_refused_by_key(self) -> None:
        status, _, err = self.main(self.argv(arms=["foe-configured", "codex-multi"]))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("key arms names 'codex-multi', which is not an arm of the autonomy family", err)
        status, _, err = self.main(self.argv(select=["absent-task"]))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("key select names 'absent-task', which is not a task under", err)
        status, _, err = self.main(self.argv(model={"route": "compatible", "name": "m"}))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("key model.base_url is absent", err)
        status, _, err = self.main(self.argv(model={"route": "subscription", "name": "m", "base_url": "http://127.0.0.1:9/v1"}))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("key model.base_url is 'http://127.0.0.1:9/v1'; the subscription route", err)
        status, _, err = self.main(self.argv(model={"route": "subscription", "name": "m", "codex_wire_api": "responses"}))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("key model.codex_wire_api is 'responses'", err)
        status, _, err = self.main(self.argv(model={"route": "compatible", "name": "m", "base_url": "http://127.0.0.1:9/v1", "codex_wire_api": "grpc"}))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("key model.codex_wire_api is 'grpc'; expected one of chat, responses", err)
        status, _, err = self.main(self.argv(tool_roots=[str(self.root / "absent-root")]))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("key tool_roots names", err)
        self.assertIn("absent-root", err)
        status, _, err = self.main(self.argv(harnesses={"foe": str(self.root / "absent-foe")}))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn(f"key harnesses.foe names {self.root / 'absent-foe'}, which is not an executable file", err)
        status, _, err = self.main(self.argv(tasks=str(self.root / "absent-tasks")))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("key tasks names", err)
        for key, value, fragment in (
            ("attempts", 0, "key attempts is 0; expected a positive integer"),
            ("attempts", "2", "key attempts is '2'; expected a positive integer"),
            ("attempts", True, "key attempts is True; expected a positive integer"),
            ("grader_timeout", 0, "key grader_timeout is 0; expected a positive integer"),
            ("tasks", 3, "key tasks is 3; expected a non-empty string"),
            ("select", "x", "key select is 'x'; expected a list of non-empty strings"),
            ("select", [], "key select is []; expected at least one task name"),
            ("arms", "foe-configured", "key arms is 'foe-configured'; expected a list of non-empty strings"),
            ("model", "m", "key model is 'm'; expected an object with the keys route, name, effort, base_url, codex_wire_api"),
            ("model", {"name": "m"}, "key model.route is absent; expected a non-empty string from subscription, compatible"),
            ("model", {"route": "subscription"}, "key model.name is absent"),
            ("model", {"route": "postal", "name": "m"}, "key model.route is 'postal'; expected one of subscription, compatible"),
            ("harnesses", [], "key harnesses is []; expected an object with the keys foe, codex, credential"),
            ("out", "", "key out is ''; expected a non-empty string"),
        ):
            status, _, err = self.main(self.argv(**{key: value}))
            self.assertEqual(status, run.NOTHING_LAUNCHED, (key, value))
            self.assertIn(fragment, err)
        status, _, err = self.main(self.argv(model=None))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("key model is absent", err)
        status, _, err = self.main(self.argv(tasks=None))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("key tasks is absent", err)
        self.assertFalse(self.out.exists())

    def test_an_unknown_key_is_refused_by_name(self) -> None:
        for extra in ((), ("--confirm-spend",)):
            status, _, err = self.main(self.argv(*extra, surprise=1))
            self.assertEqual(status, run.NOTHING_LAUNCHED)
            self.assertIn(f"{self.root / 'run.json'}: key surprise is unknown; the keys are {', '.join(run.DOCUMENT_KEYS)}", err)
        status, _, err = self.main(self.argv(model={"route": "subscription", "name": "m", "temperature": 0}))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn(f"key model.temperature is unknown; the keys are {', '.join(run.MODEL_KEYS)}", err)
        status, _, err = self.main(self.argv(harnesses={"foe": str(self.foe), "shell": "/bin/sh"}))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn(f"key harnesses.shell is unknown; the keys are {', '.join(run.HARNESS_KEYS)}", err)
        # A key of an earlier document form is unknown like any other.
        status, _, err = self.main(self.argv(tuning_log=str(self.root / "tuning-log.jsonl")))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn(f"key tuning_log is unknown; the keys are {', '.join(run.DOCUMENT_KEYS)}", err)
        status, _, err = self.main(self.argv(harnesses={"foe": str(self.foe), "as_shipped_toolchain_on_path": True}))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn(f"key harnesses.as_shipped_toolchain_on_path is unknown; the keys are {', '.join(run.HARNESS_KEYS)}", err)
        self.assertFalse(self.out.exists())

    def test_a_document_that_is_not_a_json_object_is_refused_by_path(self) -> None:
        path = self.root / "broken.json"
        status, _, err = self.main([str(path)])
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn(f"the run document {path} cannot be read", err)
        path.write_text("{", encoding="utf-8")
        status, _, err = self.main([str(path)])
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn(f"the run document {path} is not JSON", err)
        path.write_text("[]", encoding="utf-8")
        status, _, err = self.main([str(path)])
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn(f"the run document {path} is not an object", err)

    def test_an_empty_arms_list_is_refused(self) -> None:
        for extra in ((), ("--confirm-spend",)):
            status, _, err = self.main(self.argv(*extra, arms=[]))
            self.assertEqual(status, run.NOTHING_LAUNCHED)
            self.assertIn("key arms is []; expected at least one arm name", err)
        status, _, err = self.main(self.argv(arms=["foe-configured", "foe-configured"]))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("key arms names an arm twice", err)
        self.assertFalse(self.out.exists())

    def test_a_relative_tasks_path_resolves_against_the_document_directory(self) -> None:
        tasks = self.tasks_with_metadata({})
        document = self.document(directory=self.root / "runs", tasks="../tasks")
        status, out, err = self.main([str(document)])
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        self.assertResolved(out, "tasks", str(tasks))
        self.assertIn(f"{TASK} / foe-configured / 1", out)
        # A relative path elsewhere in the document follows the same rule.
        (self.root / "tools").mkdir()
        document = self.document(directory=self.root / "runs", tasks="../tasks", tool_roots=["../tools"], out="out-here")
        status, out, err = self.main([str(document)])
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        self.assertResolved(out, "tool roots", str(self.root / "tools"))
        self.assertResolved(out, "out", str(self.root / "runs" / "out-here"))

    def test_the_default_out_is_named_by_the_document_stem(self) -> None:
        document = self.document(name="pilot-3", out=None)
        expected = Path(run.DEFAULT_OUT_ROOT).expanduser() / "pilot-3"
        self.assertEqual(run.document_out(run.read_document(document)), expected)
        status, out, err = self.main([str(document)])
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        self.assertResolved(out, "out", str(expected))
        self.assertFalse(expected.exists())

    def test_a_leading_tilde_expands_to_the_home_directory(self) -> None:
        home = Path("~").expanduser()
        status, out, err = self.main(self.argv(tool_roots=["~"], out="~/cross-harness-test-out"))
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        self.assertResolved(out, "tool roots", str(home))
        self.assertResolved(out, "out", str(home / "cross-harness-test-out"))
        self.assertFalse((home / "cross-harness-test-out").exists())
        # The default credential is under the home directory, and a document arm alone leaves it unread.
        status, out, err = self.main(self.argv(harnesses={"foe": str(self.foe), "codex": str(self.codex)}))
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        expected = str(Path(run.DEFAULT_CREDENTIAL).expanduser())
        self.assertTrue(re.search(rf"\n  credential\s+(none; key harnesses.credential names )?{re.escape(expected)}", out), out)

    def test_the_default_foe_is_under_the_checkout_holding_the_document(self) -> None:
        checkout = self.root / "checkout"
        (checkout / run.GIT_ENTRY).mkdir(parents=True)
        binary = checkout / run.DEFAULT_FOE
        binary.parent.mkdir(parents=True)
        shutil.copy2(self.foe, binary)
        self.assertEqual(run.checkout_root(checkout / "evals" / "runs"), checkout)
        # A directory beside the checkout does not resolve to it. Asserting
        # None here instead would depend on no ancestor of the scratch
        # directory holding a .git entry, which the temporary directory of a
        # shared host does not guarantee.
        self.assertNotEqual(run.checkout_root(self.root), checkout)
        document = self.document(directory=checkout / "evals" / "runs", harnesses=None)
        status, out, err = self.main([str(document)])
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        self.assertResolved(out, "foe", str(binary))
        self.assertResolved(out, "source root", str(binary))
        # The source root can point elsewhere in the checkout; the plan states it resolved.
        document = self.document(directory=checkout / "evals" / "runs", harnesses=None, source_root="../..")
        status, out, err = self.main([str(document)])
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        self.assertResolved(out, "source root", str(checkout))
        # A relative default foe with no checkout above the document falls back to the current directory's checkout.
        with mock.patch.object(run.Path, "cwd", return_value=self.root):
            status, _, err = self.main([str(self.document(harnesses=None))])
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("key harnesses.foe is absent, and neither the document's directory", err)

    def test_the_family_is_read_from_the_tasks_and_a_mixed_selection_is_refused(self) -> None:
        tasks = self.tasks_with_metadata({})
        shutil.copytree(EXAMPLES / TASK, tasks / "team-task")
        task_file = tasks / "team-task" / run.protocol.TASK_FILE
        task = json.loads(task_file.read_text(encoding="utf-8"))
        task.update(name="team-task", family="teams", class_name="coherent")
        task_file.write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
        for extra in ((), ("--confirm-spend",)):
            status, out, err = self.main(self.argv(*extra, tasks=str(tasks), arms=None))
            self.assertEqual(status, run.NOTHING_LAUNCHED)
            self.assertIn(f"the selected tasks under {tasks} span two families: {TASK} is autonomy and team-task is teams", err)
            self.assertNotIn("every planned attempt", out)
        self.assertFalse(self.out.exists())
        status, out, err = self.main(self.argv(tasks=str(tasks), select=["team-task"], arms=None))
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        self.assertResolved(out, "family", "teams")
        self.assertResolved(out, "selected", "team-task")
        self.assertResolved(out, "arms", ", ".join(arm.name for arm in run.ARMS["teams"]) + " (every arm of the teams family)")
        status, out, err = self.main(self.argv(tasks=str(tasks), select=[TASK], arms=None))
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        self.assertResolved(out, "family", "autonomy")
        self.assertResolved(out, "arms", ", ".join(arm.name for arm in run.ARMS["autonomy"]) + " (every arm of the autonomy family)")
        # A teams arm is refused against an autonomy selection by name.
        status, _, err = self.main(self.argv(tasks=str(tasks), select=[TASK], arms=["codex-multi"]))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("key arms names 'codex-multi', which is not an arm of the autonomy family", err)

    def test_only_a_codex_arm_needs_the_codex_binary_and_the_credential(self) -> None:
        absent = {"foe": str(self.foe), "codex": ABSENT_COMMAND, "credential": str(self.root / "absent.json")}
        status, out, err = self.main(self.argv(harnesses=absent))
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        self.assertIn("No attempt was launched", out)
        self.assertResolved(out, "codex", f"none; key harnesses.codex names {ABSENT_COMMAND!r}, which is absent from PATH, and no selected arm needs it")
        self.assertResolved(out, "credential", f"none; key harnesses.credential names {self.root / 'absent.json'}, which is not a file, and no selected arm needs it")
        status, out, err = self.main(self.argv(harnesses=absent, arms=["foe-configured", "codex-default"]))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn(f"the arms codex-default need harnesses.codex and harnesses.credential: key harnesses.codex names {ABSENT_COMMAND!r}, which is absent from PATH", err)
        self.assertNotIn("No attempt was launched", out)
        status, _, err = self.main(self.argv(harnesses={**absent, "codex": str(self.codex)}, arms=["codex-default"]))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn(f"key harnesses.credential names {self.root / 'absent.json'}, which is not a file", err)
        status, _, err = self.main(self.argv(harnesses={**absent, "codex": str(self.root / "auth.json"), "credential": str(self.credential)}, arms=["codex-default"]))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn(f"key harnesses.codex names {self.root / 'auth.json'}, which is not an executable file", err)
        # A bare command name is looked up on PATH and the plan states what it found.
        with mock.patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"}):
            status, out, err = self.main(self.argv(harnesses={"foe": str(self.foe), "codex": "fake-codex", "credential": str(self.credential)}, arms=["codex-default"]))
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        self.assertResolved(out, "codex", f"{self.codex} (the command 'fake-codex' on PATH)")
        self.assertFalse(self.out.exists())

    def test_a_budget_override_replaces_one_key_in_the_plan_and_is_refused_by_name_when_malformed(self) -> None:
        status, out, _ = self.main(self.argv(attempts=2, budget={"input_tokens": 1000, "seconds": 7}, arms=["foe-configured", "codex-default"]))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        # Four attempts at eight model calls, 1,000 input tokens, 4,000 output tokens, and 7 seconds each.
        self.assertRegex(out, r"\n\s+8\s+1,000\s+4,000\s+7\s+hello-solvable / foe-configured / 1")
        self.assertRegex(out, r"\n\s+32\s+4,000\s+16,000\s+28\s+every planned attempt")
        self.assertIn("the document's budget replaces input_tokens=1000, seconds=7", out)
        self.assertResolved(out, "budget", "input_tokens=1000, seconds=7 replace the same keys of every task's budget")
        self.assertIn("model calls are reported per arm", out)
        for bad, fragment in (
            ("input_tokens", "key budget is 'input_tokens'; expected an object over the keys"),
            ({"credits": 3}, "key budget.credits is not a budget key; the keys are"),
            ({"seconds": "many"}, "key budget.seconds is 'many'; expected a positive integer"),
            ({"seconds": 0}, "key budget.seconds is 0; expected a positive integer"),
            ({"seconds": True}, "key budget.seconds is True; expected a positive integer"),
        ):
            status, _, err = self.main(self.argv(budget=bad))
            self.assertEqual(status, run.NOTHING_LAUNCHED, bad)
            self.assertIn(fragment, err)
        self.assertEqual(run.parse_budget(None, Path("/run.json")), {})
        self.assertFalse(self.out.exists())

    def test_a_budget_the_documents_refuse_is_refused_before_the_plan_and_before_any_launch(self) -> None:
        """A seconds ceiling with no room for the check timeout is refused
        by the document builders; the runner refuses it up front rather than
        planning attempts that would fault after other arms spent credit."""
        for extra in ((), ("--confirm-spend",)):
            status, out, err = self.main(self.argv(*extra, budget={"seconds": 1}, arms=["codex-default", "foe-configured"]))
            self.assertEqual(status, run.NOTHING_LAUNCHED)
            self.assertIn("hello-solvable: the effective budget is refused: budget.seconds is 1", err)
            self.assertNotIn("every planned attempt", out)
        self.assertFalse(self.out.exists())

    def test_the_plan_records_a_task_with_tool_roots_as_not_applicable_to_the_shipped_arm(self) -> None:
        tools = self.root / "tools"
        tools.mkdir()
        tasks = self.tasks_with_metadata({"tool_roots": [str(tools)]})
        status, out, _ = self.main(self.argv(tasks=str(tasks), arms=["foe-configured", "foe-as-shipped"]))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("Recorded as not applicable and never launched:", out)
        self.assertIn(f"  {TASK} / foe-as-shipped / 1: the foe-as-shipped arm runs the built-in document builtin:coding, whose grants cannot take the tool roots task '{TASK}' names under metadata.tool_roots: {tools}", out)
        self.assertRegex(out, r"\n\s+8\s+16,000\s+4,000\s+300\s+hello-solvable / foe-configured / 1")
        # The totals count the attempt that runs alone.
        self.assertRegex(out, r"\n\s+8\s+16,000\s+4,000\s+300\s+every planned attempt")

    def test_a_task_presuming_a_program_absent_is_refused_when_an_arm_can_reach_it(self) -> None:
        # A program under a system root reaches every foe arm.
        tasks = self.tasks_with_metadata({"presumes_absent": "sh"})
        status, out, err = self.main(self.argv(tasks=str(tasks)))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertEqual(out, "")
        self.assertIn(f"task '{TASK}' presumes sh absent under metadata.presumes_absent, and ", err)
        self.assertIn("which the foe arms run commands from; the task's premise does not hold on this host", err)
        # A program no host holds leaves the plan as it is.
        tasks = self.tasks_with_metadata({"presumes_absent": ABSENT_COMMAND})
        status, out, err = self.main(self.argv(tasks=str(tasks)))
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        self.assertIn("No attempt was launched.", out)
        # A program under a tool root the document names reaches the foe arms through the execute grant.
        tools = self.root / "tools"
        tools.mkdir()
        (tools / ABSENT_COMMAND).write_text("#!/bin/sh\n", encoding="utf-8")
        (tools / ABSENT_COMMAND).chmod(0o755)
        status, out, err = self.main(self.argv(tasks=str(tasks), tool_roots=[str(tools)]))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertEqual(out, "")
        self.assertIn(f"{tools / ABSENT_COMMAND} is under {tools}, which the foe arms run commands from", err)
        # A Codex arm inherits the runner's PATH, which the foe arms never read.
        tasks = self.tasks_with_metadata({"presumes_absent": self.foe.name})
        with mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
            status, out, err = self.main(self.argv(tasks=str(tasks), arms=["codex-default"]))
            self.assertEqual(status, run.NOTHING_LAUNCHED)
            self.assertEqual(out, "")
            self.assertIn(f"{self.foe} is on the PATH the codex-default arm inherits", err)
            status, out, err = self.main(self.argv(tasks=str(tasks), arms=["foe-configured", "foe-as-shipped"]))
            self.assertEqual(status, run.NOTHING_LAUNCHED, err)
            self.assertIn("No attempt was launched.", out)
        # A value that is not a bare program name is refused by key.
        for bad in ("", "/usr/bin/sh", 3):
            tasks = self.tasks_with_metadata({"presumes_absent": bad})
            status, _, err = self.main(self.argv(tasks=str(tasks)))
            self.assertEqual(status, run.NOTHING_LAUNCHED)
            self.assertIn(f"task '{TASK}': metadata.presumes_absent is {bad!r}; expected the bare name of a program", err)

    def test_a_task_presuming_a_module_unimportable_is_refused_when_the_grading_interpreter_imports_it(self) -> None:
        tasks = self.tasks_with_metadata({"presumes_unimportable": "json"})
        status, out, err = self.main(self.argv(tasks=str(tasks)))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertEqual(out, "")
        self.assertIn(f"task '{TASK}' presumes json unimportable under metadata.presumes_unimportable, and {run.protocol.PYTHON} imports it from ", err)
        self.assertIn("which is an interpreter the attempt reaches; the task's premise does not hold on this host", err)
        # A module no host holds leaves the plan as it is.
        tasks = self.tasks_with_metadata({"presumes_unimportable": "cross_harness_absent_module"})
        status, out, err = self.main(self.argv(tasks=str(tasks)))
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        self.assertIn("No attempt was launched.", out)
        # A value that is not a module name is refused by key, and a keyword is not a module name: `import None` is a syntax error, which no import states.
        for bad in ("", "tomli-w", "os.path", 3, "None", "class", "lambda", "True"):
            tasks = self.tasks_with_metadata({"presumes_unimportable": bad})
            status, _, err = self.main(self.argv(tasks=str(tasks)))
            self.assertEqual(status, run.NOTHING_LAUNCHED)
            self.assertIn(f"task '{TASK}': metadata.presumes_unimportable is {bad!r}; expected a module name", err)

    def test_a_module_no_interpreter_imports_is_still_found_where_it_sits(self) -> None:
        """A copy an arm can put on a path defeats the premise even when no
        interpreter imports it, and the two arms do not read the same amount
        of the filesystem, so the task compares their sandboxes."""
        home = self.root / "home"
        vendored = home / "share" / "python" / "site-packages" / "pip" / "_vendor" / "cross_harness_vendored"
        vendored.mkdir(parents=True)
        (vendored / "__init__.py").write_text("value = 1\n", encoding="utf-8")
        with unittest.mock.patch.object(run.Path, "home", staticmethod(lambda: home)):
            found = run.module_on_disk("cross_harness_vendored")
            self.assertEqual(found, str(vendored))
            self.assertIsNone(run.module_on_disk("cross_harness_absent_module"))

    def test_a_distribution_is_found_under_either_separator(self) -> None:
        home = self.root / "home-wheels"
        cache = home / "cache" / "wheels"
        cache.mkdir(parents=True)
        (cache / "cross-harness-hyphenated").mkdir()
        with unittest.mock.patch.object(run.Path, "home", staticmethod(lambda: home)):
            self.assertEqual(run.module_on_disk("cross_harness_hyphenated"), str(cache / "cross-harness-hyphenated"))

    def test_the_unimportable_check_reads_neither_the_runner_directory_nor_the_interpreter_of_another_path(self) -> None:
        # A module beside the runner's working directory is importable only from there, and no grade and no arm command runs there.
        planted = self.root / "cwd"
        planted.mkdir()
        (planted / "cross_harness_planted.py").write_text("value = 1\n", encoding="utf-8")
        tasks = self.tasks_with_metadata({"presumes_unimportable": "cross_harness_planted"})
        argv = self.argv(tasks=str(tasks))
        entered = os.getcwd()
        os.chdir(planted)
        try:
            status, out, err = self.main(argv)
        finally:
            os.chdir(entered)
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        self.assertIn("No attempt was launched.", out)
        # Both the interpreter every grade names and the python3 an arm command resolves are asked.
        self.assertEqual(run.import_interpreters()[0], run.protocol.PYTHON)
        for interpreter in run.import_interpreters():
            self.assertTrue(Path(interpreter).is_file(), interpreter)

    def test_the_plan_states_the_tool_roots_of_the_document_arms(self) -> None:
        tools = self.root / "tools"
        tools.mkdir()
        status, out, _ = self.main(self.argv(tool_roots=[str(tools)]))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn(f"  {TASK}: {', '.join([*run.graphs.EXECUTE_ROOTS, str(tools)])}", out)
        self.assertIn("to the document's tool_roots", out)
        self.assertResolved(out, "tool roots", str(tools))
        status, out, _ = self.main(self.argv(arms=["codex-default"]))
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

    def test_the_grader_paths_a_trajectory_names_are_collected_from_every_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            workspace, grader = root / run.protocol.WORKSPACE, root / run.protocol.GRADER
            workspace.mkdir(parents=True)
            grader.mkdir()
            agent = {
                "commands": [
                    {"paths_named": ["src/greeting.py", "../grader/tests", str(grader / "oracle")]},
                    {"paths_named": [str(root) + "/grader-notes"]},
                ],
                "file_changes": [{"path": str(grader / "grade")}, {"path": str(workspace / "src" / "greeting.py")}],
                "tool_calls": [{"name": "read", "summary": "../grader/corruptions"}, {"name": "block", "summary": "goal-unreachable"}],
            }
            self.assertEqual(
                run.grader_paths_named({"agents": [agent]}, root),
                ["../grader/tests", str(grader / "oracle"), str(grader / "grade"), "../grader/corruptions"],
            )
            # A record written before the tool-call stream existed carries none, and no trajectory names nothing.
            self.assertEqual(run.grader_paths_named({"agents": [{"commands": [{"paths_named": ["README.md"]}]}]}, root), [])
            self.assertEqual(run.grader_paths_named(None, root), [])
            # A relative path is named however deep in the workspace the command ran, since the directory it ran in is not recorded.
            deep = {"agents": [{"commands": [{"paths_named": ["../../grader/grade", "../grader"]}]}]}
            self.assertEqual(run.grader_paths_named(deep, root), ["../../grader/grade", "../grader"])
            # An absolute path matches through a symbolic link in the output path, which resolves on one side only.
            link = Path(tmp) / "link"
            os.symlink(root.parent, link)
            self.assertEqual(run.grader_paths_named({"agents": [{"commands": [{"paths_named": [str(grader / "grade")]}]}]}, link / root.name), [str(grader / "grade")])

    def test_a_provider_condition_is_told_from_a_harness_stop(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="provider-outage-")
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        # The Codex arm names its last message in every record: absent means the arm wrote the evidence itself.
        unwritten = directory / "last.json"

        def result(harness: str, reported: dict, errors: list[str] | None = None, last: Path | None = None) -> run.ArmResult:
            record = {"errors": errors or [], "last_message": str(last or unwritten)}
            return run.ArmResult("arm", harness, 0, 1, 2, reported, None, Path("."), record)

        outage = result("foe", {"status": "blocked", "code": "recovery-exhausted", "evidence": ["provider unavailable through 5 attempts at step 2"]})
        self.assertIn("the provider ended the attempt: foe reported blocked with the code recovery-exhausted", run.provider_outage(outage))
        bound = result("foe", {"status": "blocked", "code": "recovery-exhausted", "evidence": ["node `assess` would fire again beyond its max_fires of 3"]})
        self.assertIsNone(run.provider_outage(bound))
        self.assertIsNone(run.provider_outage(result("foe", {"status": "blocked", "code": "goal-unreachable", "evidence": []})))
        self.assertIsNone(run.provider_outage(result("foe", {"status": "completed", "code": None, "evidence": []})))
        for evidence, condition in (
            ("exit status 1: 429 Too Many Requests", "a rate limit"),
            ("the stream ended: 503 Service Unavailable", "a server fault"),
            ("unauthorized: the credential was refused", "an authentication failure"),
        ):
            failed = result("codex", {"status": "failed", "code": None, "evidence": [evidence]})
            self.assertIn(f"the provider ended the attempt with {condition}", run.provider_outage(failed), evidence)
        # The stream errors the arm recorded carry the condition when the evidence does not.
        recorded = result("codex", {"status": "failed", "code": None, "evidence": ["exit status 1"]}, ["stream error: rate limit reached"])
        self.assertIn("a rate limit", run.provider_outage(recorded))
        self.assertIsNone(run.provider_outage(result("codex", {"status": "failed", "code": None, "evidence": ["exit status 1: the workspace is not a directory"]})))
        self.assertIsNone(run.provider_outage(result("codex", {"status": "blocked", "code": "goal-unreachable", "evidence": ["429"]})))
        # A report the model wrote is the model's own words, whatever status word the arm had to replace, so its
        # sentences about the task are read as a stop and stay in the rates. Only the stream's errors are the arm's.
        written = directory / "written.json"
        written.write_text(json.dumps({"status": "done", "code": None, "evidence": ["placeholder"]}), encoding="utf-8")
        for sentence in (
            "the workspace is read-only: writing crates/code/inventory.toml is forbidden by the sandbox",
            "no authentication is configured for the registry, so the crate cannot be fetched",
            "the check suite reported 502 failing assertions",
            "the request quota for the tool was reached",
        ):
            reported = {"status": "failed", "code": None, "evidence": [sentence]}
            self.assertIsNone(run.provider_outage(result("codex", reported, last=written)), sentence)
            # The same sentence in the arm's own evidence, where the run wrote no report, is the condition it names.
            self.assertIsNotNone(run.provider_outage(result("codex", reported)), sentence)
        # A record that names no last message says nothing about who wrote the evidence, and its stream errors alone are read.
        silent = run.ArmResult("arm", "codex", 0, 1, 2, {"status": "failed", "code": None, "evidence": ["429 Too Many Requests"]}, None, Path("."), {"errors": []})
        self.assertIsNone(run.provider_outage(silent))
        self.assertIn("a rate limit", run.provider_outage(result("codex", {"status": "failed", "code": None, "evidence": ["nothing to see"]}, ["429 Too Many Requests"], last=written)))

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
        self.assertEqual(run.parse_budget({"model_calls": 3, "seconds": 12}, Path("/run.json")), {"model_calls": 3, "seconds": 12})

    def test_the_outcomes_are_stated_side_by_side(self) -> None:
        reported = run.foe_arm.reported("exhausted", "input_tokens", ["the limit was crossed"])
        self.assertEqual(run.outcomes_side_by_side(reported, None), {"arm": {"status": "exhausted", "code": "input_tokens"}, "trajectory": None, "agree": None})
        trajectory_ = run.trajectory.Trajectory("codex", {}, [], run.trajectory.Outcome("exhausted", code="input_tokens"), "subscription")
        self.assertEqual(run.outcomes_side_by_side(reported, trajectory_)["agree"], True)
        differing = run.trajectory.Trajectory("codex", {}, [], run.trajectory.Outcome("completed"), "subscription")
        self.assertEqual(run.outcomes_side_by_side(reported, differing), {"arm": {"status": "exhausted", "code": "input_tokens"}, "trajectory": {"status": "completed", "code": None}, "agree": False})

    def test_a_tool_root_is_compared_and_granted_in_its_normalized_spelling(self) -> None:
        spelled = run.Settings(Path("/foe"), None, "autonomy", 1, "subscription", None, "m", "low", Path("/out"), None, "chat", 60, Path("/foe"), ("/usr/bin/", "/usr//bin"))
        self.assertEqual(run.needed_tool_roots(spelled, self.task), [])
        self.assertEqual(run.tool_roots(spelled, self.task), list(run.graphs.EXECUTE_ROOTS))
        shipped = run.arm_by_name("autonomy", "foe-as-shipped")
        self.assertIsNone(run.not_applicable(spelled, shipped, self.task))
        named = run.protocol.Task.from_dict({**self.task.to_dict(), "metadata": {"tool_roots": ["/usr/bin/"]}})
        self.assertEqual(run.needed_tool_roots(spelled, named), [])
        self.assertEqual(run.tool_roots(spelled, named), list(run.graphs.EXECUTE_ROOTS))
        self.assertIsNone(run.not_applicable(spelled, shipped, named))

    def test_the_recorded_tool_roots_are_the_built_in_roots_for_a_shipped_arm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = run.Settings(Path("/foe"), None, "autonomy", 1, "subscription", None, "m", "low", Path("/out"), None, "chat", 60, Path("/foe"), (tmp,))
            self.assertEqual(run.recorded_tool_roots(settings, run.arm_by_name("autonomy", "foe-as-shipped"), self.task), list(run.graphs.EXECUTE_ROOTS))
            self.assertEqual(run.recorded_tool_roots(settings, run.arm_by_name("teams", "foe-as-shipped"), self.task), list(run.graphs.EXECUTE_ROOTS))
            self.assertEqual(run.recorded_tool_roots(settings, run.arm_by_name("autonomy", "foe-configured"), self.task), [*run.graphs.EXECUTE_ROOTS, tmp])

    def test_the_presumed_absent_check_resolves_against_the_roots_of_the_selected_arms(self) -> None:
        arms = list(run.ARMS["autonomy"])
        foe_arms = [arm for arm in arms if arm.harness == "foe"]
        codex_arms = [arm for arm in arms if arm.harness == "codex"]
        plain = run.Settings(Path("/foe"), None, "autonomy", 1, "subscription", None, "m", "low", Path("/out"), None, "chat", 60, Path("/foe"))
        self.assertIsNone(run.presumed_absent_fault(plain, self.task, arms))
        absent = run.protocol.Task.from_dict({**self.task.to_dict(), "metadata": {"presumes_absent": ABSENT_COMMAND}})
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp) / "tools"
            tools.mkdir()
            (tools / ABSENT_COMMAND).write_text("#!/bin/sh\n", encoding="utf-8")
            (tools / ABSENT_COMMAND).chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
                self.assertIsNone(run.presumed_absent_fault(plain, absent, arms))
                granted = run.Settings(Path("/foe"), None, "autonomy", 1, "subscription", None, "m", "low", Path("/out"), None, "chat", 60, Path("/foe"), (str(tools),))
                fault = run.presumed_absent_fault(granted, absent, foe_arms)
                self.assertIn(f"{tools / ABSENT_COMMAND} is under {tools}, which the foe arms run commands from", fault or "")
                # The tool roots are the foe arms' grants; a Codex arm reads PATH alone.
                self.assertIsNone(run.presumed_absent_fault(granted, absent, codex_arms))
            with mock.patch.dict(os.environ, {"PATH": f"{tools}:/usr/bin:/bin"}):
                self.assertIsNone(run.presumed_absent_fault(plain, absent, foe_arms))
                fault = run.presumed_absent_fault(plain, absent, arms)
                self.assertIn(f"{tools / ABSENT_COMMAND} is on the PATH the codex-equivalent arm inherits", fault or "")
        system = run.protocol.Task.from_dict({**self.task.to_dict(), "metadata": {"presumes_absent": "sh"}})
        fault = run.presumed_absent_fault(plain, system, foe_arms)
        self.assertIn("presumes sh absent under metadata.presumes_absent", fault or "")
        self.assertIn("leave the task out with select", fault or "")

    def test_the_shipped_arm_is_not_applicable_only_to_a_task_needing_tool_roots(self) -> None:
        shipped = run.arm_by_name("autonomy", "foe-as-shipped")
        plain = run.Settings(Path("/foe"), None, "autonomy", 1, "subscription", None, "m", "low", Path("/out"), None, "chat", 60, Path("/foe"))
        self.assertIsNone(run.not_applicable(plain, shipped, self.task))
        self.assertEqual(run.needed_tool_roots(plain, self.task), [])
        with tempfile.TemporaryDirectory() as tmp:
            named = run.protocol.Task.from_dict({**self.task.to_dict(), "metadata": {"tool_roots": [tmp]}})
            reason = run.not_applicable(plain, shipped, named)
            self.assertIsNotNone(reason)
            self.assertIn("foe-as-shipped", reason)
            self.assertIn("builtin:coding", reason)
            self.assertIn(f"task '{self.task.name}' names under metadata.tool_roots: {tmp}", reason)
            self.assertIn("whose document names no tool roots", reason)
            for name in ("foe-configured", "foe-ablated", "codex-equivalent", "codex-default"):
                self.assertIsNone(run.not_applicable(plain, run.arm_by_name("autonomy", name), named), name)
            empty = run.protocol.Task.from_dict({**self.task.to_dict(), "metadata": {"tool_roots": []}})
            self.assertIsNone(run.not_applicable(plain, shipped, empty))
            # The run document's own tool roots make a task need them as much as its metadata does; a system root adds no need.
            documented = run.Settings(Path("/foe"), None, "autonomy", 1, "subscription", None, "m", "low", Path("/out"), None, "chat", 60, Path("/foe"), (tmp, "/usr/bin"))
            self.assertEqual(run.needed_tool_roots(documented, self.task), [tmp])
            self.assertEqual(run.needed_tool_roots(documented, named), [tmp])
            self.assertIsNone(run.not_applicable(documented, run.arm_by_name("autonomy", "foe-configured"), self.task))
            reason = run.not_applicable(documented, shipped, self.task)
            self.assertIsNotNone(reason)
            self.assertIn(f"the run document names under tool_roots: {tmp}", reason)
            self.assertNotIn("metadata.tool_roots", reason)
            system_only = run.Settings(Path("/foe"), None, "autonomy", 1, "subscription", None, "m", "low", Path("/out"), None, "chat", 60, Path("/foe"), ("/usr/bin",))
            self.assertIsNone(run.not_applicable(system_only, shipped, self.task))

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
        status, out, err = self.main(self.argv("--confirm-spend", arms=["foe-configured", "foe-as-shipped", "codex-equivalent", "codex-default"]))
        self.assertEqual(status, run.EVALUATED, err)
        summary = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(summary["attempts"], 4)
        self.assertEqual(summary["infrastructure_failures"], 0)
        settings = json.loads((self.out / run.RUN_FILE).read_text(encoding="utf-8"))
        self.assertEqual(settings["provenance"]["codex_version"], "codex-cli 0.153.4")
        self.assertTrue(settings["provenance"]["foe"]["runtime_binary"].startswith("sha256:"))
        self.assertIsNone(settings["provenance"]["foe"]["source_tree"])
        # The run file records the resolved document beside the settings.
        document = settings["document"]
        self.assertEqual(document["path"], str(self.root / "run.json"))
        self.assertEqual(document["tasks"], str(EXAMPLES))
        self.assertEqual((document["select"], document["arms"]), (None, ["foe-configured", "foe-as-shipped", "codex-equivalent", "codex-default"]))
        self.assertEqual(document["model"], {"route": "subscription", "name": "fixture-model", "effort": run.DEFAULT_EFFORT, "base_url": None, "codex_wire_api": run.DEFAULT_CODEX_WIRE_API})
        self.assertEqual(
            document["harnesses"],
            {"foe": str(self.foe), "codex": str(self.codex), "codex_named": str(self.codex), "credential": str(self.credential)},
        )
        self.assertEqual((document["out"], document["budget"], document["tool_roots"]), (str(self.out), {}, []))
        self.assertEqual((document["grader_timeout"], document["source_root"]), (run.DEFAULT_GRADER_TIMEOUT_SECONDS, str(self.foe)))
        self.assertEqual(document["foe_config_dir"], str(self.root / "foe-config"))
        self.assertEqual(settings["settings"]["foe"], str(self.foe))
        self.assertIn("Run document", settings["plan"])

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

    def fan_out_tasks(self, verdicts: str) -> Path:
        """A copy of the example task as a fan-out task of the teams family, whose grader records `verdicts` as its units file and names beta in a finding."""
        tasks = self.root / "tasks"
        shutil.copytree(EXAMPLES / TASK, tasks / TASK, dirs_exist_ok=True)
        task_file = tasks / TASK / run.protocol.TASK_FILE
        task = json.loads(task_file.read_text(encoding="utf-8"))
        task.update(family="teams", class_name="fan-out", metadata={"units": {"alpha": ["src"], "beta": ["tests"]}, "interface_paths": ["src/greeting.py"]})
        task_file.write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
        grade = tasks / TASK / run.protocol.GRADER / run.protocol.GRADE_SCRIPT
        grade.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/python3
                import json, pathlib, sys
                json.load(sys.stdin)
                pathlib.Path(__file__).resolve().with_name({run.teams.UNITS_FILE!r}).write_text({verdicts!r}, encoding="utf-8")
                print({run.teams.UNITS_PREFIX!r} + {verdicts!r}, file=sys.stderr)
                print("unit beta: hidden test tests/greeting_test.py exited 1")
                """
            ),
            encoding="utf-8",
        )
        grade.chmod(0o755)
        return tasks

    def test_a_fan_out_grade_records_each_units_verdict_beside_the_findings(self) -> None:
        tasks = self.fan_out_tasks(json.dumps({"alpha": True, "beta": False}))
        status, _, err = self.main(self.argv("--confirm-spend", tasks=str(tasks), arms=["foe-configured"]))
        self.assertEqual(status, run.EVALUATED, err)
        record = self.record("foe-configured")
        self.assertEqual(record["task"]["class_name"], "fan-out")
        self.assertEqual(record["grade"], {"passed": False, "findings": ["unit beta: hidden test tests/greeting_test.py exited 1"], "damage": [], "units": {"alpha": True, "beta": False}})
        self.assertEqual(record["classification"], "false-completion")
        # A task of another class carries no unit verdicts.
        other = self.root / "other"
        status, _, err = self.main(self.argv("--confirm-spend", name="other", out=str(other)))
        self.assertEqual(status, run.EVALUATED, err)
        plain = json.loads(run.record_path(other, TASK, "foe-configured", 1).read_text(encoding="utf-8"))
        self.assertEqual(plain["grade"], {"passed": True, "findings": [], "damage": [], "units": None})
        # A units file the grade left in another shape is a fault of the attempt, named by path.
        tasks = self.fan_out_tasks(json.dumps({"alpha": "yes"}))
        faulted = self.root / "faulted"
        status, _, err = self.main(self.argv("--confirm-spend", name="faulted", tasks=str(tasks), arms=["foe-configured"], out=str(faulted)))
        self.assertEqual(status, run.DEPLOYMENT_FAULT)
        record = json.loads(run.record_path(faulted, TASK, "foe-configured", 1).read_text(encoding="utf-8"))
        self.assertIsNone(record["grade"]["units"])
        self.assertIsNone(record["classification"])
        self.assertIn("the fan-out grade left an unreadable units record: ", record["infrastructure_error"])
        self.assertIn(f"{run.protocol.GRADER}/{run.teams.UNITS_FILE} holds", record["infrastructure_error"])
        self.assertIn("did not evaluate the harness", err)

    def test_a_stop_on_a_solvable_task_is_a_wrong_stop(self) -> None:
        self.behave("blocked")
        status, _, err = self.main(self.argv("--confirm-spend", arms=["foe-configured", "codex-default"]))
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

    def test_the_grader_is_absent_while_the_arm_runs_and_is_recorded_when_the_arm_names_it(self) -> None:
        self.behave("peek-at-grader")
        status, _, err = self.main(self.argv("--confirm-spend"))
        self.assertEqual(status, run.EVALUATED, err)
        record = self.record("foe-configured")
        root = Path(record["paths"]["root"])
        # The arm looked for the grader beside its workspace and found nothing there, and the root beside it
        # held the workspace and the task alone: no baseline of what the damage judgement protects.
        peeked = (Path(record["paths"]["workspace"]) / "peek.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(peeked, ["False", f"{run.protocol.TASK_FILE} {run.protocol.WORKSPACE}"])
        # The grade then ran against a grader materialized after the arm had exited.
        self.assertTrue((root / run.protocol.GRADER / run.protocol.GRADE_SCRIPT).is_file())
        self.assertFalse((root / run.protocol.PROTECTED_FILE).exists())
        self.assertEqual(record["grade"]["damage"], [])
        self.assertFalse(record["grade"]["passed"])
        self.assertEqual(record["classification"], "wrong-stop")
        # Every grader path the trajectory names is recorded as the evidence a reader checks.
        self.assertEqual(
            record["grader_paths_named"],
            ["../grader/grade", str(root / run.protocol.GRADER / "oracle" / "candidate.json")],
        )
        # An attempt that names none records none.
        self.behave("completed")
        second = self.root / "second"
        status, _, err = self.main(self.argv("--confirm-spend", name="second", out=str(second)))
        self.assertEqual(status, run.EVALUATED, err)
        plain = json.loads(run.record_path(second, TASK, "foe-configured", 1).read_text(encoding="utf-8"))
        self.assertEqual(plain["grader_paths_named"], [])

    def test_a_provider_outage_is_an_infrastructure_fault_rather_than_a_stop(self) -> None:
        self.behave("provider-outage")
        status, out, err = self.main(self.argv("--confirm-spend", arms=["foe-configured"]))
        self.assertEqual(status, run.DEPLOYMENT_FAULT)
        self.assertIn("did not evaluate the harness", err)
        record = self.record("foe-configured")
        self.assertIsNone(record["classification"])
        self.assertIn("the provider ended the attempt: foe reported blocked with the code recovery-exhausted", record["infrastructure_error"])
        self.assertIn("provider unavailable through 5 attempts", record["infrastructure_error"])
        # The arm result, the trajectory, and the grade stay in the record, so the attempt is auditable.
        self.assertEqual(record["reported"]["code"], "recovery-exhausted")
        self.assertEqual(record["trajectory"]["outcome"]["code"], "recovery-exhausted")
        self.assertFalse(record["grade"]["passed"])
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["infrastructure_failures"], 1)
        # A Codex run the provider failed is the same fault, named by its condition.
        self.behave("provider-error")
        codex_out = self.root / "codex-out"
        status, _, err = self.main(self.argv("--confirm-spend", name="codex", arms=["codex-default"], out=str(codex_out)))
        self.assertEqual(status, run.DEPLOYMENT_FAULT, err)
        record = json.loads(run.record_path(codex_out, TASK, "codex-default", 1).read_text(encoding="utf-8"))
        self.assertIsNone(record["classification"])
        self.assertIn("the provider ended the attempt with a rate limit", record["infrastructure_error"])
        self.assertIn("429", record["infrastructure_error"])
        self.assertEqual(record["reported"]["status"], "failed")
        self.assertIsNotNone(record["trajectory"])

    def test_a_workflow_recovery_bound_stays_a_classified_stop(self) -> None:
        self.behave("recovery-bound")
        status, _, err = self.main(self.argv("--confirm-spend", arms=["foe-configured"]))
        self.assertEqual(status, run.EVALUATED, err)
        record = self.record("foe-configured")
        self.assertIsNone(record["infrastructure_error"])
        self.assertEqual(record["classification"], "wrong-stop")
        self.assertEqual(record["reported"]["code"], "recovery-exhausted")

    def test_a_watcher_failure_during_the_run_is_a_fault_of_the_attempt(self) -> None:
        failure = run.WatcherFailure("the arm ran and its budget watcher then failed, so the attempt spent credit and reported nothing: the budget watcher for codex failed after 12 ms and terminated the run")
        with mock.patch.object(run, "run_arm", side_effect=failure):
            status, _, err = self.main(self.argv("--confirm-spend"))
        self.assertEqual(status, run.DEPLOYMENT_FAULT, err)
        record = self.record("foe-configured")
        self.assertIsNone(record["classification"])
        self.assertIn("the budget watcher for codex failed", record["infrastructure_error"])
        # The watcher raises once the child has ended, so the record says the arm ran rather than that it never started.
        self.assertTrue(record["infrastructure_error"].startswith("the arm ran and its budget watcher then failed"), record["infrastructure_error"])
        self.assertNotIn("could not launch", record["infrastructure_error"])
        # The grade still ran, so the attempt records what the workspace held when the arm ended.
        self.assertFalse(record["grade"]["passed"])

    def test_a_runner_fault_ends_the_run_rather_than_spending_the_remaining_attempts(self) -> None:
        # A RuntimeError that is not the watcher's comes from the runner itself, and every later attempt would carry it too.
        with mock.patch.object(run, "run_arm", side_effect=RuntimeError("the graph could not be built")):
            with self.assertRaises(RuntimeError):
                self.main(self.argv("--confirm-spend", attempts=2))
        self.assertFalse(run.record_path(self.out, TASK, "foe-configured", 1).exists())

    def test_resume_skips_a_recorded_attempt_and_removes_a_leftover_attempt_directory(self) -> None:
        status, out, err = self.main(self.argv("--confirm-spend", arms=["foe-configured", "foe-as-shipped"]))
        self.assertEqual(status, run.EVALUATED, err)
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["skipped"], 0)
        first = self.record("foe-configured")["started_ms"]
        # The record of the shipped arm is gone and its attempt directory is not, which is what an interrupted run leaves.
        run.record_path(self.out, TASK, "foe-as-shipped", 1).unlink()
        leftover = run.attempt_path(self.out, TASK, "foe-as-shipped", 1)
        self.assertTrue(leftover.is_dir())
        (leftover / "log" / "kept.txt").parent.mkdir(parents=True, exist_ok=True)
        (leftover / "log" / "kept.txt").write_text("the transcript of an attempt that spent credit", encoding="utf-8")
        status, out, err = self.main(self.argv("--confirm-spend", arms=["foe-configured", "foe-as-shipped"], resume=True))
        self.assertEqual(status, run.EVALUATED, err)
        summary = json.loads(out.strip().splitlines()[-1])
        # One attempt was launched of the two planned, and the other is counted as skipped.
        self.assertEqual((summary["attempts"], summary["planned"], summary["skipped"]), (1, 2, 1))
        self.assertIn(f"is already recorded and is skipped: {run.record_path(self.out, TASK, 'foe-configured', 1)}", err)
        aside = leftover.parent / f"{leftover.name}{run.INTERRUPTED_SUFFIX}01"
        self.assertIn(f"the attempt directory {leftover} holds no record and is set aside at {aside}", err)
        # The transcript of the interrupted attempt is kept, and the attempt ran again into a fresh directory.
        self.assertEqual((aside / "log" / "kept.txt").read_text(encoding="utf-8"), "the transcript of an attempt that spent credit")
        self.assertFalse((leftover / "log" / "kept.txt").exists())
        # The attempt that had a record kept it, and the one that had none ran and wrote one.
        self.assertEqual(self.record("foe-configured")["started_ms"], first)
        self.assertEqual(self.record("foe-as-shipped")["classification"], "correct-completion")
        # A second interruption of the same attempt is set aside beside the first rather than over it.
        run.record_path(self.out, TASK, "foe-as-shipped", 1).unlink()
        status, out, err = self.main(self.argv("--confirm-spend", arms=["foe-as-shipped"], resume=True))
        self.assertEqual(status, run.EVALUATED, err)
        self.assertTrue((leftover.parent / f"{leftover.name}{run.INTERRUPTED_SUFFIX}02").is_dir())
        self.assertTrue((aside / "log" / "kept.txt").is_file())

    def test_the_plan_leaves_out_the_spend_of_the_attempts_resume_will_skip(self) -> None:
        status, _, err = self.main(self.argv("--confirm-spend", arms=["foe-configured", "foe-as-shipped"]))
        self.assertEqual(status, run.EVALUATED, err)
        status, out, err = self.main(self.argv(arms=["foe-configured", "foe-as-shipped"], resume=True))
        self.assertEqual(status, run.NOTHING_LAUNCHED, err)
        self.assertIn("Already recorded under key resume and never launched again:", out)
        for arm in ("foe-configured", "foe-as-shipped"):
            self.assertIn(f"{TASK} / {arm} / 1: {run.record_path(self.out, TASK, arm, 1)}", out)
        # Every planned attempt is already recorded, so the spend the plan states is nothing.
        self.assertRegex(out, r"\n\s+0\s+0\s+0\s+0\s+every attempt this run launches")
        self.assertNotIn("every planned attempt", out)

    def test_resume_is_refused_when_it_is_not_a_boolean(self) -> None:
        status, _, err = self.main(self.argv(resume="yes"))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("key resume is 'yes'; expected true or false", err)

    def test_an_existing_record_is_refused_before_anything_runs(self) -> None:
        status, _, err = self.main(self.argv("--confirm-spend"))
        self.assertEqual(status, run.EVALUATED, err)
        status, _, err = self.main(self.argv("--confirm-spend"))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn("a record already exists", err)

    def test_a_leftover_attempt_directory_is_refused_by_name(self) -> None:
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
        status, out, err = self.main(self.argv("--confirm-spend", arms=["foe-as-shipped"]))
        self.assertEqual(status, run.EVALUATED, err)
        second = self.out / "run-02.json"
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["run"], str(second))
        self.assertEqual(json.loads((self.out / run.RUN_FILE).read_text(encoding="utf-8"))["arms"], ["foe-configured"])
        self.assertEqual(json.loads(second.read_text(encoding="utf-8"))["arms"], ["foe-as-shipped"])
        self.assertEqual(run.run_file_path(self.out), self.out / "run-03.json")

    def test_a_malformed_codex_session_is_a_fault_of_that_attempt_alone(self) -> None:
        self.behave("malformed-session")
        status, out, err = self.main(self.argv("--confirm-spend", arms=["codex-default", "foe-configured"]))
        self.assertEqual(status, run.DEPLOYMENT_FAULT, err)
        codex = self.record("codex-default")
        self.assertIsNone(codex["classification"])
        self.assertIn("could not be reduced to a trajectory", codex["infrastructure_error"])
        self.assertIn("rollout-", codex["infrastructure_error"])
        # The attempt planned after the faulted one still ran and was scored.
        self.assertEqual(self.record("foe-configured")["classification"], "wrong-stop")
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["infrastructure_failures"], 1)

    def test_a_budget_override_bounds_every_attempt_and_is_recorded(self) -> None:
        status, out, err = self.main(self.argv("--confirm-spend", budget={"seconds": 7, "model_calls": 3}, arms=["foe-configured", "foe-as-shipped", "codex-default"]))
        self.assertEqual(status, run.EVALUATED, err)
        settings = json.loads((self.out / run.RUN_FILE).read_text(encoding="utf-8"))
        self.assertEqual(settings["settings"]["budget_overrides"], {"seconds": 7, "model_calls": 3})
        self.assertEqual(settings["document"]["budget"], {"seconds": 7, "model_calls": 3})
        self.assertEqual(settings["budgets"][TASK], {"model_calls": 3, "input_tokens": 16000, "output_tokens": 4000, "seconds": 7})
        self.assertIn("the document's budget replaces seconds=7, model_calls=3", settings["plan"])
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
        status, out, err = self.main(self.argv("--confirm-spend", budget={"input_tokens": 1000}, arms=["codex-default"]))
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
        status, out, err = self.main(self.argv("--confirm-spend", tasks=str(tasks), tool_roots=[str(extra)], arms=["foe-as-shipped", "foe-configured"]))
        self.assertEqual(status, run.EVALUATED, err)
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["infrastructure_failures"], 0)
        self.assertIn(f"{TASK} under foe-as-shipped is not applicable: the foe-as-shipped arm runs the built-in document builtin:coding", err)
        merged = [*run.graphs.EXECUTE_ROOTS, str(extra), str(tools)]
        settings = json.loads((self.out / run.RUN_FILE).read_text(encoding="utf-8"))
        self.assertEqual(settings["tool_roots"][TASK], merged)
        self.assertEqual(settings["document"]["tool_roots"], [str(extra)])

        shipped = self.record("foe-as-shipped")
        self.assertIn(str(tools), shipped["not_applicable"])
        self.assertIn("metadata.tool_roots", shipped["not_applicable"])
        self.assertIsNone(shipped["classification"])
        self.assertIsNone(shipped["infrastructure_error"])
        self.assertIsNone(shipped["arm_result"])
        self.assertIsNone(shipped["grade"])
        # The built-in document grants the system roots alone, and the record says so.
        self.assertEqual(shipped["tool_roots"], list(run.graphs.EXECUTE_ROOTS))
        self.assertIsNotNone(shipped["ended_ms"])
        self.assertFalse(run.attempt_path(self.out, TASK, "foe-as-shipped", 1).exists())

        configured = self.record("foe-configured")
        self.assertEqual(configured["classification"], "correct-completion")
        self.assertEqual(configured["tool_roots"], merged)
        config = json.loads(Path(configured["arm_result"]["record"]["config"]).read_text(encoding="utf-8"))
        self.assertEqual(config["grants"]["execute"], [*merged, configured["paths"]["workspace"]])
        self.assertEqual(config["grants"]["read"], [configured["paths"]["workspace"], str(extra), str(tools)])

    def test_the_run_documents_tool_roots_record_the_shipped_arm_as_not_applicable(self) -> None:
        extra = self.root / "extra"
        extra.mkdir()
        status, _, err = self.main(self.argv("--confirm-spend", tool_roots=[str(extra)], arms=["foe-as-shipped", "foe-configured"]))
        self.assertEqual(status, run.EVALUATED, err)
        self.assertIn(
            f"{TASK} under foe-as-shipped is not applicable: the foe-as-shipped arm runs the built-in document builtin:coding, "
            f"whose grants cannot take the tool roots the run document names under tool_roots: {extra}",
            err,
        )
        shipped = self.record("foe-as-shipped")
        self.assertIn(f"the run document names under tool_roots: {extra}", shipped["not_applicable"])
        self.assertIsNone(shipped["arm_result"])
        self.assertFalse(run.attempt_path(self.out, TASK, "foe-as-shipped", 1).exists())
        self.assertEqual(self.record("foe-configured")["classification"], "correct-completion")

    def test_the_check_wrapper_exports_the_same_home_a_bash_command_receives(self) -> None:
        """A suite whose tests read HOME must see the same value under either
        arm. A Codex arm runs the suite as a shell command and inherits the
        runner's environment, so a foe arm whose wrapper left HOME unset
        would run a different check."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            script = run.write_check_script(Path(tmp) / "check", workspace, ["./checks/run.sh"])
            text = script.read_text(encoding="utf-8")
            import pwd as passwd_database
            home = passwd_database.getpwuid(os.getuid()).pw_dir
            self.assertIn(f"HOME={shlex.quote(home)}", text)
            # The workspace is the wrong value: a toolchain manager would look for
            # its installation there, find none, and attempt a denied download.
            self.assertNotIn(f"HOME={shlex.quote(str(workspace))}", text)
            self.assertIn("export PATH LANG HOME TMPDIR", text)
            printed = subprocess.run(
                ["/bin/sh", "-c", f"{shlex.quote(str(script))} >/dev/null 2>&1; :"], capture_output=True, text=True, check=False
            )
            self.assertEqual(printed.returncode, 0)

    def test_a_check_suite_that_cannot_run_is_a_fault_until_its_tool_is_granted(self) -> None:
        tool_name = "cross-harness-fixture-checker"
        tasks = self.tasks_with_metadata({"check": f"{tool_name} --version"})
        tools = self.root / "tools"
        tools.mkdir()
        (tools / tool_name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (tools / tool_name).chmod(0o755)

        status, out, err = self.main(self.argv("--confirm-spend", tasks=str(tasks)))
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
        status, _, err = self.main(self.argv("--confirm-spend", tasks=str(tasks), tool_roots=[str(tools)], out=str(granted)))
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


class Gates(Harness):
    """The hooks the evaluation's gates rely on: the two canaries."""

    def test_a_run_plants_both_canaries_records_them_and_passes_nothing_about_the_foe_one(self) -> None:
        status, _, err = self.main(self.argv("--confirm-spend", arms=["foe-configured", "codex-default"]))
        self.assertEqual(status, run.EVALUATED, err)
        written = json.loads((self.out / run.RUN_FILE).read_text(encoding="utf-8"))
        canaries = written["canaries"]
        codex_sentence, foe_sentence = canaries[run.CODEX_CONFIG_CANARY]["sentence"], canaries[run.FOE_CONFIG_CANARY]["sentence"]
        self.assertNotEqual(codex_sentence, foe_sentence)
        self.assertRegex(codex_sentence, r"^This sentence is the codex config isolation canary [0-9a-f-]{36};")
        self.assertRegex(foe_sentence, r"^This sentence is the foe config isolation canary [0-9a-f-]{36};")
        self.assertEqual(written["settings"]["canaries"], {run.CODEX_CONFIG_CANARY: codex_sentence, run.FOE_CONFIG_CANARY: foe_sentence})
        # The foe canary lies in the document's foe configuration directory, in a file foe never reads; the run file names it, and the runner removed it after the attempts.
        canary = Path(canaries[run.FOE_CONFIG_CANARY]["path"])
        self.assertEqual(canary, self.root / "foe-config" / run.CANARY_FILE)
        self.assertEqual(written["document"]["foe_config_dir"], str(self.root / "foe-config"))
        self.assertEqual(canaries[run.FOE_CONFIG_CANARY]["placement"], f"AGENTS.md in foe's configuration directory {self.root / 'foe-config'}, removed once the attempts have ended")
        self.assertFalse(canary.exists())
        self.assertTrue(canary.parent.is_dir())
        # The Codex attempt's fresh CODEX_HOME holds the config canary, and its record names the file.
        codex = self.record("codex-default")["arm_result"]["record"]
        planted = Path(codex["config_canary_file"])
        self.assertEqual(planted, Path(codex["codex_home"]) / "config.toml")
        self.assertIn(codex_sentence, planted.read_text(encoding="utf-8"))
        self.assertEqual(canaries[run.CODEX_CONFIG_CANARY]["placement"], "config.toml in the fresh CODEX_HOME of every Codex attempt")
        self.assertIn("--ignore-user-config", codex["commands"][0])
        # Nothing given to foe carries either sentence: the document, the command line, and the task text are clean.
        foe = self.record("foe-configured")
        config = Path(foe["arm_result"]["record"]["config"]).read_text(encoding="utf-8")
        for sentence in (codex_sentence, foe_sentence):
            self.assertNotIn(sentence, config)
            self.assertNotIn(sentence, json.dumps(foe["arm_result"]["record"]["commands"]))
            self.assertNotIn(sentence, json.dumps(codex["commands"]))
        # Another run generates sentences of its own.
        other = self.root / "other"
        status, _, err = self.main(self.argv("--confirm-spend", name="other", arms=["foe-configured"], out=str(other)))
        self.assertEqual(status, run.EVALUATED, err)
        again = json.loads((other / run.RUN_FILE).read_text(encoding="utf-8"))["canaries"]
        self.assertNotEqual(again[run.CODEX_CONFIG_CANARY]["sentence"], codex_sentence)
        self.assertNotEqual(again[run.FOE_CONFIG_CANARY]["sentence"], foe_sentence)

    def test_the_foe_canary_is_planted_in_the_configuration_directory_during_the_attempts_and_removed_after_them(self) -> None:
        config_dir = self.root / "foe-config"
        sentence = "This sentence is the foe config isolation canary 22222222-2222-4222-8222-222222222222; a model request that carries it was built from a file the harness must never read."
        planted = run.plant_foe_canary(config_dir, sentence)
        self.assertEqual(planted, config_dir / "AGENTS.md")
        self.assertEqual(planted.read_text(encoding="utf-8"), sentence + "\n")
        # A later run into the same directory replaces the sentence; removal by the earlier run leaves the later run's file alone.
        later = sentence.replace("2222", "3333")
        self.assertEqual(run.plant_foe_canary(config_dir, later), planted)
        self.assertFalse(run.remove_foe_canary(planted, sentence))
        self.assertEqual(planted.read_text(encoding="utf-8"), later + "\n")
        self.assertTrue(run.remove_foe_canary(planted, later))
        self.assertFalse(planted.exists())
        self.assertFalse(run.remove_foe_canary(planted, later))
        # A file of the user's own at that path is refused by path and left as it is.
        planted.write_text("# The user's notes\n", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            run.plant_foe_canary(config_dir, sentence)
        self.assertIn(f"key foe_config_dir names {config_dir}, and {planted} exists there without a canary sentence", str(caught.exception))
        self.assertEqual(planted.read_text(encoding="utf-8"), "# The user's notes\n")
        status, _, err = self.main(self.argv("--confirm-spend"))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertIn(f"{planted} exists there without a canary sentence", err)
        self.assertFalse((self.out / run.RUN_FILE).exists())
        planted.unlink()
        # The default directory is foe's own, resolved like every other path of the document; the plan states it.
        status, out, _ = self.main(self.argv(foe_config_dir=None))
        self.assertEqual(status, run.NOTHING_LAUNCHED)
        self.assertResolved(out, "foe config dir", str(Path(run.DEFAULT_FOE_CONFIG_DIR).expanduser()))
        self.assertEqual(run.DEFAULT_FOE_CONFIG_DIR, "~/.config/foe")


if __name__ == "__main__":
    unittest.main()
