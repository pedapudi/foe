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


DIAGNOSIS_CALLS = 20
IMPLEMENTATION_CALLS = 28
DIAGNOSIS_SECONDS = 1_800
IMPLEMENTATION_SECONDS = 3_600
SECONDS = DIAGNOSIS_SECONDS + IMPLEMENTATION_SECONDS
ALLOWED_DIRECTORIES = ("crates", "docs", "examples")
ALLOWED_ROOT_FILES = ("BUILD.bazel", "Cargo.toml", "MODULE.bazel", "MODULE.bazel.lock")
CODING_TOOLS = ["read", "grep", "edit", "bash"]
SYSTEM_DEVELOPMENT_READ_DIRS = (Path("/usr/include"), Path("/usr/local/include"))
FAST_SERVICE_CREDIT_MULTIPLIER = 2.5


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def model_config(route: str, reasoning_effort: str, service_tier: str = "priority") -> dict[str, str]:
    provider, slash, model = route.partition("/")
    if not slash or not provider or not model:
        raise ValueError("model routes must have the form provider/model")
    return {
        "provider": provider,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "service_tier": service_tier,
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


def candidate_artifact_identity(
    candidate: Path, base_source_tree: str, changed: list[str]
) -> dict[str, Any]:
    files = {}
    for name in sorted(changed):
        path = candidate / name
        files[name] = sha256_file(path) if path.is_file() else "absent"
    value = {"base_source_tree": base_source_tree, "files": files}
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return {**value, "digest": "sha256:" + hashlib.sha256(encoded).hexdigest()}


def rust_toolchain_identity(cargo: Path) -> dict[str, str]:
    binaries = {}
    for name in ("cargo", "rustc", "rustfmt", "clippy-driver"):
        path = cargo.parent / name
        if not path.is_file():
            raise ValueError(f"--cargo toolchain lacks `{name}` at {path}")
        binaries[name] = sha256_file(path)
    return binaries


def validate_program(binary: Path, program: Path) -> None:
    """Construct the generated program without making a model request."""
    result = subprocess.run(
        [str(binary), "plan", "--config", str(program), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise ValueError(f"generated self-improvement program is invalid: {detail}")


def git_metadata_root(candidate: Path) -> Path:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(candidate), "rev-parse", "--git-common-dir"],
        text=True,
        capture_output=True,
        check=True,
    )
    path = Path(result.stdout.strip())
    return (candidate / path).resolve(strict=True) if not path.is_absolute() else path.resolve(strict=True)


def write_candidate_check(path: Path, candidate: Path, cargo: Path, cargo_home: Path, cargo_target: Path) -> None:
    baseline = source_hashes(candidate)
    toolchain = cargo.parent.parent
    rustup_home = toolchain.parent.parent if toolchain.parent.name == "toolchains" else None
    rustup_toolchain = toolchain.name if rustup_home else None
    script = f'''#!/usr/bin/python3
import hashlib
import pathlib
import subprocess
import sys

root = pathlib.Path({str(candidate)!r})
baseline = {baseline!r}
allowed_directories = {ALLOWED_DIRECTORIES!r}
allowed_root_files = {ALLOWED_ROOT_FILES!r}
cargo = {str(cargo)!r}
cargo_home = {str(cargo_home)!r}
cargo_target = {str(cargo_target)!r}
rustup_home = {str(rustup_home) if rustup_home else None!r}
rustup_toolchain = {rustup_toolchain!r}
full_validation = sys.argv[1:] == ["--full"]
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
if not findings:
    temporary = pathlib.Path(cargo_target) / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    env = {{
        "CARGO_HOME": cargo_home,
        "CARGO_TARGET_DIR": cargo_target,
        "HOME": str(root),
        "LANG": "C.UTF-8",
        "PATH": str(pathlib.Path(cargo).parent) + ":/usr/local/bin:/usr/bin:/bin",
        "TMPDIR": str(temporary),
    }}
    if rustup_home:
        env["RUSTUP_HOME"] = rustup_home
        env["RUSTUP_TOOLCHAIN"] = rustup_toolchain
    test_command = [cargo, "test", "--workspace"]
    if not full_validation:
        # Executable tools cannot bind TCP listeners. The CLI unit tests use
        # loopback servers, as do the transport and viewer tests. Nested
        # sandbox tests cannot expand an existing Landlock domain. The
        # post-episode check runs the omitted packages and skipped group.
        test_command.extend(
            [
                "--exclude", "foe",
                "--exclude", "foe-transport",
                "--exclude", "foe-view",
                "--", "--skip", "sandbox::tests::",
            ]
        )
    commands = [
        [cargo, "fmt", "--all", "--", "--check"],
        test_command,
        [cargo, "clippy", "--workspace", "--all-targets", "--", "-D", "warnings"],
        ["/bin/bash", "scripts/loc.sh"],
    ]
    for command in commands:
        try:
            result = subprocess.run(
                command,
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                timeout=900,
                check=False,
            )
        except subprocess.TimeoutExpired:
            findings.append("candidate validation timed out: " + " ".join(command))
            break
        if result.returncode != 0:
            output = []
            for label, content in (("stdout", result.stdout), ("stderr", result.stderr)):
                lines = content.splitlines()
                if lines:
                    output.append(label + ": " + " | ".join(lines[-20:]))
            detail = " || ".join(output)
            findings.append(
                "candidate validation failed: " + " ".join(command)
                + (f": {{detail}}" if detail else "")
            )
            break
print("\\n".join(findings))
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def build_config(
    candidate: Path,
    evidence: Path,
    check: Path,
    implementation_model: dict[str, str],
    diagnosis_model: dict[str, str],
    execute_roots: list[Path],
    source_metadata_roots: list[Path],
    development_read_roots: list[Path],
    objective: str,
) -> dict[str, Any]:
    diagnosis_read_roots = [str(evidence.parent)]
    development_reads = [str(path) for path in [*source_metadata_roots, *development_read_roots]]
    implementation_read_roots = [str(candidate), *development_reads]
    root_read_roots = [str(candidate), *diagnosis_read_roots, *development_reads]
    write_roots = [str(candidate / directory) for directory in ALLOWED_DIRECTORIES]
    execute = [str(path) for path in execute_roots]
    check_tool = {
        "exec": str(check),
        "description": "Verify candidate scope, benchmark independence, formatting, Rust workspace tests, clippy, and line budgets. The tool prints findings and prints nothing when every check passes.",
        "cwd": str(candidate),
        "timeout_seconds": 900,
    }
    diagnosis = {
        "name": "diagnose-foe-from-trajectory-measurements",
        "instructions": {
            "role": "Diagnose one general Foe limitation that explains the verified completion gap in the supplied trajectory measurements.",
            "scope": "Reason only from the bounded labeled trajectory digest supplied to this episode. Do not inspect repository source, benchmark tasks, graders, fixtures, or completed answers. The coding episode maps the causal intervention to source files.",
            "evidence": "Compare failed and successful model settings from the labeled digest. Use the final validation timeline and bounded verifier feedback before attributing a failure to missing validation. Tie claims to episode identifiers and log sequence numbers. Separate model limitations from harness limitations.",
            "controls": "Improve the lower-cost evaluated configuration. Preserve its model route, reasoning effort, task allowances, token policy, and task set. Treat a higher-cost successful setting as diagnostic evidence rather than the candidate configuration. The intervention must change general product behavior under crates, docs, or examples and must be active for the explicit program recorded in the evidence. Evaluation configuration is outside candidate scope.",
            "result": "Use four model requests as a planning target. Return one typed causal intervention as soon as the evidence supports it. Continue only while a named causal uncertainty prevents a supported intervention. The model-call allowance is a loop backstop. Name evidence that differs between failed and successful attempts, plus the observation that would falsify the intervention. The coding episode receives the diagnosis without the trajectory reports.",
        },
        "tools": ["block"],
        "grants": {"read": diagnosis_read_roots},
        "budget": {"model_calls": DIAGNOSIS_CALLS, "seconds": DIAGNOSIS_SECONDS},
        "model": diagnosis_model,
        "done_when": {
            "returns": {
                "type": "object",
                "properties": {
                    "harness_limitation": {"type": "string", "minLength": 1},
                    "model_limitation": {"type": "string", "minLength": 1},
                    "failed_attempt_evidence": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                    "successful_contrast_evidence": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                    "intervention": {"type": "string", "minLength": 1},
                    "predicted_trace_change": {"type": "string", "minLength": 1},
                    "causal_discriminant": {
                        "type": "object",
                        "properties": {
                            "failed_evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 2,
                            },
                            "successful_evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 2,
                            },
                            "difference": {"type": "string", "minLength": 1},
                        },
                        "required": [
                            "failed_evidence",
                            "successful_evidence",
                            "difference",
                        ],
                        "additionalProperties": False,
                    },
                    "falsification_condition": {"type": "string", "minLength": 1},
                    "required_paths": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                    },
                    "runtime_activation": {"type": "string", "minLength": 1},
                    "preserved_evaluation_controls": {"type": "string", "minLength": 1},
                    "acceptance": {"type": "string", "minLength": 1},
                },
                "required": [
                    "harness_limitation",
                    "model_limitation",
                    "failed_attempt_evidence",
                    "successful_contrast_evidence",
                    "intervention",
                    "predicted_trace_change",
                    "causal_discriminant",
                    "falsification_condition",
                    "required_paths",
                    "runtime_activation",
                    "preserved_evaluation_controls",
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
            "independence": "Do not change evaluation code, tasks, graders, model routes, reasoning settings, task allowances, token policy, or task selection. Do not encode benchmark identifiers, fixture values, or grader rules. Refuse an intervention that changes only a built-in default overridden by the explicit evaluated program.",
            "validation": "The candidate check runs formatting, the Rust workspace tests, clippy, and line budgets under the declared toolchain. Run it after implementation and use its findings to correct the candidate. State expected accuracy, cost, latency, and compatibility effects in the final result.",
        },
        "tools": [*CODING_TOOLS, "check"],
        "tool_defs": {"check": check_tool},
        "grants": {"read": implementation_read_roots, "write": write_roots, "execute": execute},
        "budget": {"model_calls": IMPLEMENTATION_CALLS, "seconds": IMPLEMENTATION_SECONDS},
        "done_when": {"verify": "check", "retries": 2},
    }
    return {
        "version": 2,
        "name": "identity-bound-trajectory-self-improvement",
        "instructions": {"role": "Run the declared diagnosis and implementation workflow."},
        "tools": [*CODING_TOOLS, "block", "evidence", "check"],
        "tool_defs": {
            "evidence": {
                "exec": "/usr/bin/cat",
                "description": "Return identity-bound trajectory diagnoses without modifying them.",
            },
            "check": check_tool,
        },
        "grants": {"read": root_read_roots, "write": write_roots, "execute": execute},
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
        "model": implementation_model,
        "sandbox": {"mode": "best-effort"},
        "task": objective,
    }


def check_candidate(check: Path, candidate: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [str(check), "--full"],
            cwd=candidate,
            input="{}\n",
            text=True,
            capture_output=True,
            timeout=1_800,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"accepted": False, "findings": ["candidate checker timed out"], "exit_code": None}
    findings = [line for line in result.stdout.splitlines() if line]
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        findings.append(f"candidate checker failed: {detail}")
    return {"accepted": not findings, "findings": findings, "exit_code": result.returncode}


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
    answer.add_argument("--service-tier", choices=("default", "priority"), default="priority")
    answer.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "xhigh"),
        default="high",
    )
    answer.add_argument("--diagnosis-model", default="openai-codex/gpt-5.6-luna")
    answer.add_argument(
        "--diagnosis-reasoning-effort",
        choices=("low", "medium", "high", "xhigh"),
        default="high",
    )
    answer.add_argument(
        "--objective",
        default=(
            "Use the identity-bound failed and successful trajectory contrast to implement one general Foe "
            "improvement that raises verified task completion in the lower-cost evaluated configuration. "
            "Preserve its model route, reasoning effort, task allowances, token policy, and task set. Treat "
            "higher-cost successful settings as diagnostic contrasts. Preserve correctness and benchmark independence."
        ),
    )
    answer.add_argument(
        "--cargo",
        type=Path,
        help="absolute path to the pinned toolchain's cargo binary, rather than a rustup proxy",
    )
    answer.add_argument("--cargo-home", type=Path, help="absolute Cargo cache used by candidate validation")
    answer.add_argument("--keep", type=Path)
    answer.add_argument("--confirm-spend", action="store_true")
    return answer


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        _, _, _, pricing = read_cases(args.cases.resolve(strict=True))
        model = model_config(args.model, args.reasoning_effort, args.service_tier)
        diagnosis_model = model_config(
            args.diagnosis_model, args.diagnosis_reasoning_effort, args.service_tier
        )
        if args.model not in pricing:
            raise ValueError(f"cases.pricing has no entry for {args.model}")
        if args.diagnosis_model not in pricing:
            raise ValueError(f"cases.pricing has no entry for {args.diagnosis_model}")
        if not args.objective.strip():
            raise ValueError("--objective must not be empty")
        if args.cargo is None or args.cargo_home is None:
            raise ValueError("--cargo and --cargo-home are required for program and candidate validation")
        cargo = args.cargo.resolve(strict=True) if args.cargo else None
        cargo_home = args.cargo_home.resolve(strict=True) if args.cargo_home else None
        if cargo is not None and cargo.name != "cargo":
            raise ValueError("--cargo must resolve to a pinned toolchain cargo binary rather than a rustup proxy")
        if cargo is not None and not cargo.is_file():
            raise ValueError("--cargo must name a file")
        if cargo_home is not None and not cargo_home.is_dir():
            raise ValueError("--cargo-home must name a directory")
        if cargo_home is not None and not (cargo_home / "bin").is_dir():
            raise ValueError("--cargo-home must contain the Cargo command-shim directory `bin`")
        validator_identity = rust_toolchain_identity(cargo) if cargo is not None else None
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
        "service_tier": args.service_tier,
        "chatgpt_credit_multiplier": (
            FAST_SERVICE_CREDIT_MULTIPLIER if args.service_tier == "priority" else 1.0
        ),
        "diagnosis_model": args.diagnosis_model,
        "diagnosis_reasoning_effort": args.diagnosis_reasoning_effort,
        "maximum": {"model_calls": DIAGNOSIS_CALLS + IMPLEMENTATION_CALLS, "seconds": SECONDS},
        "token_limits": "measurement_only",
    }
    if validator_identity is not None:
        preview["candidate_validator"] = {"rust_toolchain": validator_identity}
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
    assert cargo is not None and cargo_home is not None
    cargo_target = candidate / "target" / "foe-self-improvement-check"
    cargo_target.mkdir(parents=True, exist_ok=True)
    source_metadata = git_metadata_root(candidate)
    write_candidate_check(check, candidate, cargo, cargo_home, cargo_target)
    episode_evidence = root / "trajectory-evidence.json"
    episode_evidence.write_bytes(evidence.read_bytes())
    toolchain = cargo.parent.parent
    rustup_home = toolchain.parent.parent if toolchain.parent.name == "toolchains" else None
    execute_roots = [toolchain, cargo_home / "bin", cargo_target]
    development_read_roots = [
        cargo_home,
        *(path for path in [rustup_home] if path is not None),
        *(path.resolve() for path in SYSTEM_DEVELOPMENT_READ_DIRS if path.is_dir()),
    ]
    program = root / "program.json"
    write_json(
        program,
        build_config(
            candidate,
            episode_evidence,
            check,
            model,
            diagnosis_model,
            execute_roots,
            [source_metadata],
            development_read_roots,
            args.objective,
        ),
    )
    try:
        validate_program(binary, program)
    except ValueError as error:
        print(f"self-improvement: {error}", file=sys.stderr)
        if temporary:
            temporary.cleanup()
        return 2
    print(json.dumps(preview, indent=2, sort_keys=True))
    if not args.confirm_spend:
        print("No model requests were made. Add --confirm-spend after reviewing the plan.")
        if temporary:
            temporary.cleanup()
        return 0

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
    artifact_identity = candidate_artifact_identity(candidate, identity["source_tree"], changed)
    acceptance = check_candidate(check, candidate) if changed else {
        "accepted": False,
        "findings": ["candidate contains no changed files"],
        "exit_code": None,
    }
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
        "candidate_artifact": artifact_identity,
        "candidate_acceptance": acceptance,
        "artifact_outcome_mismatch": acceptance["accepted"] and result.returncode != 0,
        "direct_implementation_required": not acceptance["accepted"],
    }
    write_json(root / "result.json", record)
    print(json.dumps(record, indent=2, sort_keys=True))
    if temporary:
        temporary.cleanup()
    return 0 if acceptance["accepted"] else result.returncode or 3


if __name__ == "__main__":
    raise SystemExit(main())
