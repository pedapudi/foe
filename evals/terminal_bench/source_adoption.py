#!/usr/bin/python3
"""Invoke the trusted source-candidate checker and parse its normalized result."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
SOURCE_TREE = re.compile(r"git-tree-(?:sha1:[0-9a-f]{40}|sha256:[0-9a-f]{64})")


def checker_digest(checker: Path) -> str:
    return "sha256:" + hashlib.sha256(checker.read_bytes()).hexdigest()


def source_bundle_path(path: Path) -> tuple[Path, dict[str, str] | None]:
    """Resolve source evidence and retain every identity a result recorded."""
    if path.is_dir():
        return path.absolute(), None
    record = json.loads(path.read_text(encoding="utf-8"))
    candidate = record.get("source_candidate") if isinstance(record, dict) else None
    bundle = candidate.get("bundle") if isinstance(candidate, dict) else None
    if not isinstance(bundle, str) or not bundle:
        raise ValueError("source candidate record has no source evidence bundle")
    resolved = Path(bundle)
    if not resolved.is_absolute():
        resolved = path.parent / resolved
    if not resolved.exists():
        raise ValueError(f"source evidence bundle does not exist: {resolved}")
    names = {
        "source_bundle_identity": "source_bundle_identity",
        "source_candidate_identity": "source_candidate_identity",
        "base_source_tree": "base_source_tree",
        "parent_program_identity": "parent_program_identity",
    }
    expected = {}
    for retained, checked in names.items():
        value = candidate.get(retained)
        pattern = SOURCE_TREE if retained == "base_source_tree" else DIGEST
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise ValueError(f"source candidate record {retained} is invalid")
        expected[checked] = value
    capture_checker = candidate.get(
        "capture_checker_sha256", candidate.get("checker_sha256")
    )
    if (
        not isinstance(capture_checker, str)
        or DIGEST.fullmatch(capture_checker) is None
    ):
        raise ValueError("source candidate record capture checker identity is invalid")
    expected["capture_checker_sha256"] = capture_checker
    return resolved.absolute(), expected


def checked_output(checker: Path, arguments: list[str], fields: set[str]) -> dict[str, Any]:
    """Run the trusted checker and require its exact normalized output shape."""
    result = subprocess.run(
        [str(checker), *arguments],
        text=True,
        capture_output=True,
        timeout=1_800,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise ValueError(f"source candidate checker failed: {detail}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"source candidate checker output is not JSON: {error}") from error
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("source candidate checker output has unknown or missing fields")
    if value.get("schema_version") != 1:
        raise ValueError("source candidate checker output is not schema 1")
    observed_checker = value.get("checker_sha256")
    if observed_checker != checker_digest(checker):
        raise ValueError("source candidate checker reported a different executable digest")
    for field in (
        "source_bundle_identity",
        "source_candidate_identity",
        "parent_program_identity",
        "checker_sha256",
    ):
        if DIGEST.fullmatch(value.get(field, "")) is None:
            raise ValueError(f"source candidate checker output {field} is invalid")
    if "base_source_tree" in value and SOURCE_TREE.fullmatch(value.get("base_source_tree", "")) is None:
        raise ValueError("source candidate checker output base_source_tree is invalid")
    if "capture_checker_sha256" in value and DIGEST.fullmatch(value.get("capture_checker_sha256", "")) is None:
        raise ValueError("source candidate checker output capture_checker_sha256 is invalid")
    if "evaluated_pair" in value:
        pair = value["evaluated_pair"]
        if (
            not isinstance(pair, dict)
            or set(pair) != {"source_tree", "runtime_binary"}
            or SOURCE_TREE.fullmatch(pair.get("source_tree", "")) is None
            or DIGEST.fullmatch(pair.get("runtime_binary", "")) is None
        ):
            raise ValueError("source candidate checker output evaluated_pair is invalid")
    if "provenance" in value and value["provenance"] != (
        "source and binary digests computed and recorded as one evaluated pair"
    ):
        raise ValueError("source candidate checker output provenance is invalid")
    return value


CAPTURE_FIELDS = {
    "schema_version",
    "source_bundle_identity",
    "source_candidate_identity",
    "base_source_tree",
    "parent_program_identity",
    "checker_sha256",
}

PREFLIGHT_FIELDS = {
    "schema_version",
    "source_bundle_identity",
    "source_candidate_identity",
    "base_source_tree",
    "parent_program_identity",
    "capture_checker_sha256",
    "checker_sha256",
    "evaluated_pair",
    "provenance",
}

ADOPTION_FIELDS = {
    "schema_version",
    "source_bundle_identity",
    "source_candidate_identity",
    "adoption_identity",
    "evidence_identity",
    "program_identity",
    "state_identity",
    "parent_program_identity",
    "parent_state_identity",
    "checker_sha256",
    "evaluated_pair",
    "plan_identity",
    "launched_program_verified",
    "lineage_directory",
}


def capture_source_candidate(
    checker: Path,
    bundle: Path,
    candidate: Path,
    base_source_tree: str,
    parent_document: str,
    proposal_log: str,
    verification_log: str,
    verification_seq: int,
) -> dict[str, Any]:
    return checked_output(
        checker,
        [
            "capture",
            str(bundle),
            str(candidate),
            base_source_tree,
            parent_document,
            proposal_log,
            verification_log,
            str(verification_seq),
        ],
        CAPTURE_FIELDS,
    )


def verify_source_candidate(
    checker: Path,
    adoption_path: Path,
    source_root: Path,
    applied_source_tree: str,
    runtime_binary: Path,
) -> dict[str, Any]:
    repository = source_root if source_root.is_dir() else source_root.parent
    bundle, expected = source_bundle_path(adoption_path)
    value = checked_output(
        checker,
        [
            "preflight",
            str(bundle),
            str(repository),
            applied_source_tree,
            str(runtime_binary),
        ],
        PREFLIGHT_FIELDS,
    )
    if expected is not None:
        for field, retained in expected.items():
            if value[field] != retained:
                raise ValueError(
                    f"source candidate record {field} does not match its evidence bundle"
                )
    return value


def freeze_source_candidate(
    checker: Path,
    adoption_path: Path,
    source_root: Path,
    applied_source_tree: str,
    runtime_binary: Path,
    destination: Path,
    expected_preflight: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    """Copy validated evidence into a campaign and validate the copy."""
    expected = verify_source_candidate(
        checker, adoption_path, source_root, applied_source_tree, runtime_binary
    )
    if expected != expected_preflight:
        raise ValueError("source evidence changed after campaign preflight")
    source, _ = source_bundle_path(adoption_path)
    if destination.exists():
        raise ValueError(f"frozen source evidence already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.copying")
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(source, temporary, symlinks=True)
    try:
        observed = verify_source_candidate(
            checker, temporary, source_root, applied_source_tree, runtime_binary
        )
    except (OSError, ValueError):
        shutil.rmtree(temporary)
        raise
    if observed != expected:
        shutil.rmtree(temporary)
        raise ValueError("source evidence changed while it was copied into the campaign")
    temporary.rename(destination)
    return destination, observed


def complete_source_adoption(
    checker: Path,
    adoption_path: Path,
    source_root: Path,
    applied_source_tree: str,
    runtime_binary: Path,
    plan: Path,
    episode: Path,
    lineage: Path,
    expected_preflight: dict[str, Any],
) -> dict[str, Any]:
    repository = source_root if source_root.is_dir() else source_root.parent
    value = checked_output(
        checker,
        [
            "adopt",
            str(source_bundle_path(adoption_path)[0]),
            str(repository),
            applied_source_tree,
            str(runtime_binary),
            str(plan),
            str(episode),
            str(lineage),
        ],
        ADOPTION_FIELDS,
    )
    if value.get("launched_program_verified") is not True:
        raise ValueError("source adoption did not verify the launched program")
    if not isinstance(value.get("lineage_directory"), str) or not value["lineage_directory"]:
        raise ValueError("source adoption lineage_directory is invalid")
    for field in (
        "adoption_identity",
        "evidence_identity",
        "program_identity",
        "state_identity",
        "parent_state_identity",
        "plan_identity",
    ):
        if DIGEST.fullmatch(value.get(field, "")) is None:
            raise ValueError(f"source adoption {field} is invalid")
    for field in (
        "source_bundle_identity",
        "source_candidate_identity",
        "parent_program_identity",
        "checker_sha256",
        "evaluated_pair",
    ):
        if value[field] != expected_preflight[field]:
            raise ValueError(f"source adoption {field} differs from the frozen preflight")
    return value
