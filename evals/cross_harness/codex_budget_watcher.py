#!/usr/bin/python3
"""Enforce a token and wall-clock budget on a Codex CLI run from outside it.

Codex CLI enforces no budget of its own, so the cross-harness comparison
supplies one. A watcher follows the session files Codex writes while the
run is in progress, sums the usage they report, and terminates the whole
process tree when a limit is crossed. The record of why the run ended, a
`Stop`, gives the comparison the same kind of fact a foe episode's
`exhausted` outcome states.

Codex writes one JSON Lines session file per thread under
`$CODEX_HOME/sessions/YYYY/MM/DD/`, and a child agent writes its own file
beside the parent's. Files appear during the run, so the watcher lists the
directory on every poll and reads each file from the byte offset where its
previous read ended, keeping an unterminated final line for the next read.

Two kinds of record report usage, and the accounting rule between them is:

1. A `token_usage_record` is one model response, and its `usage` field
   holds that response's tokens. The ledger counts each `response_id` once
   across every file, so a response that appears in two files is one
   response. A record without a `response_id` is keyed by its file and line.
2. A file that holds no `token_usage_record` contributes the sum of the
   `last_token_usage` fields of its `token_count` event messages instead.
   Each such message reports the usage of the most recent response, so the
   sum over a file's messages is that file's total.
3. A file's `token_count` messages are ignored once the file holds a
   `token_usage_record`, because both describe the same responses.

`input_tokens` is the count Codex reports under that name, which includes
the cached prefix that `cached_input_tokens` also reports. A limit on
`input_tokens` therefore bounds the tokens the model read, whether or not
they were served from the cache.

The watcher terminates the process by signalling its process group, so the
process must be the leader of a group of its own. `run_with_watcher` starts
a command that way and returns its output together with the `Stop`, when
there was one. This module reads no environment variable; the caller passes
`CODEX_HOME` to the child process because Codex locates its session files
by it and offers no flag.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

TOKEN_DIMENSIONS = ("input_tokens", "output_tokens")
DIMENSIONS = (*TOKEN_DIMENSIONS, "seconds")

# The usage fields Codex reports on every response, summed by the ledger.
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens")

Usage = dict[str, int]


def empty_usage() -> Usage:
    return {name: 0 for name in USAGE_FIELDS}


def add_usage(total: Usage, part: Usage) -> None:
    for name in USAGE_FIELDS:
        total[name] += part[name]


def parse_usage(value: Any, where: str) -> Usage:
    """The integer usage a record reports; an absent field counts as zero.

    `where` names the file and line for the error a malformed field raises.
    """
    if not isinstance(value, dict):
        raise ValueError(f"{where}: usage is {type(value).__name__} rather than an object")
    usage = empty_usage()
    for name in USAGE_FIELDS:
        raw = value.get(name)
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(f"{where}: usage field {name} is {raw!r} rather than an integer")
        usage[name] = raw
    return usage


@dataclass(frozen=True)
class Stop:
    """Why the watcher terminated the run.

    `dimension` is one of DIMENSIONS, `observed` is the value that crossed
    `limit`, and `at_ms` is the wall-clock time of the decision in
    milliseconds since the epoch, on the clock the session files' timestamps
    use.
    """

    reason: str
    dimension: str
    observed: int | float
    limit: int | float
    at_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "dimension": self.dimension, "observed": self.observed, "limit": self.limit, "at_ms": self.at_ms}


def check_limits(limits: Mapping[str, int | float]) -> dict[str, int | float]:
    """The limits as a plain dict; an unknown key or a non-positive value is an error."""
    checked: dict[str, int | float] = {}
    for key, value in limits.items():
        if key not in DIMENSIONS:
            raise ValueError(f"limits: unknown key {key!r}; the keys are {', '.join(DIMENSIONS)}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"limits: {key} is {value!r} rather than a number")
        if key in TOKEN_DIMENSIONS and not isinstance(value, int):
            raise ValueError(f"limits: {key} is {value!r} rather than an integer")
        if value <= 0:
            raise ValueError(f"limits: {key} is {value!r}; a limit is positive")
        checked[key] = value
    return checked


def check_intervals(poll_interval: float, grace_seconds: float) -> None:
    """A non-positive poll interval or a negative grace period is an error."""
    if poll_interval <= 0:
        raise ValueError(f"poll_interval is {poll_interval!r}; the interval is positive")
    if grace_seconds < 0:
        raise ValueError(f"grace_seconds is {grace_seconds!r}; the grace period is zero or more")


@dataclass
class SessionFile:
    """One session file and the usage read from it so far."""

    path: Path
    offset: int = 0
    partial: bytes = b""
    line: int = 0
    # Per-response usage keyed as the ledger counts it: rule 1 above.
    responses: dict[str, Usage] = field(default_factory=dict)
    # The sum of `token_count` messages, used only under rule 2.
    counted: Usage = field(default_factory=empty_usage)
    count_messages: int = 0


class Ledger:
    """The usage every session file under one CODEX_HOME reports, read incrementally."""

    def __init__(self, codex_home: Path) -> None:
        self.codex_home = Path(codex_home)
        self.sessions = self.codex_home / "sessions"
        self.files: dict[Path, SessionFile] = {}
        # A response counted from an earlier file is skipped in a later one.
        self._responses: set[str] = set()
        # Lines the ledger could not use, each naming the file and line.
        self.problems: list[str] = []

    def refresh(self) -> None:
        """Discover new session files and read what every known file gained."""
        if self.sessions.is_dir():
            for path in sorted(self.sessions.rglob("*.jsonl")):
                if path not in self.files and path.is_file():
                    self.files[path] = SessionFile(path)
        for session in self.files.values():
            self._read(session)

    def _read(self, session: SessionFile) -> None:
        try:
            size = session.path.stat().st_size
        except FileNotFoundError:
            return
        if size < session.offset:
            self.problems.append(f"{session.path}: shrank from {session.offset} to {size} bytes; reading it again from the start")
            # The per-response records stay: the ledger skips a response it
            # has counted. The running counts are summed again from zero.
            session.offset, session.partial, session.line = 0, b"", 0
            session.counted, session.count_messages = empty_usage(), 0
        if size == session.offset:
            return
        with session.path.open("rb") as handle:
            handle.seek(session.offset)
            chunk = handle.read()
        session.offset += len(chunk)
        data = session.partial + chunk
        lines = data.split(b"\n")
        session.partial = lines.pop()
        for raw in lines:
            session.line += 1
            if raw.strip():
                self._record(session, raw)

    def _record(self, session: SessionFile, raw: bytes) -> None:
        where = f"{session.path}:{session.line}"
        try:
            record = json.loads(raw)
        except ValueError as exc:
            self.problems.append(f"{where}: {exc}")
            return
        if not isinstance(record, dict):
            return
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return
        try:
            if record.get("type") == "token_usage_record":
                key = payload.get("response_id")
                key = where if not isinstance(key, str) or not key else key
                if key in self._responses:
                    return
                session.responses[key] = parse_usage(payload.get("usage"), where)
                self._responses.add(key)
            elif record.get("type") == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info")
                if isinstance(info, dict) and info.get("last_token_usage") is not None:
                    add_usage(session.counted, parse_usage(info["last_token_usage"], where))
                    session.count_messages += 1
        except ValueError as exc:
            self.problems.append(str(exc))

    def totals(self) -> Usage:
        """The usage of the run so far under the accounting rule of the module docstring."""
        total = empty_usage()
        for session in self.files.values():
            if session.responses:
                for usage in session.responses.values():
                    add_usage(total, usage)
            else:
                add_usage(total, session.counted)
        return total

    def responses(self) -> int:
        """How many responses the totals cover."""
        return sum(len(s.responses) if s.responses else s.count_messages for s in self.files.values())


class Watcher:
    """Follow a running Codex process's session files and stop it when a limit is crossed.

    `process` must lead its own process group, as `subprocess.Popen` with
    `start_new_session=True` makes it, because termination signals the
    group so that child agents and shell commands end with the parent.
    `limits` maps any of DIMENSIONS to a bound; an absent dimension is
    unbounded. A crossing is `observed > limit`. Termination sends SIGTERM
    to the group and SIGKILL `grace_seconds` later when the process has not
    exited.

    The watch runs in a thread from `start` until the process exits or a
    limit is crossed; `join` waits for it. `stop` holds the `Stop` when the
    watcher terminated the process. A crossing found after the process has
    exited records no `Stop`: the process ended on its own, its exit status
    stands, and `usage` still reports the totals. `poll` performs one pass
    without the thread; it checks that the process is still running before
    it records a `Stop`, so a crossing the process's final records cause
    leaves `stop` empty.

    An exception inside a pass of the thread ends the budget, so the thread
    stores it in `failure`, terminates the process, and exits; the caller
    checks `failure` after `join`. `ledger` is the `Ledger` the watcher
    sums usage into; the caller may pass its own to read the totals after
    the run.
    """

    def __init__(
        self,
        codex_home: Path,
        limits: Mapping[str, int | float],
        process: subprocess.Popen[bytes],
        poll_interval: float = 0.25,
        grace_seconds: float = 5.0,
        ledger: Ledger | None = None,
    ) -> None:
        check_intervals(poll_interval, grace_seconds)
        self.limits = check_limits(limits)
        self.process = process
        self.poll_interval = poll_interval
        self.grace_seconds = grace_seconds
        codex_home = Path(codex_home)
        if ledger is None:
            ledger = Ledger(codex_home)
        elif ledger.codex_home != codex_home:
            raise ValueError(f"ledger reads {str(ledger.codex_home)!r} while codex_home is {str(codex_home)!r}; the two name one directory")
        self.ledger = ledger
        self.stop: Stop | None = None
        self.failure: BaseException | None = None
        self.started = time.monotonic()
        self._thread: threading.Thread | None = None
        try:
            group = os.getpgid(process.pid)
        except ProcessLookupError:
            group = process.pid
        if group != process.pid:
            raise ValueError(f"process {process.pid} is in process group {group} rather than leading its own; start it with start_new_session=True")

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    def usage(self) -> Usage:
        return self.ledger.totals()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._watch, name="codex-budget-watcher", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def poll(self) -> Stop | None:
        """One pass: read what the files gained, and terminate the process when a limit is crossed.

        A crossing found once the process has exited records no `Stop`.
        """
        self.ledger.refresh()
        if self.stop is not None or self.process.poll() is not None:
            return self.stop
        stop = self.crossed()
        if stop is not None:
            self.stop = stop
            self.terminate()
        return self.stop

    def crossed(self) -> Stop | None:
        """The first limit the current totals and elapsed time cross, in DIMENSIONS order."""
        totals = self.ledger.totals()
        elapsed = time.monotonic() - self.started
        for dimension in DIMENSIONS:
            limit = self.limits.get(dimension)
            if limit is None:
                continue
            observed: int | float = round(elapsed, 3) if dimension == "seconds" else totals[dimension]
            if observed > limit:
                unit = "s" if dimension == "seconds" else " tokens"
                reason = f"{dimension} reached {observed}{unit} against a limit of {limit}{unit} after {elapsed:.1f} s"
                return Stop(reason, dimension, observed, limit, int(time.time() * 1000))
        return None

    def terminate(self) -> None:
        """SIGTERM the process group, then SIGKILL it when the process outlives the grace period."""
        self._signal_group(signal.SIGTERM)
        try:
            self.process.wait(timeout=self.grace_seconds)
        except subprocess.TimeoutExpired:
            self._signal_group(signal.SIGKILL)
            self.process.wait()

    def _signal_group(self, signum: int) -> None:
        try:
            os.killpg(self.process.pid, signum)
        except ProcessLookupError:
            pass

    def _watch(self) -> None:
        try:
            while self.poll() is None:
                if self.process.poll() is not None:
                    # The process exited on its own; the records it wrote
                    # last still belong to the totals.
                    self.ledger.refresh()
                    return
                time.sleep(self.poll_interval)
        except Exception as exc:
            # Any failure of a pass ends the budget, and a run without a
            # budget does not continue.
            self.failure = exc
            if self.process.poll() is None:
                self.terminate()


def _drain(stream: Any, into: list[bytes]) -> None:
    into.append(stream.read())


def run_with_watcher(
    command: list[str],
    codex_home: Path,
    limits: Mapping[str, int | float],
    cwd: Path,
    env: Mapping[str, str],
    poll_interval: float = 0.25,
    grace_seconds: float = 5.0,
    ledger: Ledger | None = None,
) -> tuple[int | None, Stop | None, bytes, bytes]:
    """Run `command` in its own process group under a `Watcher`.

    Returns the exit status, or None when the watcher terminated the run;
    the `Stop` that says why, or None; and the bytes the command wrote to
    stdout and stderr. `env` is the child's whole environment, with
    `CODEX_HOME` set to `codex_home`; an `env` that names a different
    `CODEX_HOME` is an error, because the watcher would then read files
    Codex does not write. `ledger`, when given, receives the usage the run
    reports, so that the caller reads `ledger.totals()` afterwards.

    Every argument is validated before the command starts, so an invalid
    limit leaves no process behind. When the watcher fails during the run
    it terminates the process and this function raises the failure, since
    a run without a watcher has no budget.
    """
    codex_home = Path(codex_home)
    child_env = dict(env)
    declared = child_env.get("CODEX_HOME")
    if declared is not None and Path(declared) != codex_home:
        raise ValueError(f"env CODEX_HOME is {declared!r} while codex_home is {str(codex_home)!r}; the two name one directory")
    child_env["CODEX_HOME"] = str(codex_home)
    check_limits(limits)
    check_intervals(poll_interval, grace_seconds)
    if ledger is not None and ledger.codex_home != codex_home:
        raise ValueError(f"ledger reads {str(ledger.codex_home)!r} while codex_home is {str(codex_home)!r}; the two name one directory")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    out: list[bytes] = []
    err: list[bytes] = []
    # Reader threads rather than `communicate`, so that a descendant that
    # left the process group and kept a pipe open cannot hold the run after
    # the process itself has exited.
    readers = [threading.Thread(target=_drain, args=(process.stdout, out), daemon=True), threading.Thread(target=_drain, args=(process.stderr, err), daemon=True)]
    for reader in readers:
        reader.start()
    watcher = Watcher(codex_home, limits, process, poll_interval, grace_seconds, ledger)
    watcher.start()
    process.wait()
    watcher.join()
    for reader, stream in zip(readers, (process.stdout, process.stderr)):
        reader.join(timeout=grace_seconds)
        # A reader still blocked on the pipe holds it for a descendant that
        # outlived the group; closing under it would raise in that thread.
        if not reader.is_alive():
            stream.close()
    if watcher.failure is not None:
        raise RuntimeError(f"the budget watcher for {command[0]} failed after {watcher.elapsed_ms} ms and terminated the run: {watcher.failure}") from watcher.failure
    status = None if watcher.stop is not None else process.returncode
    return status, watcher.stop, b"".join(out), b"".join(err)
