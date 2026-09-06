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


def run(binary: Path, config: Path, log_dir: Path, responder: Responder) -> tuple[int, Path]:
    """Return the command status and episode directory for an episode served by one response function.

    `log_dir` is the directory the binary creates the episode's own directory
    under, which docs/design.md "The command line" states for `--log-dir`.
    """

    async def serve() -> tuple[foe.Outcome, Path]:
        async def model_backend(request: Request) -> AsyncIterator[Chunk]:
            chunks = await asyncio.to_thread(responder, request)
            for chunk in chunks:
                yield chunk

        handle = await foe.start_config(config, model_backend=model_backend, binary=binary, log_dir=log_dir)
        outcome = await handle.wait()
        assert handle.log_dir is not None
        return outcome, handle.log_dir

    outcome, episode = asyncio.run(serve())
    match outcome:
        case foe.Completed():
            return 0, episode
        case foe.Failed():
            return 1, episode
        case foe.Blocked():
            return 2, episode
        case foe.Exhausted():
            return 3, episode
    raise AssertionError(f"unknown outcome {outcome!r}")
