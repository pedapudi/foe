#!/bin/sh
set -eu

/bin/cat > /app/run.py <<'PY'
import asyncio
from collections.abc import Awaitable, Callable


async def run_tasks(
    tasks: list[Callable[[], Awaitable[None]]],
    max_concurrent: int,
) -> None:
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_one(task: Callable[[], Awaitable[None]]) -> None:
        async with semaphore:
            await task()

    async with asyncio.TaskGroup() as group:
        for task in tasks:
            group.create_task(run_one(task))
PY
