"""Runs config.json with a transport that returns scripted responses instead of calling a model."""

import asyncio
from pathlib import Path

import foe

HERE = Path(__file__).resolve().parent
USAGE = {"input": 0, "output": 0, "cache_read": 0}
SCRIPT = [
    [
        {"kind": "tool_call_start", "id": "tc_1", "name": "read"},
        {"kind": "tool_call_delta", "id": "tc_1", "delta": '{"path": "/home/user/project/README.md"}'},
        {"kind": "tool_call_end", "id": "tc_1"},
        {"kind": "done", "stop": "tool", "usage": USAGE},
    ],
    [
        {"kind": "text", "delta": "The README describes the project and how to build it."},
        {"kind": "done", "stop": "end", "usage": USAGE},
    ],
]


async def transport(request):
    # One scripted response per step; the step is the count of assistant messages so far.
    step = sum(1 for message in request["messages"] if message["role"] == "assistant")
    for chunk in SCRIPT[min(step, len(SCRIPT) - 1)]:
        yield chunk


async def main():
    outcome = await foe.run_config(
        HERE / "config.json", transport=transport, binary="/usr/local/bin/foe", log_dir=HERE / "episode"
    )
    print(outcome)


asyncio.run(main())
