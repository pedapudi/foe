#!/usr/bin/python3
"""Unit tests for the recorded-cost reports.

These tests build small logs in the shape `docs/log-format.md` specifies and
assert what the reports read out of them. They run no episode and need no
binary.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import search_in_logs
import workflow_node_tokens
from log_facts import Episode, is_refinement, median, normalized_grep, percentile, search_command_heads


def event(seq: int, kind: str, data: dict, time: int = 0) -> dict:
    return {"seq": seq, "time": time or seq, "type": kind, "data": data}


def start(episode_id: str, parent: str | None = None) -> dict:
    return event(0, "episode/start", {"id": episode_id, "parent_id": parent, "task": "t"})


def message(seq: int, step: int, calls: list[dict], usage: dict | None = None) -> dict:
    return event(
        seq,
        "assistant/message",
        {
            "step": step,
            "request_id": f"rq_{step}",
            "text": "",
            "tool_calls": calls,
            "stop": "tool",
            "usage": usage or {"input": 100, "output": 10, "cache_read": 0},
        },
    )


def result(seq: int, call_id: str, name: str, duration: int, value: dict, rendered: str = "") -> dict:
    return event(
        seq,
        "tool/result",
        {
            "step": 1,
            "call_id": call_id,
            "name": name,
            "value": value,
            "rendered": rendered,
            "is_error": False,
            "spill": None,
            "duration_ms": duration,
            "synthetic": False,
        },
    )


def write_log(directory: Path, events: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "episode.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


class LogFactsTest(unittest.TestCase):
    def test_seeded_prefix_is_not_this_episode_s_work(self) -> None:
        """A forked child's copied events belong to the episode it forked."""
        events = [
            start("ep_child", "ep_parent"),
            message(1, 1, [{"id": "c1", "name": "grep", "args": {"pattern": "a"}}]),
            result(2, "c1", "grep", 5, {"matches": 1}),
            event(3, "seed/end", {}),
            message(4, 2, [{"id": "c2", "name": "grep", "args": {"pattern": "b"}}]),
            result(5, "c2", "grep", 7, {"matches": 2}),
        ]
        with tempfile.TemporaryDirectory() as work:
            path = write_log(Path(work) / "ep_child", events)
            episode = Episode(path, [json.loads(line) for line in path.read_text().splitlines()])
        self.assertEqual([call["call_id"] for call in episode.calls], ["c2"])
        self.assertEqual(episode.model_calls, 1)
        self.assertEqual(episode.id, "ep_child")

    def test_spilled_value_is_read_from_the_stored_file(self) -> None:
        events = [
            start("ep_a"),
            message(1, 1, [{"id": "c1", "name": "grep", "args": {"pattern": "a"}}]),
            event(
                2,
                "tool/result",
                {
                    "step": 1,
                    "call_id": "c1",
                    "name": "grep",
                    "value": {"spill": "result-abc.json", "bytes": 3, "is_error": False},
                    "rendered": "r",
                    "is_error": False,
                    "spill": "result-abc.json",
                    "duration_ms": 9,
                    "synthetic": False,
                },
            ),
        ]
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work) / "ep_a"
            path = write_log(directory, events)
            (directory / "spill").mkdir()
            (directory / "spill" / "result-abc.json").write_text(
                json.dumps({"matches": 42, "searched_files": 7, "hits": []}), encoding="utf-8"
            )
            episode = Episode(path, [json.loads(line) for line in path.read_text().splitlines()])
        self.assertEqual(episode.calls[0]["value"]["matches"], 42)

    def test_search_command_heads_finds_a_search_after_a_pipe(self) -> None:
        self.assertEqual(search_command_heads("cd x && find . -name '*.rs' | grep foo"), ["find", "grep"])
        self.assertEqual(search_command_heads("cargo test --workspace"), [])

    def test_grep_identity_separates_scope_from_pattern(self) -> None:
        same = normalized_grep({"pattern": "a", "path": "src"})
        self.assertEqual(same, normalized_grep({"pattern": "a", "path": "src"}))
        self.assertNotEqual(same, normalized_grep({"pattern": "a", "path": "docs"}))

    def test_refinement_relates_only_containing_patterns(self) -> None:
        self.assertTrue(is_refinement("fn parse", "fn parse_args"))
        self.assertTrue(is_refinement("fn parse_args", "fn parse"))
        self.assertFalse(is_refinement("fn parse", "fn parse"))
        self.assertFalse(is_refinement("fn parse", "struct Reader"))

    def test_median_and_percentile_on_a_known_sample(self) -> None:
        self.assertEqual(median([3, 1, 2]), 2)
        self.assertEqual(median([4, 1, 2, 3]), 2.5)
        self.assertEqual(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95), 10)
        self.assertEqual(percentile([], 0.95), 0.0)


class SearchReportTest(unittest.TestCase):
    def corpus(self, work: Path) -> Path:
        root = Path(work)
        write_log(
            root / "ep_run",
            [
                start("ep_run"),
                message(
                    1,
                    1,
                    [
                        {"id": "g1", "name": "grep", "args": {"pattern": "handler", "path": "src"}},
                        {"id": "g2", "name": "grep", "args": {"pattern": "handler", "path": "src"}},
                    ],
                ),
                result(2, "g1", "grep", 20, {"matches": 3, "searched_files": 10, "complete": True,
                                             "hits": [{"path": "/x/src/a.rs", "line": 1, "context": False}]}, "r"),
                result(3, "g2", "grep", 22, {"matches": 3, "searched_files": 10, "complete": True, "hits": []}, "r"),
                message(4, 2, [{"id": "r1", "name": "read", "args": {"path": "/x/src/a.rs"}}]),
                result(5, "r1", "read", 1, {}, "text"),
                message(6, 3, [{"id": "r2", "name": "read", "args": {"path": "/x/src/a.rs", "offset": 200}}]),
                result(7, "r2", "read", 1, {}, "text"),
                message(8, 4, [{"id": "b1", "name": "bash", "args": {"command": "grep -r x ."}}]),
                result(9, "b1", "bash", 500, {}, "out"),
                event(10, "episode/end", {"outcome": {"kind": "completed", "value": {}}}),
            ],
        )
        return root

    def test_identical_grep_and_repeated_read_are_counted(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            report = search_in_logs.corpus_report(self.corpus(Path(work)))
        totals = report["totals"]
        self.assertEqual(totals["grep_calls"], 2)
        self.assertEqual(totals["identical_grep_repeats"], 1)
        self.assertEqual(totals["read_calls"], 2)
        self.assertEqual(totals["read_repeat_calls"], 1)
        self.assertEqual(totals["read_identical_calls"], 0)
        self.assertEqual(totals["bash_search_calls"], 1)
        self.assertEqual(totals["grep_ms"], 42)
        self.assertEqual(totals["bash_search_ms"], 500)

    def test_a_second_read_of_the_same_window_is_counted_apart(self) -> None:
        """Paging through a file is not the same as re-fetching what is held."""
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            write_log(
                root / "ep_run",
                [
                    start("ep_run"),
                    message(1, 1, [{"id": "r1", "name": "read", "args": {"path": "/x/a.rs"}}]),
                    result(2, "r1", "read", 1, {}, "text"),
                    message(3, 2, [{"id": "r2", "name": "read", "args": {"path": "/x/a.rs"}}]),
                    result(4, "r2", "read", 1, {}, "text"),
                    event(5, "episode/end", {"outcome": {"kind": "completed", "value": {}}}),
                ],
            )
            report = search_in_logs.corpus_report(root)
        self.assertEqual(report["totals"]["read_repeat_calls"], 1)
        self.assertEqual(report["totals"]["read_identical_calls"], 1)

    def test_a_grep_whose_paths_a_later_call_names_is_not_counted_unused(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            report = search_in_logs.corpus_report(self.corpus(Path(work)))
        self.assertEqual(report["totals"]["grep_with_matches"], 1)
        self.assertEqual(report["totals"]["grep_no_named_path_used_later"], 0)

    def test_render_names_every_section(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            text = search_in_logs.render(search_in_logs.corpus_report(self.corpus(Path(work))))
        for heading in ("search cost", "repetition", "grep pattern shapes", "per run"):
            self.assertIn(heading, text)


class WorkflowReportTest(unittest.TestCase):
    def corpus(self, work: Path) -> Path:
        root = Path(work)
        write_log(
            root / "ep_root",
            [
                start("ep_root"),
                event(1, "workflow/node-start", {"node": "implement-task", "fire": 1, "inputs": [0],
                                                 "child_id": "ep_impl"}),
                event(2, "workflow/node-end", {"node": "implement-task", "fire": 1, "value": {},
                                               "rendered": "handoff", "duration_ms": 1000}),
                event(3, "workflow/node-start", {"node": "assess-task", "fire": 1, "inputs": [2],
                                                 "child_id": "ep_assess"}),
                event(4, "workflow/node-end", {"node": "assess-task", "fire": 1, "value": {},
                                               "rendered": "verdict", "duration_ms": 2000}),
                event(5, "workflow/branch", {"node": "assess-task", "fire": 1, "label": "accept",
                                             "successors": []}),
                event(6, "episode/end", {"outcome": {"kind": "completed", "value": {}}}),
            ],
        )
        write_log(
            root / "ep_root" / "children" / "ep_impl",
            [
                start("ep_impl", "ep_root"),
                message(1, 1, [{"id": "r1", "name": "read", "args": {"path": "src/a.rs"}}],
                        {"input": 1000, "output": 100, "cache_read": 0}),
                result(2, "r1", "read", 1, {}, "body"),
                event(3, "episode/end", {"outcome": {"kind": "completed", "value": {}}}),
            ],
        )
        write_log(
            root / "ep_root" / "children" / "ep_assess",
            [
                start("ep_assess", "ep_root"),
                message(1, 1, [{"id": "r2", "name": "read", "args": {"path": "src/a.rs"}}],
                        {"input": 3000, "output": 300, "cache_read": 500}),
                result(2, "r2", "read", 1, {}, "body"),
                event(3, "episode/end", {"outcome": {"kind": "completed", "value": {}}}),
            ],
        )
        return root

    def test_tokens_are_attributed_to_the_node_that_spent_them(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            report = workflow_node_tokens.corpus_report(self.corpus(Path(work)))
        self.assertEqual(report["workflow_runs"], 1)
        self.assertEqual(report["roles"]["implement"]["input_tokens"], 1000)
        self.assertEqual(report["roles"]["assess"]["input_tokens"], 3000)
        self.assertEqual(report["roles"]["assess"]["cache_read_tokens"], 500)
        self.assertAlmostEqual(report["checking_input_ratio"], 3.0)
        self.assertAlmostEqual(report["checking_input_share"], 0.75)

    def test_a_reread_of_a_path_the_implementer_read_is_counted(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            report = workflow_node_tokens.corpus_report(self.corpus(Path(work)))
        self.assertEqual(report["checking_rereads_of_implemented_paths"], 1)
        self.assertEqual(report["checking_read_calls"], 1)

    def test_render_names_the_branch_and_both_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            text = workflow_node_tokens.render(workflow_node_tokens.corpus_report(self.corpus(Path(work))))
        self.assertIn("implement-task", text)
        self.assertIn("assess-task", text)
        self.assertIn("accept", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
