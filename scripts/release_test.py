"""Exercise release guards with a local command recorder and no publication."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

RELEASE = Path(__file__).with_name("release.sh")
COMMIT = "a" * 40
OTHER = "b" * 40

COMMAND = r'''#!/usr/bin/python3
import hashlib, io, json, pathlib, sys, tarfile
root = pathlib.Path(__file__).resolve().parent.parent
state = json.loads((root / "state.json").read_text())
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / "commands.jsonl").open("a") as stream:
    stream.write(json.dumps([name, *args]) + "\n")
if name == "git":
    if args[0] == "status": print(" M file" if state.get("dirty") else "")
    elif args[0] == "rev-parse": print(state["commit"])
    elif args[0] == "show": print('version = "0.2.0"')
    elif args[0] == "ls-remote": print(state.get("remote", ""))
    elif args[0] == "push": sys.exit(1 if state.get("push_failure") else 0)
    elif args[0] == "archive":
        files = {"Cargo.toml": state["commit"], "examples/team/run.sh": "#!/bin/sh\nexit 0\n",
                 "examples/host-model-backend/run.py": ""}
        if state.get("corrupt"):
            files["examples/host-model-backend/run.py"] = "import sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text('corrupted')\n"
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
            for path, text in files.items():
                data = text.encode()
                item = tarfile.TarInfo(path)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
elif name == "rustup": print("x86_64-unknown-linux-musl")
elif name == "cargo":
    target = pathlib.Path(args[args.index("--target-dir") + 1]) / "x86_64-unknown-linux-musl/release/foe"
    target.parent.mkdir(parents=True)
    commit = pathlib.Path("Cargo.toml").read_text()
    target.write_text("#!/bin/sh\n# " + commit + "\nexit 0\n")
    target.chmod(0o755)
elif name == "gh":
    if args[:2] == ["run", "list"]: print(state.get("conclusion", "success"))
    elif args[:2] == ["release", "create"]:
        artifact, checksum = map(pathlib.Path, args[-2:])
        assert checksum.read_text().split()[0] == hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert state["commit"] in artifact.read_text()
        (root / "published.json").write_text(json.dumps(args))
else: raise RuntimeError(name)
'''


class ReleaseTest(unittest.TestCase):
    def run_release(self, **state):
        with tempfile.TemporaryDirectory(prefix="foe-release-test-") as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copy2(RELEASE, root / "scripts/release.sh")
            (root / "state.json").write_text(json.dumps({"commit": COMMIT, **state}))
            tools = root / "bin"
            tools.mkdir()
            for name in ("git", "gh", "cargo", "rustup"):
                command = tools / name
                command.write_text(COMMAND)
                command.chmod(0o755)
            result = subprocess.run(
                ["/bin/bash", str(root / "scripts/release.sh"), "0.2.0", "--draft"],
                env={**os.environ, "PATH": f"{tools}:/usr/bin:/bin"},
                text=True, capture_output=True,
            )
            published = root / "published.json"
            commands = [json.loads(line) for line in (root / "commands.jsonl").read_text().splitlines()]
            return result, json.loads(published.read_text()) if published.exists() else None, commands

    def test_pins_source_tag_and_uploaded_artifact(self):
        """docs/build.md: source, remote tag, and asset identify one tested commit."""
        result, published, commands = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(published[published.index("--target") + 1], COMMIT)
        self.assertIn("--verify-tag", published)
        self.assertIn("--draft", published)
        self.assertIn(["git", "archive", COMMIT], commands)
        self.assertIn(["git", "push", "origin", f"{COMMIT}:refs/tags/v0.2.0"], commands)

    def test_accepts_annotated_tag_peeled_to_selected_commit(self):
        remote = f"{OTHER}\trefs/tags/v0.2.0\n{COMMIT}\trefs/tags/v0.2.0^{{}}"
        result, published, commands = self.run_release(remote=remote)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(published)
        self.assertFalse(any(command[:2] == ["git", "push"] for command in commands))

    def test_refuses_dirty_tree_failed_or_pending_ci_changed_tag_and_changed_asset(self):
        """docs/build.md: every failed precondition prevents release creation."""
        for state in ({"dirty": True}, {"conclusion": "failure"}, {"conclusion": ""},
                      {"remote": f"{OTHER}\trefs/tags/v0.2.0"}, {"corrupt": True}, {"push_failure": True}):
            with self.subTest(state=state):
                result, published, _ = self.run_release(**state)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(published)


if __name__ == "__main__":
    unittest.main()
