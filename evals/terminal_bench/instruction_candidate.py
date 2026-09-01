#!/usr/bin/python3
"""Create and validate instruction revisions against retained evidence."""

from __future__ import annotations

from typing import Any

from workflow_candidate import (
    candidate_digest,
    require_sha256,
    validate_base_configuration,
    validate_evaluated_foe,
)


SCHEMA_VERSION = 1
KIND = "instruction-revision"
REVISION_FIELDS = ("document", "new_text", "old_text", "section")


def resolve_section(document: Any, section: str) -> str:
    """Return the one instruction text the section key names in a contract document.

    The key is searched in the document's own `instructions`, in every
    nested `child_contracts` entry, and in every workflow model node, at any
    depth. A key found in two places is ambiguous and refused, so a
    revision cannot silently apply to the wrong episode's instructions.
    """
    texts: list[str] = []

    def walk(value: Any) -> None:
        if not isinstance(value, dict):
            return
        instructions = value.get("instructions")
        if isinstance(instructions, dict) and isinstance(instructions.get(section), str):
            texts.append(instructions[section])
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

    walk(document)
    if len(texts) != 1:
        raise ValueError(
            f"instruction candidate section {section!r} must name exactly one instruction section"
        )
    return texts[0]


def validate_revision(value: Any, documents: dict[str, Any]) -> dict[str, str]:
    """Return one normalized revision after checking its target document."""
    if not isinstance(value, dict) or set(value) != set(REVISION_FIELDS):
        raise ValueError(
            "instruction candidate revision must contain document, section, old_text, and new_text"
        )
    if not all(isinstance(value[field], str) and value[field] for field in REVISION_FIELDS):
        raise ValueError("instruction candidate revision fields must be nonempty strings")
    if value["document"] not in documents:
        known = ", ".join(sorted(documents))
        raise ValueError(f"instruction candidate document must be one of: {known}")
    section = resolve_section(documents[value["document"]], value["section"])
    if section.count(value["old_text"]) != 1:
        raise ValueError("instruction candidate old_text must occur exactly once in the named section")
    if value["new_text"] == value["old_text"]:
        raise ValueError("instruction candidate new_text must differ from old_text")
    return {field: value[field] for field in REVISION_FIELDS}


def create(
    evaluated_foe: dict[str, str],
    evidence_sha256: str,
    base_configuration: dict[str, str],
    revision: dict[str, str],
    documents: dict[str, Any],
) -> dict[str, Any]:
    """Associate one instruction revision with its evaluated source, binary, and evidence."""
    body = {
        "schema_version": SCHEMA_VERSION,
        "candidate_kind": KIND,
        "evaluated_foe": validate_evaluated_foe(evaluated_foe, label="instruction candidate"),
        "evidence_sha256": require_sha256("instruction candidate evidence_sha256", evidence_sha256),
        "base_configuration": validate_base_configuration(base_configuration),
        "revision": validate_revision(revision, documents),
    }
    return {**body, "digest": candidate_digest(body)}


def validate(
    value: Any, documents: dict[str, Any], evaluated_foe: dict[str, str] | None = None
) -> dict[str, Any]:
    """Validate a complete candidate and optionally require one evaluated Foe build."""
    required = {
        "schema_version",
        "candidate_kind",
        "evaluated_foe",
        "evidence_sha256",
        "base_configuration",
        "revision",
        "digest",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("instruction candidate has unknown or missing fields")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"instruction candidate schema_version must be {SCHEMA_VERSION}")
    if value.get("candidate_kind") != KIND:
        raise ValueError(f"instruction candidate candidate_kind must be {KIND}")
    evaluated = validate_evaluated_foe(value.get("evaluated_foe"), evaluated_foe, label="instruction candidate")
    body = {key: value[key] for key in required - {"digest"}}
    if value.get("digest") != candidate_digest(body):
        raise ValueError("instruction candidate digest does not match its contents")
    return create(
        evaluated,
        value.get("evidence_sha256"),
        validate_base_configuration(value.get("base_configuration")),
        validate_revision(value.get("revision"), documents),
        documents,
    )
