#!/usr/bin/python3
"""Create and validate tool definitions against retained evidence."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from workflow_candidate import (
    candidate_digest,
    require_sha256,
    validate_base_configuration,
    validate_evaluated_foe,
)


SCHEMA_VERSION = 1
KIND = "tool-definition"
TOOL_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")


def executable_digest(content: bytes) -> str:
    """Return the content digest a tool candidate declares for its executable."""
    return "sha256:" + hashlib.sha256(content).hexdigest()


def validate_definition(value: Any) -> dict[str, str]:
    """Return one normalized tool definition with a self-consistent digest.

    The definition carries the executable content itself, because the
    diagnosis episode that proposes it holds no write permission; the
    runner retains the content as a file beside the candidate.
    """
    required = {"name", "description", "executable", "executable_sha256"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(
            "tool candidate definition must contain name, description, executable, and executable_sha256"
        )
    if not all(isinstance(value[field], str) and value[field] for field in required):
        raise ValueError("tool candidate definition fields must be nonempty strings")
    if not TOOL_NAME.fullmatch(value["name"]):
        raise ValueError("tool candidate name must be a lowercase tool identifier")
    if value["executable_sha256"] != executable_digest(value["executable"].encode()):
        raise ValueError("tool candidate executable_sha256 does not match the executable content")
    return {field: value[field] for field in sorted(required)}


def create(
    evaluated_foe: dict[str, str],
    evidence_sha256: str,
    base_configuration: dict[str, str],
    tool: dict[str, str],
) -> dict[str, Any]:
    """Associate one tool_defs entry with its evaluated source, binary, and evidence.

    `tool` names the entry: its name, description, and executable content
    digest. The executable file itself is retained beside the candidate
    rather than inside it, and `validate` checks the retained bytes.
    """
    fields = {"description", "executable_sha256", "name"}
    if not isinstance(tool, dict) or set(tool) != fields:
        raise ValueError("tool candidate tool must contain name, description, and executable_sha256")
    if not all(isinstance(tool[field], str) and tool[field] for field in fields):
        raise ValueError("tool candidate tool fields must be nonempty strings")
    if not TOOL_NAME.fullmatch(tool["name"]):
        raise ValueError("tool candidate name must be a lowercase tool identifier")
    body = {
        "schema_version": SCHEMA_VERSION,
        "candidate_kind": KIND,
        "evaluated_foe": validate_evaluated_foe(evaluated_foe, label="tool candidate"),
        "evidence_sha256": require_sha256("tool candidate evidence_sha256", evidence_sha256),
        "base_configuration": validate_base_configuration(base_configuration),
        "tool": {field: tool[field] for field in sorted(fields)},
    }
    return {**body, "digest": candidate_digest(body)}


def validate(
    value: Any, executable: bytes, evaluated_foe: dict[str, str] | None = None
) -> dict[str, Any]:
    """Validate a complete candidate against the captured executable bytes."""
    required = {
        "schema_version",
        "candidate_kind",
        "evaluated_foe",
        "evidence_sha256",
        "base_configuration",
        "tool",
        "digest",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("tool candidate has unknown or missing fields")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"tool candidate schema_version must be {SCHEMA_VERSION}")
    if value.get("candidate_kind") != KIND:
        raise ValueError(f"tool candidate candidate_kind must be {KIND}")
    evaluated = validate_evaluated_foe(value.get("evaluated_foe"), evaluated_foe, label="tool candidate")
    tool = value.get("tool")
    if not isinstance(tool, dict):
        raise ValueError("tool candidate tool is invalid")
    if tool.get("executable_sha256") != executable_digest(executable):
        raise ValueError("tool candidate executable_sha256 does not match the retained file")
    body = {key: value[key] for key in required - {"digest"}}
    if value.get("digest") != candidate_digest(body):
        raise ValueError("tool candidate digest does not match its contents")
    return create(
        evaluated,
        value.get("evidence_sha256"),
        validate_base_configuration(value.get("base_configuration")),
        tool,
    )
