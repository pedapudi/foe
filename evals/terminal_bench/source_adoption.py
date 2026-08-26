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
PROTECTED_BUILD_NAMES = (
    ".bazelignore",
    ".bazelrc",
    ".bazelversion",
    "BUILD",
    "BUILD.bazel",
    "Cargo.lock",
    "Cargo.toml",
    "MODULE.bazel",
    "MODULE.bazel.lock",
    "WORKSPACE",
    "WORKSPACE.bazel",
    "build.rs",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "REPO.bazel",
    "rust-toolchain",
    "rust-toolchain.toml",
)


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def retain_parent_executables(bundle: Path, program: dict[str, Any]) -> None:
    """Retain every configured executable needed to recompute program identity."""
    paths: set[str] = set()

    def visit_program(value: dict[str, Any]) -> None:
        for definition in value.get("tool_defs", {}).values():
            if isinstance(definition, dict) and isinstance(definition.get("exec"), str):
                paths.add(definition["exec"])
        for child in value.get("programs", {}).values():
            if isinstance(child, dict):
                visit_program(child)
        workflow = value.get("workflow")
        if isinstance(workflow, dict):
            visit_workflow(workflow)

    def visit_workflow(value: dict[str, Any]) -> None:
        for node in value.get("nodes", {}).values():
            if not isinstance(node, dict):
                continue
            if isinstance(node.get("model"), dict):
                visit_program(node["model"])
            if isinstance(node.get("workflow"), dict):
                visit_workflow(node["workflow"])

    visit_program(program)
    destination = bundle / "parent-executables"
    for value in sorted(paths):
        source = Path(value).resolve(strict=True)
        if not source.is_file():
            raise ValueError(f"parent plan configured executable is not a file: {source}")
        destination.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
        shutil.copyfile(source, destination / name)


def build_graph(candidate: Path) -> dict[str, str]:
    """Identify the protected files that define the candidate build."""
    listed = subprocess.run(
        ["/usr/bin/git", "ls-files", "--cached", "-z"],
        cwd=candidate,
        capture_output=True,
        check=True,
    ).stdout
    files = {}
    for value in listed.split(b"\0"):
        if not value:
            continue
        name = value.decode("utf-8")
        base = name.rsplit("/", 1)[-1]
        if base not in PROTECTED_BUILD_NAMES and not base.endswith(".bzl"):
            continue
        path = candidate / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"protected build metadata is not a regular file: {name}")
        files[name] = file_digest(path)
    if not files:
        raise ValueError("candidate source contains no protected build metadata")
    return files


def clean_source_tree(candidate: Path) -> str:
    """Identify a committed candidate tree whose checkout has no source changes."""
    def git(*arguments: str) -> str:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(candidate), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
            raise ValueError(f"cannot identify candidate source: git {' '.join(arguments)}: {detail}")
        return result.stdout.strip()

    status = git("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise ValueError(f"candidate source changed during controller build:\n{status}")
    algorithm = git("rev-parse", "--show-object-format")
    tree = git("rev-parse", "HEAD^{tree}")
    return f"git-tree-{algorithm}:{tree}"


def build_source_candidate(
    bazel: Path,
    candidate: Path,
    source_tree: str,
    destination: Path,
) -> tuple[Path, dict[str, Any]]:
    """Build and retain the candidate binary under a recorded trusted command."""
    bazel = bazel.resolve(strict=True)
    if not bazel.is_file():
        raise ValueError(f"controller Bazel executable is not a file: {bazel}")
    if clean_source_tree(candidate) != source_tree:
        raise ValueError("controller build source differs from the accepted candidate tree")
    before = build_graph(candidate)
    graph_bytes = json.dumps(before, sort_keys=True, separators=(",", ":")).encode()
    command = [str(bazel), "build", "--color=no", "--noshow_progress", "//:foe-portable"]
    version = subprocess.run(
        [str(bazel), "--version"],
        cwd=candidate,
        text=True,
        capture_output=True,
        check=False,
    )
    if version.returncode != 0:
        detail = version.stderr.strip() or version.stdout.strip() or f"exit status {version.returncode}"
        raise ValueError(f"controller Bazel version failed: {detail}")
    destination.mkdir(parents=True, exist_ok=False)
    log = destination / "build.log"
    try:
        result = subprocess.run(
            command,
            cwd=candidate,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=3_600,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        log.write_bytes(error.stdout or b"")
        raise ValueError(f"controller build timed out; partial log: {log}") from error
    log.write_bytes(result.stdout)
    if result.returncode != 0:
        raise ValueError(f"controller build failed with exit status {result.returncode}; log: {log}")
    if build_graph(candidate) != before or clean_source_tree(candidate) != source_tree:
        raise ValueError("controller build changed the accepted source or protected build metadata")
    output = candidate / "bazel-bin/foe-portable"
    if not output.is_file():
        raise ValueError(f"controller build produced no portable Foe binary: {output}")
    retained = destination / "foe-portable"
    shutil.copyfile(output, retained)
    retained.chmod(0o555)
    record = {
        "schema_version": 1,
        "source_tree": source_tree,
        "command": command,
        "bazel": {
            "path": str(bazel),
            "sha256": file_digest(bazel),
            "version": version.stdout.strip(),
        },
        "protected_build_graph": {
            "sha256": "sha256:" + hashlib.sha256(graph_bytes).hexdigest(),
            "files": before,
        },
        "log": {
            "path": "build.log",
            "bytes": len(result.stdout),
            "sha256": file_digest(log),
        },
        "output": {
            "path": "foe-portable",
            "bytes": retained.stat().st_size,
            "sha256": file_digest(retained),
        },
    }
    (destination / "build.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return retained, record


def source_bundle_path(path: Path) -> tuple[Path, dict[str, str] | None]:
    """Resolve source evidence and retain every identity a result recorded."""
    if path.is_dir():
        return path.absolute(), None
    record = json.loads(path.read_text(encoding="utf-8"))
    candidate = record.get("source_candidate") if isinstance(record, dict) else None
    bundle = candidate.get("bundle") if isinstance(candidate, dict) else None
    if not isinstance(bundle, str) or not bundle:
        raise ValueError("source candidate record has no source evidence bundle")
    if "checker_sha256" in candidate:
        raise ValueError("source candidate record cannot claim an unauthenticated capture checker")
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
    if "checker_sha256" in fields and observed_checker != file_digest(checker):
        raise ValueError("source candidate checker reported a different executable digest")
    for field in (
        "source_bundle_identity",
        "source_candidate_identity",
        "parent_program_identity",
        "checker_sha256",
    ):
        if field in fields and DIGEST.fullmatch(value.get(field, "")) is None:
            raise ValueError(f"source candidate checker output {field} is invalid")
    if "base_source_tree" in value and SOURCE_TREE.fullmatch(value.get("base_source_tree", "")) is None:
        raise ValueError("source candidate checker output base_source_tree is invalid")
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
        "source and binary digests computed independently; no build attestation"
    ):
        raise ValueError("source candidate checker output provenance is invalid")
    return value


CAPTURE_FIELDS = {
    "schema_version",
    "source_bundle_identity",
    "source_candidate_identity",
    "base_source_tree",
    "parent_program_identity",
}

PREFLIGHT_FIELDS = {
    "schema_version",
    "source_bundle_identity",
    "source_candidate_identity",
    "base_source_tree",
    "parent_program_identity",
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
    parent_plan: str,
    proposal_log: str,
    verification_log: str,
    verification_seq: int,
    verifier_executable: str,
) -> dict[str, Any]:
    return checked_output(
        checker,
        [
            "capture",
            str(bundle),
            str(candidate),
            base_source_tree,
            parent_plan,
            proposal_log,
            verification_log,
            str(verification_seq),
            verifier_executable,
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
