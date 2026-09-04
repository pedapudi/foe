#!/usr/bin/python3
"""Build and assess deterministic Foe capability probes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_probe_contract(working_directory: str) -> dict[str, Any]:
    if not working_directory.startswith("/"):
        raise ValueError("working directory must be an absolute path")
    return {
        "version": 4,
        "name": "terminal-bench-capability-probes",
        "instructions": {"role": "Execute the deterministic capability probe calls."},
        "tools": ["read", "grep", "bash"],
        "grants": {"read": [working_directory, "/"], "write": ["/"]},
        "budget": {"model_calls": 6, "seconds": 300},
        "sandbox": {"mode": "off"},
        "task": "Measure the capabilities available to Foe in this task container.",
    }


def evaluate_probe_episode(log_dir: Path) -> dict[str, Any]:
    path = log_dir / "episode.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    results = {
        event.get("data", {}).get("call_id"): event
        for event in events
        if event.get("type") == "tool/result"
    }

    def result(call_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        event = results.get(call_id)
        if event is None:
            raise ValueError(f"capability probe result is missing: {call_id}")
        raw_item = event.get("data") if isinstance(event.get("data"), dict) else {}
        item = {"seq": event.get("seq"), **raw_item}
        value = item.get("value") if isinstance(item.get("value"), dict) else {}
        return item, value

    start, start_value = result("probe_start")
    check, check_value = result("probe_check")
    grep, _ = result("probe_large_grep")
    read, _ = result("probe_large_read")
    timeout, timeout_value = result("probe_timeout")
    pty, pty_value = result("probe_pty")
    start_text = start.get("rendered", "")
    check_text = check.get("rendered", "")
    pty_text = pty.get("rendered", "")
    ends = [event for event in events if event.get("type") == "episode/end"]
    outcome = ends[-1].get("data", {}).get("outcome") if ends else None
    starts = [event for event in events if event.get("type") == "episode/start"]
    if len(starts) != 1:
        raise ValueError("capability probe must contain one episode/start event")
    contract = starts[0].get("data", {}).get("contract", {})
    read_roots = contract.get("grants", {}).get("read", [])
    expected_cwd = read_roots[0] if read_roots else None

    def evidence(item: dict[str, Any]) -> dict[str, Any]:
        return {"seq": item.get("seq"), "subject": item.get("subject")}

    capabilities = {
        "standard_path": "STANDARD_PATH=available" in start_text,
        "task_working_directory": (
            isinstance(expected_cwd, str) and f"CWD={expected_cwd}\n" in start_text
        ),
        "write_workspace_and_large_file": start_value.get("exit_code") == 0,
        "background_process_survives_tool_call": "BACKGROUND=alive" in check_text,
        "loopback_probe_available": "LOOPBACK_PROBE=available" in start_text,
        "loopback_connection": None,
        "package_manager_present": "PACKAGE_MANAGER=apt-get" in check_text,
        "package_install_permission": (
            "UID=0" in start_text and "PACKAGE_MANAGER=apt-get" in check_text
        ),
        "large_file_grep": not grep.get("is_error", False),
        "windowed_large_file_read": not read.get("is_error", False),
        "tool_timeout_enforced": bool(timeout_value.get("timed_out")),
        "interactive_pty": "PTY=yes" in pty_text and pty_value.get("exit_code") == 0,
    }
    return {
        "schema_version": 1,
        "outcome": outcome,
        "capabilities": capabilities,
        "evidence": {
            "environment": evidence(start),
            "process_and_loopback": evidence(check),
            "large_file_grep": evidence(grep),
            "large_file_read": evidence(read),
            "timeout": evidence(timeout),
            "pty": evidence(pty),
        },
    }
