#!/usr/bin/python3
"""Record the foe episode logs the normalizer tests read.

Each case runs the foe binary once through the host runtime with scripted
model responses and copies the resulting episode directory, its child
episodes included, beside this script under the case's name. The cases
cover every outcome kind the normalizer maps, a shell command the sandbox
refused, an edit that created a file and one that changed a file, a child
episode reached through `spawn`, and one compaction.

The workspace the episodes act on is created under the recording directory
`--root` names, and its absolute path is what the logs name, so a recorded
log carries the paths of the host that recorded it. The tests read the
paths from the logs rather than assuming them. The root is an argument
because this script reads no environment variable.

    /usr/bin/python3 evals/cross_harness/fixtures/foe/record.py --foe target/debug/foe --root ~/.local/state/foe/cross-harness/fixture-recording
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

EVALS = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(EVALS))

import host_runtime  # noqa: E402
import runtime_responses  # noqa: E402
from runtime_responses import call, done  # noqa: E402

FIXTURES = Path(__file__).resolve().parent

# The log is the one file a recorded episode keeps; `spill/` and `tmp/` are
# omitted because the cases write nothing under them.
LOG_NAME = "episode.jsonl"

EXISTING_SOURCE = "def deadline(settings):\n    return settings['timeout']\n"


def base_config(name: str, workspace: Path, task: str) -> dict[str, Any]:
    return {
        "version": 4,
        "name": name,
        "instructions": {"role": "Follow the scripted task exactly."},
        "tools": ["read"],
        "grants": {"read": [str(workspace)]},
        "budget": {"model_calls": 8, "input_tokens": 16000, "output_tokens": 4000, "seconds": 120},
        "sandbox": {"mode": "off"},
        "task": task,
    }


def step(request: dict[str, Any]) -> int:
    return sum(1 for message in request["messages"] if message["role"] == "assistant")


def tool_names(request: dict[str, Any]) -> set[str]:
    return {tool["name"] for tool in request["tools"]}


def edit_and_bash_config(workspace: Path) -> dict[str, Any]:
    config = base_config("edit-and-bash", workspace, "Create one file, change one file, run three commands, then report.")
    config["tools"] = ["read", "edit", "bash"]
    config["grants"] = {"read": [str(workspace)], "write": [str(workspace / "src")], "execute": ["/bin", "/usr/bin"]}
    config["sandbox"] = {"mode": "required"}
    return config


def edit_and_bash_responder(workspace: Path, secret: Path) -> host_runtime.Responder:
    def respond(request: dict[str, Any]) -> list[dict[str, Any]]:
        match step(request):
            case 0:
                return (
                    call("bash-list", "bash", {"command": "ls src && echo listed", "timeout_seconds": 30})
                    + call("bash-secret", "bash", {"command": f"cat {secret} && echo read", "timeout_seconds": 30})
                    + done("tool", runtime_responses.ORDINARY_USAGE)
                )
            case 1:
                return (
                    call("edit-create", "edit", {"path": "src/created.py", "edits": [{"old_text": "", "new_text": "VALUE = 1\n"}]})
                    + call(
                        "edit-change",
                        "edit",
                        {"path": str(workspace / "src" / "existing.py"), "edits": [{"old_text": "'timeout'", "new_text": "'timeout_seconds'"}]},
                    )
                    + done("tool", runtime_responses.ORDINARY_USAGE)
                )
            case 2:
                return call("bash-write", "bash", {"command": "printf 'made\\n' > src/made.txt && cat src/made.txt", "timeout_seconds": 30}) + done("tool")
        return [{"kind": "text", "delta": "Both files are written and the commands ran."}] + done("end")

    return respond


def spawn_config(workspace: Path) -> dict[str, Any]:
    config = base_config("spawn-child", workspace, "Delegate one survey of src/existing.py, wait for it, then report.")
    config["tools"] = ["read", "spawn", "wait"]
    config["grants"] = {"read": [str(workspace)], "spawn": ["survey"]}
    config["budget"].update({"max_depth": 1, "max_episodes": 3, "max_concurrent": 1})
    config["child_contracts"] = {
        "survey": {
            "name": "survey",
            "instructions": {"role": "Read the file the task names and report its content to the episode that started you."},
            "tools": ["read", "notify"],
            "grants": {"read": [str(workspace)]},
            "budget": {"model_calls": 4, "input_tokens": 4000, "output_tokens": 1000},
        }
    }
    return config


def spawn_responder(workspace: Path) -> host_runtime.Responder:
    def respond(request: dict[str, Any]) -> list[dict[str, Any]]:
        if "spawn" in tool_names(request):
            match step(request):
                case 0:
                    return call("spawn-survey", "spawn", {"contract": "survey", "name": "existing-survey", "task": "Report the content of src/existing.py."}) + done("tool")
                case 1:
                    return call("wait-all", "wait", {}) + done("tool")
            return [{"kind": "text", "delta": "The survey reported."}] + done("end")
        match step(request):
            case 0:
                return call("child-read", "read", {"path": str(workspace / "src" / "existing.py")}) + done("tool", runtime_responses.ORDINARY_USAGE)
            case 1:
                return call("child-notify", "notify", {"content": "src/existing.py reads the key timeout on line 2."}) + done("tool")
        return [{"kind": "text", "delta": "Reported."}] + done("end")

    return respond


def blocked_config(workspace: Path) -> dict[str, Any]:
    config = base_config("blocked", workspace, "Report the scripted missing-capability condition.")
    config["tools"] = ["block"]
    return config


def blocked_responder(request: dict[str, Any]) -> list[dict[str, Any]]:
    return runtime_responses.blocked_outcome(request, {})


def exhausted_config(workspace: Path) -> dict[str, Any]:
    config = base_config("exhausted", workspace, "Read one file and leave work pending at the request limit.")
    config["budget"]["model_calls"] = 1
    return config


def exhausted_responder(workspace: Path) -> host_runtime.Responder:
    def respond(request: dict[str, Any]) -> list[dict[str, Any]]:
        return runtime_responses.exhausted_outcome(request, {"file": str(workspace / "src" / "existing.py")})

    return respond


def failed_config(workspace: Path) -> dict[str, Any]:
    return base_config("failed", workspace, "Receive one non-retryable response error.")


def failed_responder(request: dict[str, Any]) -> list[dict[str, Any]]:
    return runtime_responses.failed_outcome(request, {})


def compaction_config(workspace: Path) -> dict[str, Any]:
    config = base_config("compaction", workspace, "Read both fixture files and report completion.")
    config["context"] = {"compact": True, "window_tokens": 2000, "reserve_tokens": 500, "keep_recent_tokens": 1, "margin_tokens": 0}
    return config


def compaction_responder(workspace: Path) -> host_runtime.Responder:
    options = {"file_a": str(workspace / "src" / "existing.py"), "file_b": str(workspace / "src" / "second.py")}

    def respond(request: dict[str, Any]) -> list[dict[str, Any]]:
        return runtime_responses.compaction(request, options)

    return respond


def materialize(root: Path) -> tuple[Path, Path]:
    """A workspace with two source files and a secret outside it."""
    workspace = root / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "existing.py").write_text(EXISTING_SOURCE, encoding="utf-8")
    (workspace / "src" / "second.py").write_text("RETRIES = 3\n", encoding="utf-8")
    outside = root / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("the secret\n", encoding="utf-8")
    return workspace, secret


def copy_episode(source: Path, destination: Path) -> None:
    """Copy the log and every child's log, keeping the documented layout."""
    destination.mkdir(parents=True)
    shutil.copy(source / LOG_NAME, destination / LOG_NAME)
    children = source / "children"
    if children.is_dir():
        for child in sorted(children.iterdir()):
            copy_episode(child, destination / "children" / child.name)


def record(foe: Path, root: Path) -> list[str]:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    workspace, secret = materialize(root)
    cases: list[tuple[str, dict[str, Any], host_runtime.Responder, int]] = [
        ("edit-and-bash", edit_and_bash_config(workspace), edit_and_bash_responder(workspace, secret), 0),
        ("spawn-child", spawn_config(workspace), spawn_responder(workspace), 0),
        ("blocked", blocked_config(workspace), blocked_responder, 2),
        ("exhausted", exhausted_config(workspace), exhausted_responder(workspace), 3),
        ("failed", failed_config(workspace), failed_responder, 1),
        ("compaction", compaction_config(workspace), compaction_responder(workspace), 0),
    ]
    recorded = []
    for name, config, responder, expected_status in cases:
        case = root / name
        case.mkdir()
        config_path = case / "config.json"
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        status, episode = host_runtime.run(foe, config_path, case / "log", responder)
        if status != expected_status:
            raise RuntimeError(f"{name}: the episode ended with status {status}; expected {expected_status}; log {episode}")
        destination = FIXTURES / name
        if destination.exists():
            shutil.rmtree(destination)
        copy_episode(episode, destination)
        recorded.append(name)
    return recorded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--foe", required=True, type=Path, help="the foe binary")
    parser.add_argument("--root", required=True, type=Path, help="where the workspace and the raw episode directories are written; the directory is replaced")
    args = parser.parse_args(argv)
    foe = args.foe.resolve()
    if not os.access(foe, os.X_OK):
        print(f"record: {foe} is not executable", file=sys.stderr)
        return 2
    try:
        recorded = record(foe, args.root.resolve())
    except RuntimeError as exc:
        print(f"record: {exc}", file=sys.stderr)
        return 1
    for name in recorded:
        print(f"recorded {FIXTURES / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
