#!/usr/bin/python3
"""Identify a clean Foe source tree and the executable evaluated from it."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise ValueError(f"cannot identify Foe source tree at {root}: git {' '.join(args)}: {detail}")
    return result.stdout.strip()


def clean_source_tree(path: Path) -> str:
    """Return the Git tree object for a checkout whose tracked and untracked state is clean."""
    candidate = path.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    root = Path(_git(candidate, "rev-parse", "--show-toplevel")).resolve()
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise ValueError(f"Foe source tree is not clean at {root}:\n{status}")
    object_format = _git(root, "rev-parse", "--show-object-format")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    return f"git-tree-{object_format}:{tree}"


def sha256_file(path: Path) -> str:
    candidate = path.resolve()
    if not candidate.is_file():
        raise ValueError(f"Foe runtime binary does not exist: {candidate}")
    digest = hashlib.sha256()
    with candidate.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def evaluated_foe(source: Path, binary: Path) -> dict[str, str]:
    return {
        "source_tree": clean_source_tree(source),
        "runtime_binary": sha256_file(binary),
    }


def require_evaluated_foe(value: Any, context: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} lacks evaluated_foe")
    source = value.get("source_tree")
    binary = value.get("runtime_binary")
    source_match = (
        re.fullmatch(r"git-tree-(sha1|sha256):([0-9a-f]+)", source)
        if isinstance(source, str)
        else None
    )
    if source_match is None:
        raise ValueError(f"{context} evaluated_foe.source_tree is missing or malformed")
    expected_length = 40 if source_match.group(1) == "sha1" else 64
    if len(source_match.group(2)) != expected_length:
        raise ValueError(f"{context} evaluated_foe.source_tree is missing or malformed")
    if not isinstance(binary, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", binary) is None:
        raise ValueError(f"{context} evaluated_foe.runtime_binary is missing or malformed")
    return {"source_tree": source, "runtime_binary": binary}
