#!/usr/bin/python3
"""Unit tests for the containment matrix: no binary, no model, no sandbox."""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import containment_matrix as matrix  # noqa: E402


class Classification(unittest.TestCase):
    def test_the_marker_means_allowed_whatever_the_exit_status(self) -> None:
        self.assertEqual(matrix.classify(0, f"{matrix.MARKER}\n", ""), matrix.ALLOWED)
        self.assertEqual(matrix.classify(1, f"x\n{matrix.MARKER}\n", "warning"), matrix.ALLOWED)

    def test_the_kernel_and_sandbox_diagnostics_mean_denied(self) -> None:
        for stderr in (
            "cat: /home/u/outside/secret.txt: Permission denied\n",
            "bash: line 1: /home/u/ws/in.txt: Read-only file system\n",
            "bash: socket: Operation not permitted\n",
            "bash: connect: Network is unreachable\n",
        ):
            self.assertEqual(matrix.classify(1, "", stderr), matrix.DENIED, stderr)

    def test_anything_else_is_an_error_rather_than_a_verdict(self) -> None:
        self.assertEqual(matrix.classify(1, "", "bash: line 1: TMPDIR: TMPDIR is not set\n"), matrix.ERROR)
        self.assertEqual(matrix.classify(127, "", "bash: nosuch: command not found\n"), matrix.ERROR)
        self.assertEqual(matrix.classify(None, "", ""), matrix.ERROR)


class Probes(unittest.TestCase):
    def test_every_probe_prints_the_marker_only_on_success(self) -> None:
        for probe in matrix.PROBES:
            self.assertIn(f"&& echo {matrix.MARKER}", probe.command, probe.name)
            self.assertEqual(probe.command.count(matrix.MARKER), 1, probe.name)

    def test_every_placeholder_is_filled_by_the_fixture(self) -> None:
        fixture = matrix.Fixture(Path("/r"), Path("/r/workspace"), Path("/r/outside"), Path("/r/outside/secret.txt"), 4321)
        for probe in matrix.PROBES:
            command = fixture.command(probe)
            for placeholder in ("{workspace}", "{outside}", "{secret}", "{port}"):
                self.assertNotIn(placeholder, command, probe.name)
        self.assertIn("/dev/tcp/127.0.0.1/4321", fixture.command(matrix.PROBES[8]))
        self.assertIn('"${TMPDIR:?', fixture.command(matrix.PROBES[7]), "the shell expansion survives substitution")

    def test_expectations_name_only_probes_and_configurations_that_exist(self) -> None:
        names = {probe.name for probe in matrix.PROBES}
        configurations = {configuration.name for configuration in matrix.CONFIGURATIONS}
        for configuration, expected in matrix.EXPECTED.items():
            self.assertIn(configuration, configurations)
            self.assertTrue(set(expected) <= names, configuration)
            self.assertTrue(set(expected.values()) <= {matrix.ALLOWED, matrix.DENIED}, configuration)


class FixtureAndListener(unittest.TestCase):
    def test_the_fixture_has_a_program_a_secret_and_a_link_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = matrix.materialize(Path(tmp) / "fixture", 1)
            self.assertTrue((fixture.workspace / "bin" / "hello").stat().st_mode & 0o100)
            self.assertEqual(fixture.secret.read_text(encoding="utf-8"), "the secret\n")
            self.assertTrue((fixture.workspace / "link").is_symlink())
            self.assertEqual((fixture.workspace / "link" / "secret.txt").resolve(), fixture.secret.resolve())
            self.assertTrue((fixture.workspace / "tests").is_dir() and (fixture.workspace / "src").is_dir())

    def test_the_listener_accepts_a_loopback_connection(self) -> None:
        listener = matrix.Listener()
        try:
            with socket.create_connection(("127.0.0.1", listener.port), timeout=5):
                pass
        finally:
            listener.close()


class FoeSide(unittest.TestCase):
    def test_the_document_states_the_configuration(self) -> None:
        fixture = matrix.Fixture(Path("/r"), Path("/r/workspace"), Path("/r/outside"), Path("/r/outside/secret.txt"), 1)
        tight = matrix.foe_document(matrix.CONFIGURATIONS[0], fixture)
        self.assertEqual(tight["grants"], {"read": ["/r/workspace"], "write": ["/r/workspace/src"], "execute": ["/bin", "/usr/bin"]})
        self.assertEqual(tight["sandbox"], {"mode": "required"})
        shape = matrix.foe_document(matrix.CONFIGURATIONS[1], fixture)
        self.assertEqual(shape["grants"]["write"], ["/r/workspace"])
        self.assertEqual(shape["grants"]["execute"], list(matrix.BUILTIN_EXECUTE))
        self.assertEqual(matrix.foe_document(matrix.CONFIGURATIONS[2], fixture)["sandbox"], {"mode": "off"})

    def test_the_responder_issues_one_bash_call_per_probe_then_ends(self) -> None:
        fixture = matrix.Fixture(Path("/r"), Path("/r/workspace"), Path("/r/outside"), Path("/r/outside/secret.txt"), 1)
        respond = matrix.foe_responder(fixture)
        first = respond({"messages": [{"role": "user", "content": []}]})
        starts = [chunk for chunk in first if chunk["kind"] == "tool_call_start"]
        self.assertEqual([chunk["id"] for chunk in starts], [matrix.call_id(probe) for probe in matrix.PROBES])
        self.assertTrue(all(chunk["name"] == "bash" for chunk in starts))
        self.assertEqual(first[-1], {"kind": "done", "stop": "tool", "usage": first[-1]["usage"]})
        second = respond({"messages": [{"role": "user", "content": []}, {"role": "tool", "content": []}]})
        self.assertEqual(second[0]["kind"], "text")
        self.assertEqual(second[-1]["stop"], "end")

    def test_cells_are_read_from_the_episode_log(self) -> None:
        events = [
            {"type": "episode/start", "data": {"sandbox": {"landlock_abi": 8}}},
            {
                "type": "tool/result",
                "data": {
                    "call_id": matrix.call_id(matrix.PROBES[0]),
                    "is_error": False,
                    "value": {"exit_code": 1, "stdout": "", "stderr": "cat: secret: Permission denied\n"},
                },
            },
            {
                "type": "tool/result",
                "data": {
                    "call_id": matrix.call_id(matrix.PROBES[3]),
                    "is_error": False,
                    "value": {"exit_code": 0, "stdout": f"{matrix.MARKER}\n", "stderr": ""},
                },
            },
            {"type": "tool/result", "data": {"call_id": "unrelated", "is_error": False, "value": {}}},
            {"type": "tool/result", "data": {"call_id": matrix.call_id(matrix.PROBES[5]), "is_error": True, "rendered": "bash: no executor"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "episode.jsonl"
            log.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            cells, sandbox = matrix.cells_from_log(log)
        self.assertEqual(sandbox, {"landlock_abi": 8})
        self.assertEqual(cells["read-secret"].result, matrix.DENIED)
        self.assertEqual(cells["write-source"].result, matrix.ALLOWED)
        self.assertEqual(cells["exec-system"].result, matrix.ERROR)
        self.assertNotIn("unrelated", cells)
        self.assertEqual(len(cells), 3)


class Report(unittest.TestCase):
    def test_surprises_name_only_documented_cells_that_differ(self) -> None:
        cells = {
            "foe-required-tight": {
                "read-secret": matrix.Cell(matrix.ALLOWED, 0, matrix.MARKER, ""),
                "write-source": matrix.Cell(matrix.ALLOWED, 0, matrix.MARKER, ""),
            },
            "codex-read-only": {"exec-workspace": matrix.Cell(matrix.ALLOWED, 0, matrix.MARKER, "")},
        }
        self.assertEqual(matrix.surprises(cells), ["foe-required-tight / read-secret: expected denied, observed allowed"])

    def test_the_table_marks_a_surprise_and_a_missing_cell(self) -> None:
        cells = {"foe-required-tight": {"read-secret": matrix.Cell(matrix.ALLOWED, 0, matrix.MARKER, "")}}
        rendered = matrix.table(cells, ["foe-required-tight", "codex-read-only"])
        lines = rendered.splitlines()
        self.assertTrue(lines[0].startswith("| probe | access | `foe-required-tight` | `codex-read-only` |"))
        self.assertIn("| `read-secret` | read a file outside the workspace | **allowed** (expected denied) | — |", lines)
        self.assertEqual(len(lines), 2 + len(matrix.PROBES))


if __name__ == "__main__":
    unittest.main()
