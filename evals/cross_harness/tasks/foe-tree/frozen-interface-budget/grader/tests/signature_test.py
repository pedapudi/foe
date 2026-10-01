#!/usr/bin/python3
"""The signature of foe.Budget is the one docs/config.md and docs/sdk.md specify; any change to it fails this test."""

import inspect
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path.cwd() / "python"))

import foe  # noqa: E402

SPECIFIED = "(model_calls: 'int | str', input_tokens: 'int | None' = None, output_tokens: 'int | None' = None, seconds: 'int | None' = None, max_depth: 'int | None' = None, max_episodes: 'int | None' = None, max_concurrent: 'int | None' = None, loop_threshold: 'int | None' = None) -> None"


class Signature(unittest.TestCase):
    def test_the_signature_is_the_specified_one(self) -> None:
        actual = str(inspect.signature(foe.Budget))
        if actual != SPECIFIED:
            self.fail(f"foe.Budget takes {actual}; docs/config.md fixes the keys of the budget block, so its signature stays {SPECIFIED}")


if __name__ == "__main__":
    unittest.main()
