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
    candidate_artifact_identity,
    canonical_json,
    check_baseline,
    check_candidate,
    digest_bytes,
    failed_base_configuration,
    find_accepted_verification,
    instruction_candidate_from_outcome,
    line_budget_ceilings,
    measure_episode,
    model_config,
    parent_state_document,
    record_adoption,
    revised_program_document,
    rust_toolchain_identity,
    supported_independent_audits,
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

    def test_workflow_uses_typed_handoff_and_a_full_coding_surface(self):
        root = Path("/tmp/candidate")
        config = build_config(
            root,
            Path("/tmp/evidence.json"),
            Path("/tmp/check"),
            Path("/tmp/diagnosis-validator"),
            model_config("openai-codex/gpt-5.6-terra", "high"),
            model_config("openai-codex/gpt-5.6-luna", "high"),
            [Path("/opt/toolchain")],
            [Path("/repo/.git")],
            [Path("/opt/cargo-cache")],
            "Raise verified completion.",
        )
        nodes = config["workflow"]["nodes"]
        diagnosis = nodes["diagnose-runtime"]["model"]
        implementation = nodes["implement-runtime-improvement"]["model"]
        self.assertEqual(
            nodes["implement-runtime-improvement"]["follows"],
            ["task", "diagnose-runtime"],
        )
        self.assertNotIn(
            "collect-trajectory-diagnostics",
            nodes["implement-runtime-improvement"]["follows"],
        )
        self.assertEqual(
            nodes["diagnose-runtime"]["branches"],
            {
                "implement-source": ["implement-runtime-improvement"],
                "configure-workflow": [],
                "revise-instructions": [],
                "define-tool": [],
                "insufficient-evidence": [],
            },
        )
        self.assertEqual(implementation["tools"][:4], ["read", "grep", "edit", "bash"])
        self.assertEqual(diagnosis["tools"], ["block", DIAGNOSIS_VALIDATOR_TOOL])
        self.assertEqual(diagnosis["done_when"]["verify"], DIAGNOSIS_VALIDATOR_TOOL)
        self.assertEqual(diagnosis["done_when"]["retries"], 2)
        self.assertEqual(
            diagnosis["tool_defs"][DIAGNOSIS_VALIDATOR_TOOL]["exec"], "/tmp/diagnosis-validator"
        )
        self.assertEqual(diagnosis["grants"]["read"], ["/tmp"])
        self.assertIn("block", config["tools"])
        self.assertNotIn("input_tokens", config["budget"])
        self.assertNotIn("output_tokens", implementation["budget"])
        self.assertEqual(diagnosis["model"]["model"], "gpt-5.6-luna")
        self.assertIn("verified task quality", diagnosis["instructions"]["controls"])
        self.assertIn("resource changes", diagnosis["instructions"]["controls"])
        self.assertIn("general workflow setting", diagnosis["instructions"]["controls"])
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
        self.assertNotIn("required_paths", returns["properties"])
        self.assertNotIn("runtime_activation", returns["properties"])
        self.assertNotIn("implementation_files", returns["properties"])
        self.assertNotIn("model", implementation)
        self.assertIn("reasoning settings", implementation["instructions"]["independence"])
        self.assertIn("baseline-relative line budgets", implementation["instructions"]["validation"])
        self.assertEqual(config["model"]["reasoning_effort"], "high")
        self.assertEqual(config["model"]["service_tier"], "priority")
        self.assertEqual(
            diagnosis["budget"],
            {"model_calls": 20, "seconds": 1800, "loop_threshold": 8},
        )
        self.assertIn("four model requests as a planning target", diagnosis["instructions"]["result"])
        self.assertIn("loop backstop", diagnosis["instructions"]["result"])
        self.assertIn("model capability", diagnosis["instructions"]["sufficiency"])
        self.assertIn("configure-workflow", diagnosis["instructions"]["sufficiency"])
        self.assertIn("independent_audit", returns["properties"])
        self.assertIn("instruction_revision", returns["properties"])
        self.assertIn("tool_definition", returns["properties"])
        self.assertNotIn("instruction_revision", returns["required"])
        self.assertNotIn("tool_definition", returns["required"])
        self.assertIn("revise-instructions", diagnosis["instructions"]["sufficiency"])
        self.assertIn("define-tool", diagnosis["instructions"]["sufficiency"])
        self.assertIn("must not branch on", diagnosis["instructions"]["controls"])
        self.assertEqual(
            implementation["grants"]["write"],
            [
                *(str(root / directory) for directory in ("crates", "docs", "examples")),
                str(root / "target" / "foe-self-improvement-check"),
            ],
        )
        self.assertEqual(
            implementation["grants"]["read"],
            [str(root), "/repo/.git", "/opt/cargo-cache"],
        )
        self.assertEqual(implementation["grants"]["execute"], ["/opt/toolchain"])
        self.assertEqual(config["task"], "Raise verified completion.")
        self.assertEqual(config["budget"]["loop_threshold"], 8)
        self.assertEqual(implementation["budget"]["loop_threshold"], 8)

    def test_failed_base_configuration_excludes_the_successful_audit_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            evidence.write_text(
                json.dumps(
                    {
                        "evaluation_summary": [
                            {
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
            },
        )
        self.assertEqual(audits, [{"reasoning_effort": "high", "model_calls": 60}])

    def test_workflow_candidate_accepts_only_an_observed_successful_setting(self):
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
            }
            supported = [{"reasoning_effort": "high", "model_calls": 60}]
            candidate = workflow_candidate_from_outcome(
                {
                    "branch": "configure-workflow",
                    "independent_audit": supported[0],
                },
                supported,
                identity,
                evidence,
                base,
            )
            with self.assertRaisesRegex(ValueError, "not a repeated successful"):
                workflow_candidate_from_outcome(
                    {
                        "branch": "configure-workflow",
                        "independent_audit": {
                            "reasoning_effort": "xhigh",
                            "model_calls": 120,
                        },
                    },
                    supported,
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

    def test_candidate_check_validates_the_baseline_and_preserves_an_existing_line_overage(self):
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
            specification.write_text("The value is one.\n", encoding="utf-8")
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
            specification.write_text("The value is two.\n", encoding="utf-8")
            accepted = check_candidate(check, candidate)
            sandboxed = subprocess.run(
                [str(check)], text=True, capture_output=True, check=True
            )
            cargo_calls = calls.read_text(encoding="utf-8").splitlines()
        self.assertEqual(ceilings, {"cli": 2})
        self.assertTrue(baseline["accepted"], baseline)
        self.assertTrue(accepted["accepted"], accepted)
        self.assertEqual(sandboxed.stdout, "\n")
        self.assertEqual(len(cargo_calls), 9)
        self.assertEqual(cargo_calls[1], "test --workspace")
        self.assertEqual(
            cargo_calls[7],
            "test --workspace --exclude foe --exclude foe-transport --exclude foe-view -- --skip sandbox::tests::",
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
}
EVIDENCE_SHA256 = "sha256:" + "3" * 64
SUPPORTED_AUDIT = {"reasoning_effort": "high", "model_calls": 60}
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
    def judgments(self, values):
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
            )
            results = []
            for value in values:
                result = subprocess.run(
                    [str(validator)],
                    input=json.dumps(value),
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                results.append(result.stdout.strip())
        return results

    def test_validator_accepts_each_valid_diagnosis_and_reports_findings(self):
        executable = "#!/bin/sh\nexit 0\n"
        judged = self.judgments(
            [
                {"branch": "configure-workflow", "independent_audit": SUPPORTED_AUDIT},
                {
                    "branch": "configure-workflow",
                    "independent_audit": {"reasoning_effort": "xhigh", "model_calls": 120},
                },
                {"branch": "revise-instructions", "instruction_revision": REVISION},
                {
                    "branch": "revise-instructions",
                    "instruction_revision": {**REVISION, "old_text": "absent text"},
                },
                {
                    "branch": "define-tool",
                    "tool_definition": {
                        "name": "check-layout",
                        "description": "Verify the workspace layout.",
                        "executable": executable,
                        "executable_sha256": executable_digest(executable.encode()),
                    },
                },
                {
                    "branch": "define-tool",
                    "tool_definition": {
                        "name": "check-layout",
                        "description": "Verify the workspace layout.",
                        "executable": executable,
                        "executable_sha256": "sha256:" + "0" * 64,
                    },
                },
                {"branch": "implement-source"},
                {"branch": "insufficient-evidence"},
                {"branch": "unknown"},
            ]
        )
        self.assertEqual(judged[0], "")
        self.assertIn("repeated successful", judged[1])
        self.assertEqual(judged[2], "")
        self.assertIn("exactly once", judged[3])
        self.assertEqual(judged[4], "")
        self.assertIn("does not match the executable content", judged[5])
        self.assertEqual(judged[6], "")
        self.assertEqual(judged[7], "")
        self.assertIn("no supported candidate branch", judged[8])


class LineageAdoptionTest(unittest.TestCase):
    """Synthetic adoptions per candidate kind, checked end to end.

    Each test constructs a proposal episode log whose recorded program
    identity is the harness parent state's, records the adoption through
    the lineage crate's `build-bundle` binary, and verifies the resulting
    ancestry claim with `foe lineage`.
    """

    repository = Path(__file__).resolve().parents[2]
    validator_sha256 = "a" * 64
    check_sha256 = "b" * 64

    @classmethod
    def setUpClass(cls):
        subprocess.run(
            ["cargo", "build", "--quiet", "-p", "foe-lineage", "-p", "foe"],
            cwd=cls.repository,
            check=True,
        )
        cls.build_bundle = cls.repository / "target" / "debug" / "build-bundle"
        cls.foe = cls.repository / "target" / "debug" / "foe"

    def parent_document(self):
        return parent_state_document(
            PROGRAM_DOCUMENT,
            EVALUATED_FOE,
            BASE_CONFIGURATION,
            [
                {"name": "check", "exec_sha256": self.check_sha256},
                {"name": DIAGNOSIS_VALIDATOR_TOOL, "exec_sha256": self.validator_sha256},
            ],
        )

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

    def record(self, root: Path, kind, candidate, retained, tool, exec_sha256, artifacts=None):
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
            artifacts=artifacts,
        )

    def check_ancestry(self, root: Path, record):
        result = subprocess.run(
            [
                str(self.foe),
                "lineage",
                record["state"],
                "--states",
                str(root / "lineage" / "states"),
                "--evidence",
                str(root / "lineage" / "evidence"),
                "--json",
            ],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(
            [entry["program_identity"] for entry in report["chain"]],
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
            body = {key: value for key, value in candidate.items() if key != "digest"}
            self.assertEqual(record["program_identity"], digest_bytes(canonical_json(body)))
            self.check_ancestry(root, record)

    def test_source_change_adoption_cites_the_candidate_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = root / "tree"
            changed = tree / "crates/core/src/lib.rs"
            changed.parent.mkdir(parents=True)
            changed.write_text("pub fn value() -> u8 { 2 }\n", encoding="utf-8")
            candidate = candidate_artifact_identity(
                tree, EVALUATED_FOE["source_tree"], ["crates/core/src/lib.rs"]
            )
            record = self.record(
                root,
                "source-change",
                candidate,
                {},
                "check",
                self.check_sha256,
                artifacts=[
                    {"path": name, "sha256": value}
                    for name, value in sorted(candidate["files"].items())
                ],
            )
            body = {key: value for key, value in candidate.items() if key != "digest"}
            self.assertEqual(record["program_identity"], digest_bytes(canonical_json(body)))
            self.assertEqual(record["verification_log"], "episode/episode.jsonl")
            self.assertEqual(record["verification_seq"], 1)
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
