#!/usr/bin/python3
"""Create and validate identity-bound workflow configuration candidates."""

from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA_VERSION = 2
INDEPENDENT_AUDIT_SCHEMA_VERSION = 1
KIND = "verifier-governed-assessment-and-repair"
INDEPENDENT_AUDIT_KIND = "independent-audit-workflow"
REASONING_EFFORTS = ("low", "medium", "high", "xhigh")
MIN_AUDIT_MODEL_CALLS = 6
MAX_AUDIT_MODEL_CALLS = 120


def _hash_identity(value: Any, prefix: str, digits: int) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(prefix)
        and len(value) == len(prefix) + digits
        and all(character in "0123456789abcdef" for character in value[len(prefix) :])
    )


def candidate_digest(value: dict[str, Any]) -> str:
    """Return the digest that seals a candidate body."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def require_sha256(label: str, value: Any) -> str:
    """Return `value` when it is a `sha256:` digest string."""
    if not _hash_identity(value, "sha256:", 64):
        raise ValueError(f"{label} is invalid")
    return value


def validate_evaluated_foe(
    value: Any, evaluated_foe: dict[str, str] | None = None, label: str = "candidate"
) -> dict[str, str]:
    """Return one validated Foe source and binary identity."""
    if not isinstance(value, dict) or set(value) != {"source_tree", "runtime_binary"}:
        raise ValueError(f"{label} evaluated_foe is invalid")
    source_tree = value.get("source_tree")
    if not (
        _hash_identity(source_tree, "git-tree-sha1:", 40)
        or _hash_identity(source_tree, "git-tree-sha256:", 64)
    ):
        raise ValueError(f"{label} evaluated_foe.source_tree is invalid")
    if not _hash_identity(value.get("runtime_binary"), "sha256:", 64):
        raise ValueError(f"{label} evaluated_foe.runtime_binary is invalid")
    if evaluated_foe is not None and value != evaluated_foe:
        raise ValueError(f"{label} evaluates a different Foe source or binary")
    return {key: value[key] for key in ("runtime_binary", "source_tree")}


def validate_model_stage(value: Any, label: str) -> dict[str, Any]:
    """Return one normalized model-stage setting."""
    if not isinstance(value, dict) or set(value) != {"reasoning_effort", "model_calls"}:
        raise ValueError(
            f"workflow candidate {label} must contain reasoning_effort and model_calls"
        )
    effort = value.get("reasoning_effort")
    calls = value.get("model_calls")
    if effort not in REASONING_EFFORTS:
        raise ValueError(f"workflow candidate {label}.reasoning_effort is invalid")
    if type(calls) is not int or not MIN_AUDIT_MODEL_CALLS <= calls <= MAX_AUDIT_MODEL_CALLS:
        raise ValueError(
            f"workflow candidate {label}.model_calls must be between "
            f"{MIN_AUDIT_MODEL_CALLS} and {MAX_AUDIT_MODEL_CALLS}"
        )
    return {"reasoning_effort": effort, "model_calls": calls}


def validate_independent_audit(value: Any) -> dict[str, Any]:
    """Return one normalized independent-audit setting."""
    return validate_model_stage(value, "independent_audit")


def validate_assessment_and_repair(value: Any) -> dict[str, Any]:
    """Return the shared setting for assessment and conditional repair."""
    return validate_model_stage(value, "assessment_and_repair")


def validate_base_configuration(value: Any) -> dict[str, str]:
    """Return the evaluation settings a workflow candidate preserves."""
    required = {
        "model",
        "reasoning_effort",
        "service_tier",
        "token_policy",
        "workflow_ownership",
        "completion_governance",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(
            "workflow candidate base_configuration must contain model, reasoning_effort, "
            "service_tier, token_policy, workflow_ownership, and completion_governance"
        )
    if not isinstance(value.get("model"), str) or "/" not in value["model"]:
        raise ValueError("workflow candidate base_configuration.model is invalid")
    if value.get("reasoning_effort") not in REASONING_EFFORTS:
        raise ValueError("workflow candidate base_configuration.reasoning_effort is invalid")
    if value.get("service_tier") not in ("default", "priority"):
        raise ValueError("workflow candidate base_configuration.service_tier is invalid")
    if value.get("token_policy") not in ("measurement_only", "hard"):
        raise ValueError("workflow candidate base_configuration.token_policy is invalid")
    if value.get("workflow_ownership") not in ("foe-built-in", "evaluation-runner"):
        raise ValueError("workflow candidate base_configuration.workflow_ownership is invalid")
    if value.get("completion_governance") not in ("declared-verifier", "model-report"):
        raise ValueError("workflow candidate base_configuration.completion_governance is invalid")
    return {key: value[key] for key in sorted(required)}


def validate_preserved_configuration(value: Any) -> dict[str, str]:
    """Return controls unchanged by a verifier-governed workflow candidate."""
    required = {
        "model",
        "reasoning_effort",
        "service_tier",
        "token_policy",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(
            "workflow candidate preserved_configuration must contain model, reasoning_effort, "
            "service_tier and token_policy"
        )
    validated = validate_base_configuration(
        {
            **value,
            "workflow_ownership": "evaluation-runner",
            "completion_governance": "model-report",
        }
    )
    return {key: validated[key] for key in sorted(required)}


def create(
    evaluated_foe: dict[str, str],
    evidence_sha256: str,
    base_configuration: dict[str, str],
    independent_audit: dict[str, Any],
) -> dict[str, Any]:
    """Bind the retained schema-1 audit setting to its identity and evidence."""
    body = {
        "schema_version": INDEPENDENT_AUDIT_SCHEMA_VERSION,
        "candidate_kind": INDEPENDENT_AUDIT_KIND,
        "evaluated_foe": dict(evaluated_foe),
        "evidence_sha256": evidence_sha256,
        "base_configuration": validate_base_configuration(base_configuration),
        "independent_audit": validate_independent_audit(independent_audit),
    }
    return {**body, "digest": candidate_digest(body)}


def create_verifier_governed(
    evaluated_foe: dict[str, str],
    evidence_sha256: str,
    base_configuration: dict[str, str],
    assessment_and_repair: dict[str, Any],
) -> dict[str, Any]:
    """Bind verifier-governed assessment and repair to identity and evidence."""
    base = validate_base_configuration(base_configuration)
    preserved = {
        key: value
        for key, value in base.items()
        if key not in ("completion_governance", "workflow_ownership")
    }
    body = {
        "schema_version": SCHEMA_VERSION,
        "candidate_kind": KIND,
        "evaluated_foe": validate_evaluated_foe(
            evaluated_foe, label="workflow candidate"
        ),
        "evidence_sha256": require_sha256(
            "workflow candidate evidence_sha256", evidence_sha256
        ),
        "preserved_configuration": validate_preserved_configuration(preserved),
        "assessment_and_repair": validate_assessment_and_repair(
            assessment_and_repair
        ),
    }
    return {**body, "digest": candidate_digest(body)}


def validate(value: Any, evaluated_foe: dict[str, str] | None = None) -> dict[str, Any]:
    """Validate a complete candidate and optionally require one Foe identity."""
    if (
        isinstance(value, dict)
        and value.get("schema_version") == INDEPENDENT_AUDIT_SCHEMA_VERSION
    ):
        return validate_independent_audit_candidate(value, evaluated_foe)
    required = {
        "schema_version",
        "candidate_kind",
        "evaluated_foe",
        "evidence_sha256",
        "preserved_configuration",
        "assessment_and_repair",
        "digest",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("workflow candidate has unknown or missing fields")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"workflow candidate schema_version must be {SCHEMA_VERSION}")
    if value.get("candidate_kind") != KIND:
        raise ValueError(f"workflow candidate candidate_kind must be {KIND}")
    identity = value.get("evaluated_foe")
    validate_evaluated_foe(identity, evaluated_foe, label="workflow candidate")
    evidence_sha256 = require_sha256("workflow candidate evidence_sha256", value.get("evidence_sha256"))
    body = {key: value[key] for key in required - {"digest"}}
    if value.get("digest") != candidate_digest(body):
        raise ValueError("workflow candidate digest does not match its contents")
    return create_verifier_governed(
        identity,
        evidence_sha256,
        {
            **validate_preserved_configuration(value.get("preserved_configuration")),
            "workflow_ownership": "evaluation-runner",
            "completion_governance": "model-report",
        },
        validate_assessment_and_repair(value.get("assessment_and_repair")),
    )


def validate_independent_audit_candidate(
    value: Any, evaluated_foe: dict[str, str] | None = None
) -> dict[str, Any]:
    """Validate a retained schema-1 independent-audit candidate."""
    required = {
        "schema_version",
        "candidate_kind",
        "evaluated_foe",
        "evidence_sha256",
        "base_configuration",
        "independent_audit",
        "digest",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("workflow candidate has unknown or missing fields")
    if value.get("schema_version") != INDEPENDENT_AUDIT_SCHEMA_VERSION:
        raise ValueError(
            "workflow candidate schema_version must be "
            f"{INDEPENDENT_AUDIT_SCHEMA_VERSION} or {SCHEMA_VERSION}"
        )
    if value.get("candidate_kind") != INDEPENDENT_AUDIT_KIND:
        raise ValueError(
            "workflow candidate candidate_kind must be "
            f"{INDEPENDENT_AUDIT_KIND}"
        )
    identity = value.get("evaluated_foe")
    validate_evaluated_foe(identity, evaluated_foe, label="workflow candidate")
    evidence_sha256 = require_sha256(
        "workflow candidate evidence_sha256", value.get("evidence_sha256")
    )
    body = {key: value[key] for key in required - {"digest"}}
    if value.get("digest") != candidate_digest(body):
        raise ValueError("workflow candidate digest does not match its contents")
    return create(
        identity,
        evidence_sha256,
        validate_base_configuration(value.get("base_configuration")),
        validate_independent_audit(value.get("independent_audit")),
    )


def require_matching_run(
    candidate: dict[str, Any],
    *,
    model: str,
    reasoning_effort: str,
    service_tier: str,
    token_policy: str,
    workflow_ownership: str,
    completion_governance: str,
) -> dict[str, Any]:
    """Return the workflow application after checking preserved run controls."""
    observed = {
        "model": model,
        "reasoning_effort": reasoning_effort,
        "service_tier": service_tier,
        "token_policy": token_policy,
        "workflow_ownership": workflow_ownership,
        "completion_governance": completion_governance,
    }
    validated_observed = validate_base_configuration(observed)
    if candidate["schema_version"] == INDEPENDENT_AUDIT_SCHEMA_VERSION:
        if candidate["base_configuration"] != validated_observed:
            raise ValueError(
                "workflow candidate base configuration differs from the requested run"
            )
        return {
            "kind": INDEPENDENT_AUDIT_KIND,
            **candidate["independent_audit"],
        }
    if completion_governance != "declared-verifier":
        raise ValueError(
            "verifier-governed assessment and repair requires a declared completion verifier"
        )
    if workflow_ownership != "evaluation-runner":
        raise ValueError(
            "verifier-governed assessment and repair requires evaluation-runner workflow ownership"
        )
    preserved = {
        key: value
        for key, value in validated_observed.items()
        if key not in ("completion_governance", "workflow_ownership")
    }
    if candidate["preserved_configuration"] != validate_preserved_configuration(
        preserved
    ):
        raise ValueError(
            "workflow candidate preserved configuration differs from the requested run"
        )
    return {"kind": KIND, **candidate["assessment_and_repair"]}
