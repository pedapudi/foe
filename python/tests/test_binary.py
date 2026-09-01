"""One episode through the built binary. Skipped when `target/debug/foe` is absent."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import foe

from scripted import scripted, text_response, tool_response

BINARY = Path(__file__).resolve().parents[2] / "target" / "debug" / "foe"


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
