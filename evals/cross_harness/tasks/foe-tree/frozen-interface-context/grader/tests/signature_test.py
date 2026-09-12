#!/usr/bin/python3
"""The signature of foe.Context is the one docs/config.md and docs/sdk.md specify; any change to it fails this test."""

import inspect
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path.cwd() / "python"))

import foe  # noqa: E402

SPECIFIED = "(compact: 'bool' = True, window_tokens: 'int | None' = None, reserve_tokens: 'int | None' = None, keep_recent_tokens: 'int | None' = None, margin_tokens: 'int | None' = None) -> None"


class Signature(unittest.TestCase):
    def test_the_signature_is_the_specified_one(self) -> None:
        actual = str(inspect.signature(foe.Context))
        if actual != SPECIFIED:
            self.fail(f"foe.Context takes {actual}; docs/config.md fixes the keys of the context block, so its signature stays {SPECIFIED}")


if __name__ == "__main__":
    unittest.main()
