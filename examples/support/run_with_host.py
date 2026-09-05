#!/usr/bin/python3
"""Run one configuration with deterministic responses supplied by the host."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Callable, cast

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "python"))
sys.path.insert(0, str(REPO / "examples" / "support"))

import foe  # noqa: E402

Chunk = dict[str, Any]
Request = dict[str, Any]
Responder = Callable[[Request], list[Chunk]]


def load_responder(path: Path, name: str) -> Responder:
    """Load the named response function from one example support module."""
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("foe_example_responses", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load response module {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    responder = getattr(module, name, None)
    if not callable(responder):
        raise RuntimeError(f"{path}: {name} is not a response function")
    return cast(Responder, responder)


def outcome_json(outcome: foe.Outcome) -> tuple[dict[str, Any], int]:
    """Return the protocol outcome object and its command exit status."""
    match outcome:
        case foe.Completed(value):
            return {"kind": "completed", "value": value}, 0
        case foe.Failed(error):
            return {"kind": "failed", "error": error}, 1
        case foe.Blocked(code, message):
            return {"kind": "blocked", "code": code, "message": message}, 2
        case foe.Exhausted(limit):
            return {"kind": "exhausted", "limit": limit}, 3
    raise AssertionError(f"unknown outcome {outcome!r}")


async def run(binary: Path, config: Path, log_dir: Path, responder: Responder) -> int:
    """Serve response functions without blocking the protocol loop."""

    async def model_backend(request: Request) -> AsyncIterator[Chunk]:
        chunks = await asyncio.to_thread(responder, request)
        for chunk in chunks:
            yield chunk

    outcome = await foe.run_config(config, model_backend=model_backend, binary=binary, log_dir=log_dir)
    value, status = outcome_json(outcome)
    print(json.dumps(value, separators=(",", ":")))
    return status


def main() -> int:
    if len(sys.argv) not in (5, 6):
        raise SystemExit("usage: run_with_host.py BINARY CONFIG LOG_DIR RESPONSE_MODULE [FUNCTION]")
    binary, config, log_dir, response_module = map(Path, sys.argv[1:5])
    name = sys.argv[5] if len(sys.argv) == 6 else "respond"
    return asyncio.run(run(binary, config, log_dir, load_responder(response_module, name)))


if __name__ == "__main__":
    raise SystemExit(main())
