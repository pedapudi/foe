#!/usr/bin/python3
"""Run identity-bound Foe self-improvement from trajectory diagnoses."""

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

sys.path.append(str(Path(__file__).resolve().parent.parent / "harness_bench"))
from foe_source_identity import clean_source_tree, require_evaluated_foe, sha256_file

from foe_agent_support import estimate_usage_cost
from run import Pricing, read_cases


DIAGNOSIS_CALLS = 4
IMPLEMENTATION_CALLS = 16
SECONDS = 2_400
ALLOWED_DIRECTORIES = ("crates", "docs", "examples")
ALLOWED_ROOT_FILES = ("BUILD.bazel", "Cargo.toml", "MODULE.bazel", "MODULE.bazel.lock")
CODING_TOOLS = ["read", "grep", "edit", "bash"]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def model_config(route: str, reasoning_effort: str) -> dict[str, str]:
    provider, slash, model = route.partition("/")
    if not slash or not provider or not model:
        raise ValueError("model routes must have the form provider/model")
    return {
        "provider": provider,
        "model": model,
        "reasoning_effort": reasoning_effort,
    }


def verify_evidence_identity(candidate: Path, binary: Path, evidence: Path) -> dict[str, str]:
    report = json.loads(evidence.read_text(encoding="utf-8"))
    identity = require_evaluated_foe(
        report.get("evaluated_foe"), f"self-improvement evidence {evidence}"
    )
    candidate_tree = clean_source_tree(candidate)
    runtime_binary = sha256_file(binary)
    if candidate_tree != identity["source_tree"]:
        raise ValueError("candidate source tree differs from the evaluated evidence")
    if runtime_binary != identity["runtime_binary"]:
        raise ValueError("Foe binary differs from the evaluated evidence")
    return identity


def source_hashes(root: Path) -> dict[str, str]:
    answer = {}
    for directory in ALLOWED_DIRECTORIES:
        for path in sorted((root / directory).rglob("*")):
            if path.is_file():
                answer[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ALLOWED_ROOT_FILES:
        path = root / name
        if path.is_file():
            answer[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return answer


def write_candidate_check(path: Path, candidate: Path) -> None:
    baseline = source_hashes(candidate)
    script = f'''#!/usr/bin/python3
import hashlib
import pathlib
import subprocess

root = pathlib.Path({str(candidate)!r})
baseline = {baseline!r}
allowed_directories = {ALLOWED_DIRECTORIES!r}
allowed_root_files = {ALLOWED_ROOT_FILES!r}
current = {{}}
for directory in allowed_directories:
    for item in sorted((root / directory).rglob("*")):
        if item.is_file():
            current[item.relative_to(root).as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
for name in allowed_root_files:
    item = root / name
    if item.is_file():
        current[name] = hashlib.sha256(item.read_bytes()).hexdigest()
changed = sorted(name for name in set(baseline) | set(current) if baseline.get(name) != current.get(name))
status = subprocess.run(
    ["/usr/bin/git", "status", "--porcelain=v1", "--untracked-files=all"],
    cwd=root,
    text=True,
    capture_output=True,
    check=True,
).stdout.splitlines()
all_changed = [line[3:] for line in status if len(line) > 3]
findings = []
outside = sorted(set(all_changed) - set(changed))
if outside:
    findings.append("changes outside the runtime, documentation, and example surface: " + ", ".join(outside))
if not any(name.startswith("crates/") and name.endswith(".rs") and not name.endswith("_test.rs") for name in changed):
    findings.append("the candidate contains no Rust implementation change")
if not any(name.endswith("_test.rs") for name in changed):
    findings.append("the candidate contains no Rust regression test")
if not any(name.startswith("docs/") and name.endswith(".md") for name in changed):
    findings.append("the candidate does not update an affected specification")
for name in changed:
    item = root / name
    if not item.is_file():
        continue
    text = item.read_text(encoding="utf-8", errors="replace")
    if "terminal-bench/" in text:
        findings.append(f"{{name}} contains a benchmark task identifier")
    if any(line.endswith((" ", "\\t")) for line in text.splitlines()):
        findings.append(f"{{name}} contains trailing whitespace")
print("\\n".join(findings))
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def build_config(
    candidate: Path,
    evidence: Path,
    check: Path,
    model: dict[str, str],
) -> dict[str, Any]:
    diagnosis_read_roots = [str(candidate), str(evidence.parent)]
    implementation_read_roots = [str(candidate)]
    write_roots = [str(candidate)]
    check_tool = {
        "exec": str(check),
        "description": "Verify a general Rust implementation change, a regression test, an affected specification, allowed paths, benchmark independence, and clean whitespace. The tool prints findings and prints nothing when these checks pass.",
        "cwd": str(candidate),
        "timeout_seconds": 30,
    }
    diagnosis = {
        "name": "diagnose-foe-from-trajectory-measurements",
        "instructions": {
            "role": "Diagnose one general Foe limitation from the supplied typed trajectory measurements.",
            "scope": "Use read, grep, and bash to inspect runtime source, tests, and specifications. Do not edit files or inspect benchmark tasks, graders, fixtures, or completed answers.",
            "evidence": "Tie every claim to task names and log sequence numbers in the digest. Prefer a mechanism that explains multiple observations. Separate model limitations from harness limitations.",
            "result": "Return one typed diagnosis with implementation, regression-test, specification, and acceptance details. The next coding episode receives this diagnosis without the raw trajectories.",
        },
        "tools": ["read", "grep", "bash"],
        "grants": {"read": diagnosis_read_roots},
        "budget": {"model_calls": DIAGNOSIS_CALLS, "seconds": 600},
        "done_when": {
            "returns": {
                "type": "object",
                "properties": {
                    "mechanism": {"type": "string", "minLength": 1},
                    "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                    "implementation_files": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "test_files": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "specification_files": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "acceptance": {"type": "string", "minLength": 1},
                },
                "required": [
                    "mechanism",
                    "evidence",
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
        "name": "implement-foe-improvement",
        "instructions": {
            "role": "Act as a fully capable Foe coding agent and implement the supplied typed diagnosis.",
            "scope": "Inspect source before editing. Change runtime source, a regression test, and each affected specification. Preserve reconstructable logs, declared authority, typed outcomes, and explicit completion semantics.",
            "independence": "Do not change evaluation code, tasks, graders, model routes, or task allowances. Do not encode benchmark identifiers, fixture values, or grader rules.",
            "validation": "Use bash for focused tests, formatting, and clippy. Run the candidate check after the implementation. State expected accuracy, cost, latency, and compatibility effects in the final result.",
        },
        "tools": [*CODING_TOOLS, "check"],
        "tool_defs": {"check": check_tool},
        "grants": {"read": implementation_read_roots, "write": write_roots},
        "budget": {"model_calls": IMPLEMENTATION_CALLS, "seconds": 1_800},
        "done_when": {"verify": "check", "retries": 2},
    }
    return {
        "version": 2,
        "name": "identity-bound-trajectory-self-improvement",
        "instructions": {"role": "Run the declared diagnosis and implementation workflow."},
        "tools": [*CODING_TOOLS, "evidence", "check"],
        "tool_defs": {
            "evidence": {
                "exec": "/usr/bin/cat",
                "description": "Return identity-bound trajectory diagnoses without modifying them.",
            },
            "check": check_tool,
        },
        "grants": {"read": diagnosis_read_roots, "write": write_roots},
        "budget": {
            "model_calls": DIAGNOSIS_CALLS + IMPLEMENTATION_CALLS,
            "seconds": SECONDS,
            "max_depth": 1,
            "max_episodes": 3,
            "max_concurrent": 1,
            "loop_threshold": 6,
        },
        "workflow": {
            "nodes": {
                "collect-trajectory-diagnostics": {
                    "tool": "evidence",
                    "args": {"args": [str(evidence)]},
                },
                "diagnose-runtime": {
                    "model": diagnosis,
                    "follows": ["task", "collect-trajectory-diagnostics"],
                },
                "implement-runtime-improvement": {
                    "model": implementation,
                    "follows": ["task", "diagnose-runtime"],
                    "terminal": True,
                },
            },
            "recovery": {"enabled": False},
        },
        "model": model,
        "sandbox": {"mode": "best-effort"},
        "task": "Use the identity-bound trajectory diagnoses to implement one general Foe improvement. Improve successful task completion or measured model cost while preserving correctness and benchmark independence.",
    }


def measure_episode(root: Path, pricing: dict[str, Pricing]) -> dict[str, Any]:
    calls = 0
    input_tokens = 0
    cache_tokens = 0
    output_tokens = 0
    estimated_cost = 0.0
    paths = sorted(root.rglob("episode.jsonl"))
    complete = bool(paths)
    outcome = None
    for path in paths:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        starts = [event for event in events if event.get("type") == "episode/start"]
        messages = [event for event in events if event.get("type") == "assistant/message"]
        calls += sum(event.get("type") == "model/request" for event in events)
        if path == root / "episode.jsonl":
            ends = [event for event in events if event.get("type") == "episode/end"]
            outcome = ends[-1].get("data", {}).get("outcome") if ends else None
        if not starts:
            complete = False
            continue
        model = starts[0].get("data", {}).get("program", {}).get("model", {})
        route = f"{model.get('provider')}/{model.get('model')}"
        price = pricing.get(route)
        usages = []
        for event in messages:
            usage = event.get("data", {}).get("usage")
            if not isinstance(usage, dict) or not all(
                isinstance(usage.get(key), int) for key in ("input", "output", "cache_read")
            ):
                complete = False
                continue
            usages.append({key: usage[key] for key in ("input", "output", "cache_read")})
        input_tokens += sum(item["input"] for item in usages)
        cache_tokens += sum(item["cache_read"] for item in usages)
        output_tokens += sum(item["output"] for item in usages)
        if price is None:
            complete = False
        else:
            estimated_cost += estimate_usage_cost(usages, **price.agent_kwargs())
    return {
        "model_calls": calls,
        "usage_reported_and_priced": complete,
        "input_tokens": input_tokens if complete else None,
        "cache_read_tokens": cache_tokens if complete else None,
        "output_tokens": output_tokens if complete else None,
        "estimated_cost_usd": estimated_cost if complete else None,
        "outcome": outcome,
    }


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    answer.add_argument("--foe", type=Path, required=True)
    answer.add_argument("--candidate", type=Path, required=True)
    answer.add_argument("--evidence", type=Path, required=True)
    answer.add_argument("--cases", type=Path, required=True)
    answer.add_argument("--model", default="openai-codex/gpt-5.6-terra")
    answer.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "xhigh"),
        default="high",
    )
    answer.add_argument("--keep", type=Path)
    answer.add_argument("--confirm-spend", action="store_true")
    return answer


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        _, _, _, pricing = read_cases(args.cases.resolve(strict=True))
        model = model_config(args.model, args.reasoning_effort)
        if args.model not in pricing:
            raise ValueError(f"cases.pricing has no entry for {args.model}")
        candidate = args.candidate.resolve(strict=True)
        evidence = args.evidence.resolve(strict=True)
        binary = args.foe.resolve(strict=True)
        identity = verify_evidence_identity(candidate, binary, evidence)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"self-improvement: {error}", file=sys.stderr)
        return 2

    preview = {
        "evaluation": "identity-bound-trajectory-self-improvement",
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "maximum": {"model_calls": DIAGNOSIS_CALLS + IMPLEMENTATION_CALLS, "seconds": SECONDS},
        "token_limits": "measurement_only",
    }
    print(json.dumps(preview, indent=2, sort_keys=True))
    if not args.confirm_spend:
        print("No model requests were made. Add --confirm-spend after reviewing the plan.")
        return 0

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.keep:
        workspace = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
        root = args.keep if args.keep.is_absolute() or not workspace else Path(workspace) / args.keep
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=False)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="foe-trajectory-self-improvement-")
        root = Path(temporary.name)
    check = root / "candidate-check"
    write_candidate_check(check, candidate)
    program = root / "program.json"
    write_json(
        program,
        build_config(candidate, evidence, check, model),
    )
    episode = root / "episode"
    started = time.monotonic()
    result = subprocess.run(
        [str(binary), "--config", str(program), "--headless", "--log-dir", str(episode)],
        text=True,
        capture_output=True,
        timeout=SECONDS + 30,
        check=False,
    )
    changed_status = subprocess.run(
        ["/usr/bin/git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=candidate,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    changed = [line[3:] for line in changed_status if len(line) > 3]
    record = {
        **preview,
        "evaluated_foe": identity,
        "duration_seconds": round(time.monotonic() - started, 3),
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "usage": measure_episode(episode, pricing),
        "candidate": str(candidate),
        "evidence": str(evidence),
        "episode": str(episode),
        "changed_files": changed,
        "direct_implementation_required": result.returncode != 0 or not changed,
    }
    write_json(root / "result.json", record)
    print(json.dumps(record, indent=2, sort_keys=True))
    if temporary:
        temporary.cleanup()
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
