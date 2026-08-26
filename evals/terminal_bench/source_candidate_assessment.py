#!/usr/bin/python3
"""Build private source-candidate assessments and bounded model diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from source_adoption import DIGEST, SOURCE_TREE
from trajectory_diagnostics import (
    MAX_FAILURE_ASSERTION,
    MAX_FAILURE_LOCATION,
    MAX_FAILURE_MESSAGE,
    MAX_VERIFICATION_RESULTS,
    VOLATILE_PATH,
    require_confined_regular_file,
    verifier_feedback,
    verifier_feedback_from_bytes,
)


ASSESSMENT_SCHEMA_VERSION = 1
DIAGNOSTICS_SCHEMA_VERSION = 1
MAX_DIAGNOSTICS_BYTES = 48 * 1024
MAX_ASSESSMENT_FAILURES = 12
MAX_ASSESSMENT_SUCCESSES_PER_ROLE = 12
MAX_FINAL_VALIDATION_TIMELINES = 24
SOURCE_MANIFEST = "source-candidate-manifest.json"
ASSESSMENT_DIAGNOSTICS_FILE = "candidate-assessment-diagnostics.json"
GENERATION_CONTEXT_FILE = "candidate-generation-context.json"
FORBIDDEN_DIAGNOSTIC_KEYS = {
    "campaign",
    "campaign_label",
    "dataset",
    "label",
    "reward",
    "rewards",
    "task",
    "task_checksum",
    "task_name",
    "task_text",
}
RELATIVE_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\x00]+$")
GIT_BLOB = re.compile(r"^git-blob-(?:sha1:[0-9a-f]{40}|sha256:[0-9a-f]{64})$")
OUTCOME_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
BOUNDED_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
ABSOLUTE_ARTIFACT_PATH = re.compile(
    r"(?:^|[\s'\"(])/(?:[A-Za-z0-9._-]+)(?:/[A-Za-z0-9._-]+)*"
)


def canonical_json(value: Any) -> bytes:
    """Return the canonical UTF-8 representation used for assessment identities."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def bytes_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def with_identity(value: dict[str, Any], field: str) -> dict[str, Any]:
    if field in value:
        raise ValueError(f"{field} is reserved for the canonical identity")
    return {**value, field: digest(value)}


def require_exact_fields(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} has unknown or missing fields")
    return value


def require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} is not a canonical SHA-256 identity")
    return value


def require_source_tree(value: Any, label: str) -> str:
    if not isinstance(value, str) or SOURCE_TREE.fullmatch(value) is None:
        raise ValueError(f"{label} is not a Git source-tree identity")
    return value


def require_reference(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is not a non-empty reference")
    return value


def require_bounded_reference(value: Any, label: str) -> str:
    if not isinstance(value, str) or BOUNDED_REFERENCE.fullmatch(value) is None:
        raise ValueError(f"{label} is not a bounded identity reference")
    return value


def require_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or RELATIVE_PATH.fullmatch(value) is None:
        raise ValueError(f"{label} is not a confined relative path")
    first = value.split("/", 1)[0]
    if first in (".", "..") or Path(value).is_absolute():
        raise ValueError(f"{label} is not a confined relative path")
    return value


def require_git_object(value: Any, label: str) -> dict[str, str]:
    object_value = require_exact_fields(
        value, {"object_type", "mode", "identity"}, label
    )
    if (
        object_value["object_type"] != "blob"
        or object_value["mode"] not in ("100644", "100755")
        or not isinstance(object_value["identity"], str)
        or GIT_BLOB.fullmatch(object_value["identity"]) is None
    ):
        raise ValueError(f"{label} is not a regular Git blob identity")
    return object_value


def validate_source_entries(base_tree: str, entries: Any) -> dict[str, dict[str, Any]]:
    """Validate exact source-manifest entries and return present entries by path."""
    require_source_tree(base_tree, "source patch base tree")
    if not isinstance(entries, list) or not entries:
        raise ValueError("source candidate manifest has no source entries")
    blob_prefix = "git-blob-" + base_tree.removeprefix("git-tree-").split(":", 1)[0] + ":"
    paths = []
    present = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"source entry {index} is not an object")
        status = entry.get("status")
        expected = (
            {"status", "path", "base"}
            if status == "deleted"
            else {"status", "path", "applied", "sha256", "content"}
        )
        if status == "present" and "base" in entry:
            expected.add("base")
        if status not in ("present", "deleted") or set(entry) != expected:
            raise ValueError(f"source entry {index} has an invalid shape")
        source_path = require_relative_path(entry["path"], f"source entry {index} path")
        paths.append(source_path)
        if entry.get("base") is not None:
            base_object = require_git_object(
                entry["base"], f"source entry {index} base object"
            )
            if not base_object["identity"].startswith(blob_prefix):
                raise ValueError(f"source entry {index} base object uses another Git format")
        if status == "present":
            applied_object = require_git_object(
                entry["applied"], f"source entry {index} applied object"
            )
            if not applied_object["identity"].startswith(blob_prefix):
                raise ValueError(f"source entry {index} applied object uses another Git format")
            require_digest(entry["sha256"], f"source entry {index} content digest")
            require_relative_path(entry["content"], f"source entry {index} content")
            present[source_path] = entry
    if paths != sorted(set(paths)):
        raise ValueError("source candidate entries are not unique and ordered by path")
    return present


def read_json_object(path: Path, root: Path, label: str) -> tuple[dict[str, Any], str]:
    path = require_confined_regular_file(path, root, label)
    encoded = path.read_text(encoding="utf-8")
    value = json.loads(encoded)
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not one JSON object")
    return value, encoded


def require_real_directory(path: Path, label: str) -> Path:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"{label} must not contain a symbolic-link ancestor")
    if not absolute.is_dir():
        raise ValueError(f"{label} must be a real directory")
    return absolute.resolve(strict=True)


def validate_source_manifest_shape(
    manifest: Any,
) -> tuple[str, str, str, list[dict[str, Any]]]:
    manifest = require_exact_fields(
        manifest,
        {
            "schema_version",
            "candidate_identity",
            "base_source_tree",
            "entries",
            "parent_plan",
            "parent_program_identity",
            "proposal_log",
            "verification_log",
            "verification_seq",
            "verification_tool",
            "verification_executable",
            "verification_executable_sha256",
            "files",
        },
        "source candidate manifest",
    )
    if manifest["schema_version"] != 1:
        raise ValueError("source candidate manifest is not schema 1")
    base_tree = require_source_tree(manifest["base_source_tree"], "base source tree")
    candidate_identity = require_digest(
        manifest["candidate_identity"], "source candidate identity"
    )
    parent_program_identity = require_digest(
        manifest["parent_program_identity"], "parent program identity"
    )
    validate_source_entries(base_tree, manifest["entries"])
    for field in (
        "parent_plan",
        "proposal_log",
        "verification_log",
        "verification_executable",
    ):
        require_relative_path(manifest[field], f"source manifest {field}")
    require_digest(
        manifest["verification_executable_sha256"],
        "source candidate verifier identity",
    )
    if (
        type(manifest["verification_seq"]) is not int
        or manifest["verification_seq"] < 0
        or not isinstance(manifest["verification_tool"], str)
        or not manifest["verification_tool"]
    ):
        raise ValueError("source candidate verification record is invalid")
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise ValueError("source candidate manifest has no retained file inventory")
    paths = []
    for index, item in enumerate(files):
        require_exact_fields(item, {"path", "bytes", "sha256"}, f"manifest file {index}")
        paths.append(require_relative_path(item["path"], f"manifest file {index} path"))
        if type(item["bytes"]) is not int or item["bytes"] < 0:
            raise ValueError(f"manifest file {index} has an invalid byte count")
        require_digest(item["sha256"], f"manifest file {index} identity")
    if paths != sorted(set(paths)):
        raise ValueError("source manifest files are not unique and ordered by path")
    if any(
        manifest[field] not in paths
        for field in (
            "parent_plan",
            "proposal_log",
            "verification_log",
            "verification_executable",
        )
    ):
        raise ValueError("source manifest omits a required retained file")
    if digest(
        {"base_source_tree": base_tree, "entries": manifest["entries"]}
    ) != candidate_identity:
        raise ValueError("source candidate identity conflicts with its exact source entries")
    return base_tree, candidate_identity, parent_program_identity, files


def source_bundle_facts(bundle: Path) -> dict[str, Any]:
    """Validate retained source bytes and return the identity-bound proposal facts."""
    bundle = require_real_directory(bundle, "source evidence bundle")
    manifest_path = require_confined_regular_file(
        bundle / SOURCE_MANIFEST, bundle, "source candidate manifest"
    )
    encoded = manifest_path.read_bytes()
    manifest = json.loads(encoded)
    if not isinstance(manifest, dict) or canonical_json(manifest) != encoded:
        raise ValueError("source candidate manifest is not canonical JSON")
    base_tree, candidate_identity, parent_program_identity, files = (
        validate_source_manifest_shape(manifest)
    )
    entries = manifest["entries"]
    contents = []
    for index, entry in enumerate(entries):
        if entry["status"] == "present":
            source_path = entry["path"]
            content_path = require_relative_path(
                entry["content"], f"source entry {index} content"
            )
            content_file = require_confined_regular_file(
                bundle / content_path, bundle, f"source entry {index} content"
            )
            content = content_file.read_bytes()
            if bytes_digest(content) != entry["sha256"]:
                raise ValueError(f"source entry {index} content differs from its digest")
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError(
                    f"source entry {index} is not UTF-8 and cannot enter model diagnostics"
                ) from error
            contents.append(
                {
                    "path": source_path,
                    "mode": entry["applied"].get("mode")
                    if isinstance(entry["applied"], dict)
                    else None,
                    "sha256": entry["sha256"],
                    "content": text,
                }
            )
    observed_paths = []
    for index, item in enumerate(files):
        relative = require_relative_path(item["path"], f"manifest file {index} path")
        retained = require_confined_regular_file(
            bundle / relative, bundle, f"manifest file {index}"
        )
        retained_bytes = retained.read_bytes()
        if (
            type(item["bytes"]) is not int
            or item["bytes"] != len(retained_bytes)
            or item["sha256"] != bytes_digest(retained_bytes)
        ):
            raise ValueError(f"manifest file {index} differs from its retained bytes")
        observed_paths.append(relative)
    verifier = require_confined_regular_file(
        bundle / manifest["verification_executable"],
        bundle,
        "source candidate verifier",
    )
    if bytes_digest(verifier.read_bytes()) != manifest["verification_executable_sha256"]:
        raise ValueError("source candidate verifier conflicts with its manifest digest")
    if type(manifest["verification_seq"]) is not int or manifest["verification_seq"] < 0:
        raise ValueError("source candidate verification sequence is invalid")
    retained_paths = []
    pending = [bundle]
    while pending:
        directory = pending.pop()
        for child in directory.iterdir():
            if child.is_symlink():
                raise ValueError(f"source evidence bundle contains a symbolic link: {child}")
            if child.is_dir():
                pending.append(child)
            elif child.is_file():
                retained_paths.append(child.relative_to(bundle).as_posix())
            else:
                raise ValueError(f"source evidence bundle contains an unsupported entry: {child}")
    if sorted(retained_paths) != sorted([SOURCE_MANIFEST, *observed_paths]):
        raise ValueError("source manifest does not bind every retained bundle file")
    parent_plan_path = require_relative_path(manifest["parent_plan"], "parent plan path")
    parent_plan, parent_plan_text = read_json_object(
        bundle / parent_plan_path, bundle, "parent plan"
    )
    if parent_plan.get("identity") != parent_program_identity:
        raise ValueError("parent plan identity conflicts with the source manifest")
    if not isinstance(parent_plan.get("task"), str) or not parent_plan["task"].strip():
        raise ValueError("parent plan has no source-generation task")
    proposal_log_path = require_relative_path(manifest["proposal_log"], "proposal log path")
    proposal_log_file = require_confined_regular_file(
        bundle / proposal_log_path, bundle, "proposal log"
    )
    proposal_log = proposal_log_file.read_text(encoding="utf-8")
    prior_diagnosis = prior_typed_diagnosis_from_text(proposal_log)
    return {
        "bundle": bundle,
        "manifest": manifest,
        "source_bundle_identity": bytes_digest(encoded),
        "source_candidate_identity": candidate_identity,
        "parent_source_tree": base_tree,
        "parent_program_identity": parent_program_identity,
        "source_patch": {"entries": entries, "contents": contents},
        "source_evidence": {
            "parent_plan": parent_plan_text,
            "proposal_log": proposal_log,
        },
        "prior_diagnosis": prior_diagnosis,
        "source_generation_task": parent_plan.get("task"),
    }


def prior_typed_diagnosis_from_text(value: str) -> dict[str, Any]:
    found = []
    for line_number, line in enumerate(value.splitlines(), 1):
        if not line:
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError(f"proposal log line {line_number} is not an object")
        data = event.get("data")
        if (
            event.get("type") == "workflow/node-end"
            and isinstance(data, dict)
            and data.get("node") == "diagnose-runtime"
            and isinstance(data.get("value"), dict)
        ):
            found.append(data["value"])
    if len(found) != 1:
        raise ValueError("source proposal records no unique prior typed diagnosis")
    if found[0].get("branch") != "implement-source":
        raise ValueError("prior typed diagnosis did not produce the assessed source candidate")
    return found[0]


def completed_campaign_jobs(
    campaign: Any, role: str
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Validate retained campaign completion and return its identity and jobs."""
    if not isinstance(campaign, dict) or campaign.get("schema_version") != 1:
        raise ValueError(f"{role} campaign is not a schema 1 object")
    if campaign.get("cancelled") is not False or campaign.get("stopped_reason") is not None:
        raise ValueError(f"{role} campaign is incomplete")
    identity = campaign.get("evaluated_foe")
    if not isinstance(identity, dict) or set(identity) != {"source_tree", "runtime_binary"}:
        raise ValueError(f"{role} campaign has no exact evaluated Foe identity")
    require_source_tree(identity["source_tree"], f"{role} source tree")
    require_digest(identity["runtime_binary"], f"{role} runtime binary")
    jobs = campaign.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError(f"{role} campaign has no jobs")
    for job_index, job in enumerate(jobs):
        if not isinstance(job, dict):
            raise ValueError(f"{role} job {job_index} is not an object")
        completed = job.get("n_completed_trials")
        total = job.get("n_total_trials")
        if (
            "result_error" in job
            or job.get("execution_status") != "started"
            or job.get("n_errored_trials") != 0
            or type(completed) is not int
            or type(total) is not int
            or total <= 0
            or completed != total
            or job.get("configuration_claim_valid") is not True
        ):
            raise ValueError(f"{role} job {job_index} is incomplete or errored")
    return identity, jobs


def retained_trial_task(
    diagnostic_path: Path,
    job_root: Path,
    diagnostic: dict[str, Any],
    role: str,
) -> str:
    """Return the private task from a retained plan or its bound root start."""
    plan_path = diagnostic_path.parent / "foe-plan.json"
    if plan_path.exists() or plan_path.is_symlink():
        plan_path = require_confined_regular_file(
            plan_path,
            job_root,
            f"{role} trial plan",
        )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        raw_task_text = plan.get("task") if isinstance(plan, dict) else None
    else:
        log_path = require_confined_regular_file(
            diagnostic_path.parent / "foe-episode" / "episode.jsonl",
            job_root,
            f"{role} trial root episode",
        )
        with log_path.open(encoding="utf-8") as stream:
            first_line = stream.readline()
        start = json.loads(first_line)
        data = start.get("data") if isinstance(start, dict) else None
        evidence = diagnostic.get("evidence_identity")
        runtime = data.get("runtime") if isinstance(data, dict) else None
        if (
            not isinstance(start, dict)
            or start.get("type") != "episode/start"
            or not isinstance(data, dict)
            or not isinstance(runtime, dict)
            or not isinstance(evidence, dict)
            or data.get("id") != evidence.get("episode_id")
            or data.get("identity") != evidence.get("program_identity")
            or runtime.get("build") != evidence.get("runtime_build")
        ):
            raise ValueError(
                f"{role} trial root episode conflicts with its diagnostics"
            )
        raw_task_text = data.get("task")
    if not isinstance(raw_task_text, str) or not raw_task_text.strip():
        raise ValueError(f"{role} trial evidence has no raw task text")
    return raw_task_text


def campaign_trials(path: Path, role: str) -> dict[str, Any]:
    """Read one completed external campaign and its private trial evidence."""
    path = path.absolute()
    root = require_real_directory(path if path.is_dir() else path.parent, f"{role} campaign root")
    campaign_path = path / "campaign.json" if path.is_dir() else path
    campaign_path = require_confined_regular_file(campaign_path, root, f"{role} campaign")
    campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    identity, jobs = completed_campaign_jobs(campaign, role)
    trials = []
    for job_index, job in enumerate(jobs):
        total = job.get("n_total_trials")
        result_relative = require_relative_path(job.get("result"), f"{role} job result")
        job_result = require_confined_regular_file(
            root / result_relative, root, f"{role} job result"
        )
        job_root = job_result.parent
        diagnostics = job.get("diagnostics")
        if (
            not isinstance(diagnostics, list)
            or len(diagnostics) != total
            or not all(isinstance(item, str) for item in diagnostics)
            or len(diagnostics) != len(set(diagnostics))
        ):
            raise ValueError(f"{role} job {job_index} has incomplete diagnostics")
        for diagnostic_relative in diagnostics:
            diagnostic_relative = require_relative_path(
                diagnostic_relative, f"{role} trial diagnostics"
            )
            diagnostic_path = require_confined_regular_file(
                job_root / diagnostic_relative, job_root, f"{role} trial diagnostics"
            )
            trial_path = diagnostic_path.parent.parent / "result.json"
            trial_path = require_confined_regular_file(
                trial_path, job_root, f"{role} trial result"
            )
            trial = json.loads(trial_path.read_text(encoding="utf-8"))
            diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            validate_trial(trial, diagnostic, identity, role)
            raw_task_text = retained_trial_task(
                diagnostic_path, job_root, diagnostic, role
            )
            report_path = require_confined_regular_file(
                trial_path.parent / "verifier" / "ctrf.json",
                job_root,
                f"{role} raw verifier report",
            )
            raw_verifier_report = report_path.read_bytes().decode("utf-8")
            if not isinstance(json.loads(raw_verifier_report), dict):
                raise ValueError(f"{role} raw verifier report is not an object")
            retained_feedback = verifier_feedback(trial_path, artifact_root=job_root)
            if retained_feedback != diagnostic.get("verifier_feedback"):
                raise ValueError(
                    f"{role} trial diagnostics conflict with the retained verifier report"
                )
            trials.append(
                {
                    "comparison_key": [trial.get("task_name"), trial.get("task_checksum")],
                    "raw_task_text": raw_task_text,
                    "raw_result": trial,
                    "raw_verifier_report": raw_verifier_report,
                    "diagnostics": diagnostic,
                }
            )
    expected = sum(job["n_total_trials"] for job in jobs)
    if len(trials) != expected:
        raise ValueError(f"{role} campaign has an incomplete retained trial set")
    episode_ids = [trial["diagnostics"]["evidence_identity"]["episode_id"] for trial in trials]
    if len(episode_ids) != len(set(episode_ids)):
        raise ValueError(f"{role} campaign repeats a trial episode identity")
    return {"campaign": campaign, "evaluated_foe": identity, "trials": trials}


def trial_reward(trial: dict[str, Any], role: str) -> float | int:
    verifier = trial.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    if type(reward) not in (int, float) or not math.isfinite(reward):
        raise ValueError(f"{role} trial reward must be a finite number and cannot be boolean")
    return reward


def validate_trial(
    trial: Any, diagnostic: Any, identity: dict[str, str], role: str
) -> None:
    if not isinstance(trial, dict) or not isinstance(diagnostic, dict):
        raise ValueError(f"{role} trial evidence is not an object")
    if trial.get("exception_info") is not None:
        raise ValueError(f"{role} trial records an exception")
    reward = trial_reward(trial, role)
    for field in ("task_name", "task_checksum"):
        require_reference(trial.get(field), f"{role} trial {field}")
    if (
        diagnostic.get("task") != trial.get("task_name")
        or diagnostic.get("evidence_identity", {}).get("task_checksum")
        != trial.get("task_checksum")
        or diagnostic.get("verifier_reward") != reward
        or diagnostic.get("trial_error") is not None
    ):
        raise ValueError(f"{role} trial diagnostics conflict with its retained result")
    metadata = trial.get("agent_result")
    metadata = metadata.get("metadata") if isinstance(metadata, dict) else None
    outcome = metadata.get("foe_outcome") if isinstance(metadata, dict) else None
    if (
        not isinstance(metadata, dict)
        or metadata.get("foe_trace_conformant") is not True
        or not isinstance(outcome, dict)
        or outcome.get("kind") not in ("completed", "blocked", "exhausted")
        or diagnostic.get("outcome") != outcome
    ):
        raise ValueError(f"{role} trial is incomplete, failed, or nonconformant")
    evidence_identity = diagnostic.get("evidence_identity")
    if not isinstance(evidence_identity, dict):
        raise ValueError(f"{role} trial diagnostics have no evidence identity")
    for field in ("program_identity", "runtime_build"):
        require_digest(evidence_identity.get(field), f"{role} trial {field}")
    require_reference(evidence_identity.get("episode_id"), f"{role} trial episode_id")
    if evidence_identity["runtime_build"] != identity["runtime_binary"]:
        raise ValueError(f"{role} trial runtime identity conflicts with its campaign")
    feedback = diagnostic.get("verifier_feedback")
    if not isinstance(feedback, dict):
        raise ValueError(f"{role} trial has no structured verifier report")
    require_digest(feedback.get("sha256"), f"{role} verifier report")
    if reward == 1:
        counts = feedback.get("failure_evidence_counts")
        if not isinstance(counts, dict) or counts.get("total_failed_tests") != 0:
            raise ValueError(f"{role} successful trial conflicts with verifier failures")


def source_campaign_identity(
    candidate: dict[str, Any], bundle: dict[str, Any]
) -> str:
    retained = candidate["campaign"].get("source_candidate")
    if not isinstance(retained, dict):
        raise ValueError("candidate campaign has no source-candidate preflight")
    expected = {
        "source_bundle_identity": bundle["source_bundle_identity"],
        "source_candidate_identity": bundle["source_candidate_identity"],
        "base_source_tree": bundle["parent_source_tree"],
        "parent_program_identity": bundle["parent_program_identity"],
    }
    for field, value in expected.items():
        if retained.get(field) != value:
            raise ValueError(f"candidate campaign {field} conflicts with the source bundle")
    pair = retained.get("evaluated_pair")
    if pair != candidate["evaluated_foe"]:
        raise ValueError("candidate campaign evaluated pair conflicts with its source preflight")
    adoptions = candidate["campaign"].get("source_adoptions")
    if not isinstance(adoptions, list) or len(adoptions) != len(candidate["trials"]):
        raise ValueError("candidate campaign has incomplete source adoptions")
    adopted_programs = []
    for adoption in adoptions:
        if not isinstance(adoption, dict):
            raise ValueError("candidate campaign has an invalid source adoption")
        for field in (
            "source_bundle_identity",
            "source_candidate_identity",
            "parent_program_identity",
            "evaluated_pair",
        ):
            if adoption.get(field) != retained.get(field):
                raise ValueError(f"candidate source adoption conflicts on {field}")
        adopted_programs.append(
            require_digest(adoption.get("program_identity"), "candidate adoption program")
        )
    trial_programs = [
        trial["diagnostics"]["evidence_identity"]["program_identity"]
        for trial in candidate["trials"]
    ]
    if sorted(adopted_programs) != sorted(trial_programs):
        raise ValueError("candidate source adoptions conflict with trial programs")
    return candidate["evaluated_foe"]["source_tree"]


def create_source_candidate_assessment(
    source_bundle: Path,
    parent_campaign: Path,
    candidate_campaign: Path,
) -> dict[str, Any]:
    """Create one private evaluator-owned assessment from completed campaigns."""
    bundle = source_bundle_facts(source_bundle)
    parent = campaign_trials(parent_campaign, "parent")
    candidate = campaign_trials(candidate_campaign, "candidate")
    if parent["evaluated_foe"]["source_tree"] != bundle["parent_source_tree"]:
        raise ValueError("parent campaign source tree conflicts with the source bundle base")
    candidate_source_tree = source_campaign_identity(candidate, bundle)
    parent_keys = {tuple(trial["comparison_key"]) for trial in parent["trials"]}
    candidate_keys = {tuple(trial["comparison_key"]) for trial in candidate["trials"]}
    if parent_keys != candidate_keys:
        raise ValueError("parent and candidate campaigns assess different task identities")
    ordered_keys = {key: index for index, key in enumerate(sorted(parent_keys), 1)}
    for evaluation in (parent, candidate):
        for trial in evaluation["trials"]:
            trial["comparison_ordinal"] = ordered_keys[tuple(trial.pop("comparison_key"))]
    if not any(trial_reward(row["raw_result"], "candidate") != 1 for row in candidate["trials"]):
        raise ValueError("candidate campaign does not reject the assessed source candidate")
    body = {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "identities": {
            "parent_evaluation_identity": digest(parent),
            "candidate_evaluation_identity": digest(candidate),
            "parent_source_tree": bundle["parent_source_tree"],
            "candidate_source_tree": candidate_source_tree,
            "source_bundle_identity": bundle["source_bundle_identity"],
            "source_candidate_identity": bundle["source_candidate_identity"],
            "parent_program_identity": bundle["parent_program_identity"],
        },
        "source_manifest": bundle["manifest"],
        "source_patch": bundle["source_patch"],
        "source_evidence": bundle["source_evidence"],
        "prior_diagnosis": bundle["prior_diagnosis"],
        "private_source_generation_task": bundle["source_generation_task"],
        "evaluations": {"parent": parent, "candidate": candidate},
    }
    return validate_source_candidate_assessment(with_identity(body, "assessment_identity"))


def validate_source_candidate_assessment(value: Any) -> dict[str, Any]:
    """Validate a self-contained private assessment and return it unchanged."""
    assessment = require_exact_fields(
        value,
        {
            "schema_version",
            "assessment_identity",
            "identities",
            "source_manifest",
            "source_patch",
            "source_evidence",
            "prior_diagnosis",
            "private_source_generation_task",
            "evaluations",
        },
        "source candidate assessment",
    )
    if assessment["schema_version"] != ASSESSMENT_SCHEMA_VERSION:
        raise ValueError("source candidate assessment has an unsupported schema")
    unsigned = {key: item for key, item in assessment.items() if key != "assessment_identity"}
    if require_digest(assessment["assessment_identity"], "assessment identity") != digest(unsigned):
        raise ValueError("source candidate assessment identity conflicts with its canonical content")
    identities = require_exact_fields(
        assessment["identities"],
        {
            "parent_evaluation_identity",
            "candidate_evaluation_identity",
            "parent_source_tree",
            "candidate_source_tree",
            "source_bundle_identity",
            "source_candidate_identity",
            "parent_program_identity",
        },
        "assessment identities",
    )
    for field in ("parent_source_tree", "candidate_source_tree"):
        require_source_tree(identities[field], field)
    for field in (
        "parent_evaluation_identity",
        "candidate_evaluation_identity",
        "source_bundle_identity",
        "source_candidate_identity",
        "parent_program_identity",
    ):
        require_digest(identities[field], field)
    manifest = assessment["source_manifest"]
    if not isinstance(manifest, dict) or bytes_digest(canonical_json(manifest)) != identities["source_bundle_identity"]:
        raise ValueError("assessment source-bundle identity conflicts with its manifest")
    validate_source_manifest_shape(manifest)
    if manifest.get("candidate_identity") != identities["source_candidate_identity"]:
        raise ValueError("assessment source-candidate identities conflict")
    if (
        manifest.get("base_source_tree") != identities["parent_source_tree"]
        or manifest.get("parent_program_identity")
        != identities["parent_program_identity"]
    ):
        raise ValueError("assessment source manifest conflicts with its parent identities")
    patch = require_exact_fields(
        assessment["source_patch"], {"entries", "contents"}, "assessment source patch"
    )
    if patch["entries"] != manifest.get("entries"):
        raise ValueError("assessment source patch conflicts with its manifest entries")
    if digest(
        {"base_source_tree": identities["parent_source_tree"], "entries": patch["entries"]}
    ) != identities["source_candidate_identity"]:
        raise ValueError("assessment source patch conflicts with its candidate identity")
    validate_patch_contents(identities["parent_source_tree"], patch)
    source_evidence = require_exact_fields(
        assessment["source_evidence"],
        {"parent_plan", "proposal_log"},
        "assessment retained source evidence",
    )
    parent_plan_text = source_evidence["parent_plan"]
    proposal_log = source_evidence["proposal_log"]
    if not isinstance(parent_plan_text, str) or not isinstance(proposal_log, str):
        raise ValueError("assessment retained source evidence is malformed")
    parent_plan = json.loads(parent_plan_text)
    if not isinstance(parent_plan, dict):
        raise ValueError("assessment parent plan is not one JSON object")
    inventory = {item["path"]: item for item in manifest["files"]}
    bound_files = {
        manifest["parent_plan"]: parent_plan_text.encode("utf-8"),
        manifest["proposal_log"]: proposal_log.encode("utf-8"),
    }
    for path, encoded in bound_files.items():
        if (
            inventory[path]["bytes"] != len(encoded)
            or inventory[path]["sha256"] != bytes_digest(encoded)
        ):
            raise ValueError(f"assessment retained source evidence conflicts with {path}")
    if parent_plan.get("identity") != identities["parent_program_identity"]:
        raise ValueError("assessment parent plan conflicts with its program identity")
    prior_diagnosis = prior_typed_diagnosis_from_text(proposal_log)
    if (
        not isinstance(assessment["prior_diagnosis"], dict)
        or assessment["prior_diagnosis"].get("branch") != "implement-source"
        or assessment["prior_diagnosis"] != prior_diagnosis
    ):
        raise ValueError("assessment has no source-producing prior typed diagnosis")
    if (
        not isinstance(assessment["private_source_generation_task"], str)
        or not assessment["private_source_generation_task"].strip()
        or assessment["private_source_generation_task"] != parent_plan.get("task")
    ):
        raise ValueError("assessment has no private source-generation task")
    evaluations = require_exact_fields(
        assessment["evaluations"], {"parent", "candidate"}, "assessment evaluations"
    )
    keys: dict[str, set[int]] = {}
    comparison_keys: dict[str, dict[int, set[tuple[str, str]]]] = {}
    for role in ("parent", "candidate"):
        evaluation = require_exact_fields(
            evaluations[role], {"campaign", "evaluated_foe", "trials"}, f"{role} evaluation"
        )
        evaluated_foe = require_exact_fields(
            evaluation["evaluated_foe"],
            {"source_tree", "runtime_binary"},
            f"{role} evaluated Foe identity",
        )
        require_source_tree(evaluated_foe["source_tree"], f"{role} source tree")
        require_digest(evaluated_foe["runtime_binary"], f"{role} runtime binary")
        campaign_identity, jobs = completed_campaign_jobs(evaluation["campaign"], role)
        if campaign_identity != evaluated_foe:
            raise ValueError(f"{role} campaign conflicts with its evaluated Foe identity")
        trials = evaluation["trials"]
        if not isinstance(trials, list) or not trials:
            raise ValueError(f"{role} evaluation has no trials")
        if sum(job["n_total_trials"] for job in jobs) != len(trials):
            raise ValueError(f"{role} campaign has an incomplete retained trial set")
        keys[role] = set()
        comparison_keys[role] = {}
        for trial in trials:
            require_exact_fields(
                trial,
                {
                    "comparison_ordinal",
                    "raw_task_text",
                    "raw_result",
                    "raw_verifier_report",
                    "diagnostics",
                },
                f"{role} assessment trial",
            )
            ordinal = trial["comparison_ordinal"]
            if type(ordinal) is not int or ordinal <= 0:
                raise ValueError(f"{role} assessment trial has an invalid comparison ordinal")
            if not isinstance(trial["raw_task_text"], str) or not trial[
                "raw_task_text"
            ].strip():
                raise ValueError(f"{role} assessment trial has no raw task text")
            raw_result = trial["raw_result"]
            if not isinstance(raw_result, dict) or (
                "task_text" in raw_result
                and raw_result.get("task_text") != trial["raw_task_text"]
            ):
                raise ValueError(f"{role} assessment trial has conflicting raw task text")
            keys[role].add(ordinal)
            comparison_keys[role].setdefault(ordinal, set()).add(
                (raw_result.get("task_name"), raw_result.get("task_checksum"))
            )
            validate_trial(raw_result, trial["diagnostics"], evaluation["evaluated_foe"], role)
            raw_report = trial["raw_verifier_report"]
            if (
                not isinstance(raw_report, str)
                or not isinstance(json.loads(raw_report), dict)
                or verifier_feedback_from_bytes(raw_report.encode("utf-8"))
                != trial["diagnostics"]["verifier_feedback"]
            ):
                raise ValueError(f"{role} assessment trial has a conflicting raw verifier report")
    if keys["parent"] != keys["candidate"]:
        raise ValueError("assessment parent and candidate trials have conflicting comparisons")
    if any(
        len(values) != 1
        for role in ("parent", "candidate")
        for values in comparison_keys[role].values()
    ) or comparison_keys["parent"] != comparison_keys["candidate"]:
        raise ValueError("assessment comparison ordinals conflict with task identities")
    ordered_comparisons = {
        key: ordinal
        for ordinal, key in enumerate(
            sorted(value for values in comparison_keys["parent"].values() for value in values),
            1,
        )
    }
    if any(
        ordered_comparisons[next(iter(values))] != ordinal
        for role in ("parent", "candidate")
        for ordinal, values in comparison_keys[role].items()
    ):
        raise ValueError("assessment comparison ordinals are not canonically ordered")
    for ordinal in keys["parent"]:
        parent_tasks = {
            row["raw_task_text"]
            for row in evaluations["parent"]["trials"]
            if row["comparison_ordinal"] == ordinal
        }
        candidate_tasks = {
            row["raw_task_text"]
            for row in evaluations["candidate"]["trials"]
            if row["comparison_ordinal"] == ordinal
        }
        if len(parent_tasks) != 1 or candidate_tasks != parent_tasks:
            raise ValueError("assessment comparison has conflicting raw task text")
    for role in ("parent", "candidate"):
        if identities[f"{role}_evaluation_identity"] != digest(evaluations[role]):
            raise ValueError(f"assessment {role} evaluation identity conflicts with its evidence")
    if evaluations["parent"]["evaluated_foe"]["source_tree"] != identities[
        "parent_source_tree"
    ]:
        raise ValueError("assessment parent campaign conflicts with the parent source tree")
    if evaluations["candidate"]["evaluated_foe"]["source_tree"] != identities[
        "candidate_source_tree"
    ]:
        raise ValueError("assessment candidate campaign conflicts with the candidate source tree")
    observed_candidate_tree = source_campaign_identity(
        evaluations["candidate"],
        {
            "source_bundle_identity": identities["source_bundle_identity"],
            "source_candidate_identity": identities["source_candidate_identity"],
            "parent_source_tree": identities["parent_source_tree"],
            "parent_program_identity": identities["parent_program_identity"],
        },
    )
    if observed_candidate_tree != identities["candidate_source_tree"]:
        raise ValueError("assessment candidate identities conflict")
    if not any(trial_reward(row["raw_result"], "candidate") != 1 for row in evaluations["candidate"]["trials"]):
        raise ValueError("assessment contains no rejected candidate trial")
    return assessment


def validate_patch_contents(base_tree: str, patch: dict[str, Any]) -> None:
    expected = validate_source_entries(base_tree, patch["entries"])
    contents = patch["contents"]
    if not isinstance(contents, list) or len(contents) != len(expected):
        raise ValueError("assessment source patch has incomplete applied contents")
    observed = set()
    for item in contents:
        require_exact_fields(item, {"path", "mode", "sha256", "content"}, "patch content")
        path = require_relative_path(item["path"], "patch content path")
        if path in observed or path not in expected:
            raise ValueError("assessment source patch repeats or substitutes a path")
        observed.add(path)
        encoded = item["content"].encode("utf-8") if isinstance(item["content"], str) else b""
        entry = expected[path]
        applied = entry.get("applied")
        if (
            not isinstance(item["content"], str)
            or bytes_digest(encoded) != item["sha256"]
            or item["sha256"] != entry.get("sha256")
            or not isinstance(applied, dict)
            or item["mode"] != applied.get("mode")
        ):
            raise ValueError("assessment source patch content conflicts with its source entry")


def complete_failure(trial: dict[str, Any]) -> dict[str, Any]:
    diagnostic = trial["diagnostics"]
    feedback = diagnostic["verifier_feedback"]
    counts = feedback.get("failure_evidence_counts")
    failures = feedback.get("failures")
    if (
        not isinstance(counts, dict)
        or type(counts.get("total_failed_tests")) is not int
        or counts["total_failed_tests"] <= 0
        or counts.get("retained_failed_tests") != counts["total_failed_tests"]
        or counts.get("omitted_failed_tests") != 0
        or counts.get("unlocated_failed_tests") != 0
        or counts.get("ambiguous_failed_tests") != 0
        or not isinstance(failures, list)
        or len(failures) != counts["total_failed_tests"]
    ):
        raise ValueError("candidate trial has missing, ambiguous, or truncated failure loci")
    loci = []
    for failure in failures:
        locus = failure.get("locus") if isinstance(failure, dict) else None
        if not isinstance(locus, dict) or failure.get("locus_ambiguous") is not False:
            raise ValueError("candidate trial has a missing or ambiguous failure locus")
        allowed = {"locus_sha256", "location", "assertion", "message"}
        if not {"locus_sha256"}.issubset(locus) or not set(locus).issubset(allowed):
            raise ValueError("candidate trial has an invalid failure locus")
        require_digest(locus["locus_sha256"], "failure locus")
        limits = {
            "location": MAX_FAILURE_LOCATION,
            "assertion": MAX_FAILURE_ASSERTION,
            "message": MAX_FAILURE_MESSAGE,
        }
        if not any(key in locus for key in ("location", "assertion")):
            raise ValueError("candidate trial has an unlocated failure locus")
        if any(
            not isinstance(locus.get(key), str)
            or not locus[key]
            or len(locus[key]) >= limit
            for key, limit in limits.items()
            if key in locus
        ):
            raise ValueError("candidate trial has a malformed or truncated failure locus")
        loci.append(
            {
                "failure_class": failure.get("failure_class"),
                **locus,
            }
        )
    locus_ids = [row["locus_sha256"] for row in loci]
    if len(locus_ids) != len(set(locus_ids)):
        raise ValueError("candidate trial has ambiguous repeated failure loci")
    return {
        "verifier_report_sha256": feedback["sha256"],
        "failure_loci": loci,
    }


def validate_bounded_outcome(value: Any, label: str) -> dict[str, str]:
    if (
        not isinstance(value, dict)
        or not set(value).issubset({"kind", "code", "limit"})
        or value.get("kind") not in ("completed", "blocked", "exhausted")
        or not all(
            isinstance(item, str) and OUTCOME_TAG.fullmatch(item) is not None
            for item in value.values()
        )
    ):
        raise ValueError(f"{label} has no bounded typed outcome")
    return value


def bounded_outcome(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("assessment trial has no bounded typed outcome")
    projected = {
        key: value[key]
        for key in ("kind", "code", "limit")
        if isinstance(value.get(key), str)
    }
    return validate_bounded_outcome(projected, "assessment trial")


def bounded_timelines(trial: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostic = trial["diagnostics"]
    timelines = diagnostic.get("verification_timeline")
    if not isinstance(timelines, list) or not timelines:
        raise ValueError("assessment trial has no final validation timeline")
    episodes = diagnostic.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("assessment trial has no complete episode inventory")
    episode_outcomes = {}
    for episode in episodes:
        if not isinstance(episode, dict):
            raise ValueError("assessment trial has an invalid episode inventory")
        episode_id = require_reference(
            episode.get("episode_id"), "assessment episode inventory identity"
        )
        if episode_id in episode_outcomes:
            raise ValueError("assessment trial repeats an episode inventory identity")
        episode_outcomes[episode_id] = bounded_outcome(episode.get("outcome"))
    projected = []
    episode_ids = []
    for timeline in timelines:
        if not isinstance(timeline, dict) or timeline.get("omitted_results") != 0:
            raise ValueError("assessment trial has an incomplete final validation timeline")
        results = timeline.get("results")
        if not isinstance(results, list) or len(results) > MAX_VERIFICATION_RESULTS:
            raise ValueError("assessment trial has an oversized final validation timeline")
        bounded_results = []
        for result in results:
            if not isinstance(result, dict) or result.get("truncated") is not False:
                raise ValueError("assessment trial has a truncated final validation result")
            bounded_results.append(
                {
                    key: result.get(key)
                    for key in (
                        "seq",
                        "step",
                        "call_id",
                        "tool",
                        "is_error",
                        "exit_code",
                        "timed_out",
                        "truncated",
                    )
                }
            )
        episode_id = require_reference(
            timeline.get("episode_id"), "final validation timeline episode"
        )
        episode_ids.append(episode_id)
        projected_timeline = (
            {
                "episode_id": episode_id,
                "last_edit_seq": timeline.get("last_edit_seq"),
                "results": bounded_results,
                "omitted_results": 0,
                "outcome": bounded_outcome(timeline.get("outcome")),
            }
        )
        if projected_timeline["outcome"] != episode_outcomes.get(episode_id):
            raise ValueError("assessment trial timeline conflicts with its episode outcome")
        projected.append(projected_timeline)
    expected = trial["diagnostics"]["evidence_identity"]["episode_id"]
    if (
        len(episode_ids) != len(set(episode_ids))
        or set(episode_ids) != set(episode_outcomes)
        or expected not in episode_ids
    ):
        raise ValueError("assessment trial has conflicting final validation timelines")
    return projected


def trial_reference(trial: dict[str, Any], role: str) -> dict[str, Any]:
    diagnostic = trial["diagnostics"]
    identity = diagnostic["evidence_identity"]
    feedback = diagnostic["verifier_feedback"]
    return {
        "comparison_ordinal": trial["comparison_ordinal"],
        "episode_id": identity["episode_id"],
        "program_identity": identity["program_identity"],
        "runtime_build": identity["runtime_build"],
        "outcome": bounded_outcome(diagnostic.get("outcome")),
        "verifier_report_sha256": feedback["sha256"],
        "final_validation_timelines": bounded_timelines(trial),
        "qualification": {
            "complete_trial": True,
            "conformant_trace": True,
            "source_adoption_completed": role == "candidate",
            "task_grader_status": "accepted",
            "evaluation_role": role,
        },
    }


def project_candidate_assessment_diagnostics(value: Any) -> dict[str, Any]:
    """Project private assessment evidence into the sole model-visible form."""
    assessment = validate_source_candidate_assessment(value)
    evaluations = assessment["evaluations"]
    failures = []
    successes = {"parent": [], "candidate": []}
    for role in ("parent", "candidate"):
        for trial in evaluations[role]["trials"]:
            reward = trial_reward(trial["raw_result"], role)
            if reward == 1:
                successes[role].append(trial_reference(trial, role))
            elif role == "candidate":
                identity = trial["diagnostics"]["evidence_identity"]
                verifier = complete_failure(trial)
                failures.append(
                    {
                        "comparison_ordinal": trial["comparison_ordinal"],
                        "episode_id": identity["episode_id"],
                        "program_identity": identity["program_identity"],
                        "runtime_build": identity["runtime_build"],
                        "outcome": bounded_outcome(
                            trial["diagnostics"].get("outcome")
                        ),
                        "failed_verifiers": [verifier],
                        "final_validation_timelines": bounded_timelines(trial),
                    }
                )
    if not failures or len(failures) > MAX_ASSESSMENT_FAILURES:
        raise ValueError("candidate assessment has no bounded rejected-attempt set")
    for role in successes:
        if (
            (role == "parent" and not successes[role])
            or len(successes[role]) > MAX_ASSESSMENT_SUCCESSES_PER_ROLE
        ):
            raise ValueError(
                f"candidate assessment has an invalid {role} success-reference set"
            )
    identities = assessment["identities"]
    prior_diagnosis_sha256 = digest(assessment["prior_diagnosis"])
    contrast = {
        "rejected_source_candidate_identity": identities["source_candidate_identity"],
        "failed_attempts": failures,
        "success_references": successes,
    }
    body = {
        "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
        "assessment_identity": assessment["assessment_identity"],
        "identities": identities,
        "prior_diagnosis_sha256": prior_diagnosis_sha256,
        "prior_diagnosis": assessment["prior_diagnosis"],
        "verified_source_patch": assessment["source_patch"],
        "assessment_contrast_sha256": digest(contrast),
        "assessment_contrast": contrast,
    }
    diagnostics = with_identity(body, "diagnostics_identity")
    require_private_literals_absent(diagnostics, assessment)
    return validate_candidate_assessment_diagnostics(diagnostics)


def require_private_literals_absent(
    diagnostics: dict[str, Any], assessment: dict[str, Any]
) -> None:
    """Reject exact private task, campaign, and unstructured grader strings."""
    private = set()
    task = assessment.get("private_source_generation_task")
    if isinstance(task, str) and task:
        private.add(task)
    evaluations = assessment["evaluations"]
    for evaluation in evaluations.values():
        campaign = evaluation["campaign"]
        for field in ("dataset", "label"):
            value = campaign.get(field)
            if isinstance(value, str) and value:
                private.add(value)
        for trial in evaluation["trials"]:
            raw = trial["raw_result"]
            private.add(trial["raw_task_text"])
            for field in ("task_name", "task_checksum", "task_text"):
                value = raw.get(field)
                if isinstance(value, str) and value:
                    private.add(value)
            verifier = raw.get("verifier_result")
            if isinstance(verifier, dict):
                for key, value in verifier.items():
                    if (
                        isinstance(value, str)
                        and value
                        and any(word in key.lower() for word in ("message", "prose", "trace", "output"))
                    ):
                        private.add(value)
            diagnostic_feedback = trial["diagnostics"].get("verifier_feedback")
            diagnostic_failures = (
                diagnostic_feedback.get("failures")
                if isinstance(diagnostic_feedback, dict)
                else None
            )
            allowed_locus_strings = {
                text
                for failure in diagnostic_failures or []
                if isinstance(failure, dict)
                for locus in [failure.get("locus")]
                if isinstance(locus, dict)
                for text in locus.values()
                if isinstance(text, str)
            }
            raw_report = json.loads(trial["raw_verifier_report"])

            def collect_grader_strings(item: Any, field: str | None = None) -> None:
                if isinstance(item, dict):
                    for key, nested in item.items():
                        collect_grader_strings(nested, key)
                elif isinstance(item, list):
                    for nested in item:
                        collect_grader_strings(nested, field)
                elif (
                    isinstance(item, str)
                    and item
                    and field in ("message", "name", "trace")
                    and item not in allowed_locus_strings
                ):
                    private.add(item)

            collect_grader_strings(raw_report)
            feedback = trial["diagnostics"].get("verifier_feedback")
            failures = feedback.get("failures") if isinstance(feedback, dict) else None
            if isinstance(failures, list):
                for failure in failures:
                    if not isinstance(failure, dict):
                        continue
                    locus = failure.get("locus")
                    locus_message = locus.get("message") if isinstance(locus, dict) else None
                    for field in ("name", "message"):
                        text = failure.get(field)
                        if isinstance(text, str) and text and text != locus_message:
                            private.add(text)
    encoded = canonical_json(diagnostics)
    leaked = sorted(text for text in private if text.encode("utf-8") in encoded)
    if leaked:
        raise ValueError("candidate assessment diagnostics disclose private evaluator strings")


def walk_diagnostics(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_DIAGNOSTIC_KEYS:
                raise ValueError(
                    "candidate assessment diagnostics contain private field "
                    + ".".join((*path, key))
                )
            walk_diagnostics(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            walk_diagnostics(item, (*path, str(index)))
    elif (
        isinstance(value, str)
        and path[:1] != ("verified_source_patch",)
        and (
            VOLATILE_PATH.search(value) is not None
            or ABSOLUTE_ARTIFACT_PATH.search(value) is not None
            or re.search(r"(?:^|\s)[A-Za-z]:[/\\]", value) is not None
        )
    ):
        raise ValueError("candidate assessment diagnostics contain an absolute artifact path")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("candidate assessment diagnostics contain a nonfinite number")


def require_nonnegative_integer(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} is not a nonnegative integer")
    return value


def validate_final_timelines(
    value: Any, expected_episode: str, label: str
) -> list[dict[str, Any]]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > MAX_FINAL_VALIDATION_TIMELINES
    ):
        raise ValueError(f"{label} has no bounded final validation timelines")
    episodes = []
    for index, timeline_value in enumerate(value):
        timeline = require_exact_fields(
            timeline_value,
            {
                "episode_id",
                "last_edit_seq",
                "results",
                "omitted_results",
                "outcome",
            },
            f"{label} timeline {index}",
        )
        episode = require_bounded_reference(
            timeline["episode_id"], f"{label} timeline {index} episode"
        )
        episodes.append(episode)
        last_edit = timeline["last_edit_seq"]
        if last_edit is not None:
            require_nonnegative_integer(last_edit, f"{label} timeline {index} last edit")
        if timeline["omitted_results"] != 0:
            raise ValueError(f"{label} timeline {index} omits validation results")
        results = timeline["results"]
        if not isinstance(results, list) or len(results) > MAX_VERIFICATION_RESULTS:
            raise ValueError(f"{label} timeline {index} has oversized validation results")
        for result_index, result_value in enumerate(results):
            result = require_exact_fields(
                result_value,
                {
                    "seq",
                    "step",
                    "call_id",
                    "tool",
                    "is_error",
                    "exit_code",
                    "timed_out",
                    "truncated",
                },
                f"{label} timeline {index} result {result_index}",
            )
            require_nonnegative_integer(
                result["seq"], f"{label} timeline {index} result {result_index} sequence"
            )
            require_nonnegative_integer(
                result["step"], f"{label} timeline {index} result {result_index} step"
            )
            for field in ("call_id", "tool"):
                require_bounded_reference(
                    result[field],
                    f"{label} timeline {index} result {result_index} {field}",
                )
            if type(result["is_error"]) is not bool or type(result["timed_out"]) is not bool:
                raise ValueError(f"{label} timeline {index} has an invalid result status")
            exit_code = result["exit_code"]
            if exit_code is not None and type(exit_code) is not int:
                raise ValueError(f"{label} timeline {index} has an invalid exit code")
            if result["truncated"] is not False:
                raise ValueError(f"{label} timeline {index} has a truncated result")
        validate_bounded_outcome(timeline["outcome"], f"{label} timeline {index}")
    if len(episodes) != len(set(episodes)) or expected_episode not in episodes:
        raise ValueError(f"{label} has conflicting final validation timelines")
    return value


def validate_reference_core(value: dict[str, Any], label: str) -> None:
    require_nonnegative_integer(value["comparison_ordinal"], f"{label} comparison ordinal")
    if value["comparison_ordinal"] == 0:
        raise ValueError(f"{label} comparison ordinal must be positive")
    episode = require_bounded_reference(value["episode_id"], f"{label} episode")
    require_digest(value["program_identity"], f"{label} program identity")
    require_digest(value["runtime_build"], f"{label} runtime build")
    validate_bounded_outcome(value["outcome"], f"{label} outcome")
    validate_final_timelines(value["final_validation_timelines"], episode, label)


def validate_failure_reference(value: Any, index: int) -> dict[str, Any]:
    label = f"assessment contrast failed attempt {index}"
    failure = require_exact_fields(
        value,
        {
            "comparison_ordinal",
            "episode_id",
            "program_identity",
            "runtime_build",
            "outcome",
            "failed_verifiers",
            "final_validation_timelines",
        },
        label,
    )
    validate_reference_core(failure, label)
    verifiers = failure["failed_verifiers"]
    if not isinstance(verifiers, list) or not verifiers:
        raise ValueError(f"{label} omits a failed verifier")
    verifier_ids = []
    for verifier_index, verifier_value in enumerate(verifiers):
        verifier = require_exact_fields(
            verifier_value,
            {"verifier_report_sha256", "failure_loci"},
            f"{label} verifier {verifier_index}",
        )
        verifier_ids.append(
            require_digest(
                verifier["verifier_report_sha256"],
                f"{label} verifier {verifier_index} report",
            )
        )
        loci = verifier["failure_loci"]
        if not isinstance(loci, list) or not loci:
            raise ValueError(f"{label} verifier {verifier_index} omits failure loci")
        locus_ids = []
        for locus_index, locus_value in enumerate(loci):
            if not isinstance(locus_value, dict):
                raise ValueError(f"{label} has an invalid failure locus")
            allowed = {
                "failure_class",
                "locus_sha256",
                "location",
                "assertion",
                "message",
            }
            if (
                not {"failure_class", "locus_sha256"}.issubset(locus_value)
                or not set(locus_value).issubset(allowed)
                or not any(field in locus_value for field in ("location", "assertion"))
            ):
                raise ValueError(f"{label} has an incomplete failure locus")
            failure_class = locus_value["failure_class"]
            if failure_class is not None and (
                not isinstance(failure_class, str)
                or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]*(?:Error|Exception)", failure_class)
                is None
            ):
                raise ValueError(f"{label} has an invalid failure class")
            locus_ids.append(
                require_digest(
                    locus_value["locus_sha256"],
                    f"{label} verifier {verifier_index} locus {locus_index}",
                )
            )
            for field, limit in (
                ("location", MAX_FAILURE_LOCATION),
                ("assertion", MAX_FAILURE_ASSERTION),
                ("message", MAX_FAILURE_MESSAGE),
            ):
                if field in locus_value and (
                    not isinstance(locus_value[field], str)
                    or not locus_value[field]
                    or len(locus_value[field]) >= limit
                ):
                    raise ValueError(f"{label} has a malformed or truncated failure locus")
        if len(locus_ids) != len(set(locus_ids)):
            raise ValueError(f"{label} repeats a failure locus")
    if len(verifier_ids) != len(set(verifier_ids)):
        raise ValueError(f"{label} repeats a failed verifier")
    return failure


def validate_success_reference(value: Any, role: str, index: int) -> dict[str, Any]:
    label = f"assessment contrast {role} success {index}"
    reference = require_exact_fields(
        value,
        {
            "comparison_ordinal",
            "episode_id",
            "program_identity",
            "runtime_build",
            "outcome",
            "verifier_report_sha256",
            "final_validation_timelines",
            "qualification",
        },
        label,
    )
    validate_reference_core(reference, label)
    require_digest(reference["verifier_report_sha256"], f"{label} verifier report")
    qualification = require_exact_fields(
        reference["qualification"],
        {
            "complete_trial",
            "conformant_trace",
            "source_adoption_completed",
            "task_grader_status",
            "evaluation_role",
        },
        f"{label} qualification",
    )
    expected = {
        "complete_trial": True,
        "conformant_trace": True,
        "source_adoption_completed": role == "candidate",
        "task_grader_status": "accepted",
        "evaluation_role": role,
    }
    if qualification != expected:
        raise ValueError(f"{label} has an invalid qualification")
    return reference


def validate_candidate_assessment_diagnostics(value: Any) -> dict[str, Any]:
    """Validate the bounded diagnostics projection and its canonical identities."""
    diagnostics = require_exact_fields(
        value,
        {
            "schema_version",
            "diagnostics_identity",
            "assessment_identity",
            "identities",
            "prior_diagnosis_sha256",
            "prior_diagnosis",
            "verified_source_patch",
            "assessment_contrast_sha256",
            "assessment_contrast",
        },
        "candidate assessment diagnostics",
    )
    if diagnostics["schema_version"] != DIAGNOSTICS_SCHEMA_VERSION:
        raise ValueError("candidate assessment diagnostics have an unsupported schema")
    unsigned = {key: item for key, item in diagnostics.items() if key != "diagnostics_identity"}
    if require_digest(diagnostics["diagnostics_identity"], "diagnostics identity") != digest(unsigned):
        raise ValueError("candidate assessment diagnostics identity conflicts with their content")
    require_digest(diagnostics["assessment_identity"], "assessment identity")
    if require_digest(diagnostics["prior_diagnosis_sha256"], "prior diagnosis digest") != digest(
        diagnostics["prior_diagnosis"]
    ):
        raise ValueError("prior diagnosis digest conflicts with the typed diagnosis")
    identities = require_exact_fields(
        diagnostics["identities"],
        {
            "parent_evaluation_identity",
            "candidate_evaluation_identity",
            "parent_source_tree",
            "candidate_source_tree",
            "source_bundle_identity",
            "source_candidate_identity",
            "parent_program_identity",
        },
        "diagnostic identities",
    )
    for field in ("parent_source_tree", "candidate_source_tree"):
        require_source_tree(identities[field], field)
    for field in (
        "parent_evaluation_identity",
        "candidate_evaluation_identity",
        "source_bundle_identity",
        "source_candidate_identity",
        "parent_program_identity",
    ):
        require_digest(identities[field], field)
    patch = require_exact_fields(
        diagnostics["verified_source_patch"], {"entries", "contents"}, "verified source patch"
    )
    validate_patch_contents(identities["parent_source_tree"], patch)
    if digest(
        {"base_source_tree": identities["parent_source_tree"], "entries": patch["entries"]}
    ) != identities["source_candidate_identity"]:
        raise ValueError("verified source patch conflicts with the rejected candidate identity")
    contrast = require_exact_fields(
        diagnostics["assessment_contrast"],
        {"rejected_source_candidate_identity", "failed_attempts", "success_references"},
        "assessment contrast",
    )
    if contrast["rejected_source_candidate_identity"] != identities["source_candidate_identity"]:
        raise ValueError("assessment contrast cites a conflicting rejected candidate")
    if require_digest(
        diagnostics["assessment_contrast_sha256"], "assessment contrast digest"
    ) != digest(contrast):
        raise ValueError("assessment contrast digest conflicts with its content")
    failures = contrast["failed_attempts"]
    if not isinstance(failures, list) or not failures or len(failures) > MAX_ASSESSMENT_FAILURES:
        raise ValueError("assessment contrast has no bounded failure set")
    failure_ids = []
    for index, failure_value in enumerate(failures):
        failure = validate_failure_reference(failure_value, index)
        failure_ids.append(failure["episode_id"])
    if len(failure_ids) != len(set(failure_ids)):
        raise ValueError("assessment contrast repeats a failed episode")
    successes = require_exact_fields(
        contrast["success_references"], {"parent", "candidate"}, "success references"
    )
    for role, references in successes.items():
        if (
            not isinstance(references, list)
            or (role == "parent" and not references)
            or len(references) > MAX_ASSESSMENT_SUCCESSES_PER_ROLE
        ):
            raise ValueError(f"assessment contrast has an invalid {role} success set")
        episode_ids = []
        for index, reference_value in enumerate(references):
            reference = validate_success_reference(reference_value, role, index)
            episode_ids.append(reference["episode_id"])
        if len(episode_ids) != len(set(episode_ids)):
            raise ValueError(f"assessment contrast repeats a {role} success episode")
    walk_diagnostics(diagnostics)
    encoded = canonical_json(diagnostics)
    if len(encoded) > MAX_DIAGNOSTICS_BYTES:
        raise ValueError(
            f"candidate assessment diagnostics are {len(encoded)} bytes; maximum is {MAX_DIAGNOSTICS_BYTES}"
        )
    return diagnostics


def assessment_revision_schema() -> dict[str, Any]:
    citation = {
        "type": "object",
        "properties": {
            "episode_id": {"type": "string", "minLength": 1},
            "verifier_report_sha256s": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
            "locus_sha256s": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
        },
        "required": ["episode_id", "verifier_report_sha256s", "locus_sha256s"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "assessment_contrast_sha256": {"type": "string", "minLength": 71, "maxLength": 71},
            "rejected_source_candidate_identity": {"type": "string", "minLength": 71, "maxLength": 71},
            "prior_diagnosis_sha256": {"type": "string", "minLength": 71, "maxLength": 71},
            "disposition": {
                "type": "string",
                "enum": ["retain", "narrow", "replace", "insufficient-evidence"],
            },
            "failed_attempts": {"type": "array", "items": citation, "minItems": 1},
            "parent_success_episode_ids": {"type": "array", "items": {"type": "string"}},
            "candidate_success_episode_ids": {"type": "array", "items": {"type": "string"}},
            "explanation": {"type": "string", "minLength": 1},
        },
        "required": [
            "assessment_contrast_sha256",
            "rejected_source_candidate_identity",
            "prior_diagnosis_sha256",
            "disposition",
            "failed_attempts",
            "parent_success_episode_ids",
            "candidate_success_episode_ids",
            "explanation",
        ],
        "additionalProperties": False,
    }


def validate_revised_diagnosis(
    diagnosis: Any, diagnostics: dict[str, Any] | None
) -> None:
    """Require complete assessment citations when a second generation is enabled."""
    if diagnostics is None:
        return
    validate_candidate_assessment_diagnostics(diagnostics)
    if not isinstance(diagnosis, dict):
        raise ValueError("revised diagnosis is not an object")
    revision = diagnosis.get("assessment_revision")
    if not isinstance(revision, dict):
        raise ValueError("revised diagnosis omits assessment_revision")
    contrast = diagnostics["assessment_contrast"]
    expected_scalars = {
        "assessment_contrast_sha256": diagnostics["assessment_contrast_sha256"],
        "rejected_source_candidate_identity": contrast["rejected_source_candidate_identity"],
        "prior_diagnosis_sha256": diagnostics["prior_diagnosis_sha256"],
    }
    for field, expected in expected_scalars.items():
        if revision.get(field) != expected:
            raise ValueError(f"revised diagnosis cites the wrong {field}")
    disposition = revision.get("disposition")
    branch = diagnosis.get("branch")
    if branch == "insufficient-evidence":
        if disposition != "insufficient-evidence":
            raise ValueError("insufficient-evidence diagnosis requires the matching assessment disposition")
    elif disposition not in ("retain", "narrow", "replace"):
        raise ValueError("candidate-producing revised diagnosis must retain, narrow, or replace the prior diagnosis")
    expected_failures = {row["episode_id"]: row for row in contrast["failed_attempts"]}
    citations = revision.get("failed_attempts")
    if not isinstance(citations, list):
        raise ValueError("revised diagnosis has no failed-attempt citations")
    observed = {}
    for citation in citations:
        if not isinstance(citation, dict) or citation.get("episode_id") in observed:
            raise ValueError("revised diagnosis repeats or corrupts a failed-attempt citation")
        observed[citation.get("episode_id")] = citation
    if set(observed) != set(expected_failures):
        raise ValueError("revised diagnosis must cite every assessed failed attempt")
    for episode_id, failure in expected_failures.items():
        expected_reports = {
            verifier["verifier_report_sha256"] for verifier in failure["failed_verifiers"]
        }
        expected_loci = {
            locus["locus_sha256"]
            for verifier in failure["failed_verifiers"]
            for locus in verifier["failure_loci"]
        }
        citation = observed[episode_id]
        reports = citation.get("verifier_report_sha256s")
        loci = citation.get("locus_sha256s")
        if (
            not isinstance(reports, list)
            or len(reports) != len(set(reports))
            or set(reports) != expected_reports
        ):
            raise ValueError("revised diagnosis must cite every failed verifier exactly once")
        if not isinstance(loci, list) or len(loci) != len(set(loci)) or set(loci) != expected_loci:
            raise ValueError("revised diagnosis must cite every assessed failure locus exactly once")
    for role, field in (
        ("parent", "parent_success_episode_ids"),
        ("candidate", "candidate_success_episode_ids"),
    ):
        expected = {row["episode_id"] for row in contrast["success_references"][role]}
        observed_success = revision.get(field)
        if (
            not isinstance(observed_success, list)
            or len(observed_success) != len(set(observed_success))
            or set(observed_success) != expected
        ):
            raise ValueError(f"revised diagnosis must cite every qualified {role} success")
    require_generalized_revised_diagnosis(diagnosis, diagnostics)


def assessment_failure_literals(diagnostics: dict[str, Any]) -> set[str]:
    """Return assessment details that must remain inside the diagnosis episode."""
    literals = set()
    for failure in diagnostics["assessment_contrast"]["failed_attempts"]:
        for verifier in failure["failed_verifiers"]:
            for locus in verifier["failure_loci"]:
                for field in ("location", "assertion", "message"):
                    value = locus.get(field)
                    if isinstance(value, str) and len(value.strip()) >= 12:
                        literals.add(" ".join(value.split()).casefold())
    return literals


def diagnosis_explanations(value: Any, field: str | None = None) -> list[str]:
    """Return prose fields while excluding required opaque citations."""
    if isinstance(value, dict):
        return [
            text
            for key, item in value.items()
            if not any(
                marker in key
                for marker in ("sha256", "identity", "episode_id")
            )
            for text in diagnosis_explanations(item, key)
        ]
    if isinstance(value, list):
        return [text for item in value for text in diagnosis_explanations(item, field)]
    return [value] if isinstance(value, str) else []


def require_generalized_revised_diagnosis(
    diagnosis: dict[str, Any], diagnostics: dict[str, Any]
) -> None:
    """Keep task-specific verifier details out of the implementation handoff."""
    prose = [" ".join(value.split()).casefold() for value in diagnosis_explanations(diagnosis)]
    for literal in assessment_failure_literals(diagnostics):
        if any(literal in value for value in prose):
            raise ValueError(
                "revised diagnosis copies a task-specific assessment detail into its handoff"
            )


def require_source_candidate_excludes_assessment_literals(
    candidate: Path,
    changed_paths: list[str],
    diagnostics: dict[str, Any] | None,
) -> None:
    """Reject source changes that embed evaluator-owned assessment details."""
    if diagnostics is None:
        return
    validate_candidate_assessment_diagnostics(diagnostics)
    forbidden = {
        diagnostics["assessment_identity"],
        diagnostics["diagnostics_identity"],
        diagnostics["assessment_contrast_sha256"],
        diagnostics["identities"]["source_candidate_identity"],
        *assessment_failure_literals(diagnostics),
    }
    for failure in diagnostics["assessment_contrast"]["failed_attempts"]:
        for verifier in failure["failed_verifiers"]:
            forbidden.add(verifier["verifier_report_sha256"])
            forbidden.update(
                locus["locus_sha256"] for locus in verifier["failure_loci"]
            )
    for relative in changed_paths:
        path = candidate / relative
        if not path.is_file():
            continue
        text = " ".join(path.read_text(encoding="utf-8").split()).casefold()
        if any(value.casefold() in text for value in forbidden):
            raise ValueError(
                f"source candidate {relative} contains evaluator-owned assessment details"
            )


def generation_context(
    diagnostics: dict[str, Any],
    diagnosis: dict[str, Any],
    trajectory_evidence_sha256: str,
) -> dict[str, Any]:
    """Return the canonical context record retained with a generated candidate."""
    validate_candidate_assessment_diagnostics(diagnostics)
    validate_revised_diagnosis(diagnosis, diagnostics)
    require_digest(trajectory_evidence_sha256, "trajectory evidence digest")
    body = {
        "schema_version": 1,
        "assessment_diagnostics_identity": diagnostics["diagnostics_identity"],
        "assessment_contrast_sha256": diagnostics["assessment_contrast_sha256"],
        "rejected_source_candidate_identity": diagnostics["identities"]["source_candidate_identity"],
        "generation_parent_source_tree": diagnostics["identities"]["parent_source_tree"],
        "revised_diagnosis_sha256": digest(diagnosis),
        "assessment_disposition": diagnosis["assessment_revision"]["disposition"],
        "trajectory_evidence_sha256": trajectory_evidence_sha256,
    }
    return with_identity(body, "generation_context_identity")


def bind_generation_evidence(
    bundle: Path,
    diagnostics: dict[str, Any],
    context: dict[str, Any],
) -> None:
    """Write evaluator diagnostics before source capture includes the bundle files."""
    bundle = require_real_directory(bundle, "new source evidence bundle")
    validate_candidate_assessment_diagnostics(diagnostics)
    if context.get("assessment_diagnostics_identity") != diagnostics["diagnostics_identity"]:
        raise ValueError("generation context conflicts with assessment diagnostics")
    unsigned = {key: item for key, item in context.items() if key != "generation_context_identity"}
    if context.get("generation_context_identity") != digest(unsigned):
        raise ValueError("generation context identity conflicts with its content")
    for name, value in (
        (ASSESSMENT_DIAGNOSTICS_FILE, diagnostics),
        (GENERATION_CONTEXT_FILE, context),
    ):
        path = bundle / name
        if path.exists() or path.is_symlink():
            raise ValueError(f"source evidence already contains {name}")
        path.write_bytes(canonical_json(value))


def require_novel_source_candidate(
    captured: dict[str, Any], diagnostics: dict[str, Any] | None
) -> None:
    """Reject a generated candidate with the assessed candidate's identity."""
    if diagnostics is None:
        return
    validate_candidate_assessment_diagnostics(diagnostics)
    observed = require_digest(
        captured.get("source_candidate_identity"), "generated source candidate identity"
    )
    if observed == diagnostics["identities"]["source_candidate_identity"]:
        raise ValueError(
            "generated source candidate repeats the externally rejected candidate identity"
        )


def require_assessment_isolation(
    assessment: Path, diagnostics_root: Path, coding_read_roots: list[Path]
) -> None:
    """Keep private and projected assessment files outside coding read roots."""
    assessment = assessment.absolute().resolve(strict=True)
    diagnostics_root = diagnostics_root.absolute().resolve(strict=True)
    if assessment.is_relative_to(diagnostics_root):
        raise ValueError("private assessment must remain outside the diagnostics directory")
    for root in coding_read_roots:
        root = root.absolute().resolve(strict=True)
        if assessment.is_relative_to(root):
            raise ValueError("private assessment is reachable through a coding read grant")
        if diagnostics_root.is_relative_to(root):
            raise ValueError("assessment diagnostics are reachable through a coding read grant")


def load_source_candidate_assessment(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = require_confined_regular_file(path, path.absolute().parent, "source candidate assessment")
    assessment = json.loads(path.read_text(encoding="utf-8"))
    assessment = validate_source_candidate_assessment(assessment)
    return assessment, project_candidate_assessment_diagnostics(assessment)


def new_output_path(path: Path, label: str) -> Path:
    parent = require_real_directory(path.absolute().parent, f"{label} parent")
    output = parent / path.name
    if output.exists() or output.is_symlink():
        raise ValueError(f"{label} already exists: {output}")
    return output


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.touch(mode=0o600, exist_ok=False)
    path.write_bytes(canonical_json(value))


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    subcommands = answer.add_subparsers(dest="command", required=True)
    create = subcommands.add_parser("create")
    create.add_argument("--source-bundle", type=Path, required=True)
    create.add_argument("--parent-campaign", type=Path, required=True)
    create.add_argument("--candidate-campaign", type=Path, required=True)
    create.add_argument("--assessment", type=Path, required=True)
    create.add_argument("--diagnostics", type=Path, required=True)
    validate = subcommands.add_parser("validate")
    validate.add_argument("assessment", type=Path)
    return answer


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "create":
            assessment = create_source_candidate_assessment(
                args.source_bundle, args.parent_campaign, args.candidate_campaign
            )
            diagnostics = project_candidate_assessment_diagnostics(assessment)
            assessment_output = new_output_path(args.assessment, "private assessment output")
            diagnostics_output = new_output_path(args.diagnostics, "diagnostics output")
            if assessment_output == diagnostics_output:
                raise ValueError("private assessment and diagnostics must use different paths")
            write_private_json(assessment_output, assessment)
            write_private_json(diagnostics_output, diagnostics)
        else:
            _, diagnostics = load_source_candidate_assessment(args.assessment)
            print(json.dumps(diagnostics, indent=2, sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"source candidate assessment: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
