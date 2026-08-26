#!/usr/bin/python3

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from source_adoption import (
    capture_source_candidate,
    freeze_source_candidate,
    verify_source_candidate,
)
from run import Task, main as run_terminal_bench, task_record, write_json_atomic


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


class SourceAdoptionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository = Path(__file__).resolve().parents[2]
        test_srcdir = os.environ.get("TEST_SRCDIR")
        runfile = (
            Path(test_srcdir) / "_main/crates/lineage/source-adoption"
            if test_srcdir
            else cls.repository / "crates/lineage/source-adoption"
        )
        if runfile.is_file():
            cls.checker = runfile
        else:
            subprocess.run(
                ["cargo", "build", "-p", "foe-lineage", "--example", "source_adoption"],
                cwd=cls.repository,
                check=True,
            )
            cls.checker = cls.repository / "target/debug/examples/source_adoption"

    def git(self, root, *arguments):
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(root), *arguments],
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    def write_episode(self, directory, parent_identity, verifier_sha256):
        events = [
            {
                "seq": 0,
                "time": 0,
                "type": "episode/start",
                "data": {
                    "id": "ep_proposal",
                    "parent_id": None,
                    "fork_origin": None,
                    "team_id": None,
                    "program": {
                        "tools": ["check"],
                        "tool_defs": {
                            "check": {
                                "exec": "/trusted/check",
                                "description": "Judge the source candidate.",
                            }
                        },
                        "done_when": {"verify": "check"},
                    },
                    "identity": parent_identity,
                    "task": "propose one source change",
                    "runtime": {"version": "0.1.0", "build": "unknown"},
                    "sandbox": {"mode": "off", "landlock_abi": 0},
                },
            },
            {
                "seq": 1,
                "time": 1,
                "type": "verification/result",
                "data": {
                    "step": 1,
                    "tool": "check",
                    "verifier_identity": verifier_sha256,
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
        directory.mkdir(parents=True)
        (directory / "episode.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )

    def write_workflow_episode(
        self, directory, parent_identity, diagnosis_identity,
        implementation_identity, audit_identity, verifier_sha256,
    ):
        programs = {
            "ep_diagnosis": (
                "diagnose-runtime",
                "diagnose-foe-from-trajectory-measurements",
                diagnosis_identity,
            ),
            "ep_implementation": (
                "implement-runtime-improvement",
                "implement-foe-improvement",
                implementation_identity,
            ),
            "ep_audit": (
                "audit-runtime-improvement",
                "audit-and-repair-foe-improvement",
                audit_identity,
            ),
        }
        audit_program = {
            "name": "audit-runtime-improvement",
            "tools": ["check"],
            "tool_defs": {
                "check": {
                    "exec": "/controller/candidate-check",
                    "description": "Judge the source candidate.",
                }
            },
            "done_when": {"verify": "check"},
        }
        root = [
            {
                "seq": 0, "time": 0, "type": "episode/start",
                "data": {
                    "id": "ep_root", "parent_id": None, "fork_origin": None,
                    "team_id": None,
                    "program": {
                        "workflow": {
                            "nodes": {
                                "collect-trajectory-diagnostics": {"tool": "evidence"},
                                "diagnose-runtime": {"model": {"name": "diagnosis"}},
                                "implement-runtime-improvement": {"model": {"name": "implementation"}},
                                "audit-runtime-improvement": {"model": {"name": "audit"}},
                            }
                        }
                    },
                    "identity": parent_identity, "task": "improve Foe",
                    "runtime": {"version": "0.1.0", "build": "unknown"},
                    "sandbox": {"mode": "off", "landlock_abi": 0},
                },
            },
        ]
        sequence = 1
        for child_id, (node, _, _) in programs.items():
            root.extend([
                {
                    "seq": sequence, "time": sequence, "type": "spawn/start",
                    "data": {
                        "child_id": child_id, "program": node,
                        "context": "fresh", "call_id": f"tc_{child_id}",
                    },
                },
                {
                    "seq": sequence + 1, "time": sequence + 1, "type": "spawn/end",
                    "data": {"child_id": child_id, "outcome": {"kind": "completed", "value": {}}},
                },
            ])
            sequence += 2
        root.append({
            "seq": sequence, "time": sequence, "type": "episode/end",
            "data": {"outcome": {"kind": "completed", "value": {}}},
        })
        directory.mkdir(parents=True)
        (directory / "episode.jsonl").write_text(
            "\n".join(json.dumps(event) for event in root) + "\n", encoding="utf-8"
        )
        for child_id, (_, program_name, identity) in programs.items():
            child_program = audit_program if child_id == "ep_audit" else {"name": program_name}
            child = [{
                "seq": 0, "time": 0, "type": "episode/start",
                "data": {
                    "id": child_id, "parent_id": "ep_root", "fork_origin": None,
                    "team_id": None, "program": child_program,
                    "identity": identity, "task": "improve Foe",
                    "runtime": {"version": "0.1.0", "build": "unknown"},
                    "sandbox": {"mode": "off", "landlock_abi": 0},
                },
            }]
            if child_id == "ep_audit":
                child.append({
                    "seq": 1, "time": 1, "type": "verification/result",
                    "data": {
                        "step": 1, "tool": "check", "verifier_identity": verifier_sha256,
                        "status": "accepted", "findings": [], "duration_ms": 1,
                    },
                })
            child.append({
                "seq": len(child), "time": len(child), "type": "episode/end",
                "data": {"outcome": {"kind": "completed", "value": {}}},
            })
            child_dir = directory / f"children/{child_id}"
            child_dir.mkdir(parents=True)
            (child_dir / "episode.jsonl").write_text(
                "\n".join(json.dumps(event) for event in child) + "\n", encoding="utf-8"
            )

    def fixture(self, root, critical_controller_files=False):
        repository = root / "repository"
        repository.mkdir()
        self.git(repository, "init", "-q")
        self.git(repository, "config", "user.name", "Foe Test")
        self.git(repository, "config", "user.email", "foe@example.invalid")
        (repository / "changed.txt").write_text("before\n", encoding="utf-8")
        (repository / "deleted.txt").write_text("remove\n", encoding="utf-8")
        controller_files = [
            "crates/lineage/src/lib.rs",
            "crates/log/src/lib.rs",
            "crates/program/src/lib.rs",
            "Cargo.toml",
            "BUILD.bazel",
        ]
        if critical_controller_files:
            for name in controller_files:
                path = repository / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("trusted base\n", encoding="utf-8")
        self.git(repository, "add", ".")
        self.git(repository, "commit", "-qm", "base")
        algorithm = self.git(repository, "rev-parse", "--show-object-format")
        base = f"git-tree-{algorithm}:{self.git(repository, 'rev-parse', 'HEAD^{tree}')}"
        verifier = b"trusted verifier"
        verifier_sha256 = sha256(verifier)
        parent = {
            "name": "source-improvement",
            "tools": [{"name": "check", "exec_sha256": verifier_sha256.removeprefix("sha256:")}],
        }
        parent_identity = sha256(canonical(parent))
        bundle = root / "bundle"
        bundle.mkdir()
        (bundle / "parent-identity.json").write_bytes(canonical(parent))
        (bundle / "candidate-check").write_bytes(verifier)
        self.write_episode(bundle / "episode", parent_identity, verifier_sha256)
        (repository / "changed.txt").write_text("after\n", encoding="utf-8")
        (repository / "changed.txt").chmod(0o755)
        (repository / "deleted.txt").unlink()
        (repository / "added.txt").write_text("added\n", encoding="utf-8")
        if critical_controller_files:
            for name in controller_files:
                (repository / name).write_text("candidate-controlled replacement\n", encoding="utf-8")
        captured = capture_source_candidate(
            self.checker,
            bundle,
            repository,
            base,
            "parent-identity.json",
            "episode/episode.jsonl",
            "episode/episode.jsonl",
            1,
            "candidate-check",
        )
        self.git(repository, "add", "-A")
        self.git(repository, "commit", "-qm", "candidate")
        applied = f"git-tree-{algorithm}:{self.git(repository, 'rev-parse', 'HEAD^{tree}')}"
        runtime = root / "foe"
        runtime.write_bytes(b"rebuilt foe")
        return repository, bundle, base, applied, runtime, captured

    def test_candidate_controller_files_cannot_change_the_trusted_judgment(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, bundle, _, applied, runtime, _ = self.fixture(
                Path(directory), critical_controller_files=True
            )
            verified = verify_source_candidate(
                self.checker, bundle, repository, applied, runtime
            )
        self.assertEqual(verified["checker_sha256"], sha256(self.checker.read_bytes()))

    def test_workflow_child_verifier_is_closed_by_its_retained_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            self.git(repository, "init", "-q")
            self.git(repository, "config", "user.name", "Foe Test")
            self.git(repository, "config", "user.email", "foe@example.invalid")
            (repository / "changed.txt").write_text("before\n", encoding="utf-8")
            self.git(repository, "add", ".")
            self.git(repository, "commit", "-qm", "base")
            algorithm = self.git(repository, "rev-parse", "--show-object-format")
            base = f"git-tree-{algorithm}:{self.git(repository, 'rev-parse', 'HEAD^{tree}')}"
            (repository / "changed.txt").write_text("after\n", encoding="utf-8")
            verifier = b"#!/bin/sh\nexit 0\n"
            diagnosis_identity = sha256(canonical({"name": "diagnose-runtime"}))
            implementation_identity = sha256(canonical({"name": "implement-runtime-improvement"}))
            audit_identity = sha256(canonical({"name": "audit-runtime-improvement"}))
            parent = {
                "name": "source-improvement",
                "workflow": {
                    "nodes": {
                        "collect-trajectory-diagnostics": {"tool": "evidence"},
                        "diagnose-runtime": {"model": diagnosis_identity},
                        "implement-runtime-improvement": {"model": implementation_identity},
                        "audit-runtime-improvement": {"model": audit_identity},
                    }
                },
            }
            parent_identity = sha256(canonical(parent))
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "parent-identity.json").write_bytes(canonical(parent))
            (bundle / "candidate-check").write_bytes(verifier)
            self.write_workflow_episode(
                bundle / "episode", parent_identity, diagnosis_identity,
                implementation_identity, audit_identity, sha256(verifier)
            )
            captured = capture_source_candidate(
                self.checker,
                bundle,
                repository,
                base,
                "parent-identity.json",
                "episode/episode.jsonl",
                "episode/children/ep_audit/episode.jsonl",
                1,
                "candidate-check",
            )
            self.git(repository, "add", "-A")
            self.git(repository, "commit", "-qm", "candidate")
            applied = f"git-tree-{algorithm}:{self.git(repository, 'rev-parse', 'HEAD^{tree}')}"
            runtime = root / "foe"
            runtime.write_bytes(b"rebuilt foe")
            verified = verify_source_candidate(
                self.checker, bundle, repository, applied, runtime
            )
            self.assertEqual(
                verified["source_bundle_identity"], captured["source_bundle_identity"]
            )
            manifest_path = bundle / "source-candidate-manifest.json"
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes)
            manifest["verification_tool"] = "unrelated-check"
            manifest_path.write_bytes(canonical(manifest))
            with self.assertRaisesRegex(ValueError, "open authorization checks"):
                verify_source_candidate(self.checker, bundle, repository, applied, runtime)
            manifest_path.write_bytes(manifest_bytes)
            (bundle / "candidate-check").write_bytes(b"substituted")
            with self.assertRaisesRegex(ValueError, "source candidate checker failed"):
                verify_source_candidate(self.checker, bundle, repository, applied, runtime)

    def test_preflight_binds_bytes_modes_deletions_and_evaluated_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, bundle, _, applied, runtime, captured = self.fixture(Path(directory))
            verified = verify_source_candidate(
                self.checker, bundle, repository, applied, runtime
            )
            manifest = json.loads((bundle / "source-candidate-manifest.json").read_text())
        self.assertEqual(verified["source_bundle_identity"], captured["source_bundle_identity"])
        self.assertEqual(verified["source_candidate_identity"], captured["source_candidate_identity"])
        entries = {entry["path"]: entry for entry in manifest["entries"]}
        self.assertEqual(entries["changed.txt"]["applied"]["mode"], "100755")
        self.assertEqual(entries["deleted.txt"]["status"], "deleted")
        self.assertEqual(verified["evaluated_pair"]["source_tree"], applied)

    def test_preflight_rejects_changed_retained_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, bundle, _, applied, runtime, _ = self.fixture(Path(directory))
            (bundle / "candidate-files/changed.txt").write_text("different\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source candidate checker failed"):
                verify_source_candidate(self.checker, bundle, repository, applied, runtime)

    def test_retained_result_identities_must_match_the_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, bundle, _, applied, runtime, captured = self.fixture(root)
            retained = root / "result.json"
            captured.update(
                {
                    "bundle": str(bundle),
                    "lineage_status": "pending-external-evaluation",
                    "source_candidate_identity": "sha256:" + "0" * 64,
                }
            )
            retained.write_text(json.dumps({"source_candidate": captured}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match its evidence bundle"):
                verify_source_candidate(self.checker, retained, repository, applied, runtime)

    def test_frozen_bundle_is_unaffected_by_external_bundle_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, bundle, _, applied, runtime, _ = self.fixture(root)
            frozen, expected = freeze_source_candidate(
                self.checker,
                bundle,
                repository,
                applied,
                runtime,
                root / "campaign/source-candidate-bundle",
                verify_source_candidate(
                    self.checker, bundle, repository, applied, runtime
                ),
            )
            (bundle / "candidate-files/changed.txt").write_text("replacement\n", encoding="utf-8")
            (bundle / "candidate-check").write_bytes(b"replaced checker")
            self.assertEqual(
                verify_source_candidate(self.checker, frozen, repository, applied, runtime),
                expected,
            )
            with self.assertRaisesRegex(ValueError, "source candidate checker failed"):
                verify_source_candidate(self.checker, bundle, repository, applied, runtime)
            (frozen / "candidate-check").write_bytes(b"replaced frozen checker")
            with self.assertRaisesRegex(ValueError, "source candidate checker failed"):
                verify_source_candidate(self.checker, frozen, repository, applied, runtime)

    def test_bundle_replacement_after_preflight_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, bundle, _, applied, runtime, _ = self.fixture(root)
            preflight = verify_source_candidate(
                self.checker, bundle, repository, applied, runtime
            )
            (bundle / "candidate-files/changed.txt").write_text("replacement\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source candidate checker failed"):
                freeze_source_candidate(
                    self.checker,
                    bundle,
                    repository,
                    applied,
                    runtime,
                    root / "campaign/source-candidate-bundle",
                    preflight,
                )

    def test_campaign_rejects_invalid_source_evidence_before_provider_spend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, bundle, _, _, runtime, _ = self.fixture(root)
            (bundle / "candidate-files/changed.txt").write_text("different\n", encoding="utf-8")
            status = run_terminal_bench(
                [
                    "--foe",
                    str(runtime),
                    "--source-root",
                    str(repository / "changed.txt"),
                    "--source-checker",
                    str(self.checker),
                    "--controller-root",
                    str(self.repository),
                    "--controller-artifact-root",
                    str(self.checker.parent),
                    "--source-adoption",
                    str(bundle),
                    "--agent-module",
                    str(Path(__file__).with_name("foe_agent.py")),
                    "--trace-evaluator",
                    "/bin/true",
                    "--cases",
                    str(Path(__file__).with_name("cases.json")),
                    "--harbor",
                    "/bin/true",
                ]
            )
        self.assertEqual(status, 2)

    def test_campaign_rejects_unrelated_verification_episode_before_provider_spend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, bundle, _, applied, runtime, captured = self.fixture(root)
            unrelated = bundle / "unrelated"
            self.write_episode(unrelated, captured["parent_program_identity"], sha256(b"trusted verifier"))
            log = unrelated / "episode.jsonl"
            manifest_path = bundle / "source-candidate-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["verification_log"] = "unrelated/episode.jsonl"
            manifest["files"].append(
                {
                    "path": "unrelated/episode.jsonl",
                    "bytes": log.stat().st_size,
                    "sha256": sha256(log.read_bytes()),
                }
            )
            manifest["files"].sort(key=lambda item: item["path"])
            manifest_path.write_bytes(canonical(manifest))
            with self.assertRaisesRegex(ValueError, "proposal episode tree"):
                verify_source_candidate(self.checker, bundle, repository, applied, runtime)
            status = run_terminal_bench(
                [
                    "--foe", str(runtime),
                    "--source-root", str(repository / "changed.txt"),
                    "--source-checker", str(self.checker),
                    "--controller-root", str(self.repository),
                    "--controller-artifact-root", str(self.checker.parent),
                    "--source-adoption", str(bundle),
                    "--agent-module", str(Path(__file__).with_name("foe_agent.py")),
                    "--trace-evaluator", "/bin/true",
                    "--cases", str(Path(__file__).with_name("cases.json")),
                    "--harbor", "/bin/true",
                ]
            )
        self.assertEqual(status, 2)

    def test_capture_rejects_source_and_bundle_symlinks(self):
        for kind in ("source", "bundle"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                if kind == "source":
                    repository, bundle, base, _, _, _ = self.fixture(root)
                    # Start a second capture from an unsealed copy.
                    (bundle / "source-candidate-manifest.json").unlink()
                    (bundle / "candidate-files").rename(bundle / "retained-files")
                    (repository / "linked.txt").symlink_to(repository / "changed.txt")
                else:
                    repository = root / "repository"
                    repository.mkdir()
                    self.git(repository, "init", "-q")
                    self.git(repository, "config", "user.name", "Foe Test")
                    self.git(repository, "config", "user.email", "foe@example.invalid")
                    (repository / "a").write_text("a", encoding="utf-8")
                    self.git(repository, "add", ".")
                    self.git(repository, "commit", "-qm", "base")
                    algorithm = self.git(repository, "rev-parse", "--show-object-format")
                    base = f"git-tree-{algorithm}:{self.git(repository, 'rev-parse', 'HEAD^{tree}')}"
                    (repository / "a").write_text("b", encoding="utf-8")
                    bundle = root / "bundle"
                    bundle.mkdir()
                    parent = {"name": "parent"}
                    (bundle / "parent-identity.json").write_bytes(canonical(parent))
                    self.write_episode(bundle / "episode", sha256(canonical(parent)), sha256(b"check"))
                    (bundle / "candidate-check").symlink_to(bundle / "parent-identity.json")
                with self.assertRaisesRegex(ValueError, "symbolic link"):
                    capture_source_candidate(
                        self.checker,
                        bundle,
                        repository,
                        base,
                        "parent-identity.json",
                        "episode/episode.jsonl",
                        "episode/episode.jsonl",
                        1,
                        "candidate-check",
                    )

    def test_malformed_manifest_and_base_tree_fail_without_python_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, bundle, _, applied, runtime, _ = self.fixture(root)
            path = bundle / "source-candidate-manifest.json"
            value = json.loads(path.read_text())
            value["unexpected"] = True
            path.write_bytes(canonical(value))
            with self.assertRaisesRegex(ValueError, "unknown field"):
                verify_source_candidate(self.checker, bundle, repository, applied, runtime)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            self.git(repository, "init", "-q")
            blob = subprocess.run(
                ["/usr/bin/git", "-C", str(repository), "hash-object", "-w", "--stdin"],
                input="not a tree",
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            bundle = root / "bundle"
            bundle.mkdir()
            with self.assertRaisesRegex(ValueError, "source candidate checker failed"):
                capture_source_candidate(
                    self.checker,
                    bundle,
                    repository,
                    "git-tree-sha1:" + blob,
                    "parent-identity.json",
                    "episode/episode.jsonl",
                    "episode/episode.jsonl",
                    1,
                    "candidate-check",
                )

    def test_external_evaluation_completes_lineage_from_actual_plan_and_episode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, bundle, _, applied, runtime, captured = self.fixture(root)
            identity_document = {"name": "evaluated-program", "tools": []}
            identity = sha256(canonical(identity_document))
            program = {"name": "evaluated-program", "task": "transfer task"}
            plan = root / "foe-plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "identity": identity,
                        "identity_document": identity_document,
                        "program": program,
                    }
                ),
                encoding="utf-8",
            )
            episode = root / "evaluated-episode"
            episode.mkdir()
            events = [
                {
                    "seq": 0,
                    "time": 0,
                    "type": "episode/start",
                    "data": {
                        "id": "ep_evaluated",
                        "parent_id": None,
                        "fork_origin": None,
                        "team_id": None,
                        "program": {"name": "evaluated-program"},
                        "identity": identity,
                        "task": "transfer task",
                        "runtime": {"version": "0.1.0", "build": sha256(runtime.read_bytes())},
                        "sandbox": {"mode": "off", "landlock_abi": 0},
                    },
                },
                {
                    "seq": 1,
                    "time": 1,
                    "type": "episode/end",
                    "data": {"outcome": {"kind": "completed", "value": {}}},
                },
            ]
            (episode / "episode.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            run_dir = root / "campaign"
            job = run_dir / "activation-task"
            trial = job / "trial-1"
            agent = trial / "agent"
            agent.mkdir(parents=True)
            (agent / "foe-plan.json").write_bytes(plan.read_bytes())
            subprocess.run(
                ["/bin/cp", "-R", str(episode), str(agent / "foe-episode")],
                check=True,
            )
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "trial_name": "trial-1",
                        "agent_result": {
                            "metadata": {
                                "foe_outcome": {"kind": "completed", "value": {}},
                                "foe_trace_conformant": True,
                                "foe_built_in_workflow": False,
                                "foe_usage_reported": True,
                                "foe_credential_mode": "mutable",
                                "foe_credential_exposed": False,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (job / "result.json").write_text(
                json.dumps(
                    {
                        "n_total_trials": 1,
                        "stats": {
                            "n_completed_trials": 1,
                            "n_errored_trials": 0,
                            "n_input_tokens": 0,
                            "n_cache_tokens": 0,
                            "n_output_tokens": 0,
                            "cost_usd": 0.0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            record = task_record(
                task=Task("activation-task", 10, 1, 1, 60, 60, 1, 1024),
                run_dir=run_dir,
                harbor_exit_code=0,
                install_only=False,
                built_in_workflow=False,
                completion_checker=False,
                worker=1,
                execution_group="activation-task",
                credential_mode="mutable",
                started_at="2026-08-26T00:00:00Z",
                ended_at="2026-08-26T00:01:00Z",
                elapsed_seconds=60,
                source_adoption_path=bundle,
                source_checker=self.checker,
                source_root=repository,
                evaluated_source=applied,
                foe=runtime,
                source_preflight=verify_source_candidate(
                    self.checker, bundle, repository, applied, runtime
                ),
            )
            self.assertNotIn("result_error", record)
            adopted = record["source_adoptions"][0]
            campaign = root / "campaign.json"
            write_json_atomic(
                campaign,
                {
                    "source_candidate": verify_source_candidate(
                        self.checker, bundle, repository, applied, runtime
                    ),
                    "source_adoptions": record["source_adoptions"],
                },
            )
            retained_campaign = json.loads(campaign.read_text(encoding="utf-8"))
            bad_plan = json.loads((agent / "foe-plan.json").read_text(encoding="utf-8"))
            bad_plan["identity"] = "sha256:" + "0" * 64
            (agent / "foe-plan.json").write_text(json.dumps(bad_plan), encoding="utf-8")
            rejected = task_record(
                task=Task("activation-task", 10, 1, 1, 60, 60, 1, 1024),
                run_dir=run_dir,
                harbor_exit_code=0,
                install_only=False,
                built_in_workflow=False,
                completion_checker=False,
                worker=1,
                execution_group="activation-task",
                credential_mode="mutable",
                started_at="2026-08-26T00:00:00Z",
                ended_at="2026-08-26T00:01:00Z",
                elapsed_seconds=60,
                source_adoption_path=bundle,
                source_checker=self.checker,
                source_root=repository,
                evaluated_source=applied,
                foe=runtime,
                source_preflight=verify_source_candidate(
                    self.checker, bundle, repository, applied, runtime
                ),
            )
        self.assertEqual(adopted["source_bundle_identity"], captured["source_bundle_identity"])
        self.assertEqual(adopted["program_identity"], identity)
        self.assertEqual(adopted["plan_identity"], identity)
        self.assertTrue(adopted["launched_program_verified"])
        self.assertEqual(
            retained_campaign["source_candidate"]["source_candidate_identity"],
            adopted["source_candidate_identity"],
        )
        self.assertEqual(
            retained_campaign["source_adoptions"][0]["program_identity"], identity
        )
        self.assertFalse(rejected["configuration_claim_valid"])
        self.assertTrue(rejected["direct_implementation_required"])
        self.assertIn("foe plan identity", rejected["result_error"])


if __name__ == "__main__":
    unittest.main()
