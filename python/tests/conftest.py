from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Sequence

import pytest

FAKE = Path(__file__).with_name("fake_foe.py")


def _stand_in(directory: Path, options: Sequence[str]) -> Path:
    """An executable that runs tests/fake_foe.py with the current interpreter."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "foe"
    leading = "".join(f'"{option}" ' for option in options)
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" {leading}"$@"\n', encoding="utf-8")
    os.chmod(script, 0o755)
    return script


@pytest.fixture
def fake_binary(tmp_path: Path) -> Path:
    """A stand-in binary stating the versions the built binary states."""
    return _stand_in(tmp_path, ())


@pytest.fixture
def fake_binary_stating(tmp_path: Path) -> Callable[..., Path]:
    """Builds a stand-in binary that states the versions the caller names.

    The options are those tests/fake_foe.py accepts before the command line
    the package issues: `--log-version` and `--runtime-version`.
    """
    made = 0

    def build(*options: str) -> Path:
        nonlocal made
        made += 1
        return _stand_in(tmp_path / f"binary-{made}", options)

    return build
