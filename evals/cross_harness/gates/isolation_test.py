#!/usr/bin/python3
"""Unit tests for the isolation gate over synthetic run files, records, and harness logs."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import isolation  # noqa: E402
import run  # noqa: E402

EXAMPLES = HERE.parent / "tasks" / "examples"
CODEX_SENTENCE = "This sentence is the codex config isolation canary 11111111-1111-4111-8111-111111111111; a model request that carries it was built from a file the harness must never read."
FOE_SENTENCE = "This sentence is the foe config isolation canary 22222222-2222-4222-8222-222222222222; a model request that carries it was built from a file the harness must never read."


class Gate(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="isolation-test-")
        self.root = Path(self.temporary.name)
        self.foe = self.root / "fake-foe"
        self.foe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.foe.chmod(0o755)
        self.out = self.root / "out"
        self.document = self.root / "run.json"
        self.document.write_text(json.dumps({"tasks": str(EXAMPLES), "model": {"route": "subscription", "name": "m"}, "harnesses": {"foe": str(self.foe)}, "out": str(self.out)}), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def main(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = isolation.main([str(self.document)])
        return status, out.getvalue(), err.getvalue()

    def write_run_file(self, name: str = run.RUN_FILE, codex: str = CODEX_SENTENCE, foe: str = FOE_SENTENCE) -> None:
        # The foe canary's path is recorded as the runner records it; the runner removes the file after the attempts, so none is written here.
        canary = self.root / "foe-config" / run.CANARY_FILE
        run.write_json(self.out / name, {"canaries": {run.CODEX_CONFIG_CANARY: {"sentence": codex, "placement": "config.toml"}, run.FOE_CONFIG_CANARY: {"sentence": foe, "path": str(canary)}}})

    def write_record(self, arm: str, harness: str, arm_record: dict[str, Any] | None, attempt: int = 1, **fields: Any) -> Path:
        record = {"task": {"name": "hello-solvable"}, "arm": arm, "harness": harness, "attempt": attempt, "not_applicable": None, "infrastructure_error": None, "arm_result": None if arm_record is None else {"record": arm_record}, **fields}
        path = run.record_path(self.out, "hello-solvable", arm, attempt)
        run.write_json(path, record)
        return path

    def foe_episode(self, name: str, system: str = "You are a coding agent.", messages: list[Any] | None = None, child_messages: list[Any] | None = None) -> Path:
        """An episode directory with a root log and one child log, each holding a header and one request."""
        episode = self.out / "attempts" / name / "log" / "ep_root"
        for directory, texts in ((episode, messages), (episode / "children" / "ep_child", child_messages)):
            directory.mkdir(parents=True)
            events = [
                {"seq": 0, "time": 1, "version": 3, "type": "episode/start", "data": {"id": directory.name}},
                {"seq": 1, "time": 2, "type": "request/header", "data": {"system": system, "tools": []}},
                {"seq": 2, "time": 3, "type": "model/request", "data": {"request_id": "rq_1", "messages": texts or [{"role": "user", "content": "task"}]}},
                {"seq": 3, "time": 4, "type": "assistant/message", "data": {"request_id": "rq_1", "text": CODEX_SENTENCE + " is a sentence the model may well repeat"}},
            ]
            (directory / "episode.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events) + '{"partial": tru', encoding="utf-8")
        return episode

    def codex_artifacts(self, name: str, session_lines: list[str] | None = None, event_lines: list[str] | None = None, plant: bool = True) -> dict[str, Any]:
        artifacts = self.out / "attempts" / name / "artifacts"
        home = artifacts / "codex-home"
        sessions = home / "sessions" / "2026" / "09" / "11"
        sessions.mkdir(parents=True)
        session = sessions / "rollout-1.jsonl"
        session.write_text("\n".join(session_lines or ['{"type": "session_meta", "payload": {"id": "t"}}']) + "\n", encoding="utf-8")
        events = artifacts / "events.jsonl"
        events.write_text("\n".join(event_lines or ['{"type": "thread.started"}']) + "\n", encoding="utf-8")
        canary_file = home / "config.toml"
        if plant:
            canary_file.write_text(f'developer_instructions = "{CODEX_SENTENCE}"\n', encoding="utf-8")
        return {"codex_home": str(home), "session_files": [str(session)], "events": str(events), "config_canary_file": str(canary_file)}

    def test_a_run_whose_requests_carry_no_canary_passes_and_every_attempt_is_reported(self) -> None:
        self.write_run_file()
        self.write_record("foe-configured", "foe", {"episode_dir": str(self.foe_episode("foe"))})
        self.write_record("codex-equivalent", "codex", self.codex_artifacts("codex"))
        self.write_record("foe-as-shipped", "foe", None, not_applicable="the built-in document cannot take the tool roots")
        self.write_record("codex-default", "codex", None, infrastructure_error="the arm could not launch: codex binary is absent")
        status, out, err = self.main()
        self.assertEqual(status, isolation.ISOLATED, err)
        self.assertIn(f"isolation gate over {self.out}: 4 records in 1 run files", out)
        self.assertIn("hello-solvable / foe-configured / 1: foe, searched 4 request events in 2 episode logs: codex_config absent, foe_config absent", out)
        self.assertIn("hello-solvable / codex-equivalent / 1: codex, searched 2 files: every session file and the event stream: codex_config absent, foe_config absent; the codex canary was planted in CODEX_HOME", out)
        self.assertIn("hello-solvable / foe-as-shipped / 1: foe, searched nothing: the attempt was not applicable and never ran", out)
        self.assertIn("hello-solvable / codex-default / 1: codex, searched nothing: the arm did not launch (the arm could not launch: codex binary is absent)", out)
        self.assertIn("verdict: every canary is absent from every recorded request", out)
        written = json.loads((self.out / isolation.RESULT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(written["leaked"], 0)
        self.assertEqual(written["canaries"], {run.CODEX_CONFIG_CANARY: CODEX_SENTENCE, run.FOE_CONFIG_CANARY: FOE_SENTENCE})
        self.assertEqual([item["planted"] for item in written["results"]], [None, True, None, None])

    def test_a_canary_in_a_foe_request_fails_the_gate_naming_the_log_and_the_event(self) -> None:
        self.write_run_file()
        episode = self.foe_episode("foe", child_messages=[{"role": "system", "content": "Rules: " + FOE_SENTENCE}])
        self.write_record("foe-configured", "foe", {"episode_dir": str(episode)})
        status, out, _ = self.main()
        self.assertEqual(status, isolation.LEAKED)
        self.assertIn(f"codex_config absent, foe_config PRESENT at {episode / 'children' / 'ep_child' / 'episode.jsonl'} seq 2 (model/request)", out)
        self.assertIn("verdict: 1 recorded requests carry a canary", out)
        self.assertEqual(json.loads((self.out / isolation.RESULT_FILE).read_text(encoding="utf-8"))["leaked"], 1)

    def test_a_canary_in_a_foe_header_or_a_codex_session_fails_the_gate(self) -> None:
        self.write_run_file()
        self.write_record("foe-configured", "foe", {"episode_dir": str(self.foe_episode("foe", system="Global rules. " + CODEX_SENTENCE))})
        session = ['{"type": "session_meta"}', json.dumps({"type": "response_item", "payload": {"content": [{"text": "<user_instructions>" + CODEX_SENTENCE + "</user_instructions>"}]}})]
        self.write_record("codex-equivalent", "codex", self.codex_artifacts("codex", session_lines=session))
        status, out, _ = self.main()
        self.assertEqual(status, isolation.LEAKED)
        self.assertIn("seq 1 (request/header)", out)
        self.assertIn("codex_config PRESENT at " + str(self.out / "attempts" / "codex" / "artifacts" / "codex-home" / "sessions" / "2026" / "09" / "11" / "rollout-1.jsonl") + ":2", out)
        # The root and child headers both carry the sentence, and the session file once: three hits.
        self.assertIn("verdict: 3 recorded requests carry a canary", out)

    def test_an_unplanted_codex_canary_is_reported_while_the_verdict_rests_on_absence(self) -> None:
        self.write_run_file()
        self.write_record("codex-equivalent", "codex", self.codex_artifacts("codex", plant=False))
        status, out, _ = self.main()
        self.assertEqual(status, isolation.ISOLATED)
        self.assertIn("the codex canary was NOT planted, so its absence proves nothing", out)
        self.assertIn("the codex canary was not planted for: hello-solvable / codex-equivalent / 1", out)

    def test_a_run_without_records_or_run_files_exits_two(self) -> None:
        status, _, err = self.main()
        self.assertEqual(status, isolation.NO_RECORDS)
        self.assertIn("holds 0 run files and 0 records", err)
        self.write_run_file()
        status, _, err = self.main()
        self.assertEqual(status, isolation.NO_RECORDS)
        self.assertIn("holds 1 run files and 0 records", err)

    def test_every_run_file_contributes_its_sentences_and_a_run_file_without_canaries_is_refused(self) -> None:
        self.write_run_file()
        other = "This sentence is the codex config isolation canary 33333333-3333-4333-8333-333333333333; a model request that carries it was built from a file the harness must never read."
        self.write_run_file("run-02.json", codex=other)
        self.assertEqual([path.name for path in isolation.run_files(self.out)], ["run-02.json", "run.json"])
        (self.out / "runway.json").write_text("{}", encoding="utf-8")
        self.assertEqual([path.name for path in isolation.run_files(self.out)], ["run-02.json", "run.json"])
        sentences = isolation.read_canaries(isolation.run_files(self.out))
        self.assertEqual(sentences, {run.CODEX_CONFIG_CANARY: other, run.FOE_CONFIG_CANARY: FOE_SENTENCE, f"{run.CODEX_CONFIG_CANARY}:run": CODEX_SENTENCE})
        session = [json.dumps({"type": "response_item", "payload": {"text": CODEX_SENTENCE}})]
        self.write_record("codex-equivalent", "codex", self.codex_artifacts("codex", session_lines=session))
        status, out, _ = self.main()
        self.assertEqual(status, isolation.LEAKED)
        self.assertIn("codex_config:run PRESENT", out)
        # A run file or a record that cannot be read is its own status, apart from a run with nothing to examine.
        run.write_json(self.out / "run-03.json", {"settings": {}})
        status, _, err = self.main()
        self.assertEqual(status, isolation.MALFORMED)
        self.assertIn(f"a run file or record under {self.out} cannot be read: ValueError: {self.out / 'run-03.json'}: key canaries is absent", err)
        (self.out / "run-03.json").unlink()
        run.write_json(run.record_path(self.out, "hello-solvable", "foe-ablated", 1), {"task": {"name": "hello-solvable"}})
        status, _, err = self.main()
        self.assertEqual(status, isolation.MALFORMED)
        self.assertIn(f"a run file or record under {self.out} cannot be read: KeyError: 'arm'", err)


if __name__ == "__main__":
    unittest.main()
