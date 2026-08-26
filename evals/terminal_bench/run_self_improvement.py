#!/usr/bin/python3
"""Run identity-bound Foe self-improvement from trajectory diagnoses."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parent.parent / "harness_bench"))
from foe_source_identity import clean_source_tree, require_evaluated_foe, sha256_file

from foe_agent_support import build_program, estimate_usage_cost
from instruction_candidate import create as create_instruction_candidate
from run import Pricing, read_cases
from tool_candidate import create as create_tool_candidate
from tool_candidate import validate_definition as validate_tool_definition
from workflow_candidate import create as create_workflow_candidate
from workflow_candidate import validate_independent_audit


DIAGNOSIS_CALLS = 20
IMPLEMENTATION_CALLS = 60
AUDIT_CALLS = 60
AUDIT_REASONING_EFFORT = "xhigh"
DIAGNOSIS_SECONDS = 1_800
IMPLEMENTATION_SECONDS = 3_600
AUDIT_SECONDS = 3_600
SECONDS = DIAGNOSIS_SECONDS + IMPLEMENTATION_SECONDS + AUDIT_SECONDS
LOOP_THRESHOLD = 8
ALLOWED_DIRECTORIES = ("crates", "docs", "examples")
ALLOWED_ROOT_FILES = ("BUILD.bazel", "Cargo.toml", "MODULE.bazel", "MODULE.bazel.lock")
CODING_TOOLS = ["read", "grep", "edit", "bash"]
SYSTEM_DEVELOPMENT_READ_DIRS = (Path("/usr/include"), Path("/usr/local/include"))
FAST_SERVICE_CREDIT_MULTIPLIER = 2.5
LINE_BUDGET_ROW = re.compile(r"^(\w+)\s+(\d+)\s+\(budget (\d+)\)$")
DIAGNOSIS_VALIDATOR_TOOL = "validate-candidate"
DIAGNOSIS_VALIDATOR_MODULES = (
    "instruction_candidate.py",
    "tool_candidate.py",
    "workflow_candidate.py",
)
LINEAGE_SCHEMA_VERSION = 1
# The campaign's per-task floor allowances; docs/lineage-identity.md
# "Harness adoptions" declares them as the fixed members of a workflow
# adoption's development program document.
DEVELOPMENT_TASK_MODEL_CALLS = 60
DEVELOPMENT_TASK_SECONDS = 1_800
DEVELOPMENT_TASK_INSTRUCTION = "The development run supplies the task instruction at launch."
DEVELOPMENT_TASK_CREDENTIAL = "/credentials/model-token"
DEVELOPMENT_TASK_DIRECTORY = "/app"


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


def evidence_digest(evidence: Path) -> str:
    return "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest()


def supported_failure_contrasts(evidence: Path) -> list[dict[str, Any]]:
    """Return repeated task-specific contrasts that may activate a candidate."""
    report = json.loads(evidence.read_text(encoding="utf-8"))
    contrasts = report.get("repeated_failure_contrasts")
    if not isinstance(contrasts, list):
        raise ValueError("self-improvement evidence has no repeated_failure_contrasts list")
    answer = []
    for index, contrast in enumerate(contrasts):
        if not isinstance(contrast, dict):
            raise ValueError(f"repeated failure contrast {index} is not an object")
        if set(contrast) != {
            "task",
            "failure_profile",
            "failed_episode_ids",
            "successful_episode_ids",
        }:
            raise ValueError(f"repeated failure contrast {index} has an invalid shape")
        task = contrast["task"]
        profile = contrast["failure_profile"]
        failed = contrast["failed_episode_ids"]
        successful = contrast["successful_episode_ids"]
        if not isinstance(task, str) or not task or not isinstance(profile, dict):
            raise ValueError(f"repeated failure contrast {index} has no task or failure profile")
        if set(profile) != {
            "outcome",
            "artifact_outcome_mismatch",
            "failed_verifier_checks",
        }:
            raise ValueError(f"repeated failure contrast {index} has an invalid failure profile")
        outcome = profile["outcome"]
        mismatch = profile["artifact_outcome_mismatch"]
        checks = profile["failed_verifier_checks"]
        if (
            not isinstance(outcome, dict)
            or not set(outcome).issubset({"kind", "code", "limit"})
            or not isinstance(outcome.get("kind"), str)
            or not outcome["kind"]
            or not all(isinstance(value, str) for value in outcome.values())
        ):
            raise ValueError(f"repeated failure contrast {index} has an invalid outcome")
        if not isinstance(mismatch, bool):
            raise ValueError(f"repeated failure contrast {index} has an invalid mismatch flag")
        if not isinstance(checks, list) or not all(
            isinstance(check, dict)
            and set(check) == {"name", "failure_class"}
            and all(isinstance(value, str) and value for value in check.values())
            for check in checks
        ):
            raise ValueError(f"repeated failure contrast {index} has invalid verifier checks")
        if (
            not isinstance(failed, list)
            or len(failed) < 2
            or not all(isinstance(value, str) and value for value in failed)
        ):
            raise ValueError(f"repeated failure contrast {index} has fewer than two failed episodes")
        if (
            not isinstance(successful, list)
            or not successful
            or not all(isinstance(value, str) and value for value in successful)
        ):
            raise ValueError(f"repeated failure contrast {index} has no successful episode")
        if len(set(failed)) != len(failed) or len(set(successful)) != len(successful):
            raise ValueError(f"repeated failure contrast {index} repeats an episode identity")
        if set(failed).intersection(successful):
            raise ValueError(f"repeated failure contrast {index} reuses an episode across outcomes")
        answer.append(contrast)
    if not answer:
        raise ValueError(
            "self-improvement evidence has no validated repeated failure contrast"
        )
    return answer


def expected_candidate_branch(candidate_kind: str) -> str | None:
    branches = {
        "auto": None,
        "source-change": "implement-source",
        "workflow-configuration": "configure-workflow",
        "instruction-revision": "revise-instructions",
        "tool-definition": "define-tool",
    }
    try:
        return branches[candidate_kind]
    except KeyError as error:
        raise ValueError(f"unsupported candidate kind {candidate_kind}") from error


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> bytes:
    """The lineage crate's canonical form: compact, sorted keys, raw UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def state_identity(program_identity: str, program_lineage: dict[str, Any] | None) -> str:
    """The state identity docs/lineage-identity.md derives for one claim."""
    document = {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "program_identity": program_identity,
        "program_lineage": program_lineage,
    }
    return digest_bytes(canonical_json(document))


def revised_program_document(document: Any, revision: dict[str, str]) -> Any:
    """The program document with an accepted instruction revision applied."""
    revised = json.loads(json.dumps(document))
    section, old_text, new_text = revision["section"], revision["old_text"], revision["new_text"]
    holders: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if not isinstance(value, dict):
            return
        instructions = value.get("instructions")
        if isinstance(instructions, dict) and isinstance(instructions.get(section), str):
            holders.append(instructions)
        children = value.get("programs")
        if isinstance(children, dict):
            for child in children.values():
                walk(child)
        workflow = value.get("workflow")
        nodes = workflow.get("nodes") if isinstance(workflow, dict) else None
        if isinstance(nodes, dict):
            for node in nodes.values():
                if isinstance(node, dict):
                    walk(node.get("model"))

    walk(revised)
    if len(holders) != 1 or holders[0][section].count(old_text) != 1:
        raise ValueError("instruction revision does not apply to exactly one section occurrence")
    holders[0][section] = holders[0][section].replace(old_text, new_text)
    return revised


def development_program_document(
    base_configuration: dict[str, str],
    audit: dict[str, Any] | None = None,
    tool: dict[str, str] | None = None,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The development program document an adoption produces.

    The run-supplied members — task instruction, credential path, working
    directory, and per-task allowances — are fixed to the declared values
    so the document is stable across launches. `audit` adds the
    independent-audit stage, `tool` adds a tool_defs entry carrying the
    executable's content hash, and `runtime` names the produced runtime of
    a source adoption.
    """
    document = build_program(
        DEVELOPMENT_TASK_INSTRUCTION,
        base_configuration["model"],
        DEVELOPMENT_TASK_CREDENTIAL,
        DEVELOPMENT_TASK_DIRECTORY,
        model_calls=DEVELOPMENT_TASK_MODEL_CALLS,
        input_tokens=None,
        output_tokens=None,
        seconds=DEVELOPMENT_TASK_SECONDS,
        reasoning_effort=base_configuration["reasoning_effort"],
        service_tier=base_configuration["service_tier"],
        escalation_reasoning_effort=audit["reasoning_effort"] if audit else None,
        escalation_model_calls=audit["model_calls"] if audit else 0,
    )
    if tool is not None:
        document["tools"] = [*document["tools"], tool["name"]]
        document["tool_defs"] = {
            **document.get("tool_defs", {}),
            tool["name"]: {
                "description": tool["description"],
                "exec": "/tools/" + tool["name"],
                "exec_sha256": tool["executable_sha256"].removeprefix("sha256:"),
            },
        }
    if runtime is not None:
        document["runtime"] = runtime
    return document


def adoption_state_document(
    candidate_kind: str,
    candidate: dict[str, Any],
    program_document: dict[str, Any],
    base_configuration: dict[str, str],
) -> dict[str, Any]:
    """The state document an adoption of `candidate` creates.

    docs/lineage-identity.md "Harness adoptions" states the one rule: the
    state document is the program document that will run under the
    adoption. An instruction revision yields the revised self-improvement
    program document; the other kinds yield the development program
    document the adoption produces — with the audit stage applied, with
    the defined tool declared, or with the changed source named as the
    produced runtime until the rebuilt binary's hash attaches.
    """
    if candidate_kind == "instruction-revision":
        return revised_program_document(program_document, candidate["revision"])
    if candidate_kind == "workflow-configuration":
        return development_program_document(
            candidate["base_configuration"], audit=candidate["independent_audit"]
        )
    if candidate_kind == "tool-definition":
        return development_program_document(candidate["base_configuration"], tool=candidate["tool"])
    if candidate_kind == "source-change":
        return development_program_document(
            base_configuration,
            runtime={"source_tree": candidate["base_source_tree"], "files": candidate["files"]},
        )
    raise ValueError(f"candidate kind {candidate_kind} has no adoption state document")


def find_accepted_verification(episode: Path, tool: str) -> tuple[str, int]:
    """Locate the last accepted `verification/result` of `tool` in the episode tree.

    Returns the log path in bundle coordinates (under `episode/`) and the
    event's `seq`, the pairing the candidate binding record cites.
    """
    found: tuple[str, int] | None = None
    for path in sorted(episode.rglob("episode.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            event = json.loads(line)
            data = event.get("data", {})
            if (
                event.get("type") == "verification/result"
                and data.get("tool") == tool
                and data.get("status") == "accepted"
            ):
                found = ("episode/" + path.relative_to(episode).as_posix(), event["seq"])
    if found is None:
        raise ValueError(f"the episode tree records no accepted verification/result for {tool}")
    return found


def record_adoption(
    root: Path,
    episode: Path,
    state_document: dict[str, Any],
    parent_document: dict[str, Any],
    retained: dict[str, bytes],
    verification_tool: str,
    bundle_builder: list[str],
    builder_cwd: Path | None = None,
    builder_env: dict[str, str] | None = None,
    artifacts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Record one accepted candidate as a lineage transition under `root`.

    Writes the evidence bundle — the episode tree, the state document as
    the child identity document, and the artifact manifest over the
    retained candidate files — completes it through the lineage crate's
    `build-bundle` binary, which writes the adoption record and canonical
    manifest, and writes the parent and child state documents where the
    checker's resolvers read them: `root/lineage/states/<hex>.json` by
    state identity and `root/lineage/evidence/<hex>` by content address.
    """
    build = root / "lineage" / "bundle-build"
    shutil.copytree(episode, build / "episode")
    identity_bytes = canonical_json(state_document)
    (build / "child-identity.json").write_bytes(identity_bytes)
    for name, content in sorted(retained.items()):
        (build / name).write_bytes(content)
    if artifacts is None:
        artifacts = [{"path": name, "sha256": digest_bytes(content)} for name, content in sorted(retained.items())]
    (build / "artifact-manifest.json").write_bytes(canonical_json(artifacts))
    verification_log, verification_seq = find_accepted_verification(episode, verification_tool)
    result = subprocess.run(
        [
            *bundle_builder,
            str(build),
            "episode/episode.jsonl",
            "child-identity.json",
            "artifact-manifest.json",
            verification_log,
            str(verification_seq),
        ],
        cwd=builder_cwd,
        env=builder_env,
        text=True,
        capture_output=True,
        timeout=1_800,
        check=False,
    )
    address = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if result.returncode != 0 or not re.fullmatch(r"sha256:[0-9a-f]{64}", address):
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise ValueError(f"bundle builder failed: {detail}")
    evidence_dir = root / "lineage" / "evidence" / address.removeprefix("sha256:")
    evidence_dir.parent.mkdir(parents=True, exist_ok=True)
    build.rename(evidence_dir)
    states = root / "lineage" / "states"
    states.mkdir(parents=True, exist_ok=True)
    parent_identity = digest_bytes(canonical_json(parent_document))
    parent_state_identity = state_identity(parent_identity, None)
    parent_state = states / (parent_state_identity.removeprefix("sha256:") + ".json")
    write_json(parent_state, {"identity_document": parent_document})
    claim = {
        "parent": {"program_identity": parent_identity, "state_identity": parent_state_identity},
        "evidence": address,
        "verification_log": verification_log,
        "verification_seq": verification_seq,
    }
    child_identity = digest_bytes(identity_bytes)
    child_state_identity = state_identity(child_identity, claim)
    child_state = states / (child_state_identity.removeprefix("sha256:") + ".json")
    write_json(child_state, {"identity_document": state_document, "program_lineage": claim})
    return {
        "evidence": address,
        "program_identity": child_identity,
        "state_identity": child_state_identity,
        "parent_program_identity": parent_identity,
        "parent_state_identity": parent_state_identity,
        "verification_log": verification_log,
        "verification_seq": verification_seq,
        "state": str(child_state),
        "parent_state": str(parent_state),
        "evidence_directory": str(evidence_dir),
    }


def instruction_candidate_from_outcome(
    outcome_value: Any,
    documents: dict[str, Any],
    identity: dict[str, str],
    evidence: Path,
    base_configuration: dict[str, str],
) -> dict[str, Any]:
    """Validate a diagnosis result and bind its instruction revision."""
    if not isinstance(outcome_value, dict) or outcome_value.get("branch") != "revise-instructions":
        raise ValueError("self-improvement outcome did not select revise-instructions")
    return create_instruction_candidate(
        identity,
        evidence_digest(evidence),
        base_configuration,
        outcome_value.get("instruction_revision"),
        documents,
    )


def tool_candidate_from_outcome(
    outcome_value: Any,
    identity: dict[str, str],
    evidence: Path,
    base_configuration: dict[str, str],
) -> tuple[dict[str, Any], str]:
    """Validate a diagnosis result and bind its tool definition.

    Returns the candidate and the executable content the runner retains
    as a file beside it.
    """
    if not isinstance(outcome_value, dict) or outcome_value.get("branch") != "define-tool":
        raise ValueError("self-improvement outcome did not select define-tool")
    definition = validate_tool_definition(outcome_value.get("tool_definition"))
    candidate = create_tool_candidate(
        identity,
        evidence_digest(evidence),
        base_configuration,
        {field: definition[field] for field in ("name", "description", "executable_sha256")},
    )
    return candidate, definition["executable"]


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
        evidence_digest(evidence),
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


def validation_directories(candidate: Path) -> tuple[Path, Path, Path]:
    target = candidate / "target"
    return target, target / "foe-self-improvement-check", target / "test-scratch"


def prepare_validation_directories(candidate: Path) -> Path:
    """Create the generated and scratch directories used by candidate checks."""
    _, cargo_target, test_scratch = validation_directories(candidate)
    cargo_target.mkdir(parents=True, exist_ok=True)
    test_scratch.mkdir(parents=True, exist_ok=True)
    return cargo_target


def remove_preview_validation_directories(candidate: Path, existing: set[Path]) -> None:
    """Remove empty validation directories that a preview created."""
    for path in reversed(validation_directories(candidate)):
        if path not in existing:
            path.rmdir()


def validate_program(binary: Path, program: Path) -> dict[str, Any]:
    """Construct the generated program without making a model request.

    Returns the resolved plan object. Its identity document is the parent
    state an adoption descends from; the returned document is required to
    rehash to the reported identity.
    """
    result = subprocess.run(
        [str(binary), "plan", "--config", str(program), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise ValueError(f"generated self-improvement program is invalid: {detail}")
    try:
        plan = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"the resolved plan is not one JSON object: {error}") from error
    document = plan.get("identity_document") if isinstance(plan, dict) else None
    if not isinstance(document, dict) or digest_bytes(canonical_json(document)) != plan.get("identity"):
        raise ValueError("the resolved plan's identity document does not rehash to its identity")
    return plan


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
    baseline_benchmark_identifiers = {
        name: (candidate / name).read_text(encoding="utf-8", errors="replace").count("terminal-bench/")
        for name in baseline
    }
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
baseline_benchmark_identifiers = {baseline_benchmark_identifiers!r}
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
    if text.count("terminal-bench/") > baseline_benchmark_identifiers.get(name, 0):
        findings.append(f"{{name}} adds a benchmark task identifier")
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
        # loopback servers, as do the transport, viewer, and session tests.
        # Nested sandbox tests cannot expand an existing Landlock domain. The
        # post-episode check runs the omitted packages and skipped groups.
        test_command.extend(
            [
                "--exclude", "foe",
                "--exclude", "foe-transport",
                "--exclude", "foe-view",
                "--", "--skip", "sandbox::tests::",
                "--skip", "session::tests::a_session_serves_a_granted_bind_port_across_calls",
            ]
        )
    commands = [
        [cargo, "fmt", "--all", "--", "--check"],
        test_command,
    ]
    if not full_validation:
        # The command-line unit tests cover the built-in workflow that source
        # improvement most often changes. Only login tests need loopback
        # listeners, so keep every other command-line invariant in the
        # completion gate.
        commands.append(
            [cargo, "test", "-p", "foe", "--bin", "foe", "--", "--skip", "login::tests::"]
        )
    commands.append([cargo, "clippy", "--workspace", "--all-targets", "--", "-D", "warnings"])
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


def write_diagnosis_validator(
    path: Path,
    program: Path,
    identity: dict[str, str],
    evidence_sha256: str,
    base_configuration: dict[str, str],
    supported_audits: list[dict[str, Any]],
    failure_contrasts: list[dict[str, Any]],
    requested_candidate_kind: str,
) -> None:
    """Write the diagnosis node's completion verifier.

    The runtime invokes it with the returned typed diagnosis as JSON on
    standard input. It applies the same identity-bound candidate validation
    the runner applies after the episode, importing the candidate modules
    copied beside it, so an accepted workflow, instruction, or tool
    candidate has an authoritative `verification/result` event in the
    diagnosis episode's log. A source diagnosis and a typed abstention are
    accepted here: a source candidate is judged by the implementation
    node's candidate check, and an abstention proposes nothing.
    """
    for name in DIAGNOSIS_VALIDATOR_MODULES:
        (path.parent / name).write_bytes((Path(__file__).resolve().parent / name).read_bytes())
    script = f'''#!/usr/bin/python3
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from instruction_candidate import create as create_instruction_candidate
from tool_candidate import create as create_tool_candidate
from tool_candidate import validate_definition
from workflow_candidate import create as create_workflow_candidate
from workflow_candidate import validate_independent_audit

identity = {identity!r}
evidence_sha256 = {evidence_sha256!r}
base_configuration = {base_configuration!r}
supported_audits = {supported_audits!r}
failure_contrasts = {failure_contrasts!r}
expected_branch = {expected_candidate_branch(requested_candidate_kind)!r}
program = {str(program)!r}

findings = []
try:
    candidate = json.load(sys.stdin)
    branch = candidate.get("branch") if isinstance(candidate, dict) else None
    if expected_branch is not None and branch not in (expected_branch, "insufficient-evidence"):
        raise ValueError(
            f"requested candidate kind requires branch {{expected_branch}}, received {{branch}}"
        )
    if branch not in ("insufficient-evidence", None):
        if candidate.get("failure_contrast") not in failure_contrasts:
            raise ValueError(
                "candidate does not select one supported repeated failure contrast"
            )
    if branch == "configure-workflow":
        audit = validate_independent_audit(candidate.get("independent_audit"))
        if audit not in supported_audits:
            raise ValueError(
                "workflow candidate independent_audit was not a repeated successful evidence setting"
            )
        create_workflow_candidate(identity, evidence_sha256, base_configuration, audit)
    elif branch == "revise-instructions":
        documents = {{"program.json": json.loads(pathlib.Path(program).read_text(encoding="utf-8"))}}
        create_instruction_candidate(
            identity,
            evidence_sha256,
            base_configuration,
            candidate.get("instruction_revision"),
            documents,
        )
    elif branch == "define-tool":
        definition = validate_definition(candidate.get("tool_definition"))
        create_tool_candidate(
            identity,
            evidence_sha256,
            base_configuration,
            {{field: definition[field] for field in ("name", "description", "executable_sha256")}},
        )
    elif branch not in ("implement-source", "insufficient-evidence"):
        findings.append("the diagnosis selected no supported candidate branch")
except (OSError, ValueError, json.JSONDecodeError) as error:
    findings.append(str(error))
print("\\n".join(findings))
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def build_config(
    candidate: Path,
    evidence: Path,
    check: Path,
    validator: Path,
    implementation_model: dict[str, str],
    diagnosis_model: dict[str, str],
    execute_roots: list[Path],
    source_metadata_roots: list[Path],
    development_read_roots: list[Path],
    objective: str,
    requested_candidate_kind: str,
) -> dict[str, Any]:
    diagnosis_read_roots = [str(evidence.parent)]
    development_reads = [str(path) for path in [*source_metadata_roots, *development_read_roots]]
    implementation_read_roots = [str(candidate), *development_reads]
    root_read_roots = [str(candidate), *diagnosis_read_roots, *development_reads]
    write_roots = [
        *(str(candidate / directory) for directory in ALLOWED_DIRECTORIES),
        str(candidate / "target" / "foe-self-improvement-check"),
        str(candidate / "target" / "test-scratch"),
    ]
    execute = [str(path) for path in execute_roots]
    check_tool = {
        "exec": str(check),
        "description": "Verify candidate scope, benchmark independence, formatting, Rust workspace tests, clippy, and line budgets. The tool prints findings and prints nothing when every check passes.",
        "cwd": str(candidate),
        "timeout_seconds": 900,
    }
    validator_tool = {
        "exec": str(validator),
        "description": "Validate the returned typed diagnosis: an identity-bound workflow, instruction, or tool candidate is checked against the retained evidence and the preserved run controls. The tool prints findings and prints nothing when the diagnosis is valid.",
        "cwd": str(evidence.parent),
        "timeout_seconds": 60,
    }
    if requested_candidate_kind == "source-change":
        sufficiency = (
            "Choose `implement-source` when the trajectories support the general intervention named "
            "by the objective and the objective identifies behavior owned by Foe source. Choose "
            "`insufficient-evidence` when the evidence does not support that intervention, the source "
            "ownership claim is absent, or the change requires semantic task knowledge. Do not choose "
            "`configure-workflow`; this run evaluates whether a proven intervention can become source-owned behavior."
        )
    elif requested_candidate_kind == "workflow-configuration":
        sufficiency = (
            "Choose `configure-workflow` when a repeated quality gain is caused by exactly one independent "
            "audit setting. Choose `insufficient-evidence` when the evidence does not isolate that setting. "
            "Do not choose `implement-source`; this run evaluates a configuration candidate."
        )
    elif requested_candidate_kind == "instruction-revision":
        sufficiency = (
            "Choose `revise-instructions` when the evidence supports one general procedural change to an "
            "instruction section of the retained program document `program.json`. Choose "
            "`insufficient-evidence` when the evidence does not isolate that instruction change. Do not "
            "choose another candidate branch; this run evaluates an instruction revision."
        )
    elif requested_candidate_kind == "tool-definition":
        sufficiency = (
            "Choose `define-tool` when one missing executable tool explains the verified quality gap and "
            "the evidence supports its complete general definition. Choose `insufficient-evidence` when "
            "the evidence does not isolate that tool. Do not choose another candidate branch; this run "
            "evaluates a tool definition."
        )
    else:
        sufficiency = (
            "Choose `implement-source` when the trajectories activate a specific Foe source mechanism. "
            "Choose `configure-workflow` when a repeated quality gain is caused by exactly one independent "
            "audit setting; the runner binds that setting directly from the evidence. Choose `revise-instructions` "
            "when a procedural difference belongs in one instruction section of the retained program document. "
            "Choose `define-tool` when one missing executable tool explains the gap. Choose "
            "`insufficient-evidence` when the intervention requires semantic knowledge absent from the log or "
            "an evaluator change. A reasoning-effort difference without a workflow contrast establishes model "
            "capability rather than a Foe defect."
        )
    diagnosis = {
        "name": "diagnose-foe-from-trajectory-measurements",
        "instructions": {
            "role": "Diagnose one general Foe limitation that explains the verified completion gap in the supplied trajectory measurements.",
            "scope": "Reason only from the bounded labeled trajectory digest supplied to this episode. Do not inspect repository source, benchmark tasks, graders, fixtures, or completed answers. The coding episode maps the causal intervention to source files.",
            "evidence": "Select one object from repeated_failure_contrasts and copy it unchanged into failure_contrast. Diagnose only that task-specific contrast. Do not combine failure profiles or tasks. Use the final validation timeline and bounded verifier feedback before attributing a failure to missing validation. Cite episode identifiers and log sequence numbers only inside the causal contrast. Separate observed facts from uncertain attribution.",
            "controls": "Preserve the primary model route, reasoning effort, task allowances, token policy, service tier, and task set. Candidate selection uses verified task quality. Record resource changes without rejecting a quality improvement. The intervention must apply through general Foe behavior or a general workflow setting. It must not branch on a benchmark, dataset, task, program name, checksum, fixture, grader, or episode identity.",
            "sufficiency": sufficiency,
            "result": "Use four model requests as a planning target. Return one concise typed diagnosis as soon as the evidence supports either disposition. Continue only while a named causal uncertainty can be resolved from the supplied digest. The model-call allowance is a loop backstop. Each string should contain no more than two sentences; a tool definition's executable content is code and is exempt. The coding episode receives the diagnosis without the trajectory reports.",
        },
        "tools": ["block", DIAGNOSIS_VALIDATOR_TOOL],
        "tool_defs": {DIAGNOSIS_VALIDATOR_TOOL: validator_tool},
        "grants": {"read": diagnosis_read_roots},
        "budget": {
            "model_calls": DIAGNOSIS_CALLS,
            "seconds": DIAGNOSIS_SECONDS,
            "loop_threshold": LOOP_THRESHOLD,
        },
        "model": diagnosis_model,
        "done_when": {
            "verify": DIAGNOSIS_VALIDATOR_TOOL,
            "retries": 2,
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
                    "failure_contrast": {
                        "type": "object",
                        "properties": {
                            "task": {"type": "string", "minLength": 1},
                            "failure_profile": {
                                "type": "object",
                                "properties": {
                                    "outcome": {
                                        "type": "object",
                                        "properties": {
                                            "kind": {"type": "string"},
                                            "code": {"type": "string"},
                                            "limit": {"type": "string"},
                                        },
                                        "additionalProperties": False,
                                    },
                                    "artifact_outcome_mismatch": {"type": "boolean"},
                                    "failed_verifier_checks": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": "string"},
                                                "failure_class": {"type": "string"},
                                            },
                                            "additionalProperties": False,
                                        },
                                    },
                                },
                                "required": [
                                    "outcome",
                                    "artifact_outcome_mismatch",
                                    "failed_verifier_checks",
                                ],
                                "additionalProperties": False,
                            },
                            "failed_episode_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 2,
                            },
                            "successful_episode_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                        },
                        "required": [
                            "task",
                            "failure_profile",
                            "failed_episode_ids",
                            "successful_episode_ids",
                        ],
                        "additionalProperties": False,
                    },
                    "independent_audit": {
                        "type": "object",
                        "properties": {
                            "reasoning_effort": {
                                "type": "string",
                                "enum": ["low", "medium", "high", "xhigh"],
                            },
                            "model_calls": {
                                "type": "integer",
                                "minimum": 6,
                                "maximum": 120,
                            },
                        },
                        "required": ["reasoning_effort", "model_calls"],
                        "additionalProperties": False,
                    },
                    "instruction_revision": {
                        "type": "object",
                        "properties": {
                            "document": {"type": "string", "minLength": 1},
                            "section": {"type": "string", "minLength": 1},
                            "old_text": {"type": "string", "minLength": 1},
                            "new_text": {"type": "string", "minLength": 1},
                        },
                        "required": ["document", "section", "old_text", "new_text"],
                        "additionalProperties": False,
                    },
                    "tool_definition": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "minLength": 1, "maxLength": 64},
                            "description": {"type": "string", "minLength": 1, "maxLength": 1000},
                            "executable": {"type": "string", "minLength": 1, "maxLength": 16384},
                            "executable_sha256": {"type": "string", "minLength": 1},
                        },
                        "required": ["name", "description", "executable", "executable_sha256"],
                        "additionalProperties": False,
                    },
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
    implementation_handoff = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "minLength": 1},
            "changed_paths": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 24,
            },
            "validation": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 16,
            },
            "unresolved_risks": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 8,
            },
        },
        "required": ["summary", "changed_paths", "validation", "unresolved_risks"],
        "additionalProperties": False,
    }
    implementation = {
        "name": "implement-foe-improvement",
        "instructions": {
            "role": "Implement the supplied typed diagnosis as a candidate for independent source audit.",
            "scope": "Inspect source before editing. Change runtime source, a regression test, and each affected specification. Preserve reconstructable logs, declared authority, typed outcomes, and explicit completion semantics.",
            "independence": "Do not change evaluation code, tasks, graders, model routes, reasoning settings, task allowances, token policy, or task selection. Do not encode benchmark identifiers, fixture values, or grader rules. Refuse an intervention that changes only a built-in default overridden by the explicit evaluated program.",
            "validation": "Treat the diagnosis as a hypothesis that source and tests must support. Run the candidate check after implementation and use its findings to correct the candidate. Return a typed handoff for a fresh audit, including unresolved architectural risks. Use the check tool as the authority for baseline-relative line budgets because scripts/loc.sh alone cannot distinguish an existing overage from candidate growth.",
        },
        "tools": [*CODING_TOOLS, "check"],
        "tool_defs": {"check": check_tool},
        "grants": {"read": implementation_read_roots, "write": write_roots, "execute": execute},
        "budget": {
            "model_calls": IMPLEMENTATION_CALLS,
            "seconds": IMPLEMENTATION_SECONDS,
            "loop_threshold": LOOP_THRESHOLD,
        },
        "done_when": {"returns": implementation_handoff},
    }
    audit_model = {**implementation_model, "reasoning_effort": AUDIT_REASONING_EFFORT}
    audit = {
        "name": "audit-and-repair-foe-improvement",
        "instructions": {
            "role": "Independently audit the source candidate, repair every defect, and let the candidate checker decide completion.",
            "evidence": "Treat the diagnosis and implementation handoff as unverified hypotheses. Inspect the current diff, the owning source, existing tests, and affected specifications. Reject a proposed mechanism whose source lifecycle cannot produce the claimed task-visible behavior.",
            "architecture": "Trace every proposed tool, authority, process, and mutable resource from creation through model-node return, workflow settlement, and external evaluation. Preserve existing default interfaces unless the source design and general task-quality evidence require a change.",
            "independence": "Do not change evaluation code, tasks, graders, model routes, reasoning settings, task allowances, token policy, or task selection. Remove benchmark-specific behavior and refuse changes whose benefit depends on hidden evaluator knowledge.",
            "validation": "Run the candidate check after the final repair. Use its findings to continue until formatting, relevant tests, Clippy, scope, and baseline-relative line budgets pass. Report remaining semantic risks before the authoritative check.",
        },
        "tools": [*CODING_TOOLS, "check"],
        "tool_defs": {"check": check_tool},
        "grants": {"read": implementation_read_roots, "write": write_roots, "execute": execute},
        "budget": {
            "model_calls": AUDIT_CALLS,
            "seconds": AUDIT_SECONDS,
            "loop_threshold": LOOP_THRESHOLD,
        },
        "model": audit_model,
        "done_when": {"verify": "check", "retries": 4},
    }
    return {
        "version": 3,
        "name": "identity-bound-trajectory-self-improvement",
        "instructions": {"role": "Run the declared diagnosis, implementation, and independent source-audit workflow."},
        "tools": [
            *CODING_TOOLS,
            "block",
            "evidence",
            "check",
            DIAGNOSIS_VALIDATOR_TOOL,
        ],
        "tool_defs": {
            "evidence": {
                "exec": "/usr/bin/cat",
                "description": "Return identity-bound trajectory diagnoses without modifying them.",
            },
            "check": check_tool,
            DIAGNOSIS_VALIDATOR_TOOL: validator_tool,
        },
        "grants": {"read": root_read_roots, "write": write_roots, "execute": execute},
        "budget": {
            "model_calls": DIAGNOSIS_CALLS + IMPLEMENTATION_CALLS + AUDIT_CALLS,
            "seconds": SECONDS,
            "max_depth": 1,
            "max_episodes": 4,
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
                        "revise-instructions": [],
                        "define-tool": [],
                        "insufficient-evidence": [],
                    },
                },
                "implement-runtime-improvement": {
                    "model": implementation,
                    "follows": ["task", "diagnose-runtime"],
                },
                "audit-runtime-improvement": {
                    "model": audit,
                    "follows": ["task", "diagnose-runtime", "implement-runtime-improvement"],
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


def workflow_node_value(root: Path, node: str) -> dict[str, Any] | None:
    """Return the last recorded object value produced by one workflow node."""
    path = root / "episode.jsonl"
    if not path.is_file():
        return None
    value = None
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        data = event.get("data", {})
        if (
            event.get("type") == "workflow/node-end"
            and data.get("node") == node
            and isinstance(data.get("value"), dict)
        ):
            value = data["value"]
    return value


def candidate_outcome_value(root: Path, outcome: Any) -> dict[str, Any] | None:
    """Recover the diagnosis when a later terminal child owns the root outcome."""
    value = outcome.get("value") if isinstance(outcome, dict) else None
    if isinstance(value, dict) and isinstance(value.get("branch"), str):
        return value
    return workflow_node_value(root, "diagnose-runtime")


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
        "--candidate-kind",
        choices=(
            "auto",
            "source-change",
            "workflow-configuration",
            "instruction-revision",
            "tool-definition",
        ),
        default="auto",
        help="kind of improvement the evidence must support",
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


def prepare_output_root(
    keep: Path | None,
    workspace: str | None,
    confirmed: bool,
) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    """Create a retained root only for a confirmed run."""
    if confirmed and keep:
        root = keep if keep.is_absolute() or not workspace else Path(workspace) / keep
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=False)
        return root, None
    temporary = tempfile.TemporaryDirectory(prefix="foe-trajectory-self-improvement-")
    return Path(temporary.name), temporary


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
        failure_contrasts = supported_failure_contrasts(evidence)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"self-improvement: {error}", file=sys.stderr)
        return 2

    preview = {
        "evaluation": "identity-bound-trajectory-self-improvement",
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "source_audit_reasoning_effort": AUDIT_REASONING_EFFORT,
        "service_tier": args.service_tier,
        "chatgpt_credit_multiplier": (
            FAST_SERVICE_CREDIT_MULTIPLIER if args.service_tier == "priority" else 1.0
        ),
        "diagnosis_model": args.diagnosis_model,
        "diagnosis_reasoning_effort": args.diagnosis_reasoning_effort,
        "maximum": {
            "model_calls": DIAGNOSIS_CALLS + IMPLEMENTATION_CALLS + AUDIT_CALLS,
            "seconds": SECONDS,
        },
        "quality_authority": "unchanged task-owned Terminal-Bench grader",
        "token_limits": "measurement_only",
        "requested_candidate_kind": args.candidate_kind,
    }
    if validator_identity is not None:
        preview["candidate_validator"] = {"rust_toolchain": validator_identity}
    root, temporary = prepare_output_root(
        args.keep,
        os.environ.get("BUILD_WORKSPACE_DIRECTORY"),
        args.confirm_spend,
    )
    check = root / "candidate-check"
    assert cargo is not None and cargo_home is not None
    existing_validation_directories = {
        path for path in validation_directories(candidate) if path.is_dir()
    }
    cargo_target = prepare_validation_directories(candidate)
    source_metadata = git_metadata_root(candidate)
    write_candidate_check(check, candidate, cargo, cargo_home, cargo_target)
    episode_evidence = root / "trajectory-evidence.json"
    episode_evidence.write_bytes(evidence.read_bytes())
    validator = root / "diagnosis-validator"
    write_diagnosis_validator(
        validator,
        root / "program.json",
        identity,
        evidence_digest(evidence),
        base_configuration,
        supported_audits,
        failure_contrasts,
        args.candidate_kind,
    )
    toolchain = cargo.parent.parent
    rustup_home = toolchain.parent.parent if toolchain.parent.name == "toolchains" else None
    execute_roots = [toolchain, cargo_home / "bin", cargo_target]
    development_read_roots = [
        cargo_home,
        *(path for path in [rustup_home] if path is not None),
        *(path.resolve() for path in SYSTEM_DEVELOPMENT_READ_DIRS if path.is_dir()),
    ]
    program_document = build_config(
        candidate,
        episode_evidence,
        check,
        validator,
        model,
        diagnosis_model,
        execute_roots,
        [source_metadata],
        development_read_roots,
        args.objective,
        args.candidate_kind,
    )
    program = root / "program.json"
    write_json(program, program_document)
    try:
        plan = validate_program(binary, program)
    except ValueError as error:
        print(f"self-improvement: {error}", file=sys.stderr)
        if not args.confirm_spend:
            remove_preview_validation_directories(candidate, existing_validation_directories)
        if temporary:
            temporary.cleanup()
        return 2
    print(json.dumps(preview, indent=2, sort_keys=True))
    if not args.confirm_spend:
        print("No model requests were made. Add --confirm-spend after reviewing the plan.")
        remove_preview_validation_directories(candidate, existing_validation_directories)
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
    outcome_value = candidate_outcome_value(episode, outcome)
    branch = outcome_value.get("branch") if isinstance(outcome_value, dict) else None
    expected_branch = expected_candidate_branch(args.candidate_kind)
    workflow_candidate = None
    workflow_candidate_path = None
    instruction_candidate_path = None
    tool_candidate_path = None
    tool_executable_path = None
    if expected_branch is not None and branch != expected_branch:
        artifact_identity = candidate_artifact_identity(candidate, identity["source_tree"], changed)
        acceptance = {
            "accepted": False,
            "findings": [
                f"requested candidate kind {args.candidate_kind} produced branch {branch or 'absent'}"
            ],
            "exit_code": None,
        }
        candidate_kind = "no-candidate"
    elif branch == "configure-workflow":
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
    elif branch == "revise-instructions":
        findings = []
        instruction_candidate = None
        if changed:
            findings.append("instruction revision candidate also changed source files")
        try:
            instruction_candidate = instruction_candidate_from_outcome(
                outcome_value,
                {"program.json": program_document},
                identity,
                evidence,
                base_configuration,
            )
        except ValueError as error:
            findings.append(str(error))
        if not findings:
            instruction_candidate_path = root / "instruction-candidate.json"
            write_json(instruction_candidate_path, instruction_candidate)
        acceptance = {
            "accepted": not findings,
            "findings": findings,
            "exit_code": 0 if not findings else None,
        }
        artifact_identity = instruction_candidate
        candidate_kind = "instruction-revision"
    elif branch == "define-tool":
        findings = []
        tool_candidate = None
        if changed:
            findings.append("tool definition candidate also changed source files")
        try:
            tool_candidate, tool_executable = tool_candidate_from_outcome(
                outcome_value,
                identity,
                evidence,
                base_configuration,
            )
        except ValueError as error:
            findings.append(str(error))
        if not findings:
            tool_candidate_path = root / "tool-candidate.json"
            write_json(tool_candidate_path, tool_candidate)
            tool_executable_path = root / "tool-candidate-executable"
            tool_executable_path.write_text(tool_executable, encoding="utf-8")
            tool_executable_path.chmod(0o755)
        acceptance = {
            "accepted": not findings,
            "findings": findings,
            "exit_code": 0 if not findings else None,
        }
        artifact_identity = tool_candidate
        candidate_kind = "tool-definition"
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
    adoption = None
    if acceptance["accepted"] and candidate_kind != "no-candidate":
        retained: dict[str, bytes] = {}
        artifacts = None
        verification_tool = DIAGNOSIS_VALIDATOR_TOOL
        if candidate_kind == "workflow-configuration":
            retained["workflow-candidate.json"] = workflow_candidate_path.read_bytes()
        elif candidate_kind == "instruction-revision":
            retained["instruction-candidate.json"] = instruction_candidate_path.read_bytes()
        elif candidate_kind == "tool-definition":
            retained["tool-candidate.json"] = tool_candidate_path.read_bytes()
            retained["tool-candidate-executable"] = tool_executable_path.read_bytes()
        else:
            verification_tool = "check"
            artifacts = [
                {"path": name, "sha256": value}
                for name, value in sorted(artifact_identity["files"].items())
            ]
        toolchain_environment = {
            "CARGO_HOME": str(cargo_home),
            "CARGO_TARGET_DIR": str(cargo_target),
            "HOME": str(candidate),
            "LANG": "C.UTF-8",
            "PATH": f"{cargo.parent}:/usr/local/bin:/usr/bin:/bin",
            "TMPDIR": str(cargo_target / "tmp"),
        }
        if rustup_home is not None:
            toolchain_environment["RUSTUP_HOME"] = str(rustup_home)
            toolchain_environment["RUSTUP_TOOLCHAIN"] = toolchain.name
        (cargo_target / "tmp").mkdir(parents=True, exist_ok=True)
        try:
            adoption = record_adoption(
                root,
                episode,
                adoption_state_document(
                    candidate_kind, artifact_identity, program_document, base_configuration
                ),
                plan["identity_document"],
                retained,
                verification_tool,
                [str(cargo), "run", "--quiet", "-p", "foe-lineage", "--bin", "build-bundle", "--"],
                builder_cwd=candidate,
                builder_env=toolchain_environment,
                artifacts=artifacts,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            adoption = {"error": str(error)}
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
        "instruction_candidate": str(instruction_candidate_path) if instruction_candidate_path else None,
        "tool_candidate": str(tool_candidate_path) if tool_candidate_path else None,
        "tool_candidate_executable": str(tool_executable_path) if tool_executable_path else None,
        "candidate_acceptance": acceptance,
        "adoption": adoption,
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
