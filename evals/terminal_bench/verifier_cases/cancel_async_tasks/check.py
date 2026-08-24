#!/usr/bin/env python3
"""Check the public concurrency and cancellation contract for /app/run.py."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


async def check_concurrency(run_tasks: Any) -> list[str]:
    active = 0
    peak = 0
    completed: list[int] = []
    lock = asyncio.Lock()

    async def job(index: int) -> None:
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.02)
        completed.append(index)
        async with lock:
            active -= 1

    factories = [lambda index=index: job(index) for index in range(8)]
    await asyncio.wait_for(run_tasks(factories, 3), timeout=3)
    findings = []
    if sorted(completed) != list(range(8)):
        findings.append("run_tasks did not complete each supplied task exactly once")
    if peak != 3:
        findings.append(
            f"max_concurrent=3 reached peak concurrency {peak}; expected exactly 3"
        )
    return findings


async def check_cancellation(run_tasks: Any) -> list[str]:
    started = 0
    cleaned = 0
    two_started = asyncio.Event()

    async def job() -> None:
        nonlocal started, cleaned
        started += 1
        if started == 2:
            two_started.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleaned += 1

    outer = asyncio.create_task(run_tasks([job for _ in range(5)], 2))
    try:
        await asyncio.wait_for(two_started.wait(), timeout=2)
        outer.cancel()
        try:
            await asyncio.wait_for(outer, timeout=2)
        except asyncio.CancelledError:
            pass
        except BaseException as error:
            return [
                "cancelling run_tasks raised "
                f"{type(error).__name__} rather than cancellation"
            ]
        await asyncio.sleep(0)
    finally:
        if not outer.done():
            outer.cancel()
            try:
                await outer
            except BaseException:
                pass
    findings = []
    if cleaned != started:
        findings.append(
            f"cancellation started {started} task(s) but ran cleanup for {cleaned}"
        )
    return findings


def load_run_tasks(path: Path) -> Any:
    namespace: dict[str, Any] = {"__name__": "foe_completion_candidate"}
    source = path.read_text(encoding="utf-8")
    exec(compile(source, str(path), "exec"), namespace)
    return namespace.get("run_tasks")


async def main() -> list[str]:
    path = Path("/app/run.py")
    if not path.is_file():
        return ["/app/run.py does not exist"]
    try:
        run_tasks = load_run_tasks(path)
    except (OSError, SyntaxError, ImportError) as error:
        return [f"/app/run.py cannot be loaded: {type(error).__name__}: {error}"]
    if not callable(run_tasks):
        return ["/app/run.py does not define a callable run_tasks"]
    findings = []
    try:
        findings.extend(await check_concurrency(run_tasks))
    except BaseException as error:
        findings.append(
            f"the concurrency probe failed: {type(error).__name__}: {error}"
        )
    try:
        findings.extend(await check_cancellation(run_tasks))
    except BaseException as error:
        findings.append(
            f"the cancellation probe failed: {type(error).__name__}: {error}"
        )
    return findings


if __name__ == "__main__":
    for finding in asyncio.run(main()):
        print(finding)
