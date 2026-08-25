#!/usr/bin/python3

import unittest

from tool_candidate import create, executable_digest, validate, validate_definition


IDENTITY = {
    "source_tree": "git-tree-sha1:" + "1" * 40,
    "runtime_binary": "sha256:" + "2" * 64,
}
BASE = {
    "model": "openai-codex/gpt-5.6-sol",
    "reasoning_effort": "low",
    "service_tier": "default",
    "token_policy": "measurement_only",
}
EXECUTABLE = "#!/bin/sh\nexit 0\n"


def fixture():
    return create(
        IDENTITY,
        "sha256:" + "3" * 64,
        BASE,
        {
            "name": "check-layout",
            "description": "Verify the workspace layout.",
            "executable_sha256": executable_digest(EXECUTABLE.encode()),
        },
    )


class ToolCandidateTest(unittest.TestCase):
    def test_candidate_binds_identity_evidence_controls_and_executable(self):
        candidate = fixture()
        self.assertEqual(validate(candidate, EXECUTABLE.encode()), candidate)
        self.assertEqual(validate(candidate, EXECUTABLE.encode(), IDENTITY), candidate)

    def test_a_changed_retained_file_and_tampering_are_rejected(self):
        candidate = fixture()
        with self.assertRaisesRegex(ValueError, "retained file"):
            validate(candidate, b"#!/bin/sh\nexit 1\n")
        candidate["tool"]["description"] = "Something else."
        with self.assertRaisesRegex(ValueError, "digest"):
            validate(candidate, EXECUTABLE.encode())

    def test_definition_requires_a_self_consistent_digest_and_a_tool_identifier(self):
        definition = {
            "name": "check-layout",
            "description": "Verify the workspace layout.",
            "executable": EXECUTABLE,
            "executable_sha256": executable_digest(EXECUTABLE.encode()),
        }
        self.assertEqual(validate_definition(definition)["executable"], EXECUTABLE)
        with self.assertRaisesRegex(ValueError, "executable content"):
            validate_definition({**definition, "executable_sha256": "sha256:" + "0" * 64})
        with self.assertRaisesRegex(ValueError, "tool identifier"):
            validate_definition({**definition, "name": "Check Layout"})
        with self.assertRaisesRegex(ValueError, "nonempty"):
            validate_definition({**definition, "description": ""})


if __name__ == "__main__":
    unittest.main()
