#!/usr/bin/python3
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import foe_build


def repository(root: Path) -> None:
    subprocess.run(["/usr/bin/git", "init", "--quiet", str(root)], check=True)
    (root / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    subprocess.run(["/usr/bin/git", "-C", str(root), "add", "Cargo.toml"], check=True)
    subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(root),
            "-c",
            "user.name=Foe Test",
            "-c",
            "user.email=foe@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Create test repository",
        ],
        check=True,
    )


class FoeBuildTest(unittest.TestCase):
    def test_record_names_the_clean_tree_and_binary_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository(root)
            binary = root / "foe"
            binary.write_bytes(b"runtime")
            subprocess.run(["/usr/bin/git", "-C", str(root), "add", "foe"], check=True)
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=Foe Test",
                    "-c",
                    "user.email=foe@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "Add runtime",
                ],
                check=True,
            )
            evaluated = foe_build.evaluated_foe(root / "Cargo.toml", binary)
        self.assertRegex(evaluated["source_tree"], r"^git-tree-sha1:[0-9a-f]{40}$")
        self.assertEqual(
            evaluated["runtime_binary"],
            "sha256:d92c6a81b2ff50096bcda80885427d1f59a25b5f483f7055523504925d16ab23",
        )

    def test_source_record_rejects_a_dirty_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository(root)
            (root / "untracked").write_text("content", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source tree is not clean"):
                foe_build.clean_source_tree(root)

    def test_evaluated_foe_requires_both_digests(self) -> None:
        with self.assertRaisesRegex(ValueError, "runtime_binary is missing or malformed"):
            foe_build.require_evaluated_foe(
                {"source_tree": "git-tree-sha1:" + "a" * 40}, "evaluation report"
            )


if __name__ == "__main__":
    unittest.main()
