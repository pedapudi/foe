#!/usr/bin/python3
"""Run evidence-bound Foe self-improvement from trajectory diagnoses."""

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

sys.path.append(str(Path(__file__).resolve().parent.parent))
from foe_build import clean_source_tree, require_evaluated_foe, sha256_file

from collect_diagnostics import collect_from_corpus, encoded_evidence
from foe_agent_support import build_contract, estimate_usage_cost
from instruction_candidate import create as create_instruction_candidate
from run import Pricing, read_cases
from tool_candidate import create as create_tool_candidate
from tool_candidate import validate_definition as validate_tool_definition
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
DIAGNOSIS_VALIDATOR_TOOL = "validate-candidate"
DIAGNOSIS_VALIDATOR_MODULES = (
    "instruction_candidate.py",
    "tool_candidate.py",
    "workflow_candidate.py",
)
# The campaign's per-task floor allowances: the fixed members of every
# development contract document a workflow adoption produces here.
DEVELOPMENT_TASK_MODEL_CALLS = 60
DEVELOPMENT_TASK_SECONDS = 1_800
DEVELOPMENT_TASK_INSTRUCTION = "The development run supplies the task instruction at launch."
DEVELOPMENT_TASK_CREDENTIAL = "/credentials/model-token"
DEVELOPMENT_TASK_DIRECTORY = "/app"


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_bound_python_launcher(
    path: Path, entrypoint: Path, dependencies: list[Path]
) -> None:
    """Write an executable whose bytes record every evidence-module digest."""
    files = [
        entrypoint.resolve(strict=True),
        *(item.resolve(strict=True) for item in dependencies),
    ]
    expected = {
        str(item): hashlib.sha256(item.read_bytes()).hexdigest() for item in files
    }
    script = f'''#!/usr/bin/python3
import hashlib
import runpy
import sys

expected = {expected!r}
for name, digest in expected.items():
    with open(name, "rb") as source:
        observed = hashlib.sha256(source.read()).hexdigest()
    if observed != digest:
        print(f"trajectory collector dependency changed: {{name}}", file=sys.stderr)
        raise SystemExit(2)
sys.path.insert(0, {str(files[0].parent)!r})
runpy.run_path({str(files[0])!r}, run_name="__main__")
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


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


def evidence_document(evidence: Path | dict[str, Any]) -> dict[str, Any]:
    """Read an evidence path or return an already verified document."""
    value = (
        json.loads(evidence.read_text(encoding="utf-8"))
        if isinstance(evidence, Path)
        else evidence
    )
    if not isinstance(value, dict):
        raise ValueError("self-improvement evidence is not an object")
    return value


def verify_evaluated_build(
    candidate: Path, binary: Path, evidence: Path | dict[str, Any]
) -> dict[str, str]:
    report = evidence_document(evidence)
    evaluated = require_evaluated_foe(
        report.get("evaluated_foe"), "self-improvement evidence"
    )
    candidate_tree = clean_source_tree(candidate)
    runtime_binary = sha256_file(binary)
    if candidate_tree != evaluated["source_tree"]:
        raise ValueError("candidate source tree differs from the evaluated evidence")
    if runtime_binary != evaluated["runtime_binary"]:
        raise ValueError("Foe binary differs from the evaluated evidence")
    return evaluated


def failed_base_configuration(evidence: Path | dict[str, Any]) -> dict[str, str]:
    """Return the one failed configuration a candidate must preserve."""
    report = evidence_document(evidence)
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
    evidence: Path | dict[str, Any], base_configuration: dict[str, str]
) -> list[dict[str, Any]]:
    """Return repeated successful audit settings that preserve the base run."""
    report = evidence_document(evidence)
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
    if not supported:
        raise ValueError(
            "self-improvement evidence has no repeated successful independent-audit setting"
        )
    return [supported[key] for key in sorted(supported)]


def evidence_digest(evidence: Path) -> str:
    return "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest()


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> bytes:
    """Canonical JSON: compact, sorted keys, and raw UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def revised_contract_document(document: Any, revision: dict[str, str]) -> Any:
    """The contract document with an accepted instruction revision applied."""
    revised = json.loads(json.dumps(document))
    section, old_text, new_text = revision["section"], revision["old_text"], revision["new_text"]
    holders: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if not isinstance(value, dict):
            return
        instructions = value.get("instructions")
        if isinstance(instructions, dict) and isinstance(instructions.get(section), str):
            holders.append(instructions)
        children = value.get("child_contracts")
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


def development_contract_document(
    base_configuration: dict[str, str],
    audit: dict[str, Any] | None = None,
    tool: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The development contract document an adoption produces.

    The run-supplied members — task instruction, credential path, working
    directory, and per-task allowances — are fixed to the declared values
    so the document is stable across launches. `audit` adds the
    independent-audit stage. `tool` adds a `tool_defs` entry carrying the
    captured executable digest.
    """
    document = build_contract(
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
            },
        }
    return document


def adoption_contract_document(
    candidate_kind: str,
    candidate: dict[str, Any],
    contract_document: dict[str, Any],
    base_configuration: dict[str, str],
) -> dict[str, Any]:
    """The execution-contract document an accepted candidate produces.

    An instruction revision changes the self-improvement contract. Workflow
    and tool candidates produce a development contract. A source candidate
    uses the development contract with the rebuilt runtime, whose build
    fingerprint enters the resolved fingerprint document.
    """
    if candidate_kind == "instruction-revision":
        return revised_contract_document(contract_document, candidate["revision"])
    if candidate_kind == "workflow-configuration":
        return development_contract_document(
            candidate["base_configuration"], audit=candidate["independent_audit"]
        )
    if candidate_kind == "tool-definition":
        return development_contract_document(candidate["base_configuration"], tool=candidate["tool"])
    if candidate_kind == "source-change":
        return development_contract_document(base_configuration)
    raise ValueError(f"candidate kind {candidate_kind} has no adoption contract document")


def find_accepted_verification(episode: Path, tool: str) -> tuple[str, int]:
    """Locate the last accepted `verification/result` of `tool` in the episode tree.

    Returns the log path in bundle coordinates (under `episode/`) and the
    event's `seq`, the pairing the candidate adoption record cites.
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
    fingerprint_document: dict[str, Any],
    predecessor_contract_fingerprint: str,
    retained: dict[str, bytes],
    verification_tool: str,
    bundle_builder: list[str],
    bundle_verifier: list[str],
    permitted_verifier_fingerprints: set[str],
    builder_cwd: Path | None = None,
    builder_env: dict[str, str] | None = None,
    artifacts: list[dict[str, str]] | None = None,
    candidate_value: Any | None = None,
) -> dict[str, Any]:
    """Build and verify portable evidence for one accepted candidate.

    The resulting content-addressed directory is self-contained. The
    external adoption policy permits explicit verifier fingerprints and
    may require the proposal contract as the candidate's predecessor.
    `candidate_value` is the value the cited verification judged; it is
    retained as canonical JSON in `candidate.json`, so standalone
    verification can match it against the digest the event attests.
    """
    build = root / "evidence" / "bundle-build"
    shutil.copytree(episode, build / "episode")
    fingerprint_bytes = canonical_json(fingerprint_document)
    (build / "fingerprint-document.json").write_bytes(fingerprint_bytes)
    if candidate_value is not None:
        (build / "candidate.json").write_bytes(canonical_json(candidate_value))
    for name, content in sorted(retained.items()):
        destination = build / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    if artifacts is None:
        artifacts = [{"path": name, "sha256": digest_bytes(content)} for name, content in sorted(retained.items())]
    (build / "artifact-manifest.json").write_bytes(canonical_json(artifacts))
    verification_log, verification_seq = find_accepted_verification(episode, verification_tool)
    result = subprocess.run(
        [
            *bundle_builder,
            str(build),
            "episode/episode.jsonl",
            "fingerprint-document.json",
            "artifact-manifest.json",
            verification_log,
            str(verification_seq),
            predecessor_contract_fingerprint,
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
    bundle_dir = root / "evidence" / "bundles" / address.removeprefix("sha256:")
    bundle_dir.parent.mkdir(parents=True, exist_ok=True)
    build.rename(bundle_dir)
    verified_result = subprocess.run(
        [*bundle_verifier, str(bundle_dir), predecessor_contract_fingerprint],
        cwd=builder_cwd,
        env=builder_env,
        text=True,
        capture_output=True,
        timeout=1_800,
        check=False,
    )
    if verified_result.returncode != 0:
        detail = verified_result.stderr.strip() or verified_result.stdout.strip() or f"exit status {verified_result.returncode}"
        raise ValueError(f"bundle verifier failed: {detail}")
    try:
        verified = json.loads(verified_result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"bundle verifier returned invalid JSON: {error}") from error
    expected = {
        "bundle_address": address,
        "contract_fingerprint": digest_bytes(fingerprint_bytes),
        "predecessor_contract_fingerprint": predecessor_contract_fingerprint,
        "verification_tool": verification_tool,
        "verification_log": verification_log,
        "verification_seq": verification_seq,
    }
    for key, value in expected.items():
        if verified.get(key) != value:
            raise ValueError(f"bundle verifier {key} is {verified.get(key)!r}; expected {value!r}")
    verifier_fingerprint = verified.get("verifier_fingerprint")
    if verifier_fingerprint not in permitted_verifier_fingerprints:
        raise ValueError(
            f"adoption policy does not permit verifier fingerprint {verifier_fingerprint!r}"
        )
    return {**verified, "bundle_directory": str(bundle_dir)}


def instruction_candidate_from_outcome(
    outcome_value: Any,
    documents: dict[str, Any],
    evaluated: dict[str, str],
    evidence: Path,
    base_configuration: dict[str, str],
) -> dict[str, Any]:
    """Validate a diagnosis result and record its instruction revision."""
    if not isinstance(outcome_value, dict) or outcome_value.get("branch") != "revise-instructions":
        raise ValueError("self-improvement outcome did not select revise-instructions")
    return create_instruction_candidate(
        evaluated,
        evidence_digest(evidence),
        base_configuration,
        outcome_value.get("instruction_revision"),
        documents,
    )


def tool_candidate_from_outcome(
    outcome_value: Any,
    evaluated: dict[str, str],
    evidence: Path,
    base_configuration: dict[str, str],
) -> tuple[dict[str, Any], str]:
    """Validate a diagnosis result and record its tool definition.

    Returns the candidate and the executable content the runner retains
    as a file beside it.
    """
    if not isinstance(outcome_value, dict) or outcome_value.get("branch") != "define-tool":
        raise ValueError("self-improvement outcome did not select define-tool")
    definition = validate_tool_definition(outcome_value.get("tool_definition"))
    candidate = create_tool_candidate(
        evaluated,
        evidence_digest(evidence),
        base_configuration,
        {field: definition[field] for field in ("name", "description", "executable_sha256")},
    )
    return candidate, definition["executable"]


def workflow_candidate_from_outcome(
    outcome_value: Any,
    supported_audits: list[dict[str, Any]],
    evaluated: dict[str, str],
    evidence: Path,
    base_configuration: dict[str, str],
) -> dict[str, Any]:
    """Validate a diagnosis result and record its observed audit setting."""
    if not isinstance(outcome_value, dict) or outcome_value.get("branch") != "configure-workflow":
        raise ValueError("self-improvement outcome did not select configure-workflow")
    audit = validate_independent_audit(outcome_value.get("independent_audit"))
    if audit not in supported_audits:
        raise ValueError(
            "workflow candidate independent_audit was not a repeated successful evidence setting"
        )
    return create_workflow_candidate(
        evaluated,
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


def candidate_artifact_record(
    candidate: Path, base_source_tree: str, changed: list[str]
) -> dict[str, Any]:
    files = {}
    for name in sorted(changed):
        path = candidate / name
        files[name] = sha256_file(path) if path.is_file() else "absent"
    value = {"base_source_tree": base_source_tree, "files": files}
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return {**value, "digest": "sha256:" + hashlib.sha256(encoded).hexdigest()}


def rust_toolchain_fingerprints(cargo: Path) -> dict[str, str]:
    binaries = {}
    for name in ("cargo", "rustc", "rustfmt", "clippy-driver"):
        path = cargo.parent / name
        if not path.is_file():
            raise ValueError(f"--cargo toolchain lacks `{name}` at {path}")
        binaries[name] = sha256_file(path)
    return binaries


def validate_contract(binary: Path, contract: Path) -> dict[str, Any]:
    """Construct the generated contract without making a model request.

    Returns the resolved plan object. Its fingerprint document must rehash
    to the reported contract fingerprint.
    """
    result = subprocess.run(
        [str(binary), "plan", "--config", str(contract), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise ValueError(f"generated self-improvement contract is invalid: {detail}")
    try:
        plan = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"the resolved plan is not one JSON object: {error}") from error
    document = plan.get("fingerprint_document") if isinstance(plan, dict) else None
    if not isinstance(document, dict) or digest_bytes(canonical_json(document)) != plan.get("contract_fingerprint"):
        raise ValueError("the resolved plan's fingerprint document does not rehash to its contract fingerprint")
    return plan


def permitted_verifier_fingerprint(fingerprint_document: dict[str, Any], tool: str) -> str:
    """Select the configured verifier fingerprint permitted by adoption policy."""
    tools = fingerprint_document.get("tools")
    if not isinstance(tools, list):
        raise ValueError("proposal fingerprint document has no tools list")
    for entry in tools:
        if not isinstance(entry, dict) or entry.get("name") != tool:
            continue
        digest = entry.get("exec_sha256")
        if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
            return "sha256:" + digest
        raise ValueError(f"proposal verifier {tool} is not a configured executable")
    raise ValueError(f"proposal fingerprint document has no verifier tool {tool}")


def materialize_candidate_contract(
    document: dict[str, Any],
    candidate: Path,
    tool_name: str | None = None,
    tool_executable: Path | None = None,
) -> dict[str, Any]:
    """Give a candidate contract real construction paths without changing its fingerprint shape."""
    materialized = json.loads(json.dumps(document))

    def walk(value: Any) -> None:
        if not isinstance(value, dict):
            return
        grants = value.get("grants")
        if isinstance(grants, dict):
            for key in ("read", "write", "execute"):
                roots = grants.get(key)
                if isinstance(roots, list):
                    grants[key] = [str(candidate) if root == DEVELOPMENT_TASK_DIRECTORY else root for root in roots]
        tool_defs = value.get("tool_defs")
        if tool_name and tool_executable and isinstance(tool_defs, dict) and isinstance(tool_defs.get(tool_name), dict):
            tool_defs[tool_name]["exec"] = str(tool_executable)
        children = value.get("child_contracts")
        if isinstance(children, dict):
            for child in children.values():
                walk(child)
        workflow = value.get("workflow")
        nodes = workflow.get("nodes") if isinstance(workflow, dict) else None
        if isinstance(nodes, dict):
            for node in nodes.values():
                if isinstance(node, dict):
                    walk(node.get("model"))

    walk(materialized)
    return materialized


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


def write_diagnosis_validator(
    path: Path,
    contract: Path,
    evaluated: dict[str, str],
    evidence_sha256: str,
    base_configuration: dict[str, str],
    supported_audits: list[dict[str, Any]],
) -> None:
    """Write the diagnosis node's completion verifier.

    The runtime invokes it with the returned typed diagnosis as JSON on
    standard input. It applies the same evidence-backed candidate validation
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

evaluated = {evaluated!r}
evidence_sha256 = {evidence_sha256!r}
base_configuration = {base_configuration!r}
supported_audits = {supported_audits!r}
contract = {str(contract)!r}

findings = []
try:
    candidate = json.load(sys.stdin)
    branch = candidate.get("branch") if isinstance(candidate, dict) else None
    if branch == "configure-workflow":
        audit = validate_independent_audit(candidate.get("independent_audit"))
        if audit not in supported_audits:
            raise ValueError(
                "workflow candidate independent_audit was not a repeated successful evidence setting"
            )
        create_workflow_candidate(evaluated, evidence_sha256, base_configuration, audit)
    elif branch == "revise-instructions":
        documents = {{"contract.json": json.loads(pathlib.Path(contract).read_text(encoding="utf-8"))}}
        create_instruction_candidate(
            evaluated,
            evidence_sha256,
            base_configuration,
            candidate.get("instruction_revision"),
            documents,
        )
    elif branch == "define-tool":
        definition = validate_definition(candidate.get("tool_definition"))
        create_tool_candidate(
            evaluated,
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
    evidence_tool: dict[str, Any] | None = None,
    evidence_args: list[str] | None = None,
    evidence_read_roots: list[Path] | None = None,
) -> dict[str, Any]:
    diagnosis_read_roots = [
        str(path) for path in (evidence_read_roots or [evidence.parent])
    ]
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
    validator_tool = {
        "exec": str(validator),
        "description": "Validate the returned typed diagnosis: a workflow, instruction, or tool candidate must match the retained evidence and preserved run controls. The tool prints findings and prints nothing when the diagnosis is valid.",
        "timeout_seconds": 60,
    }
    diagnosis = {
        "name": "diagnose-foe-from-trajectory-measurements",
        "instructions": {
            "role": "Diagnose one general Foe limitation that explains the verified completion gap in the supplied trajectory measurements.",
            "scope": "Reason only from the bounded labeled trajectory digest supplied to this episode. Do not inspect repository source, benchmark tasks, graders, fixtures, or completed answers. The coding episode maps the causal intervention to source files.",
            "evidence": "Compare the failed and successful settings from the labeled digest. Use the final validation timeline and bounded verifier feedback before attributing a failure to missing validation. Cite episode identifiers and log sequence numbers only inside the causal contrast. Separate observed facts from uncertain attribution.",
            "controls": "Preserve the primary model route, reasoning effort, task allowances, token policy, service tier, and task set. Candidate selection uses verified task quality. Record resource changes without rejecting a quality improvement. The intervention must apply through general Foe behavior or a general workflow setting. It must not branch on a benchmark, dataset, task, contract name, checksum, fixture, grader, or episode id.",
            "sufficiency": "Choose `implement-source` when the trajectories activate a specific Foe source mechanism. Choose `configure-workflow` when a repeated quality gain is caused by an independent audit stage, and return the observed successful audit setting. Choose `revise-instructions` when the repeated causal difference is procedural guidance that one instruction section of the retained contract document `contract.json` can carry, and return the exact revision. Choose `define-tool` when one missing executable tool explains the gap, and return its complete definition. Choose `insufficient-evidence` when the intervention requires semantic knowledge absent from the log, an evaluator change, or an instruction that no runtime signal can enforce. A reasoning-effort difference without a workflow contrast establishes model capability rather than a Foe defect.",
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
    implementation = {
        "name": "implement-foe-improvement",
        "instructions": {
            "role": "Act as a fully capable Foe coding agent and implement the supplied typed diagnosis.",
            "scope": "Inspect source before editing. Change runtime source, a regression test, and each affected specification. Preserve reconstructable logs, declared permissions, typed outcomes, and explicit completion semantics.",
            "independence": "Do not change evaluation code, tasks, graders, model routes, reasoning settings, task allowances, token policy, or task selection. Do not encode benchmark identifiers, fixture values, or grader rules. Refuse an intervention that changes only a built-in default overridden by the explicit evaluated contract.",
            "validation": "The candidate check runs formatting, the Rust workspace tests, Clippy, and baseline-relative line budgets under the declared toolchain. Run it after implementation and use its findings to correct the candidate. Treat the check tool as the source of truth for line counts because scripts/loc.sh alone cannot distinguish an existing overage from candidate growth. State expected accuracy, cost, latency, and compatibility effects in the final result.",
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
        "version": 4,
        "name": "trajectory-evidence-self-improvement",
        "instructions": {"role": "Run the declared diagnosis and implementation workflow."},
        "tools": [*CODING_TOOLS, "block", "evidence", "check"],
        "tool_defs": {
            "evidence": evidence_tool
            or {
                "exec": "/usr/bin/cat",
                "description": "Return trajectory diagnoses derived from the retained evidence without modifying them.",
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
                    "args": {"args": evidence_args or [str(evidence)]},
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
        model = starts[0].get("data", {}).get("contract", {}).get("model", {})
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


def workflow_node_value(root: Path, node: str, typed: bool = True) -> Any:
    """Return the last value produced by one workflow node.

    `typed` restricts the result to object values, the shape a typed
    handoff produces; a completion verifier can judge any JSON value.
    """
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
            and (not typed or isinstance(data.get("value"), dict))
        ):
            value = data.get("value")
    return value


def trajectory_collection_findings(
    value: dict[str, Any] | None, expected: str
) -> list[str]:
    """Verify that Foe recorded the report derived during preflight."""
    if not isinstance(value, dict):
        return ["trajectory corpus diagnostics produced no recorded tool value"]
    findings = []
    if value.get("exit_code") != 0:
        findings.append(
            "trajectory corpus diagnostics executable did not exit successfully"
        )
    if value.get("stdout") != expected:
        findings.append(
            "trajectory corpus diagnostics differ from the preflight report"
        )
    return findings


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    answer.add_argument("--foe", type=Path, required=True)
    answer.add_argument("--candidate", type=Path, required=True)
    evidence = answer.add_mutually_exclusive_group(required=True)
    evidence.add_argument(
        "--corpus",
        type=Path,
        help="immutable trajectory corpus manifest collected by Foe's diagnostics tool",
    )
    evidence.add_argument(
        "--evidence",
        type=Path,
        help="precomputed compact diagnosis retained for compatibility",
    )
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
            "Use the failed and successful trajectory evidence to implement one general Foe "
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
        cases = args.cases.resolve(strict=True)
        _, _, _, pricing = read_cases(cases)
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
            raise ValueError("--cargo and --cargo-home are required for contract and candidate validation")
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
        validator_fingerprints = rust_toolchain_fingerprints(cargo) if cargo is not None else None
        candidate = args.candidate.resolve(strict=True)
        binary = args.foe.resolve(strict=True)
        corpus = args.corpus.resolve(strict=True) if args.corpus is not None else None
        if corpus is not None:
            expected_evaluated = {
                "source_tree": clean_source_tree(candidate),
                "runtime_binary": sha256_file(binary),
            }
            evidence_source: Path | dict[str, Any] = collect_from_corpus(
                corpus, cases, expected_evaluated
            )
        else:
            assert args.evidence is not None
            evidence_source = args.evidence.resolve(strict=True)
        evaluated = verify_evaluated_build(candidate, binary, evidence_source)
        base_configuration = failed_base_configuration(evidence_source)
        supported_audits = supported_independent_audits(
            evidence_source, base_configuration
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"self-improvement: {error}", file=sys.stderr)
        return 2

    preview = {
        "evaluation": "trajectory-evidence-self-improvement",
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
    if validator_fingerprints is not None:
        preview["candidate_validator"] = {"rust_toolchain": validator_fingerprints}
    if corpus is not None:
        preview["trajectory_corpus"] = {
            "manifest": str(corpus),
            "sha256": sha256_file(corpus),
        }
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
    if isinstance(evidence_source, Path):
        episode_evidence.write_bytes(evidence_source.read_bytes())
    else:
        episode_evidence.write_text(
            encoded_evidence(evidence_source), encoding="utf-8"
        )
    validator = root / "diagnosis-validator"
    write_diagnosis_validator(
        validator,
        root / "contract.json",
        evaluated,
        evidence_digest(episode_evidence),
        base_configuration,
        supported_audits,
    )
    toolchain = cargo.parent.parent
    rustup_home = toolchain.parent.parent if toolchain.parent.name == "toolchains" else None
    execute_roots = [toolchain, cargo_home / "bin", cargo_target]
    development_read_roots = [
        cargo_home,
        *(path for path in [rustup_home] if path is not None),
        *(path.resolve() for path in SYSTEM_DEVELOPMENT_READ_DIRS if path.is_dir()),
    ]
    evidence_tool = None
    evidence_args = None
    evidence_read_roots = None
    if corpus is not None:
        collector_source = Path(__file__).resolve().parent / "collect_diagnostics.py"
        corpus_source = Path(__file__).resolve().parent / "trajectory_corpus.py"
        build_record_source = Path(__file__).resolve().parent.parent / "foe_build.py"
        collector = root / "collect-trajectory-diagnostics"
        write_bound_python_launcher(
            collector,
            collector_source,
            [corpus_source, build_record_source, corpus, cases],
        )
        evidence_tool = {
            "exec": str(collector),
            "description": (
                "Derive a bounded diagnostic report after checking the declared "
                "trajectory corpus against its source and runtime fingerprints."
            ),
            "timeout_seconds": 60,
        }
        evidence_args = [
            "--corpus",
            str(corpus),
            "--cases",
            str(cases),
            "--expected-source-tree",
            evaluated["source_tree"],
            "--expected-runtime-binary",
            evaluated["runtime_binary"],
            "--expected-report-sha256",
            evidence_digest(episode_evidence),
        ]
        evidence_read_roots = [
            corpus.parent.parent,
            cases,
            collector_source,
            corpus_source,
            build_record_source,
        ]
    contract_document = build_config(
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
        evidence_tool,
        evidence_args,
        evidence_read_roots,
    )
    contract = root / "contract.json"
    write_json(contract, contract_document)
    try:
        plan = validate_contract(binary, contract)
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
        [str(binary), "--config", str(contract), "--headless", "--log-dir", str(episode)],
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
    if not isinstance(outcome_value, dict):
        outcome_value = workflow_node_value(episode, "diagnose-runtime")
    collection_findings = []
    if corpus is not None:
        collection_findings = trajectory_collection_findings(
            workflow_node_value(episode, "collect-trajectory-diagnostics"),
            episode_evidence.read_text(encoding="utf-8"),
        )
    branch = outcome_value.get("branch") if isinstance(outcome_value, dict) else None
    workflow_candidate = None
    workflow_candidate_path = None
    instruction_candidate_path = None
    tool_candidate_path = None
    tool_executable_path = None
    if branch == "configure-workflow":
        findings = []
        if changed:
            findings.append("workflow configuration candidate also changed source files")
        try:
            workflow_candidate = workflow_candidate_from_outcome(
                outcome_value,
                supported_audits,
                evaluated,
                episode_evidence,
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
        candidate_artifact = workflow_candidate
        candidate_kind = "workflow-configuration"
    elif branch == "revise-instructions":
        findings = []
        instruction_candidate = None
        if changed:
            findings.append("instruction revision candidate also changed source files")
        try:
            instruction_candidate = instruction_candidate_from_outcome(
                outcome_value,
                {"contract.json": contract_document},
                evaluated,
                episode_evidence,
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
        candidate_artifact = instruction_candidate
        candidate_kind = "instruction-revision"
    elif branch == "define-tool":
        findings = []
        tool_candidate = None
        if changed:
            findings.append("tool definition candidate also changed source files")
        try:
            tool_candidate, tool_executable = tool_candidate_from_outcome(
                outcome_value,
                evaluated,
                episode_evidence,
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
        candidate_artifact = tool_candidate
        candidate_kind = "tool-definition"
    elif branch == "implement-source":
        candidate_artifact = candidate_artifact_record(candidate, evaluated["source_tree"], changed)
        acceptance = check_candidate(check, candidate) if changed else {
            "accepted": False,
            "findings": ["source candidate contains no changed files"],
            "exit_code": None,
        }
        candidate_kind = "source-change"
    else:
        candidate_artifact = candidate_artifact_record(candidate, evaluated["source_tree"], changed)
        finding = (
            "diagnosis reported insufficient evidence"
            if branch == "insufficient-evidence"
            else "self-improvement outcome contains no supported candidate branch"
        )
        acceptance = {"accepted": False, "findings": [finding], "exit_code": None}
        candidate_kind = "no-candidate"
    if collection_findings:
        acceptance["accepted"] = False
        acceptance["findings"] = [
            *collection_findings,
            *acceptance["findings"],
        ]
    adoption = None
    if acceptance["accepted"] and candidate_kind != "no-candidate":
        retained: dict[str, bytes] = {}
        artifacts = None
        verification_tool = DIAGNOSIS_VALIDATOR_TOOL
        judged_value = outcome_value
        if candidate_kind == "workflow-configuration":
            retained["workflow-candidate.json"] = workflow_candidate_path.read_bytes()
        elif candidate_kind == "instruction-revision":
            retained["instruction-candidate.json"] = instruction_candidate_path.read_bytes()
        elif candidate_kind == "tool-definition":
            retained["tool-candidate.json"] = tool_candidate_path.read_bytes()
            retained["tool-candidate-executable"] = tool_executable_path.read_bytes()
        else:
            verification_tool = "check"
            judged_value = workflow_node_value(episode, "implement-runtime-improvement", typed=False)
            artifacts = [
                {"path": name, "sha256": value}
                for name, value in sorted(candidate_artifact["files"].items())
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
            candidate_contract = adoption_contract_document(
                candidate_kind, candidate_artifact, contract_document, base_configuration
            )
            staged_tool = None
            staged_tool_name = None
            if candidate_kind == "tool-definition":
                staged_tool_name = candidate_artifact["tool"]["name"]
                staged_tool = root / "candidate-tools" / staged_tool_name
                staged_tool.parent.mkdir(parents=True, exist_ok=True)
                staged_tool.write_bytes(tool_executable_path.read_bytes())
                staged_tool.chmod(0o755)
            candidate_contract = materialize_candidate_contract(
                candidate_contract,
                candidate,
                staged_tool_name,
                staged_tool,
            )
            candidate_binary = binary
            if candidate_kind == "source-change":
                built = subprocess.run(
                    [str(cargo), "build", "--quiet", "-p", "foe"],
                    cwd=candidate,
                    env=toolchain_environment,
                    text=True,
                    capture_output=True,
                    timeout=1_800,
                    check=False,
                )
                if built.returncode != 0:
                    detail = built.stderr.strip() or built.stdout.strip() or f"exit status {built.returncode}"
                    raise ValueError(f"candidate runtime build failed: {detail}")
                candidate_binary = cargo_target / "debug" / "foe"
            candidate_contract_path = root / "candidate-contract.json"
            write_json(candidate_contract_path, candidate_contract)
            candidate_plan = validate_contract(candidate_binary, candidate_contract_path)
            permitted_verifier = permitted_verifier_fingerprint(
                plan["fingerprint_document"], verification_tool
            )
            adoption = record_adoption(
                root,
                episode,
                candidate_plan["fingerprint_document"],
                plan["contract_fingerprint"],
                retained,
                verification_tool,
                [
                    str(cargo),
                    "run",
                    "--quiet",
                    "-p",
                    "foe-evidence",
                    "--bin",
                    "build-evidence-bundle",
                    "--",
                ],
                [
                    str(cargo),
                    "run",
                    "--quiet",
                    "-p",
                    "foe-evidence",
                    "--bin",
                    "verify-evidence-bundle",
                    "--",
                ],
                {permitted_verifier},
                builder_cwd=candidate,
                builder_env=toolchain_environment,
                artifacts=artifacts,
                candidate_value=judged_value,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            adoption = {"error": str(error)}
    record = {
        **preview,
        "evaluated_foe": evaluated,
        "duration_seconds": round(time.monotonic() - started, 3),
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "usage": usage,
        "candidate": str(candidate),
        "evidence": str(episode_evidence),
        "episode": str(episode),
        "changed_files": changed,
        "candidate_kind": candidate_kind,
        "candidate_artifact": candidate_artifact,
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
