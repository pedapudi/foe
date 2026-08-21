"""Capability handles apply the prefix rule of docs/config.md `grants`."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import foe


def test_readfs_reads_within_roots_and_refuses_outside(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hello")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    fs = foe.ReadFS([root])
    assert fs.read_text(root / "a.txt") == "hello"
    assert fs.exists(root / "a.txt")
    assert [p.name for p in fs.walk(root)] == ["a.txt"]
    with pytest.raises(foe.CapabilityError, match="outside every granted root"):
        fs.read_text(outside)


def test_readfs_resolves_symlinks_before_checking(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    os.symlink(outside, root / "link.txt")
    fs = foe.ReadFS([root])
    with pytest.raises(foe.CapabilityError):
        fs.read_text(root / "link.txt")


def test_writefs_writes_atomically_within_roots(tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    fs = foe.WriteFS([root])
    fs.mkdir(root / "sub")
    fs.write_text(root / "sub" / "note.txt", "one")
    fs.write_text(root / "sub" / "note.txt", "two")
    assert (root / "sub" / "note.txt").read_text() == "two"
    assert sorted(p.name for p in (root / "sub").iterdir()) == ["note.txt"]
    with pytest.raises(foe.CapabilityError):
        fs.write_text(tmp_path / "elsewhere.txt", "no")


def test_exec_runs_declared_executables_only(tmp_path: Path) -> None:
    ex = foe.Exec([sys.executable])
    result = ex.run(sys.executable, ["-c", "import sys; print('out'); sys.exit(3)"])
    assert result.exit_code == 3
    assert result.stdout == b"out\n"
    assert not result.timed_out
    with pytest.raises(foe.CapabilityError, match="not a declared executable"):
        ex.run("/bin/sh", ["-c", "true"])
