from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

FAKE = Path(__file__).with_name("fake_foe.py")


@pytest.fixture
def fake_binary(tmp_path: Path) -> Path:
    """An executable that runs tests/fake_foe.py with the current interpreter."""
    script = tmp_path / "foe"
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
    os.chmod(script, 0o755)
    return script
