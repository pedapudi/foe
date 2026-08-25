#!/usr/bin/python3
"""Run identity-bound Foe self-improvement from trajectory diagnoses."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
from workflow_candidate import create as create_workflow_candidate
from workflow_candidate import validate_independent_audit


DIAGNOSIS_CALLS = 20
IMPLEMENTATION_CALLS = 28
DIAGNOSIS_SECONDS = 1_800
IMPLEMENTATION_SECONDS = 3_600
SECONDS = DIAGNOSIS_SECONDS + IMPLEMENTATION_SECONDS
LOOP_THRESHOLD = 8
ALLOWED_DIRECTORIES = ("crates", "docs", "examples")
ALLOWED_ROOT_FILES = ("BUILD.bazel", "Cargo.toml", "MODULE.bazel", "MODULE.bazel.lock")
CODING_TOOLS = ["read", "grep", "edit", "bash"]
SYSTEM_DEVELOPMENT_READ_DIRS = (Path("/usr/include"), Path("/usr/local/include"))
FAST_SERVICE_CREDIT_MULTIPLIER = 2.5
LINE_BUDGET_ROW = re.compile(r"^(\w+)\s+(\d+)\s+\(budget (\d+)\)$")


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


def failed_base_configuration(evidence: Path) -> dict[str, str]:
    """Return the one failed configuration a candidate must preserve."""
    report = json.loads(evidence.read_text(encoding="utf-8"))
    summaries = report.get("evaluation_summary")
    if not isinstance(summaries, list):
        raise ValueError("self-improvement evidence has no evaluation_summary list")
    candidates: dict[str, dict[str, str | None]] = {}
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        attempts = summary.get("attempts")
        successes = summary.get("verified_successes")
        configuration = summary.get("execution_configuration")
        if (
            type(attempts) is not int
            or type(successes) is not int
            or successes >= attempts
            or not isinstance(configuration, dict)
            or "independent_audit" in configuration
        ):
            continue
        implementation = configuration.get("implementation")
        if not isinstance(implementation, dict):
            continue
        candidate = {
            "model": implementation.get("model"),
            "reasoning_effort": implementation.get("reasoning_effort"),
            "service_tier": configuration.get("service_tier"),
            "token_policy": configuration.get("token_policy"),
        }
        candidates[json.dumps(candidate, sort_keys=True)] = candidate
    if len(candidates) != 1:
        raise ValueError(
            "self-improvement evidence must identify one failed configuration without an independent audit"
        )
    values = next(iter(candidates.values()))
    if not all(isinstance(value, str) and value for value in values.values()):
        raise ValueError("self-improvement evidence has an incomplete failed configuration")
    return values


def supported_independent_audits(
    evidence: Path, base_configuration: dict[str, str]
) -> list[dict[str, Any]]:
    """Return repeated successful audit settings that preserve the base run."""
    report = json.loads(evidence.read_text(encoding="utf-8"))
    summaries = report.get("evaluation_summary")
    if not isinstance(summaries, list):
        raise ValueError("self-improvement evidence has no evaluation_summary list")
    supported: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        attempts = summary.get("attempts")
        successes = summary.get("verified_successes")
        configuration = summary.get("execution_configuration")
        if (
            type(attempts) is not int
            or attempts < 2
            or successes != attempts
            or not isinstance(configuration, dict)
        ):
            continue
        implementation = configuration.get("implementation")
        audit = configuration.get("independent_audit")
        observed_base = {
            "model": implementation.get("model") if isinstance(implementation, dict) else None,
            "reasoning_effort": (
                implementation.get("reasoning_effort") if isinstance(implementation, dict) else None
            ),
            "service_tier": configuration.get("service_tier"),
            "token_policy": configuration.get("token_policy"),
        }
        if observed_base != base_configuration or not isinstance(audit, dict):
            continue
        if audit.get("model") != base_configuration["model"]:
            continue
        setting = validate_independent_audit(
            {
                "reasoning_effort": audit.get("reasoning_effort"),
                "model_calls": audit.get("model_calls"),
            }
        )
        supported[json.dumps(setting, sort_keys=True)] = setting
    return [supported[key] for key in sorted(supported)]


def workflow_candidate_from_outcome(
    outcome_value: Any,
    supported_audits: list[dict[str, Any]],
    identity: dict[str, str],
    evidence: Path,
    base_configuration: dict[str, str],
) -> dict[str, Any]:
    """Bind a workflow diagnosis to the sole supported audit setting."""
    if not isinstance(outcome_value, dict) or outcome_value.get("branch") != "configure-workflow":
        raise ValueError("self-improvement outcome did not select configure-workflow")
    if len(supported_audits) != 1:
        raise ValueError(
            "workflow candidate evidence must contain exactly one repeated successful "
            "independent-audit setting"
        )
    audit = supported_audits[0]
    return create_workflow_candidate(
        identity,
        "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
        base_configuration,
        audit,
    )


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


def line_budget_ceilings(root: Path) -> dict[str, int]:
    """Return each declared budget, preserving a larger baseline count."""
    result = subprocess.run(
        ["/bin/bash", "scripts/loc.sh"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise ValueError(f"baseline line-budget check failed: {detail}")
    ceilings = {}
    for line in result.stdout.splitlines():
        match = LINE_BUDGET_ROW.fullmatch(line.strip())
        if match:
            name, count, budget = match.groups()
            ceilings[name] = max(int(count), int(budget))
    if not ceilings:
        raise ValueError("baseline line-budget check reported no declared budgets")
    return ceilings


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
    line_ceilings = line_budget_ceilings(candidate)
    toolchain = cargo.parent.parent
    rustup_home = toolchain.parent.parent if toolchain.parent.name == "toolchains" else None
    rustup_toolchain = toolchain.name if rustup_home else None
    script = f'''#!/usr/bin/python3
import hashlib
import pathlib
import re
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
line_ceilings = {line_ceilings!r}
baseline_validation = sys.argv[1:] == ["--baseline"]
full_validation = baseline_validation or sys.argv[1:] == ["--full"]
if sys.argv[1:] not in ([], ["--baseline"], ["--full"]):
    print("candidate checker accepts only --baseline or --full")
    raise SystemExit(0)
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
if not baseline_validation:
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
    if not findings:
        result = subprocess.run(
            ["/bin/bash", "scripts/loc.sh"],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        counts = {{}}
        for line in result.stdout.splitlines():
            match = re.fullmatch(r"(\\w+)\\s+(\\d+)\\s+\\(budget (\\d+)\\)", line.strip())
            if match:
                name, count, _ = match.groups()
                counts[name] = int(count)
        missing = sorted(set(line_ceilings) - set(counts))
        if missing:
            findings.append("candidate line-budget check omitted: " + ", ".join(missing))
        for name, ceiling in sorted(line_ceilings.items()):
            if counts.get(name, 0) > ceiling:
                findings.append(
                    f"candidate line budget {{name}} is {{counts[name]}} lines; allowed {{ceiling}}"
                )
        if result.returncode not in (0, 1):
            detail = result.stderr.strip() or f"exit status {{result.returncode}}"
            findings.append("candidate line-budget check failed: " + detail)
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
    write_roots = [
        *(str(candidate / directory) for directory in ALLOWED_DIRECTORIES),
        str(candidate / "target" / "foe-self-improvement-check"),
    ]
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
            "evidence": "Compare the failed and successful settings from the labeled digest. Use the final validation timeline and bounded verifier feedback before attributing a failure to missing validation. Cite episode identifiers and log sequence numbers only inside the causal contrast. Separate observed facts from uncertain attribution.",
            "controls": "Preserve the primary model route, reasoning effort, task allowances, token policy, service tier, and task set. Candidate selection uses verified task quality. Record resource changes without rejecting a quality improvement. The intervention must apply through general Foe behavior or a general workflow setting. It must not branch on a benchmark, dataset, task, program name, checksum, fixture, grader, or episode identity.",
            "sufficiency": "Choose `implement-source` when the trajectories activate a specific Foe source mechanism. Choose `configure-workflow` when a repeated quality gain is caused by exactly one independent audit setting; the runner binds that setting directly from the evidence. Choose `insufficient-evidence` when the intervention requires semantic knowledge absent from the log, an evaluator change, or an instruction that no runtime signal can enforce. A reasoning-effort difference without a workflow contrast establishes model capability rather than a Foe defect.",
            "result": "Use four model requests as a planning target. Return one concise typed diagnosis as soon as the evidence supports either disposition. Continue only while a named causal uncertainty can be resolved from the supplied digest. The model-call allowance is a loop backstop. Each string should contain no more than two sentences. The coding episode receives the diagnosis without the trajectory reports.",
        },
        "tools": ["block"],
        "grants": {"read": diagnosis_read_roots},
        "budget": {
            "model_calls": DIAGNOSIS_CALLS,
            "seconds": DIAGNOSIS_SECONDS,
            "loop_threshold": LOOP_THRESHOLD,
        },
        "model": diagnosis_model,
        "done_when": {
            "returns": {
                "type": "object",
                "properties": {
                    "limitation": {"type": "string", "minLength": 1},
                    "attribution": {"type": "string", "minLength": 1},
                    "causal_contrast": {
                        "type": "object",
                        "properties": {
                            "failed": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                            "successful": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                            "difference": {"type": "string", "minLength": 1},
                        },
                        "required": ["failed", "successful", "difference"],
                        "additionalProperties": False,
                    },
                    "intervention": {"type": "string", "minLength": 1},
                    "activation_path": {"type": "string", "minLength": 1},
                    "preserved_controls": {"type": "string", "minLength": 1},
                    "falsification_condition": {"type": "string", "minLength": 1},
                },
                "required": [
                    "limitation",
                    "attribution",
                    "causal_contrast",
                    "intervention",
                    "activation_path",
                    "preserved_controls",
                    "falsification_condition",
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
            "validation": "The candidate check runs formatting, the Rust workspace tests, Clippy, and baseline-relative line budgets under the declared toolchain. Run it after implementation and use its findings to correct the candidate. Use the check tool as the authority for line counts because scripts/loc.sh alone cannot distinguish an existing overage from candidate growth. State expected accuracy, cost, latency, and compatibility effects in the final result.",
        },
        "tools": [*CODING_TOOLS, "check"],
        "tool_defs": {"check": check_tool},
        "grants": {"read": implementation_read_roots, "write": write_roots, "execute": execute},
        "budget": {
            "model_calls": IMPLEMENTATION_CALLS,
            "seconds": IMPLEMENTATION_SECONDS,
            "loop_threshold": LOOP_THRESHOLD,
        },
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
            "loop_threshold": LOOP_THRESHOLD,
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
                    "branches": {
                        "implement-source": ["implement-runtime-improvement"],
                        "configure-workflow": [],
                        "insufficient-evidence": [],
                    },
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


def run_candidate_check(check: Path, candidate: Path, mode: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [str(check), mode],
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


def check_baseline(check: Path, candidate: Path) -> dict[str, Any]:
    return run_candidate_check(check, candidate, "--baseline")


def check_candidate(check: Path, candidate: Path) -> dict[str, Any]:
    return run_candidate_check(check, candidate, "--full")


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
        default="low",
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
        base_configuration = failed_base_configuration(evidence)
        supported_audits = supported_independent_audits(evidence, base_configuration)
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

    baseline_acceptance = check_baseline(check, candidate)
    write_json(root / "baseline-validation.json", baseline_acceptance)
    if not baseline_acceptance["accepted"]:
        print(
            "self-improvement: clean candidate baseline failed deterministic validation: "
            + "; ".join(baseline_acceptance["findings"]),
            file=sys.stderr,
        )
        if temporary:
            temporary.cleanup()
        return 2

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
    usage = measure_episode(episode, pricing)
    outcome = usage.get("outcome")
    outcome_value = outcome.get("value") if isinstance(outcome, dict) else None
    branch = outcome_value.get("branch") if isinstance(outcome_value, dict) else None
    workflow_candidate = None
    workflow_candidate_path = None
    if branch == "configure-workflow":
        findings = []
        if changed:
            findings.append("workflow configuration candidate also changed source files")
        try:
            workflow_candidate = workflow_candidate_from_outcome(
                outcome_value,
                supported_audits,
                identity,
                evidence,
                base_configuration,
            )
        except ValueError as error:
            findings.append(str(error))
        if not findings:
            workflow_candidate_path = root / "workflow-candidate.json"
            write_json(workflow_candidate_path, workflow_candidate)
        acceptance = {
            "accepted": not findings,
            "findings": findings,
            "exit_code": 0 if not findings else None,
        }
        artifact_identity = workflow_candidate
        candidate_kind = "workflow-configuration"
    elif branch == "implement-source":
        artifact_identity = candidate_artifact_identity(candidate, identity["source_tree"], changed)
        acceptance = check_candidate(check, candidate) if changed else {
            "accepted": False,
            "findings": ["source candidate contains no changed files"],
            "exit_code": None,
        }
        candidate_kind = "source-change"
    else:
        artifact_identity = candidate_artifact_identity(candidate, identity["source_tree"], changed)
        finding = (
            "diagnosis reported insufficient evidence"
            if branch == "insufficient-evidence"
            else "self-improvement outcome contains no supported candidate branch"
        )
        acceptance = {"accepted": False, "findings": [finding], "exit_code": None}
        candidate_kind = "no-candidate"
    record = {
        **preview,
        "evaluated_foe": identity,
        "duration_seconds": round(time.monotonic() - started, 3),
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "usage": usage,
        "candidate": str(candidate),
        "evidence": str(evidence),
        "episode": str(episode),
        "changed_files": changed,
        "candidate_kind": candidate_kind,
        "candidate_artifact": artifact_identity,
        "workflow_candidate": str(workflow_candidate_path) if workflow_candidate_path else None,
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
