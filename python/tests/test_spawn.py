"""docs/sdk.md: ownership callbacks precede the handshake and failures reap the child."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

import foe

from scripted import scripted, text_response


def _silent_binary(tmp_path: Path) -> Path:
    binary = tmp_path / "silent-runtime"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import signal\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True:\n"
        "    signal.pause()\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def _assert_reaped(handle: foe.Handle) -> None:
    assert handle.done
    with pytest.raises(ProcessLookupError):
        os.kill(handle.pid, 0)
    with pytest.raises(ChildProcessError):
        os.waitpid(handle.pid, os.WNOHANG)


def test_callback_exposes_owner_before_a_hung_handshake(tmp_path: Path) -> None:
    async def scenario() -> None:
        spawned: asyncio.Future[foe.Handle] = asyncio.get_running_loop().create_future()
        launch = asyncio.create_task(
            foe.start_config(
                {"task": "Wait.", "model": {}},
                binary=_silent_binary(tmp_path),
                log_dir=tmp_path / "episode",
                start_new_session=True,
                on_spawn=spawned.set_result,
            )
        )
        handle = await asyncio.wait_for(asyncio.shield(spawned), 5)
        try:
            assert not launch.done()
            assert not handle.done
            assert handle.runtime is None
            assert handle.episode_id is None
            assert os.getsid(handle.pid) == os.getpgid(handle.pid) == handle.pid
        finally:
            os.kill(handle.pid, signal.SIGKILL)
            await asyncio.wait_for(handle.wait(), 5)
            assert await asyncio.wait_for(launch, 5) is handle
        _assert_reaped(handle)

    asyncio.run(scenario())


def test_callback_exception_reaps_before_propagation_despite_cancellation(tmp_path: Path) -> None:
    expected = RuntimeError("owner registration failed")

    async def scenario() -> None:
        handles: list[foe.Handle] = []
        cancellations: list[bool] = []

        def reject(handle: foe.Handle) -> None:
            handles.append(handle)
            asyncio.get_running_loop().call_soon(cancel_again)
            raise expected

        def cancel_again() -> None:
            if not launch.done():
                cancellations.append(launch.cancel())
                if len(cancellations) < 3:
                    asyncio.get_running_loop().call_soon(cancel_again)

        launch = asyncio.create_task(
            foe.start_config(
                {"task": "Wait.", "model": {}},
                binary=_silent_binary(tmp_path),
                log_dir=tmp_path / "episode",
                on_spawn=reject,
            )
        )
        with pytest.raises(RuntimeError) as raised:
            await asyncio.wait_for(asyncio.shield(launch), 5)
        assert raised.value is expected
        assert len(cancellations) == 3
        assert all(cancellations)
        assert len(handles) == 1
        _assert_reaped(handles[0])
        assert isinstance(await handles[0].wait(), foe.Failed)

    asyncio.run(scenario())


def test_callback_handle_is_the_completed_episode_handle(fake_binary: Path, tmp_path: Path) -> None:
    async def scenario() -> None:
        handles: list[foe.Handle] = []
        contract = foe.ExecutionContract(
            name="test",
            instructions={"role": "Complete the task."},
            tools=["read"],
            grants=foe.Grants(read=["/"]),
            budget=foe.Budget(model_calls=1),
        )
        handle = await foe.start_config(
            contract.to_dict("Finish."),
            model_backend=scripted([text_response("done")]),
            binary=fake_binary,
            log_dir=tmp_path / "episode",
            on_spawn=handles.append,
        )
        assert handles == [handle]
        assert await handle.wait() == foe.Completed("done")
        _assert_reaped(handle)

    asyncio.run(scenario())
