# Evidence bundles

An evidence bundle is portable evidence for accepting a proposed execution
contract. It retains the proposal episode tree, the candidate contract
fingerprint document, an artifact manifest, and the accepted verifier result.
Verification reads only the bundle directory. It starts no process, exercises
no permission, opens no network connection, and writes no log.

The `foe-evidence` crate owns the bundle format and its standalone verifier.
The runtime configuration contains no field for adoption history. A system
that adopts a candidate stores the bundle externally and applies its own
adoption policy.

## Terms

An execution contract is the validated configuration Foe runs for one
episode: instructions, tools, declared permissions, budgets, completion
rules, model selection, child contracts, and workflow.
Rust names the resolved object `ResolvedContract`, and schemas use
`contract_*` fields.

During execution-contract construction, Foe captures each configured
executable's bytes, digest, source path, and invocation name. Every later
invocation uses the captured executable. Replacing, modifying, or deleting the
source cannot change the run.

A contract fingerprint is the SHA-256 digest of the canonical fingerprint
document that `foe plan --json` reports. The fingerprint includes stable
behavior, captured executable digests, child contract fingerprints, and
runtime-contributed model-visible text. The task, model route, sandbox mode,
and paths in the resolved permission set are excluded.

An adoption record associates one candidate contract fingerprint with one
accepted `verification/result` event. It may also name the contract
fingerprint that the candidate is intended to replace.

The verifier fingerprint comes from the named `verification/result` event.
Configured verifiers use the captured executable digest. Built-in and host
verifiers use the runtime build fingerprint. The adoption record does not
duplicate this value.

## Directory layout

A bundle contains these owned files and any caller-retained artifacts:

```text
manifest.json
adoption-record.json
fingerprint-document.json
artifact-manifest.json
candidate.json
episode/episode.jsonl
episode/children/<episode-id>/episode.jsonl
...
```

The file names for the fingerprint document, the artifact manifest, and the
retained candidate value are caller choices. The standard self-improvement
runner uses the names above. The adoption record selects the first two files
by their digests; the retained candidate value is selected by the
`candidate_sha256` digest the cited verification event attests, when that
event carries one.

Every path stored in the bundle uses forward slashes. A stored path is
relative and contains no empty, `.`, or `..` component.

## Canonical JSON

Owned JSON is UTF-8 with sorted object keys, no insignificant whitespace, and
no trailing newline. The manifest, adoption record, fingerprint document, and
a retained candidate value that a `candidate_sha256` attestation selects must
use this canonical form. Arrays retain their declared order.

The artifact manifest is caller-owned evidence. The bundle verifier checks
its retained bytes against the adoption record and outer manifest. The policy
that interprets its entries belongs to the adopting system.

## Manifest

`manifest.json` has schema version 1.

```json
{
  "schema_version": 1,
  "files": [
    {
      "path": "adoption-record.json",
      "bytes": 412,
      "sha256": "sha256:…"
    },
    {
      "path": "episode/episode.jsonl",
      "bytes": 9012,
      "sha256": "sha256:…"
    }
  ],
  "proposal_log": "episode/episode.jsonl",
  "adoption_record": "adoption-record.json"
}
```

`files` lists every retained regular file except `manifest.json`. Entries are
strictly ordered by path bytes and contain no duplicate path. `proposal_log`
and `adoption_record` must name listed files.

The bundle address is `sha256:` followed by the SHA-256 digest of the exact
canonical `manifest.json` bytes. Content-addressed storage uses the digest
without its prefix as the directory name.

## Adoption record

The adoption record has schema version 2.

```json
{
  "schema_version": 2,
  "contract_fingerprint": "sha256:…",
  "fingerprint_document_sha256": "sha256:…",
  "artifact_manifest_sha256": "sha256:…",
  "verification_log": "episode/children/ep_verify/episode.jsonl",
  "verification_seq": 42,
  "predecessor_contract_fingerprint": "sha256:…"
}
```

`contract_fingerprint` is the digest of the exact canonical fingerprint
document bytes. `fingerprint_document_sha256` is the same digest and selects
that retained file through the outer manifest. The separate names make the
semantic claim and file selection explicit.

`artifact_manifest_sha256` selects the retained artifact manifest.
`verification_log` and `verification_seq` select one event in the retained
proposal episode tree.

`predecessor_contract_fingerprint` is optional. When present, it must equal
the contract fingerprint recorded by the proposal tree root. A caller may
also supply an expected predecessor to standalone verification. Verification
then requires the record to contain that exact fingerprint.

Every digest uses `sha256:` followed by 64 lowercase hexadecimal digits.
Unknown record and manifest fields are errors.

## Verification result

Successful standalone verification returns these facts:

```json
{
  "bundle_address": "sha256:…",
  "contract_fingerprint": "sha256:…",
  "predecessor_contract_fingerprint": "sha256:…",
  "verifier_fingerprint": "sha256:…",
  "verification_tool": "check",
  "verification_log": "episode/episode.jsonl",
  "verification_seq": 17,
  "candidate_file": "candidate.json"
}
```

`predecessor_contract_fingerprint` is `null` when the record omits it.
Verification establishes that `contract_fingerprint` digests the retained
fingerprint document, that the verifier result at `verification_seq` is an
accepted event inside the retained proposal tree, and that the provenance
rule below holds.

The `verification/result` event may attest `candidate_sha256`, the digest of
the canonical JSON of the exact value the runtime handed to the verifier.
When the accepted event carries it, verification requires a retained file
with that digest whose content is canonical JSON, and `candidate_file` names
that file: the retained bytes are then established as what the verifier
judged. Whether that value corresponds to the candidate contract remains the
record author's claim. When the event lacks the field, `candidate_file` is
`null` and the whole association between the candidate and the verifier
result is the record author's claim: no retained byte ties the two together.

An external adoption policy selects the permitted verifier fingerprints,
may require an expected predecessor fingerprint, and decides whether to
accept what remains the record author's claim in the
candidate-to-verification association. Bundle verification establishes
facts and does not decide that policy.

## Proposal provenance

The verification event may live in the proposal root log or in a spawned
descendant. A descendant log is accepted only when every directory segment
has the form `children/<episode-id>`, every intermediate log is retained, and
the log above records `spawn/start` for that child. Each child's
`episode/start.parent_id` must name the episode above it.

This rule proves that the verifier result belongs to the retained proposal
tree. A copied event from an unrelated episode fails provenance checking.

## Standalone verification algorithm

The verifier performs these checks in order:

1. Parse `manifest.json` and require its canonical schema-version-1 form.
2. Read every listed file once; require its length and SHA-256 digest to
   match, and keep the verified bytes. Every later step parses these bytes,
   so a file rewritten mid-verification cannot reach the established facts.
3. Recompute the bundle address from the canonical manifest bytes.
4. Parse the selected adoption record and require its canonical
   schema-version-2 form.
5. Validate every digest and relative path in the record.
6. Apply an expected predecessor fingerprint when the caller supplied one.
7. Select the fingerprint document and artifact manifest by their recorded
   digests.
8. Require the fingerprint document to be canonical JSON.
9. Recompute the contract fingerprint from the fingerprint document bytes.
10. Parse and structurally validate every retained episode log.
11. Require the selected event to be an accepted `verification/result`.
12. Validate the event's verifier fingerprint form.
13. When the event attests `candidate_sha256`, require a retained file with
    that digest and canonical JSON content, and name it in the result.
14. Match an optional predecessor to the proposal root contract fingerprint.
15. Verify the selected log's spawn provenance from the proposal root.

Any failure names the record field, manifest field, file, event, or provenance
rule involved.

## Tools

The bundle builder completes a directory whose caller has already populated:

```text
build-evidence-bundle DIR PROPOSAL_LOG FINGERPRINT_DOCUMENT ARTIFACT_MANIFEST \
  VERIFICATION_LOG VERIFICATION_SEQ [PREDECESSOR_CONTRACT_FINGERPRINT]
```

It writes `adoption-record.json` and `manifest.json`, then prints the bundle
address.

The standalone verifier reads a completed bundle:

```text
verify-evidence-bundle DIR [EXPECTED_PREDECESSOR_CONTRACT_FINGERPRINT]
```

It prints the verified result as one JSON object.

## Self-improvement storage

The self-improvement runner writes completed bundles under
`evidence/bundles/<manifest-digest>`. Each bundle retains the complete
proposal episode tree, the resolved candidate fingerprint document, the
candidate artifact manifest, the value the cited verification judged as
`candidate.json` in canonical JSON, and candidate files selected by the
runner.

The runner invokes standalone verification after moving the bundle into its
content-addressed directory. Its policy permits the configured verifier
fingerprint recorded by the proposal contract. The result records the
verified facts and the bundle directory. The runner creates or modifies no
runtime configuration.
