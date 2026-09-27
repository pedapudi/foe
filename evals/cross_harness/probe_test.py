#!/usr/bin/python3
"""Unit tests for the environment probe: fake binaries, fake episode logs, no model."""

from __future__ import annotations

import http.server
import json
import shlex
import socket
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe  # noqa: E402

DENYING_SANDBOX = 'echo "/bin/bash: line 1: written.txt: Read-only file system" >&2; exit 1'


def fake_codex(directory: Path, write_sandbox: str = DENYING_SANDBOX, environment_sandbox: str = 'exec "$@"', version: str = 'echo "codex-cli 0.153.4"; exit 0') -> Path:
    """A codex stand-in: `--version` runs `version`; `sandbox` runs one of two bodies.

    `write_sandbox` handles the write command of the sandbox check and
    `environment_sandbox` handles every other command, with the command
    after `--` in the positional parameters.
    """
    script = directory / "codex"
    script.write_text(
        "#!/bin/bash\n"
        f'if [ "$1" = "--version" ]; then {version}; fi\n'
        'while [ "$1" != "--" ]; do shift; done; shift\n'
        'case "$*" in\n'
        f'  *"echo x > "*) {write_sandbox};;\n'
        f"  *) {environment_sandbox};;\n"
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def fake_runner(events: list[dict[str, Any]], status: int = 0, write_target: bool = False, raw_log: str | None = None, spill: dict[str, str] | None = None):
    """An episode runner that writes `events` as the log and reports `status`.

    `raw_log` replaces the rendered events verbatim, and `spill` maps
    filenames under the episode's `spill/` to their content.
    """

    def run(foe: Path, config: Path, log_dir: Path, responder) -> tuple[int, Path]:
        document = json.loads(config.read_text(encoding="utf-8"))
        assert document["sandbox"] == {"mode": "required"}, config
        first = responder({"messages": [{"role": "user", "content": []}]})
        target = shlex.split(json.loads(first[1]["delta"])["command"])[-1]
        if write_target:
            Path(target).write_text("x\n", encoding="utf-8")
        episode = log_dir / "ep_fake"
        episode.mkdir(parents=True, exist_ok=True)
        text = "\n".join(json.dumps(event) for event in events) + "\n" if raw_log is None else raw_log
        (episode / "episode.jsonl").write_text(text, encoding="utf-8")
        for name, content in (spill or {}).items():
            (episode / "spill").mkdir(exist_ok=True)
            (episode / "spill" / name).write_text(content, encoding="utf-8")
        return status, episode

    return run


def denied_events(abi: int = 7, mode: str = "required") -> list[dict[str, Any]]:
    return [
        {"seq": 0, "type": "episode/start", "data": {"runtime": {"version": "0.2.0", "build": "sha256:abc"}, "sandbox": {"mode": mode, "landlock_abi": abi}}},
        {
            "seq": 1,
            "type": "tool/result",
            "data": {
                "call_id": probe.FOE_CALL_ID,
                "name": "bash",
                "is_error": False,
                "value": {"exit_code": 1, "stdout": "", "stderr": "/bin/bash: line 1: written.txt: Permission denied\n", "permission_denial": "possible"},
            },
        },
        {"seq": 2, "type": "episode/end", "data": {"outcome": {"kind": "completed", "value": "The probe ran."}}},
    ]


class FoeSandbox(unittest.TestCase):
    def test_the_document_grants_the_workspace_alone_and_requires_the_sandbox(self) -> None:
        document = probe.foe_document(Path("/r/workspace"))
        self.assertEqual(document["grants"], {"read": ["/r/workspace"], "write": ["/r/workspace"], "execute": ["/bin", "/usr/bin"]})
        self.assertEqual(document["sandbox"], {"mode": "required"})
        self.assertEqual(document["tools"], ["bash"])

    def test_the_responder_writes_the_target_once_then_ends(self) -> None:
        respond = probe.foe_responder(Path("/r/outside/written.txt"))
        first = respond({"messages": [{"role": "user", "content": []}]})
        self.assertEqual(first[0], {"kind": "tool_call_start", "id": probe.FOE_CALL_ID, "name": "bash"})
        self.assertEqual(json.loads(first[1]["delta"])["command"], "echo x > /r/outside/written.txt")
        self.assertEqual(first[-1]["stop"], "tool")
        second = respond({"messages": [{"role": "user", "content": []}, {"role": "tool", "content": []}]})
        self.assertEqual(second[-1]["stop"], "end")

    def test_the_responder_quotes_a_target_with_a_space(self) -> None:
        respond = probe.foe_responder(Path("/r/my probe/written.txt"))
        command = json.loads(respond({"messages": [{"role": "user", "content": []}]})[1]["delta"])["command"]
        self.assertEqual(command, "echo x > '/r/my probe/written.txt'")

    def test_the_log_evidence_names_the_sandbox_runtime_result_and_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "episode.jsonl"
            log.write_text("\n".join(json.dumps(event) for event in denied_events()) + "\n", encoding="utf-8")
            evidence = probe.foe_log_evidence(log)
        self.assertEqual(evidence["sandbox"], {"mode": "required", "landlock_abi": 7})
        self.assertEqual(evidence["runtime"], {"version": "0.2.0", "build": "sha256:abc"})
        self.assertEqual(evidence["result"]["exit_code"], 1)
        self.assertIn("Permission denied", evidence["result"]["stderr"])
        self.assertEqual(evidence["outcome"], {"kind": "completed", "value": "The probe ran."})

    def test_a_truncated_log_line_raises_naming_the_log_and_the_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "episode.jsonl"
            log.write_text(json.dumps(denied_events()[0]) + '\n{"seq":1,"type":"tool/res', encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                probe.foe_log_evidence(log)
        self.assertIn(f"{log} line 2", str(raised.exception))

    def test_a_truncated_log_leaves_the_check_not_run(self) -> None:
        raw = json.dumps(denied_events()[0]) + '\n{"seq":1,"type":"tool/res'
        with tempfile.TemporaryDirectory() as tmp:
            check = probe.check_foe_sandbox(Path("/bin/true"), Path(tmp), fake_runner([], status=1, raw_log=raw))
        self.assertEqual(check.status, probe.NOT_RUN)
        self.assertIn("line 2 is not valid JSON", check.detail)
        self.assertIn("status 1", check.detail)

    def test_a_spilled_result_is_read_from_the_spill_file(self) -> None:
        events = denied_events()
        inline = events[1]["data"]["value"]
        events[1]["data"]["value"] = {"spill": "result-ab.json", "bytes": 9000, "is_error": False}
        with tempfile.TemporaryDirectory() as tmp:
            check = probe.check_foe_sandbox(Path("/bin/true"), Path(tmp), fake_runner(events, spill={"result-ab.json": json.dumps(inline)}))
        self.assertEqual(check.status, probe.PASSED, check.detail)
        self.assertEqual(check.evidence["result"]["exit_code"], 1)
        self.assertTrue(check.evidence["result"]["spill"].endswith("spill/result-ab.json"))

    def test_a_missing_spill_file_leaves_the_check_not_run_naming_the_locator(self) -> None:
        events = denied_events()
        events[1]["data"]["value"] = {"spill": "result-ab.json", "bytes": 9000, "is_error": False}
        with tempfile.TemporaryDirectory() as tmp:
            check = probe.check_foe_sandbox(Path("/bin/true"), Path(tmp), fake_runner(events))
        self.assertEqual(check.status, probe.NOT_RUN)
        self.assertIn("spill/result-ab.json", check.detail)
        self.assertIn("does not exist", check.detail)

    def test_a_denied_write_under_landlock_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            check = probe.check_foe_sandbox(Path("/bin/true"), Path(tmp), fake_runner(denied_events()))
        self.assertEqual(check.status, probe.PASSED, check.detail)
        self.assertTrue(check.required)
        self.assertFalse(check.evidence["target_exists"])

    def test_a_capability_denied_failure_passes(self) -> None:
        events = denied_events()
        events[1]["data"]["is_error"] = True
        events[1]["data"]["failure"] = {"code": "capability-denied", "message": "outside grants.write", "retryable": False, "details": {}}
        events[1]["data"]["value"] = {"exit_code": None, "stdout": "", "stderr": ""}
        with tempfile.TemporaryDirectory() as tmp:
            check = probe.check_foe_sandbox(Path("/bin/true"), Path(tmp), fake_runner(events))
        self.assertEqual(check.status, probe.PASSED, check.detail)

    def test_an_error_result_without_a_denial_is_not_run(self) -> None:
        events = denied_events()
        events[1]["data"]["is_error"] = True
        events[1]["data"]["failure"] = {"code": "timed-out", "message": "30 seconds", "retryable": True, "details": {}}
        events[1]["data"]["value"] = {"exit_code": None, "timed_out": True, "stderr": ""}
        with tempfile.TemporaryDirectory() as tmp:
            check = probe.check_foe_sandbox(Path("/bin/true"), Path(tmp), fake_runner(events))
        self.assertEqual(check.status, probe.NOT_RUN)
        self.assertIn("failure code timed-out", check.detail)

    def test_a_write_that_landed_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            check = probe.check_foe_sandbox(Path("/bin/true"), Path(tmp), fake_runner(denied_events(), write_target=True))
        self.assertEqual(check.status, probe.FAILED)
        self.assertIn("outside the write grant", check.detail)

    def test_a_write_that_landed_in_a_previous_run_does_not_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe.check_foe_sandbox(Path("/bin/true"), Path(tmp), fake_runner(denied_events(), write_target=True))
            check = probe.check_foe_sandbox(Path("/bin/true"), Path(tmp), fake_runner(denied_events()))
        self.assertEqual(check.status, probe.PASSED, check.detail)

    def test_no_landlock_fails_and_names_the_abi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            check = probe.check_foe_sandbox(Path("/bin/true"), Path(tmp), fake_runner(denied_events(abi=0)))
        self.assertEqual(check.status, probe.FAILED)
        self.assertIn("landlock_abi 0", check.detail)

    def test_a_write_without_a_denial_diagnostic_fails(self) -> None:
        events = denied_events()
        events[1]["data"]["value"] = {"exit_code": 0, "stdout": "", "stderr": ""}
        with tempfile.TemporaryDirectory() as tmp:
            check = probe.check_foe_sandbox(Path("/bin/true"), Path(tmp), fake_runner(events))
        self.assertEqual(check.status, probe.FAILED)
        self.assertIn("no denial diagnostic", check.detail)

    def test_an_episode_that_did_not_complete_could_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            check = probe.check_foe_sandbox(Path("/bin/true"), Path(tmp), fake_runner(denied_events()[:1], status=1))
        self.assertEqual(check.status, probe.NOT_RUN)
        self.assertIn("status 1", check.detail)
        self.assertEqual(check.evidence["runtime"], {"version": "0.2.0", "build": "sha256:abc"}, "the runtime is still read for the versions check")

    def test_a_runner_that_raises_could_not_run(self) -> None:
        def run(foe: Path, config: Path, log_dir: Path, responder) -> tuple[int, Path]:
            raise RuntimeError("the protocol closed")

        with tempfile.TemporaryDirectory() as tmp:
            check = probe.check_foe_sandbox(Path("/bin/true"), Path(tmp), run)
        self.assertEqual(check.status, probe.NOT_RUN)
        self.assertIn("the protocol closed", check.detail)


class CodexSandbox(unittest.TestCase):
    def test_a_refused_write_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp))
            check = probe.check_codex_sandbox(codex, Path(tmp) / "out")
        self.assertEqual(check.status, probe.PASSED, check.detail)
        self.assertEqual(check.evidence["exit_code"], 1)
        self.assertFalse(check.evidence["target_exists"])

    def test_a_write_that_landed_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp), write_sandbox='exec "$@"')
            check = probe.check_codex_sandbox(codex, Path(tmp) / "out")
        self.assertEqual(check.status, probe.FAILED)
        self.assertTrue(check.evidence["target_exists"])
        self.assertIn(check.evidence["target"], check.detail)

    def test_a_write_that_landed_under_a_path_with_a_space_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp), write_sandbox='exec "$@"')
            check = probe.check_codex_sandbox(codex, Path(tmp) / "my probe")
            self.assertFalse((Path(tmp) / "my").exists(), "the unquoted prefix of the path must not become a file")
        self.assertEqual(check.status, probe.FAILED, check.detail)
        self.assertTrue(check.evidence["target_exists"])

    def test_a_refusal_without_a_diagnostic_could_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp), write_sandbox='echo "sandbox unavailable" >&2; exit 3')
            check = probe.check_codex_sandbox(codex, Path(tmp) / "out")
        self.assertEqual(check.status, probe.NOT_RUN)
        self.assertIn("exit code 3", check.detail)


class CodexDecoyHome(unittest.TestCase):
    def test_the_check_is_optional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp))
            check = probe.check_codex_decoy_home(codex, Path(tmp) / "out")
        self.assertEqual(check.name, "codex-decoy-home")
        self.assertFalse(check.required)

    def test_an_environment_without_the_sentence_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp))
            check = probe.check_codex_decoy_home(codex, Path(tmp) / "out")
            self.assertEqual((Path(check.evidence["codex_home"]) / "AGENTS.md").read_text(encoding="utf-8").count(check.evidence["sentence"]), 1)
        self.assertEqual(check.status, probe.PASSED, check.detail)
        self.assertTrue(check.evidence["environment_names_codex_home"])
        self.assertEqual(check.evidence["command"], ["/usr/bin/env"])
        self.assertIn("codex-isolation-canary-model-request", check.detail)

    def test_the_sentence_in_the_environment_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp), environment_sandbox='env; echo "INSTRUCTIONS=$(cat "$CODEX_HOME/AGENTS.md")"')
            check = probe.check_codex_decoy_home(codex, Path(tmp) / "out")
        self.assertEqual(check.status, probe.FAILED)
        self.assertIn("environment", check.detail)

    def test_the_sentence_in_the_sandbox_output_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp), environment_sandbox='cat "$CODEX_HOME/AGENTS.md" >&2; env')
            check = probe.check_codex_decoy_home(codex, Path(tmp) / "out")
        self.assertEqual(check.status, probe.FAILED)
        self.assertIn("sandbox's own output", check.detail)

    def test_a_command_that_did_not_see_the_decoy_could_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp), environment_sandbox='echo "PATH=/usr/bin"')
            check = probe.check_codex_decoy_home(codex, Path(tmp) / "out")
        self.assertEqual(check.status, probe.NOT_RUN)
        self.assertIn("CODEX_HOME=", check.detail)


class Versions(unittest.TestCase):
    def test_both_versions_pass(self) -> None:
        foe_check = probe.Check("foe-kernel-sandbox", True, probe.PASSED, "", {"runtime": {"version": "0.2.0", "build": "sha256:abc"}})
        with tempfile.TemporaryDirectory() as tmp:
            check = probe.check_versions(fake_codex(Path(tmp)), foe_check)
        self.assertEqual(check.status, probe.PASSED)
        self.assertEqual(check.evidence["codex"], "codex-cli 0.153.4")
        self.assertEqual(check.detail, "foe 0.2.0 build sha256:abc; codex-cli 0.153.4")

    def test_an_unknown_foe_runtime_could_not_run(self) -> None:
        foe_check = probe.Check("foe-kernel-sandbox", True, probe.NOT_RUN, "the sandbox checks run only under --live")
        with tempfile.TemporaryDirectory() as tmp:
            check = probe.check_versions(fake_codex(Path(tmp)), foe_check)
        self.assertEqual(check.status, probe.NOT_RUN)
        self.assertIn("foe-kernel-sandbox is not_run", check.detail)
        self.assertEqual(check.evidence["codex"], "codex-cli 0.153.4")

    def test_a_missing_codex_could_not_run_and_names_the_flag(self) -> None:
        foe_check = probe.Check("foe-kernel-sandbox", True, probe.PASSED, "", {"runtime": {"version": "0.2.0", "build": "sha256:abc"}})
        check = probe.check_versions(None, foe_check)
        self.assertEqual(check.status, probe.NOT_RUN)
        self.assertIsNone(check.evidence["codex"])
        self.assertIn("--codex", check.detail)
        self.assertNotIn("printed nothing", check.detail)

    def test_a_codex_that_fails_to_start_could_not_run_and_names_the_exit_code(self) -> None:
        foe_check = probe.Check("foe-kernel-sandbox", True, probe.PASSED, "", {"runtime": {"version": "0.2.0", "build": "sha256:abc"}})
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp), version="echo 'error: unable to start'; exit 1")
            check = probe.check_versions(codex, foe_check)
        self.assertEqual(check.status, probe.NOT_RUN)
        self.assertIsNone(check.evidence["codex"])
        self.assertIn("exit code 1", check.detail)
        self.assertIn("unable to start", check.detail)

    def test_a_codex_that_prints_nothing_could_not_run(self) -> None:
        foe_check = probe.Check("foe-kernel-sandbox", True, probe.PASSED, "", {"runtime": {"version": "0.2.0", "build": "sha256:abc"}})
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp), version="exit 0")
            check = probe.check_versions(codex, foe_check)
        self.assertEqual(check.status, probe.NOT_RUN)
        self.assertIn("printed nothing", check.detail)


class ModelsHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - the name is the standard library's.
        status = 200 if self.path == "/v1/models" else 404
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data": []}')

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the name is the standard library's.
        return


class Route(unittest.TestCase):
    def serve(self) -> tuple[http.server.HTTPServer, str]:
        server = http.server.HTTPServer(("127.0.0.1", 0), ModelsHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, f"http://127.0.0.1:{server.server_address[1]}"

    def test_no_route_is_an_optional_check_that_did_not_run(self) -> None:
        check = probe.check_route(None)
        self.assertEqual((check.required, check.status), (False, probe.NOT_RUN))

    def test_a_reachable_route_passes(self) -> None:
        server, base = self.serve()
        try:
            check = probe.check_route(base + "/")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(check.status, probe.PASSED, check.detail)
        self.assertEqual(check.evidence, {"url": base + "/v1/models", "timeout_seconds": 5, "http_status": 200})

    def test_a_route_without_the_models_endpoint_fails(self) -> None:
        server, base = self.serve()
        try:
            check = probe.check_route(base + "/other")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(check.status, probe.FAILED)
        self.assertEqual(check.evidence["http_status"], 404)

    def test_a_closed_port_fails(self) -> None:
        with socket.socket() as holder:
            holder.bind(("127.0.0.1", 0))
            port = holder.getsockname()[1]
        check = probe.check_route(f"http://127.0.0.1:{port}")
        self.assertEqual(check.status, probe.FAILED)
        self.assertIn("not reachable", check.detail)


class ExitStatusAndReport(unittest.TestCase):
    def test_every_required_check_passed_is_zero(self) -> None:
        checks = [probe.Check("a", True, probe.PASSED, ""), probe.Check("b", False, probe.NOT_RUN, "")]
        self.assertEqual(probe.exit_status(checks), 0)

    def test_a_required_failure_is_one_even_beside_a_check_that_did_not_run(self) -> None:
        checks = [probe.Check("a", True, probe.NOT_RUN, ""), probe.Check("b", True, probe.FAILED, "")]
        self.assertEqual(probe.exit_status(checks), 1)

    def test_a_required_check_that_did_not_run_is_two(self) -> None:
        checks = [probe.Check("a", True, probe.PASSED, ""), probe.Check("b", True, probe.NOT_RUN, "")]
        self.assertEqual(probe.exit_status(checks), 2)

    def test_an_optional_failure_does_not_change_the_status(self) -> None:
        checks = [probe.Check("a", True, probe.PASSED, ""), probe.Check("b", False, probe.FAILED, "")]
        self.assertEqual(probe.exit_status(checks), 0)

    def test_the_table_has_one_row_per_check(self) -> None:
        checks = [probe.Check("a", True, probe.PASSED, "fine"), probe.Check("b", False, probe.NOT_RUN, "later")]
        lines = probe.table(checks).splitlines()
        self.assertEqual(lines[0], "| check | required | status | detail |")
        self.assertEqual(lines[2], "| `a` | yes | passed | fine |")
        self.assertEqual(lines[3], "| `b` | no | not_run | later |")

    def test_a_detail_with_a_pipe_or_a_line_break_stays_one_row(self) -> None:
        lines = probe.table([probe.Check("a", True, probe.NOT_RUN, "line one\nline two | x")]).splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[2], "| `a` | yes | not_run | line one line two \\| x |")

    def test_the_report_carries_the_checks_the_host_and_the_status(self) -> None:
        checks = [probe.Check("a", True, probe.FAILED, "broken", {"k": 1})]
        document = probe.report(checks, {"foe": "/f"})
        self.assertEqual(document["exit_status"], 1)
        self.assertEqual(document["host"], {"foe": "/f"})
        self.assertEqual(document["checks"], [{"name": "a", "required": True, "status": "failed", "detail": "broken", "evidence": {"k": 1}}])


class RunChecks(unittest.TestCase):
    NAMES = ["foe-kernel-sandbox", "codex-sandbox", "codex-decoy-home", "codex-isolation-canary-model-request", "versions", "route-models", "cargo-test-wall-time"]

    def test_the_live_checks_with_fakes_reach_status_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp))
            checks = probe.run_checks(Path("/bin/true"), codex, Path(tmp) / "out", None, True, fake_runner(denied_events()))
        by_name = {check.name: check for check in checks}
        self.assertEqual([check.name for check in checks], self.NAMES)
        self.assertEqual(probe.exit_status(checks), 0, [(check.name, check.status, check.detail) for check in checks])
        self.assertEqual((by_name["cargo-test-wall-time"].required, by_name["cargo-test-wall-time"].status), (False, probe.NOT_RUN))
        self.assertEqual((by_name["codex-isolation-canary-model-request"].required, by_name["codex-isolation-canary-model-request"].status), (False, probe.NOT_RUN))
        self.assertEqual((by_name["codex-decoy-home"].required, by_name["codex-decoy-home"].status), (False, probe.PASSED))

    def test_the_live_checks_run_twice_into_the_same_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp))
            out = Path(tmp) / "out"
            probe.run_checks(Path("/bin/true"), codex, out, None, True, fake_runner(denied_events()))
            checks = probe.run_checks(Path("/bin/true"), codex, out, None, True, fake_runner(denied_events()))
        self.assertEqual(probe.exit_status(checks), 0, [(check.name, check.status, check.detail) for check in checks])

    def test_without_live_the_sandbox_checks_did_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp))
            checks = probe.run_checks(Path("/bin/true"), codex, Path(tmp) / "out", None, False, fake_runner(denied_events()))
        statuses = {check.name: check.status for check in checks}
        self.assertEqual(statuses["foe-kernel-sandbox"], probe.NOT_RUN)
        self.assertEqual(statuses["codex-sandbox"], probe.NOT_RUN)
        self.assertEqual(statuses["codex-decoy-home"], probe.NOT_RUN)
        self.assertEqual(statuses["versions"], probe.NOT_RUN)
        self.assertEqual(probe.exit_status(checks), 2)

    def test_without_codex_the_codex_checks_did_not_run_and_name_the_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checks = probe.run_checks(Path("/bin/true"), None, Path(tmp) / "out", None, True, fake_runner(denied_events()))
        by_name = {check.name: check for check in checks}
        self.assertEqual(by_name["foe-kernel-sandbox"].status, probe.PASSED)
        self.assertEqual(by_name["codex-sandbox"].status, probe.NOT_RUN)
        self.assertIn("--codex", by_name["codex-sandbox"].detail)
        self.assertIn("--codex", by_name["versions"].detail)
        self.assertEqual(probe.exit_status(checks), 2)


class ClearOutput(unittest.TestCase):
    def test_a_missing_or_empty_directory_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(probe.clear_output(Path(tmp) / "missing"))
            self.assertIsNone(probe.clear_output(Path(tmp)))

    def test_a_previous_run_is_removed_entry_by_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "foe" / "log").mkdir(parents=True)
            (out / "probe.json").write_text("{}", encoding="utf-8")
            (out / "probe.md").write_text("", encoding="utf-8")
            self.assertIsNone(probe.clear_output(out))
            self.assertEqual(list(out.iterdir()), [])

    def test_a_directory_with_other_content_is_refused_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "unrelated").mkdir()
            (out / "unrelated" / "notes.txt").write_text("keep\n", encoding="utf-8")
            (out / "probe.json").write_text("{}", encoding="utf-8")
            refusal = probe.clear_output(out)
            self.assertIsNotNone(refusal)
            self.assertIn(str(out), refusal)
            self.assertIn("unrelated", refusal)
            self.assertEqual((out / "unrelated" / "notes.txt").read_text(encoding="utf-8"), "keep\n")
            self.assertTrue((out / "probe.json").exists())

    def test_a_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain"
            plain.write_text("", encoding="utf-8")
            self.assertIn("not a directory", probe.clear_output(plain))


class Main(unittest.TestCase):
    def test_the_first_line_is_the_report_and_the_files_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp))
            out = Path(tmp) / "out"
            captured = StringIO()
            with redirect_stdout(captured):
                status = probe.main(["--foe", "/bin/true", "--codex", str(codex), "--out", str(out)], fake_runner(denied_events()))
            lines = captured.getvalue().splitlines()
            document = json.loads(lines[0])
            self.assertEqual(status, 2)
            self.assertEqual(document["exit_status"], 2)
            self.assertEqual(document["host"]["codex"], str(codex.resolve()))
            self.assertFalse(document["host"]["live"])
            self.assertEqual(lines[1], "")
            self.assertEqual(lines[2], "| check | required | status | detail |")
            self.assertEqual(json.loads((out / "probe.json").read_text(encoding="utf-8")), document)
            self.assertEqual((out / "probe.md").read_text(encoding="utf-8"), "\n".join(lines[2:]) + "\n")

    def test_a_second_run_replaces_the_first_in_the_same_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp))
            out = Path(tmp) / "out"
            arguments = ["--foe", "/bin/true", "--codex", str(codex), "--out", str(out), "--live"]
            with redirect_stdout(StringIO()):
                first = probe.main(arguments, fake_runner(denied_events()))
                second = probe.main(arguments, fake_runner(denied_events()))
            self.assertEqual((first, second), (0, 0))
            self.assertEqual(sorted(entry.name for entry in out.iterdir()), ["codex", "decoy-home", "foe", "probe.json", "probe.md"])

    def test_an_out_directory_with_other_content_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = fake_codex(Path(tmp))
            out = Path(tmp) / "out"
            (out / "unrelated").mkdir(parents=True)
            (out / "unrelated" / "notes.txt").write_text("keep\n", encoding="utf-8")
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = probe.main(["--foe", "/bin/true", "--codex", str(codex), "--out", str(out)], fake_runner(denied_events()))
            self.assertEqual(status, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("unrelated", stderr.getvalue())
            self.assertEqual((out / "unrelated" / "notes.txt").read_text(encoding="utf-8"), "keep\n")
            self.assertFalse((out / "probe.json").exists())

    def test_a_route_is_checked_without_live(self) -> None:
        server = http.server.HTTPServer(("127.0.0.1", 0), ModelsHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                codex = fake_codex(Path(tmp))
                captured = StringIO()
                with redirect_stdout(captured):
                    probe.main(["--foe", "/bin/true", "--codex", str(codex), "--out", str(Path(tmp) / "out"), "--route", f"http://127.0.0.1:{server.server_address[1]}"])
        finally:
            server.shutdown()
            server.server_close()
        document = json.loads(captured.getvalue().splitlines()[0])
        route = next(check for check in document["checks"] if check["name"] == "route-models")
        self.assertEqual((route["required"], route["status"]), (True, "passed"))

    def test_a_foe_that_is_not_executable_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "foe"
            plain.write_text("", encoding="utf-8")
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = probe.main(["--foe", str(plain), "--out", str(Path(tmp) / "out")])
        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("is not executable", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
