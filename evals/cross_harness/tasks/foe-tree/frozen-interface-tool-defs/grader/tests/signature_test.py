#!/usr/bin/python3
"""The signature of foe.ToolDef is the one docs/config.md and docs/sdk.md specify; any change to it fails this test."""

import inspect
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path.cwd() / "python"))

import foe  # noqa: E402

SPECIFIED = "(exec: 'PathLike', description: 'str', instruction: 'str | None' = None, network: 'bool' = False, timeout_seconds: 'int | None' = None, cwd: 'PathLike | None' = None) -> None"


class Signature(unittest.TestCase):
    def test_the_signature_is_the_specified_one(self) -> None:
        actual = str(inspect.signature(foe.ToolDef))
        if actual != SPECIFIED:
            self.fail(f"foe.ToolDef takes {actual}; docs/config.md fixes the keys of the tool_defs block, so its signature stays {SPECIFIED}")


if __name__ == "__main__":
    unittest.main()
