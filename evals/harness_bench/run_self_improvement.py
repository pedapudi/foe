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


LIMITS = {"model_calls": 22, "input_tokens": 400_000, "output_tokens": 50_000, "seconds": 1_800}
ALLOWED_PREFIXES = ("crates/core/src/", "crates/code/src/", "crates/cli/src/", "docs/")


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
print("\\n".join(findings))
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def config(candidate: Path, evidence: Path, check: Path, model: dict[str, str]) -> dict[str, Any]:
    read = [str(candidate), str(evidence.parent)]
    write = [str(candidate / prefix) for prefix in ALLOWED_PREFIXES]
    check_def = {
        "exec": str(check),
        "description": "Checks that the candidate makes one general source change with a Rust regression test and an affected specification. It prints findings and prints nothing when the candidate has the required shape.",
        "cwd": str(candidate),
        "timeout_seconds": 30,
    }
    child = {
        "name": "improve-foe-from-assessed-evidence",
        "instructions": {
            "10-role": "Improve one general Foe runtime behavior using the supplied assessed evidence.",
            "20-scope": "Diagnose the mechanism before editing. Change runtime source, a regression test, and every affected specification. Do not change evaluation code, benchmark adapters, tasks, graders, budgets, or model routes. Do not encode benchmark identifiers, fixture values, or grader rules.",
            "30-quality": "Prefer a small behavioral change supported by more than one observation. Preserve trace reconstruction, declared authority, separate token budgets, and existing completion semantics unless the evidence justifies a specified semantic change.",
            "40-validation": "Read every file before editing it. Use check after the change. State the expected accuracy, token, latency, and compatibility effects in the final result. Full repository validation runs outside this episode.",
        },
        "tools": ["read", "grep", "edit", "bash", "check"],
        "tool_defs": {"check": check_def},
        "grants": {"read": read, "write": write},
        "budget": {
            **LIMITS,
            "max_depth": 0,
            "max_episodes": 1,
            "max_concurrent": 1,
            "loop_threshold": 5,
        },
        "context": {"compact": True},
        "done_when": {"verify": "check", "retries": 2},
    }
    return {
        "version": 2,
        "name": "assessed-evidence-self-improvement",
        "instructions": {"role": "Run the declared evidence collection and self-improvement workflow."},
        "tools": ["evidence", "check"],
        "tool_defs": {
            "evidence": {
                "exec": "/usr/bin/cat",
                "description": "Return the assessed micro and Harness-Bench evidence without modifying it.",
            },
            "check": check_def,
        },
        "grants": {"read": read, "write": write},
        "budget": {**LIMITS, "max_depth": 1, "max_episodes": 2},
        "workflow": {
            "nodes": {
                "collect-assessed-evidence": {"tool": "evidence", "args": {"args": [str(evidence)]}},
                "improve-runtime": {
                    "model": child,
                    "follows": ["task", "collect-assessed-evidence"],
                    "terminal": True,
                },
            },
            "recovery": {"max_interventions": 1},
        },
        "model": model,
        "sandbox": {"mode": "best-effort"},
        "task": "Use the assessed micro and Harness-Bench outcomes to implement one general improvement in Foe. A correct artifact followed by exhaustion is a quality failure. Retain strict finite budgets while removing avoidable work or termination failure.",
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
    record = {
        **preview,
        "duration_seconds": round(time.monotonic() - started, 3),
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
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
