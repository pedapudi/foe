#!/usr/bin/python3

import unittest

from instruction_candidate import create, resolve_section, validate


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
DOCUMENTS = {
    "program.json": {
        "instructions": {"role": "Run the workflow."},
        "programs": {"kid": {"instructions": {"scope": "Stay inside the digest."}}},
        "workflow": {
            "nodes": {
                "diagnose": {"model": {"instructions": {"sufficiency": "Prefer bounded evidence."}}}
            }
        },
    }
}


def fixture():
    return create(
        IDENTITY,
        "sha256:" + "3" * 64,
        BASE,
        {
            "document": "program.json",
            "section": "scope",
            "old_text": "inside the digest",
            "new_text": "inside the labeled digest",
        },
        DOCUMENTS,
    )


class InstructionCandidateTest(unittest.TestCase):
    def test_candidate_binds_identity_evidence_controls_and_revision(self):
        candidate = fixture()
        self.assertEqual(validate(candidate, DOCUMENTS), candidate)
        self.assertEqual(validate(candidate, DOCUMENTS, IDENTITY), candidate)

    def test_tampering_is_rejected(self):
        candidate = fixture()
        candidate["revision"]["new_text"] = "something else"
        with self.assertRaisesRegex(ValueError, "digest"):
            validate(candidate, DOCUMENTS)

    def test_section_resolution_requires_one_match_and_one_occurrence(self):
        with self.assertRaisesRegex(ValueError, "exactly one instruction section"):
            resolve_section(DOCUMENTS["program.json"], "absent")
        ambiguous = {
            "instructions": {"scope": "one"},
            "programs": {"kid": {"instructions": {"scope": "two"}}},
        }
        with self.assertRaisesRegex(ValueError, "exactly one instruction section"):
            resolve_section(ambiguous, "scope")
        with self.assertRaisesRegex(ValueError, "exactly once"):
            create(
                IDENTITY,
                "sha256:" + "3" * 64,
                BASE,
                {
                    "document": "program.json",
                    "section": "scope",
                    "old_text": "i",
                    "new_text": "y",
                },
                DOCUMENTS,
            )

    def test_an_unknown_document_and_an_identical_revision_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "document must be one of"):
            create(
                IDENTITY,
                "sha256:" + "3" * 64,
                BASE,
                {
                    "document": "other.json",
                    "section": "scope",
                    "old_text": "inside the digest",
                    "new_text": "inside the labeled digest",
                },
                DOCUMENTS,
            )
        with self.assertRaisesRegex(ValueError, "must differ"):
            create(
                IDENTITY,
                "sha256:" + "3" * 64,
                BASE,
                {
                    "document": "program.json",
                    "section": "scope",
                    "old_text": "inside the digest",
                    "new_text": "inside the digest",
                },
                DOCUMENTS,
            )


if __name__ == "__main__":
    unittest.main()
