#!/usr/bin/python3

"""Exercise the generated source-candidate wrapper through source preflight."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from source_adoption import capture_source_candidate


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def git(repository, *arguments):
    return subprocess.run(
        ["/usr/bin/git", "-C", str(repository), *arguments],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def main():
    wrapper = Path(sys.argv[1]).resolve()
    checker = Path(sys.argv[2]).resolve()
    controller_arguments = sys.argv[3:]
    controller_source = Path(
        controller_arguments[controller_arguments.index("--controller-root") + 1]
    ).resolve()
    declared_checker = Path(
        controller_arguments[controller_arguments.index("--source-checker") + 1]
    ).resolve()
    if declared_checker != checker:
        raise RuntimeError("generated source-candidate arguments name a different checker")
    controller_source_root = (
        controller_source.parent if controller_source.is_file() else controller_source
    )
    if checker.is_relative_to(controller_source_root):
        raise RuntimeError("test requires the Bazel checker outside the controller source root")
    with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR")) as directory:
        root = Path(directory)
        repository = root / "candidate"
        repository.mkdir()
        git(repository, "init", "-q")
        git(repository, "config", "user.name", "Foe Test")
        git(repository, "config", "user.email", "foe@example.invalid")
        (repository / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        (repository / "source.txt").write_text("before\n", encoding="utf-8")
        git(repository, "add", ".")
        git(repository, "commit", "-qm", "base")
        algorithm = git(repository, "rev-parse", "--show-object-format")
        base = f"git-tree-{algorithm}:{git(repository, 'rev-parse', 'HEAD^{tree}')}"

        verifier = b"#!/bin/sh\nexit 0\n"
        verifier_digest = digest(verifier)
        parent = {
            "name": "source-improvement",
            "tools": [{"name": "check", "exec_sha256": verifier_digest.removeprefix("sha256:")}],
        }
        parent_identity = digest(canonical(parent))
        bundle = root / "bundle"
        episode = bundle / "episode"
        episode.mkdir(parents=True)
        (bundle / "parent-identity.json").write_bytes(canonical(parent))
        (bundle / "candidate-check").write_bytes(verifier)
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
                        "tool_defs": {"check": {"exec": "/trusted/check"}},
                        "done_when": {"verify": "check"},
                    },
                    "identity": parent_identity,
                    "task": "improve Foe",
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
                    "verifier_identity": verifier_digest,
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
        (episode / "episode.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )
        (repository / "source.txt").write_text("after\n", encoding="utf-8")
        capture_source_candidate(
            checker,
            bundle,
            repository,
            base,
            "parent-identity.json",
            "episode/episode.jsonl",
            "episode/episode.jsonl",
            1,
            "candidate-check",
        )
        git(repository, "add", "-A")
        git(repository, "commit", "-qm", "candidate")
        runtime = root / "foe"
        runtime.write_bytes(b"candidate Foe binary")
        result = subprocess.run(
            [
                str(wrapper),
                "--foe",
                str(runtime),
                "--source-root",
                str(repository / "Cargo.toml"),
                "--source-adoption",
                str(bundle),
                "--controller-bazel",
                "/bin/true",
                *controller_arguments,
                "--harbor",
                "/bin/true",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout)
        if "No model requests were made." not in result.stdout:
            raise RuntimeError(f"wrapper did not reach the no-spend preview:\n{result.stdout}")


if __name__ == "__main__":
    main()
