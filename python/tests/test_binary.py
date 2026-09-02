"""Episodes through the built binary. Skipped when `target/debug/foe` is absent."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import foe

from scripted import SUMMARY, reference_count, scripted, scripted_model, text_response, tool_response

BINARY = Path(__file__).resolve().parents[2] / "target" / "debug" / "foe"


def contract_calling_its_model(tmp_path: Path) -> foe.ExecutionContract:
    """A contract whose `model` block leaves the model to the binary itself."""
    return foe.ExecutionContract(
        name="built-in-transport",
        instructions={"role": "You are under test."},
        tools=["read", reference_count],
        grants=foe.Grants(read=[tmp_path]),
        budget=foe.Budget(model_calls=4),
        model=scripted_model(),
    )


@pytest.mark.skipif(not BINARY.is_file(), reason="target/debug/foe has not been built")
def test_python_and_runtime_builtin_catalogues_agree(tmp_path: Path) -> None:
    child = foe.ExecutionContract(
        name="child",
        instructions={"role": "Finish the child task."},
        tools=["read"],
        grants=foe.Grants(read=[tmp_path]),
        budget=foe.Budget(model_calls=1),
    )
    contract = foe.ExecutionContract(
        name="all-builtins",
        instructions={"role": "Exercise the built-in tool catalogue."},
        tools=sorted(foe.BUILTIN_TOOLS),
        grants=foe.Grants(read=[tmp_path], write=[tmp_path], spawn=["child"]),
        budget=foe.Budget(model_calls=1),
        child_contracts={"child": child},
    )
    assert contract.fingerprint(BINARY).startswith("sha256:")


@pytest.mark.skipif(not BINARY.is_file(), reason="target/debug/foe has not been built")
def test_the_built_binary_completes_an_episode_with_a_host_tool(tmp_path: Path) -> None:
    seen: list[str] = []

    @foe.tool
    def mutation_usage(mutation_id: str) -> dict[str, Any]:
        """Find where a mutation point's value or symbol is referenced."""
        seen.append(mutation_id)
        return {"count": 3}

    @mutation_usage.render
    def _(value: dict[str, Any]) -> str:
        return f"{value['count']} references"

    contract = foe.ExecutionContract(
        name="binary-test",
        instructions={"role": "You are under test."},
        tools=["read", mutation_usage],
        grants=foe.Grants(read=[tmp_path]),
        budget=foe.Budget(model_calls=4),
    )
    events: list[foe.Event] = []
    outcome = asyncio.run(
        contract.run(
            task="Count references.",
            transport=scripted(
                [
                    tool_response(("mutation_usage", {"mutation_id": "m_41"}), text="I will look."),
                    text_response("Done: 3 references."),
                ]
            ),
            binary=BINARY,
            log_dir=tmp_path / "episode",
            on_event=events.append,
        )
    )
    assert outcome == foe.Completed("Done: 3 references.")
    assert seen == ["m_41"]
    result = next(e for e in events if e.type == "tool/result")
    assert result.data["rendered"] == "3 references"
    assert [e.type for e in events][-1] == "episode/end"
    assert contract.fingerprint(BINARY).startswith("sha256:")


@pytest.mark.skipif(not BINARY.is_file(), reason="target/debug/foe has not been built")
def test_the_built_binary_calls_the_model_while_the_package_serves_its_host_tools(tmp_path: Path) -> None:
    """A `model` block and `host_tools` in one document, run through the package."""
    events: list[foe.Event] = []
    outcome = asyncio.run(
        contract_calling_its_model(tmp_path).run(
            task="Count the references.",
            binary=BINARY,
            log_dir=tmp_path / "episode",
            on_event=events.append,
        )
    )
    assert outcome == foe.Completed(SUMMARY)
    header = next(e for e in events if e.type == "request/header")
    assert header.data["model"] == {"provider": "exec", "model": "host-tool-then-text"}
    result = next(e for e in events if e.type == "tool/result")
    assert result.data["name"] == "reference_count"
    assert result.data["value"] == {"count": 3, "symbol": "add"}


@pytest.mark.skipif(not BINARY.is_file(), reason="target/debug/foe has not been built")
def test_the_built_binary_reports_its_process_and_build_before_the_first_tool_call(tmp_path: Path) -> None:
    at_call: list[tuple[int, foe.Runtime | None]] = []
    started: list[foe.Handle] = []

    @foe.tool(name="reference_count")
    def record_identity(symbol: str) -> dict[str, Any]:
        """Count the references to a symbol."""
        at_call.append((started[0].pid, started[0].runtime))
        return {"count": 3, "symbol": symbol}

    contract = foe.ExecutionContract(
        name="process-identity",
        instructions={"role": "You are under test."},
        tools=["read", record_identity],
        grants=foe.Grants(read=[tmp_path]),
        budget=foe.Budget(model_calls=4),
        model=scripted_model(),
    )

    async def scenario() -> foe.Outcome:
        handle = await contract.start(task="Count the references.", binary=BINARY, log_dir=tmp_path / "episode")
        started.append(handle)
        assert handle.runtime is not None
        assert handle.runtime.version == foe.__version__
        assert handle.runtime.build.startswith("sha256:")
        return await handle.wait()

    assert asyncio.run(scenario()) == foe.Completed(SUMMARY)
    assert at_call == [(started[0].pid, started[0].runtime)]
