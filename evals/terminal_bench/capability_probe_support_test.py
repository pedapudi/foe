#!/usr/bin/python3

import json
import tempfile
import unittest
from pathlib import Path

from capability_probe_support import build_probe_program, evaluate_probe_episode


class CapabilityProbeTest(unittest.TestCase):
    def test_probe_program_has_no_provider_credential_or_token_limit(self):
        program = build_probe_program("/tmp/capability_transport.sh", "/workspace")
        self.assertEqual(program["model"]["provider"], "exec")
        self.assertNotIn("token_file", program["model"])
        self.assertEqual(program["budget"], {"model_calls": 6, "seconds": 300})

    def test_episode_assessment_reports_supported_and_absent_capabilities(self):
        def tool(seq, call_id, rendered, value=None):
            return {
                "seq": seq,
                "type": "tool/result",
                "data": {
                    "call_id": call_id,
                    "subject": call_id,
                    "rendered": rendered,
                    "value": value or {"exit_code": 0},
                    "is_error": False,
                },
            }

        events = [
            {
                "seq": 0,
                "type": "episode/start",
                "data": {"program": {"grants": {"read": ["/workspace", "/"]}}},
            },
            tool(1, "probe_start", "CWD=/workspace\nUID=0\nSTANDARD_PATH=available\n"),
            tool(2, "probe_check", "BACKGROUND=gone\nLOOPBACK=failed\nPACKAGE_MANAGER=apt-get\n"),
            tool(3, "probe_large_grep", "1:probe-marker\n"),
            tool(4, "probe_large_read", "1\tprobe-marker\n"),
            tool(5, "probe_timeout", "timed out\n", {"timed_out": True}),
            tool(6, "probe_pty", "PTY=no\n"),
            {"seq": 7, "type": "episode/end", "data": {"outcome": {"kind": "accepted"}}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "episode.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            report = evaluate_probe_episode(root)
        self.assertTrue(report["capabilities"]["standard_path"])
        self.assertTrue(report["capabilities"]["task_working_directory"])
        self.assertTrue(report["capabilities"]["tool_timeout_enforced"])
        self.assertFalse(report["capabilities"]["background_process_survives_tool_call"])
        self.assertFalse(report["capabilities"]["interactive_pty"])
        self.assertIsNone(report["capabilities"]["loopback_connection"])


if __name__ == "__main__":
    unittest.main()
