#!/usr/bin/python3

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from run import Pricing
from run_self_improvement import (
    DIAGNOSIS_VALIDATOR_TOOL,
    adoption_state_document,
    build_config,
    candidate_outcome_value,
    candidate_artifact_identity,
    candidate_disposition,
    canonical_json,
    check_baseline,
    check_candidate,
    digest_bytes,
    failed_base_configuration,
    final_source_review_findings,
    find_accepted_verification,
    instruction_candidate_from_outcome,
    line_budget_ceilings,
    measure_episode,
    model_config,
    parser as self_improvement_parser,
    prepare_output_root,
    prepare_isolated_source,
    prepare_validation_directories,
    record_adoption,
    require_successful_adoption,
    revised_program_document,
    remove_preview_validation_directories,
    rust_toolchain_identity,
    source_review_branch_findings,
    source_review_resolution_findings,
    supported_independent_audits,
    supported_failure_contrasts,
    tool_candidate_from_outcome,
    validate_program,
    workflow_candidate_from_outcome,
    write_candidate_check,
    write_diagnosis_validator,
)
from instruction_candidate import create as create_instruction_candidate
from tool_candidate import create as create_tool_candidate
from tool_candidate import executable_digest
from workflow_candidate import create as create_workflow_candidate


class SelfImprovementConfigTest(unittest.TestCase):
    def test_source_program_carries_the_runtime_resolved_credential(self):
        retained_plan_model = {
            "model": "gpt-5.6-sol",
            "provider": "openai-codex",
            "reasoning_effort": "low",
            "service_tier": "default",
        }
        retained_start_model = {
            **retained_plan_model,
            "token_file": "/home/operator/.config/foe/credentials/openai-codex.json",
        }
        configured = model_config(
            "openai-codex/gpt-5.6-sol",
            "low",
            credential_home=Path("/home/operator"),
        )
        self.assertNotEqual(retained_plan_model, retained_start_model)
        self.assertEqual(configured, retained_start_model)

    def test_command_line_defaults_to_the_campaign_model_and_service(self):
        args = self_improvement_parser().parse_args(
            [
                "--foe",
                "/tmp/foe",
                "--candidate",
                "/tmp/candidate",
                "--evidence",
                "/tmp/evidence.json",
                "--cases",
                "/tmp/cases.json",
                "--bundle-builder",
                "/tmp/build-bundle",
                "--ancestry-checker",
                "/tmp/check-ancestry",
                "--source-checker",
                "/tmp/source-adoption",
            ]
        )
        self.assertEqual(args.model, "openai-codex/gpt-5.6-sol")
        self.assertEqual(args.reasoning_effort, "low")
        self.assertEqual(args.diagnosis_model, "openai-codex/gpt-5.6-sol")
        self.assertEqual(args.diagnosis_reasoning_effort, "low")
        self.assertEqual(args.service_tier, "default")
        self.assertIn("Task quality is the promotion metric", args.objective)

    def test_preview_does_not_create_the_requested_retained_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            requested = Path(directory) / "retained"
            root, temporary = prepare_output_root(requested, None, False)
            self.assertNotEqual(root, requested)
            self.assertFalse(requested.exists())
            self.assertIsNotNone(temporary)
            temporary.cleanup()

    def test_confirmed_run_creates_the_requested_retained_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            requested = Path(directory) / "retained"
            root, temporary = prepare_output_root(requested, None, True)
            self.assertEqual(root, requested)
            self.assertTrue(requested.is_dir())
            self.assertIsNone(temporary)

    def test_isolated_source_excludes_unrelated_repository_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=source, check=True)
            subprocess.run(
                ["git", "config", "user.name", "Foe Test"], cwd=source, check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "foe-test@example.invalid"],
                cwd=source,
                check=True,
            )
            (source / "source.txt").write_text("frozen\n", encoding="utf-8")
            subprocess.run(["git", "add", "source.txt"], cwd=source, check=True)
            subprocess.run(
                ["git", "commit", "--quiet", "-m", "Frozen source"],
                cwd=source,
                check=True,
            )
            frozen = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=source,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            frozen_tree = "git-tree-sha1:" + subprocess.run(
                ["git", "rev-parse", "HEAD^{tree}"],
                cwd=source,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            (source / "evaluator-checker.txt").write_text("private\n", encoding="utf-8")
            subprocess.run(["git", "add", "evaluator-checker.txt"], cwd=source, check=True)
            subprocess.run(
                ["git", "commit", "--quiet", "-m", "Evaluator checker"],
                cwd=source,
                check=True,
            )
            evaluator_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=source,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "branch", "evaluator-checker"], cwd=source, check=True
            )
            subprocess.run(
                ["git", "checkout", "--quiet", "--detach", frozen], cwd=source, check=True
            )

            isolated = prepare_isolated_source(source, root / "isolated", frozen_tree)
            all_commits = subprocess.run(
                ["git", "log", "--all", "--format=%H"],
                cwd=isolated,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.splitlines()
            common = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                cwd=isolated,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            evaluator_object = subprocess.run(
                ["git", "cat-file", "-e", evaluator_commit],
                cwd=isolated,
                capture_output=True,
                check=False,
            )

            self.assertIn(frozen, all_commits)
            self.assertNotIn(evaluator_commit, all_commits)
            self.assertNotEqual(evaluator_object.returncode, 0)
            self.assertFalse((isolated / "evaluator-checker.txt").exists())
            self.assertEqual((isolated / common).resolve(), (isolated / ".git").resolve())

    def test_validation_directories_exist_before_program_construction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cargo_target = prepare_validation_directories(root)
            self.assertTrue(cargo_target.is_dir())
            self.assertTrue((root / "target" / "test-scratch").is_dir())

    def test_preview_removes_validation_directories_it_created(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            candidate.mkdir()
            prepare_validation_directories(candidate)
            remove_preview_validation_directories(candidate, set())
            self.assertFalse((candidate / "target").exists())

    def test_rust_toolchain_identity_hashes_every_validation_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("cargo", "rustc", "rustfmt", "clippy-driver"):
                (root / name).write_text(name + "\n", encoding="utf-8")
            identity = rust_toolchain_identity(root / "cargo")
            (root / "rustc").write_text("changed\n", encoding="utf-8")
            changed = rust_toolchain_identity(root / "cargo")
        self.assertEqual(sorted(identity), ["cargo", "clippy-driver", "rustc", "rustfmt"])
        self.assertNotEqual(identity["rustc"], changed["rustc"])
        self.assertEqual(identity["cargo"], changed["cargo"])

    def test_candidate_artifact_identity_binds_base_and_changed_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changed = root / "crates/core/src/lib.rs"
            changed.parent.mkdir(parents=True)
            changed.write_text("one\n", encoding="utf-8")
            first = candidate_artifact_identity(
                root, "git-tree-sha1:" + "1" * 40, ["docs/deleted.md", "crates/core/src/lib.rs"]
            )
            changed.write_text("two\n", encoding="utf-8")
            second = candidate_artifact_identity(
                root, "git-tree-sha1:" + "1" * 40, ["docs/deleted.md", "crates/core/src/lib.rs"]
            )
        self.assertEqual(first["files"]["docs/deleted.md"], "absent")
        self.assertNotEqual(first["files"]["crates/core/src/lib.rs"], second["files"]["crates/core/src/lib.rs"])
        self.assertNotEqual(first["digest"], second["digest"])

    def test_lineage_adoption_failure_rejects_the_candidate(self):
        def fail():
            raise ValueError("injected bundle failure")

        adoption, acceptance = require_successful_adoption(
            {"accepted": True, "findings": [], "exit_code": 0}, fail
        )
        self.assertEqual(adoption, {"error": "injected bundle failure"})
        self.assertFalse(acceptance["accepted"])
        self.assertIn("lineage adoption failed: injected bundle failure", acceptance["findings"])
        direct_implementation_required, process_exit = candidate_disposition(acceptance, 0)
        self.assertTrue(direct_implementation_required)
        self.assertNotEqual(process_exit, 0)

    def test_workflow_uses_typed_handoff_and_a_full_coding_surface(self):
        root = Path("/tmp/candidate")
        evidence = Path("/tmp/evidence.json")
        config = build_config(
            root,
            evidence,
            Path("/tmp/check"),
            Path("/tmp/diagnosis-validator"),
            model_config(
                "openai-codex/gpt-5.6-terra",
                "high",
                "priority",
                credential_home=Path("/home/controller"),
            ),
            model_config(
                "openai-codex/gpt-5.6-luna",
                "high",
                "priority",
                credential_home=Path("/home/controller"),
            ),
            [Path("/opt/toolchain")],
            [Path("/repo/.git")],
            [Path("/opt/cargo-cache")],
            "Raise verified completion.",
            "auto",
        )
        nodes = config["workflow"]["nodes"]
        diagnosis = nodes["diagnose-runtime"]["model"]
        implementation = nodes["implement-runtime-improvement"]["model"]
        review = nodes["review-runtime-improvement"]["model"]
        finalization = nodes["finalize-runtime-improvement"]["model"]
        final_review = nodes["assess-finalized-runtime-improvement"]["model"]
        second_finalization = nodes["repair-after-final-source-assessment"]["model"]
        second_final_review = nodes["assess-repaired-runtime-improvement"]["model"]
        self.assertEqual(
            nodes["implement-runtime-improvement"]["follows"],
            ["task", "diagnose-runtime"],
        )
        self.assertIn(
            "without a completed implementation handoff",
            nodes["implement-runtime-improvement"]["empty"]["unresolved_risks"][0],
        )
        self.assertEqual(
            nodes["implement-runtime-improvement"]["empty"]["changed_paths"],
            [],
        )
        self.assertNotIn(
            "collect-trajectory-diagnostics",
            nodes["implement-runtime-improvement"]["follows"],
        )
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
        self.assertNotIn("terminal", nodes["finalize-runtime-improvement"])
        self.assertNotIn("terminal", nodes["assess-finalized-runtime-improvement"])
        self.assertEqual(
            nodes["assess-finalized-runtime-improvement"]["follows"],
            [
                "task",
                "diagnose-runtime",
                "implement-runtime-improvement",
                "review-runtime-improvement",
                "finalize-runtime-improvement",
            ],
        )
        self.assertEqual(
            nodes["assess-finalized-runtime-improvement"]["branches"],
            {
                "accept": [],
                "repair-source": ["repair-after-final-source-assessment"],
            },
        )
        self.assertEqual(
            nodes["repair-after-final-source-assessment"]["follows"],
            ["task", "diagnose-runtime", "assess-finalized-runtime-improvement"],
        )
        self.assertEqual(
            nodes["assess-repaired-runtime-improvement"]["follows"],
            ["task", "diagnose-runtime", "repair-after-final-source-assessment"],
        )
        self.assertTrue(nodes["assess-repaired-runtime-improvement"]["terminal"])
        self.assertIn("unresolved_risks", nodes["review-runtime-improvement"]["empty"])
        self.assertIn("findings", nodes["review-runtime-improvement"]["empty"])
        self.assertEqual(len(nodes["review-runtime-improvement"]["empty"]["findings"]), 1)
        self.assertIn(
            "did not complete",
            nodes["review-runtime-improvement"]["empty"]["findings"][0],
        )
        self.assertEqual(
            nodes["diagnose-runtime"]["branches"],
            {
                "implement-source": ["implement-runtime-improvement"],
                "configure-workflow": [],
                "insufficient-evidence": [],
            },
        )
        self.assertEqual(implementation["tools"][:4], ["read", "grep", "edit", "bash"])
        self.assertEqual(review["tools"], ["read", "grep", "bash", "check"])
        self.assertNotIn("edit", review["tools"])
        self.assertEqual(finalization["tools"][:4], ["read", "grep", "edit", "bash"])
        self.assertEqual(second_finalization["tools"][:4], ["read", "grep", "edit", "bash"])
        self.assertNotIn("edit", second_final_review["tools"])
        self.assertIn("build-script metadata", implementation["instructions"]["build_metadata"])
        self.assertIn("build-script metadata", review["instructions"]["build_metadata"])
        self.assertEqual(diagnosis["tools"], ["block", DIAGNOSIS_VALIDATOR_TOOL])
        self.assertEqual(diagnosis["done_when"]["verify"], DIAGNOSIS_VALIDATOR_TOOL)
        self.assertEqual(diagnosis["done_when"]["retries"], 2)
        self.assertEqual(
            diagnosis["tool_defs"][DIAGNOSIS_VALIDATOR_TOOL]["cwd"],
            str(evidence.parent),
        )
        self.assertEqual(
            diagnosis["tool_defs"][DIAGNOSIS_VALIDATOR_TOOL]["exec"], "/tmp/diagnosis-validator"
        )
        self.assertEqual(diagnosis["grants"]["read"], ["/tmp"])
        self.assertIn("block", config["tools"])
        self.assertIn(DIAGNOSIS_VALIDATOR_TOOL, config["tools"])
        self.assertEqual(
            config["tool_defs"][DIAGNOSIS_VALIDATOR_TOOL]["exec"],
            "/tmp/diagnosis-validator",
        )
        self.assertNotIn("input_tokens", config["budget"])
        self.assertNotIn("output_tokens", implementation["budget"])
        self.assertEqual(diagnosis["model"]["model"], "gpt-5.6-luna")
        self.assertIn("verified task quality", diagnosis["instructions"]["controls"])
        self.assertIn("resource changes", diagnosis["instructions"]["controls"])
        self.assertIn("general workflow setting", diagnosis["instructions"]["controls"])
        self.assertIn("observed_assertion", diagnosis["instructions"]["evidence"])
        self.assertIn("exercised by every failed configuration", diagnosis["instructions"]["evidence"])
        returns = diagnosis["done_when"]["returns"]
        self.assertEqual(
            returns["required"],
            [
                "limitation",
                "attribution",
                "causal_contrast",
                "intervention",
                "activation_path",
                "preserved_controls",
                "falsification_condition",
            ],
        )
        self.assertIn("falsification_condition", returns["required"])
        self.assertIn("failure_contrast_sha256", returns["properties"])
        causal = returns["properties"]["causal_contrast"]
        self.assertNotIn("minItems", causal["properties"]["failed"])
        self.assertNotIn("shared_mechanism", causal["required"])
        self.assertNotIn("required_paths", returns["properties"])
        self.assertNotIn("runtime_activation", returns["properties"])
        self.assertNotIn("implementation_files", returns["properties"])
        self.assertNotIn("model", implementation)
        self.assertIn("reasoning settings", implementation["instructions"]["independence"])
        self.assertIn("baseline-relative line budgets", implementation["instructions"]["validation"])
        self.assertIn("Treat the diagnosis as a hypothesis", implementation["instructions"]["validation"])
        self.assertIn("source lifecycle", review["instructions"]["evidence"])
        self.assertIn("workflow settlement", review["instructions"]["architecture"])
        self.assertIn("without editing", review["instructions"]["validation"])
        self.assertEqual(review["model"]["reasoning_effort"], "xhigh")
        self.assertEqual(review["model"]["service_tier"], "priority")
        self.assertEqual(
            review["done_when"]["returns"]["required"],
            ["summary", "findings", "validation", "unresolved_risks"],
        )
        self.assertEqual(finalization["done_when"]["verify"], "check")
        self.assertEqual(finalization["done_when"]["retries"], 4)
        finalization_returns = finalization["done_when"]["returns"]
        self.assertEqual(
            finalization_returns["required"],
            ["summary", "review_findings", "validation", "unresolved_risks"],
        )
        resolution = finalization_returns["properties"]["review_findings"]["items"]
        self.assertEqual(
            resolution["required"], ["finding", "disposition", "explanation"]
        )
        self.assertEqual(
            resolution["properties"]["disposition"]["enum"], ["fixed", "unresolved"]
        )
        self.assertIn("Run the candidate check first", finalization["instructions"]["correction"])
        self.assertIn("every independent-review finding", finalization["instructions"]["correction"])
        self.assertIn("rejects missing", finalization["instructions"]["correction"])
        self.assertEqual(
            implementation["done_when"]["returns"]["required"],
            ["summary", "changed_paths", "validation", "unresolved_risks"],
        )
        self.assertEqual(config["model"]["reasoning_effort"], "high")
        self.assertEqual(config["model"]["service_tier"], "priority")
        self.assertEqual(diagnosis["model"]["service_tier"], "priority")
        self.assertEqual(finalization["model"]["service_tier"], "priority")
        self.assertEqual(config["version"], 3)
        self.assertEqual(
            diagnosis["budget"],
            {"model_calls": 20, "seconds": 1800, "loop_threshold": 8},
        )
        self.assertIn("four model requests as a planning target", diagnosis["instructions"]["result"])
        self.assertIn("do not call that tool directly", diagnosis["instructions"]["result"])
        self.assertIn("loop backstop", diagnosis["instructions"]["result"])
        self.assertIn("model capability", diagnosis["instructions"]["sufficiency"])
        self.assertIn("configure-workflow", diagnosis["instructions"]["sufficiency"])
        self.assertIn("independent_audit", returns["properties"])
        self.assertIn("instruction_revision", returns["properties"])
        self.assertIn("tool_definition", returns["properties"])
        self.assertNotIn("instruction_revision", returns["required"])
        self.assertNotIn("tool_definition", returns["required"])
        self.assertIn("require application support", diagnosis["instructions"]["sufficiency"])
        self.assertIn("must not branch on", diagnosis["instructions"]["controls"])
        self.assertEqual(
            implementation["grants"]["write"],
            [
                *(str(root / directory) for directory in ("crates", "docs", "examples")),
                str(root / "target" / "foe-self-improvement-check"),
                str(root / "target" / "test-scratch"),
            ],
        )
        self.assertEqual(
            implementation["grants"]["read"],
            [str(root), "/repo/.git", "/opt/cargo-cache"],
        )
        self.assertEqual(implementation["grants"]["execute"], ["/opt/toolchain"])
        self.assertEqual(config["task"], "Raise verified completion.")
        self.assertEqual(config["budget"]["loop_threshold"], 8)
        self.assertEqual(config["budget"]["model_calls"], 340)
        self.assertEqual(config["budget"]["max_episodes"], 8)
        self.assertEqual(
            config["budget"]["max_episodes"],
            1
            + sum(
                "model" in node
                for node in config["workflow"]["nodes"].values()
            ),
        )
        self.assertEqual(implementation["budget"]["model_calls"], 60)
        self.assertEqual(review["budget"]["model_calls"], 20)
        self.assertEqual(finalization["budget"]["model_calls"], 100)
        self.assertEqual(second_finalization["budget"]["model_calls"], 100)
        self.assertEqual(final_review["budget"]["model_calls"], 20)
        self.assertEqual(implementation["budget"]["loop_threshold"], 8)
        self.assertEqual(
            review["grants"]["write"],
            [
                str(root / "target" / "foe-self-improvement-check"),
                str(root / "target" / "test-scratch"),
            ],
        )
        self.assertEqual(finalization["grants"]["write"], implementation["grants"]["write"])
        self.assertEqual(final_review["grants"]["write"], review["grants"]["write"])
        credential = "/home/controller/.config/foe/credentials/openai-codex.json"
        self.assertEqual(config["model"]["token_file"], credential)
        self.assertEqual(diagnosis["model"]["token_file"], credential)
        self.assertEqual(review["model"]["token_file"], credential)
        self.assertEqual(finalization["model"]["token_file"], credential)
        self.assertEqual(final_review["model"]["token_file"], credential)

    def test_source_candidate_is_a_falsifiable_hypothesis_until_external_evaluation(self):
        config = build_config(
            Path("/tmp/candidate"),
            Path("/tmp/evidence.json"),
            Path("/tmp/check"),
            Path("/tmp/diagnosis-validator"),
            model_config("openai-codex/gpt-5.6-sol", "low"),
            model_config("openai-codex/gpt-5.6-luna", "low"),
            [Path("/opt/toolchain")],
            [Path("/repo/.git")],
            [Path("/opt/cargo-cache")],
            "Promote a verified intervention into built-in behavior.",
            "source-change",
        )
        sufficiency = config["workflow"]["nodes"]["diagnose-runtime"]["model"][
            "instructions"
        ]["sufficiency"]
        self.assertIn("general, source-owned, falsifiable runtime mechanism", sufficiency)
        self.assertIn("semantic task-quality error", sufficiency)
        self.assertIn("false acceptance by a built-in terminal audit", sufficiency)
        self.assertIn("does not require prior proof of transfer", sufficiency)
        self.assertIn("external task evaluation decides promotion", sufficiency)
        self.assertIn("Choose `insufficient-evidence`", sufficiency)
        self.assertIn("Do not choose `configure-workflow`", sufficiency)

    def test_diagnosis_value_survives_a_blocked_terminal_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = [
                {
                    "seq": 1,
                    "type": "workflow/node-end",
                    "data": {
                        "node": "diagnose-runtime",
                        "value": {"branch": "implement-source", "intervention": "change source"},
                    },
                },
                {
                    "seq": 2,
                    "type": "workflow/node-end",
                    "data": {
                        "node": "implement-runtime-improvement",
                        "value": None,
                        "error": "verification failed",
                    },
                },
                {
                    "seq": 3,
                    "type": "episode/end",
                    "data": {"outcome": {"kind": "blocked", "code": "verification-unsatisfiable"}},
                },
            ]
            (root / "episode.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
            )
            value = candidate_outcome_value(
                root,
                {"kind": "completed", "value": {"summary": "audit completed"}},
            )
        self.assertEqual(value["branch"], "implement-source")
        self.assertEqual(value["intervention"], "change source")

    def test_source_finalization_must_resolve_each_review_finding(self):
        finding = "The source change does not enforce the claimed runtime behavior."
        review = {"findings": [finding]}
        self.assertEqual(
            source_review_resolution_findings(review, None),
            ["source finalization returned no typed value"],
        )
        fixed = {
            "review_findings": [
                {
                    "finding": finding,
                    "disposition": "fixed",
                    "explanation": "The runtime rejects the invalid state and the regression covers it.",
                }
            ]
        }
        self.assertEqual(source_review_resolution_findings(review, fixed), [])

        unresolved = {
            "review_findings": [
                {
                    "finding": finding,
                    "disposition": "unresolved",
                    "explanation": "The candidate check cannot prove the semantic behavior.",
                }
            ]
        }
        self.assertEqual(
            source_review_resolution_findings(review, unresolved),
            [f"source finalization left review finding unresolved: {finding}"],
        )

    def test_source_finalization_rejects_missing_duplicate_and_unexpected_findings(self):
        first = "Repair the completion gate."
        second = "Add a behavioral regression."
        finalization = {
            "review_findings": [
                {"finding": first, "disposition": "fixed", "explanation": "done"},
                {"finding": first, "disposition": "fixed", "explanation": "duplicate"},
                {"finding": "Unreviewed claim.", "disposition": "fixed", "explanation": "done"},
            ]
        }
        findings = source_review_resolution_findings(
            {"findings": [first, second]}, finalization
        )
        self.assertIn(f"source finalization duplicated review finding: {first}", findings)
        self.assertIn(f"source finalization omitted review finding: {second}", findings)
        self.assertIn(
            "source finalization reported an unexpected review finding: Unreviewed claim.",
            findings,
        )

    def test_final_source_review_must_return_no_findings_or_unresolved_risks(self):
        self.assertEqual(
            final_source_review_findings(None),
            ["the final independent source review returned no typed value"],
        )
        self.assertEqual(
            final_source_review_findings(
                {"findings": [], "unresolved_risks": []}
            ),
            [],
        )
        self.assertEqual(
            final_source_review_findings(
                {
                    "findings": ["The repaired mechanism is still advisory."],
                    "unresolved_risks": [
                        "The regression does not exercise settlement."
                    ],
                }
            ),
            [
                "final independent source review finding: The repaired mechanism is still advisory.",
                "final independent source review risk: The regression does not exercise settlement.",
            ],
        )

    def test_first_final_source_review_routes_findings_to_one_more_repair(self):
        self.assertEqual(
            source_review_branch_findings(
                {"branch": "accept", "findings": [], "unresolved_risks": []}
            ),
            [],
        )
        self.assertEqual(
            source_review_branch_findings(
                {
                    "branch": "repair-source",
                    "findings": ["The final state can still change."],
                    "unresolved_risks": [],
                }
            ),
            [],
        )
        self.assertIn(
            "accepted a candidate with findings",
            source_review_branch_findings(
                {
                    "branch": "accept",
                    "findings": ["The final state can still change."],
                    "unresolved_risks": [],
                }
            )[0],
        )
        self.assertIn(
            "requested repair without a finding",
            source_review_branch_findings(
                {"branch": "repair-source", "findings": [], "unresolved_risks": []}
            )[0],
        )

    def test_failed_base_configuration_excludes_the_successful_audit_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            evidence.write_text(
                json.dumps(
                    {
                        "evaluation_summary": [
                            {
                                "task": "activation-task",
                                "attempts": 3,
                                "verified_successes": 0,
                                "execution_configuration": {
                                    "implementation": {
                                        "model": "openai-codex/gpt-5.6-sol",
                                        "reasoning_effort": "low",
                                    },
                                    "service_tier": "default",
                                    "token_policy": "measurement_only",
                                },
                            },
                            {
                                "task": "activation-task",
                                "attempts": 2,
                                "verified_successes": 2,
                                "execution_configuration": {
                                    "implementation": {
                                        "model": "openai-codex/gpt-5.6-sol",
                                        "reasoning_effort": "low",
                                    },
                                    "independent_audit": {
                                        "model": "openai-codex/gpt-5.6-sol",
                                        "reasoning_effort": "high",
                                        "model_calls": 60,
                                    },
                                    "service_tier": "default",
                                    "token_policy": "measurement_only",
                                },
                            },
                            {
                                "task": "transfer-task",
                                "attempts": 2,
                                "verified_successes": 2,
                                "execution_configuration": {
                                    "implementation": {
                                        "model": "openai-codex/gpt-5.6-sol",
                                        "reasoning_effort": "low",
                                    },
                                    "independent_audit": {
                                        "model": "openai-codex/gpt-5.6-sol",
                                        "reasoning_effort": "xhigh",
                                        "model_calls": 80,
                                    },
                                    "service_tier": "default",
                                    "token_policy": "measurement_only",
                                },
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            configuration = failed_base_configuration(evidence)
            audits = supported_independent_audits(evidence, configuration)
        self.assertEqual(
            configuration,
            {
                "model": "openai-codex/gpt-5.6-sol",
                "reasoning_effort": "low",
                "service_tier": "default",
                "token_policy": "measurement_only",
                "workflow_ownership": "evaluation-runner",
                "completion_governance": "model-report",
            },
        )
        self.assertEqual(audits, [{"reasoning_effort": "high", "model_calls": 60}])

    def test_source_diagnosis_does_not_require_an_independent_audit_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            evidence.write_text(
                json.dumps(
                    {
                        "evaluation_summary": [
                            {
                                "task": "activation-task",
                                "attempts": 1,
                                "verified_successes": 0,
                                "execution_configuration": {
                                    "implementation": {
                                        "model": "openai-codex/gpt-5.6-sol",
                                        "reasoning_effort": "low",
                                    },
                                    "service_tier": "priority",
                                    "token_policy": "measurement_only",
                                },
                            },
                            {
                                "task": "transfer-task",
                                "attempts": 1,
                                "verified_successes": 1,
                                "execution_configuration": {
                                    "implementation": {
                                        "model": "openai-codex/gpt-5.6-sol",
                                        "reasoning_effort": "xhigh",
                                    },
                                    "service_tier": "priority",
                                    "token_policy": "measurement_only",
                                },
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            configuration = failed_base_configuration(evidence)
            audits = supported_independent_audits(evidence, configuration)
        self.assertEqual(configuration["reasoning_effort"], "low")
        self.assertEqual(audits, [])

    def test_workflow_candidate_uses_the_only_observed_successful_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            evidence.write_text("{}\n", encoding="utf-8")
            identity = {
                "source_tree": "git-tree-sha1:" + "1" * 40,
                "runtime_binary": "sha256:" + "2" * 64,
            }
            base = {
                "model": "openai-codex/gpt-5.6-sol",
                "reasoning_effort": "low",
                "service_tier": "default",
                "token_policy": "measurement_only",
                "workflow_ownership": "evaluation-runner",
                "completion_governance": "model-report",
            }
            supported = [{"reasoning_effort": "high", "model_calls": 60}]
            candidate = workflow_candidate_from_outcome(
                {"branch": "configure-workflow"},
                supported,
                identity,
                evidence,
                base,
            )
            with self.assertRaisesRegex(ValueError, "exactly one repeated successful"):
                workflow_candidate_from_outcome(
                    {"branch": "configure-workflow"},
                    [*supported, {"reasoning_effort": "xhigh", "model_calls": 120}],
                    identity,
                    evidence,
                    base,
                )
        self.assertEqual(candidate["independent_audit"], supported[0])

    def test_instruction_candidate_binds_a_unique_revision_of_the_program_document(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            evidence.write_text("{}\n", encoding="utf-8")
            identity = {
                "source_tree": "git-tree-sha1:" + "1" * 40,
                "runtime_binary": "sha256:" + "2" * 64,
            }
            base = {
                "model": "openai-codex/gpt-5.6-sol",
                "reasoning_effort": "low",
                "service_tier": "default",
                "token_policy": "measurement_only",
                "workflow_ownership": "evaluation-runner",
                "completion_governance": "model-report",
            }
            documents = {
                "program.json": {
                    "instructions": {"role": "Run the workflow."},
                    "workflow": {
                        "nodes": {
                            "diagnose": {
                                "model": {"instructions": {"sufficiency": "Prefer bounded evidence."}}
                            }
                        }
                    },
                }
            }
            revision = {
                "document": "program.json",
                "section": "sufficiency",
                "old_text": "bounded evidence",
                "new_text": "bounded, labeled evidence",
            }
            candidate = instruction_candidate_from_outcome(
                {"branch": "revise-instructions", "instruction_revision": revision},
                documents,
                identity,
                evidence,
                base,
            )
            with self.assertRaisesRegex(ValueError, "exactly once"):
                instruction_candidate_from_outcome(
                    {
                        "branch": "revise-instructions",
                        "instruction_revision": {**revision, "old_text": "absent text"},
                    },
                    documents,
                    identity,
                    evidence,
                    base,
                )
            with self.assertRaisesRegex(ValueError, "did not select"):
                instruction_candidate_from_outcome(
                    {"branch": "configure-workflow"}, documents, identity, evidence, base
                )
        self.assertEqual(candidate["candidate_kind"], "instruction-revision")
        self.assertEqual(candidate["revision"]["new_text"], "bounded, labeled evidence")

    def test_tool_candidate_binds_the_executable_content_by_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            evidence.write_text("{}\n", encoding="utf-8")
            identity = {
                "source_tree": "git-tree-sha1:" + "1" * 40,
                "runtime_binary": "sha256:" + "2" * 64,
            }
            base = {
                "model": "openai-codex/gpt-5.6-sol",
                "reasoning_effort": "low",
                "service_tier": "default",
                "token_policy": "measurement_only",
                "workflow_ownership": "evaluation-runner",
                "completion_governance": "model-report",
            }
            executable = "#!/bin/sh\nexit 0\n"
            definition = {
                "name": "check-layout",
                "description": "Verify the workspace layout.",
                "executable": executable,
                "executable_sha256": executable_digest(executable.encode()),
            }
            candidate, content = tool_candidate_from_outcome(
                {"branch": "define-tool", "tool_definition": definition},
                identity,
                evidence,
                base,
            )
            with self.assertRaisesRegex(ValueError, "does not match the executable content"):
                tool_candidate_from_outcome(
                    {
                        "branch": "define-tool",
                        "tool_definition": {**definition, "executable_sha256": "sha256:" + "0" * 64},
                    },
                    identity,
                    evidence,
                    base,
                )
        self.assertEqual(content, executable)
        self.assertEqual(candidate["candidate_kind"], "tool-definition")
        self.assertNotIn("executable", candidate["tool"])
        self.assertEqual(candidate["tool"]["executable_sha256"], executable_digest(executable.encode()))

    def test_program_validation_reports_construction_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / "program.json"
            program.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "generated self-improvement program is invalid"):
                validate_program(Path("/bin/false"), program)

    def test_candidate_check_preserves_line_overages_and_rejects_build_metadata_bypasses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            for directory_name in ("crates/core/src", "docs", "scripts"):
                (candidate / directory_name).mkdir(parents=True, exist_ok=True)
            implementation = candidate / "crates/core/src/lib.rs"
            regression = candidate / "crates/core/src/lib_test.rs"
            specification = candidate / "docs/design.md"
            implementation.write_text("pub fn value() -> u8 { 1 }\n", encoding="utf-8")
            regression.write_text("#[test]\nfn value_is_one() {}\n", encoding="utf-8")
            specification.write_text(
                "The value is one. Existing terminal-bench/reference.\n",
                encoding="utf-8",
            )
            manifest = candidate / "Cargo.toml"
            lock = candidate / "Cargo.lock"
            manifest.write_text("[workspace]\nmembers = []\n", encoding="utf-8")
            lock.write_text("version = 4\n", encoding="utf-8")
            loc = candidate / "scripts/loc.sh"
            loc.write_text("#!/bin/sh\nprintf 'cli 2 (budget 1)\\n'\nexit 1\n", encoding="utf-8")
            subprocess.run(["git", "init", "--quiet", str(candidate)], check=True)
            subprocess.run(["git", "-C", str(candidate), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(candidate),
                    "-c",
                    "user.name=Foe Test",
                    "-c",
                    "user.email=foe@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "Create candidate",
                ],
                check=True,
            )
            toolchain = root / "toolchain/bin"
            toolchain.mkdir(parents=True)
            cargo = toolchain / "cargo"
            calls = root / "cargo-calls"
            cargo.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {calls}\nexit 0\n", encoding="utf-8")
            cargo.chmod(0o755)
            cargo_home = root / "cargo-home"
            cargo_home.mkdir()
            cargo_target = candidate / "target/check"
            cargo_target.mkdir(parents=True)
            check = root / "candidate-check"
            write_candidate_check(check, candidate, cargo, cargo_home, cargo_target)
            ceilings = line_budget_ceilings(candidate)
            baseline = check_baseline(check, candidate)
            implementation.write_text("pub fn value() -> u8 { 2 }\n", encoding="utf-8")
            regression.write_text("#[test]\nfn value_is_two() {}\n", encoding="utf-8")
            specification.write_text(
                "The value is two. Existing terminal-bench/reference.\n",
                encoding="utf-8",
            )
            accepted = check_candidate(check, candidate)
            sandboxed = subprocess.run(
                [str(check)], text=True, capture_output=True, check=True
            )
            implementation.unlink()
            deleted_implementation = check_candidate(check, candidate)
            implementation.write_text("pub fn value() -> u8 { 2 }\n", encoding="utf-8")
            specification.unlink()
            deleted_specification = check_candidate(check, candidate)
            specification.write_text(
                "The value is two. Existing terminal-bench/reference.\n",
                encoding="utf-8",
            )
            regression.unlink()
            deleted_test = check_candidate(check, candidate)
            regression.symlink_to(implementation)
            symlink_test = check_candidate(check, candidate)
            regression.unlink()
            regression.write_text("#[test]\nfn value_is_one() {}\n", encoding="utf-8")
            renamed_test = regression.with_name("renamed_test.rs")
            regression.rename(renamed_test)
            renamed_unchanged_test = check_candidate(check, candidate)
            renamed_test.rename(regression)
            regression.write_text("#[test]\nfn value_is_two() {}\n", encoding="utf-8")
            cargo_calls = calls.read_text(encoding="utf-8").splitlines()
            manifest.write_text("[workspace]\nmembers = [\"dummy\"]\n", encoding="utf-8")
            lock.write_text("version = 4\n", encoding="utf-8")
            dummy_workspace = check_candidate(check, candidate)
            dummy_workspace_calls = calls.read_text(encoding="utf-8").splitlines()
            manifest.write_text("[workspace]\nmembers = []\n", encoding="utf-8")
            cargo.write_text(
                f"#!/bin/sh\nprintf 'rewritten by validation\\n' > {lock}\nexit 0\n",
                encoding="utf-8",
            )
            cargo_rewrite = check_candidate(check, candidate)
        self.assertEqual(ceilings, {"cli": 2})
        self.assertTrue(baseline["accepted"], baseline)
        self.assertTrue(accepted["accepted"], accepted)
        self.assertEqual(sandboxed.stdout, "\n")
        self.assertEqual(len(cargo_calls), 10)
        self.assertEqual(cargo_calls[1], "test --workspace")
        self.assertEqual(
            cargo_calls[7],
            "test --workspace --exclude foe --exclude foe-transport --exclude foe-view "
            "-- --skip sandbox::tests:: "
            "--skip session::tests::a_session_serves_a_granted_bind_port_across_calls",
        )
        self.assertEqual(cargo_calls[8], "test -p foe --bin foe -- --skip login::tests::")
        for rejected, finding in (
            (deleted_implementation, "no Rust implementation change"),
            (deleted_specification, "does not update an affected specification"),
            (deleted_test, "no Rust regression test"),
            (symlink_test, "no Rust regression test"),
            (renamed_unchanged_test, "no Rust regression test"),
        ):
            self.assertFalse(rejected["accepted"])
            self.assertIn(finding, " ".join(rejected["findings"]))
        self.assertFalse(dummy_workspace["accepted"])
        self.assertEqual(dummy_workspace_calls, cargo_calls)
        self.assertIn(
            "cannot change build metadata: Cargo.toml",
            " ".join(dummy_workspace["findings"]),
        )
        self.assertFalse(cargo_rewrite["accepted"])
        self.assertIn(
            "validation changed the source tree or build metadata",
            " ".join(cargo_rewrite["findings"]),
        )

    def test_episode_measurement_prices_each_model_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "children" / "ep_child"
            child.mkdir(parents=True)
            events = [
                {
                    "type": "episode/start",
                    "data": {
                        "program": {
                            "model": {
                                "provider": "openai-codex",
                                "model": "gpt-5.6-luna",
                            }
                        }
                    },
                },
                {"type": "model/request", "data": {}},
                {
                    "type": "assistant/message",
                    "data": {"usage": {"input": 1000, "cache_read": 500, "output": 100}},
                },
            ]
            (child / "episode.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            pricing = {
                "openai-codex/gpt-5.6-luna": Pricing(
                    source="https://example.invalid",
                    input_per_million=0.2,
                    cached_input_per_million=0.02,
                    output_per_million=1.2,
                    long_context_threshold=272000,
                    long_context_input_multiplier=2.0,
                    long_context_output_multiplier=1.5,
                )
            }
            measured = measure_episode(root, pricing)
        self.assertEqual(measured["model_calls"], 1)
        self.assertAlmostEqual(measured["estimated_cost_usd"], 0.00023)


EVALUATED_FOE = {
    "source_tree": "git-tree-sha1:" + "1" * 40,
    "runtime_binary": "sha256:" + "2" * 64,
}
BASE_CONFIGURATION = {
    "model": "openai-codex/gpt-5.6-sol",
    "reasoning_effort": "low",
    "service_tier": "default",
    "token_policy": "measurement_only",
    "workflow_ownership": "evaluation-runner",
    "completion_governance": "model-report",
}
EVIDENCE_SHA256 = "sha256:" + "3" * 64
SUPPORTED_AUDIT = {"reasoning_effort": "high", "model_calls": 60}
COMPLETE_FAILURE_COUNTS = {
    "total_failed_tests": 1,
    "retained_failed_tests": 1,
    "omitted_failed_tests": 0,
    "unlocated_failed_tests": 0,
    "ambiguous_failed_tests": 0,
}
FAILURE_CONTRAST = {
    "task": "terminal-bench/example",
    "failure_profile": {
        "outcome": {"kind": "exhausted", "limit": "model_calls"},
        "artifact_outcome_mismatch": False,
        "failed_verifier_checks": [
            {"name": "test_public_interface", "failure_class": "AssertionError"}
        ],
    },
    "failed_attempts": [
        {
            "episode_id": "ep_failed_one",
            "verifier_report_sha256": "sha256:" + "4" * 64,
            "failure_evidence_counts": COMPLETE_FAILURE_COUNTS,
            "failure_loci": [
                {
                    "name": "test_public_interface",
                    "failure_class": "AssertionError",
                    "locus_sha256": "sha256:" + "5" * 64,
                    "location": "tests/test_outputs.py:116",
                    "assertion": "abs(fwd_tm - rev_tm) <= 5",
                    "observed_assertion": "5.8 <= 5",
                    "message": "primer melting temperatures differ by more than five degrees",
                }
            ],
        },
        {
            "episode_id": "ep_failed_two",
            "verifier_report_sha256": "sha256:" + "6" * 64,
            "failure_evidence_counts": COMPLETE_FAILURE_COUNTS,
            "failure_loci": [
                {
                    "name": "test_public_interface",
                    "failure_class": "AssertionError",
                    "locus_sha256": "sha256:" + "7" * 64,
                    "location": "tests/test_outputs.py:99",
                    "assertion": "15 <= len(extra_r) <= 45",
                    "message": "effective reverse annealing length is 46 bases",
                }
            ],
        },
    ],
    "successful_episode_ids": ["ep_success"],
}
FAILURE_CONTRAST["contrast_sha256"] = digest_bytes(
    canonical_json(FAILURE_CONTRAST)
)
FAILURE_CONTRAST_SHA256 = FAILURE_CONTRAST["contrast_sha256"]


def with_contrast_digest(value):
    answer = {key: item for key, item in value.items() if key != "contrast_sha256"}
    answer["contrast_sha256"] = digest_bytes(canonical_json(answer))
    return answer


COMPLETED_ARTIFACT_REJECTION_CONTRAST = with_contrast_digest(
    {
        **FAILURE_CONTRAST,
        "failure_profile": {
            **FAILURE_CONTRAST["failure_profile"],
            "outcome": {"kind": "completed"},
            "artifact_outcome_mismatch": True,
        },
    }
)


VALID_CAUSAL_CONTRAST = {
    "failed": [
        {
            "episode_id": "ep_failed_one",
            "verifier_report_sha256": "sha256:" + "4" * 64,
            "locus_sha256s": ["sha256:" + "5" * 64],
            "explanation": "The candidate missed the temperature-pair constraint.",
        },
        {
            "episode_id": "ep_failed_two",
            "verifier_report_sha256": "sha256:" + "6" * 64,
            "locus_sha256s": ["sha256:" + "7" * 64],
            "explanation": "The candidate missed the effective annealing-length constraint.",
        },
    ],
    "successful": ["ep_success"],
    "difference": "The success validated complete annealing tracts before completion.",
    "shared_mechanism": "Completion evidence omitted constraints on complete annealing tracts.",
}
PROGRAM_DOCUMENT = {
    "instructions": {"role": "Run the declared workflow."},
    "workflow": {
        "nodes": {
            "diagnose-runtime": {
                "model": {"instructions": {"sufficiency": "Prefer bounded evidence."}}
            }
        }
    },
}
REVISION = {
    "document": "program.json",
    "section": "sufficiency",
    "old_text": "bounded evidence",
    "new_text": "bounded, labeled evidence",
}


class DiagnosisValidatorTest(unittest.TestCase):
    def test_diagnosis_schema_exposes_only_evidence_bound_success_ids(self):
        config = build_config(
            Path("/tmp/candidate"),
            Path("/tmp/evidence.json"),
            Path("/tmp/check"),
            Path("/tmp/diagnosis-validator"),
            model_config("openai-codex/gpt-5.6-sol", "low"),
            model_config("openai-codex/gpt-5.6-sol", "low"),
            [Path("/opt/toolchain")],
            [Path("/repo/.git")],
            [Path("/opt/cargo-cache")],
            "Raise verified completion.",
            "source-change",
            failure_contrasts=[FAILURE_CONTRAST],
        )
        diagnosis = config["workflow"]["nodes"]["diagnose-runtime"]["model"]
        successful = diagnosis["done_when"]["returns"]["properties"][
            "causal_contrast"
        ]["properties"]["successful"]
        self.assertEqual(successful["items"]["enum"], ["ep_success"])
        self.assertIn(
            "copied verbatim as bare strings",
            diagnosis["instructions"]["evidence"],
        )
        self.assertIn(
            "semantic task-quality error",
            diagnosis["instructions"]["sufficiency"],
        )

    def judgments(
        self,
        values,
        requested_candidate_kind="auto",
        failure_contrasts=None,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program = root / "program.json"
            program.write_text(json.dumps(PROGRAM_DOCUMENT), encoding="utf-8")
            validator = root / "diagnosis-validator"
            write_diagnosis_validator(
                validator,
                program,
                EVALUATED_FOE,
                EVIDENCE_SHA256,
                BASE_CONFIGURATION,
                [SUPPORTED_AUDIT],
                failure_contrasts or [FAILURE_CONTRAST],
                requested_candidate_kind,
            )
            results = []
            for value in values:
                if value.get("branch") not in ("insufficient-evidence", None):
                    value = {
                        "causal_contrast": VALID_CAUSAL_CONTRAST,
                        **value,
                    }
                result = subprocess.run(
                    [str(validator)],
                    input=json.dumps(value),
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                results.append(result.stdout.strip())
        return results

    def test_supported_failure_contrasts_require_repetition_and_a_success(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            evidence.write_text(
                json.dumps({"repeated_failure_contrasts": [FAILURE_CONTRAST]}),
                encoding="utf-8",
            )
            self.assertEqual(supported_failure_contrasts(evidence), [FAILURE_CONTRAST])

            evidence.write_text(
                json.dumps({"repeated_failure_contrasts": []}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "no validated repeated failure contrast"):
                supported_failure_contrasts(evidence)
            invalid = with_contrast_digest({
                **FAILURE_CONTRAST,
                "failed_attempts": FAILURE_CONTRAST["failed_attempts"][:1],
            })
            evidence.write_text(
                json.dumps({"repeated_failure_contrasts": [invalid]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "fewer than two failed episodes"):
                supported_failure_contrasts(evidence)

    def test_supported_failure_contrasts_validate_nested_fields_and_disjoint_episodes(self):
        malformed_profile = with_contrast_digest({
            **FAILURE_CONTRAST,
            "failure_profile": {
                **FAILURE_CONTRAST["failure_profile"],
                "failed_verifier_checks": [{"name": "test_public_interface"}],
            },
        })
        overlapping_episodes = with_contrast_digest({
            **FAILURE_CONTRAST,
            "successful_episode_ids": ["ep_failed_one"],
        })
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            evidence.write_text(
                json.dumps({"repeated_failure_contrasts": [malformed_profile]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "invalid verifier checks"):
                supported_failure_contrasts(evidence)
            evidence.write_text(
                json.dumps({"repeated_failure_contrasts": [overlapping_episodes]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "reuses an episode across outcomes"):
                supported_failure_contrasts(evidence)

    def test_supported_failure_contrasts_reject_partial_loci_and_tampering(self):
        incomplete_attempt = {
            **FAILURE_CONTRAST["failed_attempts"][0],
            "failure_evidence_counts": {
                **COMPLETE_FAILURE_COUNTS,
                "unlocated_failed_tests": 1,
            },
        }
        incomplete = with_contrast_digest(
            {
                **FAILURE_CONTRAST,
                "failed_attempts": [
                    incomplete_attempt,
                    FAILURE_CONTRAST["failed_attempts"][1],
                ],
            }
        )
        tampered = {**FAILURE_CONTRAST, "task": "terminal-bench/different"}
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            evidence.write_text(
                json.dumps({"repeated_failure_contrasts": [incomplete]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "incomplete failure evidence"):
                supported_failure_contrasts(evidence)
            evidence.write_text(
                json.dumps({"repeated_failure_contrasts": [tampered]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "invalid contrast digest"):
                supported_failure_contrasts(evidence)

    def test_requested_candidate_kind_is_enforced_by_the_diagnosis_verifier(self):
        judged = self.judgments(
            [
                {
                    "branch": "implement-source",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                },
                {
                    "branch": "configure-workflow",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                },
                {"branch": "insufficient-evidence"},
            ],
            "source-change",
        )
        self.assertEqual(judged[0], "")
        self.assertIn("requires branch implement-source", judged[1])
        self.assertEqual(judged[2], "")

    def test_repeated_completed_rejections_can_support_a_source_hypothesis(self):
        contrast = COMPLETED_ARTIFACT_REJECTION_CONTRAST
        judged = self.judgments(
            [
                {
                    "branch": "implement-source",
                    "failure_contrast_sha256": contrast["contrast_sha256"],
                },
                {"branch": "insufficient-evidence"},
            ],
            "source-change",
            failure_contrasts=[contrast],
        )
        self.assertEqual(judged[0], "")
        self.assertEqual(judged[1], "")

    def test_validator_requires_every_failed_attempt_and_distinct_locus(self):
        missing_attempt = {
            **VALID_CAUSAL_CONTRAST,
            "failed": VALID_CAUSAL_CONTRAST["failed"][:1],
        }
        missing_locus = {
            **VALID_CAUSAL_CONTRAST,
            "failed": [
                VALID_CAUSAL_CONTRAST["failed"][0],
                {
                    **VALID_CAUSAL_CONTRAST["failed"][1],
                    "locus_sha256s": ["sha256:" + "8" * 64],
                },
            ],
        }
        missing_mechanism = {**VALID_CAUSAL_CONTRAST, "shared_mechanism": ""}
        described_success = {
            **VALID_CAUSAL_CONTRAST,
            "successful": ["Successful episode ep_success passed."],
        }
        judged = self.judgments(
            [
                {
                    "branch": "implement-source",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                    "causal_contrast": VALID_CAUSAL_CONTRAST,
                },
                {
                    "branch": "implement-source",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                    "causal_contrast": missing_attempt,
                },
                {
                    "branch": "implement-source",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                    "causal_contrast": missing_locus,
                },
                {
                    "branch": "implement-source",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                    "causal_contrast": missing_mechanism,
                },
                {
                    "branch": "implement-source",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                    "causal_contrast": described_success,
                },
            ]
        )
        self.assertEqual(judged[0], "")
        self.assertIn("every failed episode exactly once", judged[1])
        self.assertIn("every failure locus exactly once", judged[2])
        self.assertIn("one shared failure mechanism", judged[3])
        self.assertIn(
            'bare successful_episode_ids: ["ep_success"]',
            judged[4],
        )

    def test_incomplete_failure_evidence_cannot_produce_a_candidate(self):
        incomplete = with_contrast_digest({
            **FAILURE_CONTRAST,
            "failed_attempts": [
                FAILURE_CONTRAST["failed_attempts"][0],
                {
                    "episode_id": "ep_failed_two",
                    "verifier_report_sha256": None,
                    "failure_evidence_counts": {
                        "total_failed_tests": 1,
                        "retained_failed_tests": 1,
                        "omitted_failed_tests": 0,
                        "unlocated_failed_tests": 1,
                        "ambiguous_failed_tests": 0,
                    },
                    "failure_loci": [],
                },
            ],
        })
        judged = self.judgments(
            [
                {
                    "branch": "implement-source",
                    "failure_contrast_sha256": incomplete["contrast_sha256"],
                    "causal_contrast": VALID_CAUSAL_CONTRAST,
                },
                {"branch": "insufficient-evidence"},
            ],
            failure_contrasts=[incomplete],
        )
        self.assertIn("complete verifier failure loci", judged[0])
        self.assertEqual(judged[1], "")

    def test_validator_accepts_each_valid_diagnosis_and_reports_findings(self):
        executable = "#!/bin/sh\nexit 0\n"
        judged = self.judgments(
            [
                {
                    "branch": "configure-workflow",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                    "independent_audit": SUPPORTED_AUDIT,
                },
                {
                    "branch": "configure-workflow",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                    "independent_audit": {"reasoning_effort": "xhigh", "model_calls": 120},
                },
                {
                    "branch": "implement-source",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                },
                {
                    "branch": "implement-source",
                    "failure_contrast_sha256": "sha256:" + "0" * 64,
                },
                {"branch": "insufficient-evidence"},
                {
                    "branch": "unknown",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                },
                {"branch": "implement-source"},
            ]
        )
        self.assertEqual(judged[0], "")
        self.assertIn("repeated successful", judged[1])
        self.assertEqual(judged[2], "")
        self.assertIn("one supported repeated failure contrast", judged[3])
        self.assertEqual(judged[4], "")
        self.assertIn("automatic selection permits only", judged[5])
        self.assertIn("one supported repeated failure contrast", judged[6])

        instructions = self.judgments(
            [
                {
                    "branch": "revise-instructions",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                    "instruction_revision": REVISION,
                },
                {
                    "branch": "revise-instructions",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                    "instruction_revision": {**REVISION, "old_text": "absent text"},
                },
            ],
            "instruction-revision",
        )
        self.assertEqual(instructions[0], "")
        self.assertIn("exactly once", instructions[1])

        tools = self.judgments(
            [
                {
                    "branch": "define-tool",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                    "tool_definition": {
                        "name": "check-layout",
                        "description": "Verify the workspace layout.",
                        "executable": executable,
                        "executable_sha256": executable_digest(executable.encode()),
                    },
                },
                {
                    "branch": "define-tool",
                    "failure_contrast_sha256": FAILURE_CONTRAST_SHA256,
                    "tool_definition": {
                        "name": "check-layout",
                        "description": "Verify the workspace layout.",
                        "executable": executable,
                        "executable_sha256": "sha256:" + "0" * 64,
                    },
                },
            ],
            "tool-definition",
        )
        self.assertEqual(tools[0], "")
        self.assertIn("does not match the executable content", tools[1])


class LineageAdoptionTest(unittest.TestCase):
    """Synthetic adoptions per candidate kind, checked end to end.

    Each test constructs a proposal episode log whose recorded program
    identity is the parent state's, records the adoption through the
    lineage crate's `build-bundle` binary, and verifies the resulting
    ancestry claim with the crate's `check_ancestry` example.
    """

    # Preserve the Bazel runfiles path so the declared Rust binaries remain
    # reachable through their runfile symlinks.
    repository = Path(__file__).absolute().parents[2]
    validator_sha256 = "a" * 64
    check_sha256 = "b" * 64

    @classmethod
    def setUpClass(cls):
        bazel_build_bundle = cls.repository / "crates" / "lineage" / "build-bundle"
        bazel_check_ancestry = cls.repository / "crates" / "lineage" / "check-ancestry"
        if bazel_build_bundle.is_file() and bazel_check_ancestry.is_file():
            cls.build_bundle = bazel_build_bundle
            cls.check_ancestry_binary = bazel_check_ancestry
            return
        subprocess.run(
            ["cargo", "build", "--quiet", "-p", "foe-lineage", "--bins", "--examples"],
            cwd=cls.repository,
            check=True,
        )
        cls.build_bundle = cls.repository / "target" / "debug" / "build-bundle"
        cls.check_ancestry_binary = cls.repository / "target" / "debug" / "examples" / "check_ancestry"

    def parent_document(self):
        """An identity document declaring the two admission verifiers."""
        return {
            "name": "identity-bound-trajectory-self-improvement",
            "tools": [
                {"name": "check", "exec_sha256": self.check_sha256},
                {"name": DIAGNOSIS_VALIDATOR_TOOL, "exec_sha256": self.validator_sha256},
            ],
        }

    def write_episode(self, episode: Path, identity: str, tool: str, exec_sha256: str):
        program = {
            "tools": ["block", tool],
            "tool_defs": {tool: {"exec": "/verifier", "description": "judges the candidate"}},
            "done_when": {"verify": tool},
        }
        events = [
            {
                "seq": 0,
                "time": 0,
                "type": "episode/start",
                "data": {
                    "id": "ep_root",
                    "parent_id": None,
                    "fork_origin": None,
                    "team_id": None,
                    "program": program,
                    "identity": identity,
                    "task": "propose a candidate",
                    "runtime": {"version": "0.1.0", "build": "sha256:test"},
                    "sandbox": {"mode": "off", "landlock_abi": 0},
                },
            },
            {
                "seq": 1,
                "time": 1,
                "type": "verification/result",
                "data": {
                    "step": 1,
                    "tool": tool,
                    "verifier_identity": "sha256:" + exec_sha256,
                    "status": "accepted",
                    "findings": [],
                    "duration_ms": 1,
                },
            },
            {
                "seq": 2,
                "time": 2,
                "type": "episode/end",
                "data": {"outcome": {"kind": "completed", "value": {}}},
            },
        ]
        episode.mkdir(parents=True)
        (episode / "episode.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
        )

    def record(self, root: Path, kind, candidate, retained, tool, exec_sha256):
        parent = self.parent_document()
        episode = root / "episode"
        self.write_episode(episode, digest_bytes(canonical_json(parent)), tool, exec_sha256)
        return record_adoption(
            root,
            episode,
            adoption_state_document(kind, candidate, PROGRAM_DOCUMENT),
            parent,
            retained,
            tool,
            [str(self.build_bundle)],
            [str(self.check_ancestry_binary)],
        )

    def check_ancestry(self, root: Path, record):
        result = subprocess.run(
            [
                str(self.check_ancestry_binary),
                record["state"],
                str(root / "lineage" / "states"),
                str(root / "lineage" / "evidence"),
            ],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["chain"],
            [record["program_identity"], record["parent_program_identity"]],
        )
        self.assertEqual(report["unverifiable"], [])

    def test_instruction_revision_adoption_verifies_end_to_end(self):
        candidate = create_instruction_candidate(
            EVALUATED_FOE,
            EVIDENCE_SHA256,
            BASE_CONFIGURATION,
            REVISION,
            {"program.json": PROGRAM_DOCUMENT},
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = self.record(
                root,
                "instruction-revision",
                candidate,
                {"instruction-candidate.json": json.dumps(candidate).encode()},
                DIAGNOSIS_VALIDATOR_TOOL,
                self.validator_sha256,
            )
            revised = revised_program_document(PROGRAM_DOCUMENT, REVISION)
            self.assertEqual(record["program_identity"], digest_bytes(canonical_json(revised)))
            self.check_ancestry(root, record)

    def test_workflow_adoption_verifies_end_to_end(self):
        candidate = create_workflow_candidate(
            EVALUATED_FOE, EVIDENCE_SHA256, BASE_CONFIGURATION, SUPPORTED_AUDIT
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = self.record(
                root,
                "workflow-configuration",
                candidate,
                {"workflow-candidate.json": json.dumps(candidate).encode()},
                DIAGNOSIS_VALIDATOR_TOOL,
                self.validator_sha256,
            )
            state = json.loads(Path(record["state"]).read_text(encoding="utf-8"))
            audit = state["identity_document"]["workflow"]["nodes"]["audit-and-repair-task"]
            self.assertEqual(
                audit["model"]["model"]["reasoning_effort"], SUPPORTED_AUDIT["reasoning_effort"]
            )
            self.check_ancestry(root, record)

    def test_tool_definition_adoption_verifies_end_to_end(self):
        executable = "#!/bin/sh\nexit 0\n"
        candidate = create_tool_candidate(
            EVALUATED_FOE,
            EVIDENCE_SHA256,
            BASE_CONFIGURATION,
            {
                "name": "check-layout",
                "description": "Verify the workspace layout.",
                "executable_sha256": executable_digest(executable.encode()),
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = self.record(
                root,
                "tool-definition",
                candidate,
                {
                    "tool-candidate.json": json.dumps(candidate).encode(),
                    "tool-candidate-executable": executable.encode(),
                },
                DIAGNOSIS_VALIDATOR_TOOL,
                self.validator_sha256,
            )
            state = json.loads(Path(record["state"]).read_text(encoding="utf-8"))
            declared = state["identity_document"]["tool_defs"]["check-layout"]
            self.assertEqual(
                "sha256:" + declared["exec_sha256"], executable_digest(executable.encode())
            )
            self.assertIn("check-layout", state["identity_document"]["tools"])
            self.check_ancestry(root, record)

    def test_missing_verification_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            episode = root / "episode"
            parent = self.parent_document()
            self.write_episode(
                episode,
                digest_bytes(canonical_json(parent)),
                DIAGNOSIS_VALIDATOR_TOOL,
                self.validator_sha256,
            )
            with self.assertRaisesRegex(ValueError, "no accepted verification/result"):
                find_accepted_verification(episode, "check")


if __name__ == "__main__":
    unittest.main()
