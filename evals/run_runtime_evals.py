#!/usr/bin/python3
"""Run deterministic foe episodes and score their guarantee evidence."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from trace_quality import DIMENSIONS, evaluate

Mutation = Callable[[list[dict[str, Any]]], None]


def base_config(name: str, root: Path, transport: Path) -> dict[str, Any]:
    return {
        "version": 1,
        "name": name,
        "instructions": {"role": "Follow the deterministic evaluation task."},
        "tools": ["read"],
        "grants": {"read": [str(root)]},
        "budget": {"model_calls": 8, "tokens": 10000, "seconds": 60},
        "model": {
            "provider": "exec",
            "model": name,
            "exec": str(transport),
        },
        "sandbox": {"mode": "off"},
        "task": "Complete the deterministic evaluation task.",
    }


def typed_config(root: Path, transport: Path) -> dict[str, Any]:
    config = base_config("eval-typed-outcome", root, transport)
    config["tools"] = ["block"]
    config["done_when"] = {
        "returns": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["accepted"]},
                "items": {"type": "integer"},
            },
            "required": ["status", "items"],
        }
    }
    return config


def blocked_config(root: Path, transport: Path) -> dict[str, Any]:
    config = base_config("eval-blocked-outcome", root, transport)
    config["tools"] = ["block"]
    config["task"] = "Report the deterministic missing-capability condition."
    return config


def exhausted_config(root: Path, transport: Path) -> dict[str, Any]:
    config = base_config("eval-exhausted-outcome", root, transport)
    config["budget"]["model_calls"] = 1
    config["model"]["file"] = str(root / "allowed" / "a.txt")
    config["task"] = "Read one fixture and leave work pending at the request limit."
    return config


def failed_config(root: Path, transport: Path) -> dict[str, Any]:
    config = base_config("eval-failed-outcome", root, transport)
    config["tools"] = ["block"]
    config["task"] = "Receive the deterministic non-retryable transport error."
    return config


def authority_config(root: Path, transport: Path) -> dict[str, Any]:
    allowed = root / "allowed"
    denied = root / "denied" / "secret.txt"
    config = base_config("eval-authority", allowed, transport)
    config["model"].update(
        {"allowed_file": str(allowed / "a.txt"), "denied_file": str(denied)}
    )
    config["task"] = "Use the built-in read tool on one granted path and one path outside the grant."
    return config


def workflow_config(root: Path, transport: Path) -> dict[str, Any]:
    config = base_config("eval-workflow", root, transport)
    config["tools"] = ["record"]
    config["tool_defs"] = {
        "record": {
            "exec": "/usr/bin/printf",
            "description": "Prints the supplied evaluation marker.",
        }
    }
    config["budget"].update({"max_episodes": 2, "max_concurrent": 1})
    config["workflow"] = {
        "nodes": {
            "propose": {
                "model": {
                    "name": "propose",
                    "instructions": {"role": "Return the declared workflow branch and plan."},
                    "tools": ["read"],
                    "grants": {"read": [str(root)]},
                    "budget": {"model_calls": 2, "tokens": 1000},
                    "done_when": {
                        "returns": {
                            "type": "object",
                            "properties": {"plan": {"type": "string"}},
                            "required": ["plan"],
                        }
                    },
                },
                "follows": ["task"],
                "branches": {"apply": ["apply"]},
            },
            "apply": {
                "tool": "record",
                "args": {"args": ["workflow complete"]},
                "follows": ["propose"],
                "terminal": True,
            },
        }
    }
    config["task"] = "Choose and execute the declared workflow branch."
    return config


def compaction_config(root: Path, transport: Path) -> dict[str, Any]:
    config = base_config("eval-compaction", root, transport)
    config["context"] = {
        "compact": True,
        "window_tokens": 2000,
        "reserve_tokens": 500,
        "keep_recent_tokens": 1,
        "margin_tokens": 0,
    }
    config["model"].update(
        {
            "file_a": str(root / "allowed" / "a.txt"),
            "file_b": str(root / "allowed" / "b.txt"),
        }
    )
    config["task"] = "Read both fixture files and report completion."
    return config


def sandbox_config(root: Path, transport: Path) -> dict[str, Any]:
    allowed = root / "allowed"
    denied = root / "denied" / "secret.txt"
    config = base_config("eval-sandbox", allowed, transport)
    config["tools"] = ["cat"]
    config["tool_defs"] = {
        "cat": {
            "exec": "/usr/bin/cat",
            "description": "Reads the file named in args without a shell.",
        }
    }
    config["model"].update(
        {"allowed_file": str(allowed / "a.txt"), "denied_file": str(denied)}
    )
    config["sandbox"] = {"mode": "required"}
    config["task"] = "Try the granted file and the file outside the read grant."
    return config


def write_config(path: Path, config: dict[str, Any]) -> None:
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def support_file(name: str) -> Path:
    relative = Path("evals") / name
    candidates = [Path(__file__).resolve().with_name(name)]
    runfiles_dir = os.environ.get("RUNFILES_DIR")
    if runfiles_dir:
        candidates.append(Path(runfiles_dir) / "_main" / relative)
    candidates.append(Path(str(Path(sys.argv[0]).resolve()) + ".runfiles") / "_main" / relative)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(f"evaluation support file is absent: {name}")


def run_case(
    binary: Path,
    run_root: Path,
    name: str,
    config: dict[str, Any],
    expected_exit: int = 0,
) -> Path:
    case_dir = run_root / name
    log_dir = case_dir / "episode"
    case_dir.mkdir()
    config_path = case_dir / "config.json"
    write_config(config_path, config)
    result = subprocess.run(
        [
            str(binary),
            "--config",
            str(config_path),
            "--log-dir",
            str(log_dir),
            "--headless",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != expected_exit:
        raise RuntimeError(
            f"{name} exited {result.returncode}; expected {expected_exit}"
            f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return log_dir


def check_sandbox_probe(log_dir: Path) -> None:
    events = [json.loads(line) for line in (log_dir / "episode.jsonl").read_text().splitlines()]
    start = events[0]["data"]
    if start["sandbox"]["landlock_abi"] < 1:
        raise RuntimeError("the required sandbox probe recorded no Landlock enforcement")
    results = {
        event["data"]["call_id"]: event["data"]
        for event in events
        if event["type"] == "tool/result" and event["data"]["name"] == "cat"
    }
    allowed = results.get("allowed-read", {}).get("value", {})
    denied = results.get("denied-read", {}).get("value", {})
    if allowed.get("exit_code") != 0 or "allowed evidence" not in allowed.get("stdout", ""):
        raise RuntimeError("the granted read did not succeed")
    denied_text = str(denied.get("stdout", "")) + str(denied.get("stderr", ""))
    if denied.get("exit_code") == 0 or "denied secret" in denied_text:
        raise RuntimeError("the read outside the grant was not denied")


def check_authority_probe(log_dir: Path) -> None:
    events = [json.loads(line) for line in (log_dir / "episode.jsonl").read_text().splitlines()]
    results = {
        event["data"]["call_id"]: event["data"]
        for event in events
        if event["type"] == "tool/result" and event["data"]["name"] == "read"
    }
    allowed = results.get("allowed-built-in-read", {})
    denied = results.get("denied-built-in-read", {})
    if allowed.get("is_error") is not False or "allowed evidence a" not in allowed.get("rendered", ""):
        raise RuntimeError("the built-in read rejected a granted file")
    if denied.get("is_error") is not True or "denied secret" in denied.get("rendered", ""):
        raise RuntimeError("the built-in read exposed a file outside its grant")


def check_compaction_probe(log_dir: Path, fixture_root: Path) -> None:
    events = [json.loads(line) for line in (log_dir / "episode.jsonl").read_text().splitlines()]
    summaries = [event for event in events if event["type"] == "compaction/summary"]
    if len(summaries) != 1:
        raise RuntimeError("the compaction case did not record one successful compaction")
    successful_reads = {
        call["args"]["path"]
        for event in events
        if event["type"] == "assistant/message"
        for call in event["data"]["tool_calls"]
        if call["name"] == "read"
        and any(
            result["type"] == "tool/result"
            and result["data"]["call_id"] == call["id"]
            and result["data"]["is_error"] is False
            for result in events
        )
    }
    expected = {
        str(fixture_root / "allowed" / "a.txt"),
        str(fixture_root / "allowed" / "b.txt"),
    }
    if successful_reads != expected:
        raise RuntimeError("the compaction case did not read both fixture files")


def check_outcome_cases(logs: dict[str, Path]) -> None:
    expected = {
        "typed-outcome": ("completed", None),
        "blocked-outcome": ("blocked", "missing-capability"),
        "exhausted-outcome": ("exhausted", "model_calls"),
        "failed-outcome": ("failed", None),
    }
    for name, (kind, detail) in expected.items():
        events = [
            json.loads(line)
            for line in (logs[name] / "episode.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        outcome = next(event for event in events if event["type"] == "episode/end")["data"]["outcome"]
        observed_detail = outcome.get("code", outcome.get("limit"))
        if outcome.get("kind") != kind or detail is not None and observed_detail != detail:
            raise RuntimeError(f"{name} produced an unexpected outcome: {outcome}")


def write_events(path: Path, events: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events),
        encoding="utf-8",
    )


def first_event(events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    return next(event for event in events if event["type"] == event_type)


def corrupt_authority(events: list[dict[str, Any]]) -> None:
    first_event(events, "episode/start")["data"]["program"]["grants"]["read"][0] = "relative"


def corrupt_request_messages(events: list[dict[str, Any]]) -> None:
    first_event(events, "model/request")["data"]["messages"].append(
        {"role": "user", "content": []}
    )


def corrupt_typed_value(events: list[dict[str, Any]]) -> None:
    first_event(events, "episode/end")["data"]["outcome"]["value"]["items"] = "two"


def corrupt_child_spend(events: list[dict[str, Any]]) -> None:
    first_event(events, "budget/release")["data"]["spent"]["model_calls"] = 2


def corrupt_workflow_branch(events: list[dict[str, Any]]) -> None:
    first_event(events, "workflow/branch")["data"]["successors"] = ["undeclared"]


def corrupt_compaction_task(events: list[dict[str, Any]]) -> None:
    first_event(events, "compaction/summary")["data"]["state"]["task"] = "altered task"


def mutation_checks(run_root: Path, logs: dict[str, Path]) -> None:
    cases: list[tuple[str, str, Mutation]] = [
        ("declared_authority", "typed-outcome", corrupt_authority),
        ("reconstructable_evidence", "typed-outcome", corrupt_request_messages),
        ("typed_outcomes", "typed-outcome", corrupt_typed_value),
        ("hierarchical_budgets", "workflow-provenance", corrupt_child_spend),
        ("workflow_provenance", "workflow-provenance", corrupt_workflow_branch),
        ("compaction_continuity", "context-compaction", corrupt_compaction_task),
    ]
    mutation_root = run_root / "mutations"
    mutation_root.mkdir()
    for dimension, case_name, mutate in cases:
        destination = mutation_root / dimension
        shutil.copytree(logs[case_name], destination)
        log_path = destination / "episode.jsonl"
        events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        mutated = copy.deepcopy(events)
        mutate(mutated)
        write_events(log_path, mutated)
        report = evaluate([destination])
        detected = any(item["dimension"] == dimension for item in report["violations"])
        if not detected:
            raise RuntimeError(f"the {dimension} evaluator did not detect its trace mutation")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--foe", type=Path, required=True, help="Path to the built foe binary.")
    parser.add_argument(
        "--include-kernel-sandbox",
        action="store_true",
        help="Require Landlock and run the stronger configured-executable denial probe.",
    )
    parser.add_argument("--keep", type=Path, help="Keep run artifacts in this directory.")
    args = parser.parse_args()
    binary = args.foe.resolve()
    transport = support_file("scripted_transport.py")
    if not binary.is_file():
        raise SystemExit(f"foe binary does not exist: {binary}")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.keep is None:
        temporary = tempfile.TemporaryDirectory(prefix="foe-runtime-evals-")
        run_root = Path(temporary.name)
    else:
        run_root = args.keep.resolve()
        run_root.mkdir(parents=True, exist_ok=True)

    fixtures = run_root / "fixtures"
    (fixtures / "allowed").mkdir(parents=True)
    (fixtures / "denied").mkdir()
    (fixtures / "allowed" / "a.txt").write_text("allowed evidence a\n", encoding="utf-8")
    (fixtures / "allowed" / "b.txt").write_text("allowed evidence b\n", encoding="utf-8")
    (fixtures / "denied" / "secret.txt").write_text("denied secret\n", encoding="utf-8")

    cases = {
        "typed-outcome": (typed_config(fixtures, transport), 0),
        "blocked-outcome": (blocked_config(fixtures, transport), 2),
        "exhausted-outcome": (exhausted_config(fixtures, transport), 3),
        "failed-outcome": (failed_config(fixtures, transport), 1),
        "declared-authority": (authority_config(fixtures, transport), 0),
        "workflow-provenance": (workflow_config(fixtures, transport), 0),
        "context-compaction": (compaction_config(fixtures, transport), 0),
    }
    if args.include_kernel_sandbox:
        cases["kernel-sandbox"] = (sandbox_config(fixtures, transport), 0)

    logs = {
        name: run_case(binary, run_root, name, config, expected_exit)
        for name, (config, expected_exit) in cases.items()
    }
    check_outcome_cases(logs)
    check_authority_probe(logs["declared-authority"])
    check_compaction_probe(logs["context-compaction"], fixtures)
    if args.include_kernel_sandbox:
        check_sandbox_probe(logs["kernel-sandbox"])
    report = evaluate(logs.values())
    report["observations"]["kernel_denial_probe"] = (
        "passed" if args.include_kernel_sandbox else "not_requested"
    )
    report["observations"]["capability_denial_probe"] = "passed"
    report["observations"]["compaction_continuation_probe"] = "passed"
    mutation_checks(run_root, logs)
    report["observations"]["trace_mutation_checks"] = len(DIMENSIONS)
    print(json.dumps(report, indent=2, sort_keys=True))

    required = set(
        [
            "declared_authority",
            "reconstructable_evidence",
            "typed_outcomes",
            "hierarchical_budgets",
            "workflow_provenance",
            "compaction_continuity",
        ]
    )
    missing = [name for name in required if report["metrics"][name]["covered_episodes"] == 0]
    if temporary is not None:
        temporary.cleanup()
    if missing:
        raise SystemExit("evaluation cases did not exercise: " + ", ".join(sorted(missing)))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
