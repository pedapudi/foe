#!/usr/bin/python3
"""Run identity-bound Foe self-improvement from trajectory diagnoses."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import pwd
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
from source_adoption import PROTECTED_BUILD_NAMES, capture_source_candidate, retain_parent_executables
from source_candidate_assessment import (
    assessment_revision_schema,
    bind_generation_evidence,
    generation_context,
    load_source_candidate_assessment,
    require_assessment_isolation,
    require_novel_source_candidate,
    validate_candidate_assessment_diagnostics,
)
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
CODING_TOOLS = ["read", "grep", "edit", "bash"]
SYSTEM_DEVELOPMENT_READ_DIRS = (Path("/usr/include"), Path("/usr/local/include"))
FAST_SERVICE_CREDIT_MULTIPLIER = 2.5
LINE_BUDGET_ROW = re.compile(r"^(\w+)\s+(\d+)\s+\(budget (\d+)\)$")
DIAGNOSIS_VALIDATOR_TOOL = "validate-candidate"
DIAGNOSIS_VALIDATOR_MODULES = (
    "instruction_candidate.py",
    "source_adoption.py",
    "source_candidate_assessment.py",
    "tool_candidate.py",
    "trajectory_diagnostics.py",
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
SHA256_IDENTITY = re.compile(r"^sha256:[0-9a-f]{64}$")
FAILURE_COUNT_FIELDS = {
    "total_failed_tests",
    "retained_failed_tests",
    "omitted_failed_tests",
    "unlocated_failed_tests",
    "ambiguous_failed_tests",
}


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def model_config(
    route: str,
    reasoning_effort: str,
    service_tier: str = "priority",
    credential_home: Path | None = None,
) -> dict[str, str]:
    provider, slash, model = route.partition("/")
    if not slash or not provider or not model:
        raise ValueError("model routes must have the form provider/model")
    answer = {
        "provider": provider,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "service_tier": service_tier,
    }
    home = credential_home or Path(pwd.getpwuid(os.getuid()).pw_dir)
    conventional = home / ".config" / "foe" / "credentials" / f"{provider}.json"
    if provider == "openai-codex":
        answer["token_file"] = str(conventional)
    elif provider in {"anthropic", "openai", "openai-compatible", "openrouter"}:
        answer["api_key_file"] = str(conventional)
    elif provider == "vertex":
        if conventional.is_file():
            values = json.loads(conventional.read_text(encoding="utf-8"))
            for field in ("credentials_file", "project", "location"):
                if isinstance(values.get(field), str):
                    answer[field] = values[field]
        answer.setdefault("credentials_file", str(conventional))
    return answer


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


def evaluation_base_configuration(configuration: dict[str, Any]) -> dict[str, str | None] | None:
    """Return the controls that determine candidate activation."""
    implementation = configuration.get("implementation")
    if not isinstance(implementation, dict):
        return None
    return {
        "model": implementation.get("model"),
        "reasoning_effort": implementation.get("reasoning_effort"),
        "service_tier": configuration.get("service_tier"),
        "token_policy": configuration.get("token_policy"),
        "workflow_ownership": (
            "foe-built-in" if configuration.get("built_in_workflow") is True else "evaluation-runner"
        ),
        "completion_governance": (
            "declared-verifier" if "completion_verifier" in configuration else "model-report"
        ),
    }


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
        candidate = evaluation_base_configuration(configuration)
        if candidate is None:
            continue
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
    """Return audits that reverse a same-task baseline failure under fixed controls."""
    report = json.loads(evidence.read_text(encoding="utf-8"))
    summaries = report.get("evaluation_summary")
    if not isinstance(summaries, list):
        raise ValueError("self-improvement evidence has no evaluation_summary list")
    activation_tasks = {
        summary.get("task")
        for summary in summaries
        if isinstance(summary, dict)
        and isinstance(summary.get("task"), str)
        and isinstance(summary.get("execution_configuration"), dict)
        and "independent_audit" not in summary["execution_configuration"]
        and evaluation_base_configuration(summary["execution_configuration"])
        == base_configuration
        and type(summary.get("attempts")) is int
        and type(summary.get("verified_successes")) is int
        and summary["verified_successes"] < summary["attempts"]
    }
    if not activation_tasks:
        raise ValueError(
            "self-improvement evidence has no task-specific baseline failure for the preserved controls"
        )
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
            or summary.get("task") not in activation_tasks
        ):
            continue
        audit = configuration.get("independent_audit")
        observed_base = evaluation_base_configuration(configuration)
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
            "contrast_sha256",
            "task",
            "failure_profile",
            "failed_attempts",
            "successful_episode_ids",
        }:
            raise ValueError(f"repeated failure contrast {index} has an invalid shape")
        task = contrast["task"]
        contrast_sha256 = contrast["contrast_sha256"]
        profile = contrast["failure_profile"]
        failed = contrast["failed_attempts"]
        successful = contrast["successful_episode_ids"]
        unsigned_contrast = {
            key: value for key, value in contrast.items() if key != "contrast_sha256"
        }
        expected_contrast_sha256 = digest_bytes(canonical_json(unsigned_contrast))
        if (
            not isinstance(contrast_sha256, str)
            or SHA256_IDENTITY.fullmatch(contrast_sha256) is None
            or contrast_sha256 != expected_contrast_sha256
        ):
            raise ValueError(
                f"repeated failure contrast {index} has an invalid contrast digest"
            )
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
        if not isinstance(failed, list) or len(failed) < 2:
            raise ValueError(f"repeated failure contrast {index} has fewer than two failed episodes")
        failed_episode_ids = []
        for attempt in failed:
            if not isinstance(attempt, dict) or set(attempt) != {
                "episode_id",
                "verifier_report_sha256",
                "failure_evidence_counts",
                "failure_loci",
            }:
                raise ValueError(
                    f"repeated failure contrast {index} has an invalid failed attempt"
                )
            episode_id = attempt.get("episode_id")
            report_sha256 = attempt.get("verifier_report_sha256")
            counts = attempt.get("failure_evidence_counts")
            loci = attempt.get("failure_loci")
            if not isinstance(episode_id, str) or not episode_id:
                raise ValueError(
                    f"repeated failure contrast {index} has an invalid failed episode"
                )
            if (
                not isinstance(report_sha256, str)
                or SHA256_IDENTITY.fullmatch(report_sha256) is None
            ):
                raise ValueError(
                    f"repeated failure contrast {index} has an invalid verifier report digest"
                )
            if (
                not isinstance(counts, dict)
                or set(counts) != FAILURE_COUNT_FIELDS
                or not all(
                    type(value) is int and value >= 0 for value in counts.values()
                )
                or counts["total_failed_tests"] == 0
                or counts["retained_failed_tests"]
                != counts["total_failed_tests"]
                or counts["omitted_failed_tests"] != 0
                or counts["unlocated_failed_tests"] != 0
                or counts["ambiguous_failed_tests"] != 0
            ):
                raise ValueError(
                    f"repeated failure contrast {index} has incomplete failure evidence"
                )
            if not isinstance(loci, list) or len(loci) != counts["total_failed_tests"]:
                raise ValueError(
                    f"repeated failure contrast {index} has invalid failure loci"
                )
            locus_ids = []
            for locus in loci:
                if (
                    not isinstance(locus, dict)
                    or not {"name", "failure_class", "locus_sha256"}.issubset(locus)
                    or not set(locus).issubset(
                        {
                            "name",
                            "failure_class",
                            "locus_sha256",
                            "location",
                            "assertion",
                            "message",
                        }
                    )
                    or not all(isinstance(value, str) and value for value in locus.values())
                    or SHA256_IDENTITY.fullmatch(locus["locus_sha256"]) is None
                    or not any(key in locus for key in ("location", "assertion", "message"))
                ):
                    raise ValueError(
                        f"repeated failure contrast {index} has an invalid failure locus"
                    )
                locus_ids.append(locus["locus_sha256"])
            if len(set(locus_ids)) != len(locus_ids):
                raise ValueError(
                    f"repeated failure contrast {index} repeats a failure locus"
                )
            failed_episode_ids.append(episode_id)
        if (
            not isinstance(successful, list)
            or not successful
            or not all(isinstance(value, str) and value for value in successful)
        ):
            raise ValueError(f"repeated failure contrast {index} has no successful episode")
        if len(set(failed_episode_ids)) != len(failed_episode_ids) or len(
            set(successful)
        ) != len(successful):
            raise ValueError(f"repeated failure contrast {index} repeats an episode identity")
        if set(failed_episode_ids).intersection(successful):
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
) -> dict[str, Any]:
    """The development program document an adoption produces.

    The run-supplied members — task instruction, credential path, working
    directory, and per-task allowances — are fixed to the declared values
    so the document is stable across launches. `audit` adds the
    independent-audit stage, `tool` adds a tool_defs entry carrying the
    executable's content hash.
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
    return document


def adoption_state_document(
    candidate_kind: str,
    candidate: dict[str, Any],
    program_document: dict[str, Any],
) -> dict[str, Any]:
    """The state document an adoption of `candidate` creates.

    docs/lineage-identity.md "Harness adoptions" states the one rule: the
    state document is the program document that will run under the
    adoption. An instruction revision yields the revised self-improvement
    program document; the other kinds yield the development program
    document the adoption produces, with the audit stage applied or the
    defined tool declared. A source candidate has no program identity until
    its rebuilt binary resolves the external evaluation program.
    """
    if candidate_kind == "instruction-revision":
        return revised_program_document(program_document, candidate["revision"])
    if candidate_kind == "workflow-configuration":
        return development_program_document(
            candidate["base_configuration"], audit=candidate["independent_audit"]
        )
    if candidate_kind == "tool-definition":
        return development_program_document(candidate["base_configuration"], tool=candidate["tool"])
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
    ancestry_checker: list[str],
) -> dict[str, Any]:
    """Record one accepted candidate as a lineage transition under `root`.

    Writes an evidence bundle containing the episode tree, child identity
    document, and artifact manifest over retained candidate files. It completes
    the bundle through `build-bundle`, writes the state documents, and requires
    the lineage ancestry checker to accept the resulting transition.
    Resolver inputs live at `root/lineage/states/<hex>.json` by state identity
    and `root/lineage/evidence/<hex>` by content address.
    """
    build = root / "lineage" / "bundle-build"
    shutil.copytree(episode, build / "episode")
    identity_bytes = canonical_json(state_document)
    (build / "child-identity.json").write_bytes(identity_bytes)
    for name, content in sorted(retained.items()):
        retained_path = build / name
        retained_path.parent.mkdir(parents=True, exist_ok=True)
        retained_path.write_bytes(content)
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
    checked = subprocess.run(
        [*ancestry_checker, str(child_state), str(states), str(evidence_dir.parent)],
        text=True,
        capture_output=True,
        timeout=1_800,
        check=False,
    )
    if checked.returncode != 0:
        detail = checked.stderr.strip() or checked.stdout.strip() or f"exit status {checked.returncode}"
        raise ValueError(f"lineage ancestry check failed: {detail}")
    try:
        ancestry = json.loads(checked.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"lineage ancestry checker output is not JSON: {error}") from error
    if (
        not isinstance(ancestry, dict)
        or set(ancestry) != {"chain", "unverifiable"}
        or not isinstance(ancestry.get("chain"), list)
        or ancestry["chain"][0:1] != [child_identity]
        or not isinstance(ancestry.get("unverifiable"), list)
    ):
        raise ValueError("lineage ancestry checker output does not start with the adopted program")
    builder_path = Path(bundle_builder[0])
    checker_path = Path(ancestry_checker[0])
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
        "bundle_builder_sha256": digest_bytes(builder_path.read_bytes()),
        "ancestry_checker_sha256": digest_bytes(checker_path.read_bytes()),
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
    return answer


def build_metadata_hashes(root: Path) -> dict[str, str]:
    """Hash tracked and untracked files that can change the trusted build graph."""
    result = subprocess.run(
        ["/usr/bin/git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    answer = {}
    for value in result.stdout.split(b"\0"):
        if not value:
            continue
        name = value.decode("utf-8")
        base = name.rsplit("/", 1)[-1]
        if base in PROTECTED_BUILD_NAMES or base.endswith(".bzl"):
            path = root / name
            answer[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "absent"
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


def changed_source_paths(candidate: Path) -> list[str]:
    """Return tracked and untracked changes without interpreting status prefixes."""
    commands = (
        ["/usr/bin/git", "diff", "--name-only", "--no-renames", "-z", "HEAD", "--"],
        ["/usr/bin/git", "ls-files", "--others", "--exclude-standard", "-z"],
    )
    paths = set()
    for command in commands:
        result = subprocess.run(command, cwd=candidate, capture_output=True, check=True)
        for value in result.stdout.split(b"\0"):
            if value:
                paths.add(value.decode("utf-8"))
    return sorted(paths)


def require_successful_adoption(
    acceptance: dict[str, Any], recorder: Any
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Make lineage adoption part of candidate acceptance."""
    if not acceptance["accepted"]:
        return None, acceptance
    try:
        return recorder(), acceptance
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        finding = f"lineage adoption failed: {error}"
        return {"error": str(error)}, {
            **acceptance,
            "accepted": False,
            "findings": [*acceptance["findings"], finding],
            "exit_code": None,
        }


def candidate_disposition(acceptance: dict[str, Any], episode_exit: int) -> tuple[bool, int]:
    """Return whether direct work is required and the process exit status."""
    accepted = acceptance["accepted"]
    return not accepted, 0 if accepted else episode_exit or 3


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
    task = json.loads(program.read_text(encoding="utf-8"))["task"]
    if plan.get("task", task) != task:
        raise ValueError("the resolved plan task differs from its program document")
    plan["task"] = task
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
    baseline_build_metadata = build_metadata_hashes(candidate)
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
baseline_build_metadata = {baseline_build_metadata!r}
baseline_benchmark_identifiers = {baseline_benchmark_identifiers!r}
allowed_directories = {ALLOWED_DIRECTORIES!r}
protected_build_names = {PROTECTED_BUILD_NAMES!r}
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
def source_hashes():
    answer = {{}}
    for directory in allowed_directories:
        for item in sorted((root / directory).rglob("*")):
            if item.is_file():
                answer[item.relative_to(root).as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
    return answer

def repository_status():
    output = subprocess.run(
        [
            "/usr/bin/git", "status", "--porcelain=v1", "-z",
            "--untracked-files=all", "--no-renames",
        ],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    return [value.decode("utf-8") for value in output.split(b"\\0") if value]

def build_metadata_hashes():
    listed = subprocess.run(
        ["/usr/bin/git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    answer = {{}}
    for value in listed.split(b"\\0"):
        if not value:
            continue
        name = value.decode("utf-8")
        base = name.rsplit("/", 1)[-1]
        if base in protected_build_names or base.endswith(".bzl"):
            item = root / name
            answer[name] = hashlib.sha256(item.read_bytes()).hexdigest() if item.is_file() else "absent"
    return answer

current = source_hashes()
changed = sorted(name for name in set(baseline) | set(current) if baseline.get(name) != current.get(name))
removed_digests = {{baseline[name] for name in baseline if name not in current}}
def material_regular_change(name):
    item = root / name
    return (
        name in current
        and baseline.get(name) != current[name]
        and item.is_file()
        and not item.is_symlink()
        and (name in baseline or current[name] not in removed_digests)
    )
status = repository_status()
current_build_metadata = build_metadata_hashes()
all_changed = [line[3:] for line in status if len(line) > 3]
findings = []
if not baseline_validation:
    outside = sorted(set(all_changed) - set(changed))
    if outside:
        findings.append("changes outside the runtime, documentation, and example surface: " + ", ".join(outside))
    if not any(
        material_regular_change(name)
        and name.startswith("crates/")
        and name.endswith(".rs")
        and not name.endswith("_test.rs")
        for name in changed
    ):
        findings.append("the candidate contains no Rust implementation change")
    if not any(material_regular_change(name) and name.endswith("_test.rs") for name in changed):
        findings.append("the candidate contains no Rust regression test")
    if not any(
        material_regular_change(name) and name.startswith("docs/") and name.endswith(".md")
        for name in changed
    ):
        findings.append("the candidate does not update an affected specification")
    protected_changes = sorted(
        name
        for name in set(baseline_build_metadata) | set(current_build_metadata)
        if baseline_build_metadata.get(name) != current_build_metadata.get(name)
    )
    if protected_changes:
        findings.append(
            "automatic source candidates cannot change build metadata: "
            + ", ".join(protected_changes)
        )
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
    post_status = repository_status()
    post_source = source_hashes()
    post_build_metadata = build_metadata_hashes()
    if post_status != status or post_source != current or post_build_metadata != current_build_metadata:
        findings.append("candidate validation changed the source tree or build metadata")
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
    candidate_assessment_diagnostics: dict[str, Any] | None = None,
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
    encoded_assessment = (
        base64.b64encode(canonical_json(candidate_assessment_diagnostics)).decode("ascii")
        if candidate_assessment_diagnostics is not None
        else None
    )
    script = f'''#!/usr/bin/python3
import base64
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from instruction_candidate import create as create_instruction_candidate
from tool_candidate import create as create_tool_candidate
from tool_candidate import validate_definition
from workflow_candidate import create as create_workflow_candidate
from workflow_candidate import validate_independent_audit
from source_candidate_assessment import validate_revised_diagnosis

identity = {identity!r}
evidence_sha256 = {evidence_sha256!r}
base_configuration = {base_configuration!r}
supported_audits = {supported_audits!r}
failure_contrasts = {failure_contrasts!r}
expected_branch = {expected_candidate_branch(requested_candidate_kind)!r}
automatic_selection = {requested_candidate_kind == "auto"!r}
candidate_assessment_base64 = {encoded_assessment!r}
candidate_assessment_diagnostics = (
    json.loads(base64.b64decode(candidate_assessment_base64))
    if candidate_assessment_base64 is not None
    else None
)
program = {str(program)!r}

findings = []

def require_failure_coverage(candidate, contrast):
    causal = candidate.get("causal_contrast")
    if not isinstance(causal, dict):
        raise ValueError("candidate has no causal contrast")
    failed = causal.get("failed")
    if not isinstance(failed, list):
        raise ValueError("causal contrast has no failed-attempt citations")
    expected = {{attempt.get("episode_id"): attempt for attempt in contrast["failed_attempts"]}}
    observed = {{}}
    for citation in failed:
        if not isinstance(citation, dict):
            raise ValueError("causal contrast has an invalid failed-attempt citation")
        episode_id = citation.get("episode_id")
        if not isinstance(episode_id, str) or episode_id in observed:
            raise ValueError("causal contrast repeats or omits a failed episode identity")
        observed[episode_id] = citation
    if set(observed) != set(expected):
        raise ValueError("causal contrast must cite every failed episode exactly once")
    for episode_id, attempt in expected.items():
        report_sha256 = attempt.get("verifier_report_sha256")
        counts = attempt.get("failure_evidence_counts")
        expected_loci = {{locus.get("locus_sha256") for locus in attempt.get("failure_loci", [])}}
        if (
            not isinstance(report_sha256, str)
            or not isinstance(counts, dict)
            or counts.get("total_failed_tests") != len(expected_loci)
            or counts.get("retained_failed_tests") != len(expected_loci)
            or counts.get("omitted_failed_tests") != 0
            or counts.get("unlocated_failed_tests") != 0
            or counts.get("ambiguous_failed_tests") != 0
            or not expected_loci
            or None in expected_loci
        ):
            raise ValueError(
                "selected contrast lacks complete verifier failure loci"
            )
        citation = observed[episode_id]
        if citation.get("verifier_report_sha256") != report_sha256:
            raise ValueError("causal contrast cites the wrong verifier report digest")
        locus_sha256s = citation.get("locus_sha256s")
        if (
            not isinstance(locus_sha256s, list)
            or not all(isinstance(value, str) for value in locus_sha256s)
            or len(locus_sha256s) != len(set(locus_sha256s))
            or set(locus_sha256s) != expected_loci
        ):
            raise ValueError("causal contrast must cite every failure locus exactly once")
        if not isinstance(citation.get("explanation"), str) or not citation["explanation"].strip():
            raise ValueError("causal contrast must explain every failed attempt")
    successful = causal.get("successful")
    if (
        not isinstance(successful, list)
        or not all(isinstance(value, str) for value in successful)
        or set(successful) != set(contrast["successful_episode_ids"])
    ):
        raise ValueError("causal contrast must cite every successful episode")
    shared = causal.get("shared_mechanism")
    if not isinstance(shared, str) or not shared.strip():
        raise ValueError("causal contrast must state one shared failure mechanism")

try:
    candidate = json.load(sys.stdin)
    validate_revised_diagnosis(candidate, candidate_assessment_diagnostics)
    branch = candidate.get("branch") if isinstance(candidate, dict) else None
    if automatic_selection and branch not in (
        "implement-source", "configure-workflow", "insufficient-evidence"
    ):
        raise ValueError(
            "automatic selection permits only source-change and workflow-configuration candidates"
        )
    if expected_branch is not None and branch not in (expected_branch, "insufficient-evidence"):
        raise ValueError(
            f"requested candidate kind requires branch {{expected_branch}}, received {{branch}}"
        )
    if branch not in ("insufficient-evidence", None):
        selected_digest = candidate.get("failure_contrast_sha256")
        matches = [
            contrast
            for contrast in failure_contrasts
            if contrast.get("contrast_sha256") == selected_digest
        ]
        if len(matches) != 1:
            raise ValueError(
                "candidate does not select one supported repeated failure contrast"
            )
        selected_contrast = matches[0]
        require_failure_coverage(candidate, selected_contrast)
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
    candidate_assessment_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if candidate_assessment_diagnostics is not None:
        validate_candidate_assessment_diagnostics(candidate_assessment_diagnostics)
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
            "audit setting; the runner binds that setting directly from the evidence. Instruction revisions and "
            "tool definitions require application support before automatic selection may choose them. Choose "
            "`insufficient-evidence` when the intervention requires semantic knowledge absent from the log or "
            "an evaluator change. A reasoning-effort difference without a workflow contrast establishes model "
            "capability rather than a Foe defect."
        )
    assessment_instruction = (
        "The supplied candidate_assessment_diagnostics describe an externally rejected source "
        "candidate and the prior typed diagnosis that produced it. Contrast the verified patch "
        "with every bounded failed verifier locus and final validation timeline. Cite the assessment "
        "contrast, rejected candidate identity, prior diagnosis digest, every failed attempt, every "
        "verifier-report digest, every locus digest, and all qualified success episode references in "
        "assessment_revision. Choose retain when the prior mechanism remains supported, narrow when "
        "only a qualified subset remains supported, replace when the contrast falsifies the mechanism, "
        "or insufficient-evidence when the bounded evidence cannot distinguish those dispositions."
        if candidate_assessment_diagnostics is not None
        else None
    )
    diagnosis = {
        "name": "diagnose-foe-from-trajectory-measurements",
        "instructions": {
            "role": "Diagnose one general Foe limitation that explains the verified completion gap in the supplied trajectory measurements.",
            "scope": "Reason only from the bounded labeled trajectory digest supplied to this episode. Do not inspect repository source, benchmark tasks, graders, fixtures, or completed answers. The coding episode maps the causal intervention to source files.",
            "evidence": "For a candidate-producing disposition, select one object from repeated_failure_contrasts and return its contrast_sha256 as failure_contrast_sha256. For every failed attempt, cite its episode identity, verifier-report digest, and every failure-locus digest in causal_contrast.failed, then explain that attempt's locus. State one shared mechanism that accounts for every cited locus. An insufficient-evidence disposition omits failure_contrast_sha256, may leave causal_contrast.failed empty, and explains the missing shared mechanism in causal_contrast.difference. Diagnose only one task-specific contrast. Do not combine failure profiles or tasks. Choose insufficient-evidence when the loci do not support one shared mechanism. Use the final validation timeline and bounded verifier feedback before attributing a failure to missing validation. Cite log sequence numbers only inside the causal contrast. Separate observed facts from uncertain attribution.",
            "controls": "Preserve the primary model route, reasoning effort, task allowances, token policy, service tier, and task set. Candidate selection uses verified task quality. Record resource changes without rejecting a quality improvement. The intervention must apply through general Foe behavior or a general workflow setting. It must not branch on a benchmark, dataset, task, program name, checksum, fixture, grader, or episode identity.",
            "sufficiency": sufficiency,
            **({"candidate_assessment": assessment_instruction} if assessment_instruction else {}),
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
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "episode_id": {"type": "string", "minLength": 1},
                                        "verifier_report_sha256": {
                                            "type": "string",
                                            "minLength": 1,
                                        },
                                        "locus_sha256s": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "minItems": 1,
                                        },
                                        "explanation": {"type": "string", "minLength": 1},
                                    },
                                    "required": [
                                        "episode_id",
                                        "verifier_report_sha256",
                                        "locus_sha256s",
                                        "explanation",
                                    ],
                                    "additionalProperties": False,
                                },
                            },
                            "successful": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "difference": {"type": "string", "minLength": 1},
                            "shared_mechanism": {"type": "string", "minLength": 1},
                        },
                        "required": [
                            "failed",
                            "successful",
                            "difference",
                        ],
                        "additionalProperties": False,
                    },
                    "intervention": {"type": "string", "minLength": 1},
                    "activation_path": {"type": "string", "minLength": 1},
                    "preserved_controls": {"type": "string", "minLength": 1},
                    "falsification_condition": {"type": "string", "minLength": 1},
                    "failure_contrast_sha256": {
                        "type": "string",
                        "minLength": 71,
                        "maxLength": 71,
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
                    **(
                        {"assessment_revision": assessment_revision_schema()}
                        if candidate_assessment_diagnostics is not None
                        else {}
                    ),
                },
                "required": [
                    "limitation",
                    "attribution",
                    "causal_contrast",
                    "intervention",
                    "activation_path",
                    "preserved_controls",
                    "falsification_condition",
                    *(
                        ["assessment_revision"]
                        if candidate_assessment_diagnostics is not None
                        else []
                    ),
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
            "build_metadata": "Do not change Cargo, Bazel, module, toolchain, package, or build-script metadata. Automatic source candidates preserve the trusted build graph.",
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
            "build_metadata": "Reject and revert changes to Cargo, Bazel, module, toolchain, package, or build-script metadata.",
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
                        "insufficient-evidence": [],
                        **(
                            {"revise-instructions": []}
                            if requested_candidate_kind == "instruction-revision"
                            else {}
                        ),
                        **(
                            {"define-tool": []}
                            if requested_candidate_kind == "tool-definition"
                            else {}
                        ),
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
    answer.add_argument(
        "--candidate-assessment",
        type=Path,
        help="private evaluator-owned assessment of one rejected source candidate",
    )
    answer.add_argument("--cases", type=Path, required=True)
    answer.add_argument("--model", default="openai-codex/gpt-5.6-sol")
    answer.add_argument("--service-tier", choices=("default", "priority"), default="priority")
    answer.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "xhigh"),
        default="low",
    )
    answer.add_argument("--diagnosis-model", default="openai-codex/gpt-5.6-sol")
    answer.add_argument(
        "--diagnosis-reasoning-effort",
        choices=("low", "medium", "high", "xhigh"),
        default="low",
    )
    answer.add_argument(
        "--objective",
        default=(
            "Use the identity-bound failed and successful trajectory contrast to implement one general Foe "
            "improvement that raises task-owned verified completion across activation and transfer cases. "
            "Preserve the model route, reasoning effort, task allowances, token policy, and task set. "
            "Task quality is the promotion metric. Record tokens, cost, cache use, and latency as measurements. "
            "Preserve correctness and benchmark independence."
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
    answer.add_argument("--bundle-builder", type=Path, required=True)
    answer.add_argument("--ancestry-checker", type=Path, required=True)
    answer.add_argument("--source-checker", type=Path, required=True)
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
        bundle_builder = args.bundle_builder.resolve(strict=True)
        ancestry_checker = args.ancestry_checker.resolve(strict=True)
        source_checker = args.source_checker.resolve(strict=True)
        for label, executable in (
            ("--bundle-builder", bundle_builder),
            ("--ancestry-checker", ancestry_checker),
            ("--source-checker", source_checker),
        ):
            if not executable.is_file():
                raise ValueError(f"{label} must name a trusted executable file")
        identity = verify_evidence_identity(candidate, binary, evidence)
        base_configuration = failed_base_configuration(evidence)
        supported_audits = supported_independent_audits(evidence, base_configuration)
        failure_contrasts = supported_failure_contrasts(evidence)
        candidate_assessment_diagnostics = None
        if args.candidate_assessment is not None:
            if args.candidate_kind != "source-change":
                raise ValueError(
                    "candidate assessment feedback requires --candidate-kind source-change"
                )
            _, candidate_assessment_diagnostics = (
                load_source_candidate_assessment(args.candidate_assessment)
            )
            if (
                candidate_assessment_diagnostics["identities"]["parent_source_tree"]
                != identity["source_tree"]
            ):
                raise ValueError(
                    "candidate assessment parent source tree differs from the evaluated evidence"
                )
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
    if candidate_assessment_diagnostics is not None:
        preview["candidate_assessment_diagnostics_identity"] = (
            candidate_assessment_diagnostics["diagnostics_identity"]
        )
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
    toolchain = cargo.parent.parent
    rustup_home = toolchain.parent.parent if toolchain.parent.name == "toolchains" else None
    development_read_roots = [
        cargo_home,
        *(path for path in [rustup_home] if path is not None),
        *(path.resolve() for path in SYSTEM_DEVELOPMENT_READ_DIRS if path.is_dir()),
    ]
    if args.candidate_assessment is not None:
        try:
            require_assessment_isolation(
                args.candidate_assessment,
                root,
                [candidate, source_metadata, *development_read_roots],
            )
        except ValueError as error:
            print(f"self-improvement: {error}", file=sys.stderr)
            if not args.confirm_spend:
                remove_preview_validation_directories(
                    candidate, existing_validation_directories
                )
            if temporary:
                temporary.cleanup()
            return 2
    write_candidate_check(check, candidate, cargo, cargo_home, cargo_target)
    episode_evidence = root / "trajectory-evidence.json"
    if candidate_assessment_diagnostics is None:
        episode_evidence.write_bytes(evidence.read_bytes())
    else:
        write_json(
            episode_evidence,
            {
                "trajectory_diagnostics": json.loads(evidence.read_text(encoding="utf-8")),
                "candidate_assessment_diagnostics": candidate_assessment_diagnostics,
            },
        )
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
        candidate_assessment_diagnostics,
    )
    execute_roots = [toolchain, cargo_home / "bin", cargo_target]
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
        candidate_assessment_diagnostics,
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
    changed = changed_source_paths(candidate)
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
    artifact_accepted = acceptance["accepted"]
    adoption = None
    source_candidate = None
    if acceptance["accepted"] and candidate_kind == "source-change":
        source_bundle = root / "source-candidate-bundle"
        try:
            shutil.copytree(episode, source_bundle / "episode")
            (source_bundle / "parent-plan.json").write_bytes(
                canonical_json(
                    {
                        "identity": plan["identity"],
                        "identity_document": plan["identity_document"],
                        "program": plan["program"],
                        "task": plan["task"],
                    }
                )
            )
            retain_parent_executables(source_bundle, plan["program"])
            shutil.copy2(check, source_bundle / "candidate-check")
            if candidate_assessment_diagnostics is not None:
                if not isinstance(outcome_value, dict):
                    raise ValueError(
                        "assessment-guided source generation has no typed diagnosis"
                    )
                candidate_generation_context = generation_context(
                    candidate_assessment_diagnostics,
                    outcome_value,
                    evidence_digest(evidence),
                )
                bind_generation_evidence(
                    source_bundle,
                    candidate_assessment_diagnostics,
                    candidate_generation_context,
                )
            verification_log, verification_seq = find_accepted_verification(
                episode, "check"
            )
            planned_model = plan["program"].get("model", {})
            execution_credential = (
                Path(model["token_file"])
                if model.get("provider") == "openai-codex"
                and "token_file" not in planned_model
                else None
            )
            source_candidate = capture_source_candidate(
                source_checker,
                source_bundle,
                candidate,
                identity["source_tree"],
                "parent-plan.json",
                "episode/episode.jsonl",
                verification_log,
                verification_seq,
                "candidate-check",
                execution_credential,
            )
            require_novel_source_candidate(
                source_candidate, candidate_assessment_diagnostics
            )
            source_candidate["bundle"] = str(source_bundle.relative_to(root))
            source_candidate["lineage_status"] = "pending-external-evaluation"
            artifact_identity = source_candidate
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            acceptance = {
                **acceptance,
                "accepted": False,
                "findings": [
                    *acceptance["findings"],
                    f"source evidence capture failed: {error}",
                ],
                "exit_code": None,
            }
    elif acceptance["accepted"] and candidate_kind != "no-candidate":
        retained: dict[str, bytes] = {}
        verification_tool = DIAGNOSIS_VALIDATOR_TOOL
        if candidate_kind == "workflow-configuration":
            retained["workflow-candidate.json"] = workflow_candidate_path.read_bytes()
        elif candidate_kind == "instruction-revision":
            retained["instruction-candidate.json"] = instruction_candidate_path.read_bytes()
        elif candidate_kind == "tool-definition":
            retained["tool-candidate.json"] = tool_candidate_path.read_bytes()
            retained["tool-candidate-executable"] = tool_executable_path.read_bytes()

        def record_candidate_adoption() -> dict[str, Any]:
            return record_adoption(
                root,
                episode,
                adoption_state_document(
                    candidate_kind, artifact_identity, program_document
                ),
                plan["identity_document"],
                retained,
                verification_tool,
                [str(bundle_builder)],
                [str(ancestry_checker)],
            )

        adoption, acceptance = require_successful_adoption(
            acceptance,
            record_candidate_adoption,
        )
    direct_implementation_required, process_exit = candidate_disposition(
        acceptance, result.returncode
    )
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
        "source_candidate": source_candidate,
        "workflow_candidate": str(workflow_candidate_path) if workflow_candidate_path else None,
        "instruction_candidate": str(instruction_candidate_path) if instruction_candidate_path else None,
        "tool_candidate": str(tool_candidate_path) if tool_candidate_path else None,
        "tool_candidate_executable": str(tool_executable_path) if tool_executable_path else None,
        "candidate_acceptance": acceptance,
        "adoption": adoption,
        "artifact_outcome_mismatch": artifact_accepted and result.returncode != 0,
        "direct_implementation_required": direct_implementation_required,
    }
    write_json(root / "result.json", record)
    print(json.dumps(record, indent=2, sort_keys=True))
    if temporary:
        temporary.cleanup()
    return process_exit


if __name__ == "__main__":
    raise SystemExit(main())
