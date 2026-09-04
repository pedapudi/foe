#!/usr/bin/python3
"""Run one configuration with responses supplied by the host."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "python"))

import foe  # noqa: E402

Chunk = dict[str, Any]
Request = dict[str, Any]
Responder = Callable[[Request], list[Chunk]]


def run(binary: Path, config: Path, log_dir: Path, responder: Responder) -> int:
    """Return the command status for an episode served by one response function."""

    async def serve() -> foe.Outcome:
        async def transport(request: Request) -> AsyncIterator[Chunk]:
            chunks = await asyncio.to_thread(responder, request)
            for chunk in chunks:
                yield chunk

        return await foe.run_config(config, transport=transport, binary=binary, log_dir=log_dir)

    outcome = asyncio.run(serve())
    match outcome:
        case foe.Completed():
            return 0
        case foe.Failed():
            return 1
        case foe.Blocked():
            return 2
        case foe.Exhausted():
            return 3
    raise AssertionError(f"unknown outcome {outcome!r}")
