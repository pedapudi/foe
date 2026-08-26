#!/usr/bin/python3
"""Verify a source-change lineage bundle before Terminal-Bench evaluation."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
SOURCE_TREE = re.compile(r"git-tree-(sha1|sha256):([0-9a-f]+)")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def manifest_path(label: str, value: Any) -> str:
    if not isinstance(value, str) or "\\" in value or any(
        part in ("", ".", "..") for part in value.split("/")
    ):
        raise ValueError(f"{label} is not a relative manifest path")
    return value


def canonical_object(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    data = path.read_bytes()
    value = json.loads(data)
    if not isinstance(value, dict) or canonical_json(value) != data:
        raise ValueError(f"{label} is not one canonical JSON object")
    return value, data


def source_bundle_path(path: Path) -> tuple[Path, dict[str, Any] | None, Path | None]:
    """Resolve either an evidence bundle or its retained self-improvement result."""
    if path.is_dir():
        return path, None, None
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("source adoption record is not a JSON object")
    adoption = record.get("adoption")
    bundle = adoption.get("evidence_directory") if isinstance(adoption, dict) else None
    if not isinstance(bundle, str) or not bundle:
        raise ValueError("source adoption record has no evidence bundle")
    bundle_path = Path(bundle)
    if not bundle_path.is_absolute():
        bundle_path = path.parent / bundle_path
    state = adoption.get("state")
    if not isinstance(state, str) or not state:
        raise ValueError("source adoption record has no child state document")
    state_path = Path(state)
    if not state_path.is_absolute():
        state_path = path.parent / state_path
    return bundle_path.resolve(strict=True), record, state_path.resolve(strict=True)


def require_checked_ancestry(
    bundle: Path,
    state: Path | None,
    evidence_identity: str,
    ancestry_checker: Path,
) -> None:
    """Require the canonical lineage checker to accept the retained transition."""
    lineage = bundle.parent.parent
    states = (lineage / "states").resolve(strict=True)
    evidence = (lineage / "evidence").resolve(strict=True)
    expected_bundle = evidence / evidence_identity.removeprefix("sha256:")
    if bundle.resolve() != expected_bundle.resolve():
        raise ValueError("source adoption bundle is outside its content-addressed lineage layout")
    if state is None:
        matches = []
        for candidate in states.glob("*.json"):
            value = json.loads(candidate.read_text(encoding="utf-8"))
            claim = value.get("program_lineage") if isinstance(value, dict) else None
            if isinstance(claim, dict) and claim.get("evidence") == evidence_identity:
                matches.append(candidate)
        if len(matches) != 1:
            raise ValueError("source adoption bundle does not have one child state document")
        state = matches[0]
    if state.parent.resolve() != states:
        raise ValueError("source adoption child state is outside the lineage state directory")
    checked = subprocess.run(
        [str(ancestry_checker), str(state), str(states), str(evidence)],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if checked.returncode != 0:
        detail = checked.stderr.strip() or checked.stdout.strip() or f"exit status {checked.returncode}"
        raise ValueError(f"source adoption ancestry check failed: {detail}")


def verified_bundle(path: Path) -> tuple[dict[str, Any], dict[str, bytes], str]:
    """Verify the lineage manifest and return its retained bytes by path."""
    manifest, manifest_bytes = canonical_object(path / "manifest.json", "lineage manifest")
    if set(manifest) != {"schema_version", "files", "proposal_log", "adoption_record"}:
        raise ValueError("lineage manifest has unknown or missing fields")
    if manifest["schema_version"] != 1 or not isinstance(manifest["files"], list):
        raise ValueError("lineage manifest is not schema 1")
    retained = {}
    previous = None
    for index, entry in enumerate(manifest["files"]):
        if not isinstance(entry, dict) or set(entry) != {"path", "bytes", "sha256"}:
            raise ValueError(f"lineage manifest file {index} has an invalid shape")
        name = manifest_path(f"lineage manifest file {index}", entry["path"])
        if previous is not None and name <= previous:
            raise ValueError("lineage manifest file paths are not in byte order")
        data = (path / name).read_bytes()
        if entry["bytes"] != len(data) or entry["sha256"] != digest_bytes(data):
            raise ValueError(f"lineage manifest file does not match retained bytes: {name}")
        retained[name] = data
        previous = name
    for field in ("proposal_log", "adoption_record"):
        if manifest_path(f"lineage manifest {field}", manifest[field]) not in retained:
            raise ValueError(f"lineage manifest {field} does not name a retained file")
    return manifest, retained, digest_bytes(manifest_bytes)


def git_output(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(root), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ValueError(f"cannot verify adopted source: git {' '.join(arguments)}: {detail}")
    return result.stdout


def changed_source(root: Path, base_source_tree: str, applied_source_tree: str) -> dict[str, str]:
    """Return changed-file digests between the adopted base and clean candidate tree."""
    base_match = SOURCE_TREE.fullmatch(base_source_tree)
    applied_match = SOURCE_TREE.fullmatch(applied_source_tree)
    if base_match is None or applied_match is None or base_match.group(1) != applied_match.group(1):
        raise ValueError("source adoption and evaluated source use incompatible Git object identities")
    names = git_output(
        root,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        base_match.group(2),
        applied_match.group(2),
    ).split("\0")
    answer = {}
    for name in sorted(item for item in names if item):
        manifest_path("adopted changed path", name)
        item = root / name
        answer[name] = digest_bytes(item.read_bytes()) if item.is_file() else "absent"
    return answer


def verify_source_adoption(
    adoption_path: Path,
    source_root: Path,
    applied_source_tree: str,
    runtime_binary: str,
    ancestry_checker: Path,
) -> dict[str, Any]:
    """Verify an adopted patch and bind it to the evaluated source and binary."""
    bundle, result_record, state = source_bundle_path(adoption_path)
    manifest, retained, evidence_identity = verified_bundle(bundle)
    require_checked_ancestry(bundle, state, evidence_identity, ancestry_checker)
    record_path = manifest["adoption_record"]
    adoption_record = json.loads(retained[record_path])
    if not isinstance(adoption_record, dict) or canonical_json(adoption_record) != retained[record_path]:
        raise ValueError("lineage adoption record is not one canonical JSON object")
    required = {
        "schema_version",
        "program_identity",
        "identity_document_sha256",
        "artifact_manifest_sha256",
        "verification_log",
        "verification_seq",
    }
    if set(adoption_record) != required or adoption_record["schema_version"] != 1:
        raise ValueError("lineage adoption record is not schema 1")
    by_digest = {digest_bytes(data): name for name, data in retained.items()}
    identity_name = by_digest.get(adoption_record["identity_document_sha256"])
    artifact_name = by_digest.get(adoption_record["artifact_manifest_sha256"])
    if identity_name is None or artifact_name is None:
        raise ValueError("lineage adoption record does not name retained identity and artifact files")
    identity_document = json.loads(retained[identity_name])
    if canonical_json(identity_document) != retained[identity_name]:
        raise ValueError("lineage child identity document is not one canonical JSON object")
    if digest_bytes(canonical_json(identity_document)) != adoption_record["program_identity"]:
        raise ValueError("lineage adoption program identity does not match the child identity document")
    artifact = json.loads(retained[artifact_name])
    if not isinstance(artifact, dict) or canonical_json(artifact) != retained[artifact_name]:
        raise ValueError("source artifact manifest is not one canonical JSON object")
    if set(artifact) != {"schema_version", "candidate_identity", "base_source_tree", "files"}:
        raise ValueError("source artifact manifest has unknown or missing fields")
    if artifact["schema_version"] != 1 or not isinstance(artifact["files"], list):
        raise ValueError("source artifact manifest is not schema 1")
    files = {}
    for index, entry in enumerate(artifact["files"]):
        if not isinstance(entry, dict) or set(entry) not in ({"path", "sha256"}, {"path", "sha256", "content"}):
            raise ValueError(f"source artifact file {index} has an invalid shape")
        name = manifest_path(f"source artifact file {index}", entry.get("path"))
        sha256 = entry.get("sha256")
        if name in files:
            raise ValueError(f"source artifact repeats changed path: {name}")
        if sha256 == "absent":
            if "content" in entry:
                raise ValueError(f"deleted source artifact has retained content: {name}")
        elif not isinstance(sha256, str) or DIGEST.fullmatch(sha256) is None:
            raise ValueError(f"source artifact file has an invalid digest: {name}")
        else:
            content = manifest_path(f"source artifact content {index}", entry.get("content"))
            if retained.get(content) is None or digest_bytes(retained[content]) != sha256:
                raise ValueError(f"source artifact content does not match changed file: {name}")
        files[name] = sha256
    candidate_body = {"base_source_tree": artifact["base_source_tree"], "files": files}
    if artifact["candidate_identity"] != digest_bytes(canonical_json(candidate_body)):
        raise ValueError("source artifact candidate identity does not match its changed files")
    runtime = identity_document.get("runtime") if isinstance(identity_document, dict) else None
    if runtime != {"source_tree": artifact["base_source_tree"], "files": files}:
        raise ValueError("source artifact manifest differs from the child identity document")
    root = source_root if source_root.is_dir() else source_root.parent
    if changed_source(root, artifact["base_source_tree"], applied_source_tree) != files:
        raise ValueError("clean candidate tree differs from the adopted changed-file digests")
    if DIGEST.fullmatch(runtime_binary) is None:
        raise ValueError("evaluated runtime binary identity is invalid")
    adoption_identity = digest_bytes(retained[record_path])
    if result_record is not None:
        acceptance = result_record.get("candidate_acceptance")
        result_adoption = result_record.get("adoption")
        if result_record.get("candidate_kind") != "source-change" or not isinstance(acceptance, dict) or acceptance.get("accepted") is not True:
            raise ValueError("source adoption record does not contain an accepted source candidate")
        if not isinstance(result_adoption, dict) or result_adoption.get("evidence") != evidence_identity:
            raise ValueError("source adoption record names a different lineage evidence bundle")
        result_artifact = result_record.get("candidate_artifact")
        if not isinstance(result_artifact, dict) or result_artifact.get("digest") != artifact["candidate_identity"]:
            raise ValueError("source adoption record names a different candidate identity")
    return {
        "adoption_identity": adoption_identity,
        "evidence_identity": evidence_identity,
        "candidate_identity": artifact["candidate_identity"],
        "program_identity": adoption_record["program_identity"],
        "base_source_tree": artifact["base_source_tree"],
        "evaluated_foe": {
            "source_tree": applied_source_tree,
            "runtime_binary": runtime_binary,
        },
    }
