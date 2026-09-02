"""Episodes through the fake binary: the host side of docs/protocol.md."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

import foe

from scripted import SUMMARY, reference_count, scripted, scripted_model, text_response, tool_response


def contract_with(
    tools: list[str | foe.HostTool],
    *,
    model_calls: int = 5,
    output_tokens: int | None = None,
    done_when: foe.DoneWhen | None = None,
    model: foe.Model | None = None,
) -> foe.ExecutionContract:
    return foe.ExecutionContract(
        name="test",
        instructions={"role": "You are under test."},
        tools=tools,
        grants=foe.Grants(read=["/"]),
        budget=foe.Budget(model_calls=model_calls, output_tokens=output_tokens),
        done_when=done_when,
        model=model,
    )


def test_full_run_with_host_tool(fake_binary: Path, tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []

    @foe.tool
    def mutation_usage(mutation_id: str, fs: foe.ReadFS) -> dict[str, Any]:
        """Find where a mutation point's value or symbol is referenced."""
        seen.append({"mutation_id": mutation_id, "roots": [str(r) for r in fs.roots]})
        return {"count": 3, "mutation_id": mutation_id}

    @mutation_usage.render
    def _(value: dict[str, Any]) -> str:
        return f"{value['count']} references"

    requests: list[dict[str, Any]] = []
    transport = scripted(
        [
            tool_response(("mutation_usage", {"mutation_id": "m_41"}), text="I will look."),
            text_response("Done: 3 references."),
        ],
        requests,
    )
    events: list[foe.Event] = []
    log_dir = tmp_path / "episode"
    outcome = asyncio.run(
        contract_with(["read", mutation_usage]).run(
            task="Count references.",
            transport=transport,
            binary=fake_binary,
            log_dir=log_dir,
            on_event=events.append,
        )
    )

    assert outcome == foe.Completed("Done: 3 references.")
    assert seen == [{"mutation_id": "m_41", "roots": ["/"]}]

    # The transport received the header joined with the messages.
    assert len(requests) == 2
    first = requests[0]
    assert first["request_id"] == "rq_01"
    assert first["system"] == "You are under test."
    assert [t["name"] for t in first["tools"]] == ["read", "mutation_usage"]
    assert first["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Count references."}]}]
    assert first["max_output_tokens"] is None
    second_messages = requests[1]["messages"]
    assert second_messages[1]["role"] == "assistant"
    assert second_messages[1]["tool_calls"] == [{"id": "tc_1", "name": "mutation_usage", "args": {"mutation_id": "m_41"}}]
    assert second_messages[2] == {
        "role": "tool",
        "call_id": "tc_1",
        "name": "mutation_usage",
        "rendered": "3 references",
        "is_error": False,
    }

    # Every event the callback saw is the log, line for line.
    logged = [json.loads(line) for line in (log_dir / "episode.jsonl").read_text().splitlines()]
    assert [e.type for e in events] == [e["type"] for e in logged]
    assert [e.seq for e in events] == list(range(len(logged)))
    types = [e.type for e in events]
    assert types[:4] == ["episode/start", "inbox/item", "request/header", "model/request"]
    assert "host/tool-call" in types
    assert types[-1] == "episode/end"
    result_event = next(e for e in events if e.type == "tool/result")
    assert result_event.data["value"] == {"count": 3, "mutation_id": "m_41"}
    assert result_event.data["rendered"] == "3 references"


def test_runtime_output_allowance_clamps_the_host_setting(fake_binary: Path, tmp_path: Path) -> None:
    requests: list[dict[str, Any]] = []
    outcome = asyncio.run(
        contract_with(["read"], output_tokens=30).run(
            task="Finish.",
            transport=scripted([text_response("Done.")], requests),
            binary=fake_binary,
            log_dir=tmp_path / "episode",
            max_output_tokens=100,
        )
    )
    assert outcome == foe.Completed("Done.")
    assert requests[0]["max_output_tokens"] == 30


def test_host_tool_exception_becomes_an_error_result(fake_binary: Path, tmp_path: Path) -> None:
    @foe.tool
    def explode(reason: str) -> str:
        """Raise."""
        raise RuntimeError(reason)

    events: list[foe.Event] = []
    outcome = asyncio.run(
        contract_with(["read", explode]).run(
            task="t",
            transport=scripted([tool_response(("explode", {"reason": "boom"})), text_response("ok")]),
            binary=fake_binary,
            log_dir=tmp_path / "episode",
            on_event=events.append,
        )
    )
    assert outcome == foe.Completed("ok")
    result = next(e for e in events if e.type == "tool/result")
    assert result.data["is_error"] is True
    assert result.data["value"] == {"error": "RuntimeError: boom"}


def test_steer_arrives_as_an_inbox_item(fake_binary: Path, tmp_path: Path) -> None:
    events: list[foe.Event] = []
    requests: list[dict[str, Any]] = []
    gate: asyncio.Event | None = None

    async def transport(request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        requests.append(request)
        if len(requests) == 1:
            assert gate is not None
            await gate.wait()
            for chunk in tool_response(("read", {"path": "/x"})):
                yield chunk
        else:
            for chunk in text_response("finished"):
                yield chunk

    async def scenario() -> foe.Outcome:
        nonlocal gate
        gate = asyncio.Event()
        handle = await contract_with(["read"]).start(
            task="t", transport=transport, binary=fake_binary, log_dir=tmp_path / "episode", on_event=events.append
        )
        while not any(e.type == "model/request" for e in events):
            await asyncio.sleep(0.01)
        # The steer line is written before the gate releases the response, so
        # the runtime holds it during step 1 and records it when step 2 assembles.
        await handle.steer("Stop after the first failing test.")
        gate.set()
        return await handle.wait()

    outcome = asyncio.run(scenario())
    assert outcome == foe.Completed("finished")
    steer = next(e for e in events if e.type == "inbox/item" and e.data["source"] == "parent")
    assert steer.data == {
        "source": "parent",
        "content": [{"type": "text", "text": "Stop after the first failing test."}],
        "from": None,
        "message_id": None,
    }
    second = next(e for e in events if e.type == "model/request" and e.data["step"] == 2)
    assert steer.seq in second.data["consumed"]
    assert second.data["messages"] == requests[1]["messages"]
    assert requests[1]["messages"][-1] == {
        "role": "user",
        "content": [{"type": "text", "text": "Stop after the first failing test."}],
    }
    assert steer.seq > max(e.seq for e in events if e.type == "tool/result" and e.data["step"] == 1)


def test_cancel_ends_the_episode_as_failed(fake_binary: Path, tmp_path: Path) -> None:
    started = asyncio.Event()

    async def transport(request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        started.set()
        await asyncio.sleep(3600)
        yield {"kind": "done", "stop": "end", "usage": {"input": 0, "output": 0, "cache_read": 0}}

    async def scenario() -> tuple[foe.Outcome, foe.Handle]:
        handle = await contract_with(["read"]).start(
            task="t", transport=transport, binary=fake_binary, log_dir=tmp_path / "episode"
        )
        await started.wait()
        return await handle.cancel(), handle

    outcome, handle = asyncio.run(scenario())
    assert outcome == foe.Failed("cancelled")
    assert handle.done
    assert handle.outcome == outcome


def test_blocked_outcome(fake_binary: Path, tmp_path: Path) -> None:
    outcome = asyncio.run(
        contract_with(["read", "block"]).run(
            task="t",
            transport=scripted([tool_response(("block", {"code": "ambiguous-task", "message": "Which test?"}))]),
            binary=fake_binary,
            log_dir=tmp_path / "episode",
        )
    )
    assert outcome == foe.Blocked("ambiguous-task", "Which test?")


def test_exhausted_outcome(fake_binary: Path, tmp_path: Path) -> None:
    outcome = asyncio.run(
        contract_with(["read"], model_calls=1).run(
            task="t",
            transport=scripted([tool_response(("read", {"path": "/x"}))]),
            binary=fake_binary,
            log_dir=tmp_path / "episode",
        )
    )
    assert outcome == foe.Exhausted("model_calls")


def test_failed_outcome_from_a_transport_exception(fake_binary: Path, tmp_path: Path) -> None:
    async def transport(request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        raise ConnectionError("no route to provider")
        yield {}

    outcome = asyncio.run(
        contract_with(["read"]).run(task="t", transport=transport, binary=fake_binary, log_dir=tmp_path / "episode")
    )
    assert outcome == foe.Failed("ConnectionError: no route to provider")


def test_returns_and_verify(fake_binary: Path, tmp_path: Path) -> None:
    attempts: list[dict[str, Any]] = []

    @foe.tool
    def check(candidate: dict[str, Any]) -> list[str]:
        """Require a title."""
        attempts.append(candidate)
        return [] if candidate.get("title") else ["title is missing"]

    contract = contract_with(
        ["read"],
        done_when=foe.Verified(verify=check, returns={"type": "object", "properties": {"title": {"type": "string"}}}),
    )
    requests: list[dict[str, Any]] = []
    outcome = asyncio.run(
        contract.run(
            task="t",
            transport=scripted(
                [
                    tool_response(("return", {"value": {"title": ""}})),
                    tool_response(("return", {"value": {"title": "Experiment 7"}})),
                ],
                requests,
            ),
            binary=fake_binary,
            log_dir=tmp_path / "episode",
        )
    )
    assert outcome == foe.Completed({"title": "Experiment 7"})
    assert attempts == [{"title": ""}, {"title": "Experiment 7"}]
    assert requests[1]["messages"][-1]["content"] == [{"type": "text", "text": "title is missing"}]


def test_run_config_from_a_file(fake_binary: Path, tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(contract_with(["read"]).to_json("t"))
    outcome = asyncio.run(
        foe.run_config(
            config, transport=scripted([text_response("hello")]), binary=fake_binary, log_dir=tmp_path / "episode"
        )
    )
    assert outcome == foe.Completed("hello")


def test_run_config_rejects_missing_tool_implementations(fake_binary: Path, tmp_path: Path) -> None:
    doc = contract_with(["read"]).to_dict("t")
    doc["host_tools"] = {"missing": {"description": "d", "params": {"type": "object"}, "effect": "pure"}}
    doc["tools"].append("missing")
    with pytest.raises(ValueError, match="host_tools: no implementation was supplied for missing"):
        asyncio.run(foe.run_config(doc, transport=scripted([]), binary=fake_binary, log_dir=tmp_path / "e"))


def test_a_model_block_and_a_host_transport_are_exclusive(fake_binary: Path, tmp_path: Path) -> None:
    """docs/config.md `model`: the block decides who calls the model."""
    with_block = contract_with(["read"], model=scripted_model()).to_dict("t")
    with pytest.raises(ValueError, match="takes no transport"):
        asyncio.run(foe.run_config(with_block, transport=scripted([]), binary=fake_binary, log_dir=tmp_path / "e"))
    without_block = contract_with(["read"]).to_dict("t")
    with pytest.raises(ValueError, match="needs a transport"):
        asyncio.run(foe.run_config(without_block, binary=fake_binary, log_dir=tmp_path / "e"))


def test_a_child_model_block_under_a_host_transport_is_refused(fake_binary: Path, tmp_path: Path) -> None:
    """A descendant's recorded request is not distinguishable from one the host owes."""
    doc = contract_with(["read"]).to_dict("t")
    doc["child_contracts"] = {"survey": contract_with(["read"], model=scripted_model()).to_dict(child=True)}
    with pytest.raises(ValueError, match="child_contracts: survey declares a `model` block"):
        asyncio.run(foe.run_config(doc, transport=scripted([]), binary=fake_binary, log_dir=tmp_path / "e"))


def test_a_model_block_runs_through_the_host_with_its_host_tools(fake_binary: Path, tmp_path: Path) -> None:
    """The built-in transport answers the model; the host still serves `host/tool-call`."""
    events: list[foe.Event] = []
    outcome = asyncio.run(
        contract_with(["read", reference_count], model=scripted_model()).run(
            task="Count the references.",
            binary=fake_binary,
            log_dir=tmp_path / "episode",
            on_event=events.append,
        )
    )
    assert outcome == foe.Completed(SUMMARY)

    # The route names the configured provider, so no `model/chunk` was owed.
    header = next(e for e in events if e.type == "request/header")
    assert header.data["model"] == {"provider": "exec", "model": "host-tool-then-text"}
    call = next(e for e in events if e.type == "host/tool-call")
    assert call.data["name"] == "reference_count"
    result = next(e for e in events if e.type == "tool/result")
    assert result.data["value"] == {"count": 3, "symbol": "add"}


def test_the_handle_carries_the_process_id_and_the_runtime_build(fake_binary: Path, tmp_path: Path) -> None:
    """Both are known before the first tool call, which the host tool records."""
    started: list[foe.Handle] = []
    at_call: list[tuple[int, foe.Runtime | None]] = []

    @foe.tool(name="reference_count")
    def record_identity(symbol: str) -> dict[str, Any]:
        """Count the references to a symbol."""
        at_call.append((started[0].pid, started[0].runtime))
        return {"count": 3, "symbol": symbol}

    async def scenario() -> foe.Outcome:
        handle = await contract_with(["read", record_identity], model=scripted_model()).start(
            task="Count the references.", binary=fake_binary, log_dir=tmp_path / "episode"
        )
        started.append(handle)
        assert handle.pid > 0
        assert handle.runtime == foe.Runtime(version="0.2.0", build="sha256:" + "0" * 64)
        return await handle.wait()

    assert asyncio.run(scenario()) == foe.Completed(SUMMARY)
    assert at_call == [(started[0].pid, started[0].runtime)]


def test_serve_returns_the_url(fake_binary: Path, tmp_path: Path) -> None:
    async def scenario() -> str:
        viewer = await foe.serve(tmp_path, binary=fake_binary)
        try:
            return viewer.url
        finally:
            await viewer.close()

    assert asyncio.run(scenario()) == "http://127.0.0.1:34567/"
