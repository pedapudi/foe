"""An application that drives foe: a defect triage service.

The application owns the model call, one tool, and every decision made
about a result. foe owns the episode: the system prompt, the step loop, the
budget, the grants, the sandbox, and the log. The seam between them is the
Python package, which `docs/sdk.md` documents.

Three reports arrive. One is triaged, one blocks because the application's
build store has no record of the build it names, and one spends its budget
without reaching an answer. A contract that runs episodes unattended has to
act on all three, so `act_on` matches every outcome kind.

Usage: run.py [ABSOLUTE-PATH-OF-FOE]. Without an argument the binary is
`target/release/foe` in this checkout.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

# The package depends on the standard library alone, so a checkout imports
# it without an installation step.
sys.path.insert(0, str(REPO / "python"))

import foe  # noqa: E402 - the import path is set above

# ---- what the application knows -------------------------------------------

# The application's own records. Nothing in the episode reaches them except
# through the tool below, which runs in this process.
BUILDS = {
    "b-2291": {"branch": "release", "committed": "2026-08-14", "owner": "parser"},
    "b-2287": {"branch": "release", "committed": "2026-08-11", "owner": "runtime"},
}

REPORTS = {
    "nested-brackets": "Build b-2291 crashes on a nested bracket expression.",
    "unknown-build": "Build b-9999 hangs while loading a large file.",
    "no-build-named": "Something in the release line is slower than it was.",
}


@dataclass
class Triage:
    """Where a defect report belongs and how urgent it is."""

    component: str
    severity: str
    summary: str


@foe.tool
def build_record(build_id: str) -> dict:
    """Look up one build in the build store by its identifier."""
    # An exception becomes an error result the model reads and the episode
    # continues, so an unknown identifier needs no special case here.
    return BUILDS[build_id]


@build_record.render
def describe_build(record: dict) -> str:
    """The text the model sees in place of the record. `render` returns the function."""
    return f"branch {record['branch']}, committed {record['committed']}, owned by {record['owner']}"


def triage_contract(reports_dir: Path) -> foe.ExecutionContract:
    """The contract every report is triaged by. Its fingerprint is the same for all three."""
    return foe.ExecutionContract(
        name="defect-triage",
        instructions={
            "10-role": "You triage defect reports for a Python package.",
            "20-method": (
                "Read the report, look up the build it names, and return the component, "
                "the severity, and a one-sentence summary."
            ),
            "30-limits": (
                "Call block with the code goal-unreachable when the build store has no "
                "record of the build the report names."
            ),
        },
        tools=["read", "block", build_record],
        grants=foe.Grants(read=[reports_dir]),
        budget=foe.Budget(model_calls=4, seconds=60),
        done_when=foe.Returns(Triage),
    )


# ---- what the application answers model requests with ----------------------

USAGE = {"input": 0, "output": 0, "cache_read": 0}
Answer = Callable[[int], list[dict]]


def call(call_id: str, name: str, args: dict) -> list[dict]:
    """The chunks of one answer that is a single tool call."""
    return [
        {"kind": "tool_call_start", "id": call_id, "name": name},
        {"kind": "tool_call_delta", "id": call_id, "delta": json.dumps(args)},
        {"kind": "tool_call_end", "id": call_id},
        {"kind": "done", "stop": "tool", "usage": USAGE},
    ]


def transport_from(answer: Answer) -> foe.Transport:
    """A transport that plays `answer` for each step in place of a model.

    A transport that calls a real model has the same signature: an
    asynchronous callable that receives one request and yields chunk
    objects in the shape docs/protocol.md defines under `model/chunk`.
    `foe.adapters.litellm.litellm_transport` is one such callable.
    """

    async def transport(request: dict):
        step = sum(1 for message in request["messages"] if message["role"] == "assistant")
        for chunk in answer(step):
            yield chunk

    return transport


def answers_for(name: str, report_path: Path) -> Answer:
    """The scripted answers for one report. Step 0 always reads the report."""

    def answer(step: int) -> list[dict]:
        if step == 0:
            return call("tc_1", "read", {"path": str(report_path)})
        if name == "nested-brackets":
            if step == 1:
                return call("tc_2", "build_record", {"build_id": "b-2291"})
            # The synthesized `return` tool takes the value under `value`.
            return call(
                "tc_3",
                "return",
                {
                    "value": {
                        "component": "parser",
                        "severity": "high",
                        "summary": "The parser crashes on nested bracket expressions in build b-2291.",
                    }
                },
            )
        if name == "unknown-build":
            if step == 1:
                return call("tc_2", "build_record", {"build_id": "b-9999"})
            return call(
                "tc_3",
                "block",
                {"code": "goal-unreachable", "message": "the build store has no record of build b-9999"},
            )
        # No build is named, so the agent tries recent identifiers one by one
        # and never converges. Each call differs, so the episode reaches its
        # budget rather than the runtime's loop detector.
        return call(f"tc_{step + 1}", "build_record", {"build_id": f"b-22{88 + step}"})

    return answer


# ---- what the application does with each outcome ---------------------------


def act_on(name: str, outcome: foe.Outcome) -> str:
    """Decide what happens to one report. Every outcome kind has an answer."""
    match outcome:
        case foe.Completed(value):
            triage = Triage(**value)
            return f"{name}: filed against {triage.component} at severity {triage.severity}"
        case foe.Blocked(code, message):
            return f"{name}: sent to a person, because the agent blocked with {code}: {message}"
        case foe.Exhausted(limit):
            return f"{name}: requeued with a larger budget, because the episode spent its {limit}"
        case foe.Failed(error):
            return f"{name}: held for an operator, because the runtime could not continue: {error}"
    raise AssertionError(f"{name}: unhandled outcome {outcome!r}")


# ---- the run ---------------------------------------------------------------


def check(outcomes: dict[str, foe.Outcome], log_dirs: dict[str, Path]) -> None:
    """Checks the outcomes and the logs against what the README states."""
    triaged = outcomes["nested-brackets"]
    assert isinstance(triaged, foe.Completed), triaged
    assert Triage(**triaged.value).component == "parser", triaged.value
    blocked = foe.Blocked("goal-unreachable", "the build store has no record of build b-9999")
    assert outcomes["unknown-build"] == blocked, outcomes["unknown-build"]
    assert outcomes["no-build-named"] == foe.Exhausted("model_calls"), outcomes["no-build-named"]

    def events(name: str) -> list[dict]:
        text = (log_dirs[name] / "episode.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines()]

    calls = [e for e in events("nested-brackets") if e["type"] == "host/tool-call"]
    assert [e["data"]["name"] for e in calls] == ["build_record"], calls
    results = [e for e in events("nested-brackets") if e["type"] == "tool/result"]
    assert results[1]["data"]["rendered"] == describe_build(BUILDS["b-2291"]), results[1]
    failed = [e for e in events("unknown-build") if e["type"] == "tool/result" and e["data"].get("is_error")]
    assert failed and failed[0]["data"]["value"] == {"error": "KeyError: 'b-9999'"}, failed


async def main() -> None:
    binary = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "target/release/foe"
    output_dir = REPO / "target"
    output_dir.mkdir(exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="foe-embedding-demo.", dir=output_dir))
    reports_dir = run_dir / "reports"
    reports_dir.mkdir()
    for name, text in REPORTS.items():
        (reports_dir / f"{name}.txt").write_text(text + "\n", encoding="utf-8")

    print(f"Running the embedding demo in {run_dir}")
    contract = triage_contract(reports_dir)
    print(f"contract fingerprint {contract.fingerprint(binary)}")

    outcomes: dict[str, foe.Outcome] = {}
    log_dirs: dict[str, Path] = {}
    for name in REPORTS:
        report_path = reports_dir / f"{name}.txt"
        log_dirs[name] = run_dir / name
        outcomes[name] = await contract.run(
            task=f"Triage the defect report at {report_path}.",
            transport=transport_from(answers_for(name, report_path)),
            binary=binary,
            log_dir=log_dirs[name],
        )
        print(f"  {act_on(name, outcomes[name])}")

    check(outcomes, log_dirs)
    print("Embedding demo passed. Inspect one episode with:")
    print(f"  {binary} view {log_dirs['nested-brackets']} --serve")


asyncio.run(main())
