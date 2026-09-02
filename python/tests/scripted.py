"""Model responses for tests, and the host tool the scripted ones call.

A response reaches an episode one of two ways. `scripted` builds a
transport the host serves over the protocol. `scripted_model` builds the
`model` block that leaves the model to the binary's own transport, whose
`exec` provider runs `exec_transport.py`. Both drive the same host tool,
`reference_count`, so a test can compare the two seams.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Sequence

import foe

Chunks = list[dict[str, Any]]

USAGE = {"input": 10, "output": 5, "cache_read": 0}

EXEC_TRANSPORT = Path(__file__).with_name("exec_transport.py")

# What `exec_transport.py` answers with once the host tool has been called.
SUMMARY = "Done: 3 references."


def scripted_model() -> foe.Model:
    """A `model` block the built-in transport answers without a credential."""
    return foe.Model(provider="exec", model="host-tool-then-text", options={"exec": str(EXEC_TRANSPORT)})


@foe.tool
def reference_count(symbol: str) -> dict[str, Any]:
    """Count the references to a symbol. The scripted responses call this name."""
    return {"count": 3, "symbol": symbol}


def text_response(text: str) -> Chunks:
    return [{"kind": "text", "delta": text}, {"kind": "done", "stop": "end", "usage": USAGE}]


def tool_response(*calls: tuple[str, dict[str, Any]], text: str = "") -> Chunks:
    chunks: Chunks = []
    if text:
        chunks.append({"kind": "text", "delta": text})
    for index, (name, args) in enumerate(calls):
        call_id = f"tc_{index + 1}"
        chunks.append({"kind": "tool_call_start", "id": call_id, "name": name})
        arguments = json.dumps(args)
        chunks.append({"kind": "tool_call_delta", "id": call_id, "delta": arguments[: len(arguments) // 2]})
        chunks.append({"kind": "tool_call_delta", "id": call_id, "delta": arguments[len(arguments) // 2 :]})
        chunks.append({"kind": "tool_call_end", "id": call_id})
    chunks.append({"kind": "done", "stop": "tool", "usage": USAGE})
    return chunks


def scripted(
    responses: Sequence[Chunks], requests: list[dict[str, Any]] | None = None
) -> Callable[[dict[str, Any]], AsyncIterator[dict[str, Any]]]:
    """A transport that answers the n-th request with the n-th response.

    Every request received is appended to `requests` when given. A request
    beyond the script ends the episode with an error chunk.
    """
    queue = list(responses)

    async def transport(request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        if requests is not None:
            requests.append(request)
        if not queue:
            yield {"kind": "error", "message": "script exhausted", "retryable": False}
            return
        for chunk in queue.pop(0):
            yield chunk

    return transport
