#!/usr/bin/python3
"""Run one bounded evidence-guided Foe self-improvement workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


LIMITS = {"model_calls": 12, "input_tokens": 300_000, "output_tokens": 20_000, "seconds": 1_200}
DIAGNOSIS_LIMITS = {"model_calls": 3, "input_tokens": 70_000, "output_tokens": 4_000, "seconds": 300}
IMPLEMENTATION_LIMITS = {"model_calls": 9, "input_tokens": 230_000, "output_tokens": 16_000, "seconds": 900}
ALLOWED_PREFIXES = ("crates/core/src/", "crates/code/src/", "crates/cli/src/", "docs/")
LINE_BUDGETS = {"runtime": 6_000, "workflow": 1_000, "context": 500, "view": 600, "cli": 1_300}
CODING_TOOLS = ["read", "grep", "edit", "bash"]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def route(value: str) -> dict[str, str]:
    provider, slash, model = value.partition("/")
    if not slash or not provider or not model:
        raise ValueError("--model takes PROVIDER/MODEL")
    return {"provider": provider, "model": model}


def source_hashes(root: Path) -> dict[str, str]:
    hashes = {}
    for prefix in ALLOWED_PREFIXES:
        for path in sorted((root / prefix).rglob("*")):
            if path.is_file():
                hashes[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def episode_measurement(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    logs = [read_events(path) for path in sorted(root.rglob("episode.jsonl"))]
    messages = [
        event.get("data", {})
        for values in logs
        for event in values
        if event.get("type") == "assistant/message"
    ]
    usages = [message.get("usage") for message in messages if isinstance(message, dict)]
    reported = (
        bool(messages)
        and len(usages) == len(messages)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("input"), int)
            and isinstance(item.get("output"), int)
            for item in usages
        )
    )
    input_tokens = sum(item.get("input", 0) for item in usages) if reported else None
    output_tokens = sum(item.get("output", 0) for item in usages) if reported else None
    measured = {
        "model_calls": sum(event.get("type") == "model/request" for values in logs for event in values),
        "usage_reported": reported,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": sum(item.get("cache_read", 0) for item in usages) if reported else None,
        "total_tokens": input_tokens + output_tokens if reported else None,
    }
    root_events = read_events(root / "episode.jsonl")
    outcomes = [
        event.get("data", {}).get("outcome")
        for event in root_events
        if event.get("type") == "episode/end"
    ]
    outcome = outcomes[-1] if outcomes and isinstance(outcomes[-1], dict) else {}
    return measured, outcome


def checker(path: Path, candidate: Path) -> None:
    baseline = source_hashes(candidate)
    script = f'''#!/usr/bin/python3
import hashlib
import pathlib

root = pathlib.Path({str(candidate)!r})
baseline = {baseline!r}
prefixes = {ALLOWED_PREFIXES!r}
current = {{}}
for prefix in prefixes:
    for path in sorted((root / prefix).rglob("*")):
        if path.is_file():
            current[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
changed = sorted(name for name in set(baseline) | set(current) if baseline.get(name) != current.get(name))
findings = []
if not changed:
    findings.append("the candidate contains no source change")
if not any(name.startswith("crates/") and name.endswith(".rs") and not name.endswith("_test.rs") for name in changed):
    findings.append("the candidate does not change implementation Rust source")
if not any(name.endswith("_test.rs") for name in changed):
    findings.append("the candidate does not change a Rust regression test")
if not any(name.startswith("docs/") and name.endswith(".md") for name in changed):
    findings.append("the candidate does not update an affected specification")
for name in changed:
    path = root / name
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(marker in text for marker in ("015-security-injection-defense", "043-db-migration-safety", "078-local-api-cursor-retry-ledger", "083-monorepo-interface-repair")):
            findings.append(f"{{name}} contains a development-task identifier")
        if any(line.endswith((" ", "\\t")) for line in text.splitlines()):
            findings.append(f"{{name}} contains trailing whitespace")
budgets = {LINE_BUDGETS!r}
counts = {{}}
for crate in ("log", "core", "code", "workflow", "context", "view", "cli"):
    lines = 0
    for source in (root / "crates" / crate / "src").rglob("*.rs"):
        relative = source.relative_to(root / "crates" / crate / "src")
        if "tests" in relative.parts or source.name.endswith("_test.rs") or source.name.startswith("generated"):
            continue
        lines += sum(bool(line.strip()) and not line.lstrip().startswith("//") for line in source.read_text(encoding="utf-8").splitlines())
    counts[crate] = lines
counts["runtime"] = counts["log"] + counts["core"] + counts["code"]
for crate, budget in budgets.items():
    if counts[crate] > budget:
        findings.append(f"{{crate}} contains {{counts[crate]}} counted lines; its limit is {{budget}}")
print("\\n".join(findings))
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def config(candidate: Path, evidence: Path, check: Path, model: dict[str, str]) -> dict[str, Any]:
    read = [str(candidate), str(evidence.parent)]
    write = [str(candidate / prefix) for prefix in ALLOWED_PREFIXES]
    check_def = {
        "exec": str(check),
        "description": "Checks for a general implementation change, a Rust regression test, an affected specification, clean whitespace, and repository line budgets. It prints findings and prints nothing when these deterministic checks pass.",
        "cwd": str(candidate),
        "timeout_seconds": 30,
    }
    diagnosis = {
        "name": "diagnose-runtime-from-assessed-evidence",
        "instructions": {
            "10-role": "Diagnose one general Foe runtime behavior using the supplied assessed evidence.",
            "20-scope": "Inspect relevant runtime source, tests, and specifications. Do not edit files. Do not inspect evaluation code, benchmark adapters, tasks, fixtures, graders, or completed benchmark answers.",
            "30-evidence": "Name the mechanism supported by more than one observation. Return the implementation, regression-test, and specification paths that a coding agent should change. Exclude benchmark identifiers, fixture values, and grader rules.",
            "40-budget": "This diagnosis has three model requests. Use targeted parallel searches and reads, then return the typed diagnosis by the third request.",
        },
        "tools": ["read", "grep", "bash"],
        "grants": {"read": read},
        "budget": {
            **DIAGNOSIS_LIMITS,
            "max_depth": 0,
            "max_episodes": 1,
            "max_concurrent": 1,
            "loop_threshold": 3,
        },
        "done_when": {
            "returns": {
                "type": "object",
                "properties": {
                    "mechanism": {"type": "string", "minLength": 1},
                    "implementation_files": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "test_files": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "specification_files": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "acceptance": {"type": "string", "minLength": 1},
                },
                "required": [
                    "mechanism",
                    "implementation_files",
                    "test_files",
                    "specification_files",
                    "acceptance",
                ],
                "additionalProperties": False,
            }
        },
    }
    implementation = {
        "name": "improve-foe-from-assessed-evidence",
        "instructions": {
            "10-role": "Implement the supplied typed diagnosis as one general Foe runtime improvement.",
            "20-scope": "Confirm the relevant source excerpts, then change runtime source, a regression test, and every affected specification. Do not repeat broad diagnosis. Do not change evaluation code, benchmark adapters, tasks, graders, budgets, or model routes. Do not encode benchmark identifiers, fixture values, or grader rules.",
            "30-quality": "Prefer a small behavioral change supported by more than one observation. Preserve trace reconstruction, declared authority, separate token budgets, and explicit completion semantics. A successful tool action alone never proves that an open-ended task is complete.",
            "40-validation": "This implementation has nine model requests. Read each changed file before editing it. Reserve the final two requests for tests, specifications, and check. Use bash for focused diagnostics and tests that the contained environment supports. The check validates candidate shape, whitespace, and line budgets. Full repository validation runs outside this episode. State the expected accuracy, token, latency, and compatibility effects in the final result.",
        },
        "tools": [*CODING_TOOLS, "check"],
        "tool_defs": {"check": check_def},
        "grants": {"read": read, "write": write},
        "budget": {
            **IMPLEMENTATION_LIMITS,
            "max_depth": 0,
            "max_episodes": 1,
            "max_concurrent": 1,
            "loop_threshold": 5,
        },
        "done_when": {"verify": "check", "retries": 2},
    }
    return {
        "version": 2,
        "name": "assessed-evidence-self-improvement",
        "instructions": {"role": "Run the declared evidence collection and self-improvement workflow."},
        "tools": [*CODING_TOOLS, "evidence", "check"],
        "tool_defs": {
            "evidence": {
                "exec": "/usr/bin/cat",
                "description": "Return the assessed micro and Harness-Bench evidence without modifying it.",
            },
            "check": check_def,
        },
        "grants": {"read": read, "write": write},
        "budget": {**LIMITS, "max_depth": 1, "max_episodes": 3, "max_concurrent": 1, "loop_threshold": 5},
        "workflow": {
            "nodes": {
                "collect-assessed-evidence": {"tool": "evidence", "args": {"args": [str(evidence)]}},
                "diagnose-runtime": {
                    "model": diagnosis,
                    "follows": ["task", "collect-assessed-evidence"],
                },
                "improve-runtime": {
                    "model": implementation,
                    "follows": ["task", "diagnose-runtime"],
                    "terminal": True,
                },
            },
            "recovery": {"max_interventions": 1},
        },
        "model": model,
        "sandbox": {"mode": "best-effort"},
        "task": "Use the assessed micro and Harness-Bench outcomes to improve budget-aware model behavior. A correct artifact followed by exhaustion is a quality failure. Bounded child episodes also need enough awareness to return available evidence before spending their final request on repeated exploration. Retain strict finite budgets and explicit completion signals.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--foe", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--model", default="openai-codex/gpt-5.6-sol")
    parser.add_argument("--keep", type=Path)
    parser.add_argument("--confirm-spend", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model = route(args.model)
    preview = {"evaluation": "foe-assessed-evidence-self-improvement", "model": model, "maximum": LIMITS}
    print(json.dumps(preview, indent=2, sort_keys=True))
    if not args.confirm_spend:
        print("No model calls were launched. Pass --confirm-spend after reviewing the maximum.", file=sys.stderr)
        return 2
    candidate = args.candidate.resolve()
    evidence = args.evidence.resolve()
    if not (candidate / "Cargo.toml").is_file() or not evidence.is_file():
        raise SystemExit("--candidate must be a Foe checkout and --evidence must be a file")
    status = subprocess.run(
        ["/usr/bin/git", "status", "--short"], cwd=candidate, text=True, capture_output=True, check=True
    ).stdout.strip()
    if status:
        raise SystemExit(f"candidate checkout is not clean before self-improvement:\n{status}")
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.keep:
        workspace_directory = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
        root = args.keep
        if not root.is_absolute() and workspace_directory:
            root = Path(workspace_directory) / root
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=False)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="foe-self-improvement-")
        root = Path(temporary.name)
    check = root / "candidate-check"
    checker(check, candidate)
    program_path = root / "program.json"
    write_json(program_path, config(candidate, evidence, check, model))
    log_dir = root / "episode"
    started = time.monotonic()
    result = subprocess.run(
        [str(args.foe.resolve()), "--config", str(program_path), "--log-dir", str(log_dir), "--headless"],
        text=True,
        capture_output=True,
        timeout=LIMITS["seconds"] + 30,
        check=False,
    )
    measured, outcome = episode_measurement(log_dir)
    record = {
        **preview,
        "duration_seconds": round(time.monotonic() - started, 3),
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "outcome": outcome,
        "usage": measured,
        "candidate": str(candidate),
        "evidence": str(evidence),
        "episode": str(log_dir),
        "changed_files": subprocess.run(
            ["/usr/bin/git", "diff", "--name-only"], cwd=candidate, text=True, capture_output=True, check=True
        ).stdout.splitlines(),
    }
    write_json(root / "result.json", record)
    print(json.dumps(record, indent=2, sort_keys=True))
    if temporary:
        temporary.cleanup()
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
