#!/usr/bin/python3

import subprocess
import tempfile
import unittest
from pathlib import Path


class TaskDerivedCheckerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        source = Path(__file__).with_name("task-derived-checker-runner-portable")
        self.runner = self.root / "task-check"
        self.runner.write_bytes(source.read_bytes())
        self.runner.chmod(0o755)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        captured = self.invoke("--capture-initial-state")
        self.assertEqual(captured.returncode, 0, captured.stderr)

    def tearDown(self):
        self.temporary.cleanup()

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.runner), *arguments],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_checker_must_fail_the_untouched_workspace_then_accept_repaired_state(self):
        checker = """\
#!/bin/bash
if test ! -f "$1/answer.txt"; then
  echo "answer.txt is missing"
fi
"""
        installed = self.invoke(checker)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertIn("Negative control produced 1 finding", installed.stdout)
        finding = self.invoke()
        self.assertEqual(finding.returncode, 0)
        self.assertEqual(finding.stdout.strip(), "answer.txt is missing")
        (self.workspace / "answer.txt").write_text("done\n", encoding="utf-8")
        accepted = self.invoke()
        self.assertEqual(accepted.returncode, 0)
        self.assertEqual(accepted.stdout, "")

    def test_installation_rejects_invalid_or_vacuous_checkers(self):
        invalid = self.invoke("if then")
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("invalid Bash syntax", invalid.stderr)
        vacuous = self.invoke(":\n")
        self.assertEqual(vacuous.returncode, 2)
        self.assertIn("accepted the untouched task workspace", vacuous.stderr)

    def test_verification_rejects_a_checker_changed_after_installation(self):
        checker = "echo unfinished\n"
        installed = self.invoke(checker)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        generated = self.runner.with_name(self.runner.name + ".generated.sh")
        generated.chmod(0o600)
        generated.write_text(":\n", encoding="utf-8")
        result = self.invoke()
        self.assertEqual(result.returncode, 0)
        self.assertIn("changed after installation", result.stdout)

    def test_installation_rejects_preimplementation_workspace_changes(self):
        (self.workspace / "unexpected.txt").write_text("changed\n", encoding="utf-8")
        result = self.invoke("echo unfinished\n")
        self.assertEqual(result.returncode, 2)
        self.assertIn("workspace changed before checker installation", result.stderr)


if __name__ == "__main__":
    unittest.main()
