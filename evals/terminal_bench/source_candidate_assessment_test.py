#!/usr/bin/python3

import copy
import json
import math
import subprocess
import tempfile
import unittest
from pathlib import Path

from source_candidate_assessment import (
    ASSESSMENT_DIAGNOSTICS_FILE,
    GENERATION_CONTEXT_FILE,
    MAX_DIAGNOSTICS_BYTES,
    MAX_TERMINAL_AUDIT_TEXT,
    bind_generation_evidence,
    bytes_digest,
    canonical_json,
    create_source_candidate_assessment,
    digest,
    generation_context,
    project_candidate_assessment_diagnostics,
    require_assessment_isolation,
    require_novel_source_candidate,
    require_source_candidate_excludes_assessment_literals,
    source_unified_diff,
    validate_candidate_assessment_diagnostics,
    validate_revised_diagnosis,
)
from run_self_improvement import build_config, model_config, write_diagnosis_validator
from trajectory_diagnostics import (
    MAX_VERIFICATION_RESULTS,
    verifier_feedback,
    verifier_feedback_from_bytes,
)


PRIVATE_TASK = "PRIVATE TASK TEXT SENTINEL"
PRIVATE_GRADER = "PRIVATE GRADER PROSE SENTINEL"
PRIVATE_CAMPAIGN = "PRIVATE CAMPAIGN LABEL SENTINEL"
SHA_ONE = "sha256:" + "1" * 64
SHA_TWO = "sha256:" + "2" * 64
SHA_THREE = "sha256:" + "3" * 64
PARENT_TREE = "git-tree-sha1:" + "a" * 40
CANDIDATE_TREE = "git-tree-sha1:" + "b" * 40


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def reidentify_assessment(value):
    for role in ("parent", "candidate"):
        value["identities"][f"{role}_evaluation_identity"] = digest(
            value["evaluations"][role]
        )
    unsigned = {key: item for key, item in value.items() if key != "assessment_identity"}
    value["assessment_identity"] = digest(unsigned)


def reidentify_projection(value):
    value["assessment_contrast_sha256"] = digest(value["assessment_contrast"])
    unsigned = {key: item for key, item in value.items() if key != "diagnostics_identity"}
    value["diagnostics_identity"] = digest(unsigned)


def timeline(episode_id: str, outcome: str = "completed"):
    return [
        {
            "episode_id": episode_id,
            "last_edit_seq": 4,
            "results": [
                {
                    "seq": 4,
                    "step": 2,
                    "call_id": "call-edit",
                    "tool": "edit",
                    "subject": "/private/artifact/path",
                    "is_error": False,
                    "exit_code": None,
                    "timed_out": False,
                    "truncated": False,
                },
                {
                    "seq": 7,
                    "step": 3,
                    "call_id": "call-check",
                    "tool": "check",
                    "subject": PRIVATE_TASK,
                    "is_error": False,
                    "exit_code": 0,
                    "timed_out": False,
                    "truncated": False,
                },
            ],
            "omitted_results": 0,
            "outcome": {"kind": outcome},
        }
    ]


def terminal_audit_value():
    return {
        "acceptance_evidence": [
            {
                "requirement": "The final artifact satisfies its measured constraint.",
                "seq": 7,
                "status": "passed",
            }
        ],
        "changed_paths": ["artifact.txt"],
        "learned": [
            {
                "claim": "The terminal audit measured 4.5 against a limit of 5.",
                "seq": 7,
            }
        ],
        "summary": "The terminal audit accepted the final artifact.",
        "unresolved_risks": [],
        "validation": [
            "A project-owned check at /workspace/bin/check measured the final artifact."
        ],
    }


def diagnostics(episode_id: str, runtime: str, success: bool):
    failures = []
    total = 0
    if not success:
        total = 1
        failures = [
            {
                "name": "private task verifier name",
                "status": "failed",
                "raw_status": "failed",
                "failure_class": "AssertionError",
                "message": PRIVATE_GRADER,
                "locus": {
                    "locus_sha256": SHA_THREE,
                    "location": "tests/behavior.rs:19",
                    "assertion": "observed == expected",
                    "observed_assertion": "5.263745 <= 5",
                    "message": "values differ",
                },
                "locus_ambiguous": False,
            }
        ]
    return {
        "schema_version": 5,
        "evidence_identity": {
            "program_identity": SHA_TWO,
            "runtime_build": runtime,
            "episode_id": episode_id,
            "task_checksum": "private-task-checksum",
        },
        "task": "private-task-name",
        "outcome": {"kind": "completed", "value": terminal_audit_value()},
        "verifier_reward": 1.0 if success else 0.0,
        "trial_error": None,
        "artifact_outcome_mismatch": False,
        "episodes": [
            {
                "episode_id": episode_id,
                "parent_id": None,
                "program": "private-program-name",
                "model": "private-model-name",
                "model_calls": 1,
                "tool_results": 2,
                "outcome": {"kind": "completed"},
            }
        ],
        "verifier_feedback": {
            "source": "verifier/ctrf.json",
            "sha256": SHA_ONE if success else SHA_TWO,
            "summary": {"failed": total},
            "failure_classes": [] if success else ["AssertionError"],
            "failures": failures,
            "failure_evidence_counts": {
                "total_failed_tests": total,
                "retained_failed_tests": total,
                "omitted_failed_tests": 0,
                "unlocated_failed_tests": 0,
                "ambiguous_failed_tests": 0,
            },
        },
        "verification_timeline": timeline(episode_id),
    }


def trial_result(task: str, checksum: str, success: bool):
    return {
        "task_name": task,
        "task_checksum": checksum,
        "task_text": PRIVATE_TASK,
        "trial_name": "private-trial-name",
        "exception_info": None,
        "verifier_result": {
            "rewards": {"reward": 1.0 if success else 0.0},
            "arbitrary_grader_prose": PRIVATE_GRADER,
        },
        "agent_result": {
            "metadata": {
                "foe_trace_conformant": True,
                "foe_outcome": {
                    "kind": "completed",
                    "value": terminal_audit_value(),
                },
            }
        },
    }


def write_campaign(
    root: Path,
    *,
    source_tree: str,
    runtime: str,
    trials: list[tuple[str, bool]],
    source_candidate: dict | None = None,
):
    job_root = root / "job"
    write_json(
        job_root / "result.json",
        {
            "stats": {
                "n_completed_trials": len(trials),
                "n_errored_trials": 0,
            },
            "n_total_trials": len(trials),
        },
    )
    diagnostic_paths = []
    for index, (episode_id, success) in enumerate(trials, 1):
        trial_root = job_root / f"trial-{index}"
        write_json(
            trial_root / "result.json",
            trial_result("private-task-name", "private-task-checksum", success),
        )
        report = {
            "results": {
                "summary": {
                    "tests": 1,
                    "passed": 1 if success else 0,
                    "failed": 0 if success else 1,
                    "skipped": 0,
                    "pending": 0,
                    "other": 0,
                },
                "tests": (
                    [{"name": "successful check", "status": "passed"}]
                    if success
                    else [
                        {
                            "name": "private task verifier name",
                            "status": "failed",
                            "raw_status": "failed",
                            "message": PRIVATE_GRADER,
                            "trace": (
                                "tests/behavior_test.py:19: failure\n"
                                "> assert observed == expected\n"
                                "E assert 5.263745 <= 5\n"
                                "E AssertionError: values differ\n"
                            ),
                        }
                    ]
                ),
            }
        }
        write_json(trial_root / "verifier" / "ctrf.json", report)
        diagnostic_path = trial_root / "agent" / "foe-diagnostics.json"
        write_json(trial_root / "agent" / "foe-plan.json", {"task": PRIVATE_TASK})
        diagnostic = diagnostics(episode_id, runtime, success)
        diagnostic["verifier_feedback"] = verifier_feedback(
            trial_root / "result.json", artifact_root=job_root
        )
        write_json(diagnostic_path, diagnostic)
        write_json(
            trial_root / "agent" / "foe-episode" / "episode.jsonl",
            {
                "seq": 0,
                "type": "episode/start",
                "data": {
                    "id": episode_id,
                    "identity": diagnostic["evidence_identity"]["program_identity"],
                    "runtime": {"build": runtime},
                    "task": PRIVATE_TASK,
                },
            },
        )
        diagnostic_paths.append(str(diagnostic_path.relative_to(job_root)))
    job = {
        "task": "private-task-name",
        "result": "job/result.json",
        "execution_status": "started",
        "n_completed_trials": len(trials),
        "n_errored_trials": 0,
        "n_total_trials": len(trials),
        "configuration_claim_valid": True,
        "diagnostics": diagnostic_paths,
    }
    campaign = {
        "schema_version": 1,
        "dataset": "private-dataset",
        "label": PRIVATE_CAMPAIGN,
        "cancelled": False,
        "stopped_reason": None,
        "evaluated_foe": {
            "source_tree": source_tree,
            "runtime_binary": runtime,
        },
        "source_candidate": source_candidate,
        "source_adoptions": [],
        "jobs": [job],
    }
    if source_candidate is not None:
        campaign["source_adoptions"] = [
            {
                "source_bundle_identity": source_candidate["source_bundle_identity"],
                "source_candidate_identity": source_candidate["source_candidate_identity"],
                "parent_program_identity": source_candidate["parent_program_identity"],
                "evaluated_pair": source_candidate["evaluated_pair"],
                "program_identity": SHA_TWO,
            }
            for _ in trials
        ]
    write_json(root / "campaign.json", campaign)


def write_source_bundle(root: Path):
    bundle = root / "source-bundle"
    content = b"pub fn assessed_behavior() -> bool { true }\n"
    content_path = "candidate-files/crates/core/src/assessed.rs"
    source_path = "crates/core/src/assessed.rs"
    (bundle / content_path).parent.mkdir(parents=True, exist_ok=True)
    (bundle / content_path).write_bytes(content)
    prior_diagnosis = {
        "branch": "implement-source",
        "limitation": "The runtime omits a general completion check.",
        "intervention": "Add the general completion check.",
    }
    log = canonical_json(
        {
            "seq": 8,
            "type": "workflow/node-end",
            "data": {"node": "diagnose-runtime", "value": prior_diagnosis},
        }
    ) + b"\n"
    (bundle / "episode").mkdir()
    (bundle / "episode/episode.jsonl").write_bytes(log)
    parent_plan = {
        "identity": SHA_ONE,
        "identity_document": {},
        "program": {},
        "task": PRIVATE_TASK,
    }
    write_json(bundle / "parent-plan.json", parent_plan)
    (bundle / "candidate-check").write_bytes(b"#!/bin/sh\n")
    entry = {
        "status": "present",
        "path": source_path,
        "applied": {
            "object_type": "blob",
            "mode": "100644",
            "identity": "git-blob-sha1:" + "c" * 40,
        },
        "sha256": bytes_digest(content),
        "content": content_path,
    }
    candidate_identity = digest(
        {"base_source_tree": PARENT_TREE, "entries": [entry]}
    )
    retained = [
        "candidate-check",
        content_path,
        "episode/episode.jsonl",
        "parent-plan.json",
    ]
    files = []
    for name in retained:
        encoded = (bundle / name).read_bytes()
        files.append({"path": name, "bytes": len(encoded), "sha256": bytes_digest(encoded)})
    manifest = {
        "schema_version": 1,
        "candidate_identity": candidate_identity,
        "base_source_tree": PARENT_TREE,
        "entries": [entry],
        "parent_plan": "parent-plan.json",
        "parent_program_identity": SHA_ONE,
        "proposal_log": "episode/episode.jsonl",
        "verification_log": "episode/episode.jsonl",
        "verification_seq": 9,
        "verification_tool": "check",
        "verification_executable": "candidate-check",
        "verification_executable_sha256": bytes_digest(b"#!/bin/sh\n"),
        "files": files,
    }
    (bundle / "source-candidate-manifest.json").write_bytes(canonical_json(manifest))
    return bundle, manifest, candidate_identity, prior_diagnosis, content


class SourceCandidateAssessmentTest(unittest.TestCase):
    def test_unified_diff_reads_the_recorded_base_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["/usr/bin/git", "init", "-q", str(root)], check=True)
            subprocess.run(
                ["/usr/bin/git", "-C", str(root), "config", "user.name", "Foe Test"],
                check=True,
            )
            subprocess.run(
                ["/usr/bin/git", "-C", str(root), "config", "user.email", "foe@example.invalid"],
                check=True,
            )
            path = root / "src/example.rs"
            path.parent.mkdir()
            path.write_text("fn value() -> bool { false }\n", encoding="utf-8")
            subprocess.run(["/usr/bin/git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["/usr/bin/git", "-C", str(root), "commit", "-qm", "base"], check=True)
            algorithm = subprocess.check_output(
                ["/usr/bin/git", "-C", str(root), "rev-parse", "--show-object-format"],
                text=True,
            ).strip()
            tree = subprocess.check_output(
                ["/usr/bin/git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
                text=True,
            ).strip()
            blob = subprocess.check_output(
                ["/usr/bin/git", "-C", str(root), "rev-parse", "HEAD:src/example.rs"],
                text=True,
            ).strip()
            entry = {
                "status": "present",
                "path": "src/example.rs",
                "base": {
                    "mode": "100644",
                    "object_type": "blob",
                    "identity": f"git-blob-{algorithm}:{blob}",
                },
            }
            diff = source_unified_diff(
                root,
                f"git-tree-{algorithm}:{tree}",
                [entry],
                [{"path": "src/example.rs", "content": "fn value() -> bool { true }\n"}],
            )
            self.assertIn("-fn value() -> bool { false }", diff)
            self.assertIn("+fn value() -> bool { true }", diff)
            entry["base"]["identity"] = f"git-blob-{algorithm}:" + "0" * len(blob)
            with self.assertRaisesRegex(ValueError, "recorded base tree"):
                source_unified_diff(
                    root,
                    f"git-tree-{algorithm}:{tree}",
                    [entry],
                    [{"path": "src/example.rs", "content": "fn value() -> bool { true }\n"}],
                )

    def assessment(
        self,
        root: Path,
        candidate_trials: list[tuple[str, bool]] | None = None,
    ):
        bundle, manifest, candidate_identity, prior_diagnosis, content = write_source_bundle(root)
        bundle_identity = bytes_digest(canonical_json(manifest))
        parent = root / "parent-campaign"
        candidate = root / "candidate-campaign"
        write_campaign(
            parent,
            source_tree=PARENT_TREE,
            runtime=SHA_ONE,
            trials=[("ep-parent-success", True)],
        )
        source_candidate = {
            "source_bundle_identity": bundle_identity,
            "source_candidate_identity": candidate_identity,
            "base_source_tree": PARENT_TREE,
            "parent_program_identity": SHA_ONE,
            "evaluated_pair": {
                "source_tree": CANDIDATE_TREE,
                "runtime_binary": SHA_TWO,
            },
        }
        write_campaign(
            candidate,
            source_tree=CANDIDATE_TREE,
            runtime=SHA_TWO,
            trials=(
                candidate_trials
                if candidate_trials is not None
                else [("ep-candidate-success", True), ("ep-candidate-failure", False)]
            ),
            source_candidate=source_candidate,
        )
        assessment = create_source_candidate_assessment(bundle, parent, candidate)
        return assessment, candidate_identity, prior_diagnosis, content

    def test_projection_binds_patch_failures_timelines_and_successes_without_private_text(self):
        with tempfile.TemporaryDirectory() as directory:
            assessment, candidate_identity, prior_diagnosis, content = self.assessment(
                Path(directory)
            )
            projection = project_candidate_assessment_diagnostics(assessment)
        private_bytes = canonical_json(assessment)
        public_bytes = canonical_json(projection)
        for planted in (PRIVATE_TASK, PRIVATE_GRADER, PRIVATE_CAMPAIGN):
            self.assertIn(planted.encode(), private_bytes)
            self.assertNotIn(planted.encode(), public_bytes)
        self.assertEqual(
            projection["identities"]["source_candidate_identity"], candidate_identity
        )
        self.assertEqual(projection["prior_diagnosis"], prior_diagnosis)
        self.assertIn(
            content.decode().strip(),
            projection["verified_source_patch"]["unified_diff"],
        )
        self.assertEqual(
            projection["verified_source_patch"]["source_patch_sha256"],
            digest(assessment["source_patch"]),
        )
        contrast = projection["assessment_contrast"]
        self.assertEqual(len(contrast["failed_attempts"]), 1)
        terminal_report = contrast["failed_attempts"][0]["terminal_audit_report"]
        self.assertEqual(
            terminal_report["learned"][0]["claim"],
            "The terminal audit measured 4.5 against a limit of 5.",
        )
        self.assertIn("<absolute-path>", terminal_report["validation"][0])
        self.assertNotIn("/workspace", terminal_report["validation"][0])
        self.assertRegex(
            contrast["failed_attempts"][0]["failed_verifiers"][0]["failure_loci"][0][
                "locus_sha256"
            ],
            r"^sha256:[0-9a-f]{64}$",
        )
        self.assertEqual(len(contrast["success_references"]["parent"]), 1)
        self.assertEqual(len(contrast["success_references"]["candidate"]), 1)
        self.assertLessEqual(len(public_bytes), MAX_DIAGNOSTICS_BYTES)

    def test_assessment_uses_identity_bound_root_start_when_plan_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, manifest, candidate_identity, _, _ = write_source_bundle(root)
            parent = root / "parent-campaign"
            candidate = root / "candidate-campaign"
            write_campaign(
                parent,
                source_tree=PARENT_TREE,
                runtime=SHA_ONE,
                trials=[("ep-parent-success", True)],
            )
            (parent / "job/trial-1/agent/foe-plan.json").unlink()
            source_candidate = {
                "source_bundle_identity": bytes_digest(canonical_json(manifest)),
                "source_candidate_identity": candidate_identity,
                "base_source_tree": PARENT_TREE,
                "parent_program_identity": SHA_ONE,
                "evaluated_pair": {
                    "source_tree": CANDIDATE_TREE,
                    "runtime_binary": SHA_TWO,
                },
            }
            write_campaign(
                candidate,
                source_tree=CANDIDATE_TREE,
                runtime=SHA_TWO,
                trials=[("ep-candidate-failure", False)],
                source_candidate=source_candidate,
            )

            assessment = create_source_candidate_assessment(bundle, parent, candidate)

        self.assertEqual(
            assessment["evaluations"]["parent"]["trials"][0]["raw_task_text"],
            PRIVATE_TASK,
        )

    def test_projection_accepts_a_candidate_with_only_failed_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            assessment, _, _, _ = self.assessment(
                Path(directory),
                [
                    ("ep-candidate-failure-one", False),
                    ("ep-candidate-failure-two", False),
                ],
            )
            projection = project_candidate_assessment_diagnostics(assessment)
        contrast = projection["assessment_contrast"]
        self.assertEqual(len(contrast["failed_attempts"]), 2)
        self.assertEqual(contrast["success_references"]["candidate"], [])
        validate_candidate_assessment_diagnostics(projection)

    def test_rejects_boolean_nonfinite_and_incomplete_trial_rewards(self):
        with tempfile.TemporaryDirectory() as directory:
            assessment, _, _, _ = self.assessment(Path(directory))
        for invalid in (True, math.inf, math.nan):
            changed = copy.deepcopy(assessment)
            changed["evaluations"]["candidate"]["trials"][0]["raw_result"][
                "verifier_result"
            ]["rewards"]["reward"] = invalid
            if invalid is True:
                reidentify_assessment(changed)
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    project_candidate_assessment_diagnostics(changed)
        incomplete = copy.deepcopy(assessment)
        incomplete["evaluations"]["candidate"]["trials"][0]["raw_result"][
            "exception_info"
        ] = {"message": "trial failed"}
        reidentify_assessment(incomplete)
        with self.assertRaisesRegex(ValueError, "records an exception"):
            project_candidate_assessment_diagnostics(incomplete)
        incomplete_campaign = copy.deepcopy(assessment)
        incomplete_campaign["evaluations"]["candidate"]["campaign"]["jobs"][0][
            "n_errored_trials"
        ] = 1
        reidentify_assessment(incomplete_campaign)
        with self.assertRaisesRegex(ValueError, "incomplete or errored"):
            project_candidate_assessment_diagnostics(incomplete_campaign)

    def test_rejects_conflicting_candidate_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            assessment, _, _, _ = self.assessment(Path(directory))
        changed = copy.deepcopy(assessment)
        changed["identities"]["candidate_source_tree"] = (
            "git-tree-sha1:" + "d" * 40
        )
        unsigned = {key: value for key, value in changed.items() if key != "assessment_identity"}
        changed["assessment_identity"] = digest(unsigned)
        with self.assertRaisesRegex(ValueError, "candidate campaign conflicts"):
            project_candidate_assessment_diagnostics(changed)
        conflicting_diagnosis = copy.deepcopy(assessment)
        conflicting_diagnosis["prior_diagnosis"]["intervention"] = (
            "Replace the identity-bound diagnosis without changing its source log."
        )
        reidentify_assessment(conflicting_diagnosis)
        with self.assertRaisesRegex(ValueError, "source-producing prior typed diagnosis"):
            project_candidate_assessment_diagnostics(conflicting_diagnosis)

    def test_rejects_ambiguous_truncated_and_oversized_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            assessment, _, _, _ = self.assessment(Path(directory))
        ambiguous = copy.deepcopy(assessment)
        ambiguous_trial = ambiguous["evaluations"]["candidate"]["trials"][1]
        report = json.loads(ambiguous_trial["raw_verifier_report"])
        report["results"]["tests"][0]["trace"] += "> assert another == expression\n"
        ambiguous_trial["raw_verifier_report"] = json.dumps(report)
        ambiguous_trial["diagnostics"]["verifier_feedback"] = (
            verifier_feedback_from_bytes(
                ambiguous_trial["raw_verifier_report"].encode("utf-8")
            )
        )
        reidentify_assessment(ambiguous)
        with self.assertRaisesRegex(ValueError, "missing, ambiguous"):
            project_candidate_assessment_diagnostics(ambiguous)
        truncated = copy.deepcopy(assessment)
        truncated["evaluations"]["candidate"]["trials"][1]["diagnostics"][
            "verification_timeline"
        ][0]["results"][0]["truncated"] = True
        reidentify_assessment(truncated)
        with self.assertRaisesRegex(ValueError, "truncated final validation"):
            project_candidate_assessment_diagnostics(truncated)
        missing_timeline = copy.deepcopy(assessment)
        missing_timeline["evaluations"]["candidate"]["trials"][1]["diagnostics"][
            "episodes"
        ].append(
            {
                "episode_id": "ep-omitted-child",
                "parent_id": "ep-candidate-failure",
                "program": "private-child-program",
                "model": "private-model-name",
                "model_calls": 1,
                "tool_results": 1,
                "outcome": {"kind": "completed"},
            }
        )
        reidentify_assessment(missing_timeline)
        with self.assertRaisesRegex(ValueError, "conflicting final validation"):
            project_candidate_assessment_diagnostics(missing_timeline)
        projection = project_candidate_assessment_diagnostics(assessment)
        projection["prior_diagnosis"]["oversized_detail"] = "x" * MAX_DIAGNOSTICS_BYTES
        projection["prior_diagnosis_sha256"] = digest(
            projection["prior_diagnosis"]
        )
        unsigned = {
            key: value for key, value in projection.items() if key != "diagnostics_identity"
        }
        projection["diagnostics_identity"] = digest(unsigned)
        with self.assertRaisesRegex(ValueError, "maximum"):
            validate_candidate_assessment_diagnostics(projection)

    def test_projection_validation_rejects_unstructured_prose_and_incomplete_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            assessment, _, _, _ = self.assessment(Path(directory))
        projection = project_candidate_assessment_diagnostics(assessment)
        exposed = copy.deepcopy(projection)
        exposed["assessment_contrast"]["failed_attempts"][0][
            "grader_prose"
        ] = PRIVATE_GRADER
        reidentify_projection(exposed)
        with self.assertRaisesRegex(ValueError, "unknown or missing fields"):
            validate_candidate_assessment_diagnostics(exposed)
        incomplete = copy.deepcopy(projection)
        incomplete["assessment_contrast"]["failed_attempts"][0][
            "final_validation_timelines"
        ][0]["omitted_results"] = 1
        reidentify_projection(incomplete)
        with self.assertRaisesRegex(ValueError, "incomplete bounded validation window"):
            validate_candidate_assessment_diagnostics(incomplete)
        oversized_report = copy.deepcopy(projection)
        oversized_report["assessment_contrast"]["failed_attempts"][0][
            "terminal_audit_report"
        ]["summary"] = "x" * (MAX_TERMINAL_AUDIT_TEXT + 1)
        reidentify_projection(oversized_report)
        with self.assertRaisesRegex(ValueError, "terminal audit text bound"):
            validate_candidate_assessment_diagnostics(oversized_report)
        omitted_citation = copy.deepcopy(projection)
        omitted_citation["assessment_contrast"]["failed_attempts"][0][
            "terminal_audit_report"
        ]["learned"][0]["seq"] = 999
        reidentify_projection(omitted_citation)
        with self.assertRaisesRegex(ValueError, "omitted validation result"):
            validate_candidate_assessment_diagnostics(omitted_citation)

        bounded = copy.deepcopy(assessment)
        timeline = bounded["evaluations"]["candidate"]["trials"][1][
            "diagnostics"
        ]["verification_timeline"][0]
        while len(timeline["results"]) < MAX_VERIFICATION_RESULTS:
            result = copy.deepcopy(timeline["results"][-1])
            result["seq"] += len(timeline["results"])
            result["step"] += len(timeline["results"])
            result["call_id"] += f"-{len(timeline['results'])}"
            timeline["results"].append(result)
        timeline["omitted_results"] = 3
        reidentify_assessment(bounded)
        retained = project_candidate_assessment_diagnostics(bounded)
        self.assertEqual(
            retained["assessment_contrast"]["failed_attempts"][0][
                "final_validation_timelines"
            ][0]["omitted_results"],
            3,
        )

    def test_rejects_symlinked_bundle_entries_and_escaped_campaign_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, _, _, _, _ = write_source_bundle(root)
            (bundle / "unexpected-link").symlink_to(bundle / "candidate-check")
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                create_source_candidate_assessment(
                    bundle, root / "missing-parent", root / "missing-candidate"
                )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assessment, _, _, _ = self.assessment(root)
            campaign_path = root / "parent-campaign/campaign.json"
            campaign = json.loads(campaign_path.read_text())
            campaign["jobs"][0]["result"] = "../escaped.json"
            write_json(campaign_path, campaign)
            with self.assertRaisesRegex(ValueError, "confined relative path"):
                create_source_candidate_assessment(
                    root / "source-bundle",
                    root / "parent-campaign",
                    root / "candidate-campaign",
                )
            self.assertIsInstance(assessment, dict)

    def test_revised_diagnosis_cites_complete_contrast_and_binding_stays_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assessment, rejected_identity, _, generated_source = self.assessment(root)
            projection = project_candidate_assessment_diagnostics(assessment)
            contrast = projection["assessment_contrast"]
            failure = contrast["failed_attempts"][0]
            diagnosis = {
                "branch": "implement-source",
                "assessment_revision": {
                    "assessment_contrast_sha256": projection[
                        "assessment_contrast_sha256"
                    ],
                    "rejected_source_candidate_identity": rejected_identity,
                    "prior_diagnosis_sha256": projection["prior_diagnosis_sha256"],
                    "disposition": "replace",
                    "failed_attempts": [
                        {
                            "episode_id": failure["episode_id"],
                            "verifier_report_sha256s": [
                                row["verifier_report_sha256"]
                                for row in failure["failed_verifiers"]
                            ],
                            "locus_sha256s": [
                                locus["locus_sha256"]
                                for verifier in failure["failed_verifiers"]
                                for locus in verifier["failure_loci"]
                            ],
                        }
                    ],
                    "parent_success_episode_ids": [
                        row["episode_id"]
                        for row in contrast["success_references"]["parent"]
                    ],
                    "candidate_success_episode_ids": [
                        row["episode_id"]
                        for row in contrast["success_references"]["candidate"]
                    ],
                    "explanation": "The external contrast falsifies the prior mechanism.",
                },
            }
            validate_revised_diagnosis(diagnosis, projection)
            with self.assertRaisesRegex(ValueError, "repeats the externally rejected"):
                require_novel_source_candidate(
                    {"source_candidate_identity": rejected_identity}, projection
                )
            require_novel_source_candidate(
                {"source_candidate_identity": "sha256:" + "9" * 64}, projection
            )
            context = generation_context(
                projection, diagnosis, SHA_THREE, CANDIDATE_TREE
            )
            evidence = root / "new-source-bundle"
            evidence.mkdir()
            bind_generation_evidence(evidence, projection, context)
            retained = b"".join(path.read_bytes() for path in sorted(evidence.iterdir()))
        self.assertTrue((canonical_json(projection)))
        self.assertIn(rejected_identity.encode(), retained)
        for planted in (PRIVATE_TASK, PRIVATE_GRADER, PRIVATE_CAMPAIGN):
            self.assertNotIn(planted.encode(), retained)
            self.assertNotIn(planted.encode(), generated_source)
        self.assertTrue((evidence / ASSESSMENT_DIAGNOSTICS_FILE).name)
        self.assertTrue((evidence / GENERATION_CONTEXT_FILE).name)
        self.assertEqual(context["schema_version"], 2)
        self.assertEqual(context["assessed_parent_source_tree"], PARENT_TREE)
        self.assertEqual(context["generation_parent_source_tree"], CANDIDATE_TREE)

    def test_revised_diagnosis_cannot_copy_failure_details_into_the_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            assessment, rejected_identity, _, _ = self.assessment(Path(directory))
            projection = project_candidate_assessment_diagnostics(assessment)
        contrast = projection["assessment_contrast"]
        failure = contrast["failed_attempts"][0]
        locus = failure["failed_verifiers"][0]["failure_loci"][0]
        for field in ("assertion", "observed_assertion"):
            diagnosis = {
                "branch": "implement-source",
                "intervention": locus[field],
                "assessment_revision": {
                    "assessment_contrast_sha256": projection[
                        "assessment_contrast_sha256"
                    ],
                    "rejected_source_candidate_identity": rejected_identity,
                    "prior_diagnosis_sha256": projection["prior_diagnosis_sha256"],
                    "disposition": "replace",
                    "failed_attempts": [
                        {
                            "episode_id": failure["episode_id"],
                            "verifier_report_sha256s": [
                                row["verifier_report_sha256"]
                                for row in failure["failed_verifiers"]
                            ],
                            "locus_sha256s": [
                                item["locus_sha256"]
                                for row in failure["failed_verifiers"]
                                for item in row["failure_loci"]
                            ],
                        }
                    ],
                    "parent_success_episode_ids": [
                        row["episode_id"]
                        for row in contrast["success_references"]["parent"]
                    ],
                    "candidate_success_episode_ids": [
                        row["episode_id"]
                        for row in contrast["success_references"]["candidate"]
                    ],
                    "explanation": "The assessment supports a general source change.",
                },
            }
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    ValueError,
                    r"field `\$\.intervention` copies a task-specific assessment detail",
                ):
                    validate_revised_diagnosis(diagnosis, projection)

        diagnosis["intervention"] = "Enforce one general completion invariant."
        diagnosis["assessment_revision"]["explanation"] = (
            "The assessment observed " + locus["observed_assertion"]
        )
        with self.assertRaisesRegex(
            ValueError,
            r"field `\$\.assessment_revision\.explanation` copies a task-specific assessment detail",
        ):
            validate_revised_diagnosis(diagnosis, projection)

    def test_source_candidate_cannot_embed_assessment_details(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assessment, _, _, _ = self.assessment(root)
            projection = project_candidate_assessment_diagnostics(assessment)
            candidate = root / "candidate"
            candidate.mkdir()
            changed = candidate / "src.rs"
            changed.write_text(
                f'const ASSESSMENT: &str = "{projection["diagnostics_identity"]}";\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "evaluator-owned assessment"):
                require_source_candidate_excludes_assessment_literals(
                    candidate,
                    ["src.rs"],
                    projection,
                )
            changed.write_text(
                "pub fn assessment_guided_behavior() -> bool { true }\n",
                encoding="utf-8",
            )
            require_source_candidate_excludes_assessment_literals(
                candidate,
                ["src.rs"],
                projection,
            )

    def test_program_exposes_projection_only_to_the_existing_diagnosis_node(self):
        with tempfile.TemporaryDirectory() as directory:
            assessment, _, _, _ = self.assessment(Path(directory))
            projection = project_candidate_assessment_diagnostics(assessment)
            config = build_config(
                Path("/candidate"),
                Path("/private/trajectory-evidence.json"),
                Path("/controller/candidate-check"),
                Path("/controller/diagnosis-validator"),
                model_config(
                    "openai-codex/gpt-5.6-sol",
                    "low",
                    credential_home=Path("/credentials"),
                ),
                model_config(
                    "openai-codex/gpt-5.6-sol",
                    "low",
                    credential_home=Path("/credentials"),
                ),
                [Path("/toolchain")],
                [Path("/repository-metadata")],
                [Path("/cargo-cache")],
                "Improve general verified completion.",
                "source-change",
                projection,
            )
        nodes = config["workflow"]["nodes"]
        self.assertEqual(
            sorted(nodes),
            [
                "assess-finalized-runtime-improvement",
                "collect-trajectory-diagnostics",
                "diagnose-runtime",
                "finalize-runtime-improvement",
                "implement-runtime-improvement",
                "review-runtime-improvement",
            ],
        )
        diagnosis = nodes["diagnose-runtime"]["model"]
        implementation = nodes["implement-runtime-improvement"]["model"]
        review = nodes["review-runtime-improvement"]["model"]
        finalization = nodes["finalize-runtime-improvement"]["model"]
        self.assertIn("assessment_revision", diagnosis["done_when"]["returns"]["required"])
        self.assertIn(
            "one distinct general and falsifiable source hypothesis",
            diagnosis["instructions"]["candidate_assessment"],
        )
        self.assertIn(
            "External task quality decides",
            diagnosis["instructions"]["candidate_assessment"],
        )
        self.assertIn(
            "historical evidence of the rejected mechanism",
            diagnosis["instructions"]["candidate_assessment"],
        )
        self.assertEqual(
            nodes["implement-runtime-improvement"]["follows"],
            ["task", "diagnose-runtime"],
        )
        self.assertIn("empty", nodes["implement-runtime-improvement"])
        self.assertEqual(
            nodes["review-runtime-improvement"]["follows"],
            ["task", "diagnose-runtime", "implement-runtime-improvement"],
        )
        self.assertEqual(
            nodes["finalize-runtime-improvement"]["follows"],
            [
                "task",
                "diagnose-runtime",
                "implement-runtime-improvement",
                "review-runtime-improvement",
            ],
        )
        for model_input in (implementation, review, finalization, config["task"]):
            encoded = canonical_json(model_input)
            for planted in (PRIVATE_TASK, PRIVATE_GRADER, PRIVATE_CAMPAIGN):
                self.assertNotIn(planted.encode(), encoded)
        self.assertNotIn("/private", implementation["grants"]["read"])
        self.assertNotIn("/private", review["grants"]["read"])
        self.assertNotIn("/private", finalization["grants"]["read"])

    def test_generated_validator_loads_the_bounded_assessment_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assessment, _, _, _ = self.assessment(root)
            projection = project_candidate_assessment_diagnostics(assessment)
            validator = root / "diagnosis-validator"
            write_diagnosis_validator(
                validator,
                root / "program.json",
                {"source_tree": PARENT_TREE, "runtime_binary": SHA_ONE},
                SHA_TWO,
                {
                    "model": "openai-codex/gpt-5.6-sol",
                    "reasoning_effort": "low",
                    "service_tier": "priority",
                    "token_policy": "measurement_only",
                    "workflow_ownership": "evaluation-runner",
                    "completion_governance": "model-report",
                },
                [],
                [],
                "source-change",
                projection,
            )
            result = subprocess.run(
                [str(validator)],
                input="{}\n",
                text=True,
                capture_output=True,
                check=False,
            )
            generated_validator_source = validator.read_bytes()
        self.assertEqual(result.returncode, 0)
        self.assertIn("revised diagnosis omits assessment_revision", result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        for planted in (PRIVATE_TASK, PRIVATE_GRADER, PRIVATE_CAMPAIGN):
            self.assertNotIn(planted.encode(), generated_validator_source)

    def test_private_and_projected_assessment_files_stay_outside_coding_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coding = root / "candidate"
            coding.mkdir()
            private = root / "private-assessment.json"
            private.write_text("{}", encoding="utf-8")
            diagnostics = root / "diagnostics"
            diagnostics.mkdir()
            require_assessment_isolation(private, diagnostics, [coding])
            exposed_private = coding / "assessment.json"
            exposed_private.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "coding read grant"):
                require_assessment_isolation(exposed_private, diagnostics, [coding])
            with self.assertRaisesRegex(ValueError, "diagnostics are reachable"):
                require_assessment_isolation(private, coding, [coding])


if __name__ == "__main__":
    unittest.main()
