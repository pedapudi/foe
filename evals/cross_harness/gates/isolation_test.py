#!/usr/bin/python3
"""Unit tests for the isolation gate over synthetic run files, records, and harness logs."""

from __future__ import annotations

import contextlib
import hashlib
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

    def main(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = isolation.main([str(self.document), *extra])
        return status, out.getvalue(), err.getvalue()

    def write_run_file(self, name: str = run.RUN_FILE, codex: str = CODEX_SENTENCE, foe: str = FOE_SENTENCE, planted: dict[str, Any] | None | bool = True) -> None:
        """A run file recording both sentences and, unless `planted` says otherwise, the evidence that the foe canary was planted.

        The foe canary's path is recorded as the runner records it; the
        runner removes the file after the attempts, so none is written here.
        """
        canary = self.root / "foe-config" / run.CANARY_FILE
        foe_item: dict[str, Any] = {"sentence": foe, "path": str(canary)}
        if planted is True:
            foe_item[isolation.PLANTED_KEY] = {"sha256": hashlib.sha256((foe + "\n").encode("utf-8")).hexdigest(), "path": str(canary)}
        elif isinstance(planted, dict):
            foe_item[isolation.PLANTED_KEY] = planted
        run.write_json(self.out / name, {"canaries": {run.CODEX_CONFIG_CANARY: {"sentence": codex, "placement": "config.toml"}, run.FOE_CONFIG_CANARY: foe_item}})

    def write_record(self, arm: str, harness: str, arm_record: dict[str, Any] | None, attempt: int = 1, **fields: Any) -> Path:
        """A record; a launched foe attempt counts two model calls and states its canary present unless `fields` says otherwise, as the runner writes it."""
        if harness == "foe" and arm_record is not None:
            fields.setdefault("totals", {"model_calls": 2})
            fields.setdefault(isolation.PRESENT_KEY, True)
        record = {"task": {"name": "hello-solvable"}, "arm": arm, "harness": harness, "attempt": attempt, "not_applicable": None, "infrastructure_error": None, "arm_result": None if arm_record is None else {"record": arm_record}, **fields}
        path = run.record_path(self.out, "hello-solvable", arm, attempt)
        run.write_json(path, record)
        return path

    def foe_episode(self, name: str, system: str = "You are a coding agent.", messages: list[Any] | None = None, child_messages: list[Any] | None = None, tail: str = "") -> Path:
        """An episode directory with a root log and one child log, each holding a header and one request, and `tail` appended to each log."""
        episode = self.out / "attempts" / name / "log" / "ep_root"
        for directory, texts in ((episode, messages), (episode / "children" / "ep_child", child_messages)):
            directory.mkdir(parents=True)
            events = [
                {"seq": 0, "time": 1, "version": 3, "type": "episode/start", "data": {"id": directory.name}},
                {"seq": 1, "time": 2, "type": "request/header", "data": {"system": system, "tools": []}},
                {"seq": 2, "time": 3, "type": "model/request", "data": {"request_id": "rq_1", "messages": texts or [{"role": "user", "content": "task"}]}},
                {"seq": 3, "time": 4, "type": "assistant/message", "data": {"request_id": "rq_1", "text": CODEX_SENTENCE + " is a sentence the model may well repeat"}},
            ]
            (directory / "episode.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events) + tail, encoding="utf-8")
        return episode

    def codex_artifacts(self, name: str, session_lines: list[str] | None = None, event_lines: list[str] | None = None, plant: bool = True) -> dict[str, Any]:
        artifacts = self.out / "attempts" / name / "artifacts"
        home = artifacts / "codex-home"
        sessions = home / "sessions" / "2026" / "09" / "11"
        sessions.mkdir(parents=True)
        session = sessions / "rollout-1.jsonl"
        session.write_text("\n".join(session_lines or ['{"type": "session_meta", "payload": {"id": "t"}}', '{"type": "turn_context", "payload": {"model": "m"}}']) + "\n", encoding="utf-8")
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
        self.assertIn("verdict: isolated: every canary is absent from every recorded request", out)
        written = json.loads((self.out / isolation.RESULT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(written["leaked"], 0)
        self.assertEqual(written["canaries"], {run.CODEX_CONFIG_CANARY: CODEX_SENTENCE, run.FOE_CONFIG_CANARY: FOE_SENTENCE})
        self.assertEqual([item["planted"] for item in written["results"]], [None, True, None, None])
        self.assertEqual(written["qualification_failures"], [])

    def test_a_canary_in_a_foe_request_fails_the_gate_naming_the_log_and_the_event(self) -> None:
        self.write_run_file()
        episode = self.foe_episode("foe", child_messages=[{"role": "system", "content": "Rules: " + FOE_SENTENCE}])
        self.write_record("foe-configured", "foe", {"episode_dir": str(episode)})
        status, out, _ = self.main()
        self.assertEqual(status, isolation.LEAKED)
        self.assertIn(f"codex_config absent, foe_config PRESENT at {episode / 'children' / 'ep_child' / 'episode.jsonl'} seq 2 (model/request)", out)
        self.assertIn("verdict: leaked: 1 recorded requests carry a canary", out)
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
        self.assertIn("verdict: leaked: 3 recorded requests carry a canary", out)

    def test_an_unplanted_codex_canary_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a canary that was never written cannot be found, so its absence proves nothing."""
        self.write_run_file()
        self.write_record("codex-equivalent", "codex", self.codex_artifacts("codex", plant=False))
        status, out, _ = self.main()
        self.assertEqual(status, isolation.UNQUALIFIED)
        self.assertIn("the codex canary was NOT planted, so its absence proves nothing", out)
        self.assertIn("qualification failure: the codex canary file", out)
        written = json.loads((self.out / isolation.RESULT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(written["unplanted"], ["hello-solvable / codex-equivalent / 1"])

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

    def qualification(self) -> tuple[int, str, list[dict[str, str]]]:
        """Run the gate and return its status, its printed report, and the qualification failures it wrote."""
        status, out, err = self.main()
        written = json.loads((self.out / isolation.RESULT_FILE).read_text(encoding="utf-8"))
        # A gate that writes no qualification failures reports none, so the status assertion is what fails against it.
        return status, out + err, written.get("qualification_failures", [])

    def assert_unqualified(self, rule: str) -> None:
        status, out, failures = self.qualification()
        self.assertEqual(status, isolation.UNQUALIFIED, out)
        self.assertIn("verdict: unqualified: no recorded request carries a canary", out)
        self.assertTrue(any(rule in failure["rule"] for failure in failures), failures)

    def test_a_foe_attempt_whose_episode_directory_holds_no_log_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): an attempt with no episode log recorded no request, so an absence there proves nothing."""
        self.write_run_file()
        empty = self.out / "attempts" / "foe" / "log" / "ep_root"
        empty.mkdir(parents=True)
        self.write_record("foe-configured", "foe", {"episode_dir": str(empty)})
        self.assert_unqualified(f"holds no {isolation.LOG_NAME}")
        status, out, failures = self.qualification()
        self.assertIn("searched 0 request events in 0 episode logs", out)
        self.assertEqual(failures[0]["attempt"], "hello-solvable / foe-configured / 1")

    def test_a_foe_log_holding_no_request_event_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a log without request/header or model/request events recorded no request."""
        self.write_run_file()
        episode = self.out / "attempts" / "foe" / "log" / "ep_root"
        episode.mkdir(parents=True)
        (episode / isolation.LOG_NAME).write_text(json.dumps({"seq": 0, "type": "episode/start", "data": {}}) + "\n" + json.dumps({"seq": 1, "type": "assistant/message", "data": {"text": "done"}}) + "\n", encoding="utf-8")
        self.write_record("foe-configured", "foe", {"episode_dir": str(episode)})
        self.assert_unqualified("hold no well-formed request/header or model/request event")

    def test_a_foe_log_line_that_is_not_json_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a line that does not parse may be a request the search could not read."""
        self.write_run_file()
        episode = self.foe_episode("foe", tail='{"partial": tru')
        self.write_record("foe-configured", "foe", {"episode_dir": str(episode)})
        self.assert_unqualified(f"episode log {episode / isolation.LOG_NAME}:5 is not JSON")

    def test_fewer_foe_request_events_than_model_calls_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a log that records fewer requests than the trajectory counts model calls lost requests the search needed."""
        self.write_run_file()
        self.write_record("foe-configured", "foe", {"episode_dir": str(self.foe_episode("foe"))}, totals={"model_calls": 5})
        self.assert_unqualified("hold 4 request/header and model/request events, fewer than the 5 totals.model_calls")

    def test_a_codex_attempt_listing_no_session_file_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a Codex attempt without a session file recorded no request."""
        self.write_run_file()
        artifacts = self.codex_artifacts("codex")
        artifacts["session_files"] = []
        self.write_record("codex-equivalent", "codex", artifacts)
        self.assert_unqualified("the arm record lists no session file")

    def test_a_missing_codex_session_file_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a listed session file that is absent cannot be searched."""
        self.write_run_file()
        artifacts = self.codex_artifacts("codex")
        missing = self.out / "attempts" / "codex" / "artifacts" / "codex-home" / "sessions" / "absent.jsonl"
        artifacts["session_files"] = [*artifacts["session_files"], str(missing)]
        self.write_record("codex-equivalent", "codex", artifacts)
        self.assert_unqualified(f"codex record file {missing} is missing")

    def test_a_codex_attempt_without_a_canary_file_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a Codex attempt whose record names no canary file planted no canary."""
        self.write_run_file()
        artifacts = self.codex_artifacts("codex", plant=False)
        artifacts["config_canary_file"] = None
        self.write_record("codex-equivalent", "codex", artifacts)
        self.assert_unqualified("the codex canary file None is absent")

    def test_a_run_file_without_foe_planting_evidence_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a foe canary that nothing shows was planted proves nothing by its absence."""
        self.write_run_file(planted=False)
        self.write_record("foe-configured", "foe", {"episode_dir": str(self.foe_episode("foe"))})
        self.assert_unqualified(f"key canaries.{run.FOE_CONFIG_CANARY}.planted is absent")
        _, out, failures = self.qualification()
        self.assertEqual(failures[0]["attempt"], f"run file {run.RUN_FILE}")
        self.assertIn("foe canary planting NOT evidenced", out)

    def test_a_planting_digest_that_does_not_match_the_sentence_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a planted file whose digest differs from the recorded sentence did not hold that sentence."""
        canary = str(self.root / "foe-config" / run.CANARY_FILE)
        self.write_run_file(planted={"sha256": hashlib.sha256(b"another sentence\n").hexdigest(), "path": canary})
        self.write_record("foe-configured", "foe", {"episode_dir": str(self.foe_episode("foe"))})
        self.assert_unqualified("so the planted file did not hold the sentence")

    def test_a_fully_evidenced_clean_run_is_isolated(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): with every request recorded and both canaries planted, the absence of both canaries passes the gate."""
        self.write_run_file()
        self.write_record("foe-configured", "foe", {"episode_dir": str(self.foe_episode("foe"))}, totals={"model_calls": 2})
        self.write_record("codex-equivalent", "codex", self.codex_artifacts("codex"))
        status, out, failures = self.qualification()
        self.assertEqual(status, isolation.ISOLATED, out)
        self.assertEqual(failures, [])
        self.assertIn("run file run.json: foe canary planting evidenced", out)
        self.assertIn("verdict: isolated", out)

    def test_a_leak_beside_a_qualification_failure_reports_the_leak(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a canary found in a request fails the gate whatever else the evidence lacks."""
        self.write_run_file(planted=False)
        self.write_record("foe-configured", "foe", {"episode_dir": str(self.foe_episode("foe", system="Rules. " + FOE_SENTENCE))})
        status, out, failures = self.qualification()
        self.assertEqual(status, isolation.LEAKED, out)
        self.assertIn("verdict: leaked: 2 recorded requests carry a canary", out)
        self.assertEqual(len(failures), 1)

    def test_a_foe_attempt_whose_canary_was_not_present_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a foe canary planted at the run's start but absent when an attempt started proves nothing about that attempt."""
        self.write_run_file()
        self.write_record("foe-configured", "foe", {"episode_dir": str(self.foe_episode("foe"))}, **{isolation.PRESENT_KEY: False})
        self.assert_unqualified(f"the record's {isolation.PRESENT_KEY} is False")

    def test_a_foe_record_written_before_the_presence_key_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a record that does not state the canary present is treated as one whose canary was absent."""
        self.write_run_file()
        path = self.write_record("foe-configured", "foe", {"episode_dir": str(self.foe_episode("foe"))})
        record = json.loads(path.read_text(encoding="utf-8"))
        del record[isolation.PRESENT_KEY]
        run.write_json(path, record)
        self.assert_unqualified(f"the record's {isolation.PRESENT_KEY} is None")

    def test_a_foe_record_without_an_integer_model_call_count_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): without totals.model_calls, as when normalization failed, the recorded requests cannot be counted against the calls made."""
        self.write_run_file()
        self.write_record("foe-configured", "foe", {"episode_dir": str(self.foe_episode("foe"))}, totals=None)
        self.assert_unqualified("totals.model_calls is None, not an integer")

    def test_a_foe_request_event_without_a_payload_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a request event whose data is null recorded no request, so it neither counts nor qualifies."""
        self.write_run_file()
        episode = self.out / "attempts" / "foe" / "log" / "ep_root"
        episode.mkdir(parents=True)
        (episode / isolation.LOG_NAME).write_text(json.dumps({"seq": 0, "type": "model/request", "data": None}) + "\n", encoding="utf-8")
        self.write_record("foe-configured", "foe", {"episode_dir": str(episode)}, totals={"model_calls": 1})
        self.assert_unqualified("seq 0 (model/request) carries no request")
        _, out, _ = self.qualification()
        self.assertIn("searched 0 request events in 1 episode logs", out)

    def test_a_codex_request_record_without_a_payload_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a response_item record without a payload recorded no request."""
        self.write_run_file()
        self.write_record("codex-equivalent", "codex", self.codex_artifacts("codex", session_lines=['{"type": "response_item"}']))
        self.assert_unqualified("is a response_item record without a payload")
        _, _, failures = self.qualification()
        self.assertTrue(any("with a payload, so no request was recorded" in failure["rule"] for failure in failures), failures)

    def test_fewer_codex_response_items_than_model_calls_fails_qualification(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): every Codex model call returns at least one item, so fewer response_item records than calls lost requests."""
        self.write_run_file()
        lines = ['{"type": "turn_context", "payload": {"model": "m"}}', '{"type": "response_item", "payload": {"type": "message"}}']
        self.write_record("codex-equivalent", "codex", self.codex_artifacts("codex", session_lines=lines), totals={"model_calls": 3})
        self.assert_unqualified("hold 1 response_item records, fewer than the 3 totals.model_calls")
        self.write_record("codex-equivalent", "codex", self.codex_artifacts("codex-2", session_lines=lines), totals={"model_calls": 1})
        status, out, failures = self.qualification()
        self.assertEqual([failure for failure in failures if failure["attempt"].startswith("hello-solvable / codex-equivalent / 1") and "response_item" in failure["rule"]], [])

    def test_a_run_whose_every_record_launched_nothing_has_no_records(self) -> None:
        """docs/evaluation.md "Gates before a result counts", item 6 (harness isolation): a run in which no harness launched recorded no request, so it passes nothing."""
        self.write_run_file(planted=False)
        self.write_record("foe-as-shipped", "foe", None, not_applicable="the built-in document cannot take the tool roots")
        status, out, failures = self.qualification()
        self.assertEqual(status, isolation.NO_RECORDS, out)
        self.assertIn("verdict: no records: no record launched a harness", out)
        self.assertEqual(failures, [])

    def test_out_replaces_the_document_out(self) -> None:
        """--out examines a run directory other than the document's out."""
        elsewhere = self.root / "elsewhere"
        self.out, original = elsewhere, self.out
        self.write_run_file()
        self.write_record("foe-configured", "foe", {"episode_dir": str(self.foe_episode("foe"))})
        status, out, err = self.main("--out", str(elsewhere))
        self.assertEqual(status, isolation.ISOLATED, out + err)
        self.assertIn(f"isolation gate over {elsewhere}", out)
        self.assertFalse(original.exists())


    # docs/evaluation.md, "Evidence archive": the gate reads a copy of the
    # state root, such as the extracted archive, in place of the paths the
    # records name under the home directory of the host that ran the run.
    def test_state_root_reads_a_copy_in_place_of_the_recorded_home_directory(self) -> None:
        copy = self.root / "copy"
        recorded = "/home/writer-absent/.local/state/foe/cross-harness/run-a"
        self.out = copy / "run-a"
        self.document.write_text(json.dumps({"tasks": str(EXAMPLES), "model": {"route": "subscription", "name": "m"}, "harnesses": {"foe": str(self.foe)}, "out": recorded}), encoding="utf-8")

        def as_recorded(path: str) -> str:
            return f"{recorded}/{Path(path).relative_to(self.out).as_posix()}"

        self.write_run_file()
        self.write_record("foe-configured", "foe", {"episode_dir": as_recorded(str(self.foe_episode("foe")))})
        codex = self.codex_artifacts("codex")
        codex = {**codex, "session_files": [as_recorded(item) for item in codex["session_files"]], "events": as_recorded(codex["events"]), "config_canary_file": as_recorded(codex["config_canary_file"])}
        self.write_record("codex-equivalent", "codex", codex)
        status, out, err = self.main("--out", str(self.out))
        self.assertEqual(status, isolation.UNQUALIFIED, "without --state-root the recorded paths name nothing on this host")
        status, out, err = self.main("--state-root", str(copy))
        self.assertEqual(status, isolation.ISOLATED, out + err)
        self.assertIn(f"isolation gate over {self.out}", out)
        result = json.loads((self.out / isolation.RESULT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(result["out"], "~/.local/state/foe/cross-harness/run-a")
        self.assertEqual(result["document"], str(self.document.resolve()))
        self.assertEqual([item["planted"] for item in result["results"] if item["harness"] == "codex"], [True])

    def test_the_result_names_a_document_the_repository_holds_relative_to_it(self) -> None:
        self.assertEqual(isolation.repository_relative(isolation.REPOSITORY / "evals" / "cross_harness" / "runs" / "lean.json"), "evals/cross_harness/runs/lean.json")
        self.assertEqual(isolation.repository_relative(Path("/elsewhere/run.json")), "/elsewhere/run.json")


if __name__ == "__main__":
    unittest.main()
