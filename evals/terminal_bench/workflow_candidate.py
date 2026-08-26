#!/usr/bin/python3
"""Create and validate identity-bound workflow configuration candidates."""

from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA_VERSION = 1
KIND = "independent-audit-workflow"
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


def validate_independent_audit(value: Any) -> dict[str, Any]:
    """Return one normalized independent-audit setting."""
    if not isinstance(value, dict) or set(value) != {"reasoning_effort", "model_calls"}:
        raise ValueError(
            "workflow candidate independent_audit must contain reasoning_effort and model_calls"
        )
    effort = value.get("reasoning_effort")
    calls = value.get("model_calls")
    if effort not in REASONING_EFFORTS:
        raise ValueError("workflow candidate independent_audit.reasoning_effort is invalid")
    if type(calls) is not int or not MIN_AUDIT_MODEL_CALLS <= calls <= MAX_AUDIT_MODEL_CALLS:
        raise ValueError(
            "workflow candidate independent_audit.model_calls must be between "
            f"{MIN_AUDIT_MODEL_CALLS} and {MAX_AUDIT_MODEL_CALLS}"
        )
    return {"reasoning_effort": effort, "model_calls": calls}


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


def create(
    evaluated_foe: dict[str, str],
    evidence_sha256: str,
    base_configuration: dict[str, str],
    independent_audit: dict[str, Any],
) -> dict[str, Any]:
    """Bind a workflow setting to the evaluated source, binary, and evidence."""
    body = {
        "schema_version": SCHEMA_VERSION,
        "candidate_kind": KIND,
        "evaluated_foe": dict(evaluated_foe),
        "evidence_sha256": evidence_sha256,
        "base_configuration": validate_base_configuration(base_configuration),
        "independent_audit": validate_independent_audit(independent_audit),
    }
    return {**body, "digest": candidate_digest(body)}


def validate(value: Any, evaluated_foe: dict[str, str] | None = None) -> dict[str, Any]:
    """Validate a complete candidate and optionally require one Foe identity."""
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
    """Return the audit setting after checking the preserved run controls."""
    observed = {
        "model": model,
        "reasoning_effort": reasoning_effort,
        "service_tier": service_tier,
        "token_policy": token_policy,
        "workflow_ownership": workflow_ownership,
        "completion_governance": completion_governance,
    }
    if candidate["base_configuration"] != validate_base_configuration(observed):
        raise ValueError("workflow candidate base configuration differs from the requested run")
    return candidate["independent_audit"]
