#!/usr/bin/python3

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from trajectory_corpus import (
    corpus_run_files,
    inspect_manifest,
    load_manifest,
    main,
    read_object,
    snapshot_corpus,
    verify_manifest,
)


class TrajectoryCorpusTest(unittest.TestCase):
    def fixture(self, root: Path, task: str = "fix-git") -> tuple[Path, Path, Path, Path]:
        source = root / "source"
        source.mkdir()
        (source / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        subprocess.run(["git", "init", "--quiet", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "Cargo.toml"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Foe Test",
                "-c",
                "user.email=foe@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "Create source",
            ],
            check=True,
        )
        binary = root / "foe"
        binary.write_bytes(b"foe runtime")
        tree = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD^{tree}"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        identity = {
            "source_tree": f"git-tree-sha1:{tree}",
            "runtime_binary": "sha256:" + hashlib.sha256(b"foe runtime").hexdigest(),
        }
        cases = root / "cases.json"
        cases.write_text(
            json.dumps(
                {
                    "dataset": "terminal-bench/example@1",
                    "groups": {
                        "development": ["fix-git"],
                        "capability_search": ["regex-log"],
                        "confirmation": ["held-out"],
                    },
                }
            ),
            encoding="utf-8",
        )
        run = root / "run"
        trial = run / task / f"{task}__trial"
        episode = trial / "agent" / "foe-episode"
        spill = episode / "children" / "ep_child" / "spill"
        verifier = trial / "verifier"
        artifacts = trial / "artifacts"
        for directory in (spill, verifier, artifacts):
            directory.mkdir(parents=True)
        campaign = {
            "schema_version": 1,
            "dataset": "terminal-bench/example@1",
            "label": "development-evidence",
            "evaluated_foe": identity,
            "tasks": [{"name": task, "model_calls": 60, "seconds": 900}],
        }
        (run / "campaign.json").write_text(json.dumps(campaign), encoding="utf-8")
        (run / task / "config.json").write_text(
            json.dumps({"agent": "foe_agent:FoeAgent", "jobs_dir": str(run)}),
            encoding="utf-8",
        )
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "trial_name": f"{task}__trial",
                    "task_name": f"terminal-bench/{task}",
                    "agent_result": {
                        "metadata": {
                            "foe_credential_exposed": False,
                            "foe_episode_path": "agent/foe-episode",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (trial / "agent" / "foe-diagnostics.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task": f"terminal-bench/{task}",
                    "evidence_identity": {"runtime_build": identity["runtime_binary"]},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (trial / "agent" / "foe-invocation.json").write_text(
            '{"arguments":["--headless"]}\n', encoding="utf-8"
        )
        (episode / "episode.jsonl").write_text(
            json.dumps(
                {
                    "seq": 1,
                    "type": "episode/start",
                    "data": {"runtime": {"build": identity["runtime_binary"]}},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (spill / "call_1.json").write_text('{"canonical":"large result"}\n')
        (verifier / "ctrf.json").write_text('{"results":[]}\n')
        (artifacts / "manifest.json").write_text("[]\n")
        return source / "Cargo.toml", binary, cases, run

    def test_snapshot_retains_complete_runs_under_content_addresses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, cases, run = self.fixture(root)
            manifest_path = snapshot_corpus(
                source, binary, [run], cases, root / "corpus"
            )
            manifest, corpus = load_manifest(manifest_path)
            files = corpus_run_files(manifest, corpus, 0)
            summary = inspect_manifest(manifest_path)
            stored_cases = read_object(corpus, manifest["cases"])
            invocation = next(
                entry
                for entry in manifest["runs"][0]["files"]
                if entry["path"].endswith("foe-invocation.json")
            )
            object_path = (
                corpus
                / "objects"
                / "sha256"
                / invocation["object"].split(":", 1)[1]
            )
            object_mode = object_path.stat().st_mode & 0o777
            expected_cases = cases.read_bytes()

        self.assertEqual(manifest_path.parent.name, "manifests")
        self.assertEqual(manifest["runs"][0]["tasks"], [{"group": "development", "name": "fix-git"}])
        self.assertIn("campaign.json", files)
        self.assertIn("fix-git/fix-git__trial/agent/foe-episode/episode.jsonl", files)
        self.assertIn(
            "fix-git/fix-git__trial/agent/foe-episode/children/ep_child/spill/call_1.json",
            files,
        )
        self.assertIn("fix-git/fix-git__trial/verifier/ctrf.json", files)
        self.assertEqual(invocation["role"], "adapter_invocation")
        self.assertEqual(stored_cases, expected_cases)
        self.assertEqual(object_mode, 0o444)
        self.assertEqual(summary["runs"], 1)
        self.assertEqual(summary["tasks"], ["fix-git"])
        self.assertNotIn(str(root), json.dumps(manifest))

    def test_identical_bytes_share_one_immutable_object(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, cases, run = self.fixture(root)
            repeated = run / "fix-git" / "fix-git__trial" / "agent" / "repeated.txt"
            repeated.write_bytes((run / "campaign.json").read_bytes())
            path = snapshot_corpus(source, binary, [run], cases, root / "corpus")
            manifest, corpus = load_manifest(path)
            campaign = next(
                entry for entry in manifest["runs"][0]["files"] if entry["path"] == "campaign.json"
            )
            duplicate = next(
                entry for entry in manifest["runs"][0]["files"] if entry["path"].endswith("repeated.txt")
            )
            second = snapshot_corpus(source, binary, [run], cases, root / "corpus")
            object_count = len(list((corpus / "objects" / "sha256").iterdir()))
            referenced = len(
                {
                    manifest["cases"]["object"],
                    *(entry["object"] for entry in manifest["runs"][0]["files"]),
                }
            )

            self.assertEqual(campaign["object"], duplicate["object"])
            self.assertEqual(path, second)
            self.assertEqual(object_count, referenced)

    def test_snapshot_accepts_capability_search_and_rejects_other_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, cases, run = self.fixture(root, "regex-log")
            path = snapshot_corpus(source, binary, [run], cases, root / "corpus")
            manifest, _ = load_manifest(path)
            self.assertEqual(manifest["runs"][0]["tasks"][0]["group"], "capability_search")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, cases, run = self.fixture(root, "held-out")
            with self.assertRaisesRegex(ValueError, "development or capability-search"):
                snapshot_corpus(source, binary, [run], cases, root / "corpus")

    def test_snapshot_refuses_exposed_credentials_before_writing_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, cases, run = self.fixture(root)
            result = run / "fix-git" / "fix-git__trial" / "result.json"
            value = json.loads(result.read_text(encoding="utf-8"))
            value["agent_result"]["metadata"]["foe_credential_exposed"] = True
            result.write_text(json.dumps(value), encoding="utf-8")
            corpus = root / "corpus"
            with self.assertRaisesRegex(ValueError, "foe_credential_exposed"):
                snapshot_corpus(source, binary, [run], cases, corpus)
            self.assertFalse((corpus / "objects").exists())
            self.assertFalse((corpus / "manifests").exists())

    def test_snapshot_rejects_a_different_evaluated_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, cases, run = self.fixture(root)
            binary.write_bytes(b"different runtime")
            with self.assertRaisesRegex(ValueError, "different Foe source or binary"):
                snapshot_corpus(source, binary, [run], cases, root / "corpus")

    def test_snapshot_refuses_symbolic_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, cases, run = self.fixture(root)
            (run / "outside").symlink_to(binary)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                snapshot_corpus(source, binary, [run], cases, root / "corpus")

    def test_snapshot_refuses_a_corpus_inside_the_source_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, cases, run = self.fixture(root)
            with self.assertRaisesRegex(ValueError, "outside its source run"):
                snapshot_corpus(source, binary, [run], cases, run / "corpus")

    def test_verification_detects_object_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, cases, run = self.fixture(root)
            path = snapshot_corpus(source, binary, [run], cases, root / "corpus")
            manifest, corpus = load_manifest(path)
            entry = manifest["runs"][0]["files"][0]
            object_path = corpus / "objects" / "sha256" / entry["object"].split(":", 1)[1]
            object_path.chmod(0o644)
            object_path.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "unexpected byte count|does not match"):
                read_object(corpus, entry)
            with self.assertRaisesRegex(ValueError, "unexpected byte count|does not match"):
                verify_manifest(path)

    def test_cli_snapshots_inspects_and_verifies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, cases, run = self.fixture(root)
            corpus = root / "corpus"
            self.assertEqual(
                main(
                    [
                        "snapshot",
                        "--source-root",
                        str(source),
                        "--binary",
                        str(binary),
                        "--cases",
                        str(cases),
                        "--corpus",
                        str(corpus),
                        str(run),
                    ]
                ),
                0,
            )
            manifest = next((corpus / "manifests").iterdir())
            self.assertEqual(main(["inspect", str(manifest)]), 0)
            self.assertEqual(main(["verify", str(manifest)]), 0)


if __name__ == "__main__":
    unittest.main()
