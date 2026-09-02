"""Runs a contract whose `model` block leaves the model call to foe.

The application declares one tool of its own and no transport. foe reaches
the model through its own client, and every call the model makes to the
Python tool comes back over the host protocol.

Usage: run.py [ABSOLUTE-PATH-OF-FOE]. Without an argument the binary is
`target/release/foe` in this checkout.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

# The package depends on the standard library alone, so a checkout imports
# it without an installation step.
sys.path.insert(0, str(REPO / "python"))

import foe  # noqa: E402 - the import path is set above

SUMMARY = "Recorded one finding against the calculator project."

# The findings the application keeps. The episode never touches this store;
# it reaches it only by calling the tool below.
findings: list[dict] = []


@foe.tool
def record_finding(component: str, summary: str) -> dict:
    """Record one finding against a component of the project."""
    findings.append({"component": component, "summary": summary})
    return {"recorded": len(findings)}


@record_finding.render
def _(value: dict) -> str:
    return f"recorded; {value['recorded']} findings so far"


def review_contract(project_dir: Path) -> foe.ExecutionContract:
    """The contract: foe calls the model, the application serves the tool."""
    return foe.ExecutionContract(
        name="readme-review",
        instructions={"role": "You review a project's README and record what it fails to state."},
        tools=["read", record_finding],
        grants=foe.Grants(read=[project_dir]),
        budget=foe.Budget(model_calls=6, seconds=120),
        model=foe.Model(
            provider="exec",
            model="embedding-demo",
            options={"exec": str(HERE / "transport.py"), "readme": str(project_dir / "README.md")},
        ),
    )


def prepare(run_dir: Path) -> Path:
    """Writes the disposable project the episode reads."""
    project_dir = run_dir / "project"
    project_dir.mkdir()
    readme = project_dir / "README.md"
    readme.write_text("# Calculator\n\nA small Python package with one module.\n", encoding="utf-8")
    return project_dir


def check(log_dir: Path, outcome: foe.Outcome) -> None:
    """Checks the outcome and the log against what the README states."""
    assert outcome == foe.Completed(SUMMARY), outcome
    assert findings == [{"component": "calculator", "summary": "The README states no supported Python version."}]
    events = [json.loads(line) for line in (log_dir / "episode.jsonl").read_text(encoding="utf-8").splitlines()]

    def data_of(name: str) -> list[dict]:
        return [event["data"] for event in events if event["type"] == name]

    assert data_of("request/header")[0]["model"] == {"provider": "exec", "model": "embedding-demo"}
    assert len(data_of("model/request")) == 3
    assert [call["name"] for call in data_of("host/tool-call")] == ["record_finding"]
    results = {result["name"]: result for result in data_of("tool/result")}
    assert results["read"]["value"]["content"].startswith("# Calculator")
    assert results["record_finding"]["rendered"] == "recorded; 1 findings so far"
    assert data_of("episode/end")[0]["outcome"] == {"kind": "completed", "value": SUMMARY}


async def main() -> None:
    binary = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "target/release/foe"
    output_dir = REPO / "target"
    output_dir.mkdir(exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="foe-model-block-demo.", dir=output_dir))
    project_dir = prepare(run_dir)
    log_dir = run_dir / "episode"

    print(f"Running the model block demo in {run_dir}")
    handle = await review_contract(project_dir).start(task="Review the README.", binary=binary, log_dir=log_dir)
    assert handle.runtime is not None
    print(f"Episode {handle.episode_id} is process {handle.pid}, runtime {handle.runtime.version}")
    print(f"Build {handle.runtime.build}")
    outcome = await handle.wait()
    print(outcome)
    check(log_dir, outcome)
    print("Model block demo passed. Inspect it with:")
    print(f"  {binary} view {log_dir} --serve")


asyncio.run(main())
