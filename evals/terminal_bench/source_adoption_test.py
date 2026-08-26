#!/usr/bin/python3

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from source_adoption import canonical_json, digest_bytes, verify_source_adoption


class SourceAdoptionTest(unittest.TestCase):
    def git(self, root: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(root), *arguments],
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    def fixture(self, root: Path) -> tuple[Path, str, str, str, Path, Path]:
        source = root / "source"
        source.mkdir()
        self.git(source, "init", "--quiet")
        changed = source / "crates/core/src/lib.rs"
        changed.parent.mkdir(parents=True)
        changed.write_text("pub fn value() -> u8 { 1 }\n", encoding="utf-8")
        deleted = source / "obsolete.rs"
        deleted.write_text("remove me\n", encoding="utf-8")
        self.git(source, "add", ".")
        self.git(
            source,
            "-c",
            "user.name=Foe Test",
            "-c",
            "user.email=foe@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Create base",
        )
        base_tree = self.git(source, "rev-parse", "HEAD^{tree}")
        changed.write_text("pub fn value() -> u8 { 2 }\n", encoding="utf-8")
        added = source / "docs/design.md"
        added.parent.mkdir()
        added.write_text("Changed behavior.\n", encoding="utf-8")
        deleted.unlink()
        self.git(source, "add", ".")
        self.git(
            source,
            "-c",
            "user.name=Foe Test",
            "-c",
            "user.email=foe@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Apply candidate",
        )
        applied_tree = self.git(source, "rev-parse", "HEAD^{tree}")
        files = {
            "crates/core/src/lib.rs": digest_bytes(changed.read_bytes()),
            "docs/design.md": digest_bytes(added.read_bytes()),
            "obsolete.rs": "absent",
        }
        candidate_body = {"base_source_tree": f"git-tree-sha1:{base_tree}", "files": files}
        artifact = {
            "schema_version": 1,
            "candidate_identity": digest_bytes(canonical_json(candidate_body)),
            **candidate_body,
            "files": [
                {
                    "path": name,
                    "sha256": sha256,
                    **(
                        {"content": f"candidate-files/{name}"}
                        if sha256 != "absent"
                        else {}
                    ),
                }
                for name, sha256 in sorted(files.items())
            ],
        }
        identity = {
            "runtime": {
                "source_tree": candidate_body["base_source_tree"],
                "files": files,
            }
        }
        bundle = root / "bundle"
        (bundle / "candidate-files/crates/core/src").mkdir(parents=True)
        (bundle / "candidate-files/docs").mkdir(parents=True)
        (bundle / "candidate-files/crates/core/src/lib.rs").write_bytes(changed.read_bytes())
        (bundle / "candidate-files/docs/design.md").write_bytes(added.read_bytes())
        (bundle / "child-identity.json").write_bytes(canonical_json(identity))
        (bundle / "artifact-manifest.json").write_bytes(canonical_json(artifact))
        (bundle / "episode.jsonl").write_text("{}\n", encoding="utf-8")
        adoption = {
            "schema_version": 1,
            "program_identity": digest_bytes(canonical_json(identity)),
            "identity_document_sha256": digest_bytes((bundle / "child-identity.json").read_bytes()),
            "artifact_manifest_sha256": digest_bytes((bundle / "artifact-manifest.json").read_bytes()),
            "verification_log": "episode.jsonl",
            "verification_seq": 1,
        }
        (bundle / "adoption-record.json").write_bytes(canonical_json(adoption))
        retained = []
        for path in sorted(item for item in bundle.rglob("*") if item.is_file()):
            data = path.read_bytes()
            retained.append(
                {
                    "path": path.relative_to(bundle).as_posix(),
                    "bytes": len(data),
                    "sha256": digest_bytes(data),
                }
            )
        manifest = {
            "schema_version": 1,
            "files": retained,
            "proposal_log": "episode.jsonl",
            "adoption_record": "adoption-record.json",
        }
        (bundle / "manifest.json").write_bytes(canonical_json(manifest))
        evidence_identity = digest_bytes(canonical_json(manifest))
        lineage = root / "lineage"
        evidence_bundle = lineage / "evidence" / evidence_identity.removeprefix("sha256:")
        evidence_bundle.parent.mkdir(parents=True)
        bundle.rename(evidence_bundle)
        states = lineage / "states"
        states.mkdir()
        state = states / ("1" * 64 + ".json")
        state.write_text(
            json.dumps({"program_lineage": {"evidence": evidence_identity}}),
            encoding="utf-8",
        )
        checker = root / "check-ancestry"
        checker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        checker.chmod(0o755)
        return (
            source,
            f"git-tree-sha1:{applied_tree}",
            artifact["candidate_identity"],
            digest_bytes(canonical_json(adoption)),
            evidence_bundle,
            checker,
        )

    def test_bundle_binds_adopted_source_and_evaluated_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, applied, candidate_identity, adoption_identity, bundle, checker = self.fixture(root)
            record = verify_source_adoption(
                bundle,
                source,
                applied,
                "sha256:" + "9" * 64,
                checker,
            )
        self.assertEqual(record["candidate_identity"], candidate_identity)
        self.assertEqual(record["adoption_identity"], adoption_identity)
        self.assertEqual(record["evaluated_foe"]["source_tree"], applied)
        self.assertEqual(record["evaluated_foe"]["runtime_binary"], "sha256:" + "9" * 64)

    def test_clean_tree_that_does_not_apply_the_adopted_bytes_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _, _, bundle, checker = self.fixture(root)
            (source / "crates/core/src/lib.rs").write_text("different\n", encoding="utf-8")
            self.git(source, "add", ".")
            self.git(
                source,
                "-c",
                "user.name=Foe Test",
                "-c",
                "user.email=foe@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "Change candidate bytes",
            )
            applied = "git-tree-sha1:" + self.git(source, "rev-parse", "HEAD^{tree}")
            with self.assertRaisesRegex(ValueError, "differs from the adopted changed-file digests"):
                verify_source_adoption(
                    bundle,
                    source,
                    applied,
                    "sha256:" + "9" * 64,
                    checker,
                )

    def test_retained_result_must_report_successful_lineage_adoption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, applied, candidate_identity, _, bundle, checker = self.fixture(root)
            manifest_identity = digest_bytes((bundle / "manifest.json").read_bytes())
            result = {
                "candidate_kind": "source-change",
                "candidate_acceptance": {"accepted": False},
                "candidate_artifact": {"digest": candidate_identity},
                "adoption": {
                    "evidence": manifest_identity,
                    "evidence_directory": str(bundle),
                    "state": str(root / "lineage/states" / ("1" * 64 + ".json")),
                },
            }
            result_path = root / "result.json"
            result_path.write_text(json.dumps(result), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "accepted source candidate"):
                verify_source_adoption(
                    result_path,
                    source,
                    applied,
                    "sha256:" + "9" * 64,
                    checker,
                )

    def test_rejected_ancestry_prevents_source_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, applied, _, _, bundle, checker = self.fixture(root)
            checker.write_text("#!/bin/sh\necho rejected >&2\nexit 1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ancestry check failed: rejected"):
                verify_source_adoption(
                    bundle,
                    source,
                    applied,
                    "sha256:" + "9" * 64,
                    checker,
                )


if __name__ == "__main__":
    unittest.main()
