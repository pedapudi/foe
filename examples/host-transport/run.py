"""Runs config.json with a transport that returns fixed responses instead of calling a model.

The runner creates a disposable project, materializes the configuration
with absolute paths, runs the episode through the Python package, and
checks the outcome and the episode log.

Usage: run.py [ABSOLUTE-PATH-OF-FOE]. Without an argument the binary is
`target/release/foe` in this checkout.
"""

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

# The package depends on the standard library alone, so a checkout imports
# it without an installation step.
sys.path.insert(0, str(REPO / "python"))

import foe  # noqa: E402 - the import path is set above

SUMMARY = "The README describes the project and how to build it."
PROPOSAL = {"summary": SUMMARY, "risks": [], "candidate": "a field inside the complete candidate"}
USAGE = {"input": 0, "output": 0, "cache_read": 0}
verified = []


@foe.tool
def validate_proposal(candidate: dict) -> list[str]:
    """Check that the complete proposal matches the expected result."""
    verified.append(candidate)
    return [] if candidate == PROPOSAL else ["the proposal differs from the expected result"]


def transport_for(readme: Path):
    """A transport that plays a `read` call, then returns one proposal.

    A transport that calls a real model has the same signature: an
    asynchronous callable that receives one request and yields chunk
    objects in the shape docs/protocol.md defines under `model/chunk`.
    """
    script = [
        [
            {"kind": "tool_call_start", "id": "tc_1", "name": "read"},
            {"kind": "tool_call_delta", "id": "tc_1", "delta": json.dumps({"path": str(readme)})},
            {"kind": "tool_call_end", "id": "tc_1"},
            {"kind": "done", "stop": "tool", "usage": USAGE},
        ],
        [
            {"kind": "tool_call_start", "id": "tc_2", "name": "return"},
            {"kind": "tool_call_delta", "id": "tc_2", "delta": json.dumps({"value": PROPOSAL})},
            {"kind": "tool_call_end", "id": "tc_2"},
            {"kind": "done", "stop": "tool", "usage": USAGE},
        ],
    ]

    async def transport(request):
        # One response per step; the step is the count of assistant messages so far.
        step = sum(1 for message in request["messages"] if message["role"] == "assistant")
        for chunk in script[min(step, len(script) - 1)]:
            yield chunk

    return transport


def prepare(run_dir: Path) -> tuple[Path, Path]:
    """Writes the disposable project and the materialized configuration."""
    project_dir = run_dir / "project"
    project_dir.mkdir()
    readme = project_dir / "README.md"
    readme.write_text("# Calculator\n\nA small Python package with one module.\n", encoding="utf-8")
    config_path = run_dir / "config.json"
    subprocess.run(
        [
            sys.executable,
            str(REPO / "examples/support/materialize.py"),
            str(HERE / "config.json"),
            str(config_path),
            "/home/user/project",
            str(project_dir),
        ],
        check=True,
    )
    return config_path, readme


def check(log_dir: Path, outcome: foe.Outcome) -> None:
    """Checks the outcome and the log against what the README states."""
    assert outcome == foe.Completed(PROPOSAL), outcome
    assert verified == [PROPOSAL], verified
    events = [json.loads(line) for line in (log_dir / "episode.jsonl").read_text(encoding="utf-8").splitlines()]

    def data_of(name: str) -> list[dict]:
        return [event["data"] for event in events if event["type"] == name]

    assert data_of("request/header")[0]["model"] == {"provider": "host", "model": "host"}
    requests = data_of("model/request")
    assert requests[0]["consumed"] == [1], requests[0]["consumed"]
    assert [m["role"] for m in requests[1]["messages"]] == ["user", "assistant", "tool"]
    messages = data_of("assistant/message")
    assert messages[0]["stop"] == "tool" and len(messages[0]["tool_calls"]) == 1
    assert messages[1]["stop"] == "tool" and messages[1]["tool_calls"][0]["name"] == "return"
    assert [result["name"] for result in data_of("tool/result")] == ["read", "return"]
    assert data_of("host/tool-call") == [{
        "step": 2,
        "call_id": "verify-2",
        "name": "validate_proposal",
        "args": {"candidate": PROPOSAL},
    }]
    verification = data_of("verification/result")
    assert len(verification) == 1 and verification[0]["status"] == "accepted"
    assert data_of("episode/end")[0]["outcome"] == {"kind": "completed", "value": PROPOSAL}


async def main() -> None:
    binary = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "target/release/foe"
    output_dir = REPO / "target"
    output_dir.mkdir(exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="foe-host-transport-demo.", dir=output_dir))
    config_path, readme = prepare(run_dir)
    log_dir = run_dir / "episode"

    print(f"Running the host transport demo in {run_dir}")
    outcome = await foe.run_config(
        config_path,
        transport=transport_for(readme),
        binary=binary,
        log_dir=log_dir,
        tools=[validate_proposal],
    )
    print(outcome)
    check(log_dir, outcome)
    print("Host transport demo passed. Inspect it with:")
    print(f"  {binary} view {log_dir} --serve")


asyncio.run(main())
