"""Capability handles passed to host tools.

Each handle is bounded to the roots the program granted. Every path is
resolved through symbolic links and checked against the roots before use,
which is the same prefix rule the runtime applies to its own tools. The
handle is a convenience for writing a host tool that behaves like a
built-in one; the host process itself is never sandboxed, so a host tool
that reaches the filesystem without the handle is outside this check.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from ._errors import CapabilityError

PathLike = str | os.PathLike[str]


def _canonical(path: PathLike) -> Path:
    return Path(os.path.realpath(os.fspath(path)))


def _within(path: Path, roots: Sequence[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


class ReadFS:
    """Filesystem reads bounded to the read roots."""

    def __init__(self, roots: Sequence[PathLike]) -> None:
        self.roots: tuple[Path, ...] = tuple(_canonical(r) for r in roots)

    def resolve(self, path: PathLike) -> Path:
        """The canonical form of `path`, or an error when it lies outside every root."""
        canonical = _canonical(path)
        if not _within(canonical, self.roots):
            raise CapabilityError(f"{canonical}: outside every granted root")
        return canonical

    def read_bytes(self, path: PathLike) -> bytes:
        return self.resolve(path).read_bytes()

    def read_text(self, path: PathLike, encoding: str = "utf-8") -> str:
        return self.resolve(path).read_text(encoding=encoding)

    def exists(self, path: PathLike) -> bool:
        return self.resolve(path).exists()

    def walk(self, root: PathLike) -> Iterator[Path]:
        """Every file below `root`, as canonical paths, in sorted order."""
        start = self.resolve(root)
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames.sort()
            for name in sorted(filenames):
                yield Path(dirpath) / name


class WriteFS:
    """Filesystem writes bounded to the write roots.

    `write_bytes` replaces the file atomically: it stages beside the target
    and renames.
    """

    def __init__(self, roots: Sequence[PathLike]) -> None:
        self.roots: tuple[Path, ...] = tuple(_canonical(r) for r in roots)

    def resolve(self, path: PathLike) -> Path:
        """The canonical form of `path`, or an error when it lies outside every root.

        A path that does not exist yet is resolved through its existing
        ancestors, so a new file under a granted root is permitted.
        """
        canonical = _canonical(path)
        if not _within(canonical, self.roots):
            raise CapabilityError(f"{canonical}: outside every granted root")
        return canonical

    def write_bytes(self, path: PathLike, data: bytes) -> None:
        target = self.resolve(path)
        fd, staged = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(staged, target)
        except BaseException:
            if os.path.exists(staged):
                os.unlink(staged)
            raise

    def write_text(self, path: PathLike, text: str, encoding: str = "utf-8") -> None:
        self.write_bytes(path, text.encode(encoding))

    def mkdir(self, path: PathLike) -> None:
        self.resolve(path).mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True, slots=True)
class ExecResult:
    """What a process produced. `exit_code` is None when it was killed."""

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    duration_ms: int


class Exec:
    """Process execution bounded to the executables declared in `tool_defs`.

    A process receives a fixed argument vector and a constructed
    environment, never a shell. Standard input is empty unless `stdin` is
    given.
    """

    def __init__(self, executables: Sequence[PathLike]) -> None:
        self.executables: tuple[Path, ...] = tuple(_canonical(e) for e in executables)

    def resolve(self, program: PathLike) -> Path:
        canonical = _canonical(program)
        if canonical not in self.executables:
            raise CapabilityError(f"{canonical}: not a declared executable")
        return canonical

    def run(
        self,
        program: PathLike,
        args: Sequence[str] = (),
        *,
        cwd: PathLike | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 120.0,
        stdin: bytes | None = None,
    ) -> ExecResult:
        path = self.resolve(program)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [str(path), *args],
                cwd=None if cwd is None else os.fspath(cwd),
                env=dict(env or {}),
                input=stdin if stdin is not None else b"",
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as expired:
            elapsed = int((time.monotonic() - started) * 1000)
            return ExecResult(None, expired.stdout or b"", expired.stderr or b"", True, elapsed)
        elapsed = int((time.monotonic() - started) * 1000)
        return ExecResult(completed.returncode, completed.stdout, completed.stderr, False, elapsed)
