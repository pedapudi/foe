"""The compatibility surface between this package and a foe binary.

Three versions have to agree for an episode to run: the configuration
format the package writes, the log format the binary writes, and the
runtime version, which docs/protocol.md "Versioning" makes the version of
the protocol the two speak. The first group of tests pins the package's
constants to the definitions in this repository, so a change to either
side that leaves the other behind fails here. The second group drives a
binary that states a version the package does not read and shows that the
episode is refused at its start rather than partway through.
"""

from __future__ import annotations

import asyncio
import json
import re
import tomllib
from pathlib import Path
from typing import Any, Callable

import pytest

import foe

from scripted import SUMMARY, reference_count, scripted, text_response, tool_response
from test_run import contract_with

REPOSITORY = Path(__file__).resolve().parents[2]


def rust_constant(source: Path, name: str) -> int:
    """The integer a `pub const NAME: u32 = N;` item declares."""
    found = re.search(rf"pub const {name}: u32 = (\d+);", source.read_text(encoding="utf-8"))
    assert found is not None, f"{source}: no `pub const {name}`"
    return int(found.group(1))


def test_the_package_writes_the_configuration_format_the_binary_accepts() -> None:
    """`CONTRACT_FORMAT_VERSION` in crates/contract is the only version it reads."""
    accepted = rust_constant(REPOSITORY / "crates/contract/src/document.rs", "CONTRACT_FORMAT_VERSION")
    assert foe.CONFIG_VERSION == accepted
    assert contract_with(["read"]).to_dict("t")["version"] == accepted


def test_the_package_reads_the_log_format_the_binary_writes() -> None:
    """`LOG_VERSION` in crates/log is the version the first event states."""
    assert foe.LOG_FORMAT_VERSION == rust_constant(REPOSITORY / "crates/log/src/lib.rs", "LOG_VERSION")


def test_the_package_speaks_the_protocol_of_the_runtime_it_ships_with() -> None:
    """The workspace version is what `episode/start.runtime.version` states."""
    workspace = tomllib.loads((REPOSITORY / "Cargo.toml").read_text(encoding="utf-8"))
    runtime_version = str(workspace["workspace"]["package"]["version"])
    assert runtime_version.startswith(f"{foe.PROTOCOL_VERSION}.")
    assert foe.__version__ == runtime_version


def run_one(binary: Path, log_dir: Path) -> foe.Outcome:
    """One episode of the smallest contract that reaches a host tool."""
    responses = [tool_response(("reference_count", {"symbol": "add"})), text_response(SUMMARY)]
    return asyncio.run(
        contract_with(["read", reference_count]).run(
            task="Count the references.", model_backend=scripted(responses), binary=binary, log_dir=log_dir
        )
    )


def test_a_log_format_the_package_does_not_read_ends_the_episode_at_its_start(
    fake_binary_stating: Callable[..., Path], tmp_path: Path
) -> None:
    unread = foe.LOG_FORMAT_VERSION + 1
    binary = fake_binary_stating("--log-version", str(unread))
    log_dir = tmp_path / "episode"
    with pytest.raises(foe.CompatibilityError, match=f"log format: the binary writes version {unread}"):
        run_one(binary, log_dir)
    assert last_outcome(log_dir) == {"kind": "failed", "error": "cancelled"}


def test_a_runtime_whose_protocol_the_package_does_not_speak_ends_the_episode_at_its_start(
    fake_binary_stating: Callable[..., Path], tmp_path: Path
) -> None:
    binary = fake_binary_stating("--runtime-version", "9.9.9")
    log_dir = tmp_path / "episode"
    with pytest.raises(foe.CompatibilityError, match="the binary states runtime version '9.9.9'"):
        run_one(binary, log_dir)
    assert last_outcome(log_dir) == {"kind": "failed", "error": "cancelled"}


def test_a_first_event_stating_no_log_version_is_read_as_version_three(
    fake_binary_stating: Callable[..., Path], tmp_path: Path
) -> None:
    """docs/log-format.md: absence identifies a version 3 log."""
    binary = fake_binary_stating("--log-version", "none")
    assert run_one(binary, tmp_path / "episode") == foe.Completed(SUMMARY)


def last_outcome(log_dir: Path) -> dict[str, Any]:
    """The outcome of the `episode/end` event the binary wrote."""
    events = [json.loads(line) for line in (log_dir / "episode.jsonl").read_text(encoding="utf-8").splitlines()]
    return dict(events[-1]["data"]["outcome"])
