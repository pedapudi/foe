# Program lineage and transition evidence

Status: implemented. The configuration parser accepts the
`program_lineage` key, the identity computation omits it, and the
`foe-lineage` crate derives the lineage identity, checks an evidence
bundle against its canonical manifest, and verifies an ancestry claim
through two resolvers. The authoritative `verification/result` event
carries no digest of the verifier's input, because the log format freezes
an implemented type's data; the candidate binding record retained in the
evidence bundle carries that digest instead. "The candidate binding gap"
states the resolution and its attestation strength. The crate's
`build-bundle` binary completes a bundle assembled outside the runtime, and
"Harness adoptions" defines the states the self-improvement runner records.

## Scope of the existing identity

A resolved program has a content hash called its program identity. The hash
covers instructions, tool specifications, grant kinds and counts, budgets,
completion and context policies, child programs, workflows, runtime text,
and the runtime build. [design.md](design.md#programs-and-identity) specifies
the complete input.

Program identity is a portable compatibility fingerprint. It omits the task,
model route, sandbox mode, and concrete grant paths. Two episodes with the
same program identity can therefore use different models, directories, and
sandbox modes. A runtime build change can also change identity when the
model-visible text remains the same.

This design keeps that identity function unchanged. It adds evidence that
relates one immutable program state to another. The relation records descent
and admission. An evaluation must establish whether the descendant improves
quality.

## Program states and transitions

A program state consists of a resolved program, its canonical identity
document, its existing program identity, and an optional lineage record. An
episode uses one state for its entire lifetime. Its `episode/start` event
records the resolved program and identity.

A transition relates one parent state to one child state. The transition has
three parts:

- the parent program identity;
- a content-addressed evidence bundle from the episode that proposed the
  child;
- an accepted verifier result bound to a candidate envelope that names the
  child program identity.

A chain of valid transitions is a program lineage. The first state in the
chain is its root. Each ancestry claim has a lineage identity derived from
the current program identity and its transition record.

Program lineage is distinct from episode lineage. Episode lineage connects a
running parent episode to the children that it spawned. Program lineage
connects immutable program states across separate launches.

## Configuration representation

A root configuration for a descendant state carries one optional object.
Nested entries under `programs` and workflow model nodes do not carry this
field because their identities already participate in the root program
identity.

```json
{
  "program_lineage": {
    "parent": {
      "program_identity": "sha256:…",
      "lineage_identity": "sha256:…"
    },
    "evidence": "sha256:…",
    "verification_log": "children/ep_9c21/episode.jsonl",
    "verification_seq": 74
  }
}
```

`parent.program_identity` is the program identity of the immediate
predecessor. `parent.lineage_identity` selects that predecessor's ancestry
claim. `evidence` is the content address of the proposal episode's evidence
bundle. `verification_log` is the relative path of the episode log that
contains the authoritative verifier result. `verification_seq` identifies
the result inside that log.

The identity computation omits `program_lineage`. The omission avoids a
self-reference and preserves the existing identity function. A configuration
without `program_lineage` is a root state.

Configuration resolution copies `program_lineage` into the program recorded
by `episode/start`. The identity document omits it. The log therefore records
the ancestry claim while program identity retains its existing input.

The lineage identity is a SHA-256 digest over this canonical object:

```json
{
  "schema_version": 1,
  "program_identity": "sha256:…",
  "program_lineage": null
}
```

For a descendant, `program_lineage` contains the four members shown above.
The lineage identity therefore names program content and one ancestry claim.
It is derived and does not appear inside the object it hashes.

A state document is the canonical identity document paired with the optional
`program_lineage` object. A state resolver returns this pair for a requested
lineage identity.

Two state documents can contain the same resolved program and different
`program_lineage` objects. They share a program identity and make different
ancestry claims. Their lineage identities differ. A verified series groups
state documents by the root lineage identity after validating each claim.

## Evidence bundle

The evidence bundle makes a transition verifiable after files move between
machines. Its canonical manifest lists every retained file by relative path,
byte length, and SHA-256 digest. The manifest identifies the proposal log,
the verifier input, and the candidate binding record when the bundle
retains one. The `evidence` value is the SHA-256 digest of the canonical
manifest.

Manifest paths use forward slashes, contain no empty, `.` or `..` component,
and appear in byte order without duplicates. A checker rejects a manifest
that violates any of these rules before opening a listed file.

The bundle contains these files:

- every log and referenced spill file in the proposal episode tree;
- the canonical identity document for the proposed child program;
- the complete verifier input, called the transition candidate envelope;
- the candidate binding record, when the builder wrote one;
- every artifact manifest that the verifier assessed.

The manifest contains no absolute path. A store may place the bundle
anywhere, provided that a resolver can retrieve it by content address.

The transition candidate envelope has this canonical form:

```json
{
  "program_identity": "sha256:…",
  "identity_document_sha256": "sha256:…",
  "artifact_manifest_sha256": "sha256:…"
}
```

The verifier accepts this envelope as its candidate. The child configuration
is absent from the bundle because it contains the bundle address. An artifact
manifest is a checker-defined list of candidate files and their content
digests.

The candidate binding record has this canonical form:

```json
{
  "schema_version": 1,
  "candidate_sha256": "sha256:…",
  "verification_log": "children/ep_9c21/episode.jsonl",
  "verification_seq": 74
}
```

The bundle builder writes the record beside the envelope. `candidate_sha256`
is the digest of the retained envelope's bytes; `verification_log` and
`verification_seq` are the coordinates of the accepted verifier result. The
manifest names the record in `candidate_binding` as it names the proposal
log and the envelope. "The candidate binding gap" states what the record
establishes.

The `foe-lineage` crate carries a `build-bundle` binary so the canonical
form has one implementation for builders outside the runtime. Given a
bundle directory whose files are already retained, the relative paths of
the proposal log and the candidate envelope, and the verification
coordinates, it writes the candidate binding record and the canonical
manifest through the crate's builder and prints the bundle's content
address.

## Authoritative verifier record

The existing completion verifier runs authoritatively after an ordinary
model call. The `verification/result` event records each authoritative
invocation with these fields:

```json
{
  "step": 8,
  "tool": "check-candidate",
  "verifier_identity": "sha256:…",
  "status": "accepted",
  "findings": [],
  "duration_ms": 1834
}
```

`status` is `accepted`, `findings`, or `failed`. A findings result carries
the finding strings in `findings`. A failed result carries an `error`
string. The other two statuses omit `error`. The event carries no digest of
the verifier's input; the candidate binding record of the evidence bundle
carries that digest.

`verifier_identity` binds the execution to the verifier that the parent
program declared. It hashes the complete tool specification and its
implementation identity. A configured executable uses its content hash at
invocation time. A built-in verifier uses the runtime build. The first
implementation excludes host tools because the current host-tool contract
does not identify their implementations.

The event does not enter the model's derived messages. Verifier findings
continue to reach the model through the existing `verify` inbox item.

## Transition rules

Seven rules govern a valid transition.

1. **One state per episode.** A transition affects only episodes launched
   from the child state. It never changes a running episode's program,
   sandbox policy, or budget.
2. **Exact child binding.** The transition candidate envelope names the
   program identity recomputed from the child state's canonical identity
   document.
3. **Exact input binding.** The candidate the verifier judged equals the
   transition candidate envelope retained in the bundle. The candidate
   binding record carries the pairing that a checker can test.
4. **Parent-owned admission.** The verifier is present in the parent
   program's reachable child-program and workflow tree. A verifier introduced
   by the candidate can admit only a later transition.
5. **Protected verifier implementation.** The verifier identity observed at
   invocation equals the identity recorded in the parent program. A proposing
   episode cannot replace its verifier and retain valid evidence.
6. **Separate authority decision.** Lineage records provenance and supplies
   no grant. The launcher validates the child configuration and applies its
   deployment policy before starting an episode.
7. **Evidence before adoption.** The proposal episode has ended, its evidence
   bundle is complete, and its transition verifier has accepted before the
   launcher constructs the lineage-bearing child state.

The deployment policy may permit unattended adoption. For example, it can
require the child's effective authority to remain within a predeclared
ceiling. A wider deployment requires authority that the launcher already
holds; the lineage record cannot create that authority.

## Verifying an ancestry claim

An ancestry checker receives a state document and two resolvers. The state
resolver retrieves a state record by lineage identity. The evidence resolver
retrieves an evidence bundle by content address. The checker performs these
steps:

1. Hash the canonical identity document to recompute the program identity.
2. Recompute the lineage identity from the program identity and
   `program_lineage`.
3. Accept a state without `program_lineage` as a root.
4. Resolve the evidence bundle and verify its canonical manifest and files.
5. Validate the proposal log under [log-format.md](log-format.md).
6. Resolve `verification_log` within the bundle and read its
   `verification/result` event at `verification_seq`.
7. Require an accepted result. When the manifest names a candidate binding
   record, require the record to pair the claimed verification coordinates
   with the digest of the retained envelope; without the record, report
   exact input binding as unverifiable.
8. Require the envelope's identity-document and artifact-manifest digests to
   equal the corresponding files in the bundle.
9. Require the envelope's program identity to equal the hash of the canonical
   child identity document.
10. Require the proposal tree's root log program identity to equal
   `parent.program_identity`.
11. Require `verification_log` to be the root log or a descendant linked by
    valid spawn or workflow provenance.
12. Resolve `parent.lineage_identity` and require its program identity to
    equal `parent.program_identity`.
13. Require the verifier episode's program identity to be reachable in the
    parent state's program tree.
14. Require the recorded verifier identity to match the verifier declared by
    the verifier episode's program.
15. Repeat from the parent while rejecting a repeated lineage identity as a
    cycle.

The check establishes a complete chain from the state to a chosen root. It
does not establish that the verifier measured the right property. Evaluator
quality remains part of the adoption policy and the evaluation record.

The command line exposes the checker. `foe plan --config FILE --states DIR
--evidence DIR` resolves the configuration, pairs its identity document
with the `program_lineage` claim it carries, resolves states from
`DIR/<hex>.json` by lineage identity and bundles from `DIR/<hex>` by
content address, and prints, below the plan report, the chain of program
identities from this program through its parents to the root with every
check the retained evidence leaves open; with `--json`, the same chain and
open checks are the plan object's `lineage` member. The report names
program identities only; the derived lineage identity stays internal to
the checker. Without `--states` and `--evidence`, a configuration that
carries a claim is reported as carrying it, unverified.

## The candidate binding gap

The `verification/result` event carries no digest of the verifier's input.
The log format freezes an implemented type's data, so such a member cannot
be added to the existing event.

The candidate binding record closes the resulting gap at step 7. The
checker requires an accepted `verification/result` at `verification_seq`,
and steps 8 and 9 bind the retained envelope to the retained identity
document, the retained artifact manifest, and the recomputed child
identity. When the bundle retains the record, the checker requires it to
name the claimed verification coordinates and the retained envelope's
digest, and exact input binding, transition rule 3, is verified. The
record is written by the bundle builder rather than by the runtime that
invoked the verifier, so it attests the pairing only as strongly as the
bundle that carries it. A bundle without the record keeps the graded
result: the checker reports exact input binding as unverifiable rather
than failing the claim.

One further check is weakened by the shape of the identity document rather
than by the event. Step 14 compares the recorded verifier identity with
the executable hash the parent program declares. The parent identity
document reduces a child program to its hash, so when the verifier episode
runs a child program with a configured verifier, the declared executable
hash is retained nowhere the checker can reach, and the comparison is
reported as unverifiable. A built-in verifier is checked in every
position, against the recorded runtime build.

## Branches and repeated states

One parent can have several valid children. The lineage data does not select
a preferred child. Selection belongs to an evaluator or deployment policy
whose evidence is retained separately.

The same program identity can occur with more than one ancestry claim. This
happens when independent transitions produce identical resolved programs.
Each claim has a distinct lineage identity and is verified through its own
evidence bundle.

A state document names one parent. Merging several parent histories into one
claim requires a separate design because one successful verifier result does
not establish compatibility with every parent.

## Interaction with code mode

A [code-mode](code-mode.md) call is ordinary behavior inside one episode.
Its source is a tool argument recorded in the episode log. It does not alter
the program state or request header.

Promoting a useful program into the configured tool surface changes what the
model sees. The promoted tool therefore belongs in a child program state and
can be admitted through a transition.

## Harness adoptions

The identity-bound self-improvement runner
(`evals/terminal_bench/run_self_improvement.py`) records every accepted
candidate as a transition between harness states. A harness state describes
the evaluated harness rather than one runtime-resolved program, and the
runner constructs its identity document from retained evidence. Nothing in
this section changes how a runtime-constructed state derives its identity
document; the two kinds of state share the lineage identity, the evidence
bundle, and the checker.

The parent state's identity document carries the evaluated Foe identities
(source-tree and runtime-binary digests), the preserved base configuration,
the retained self-improvement program document, and a `tools` list naming
the admission verifiers the state declares — the generated diagnosis
validator and the candidate check — each by executable content hash. The
declaration makes the checker's verifier-identity comparison apply to a
harness state as it does to a runtime state.

An adoption's state document is defined per candidate kind:

- an instruction revision's state document is the revised program document;
- a workflow candidate's state document is the development program document
  the runner constructs when applying it: the candidate's independent-audit
  setting applied to its preserved base configuration, with the
  run-supplied members — task instruction, credential path, working
  directory, and per-task allowances — fixed at the runner's declared
  values so the identity is stable across launches;
- a source or tool-definition candidate's state document is the candidate's
  canonical body: the candidate without the digest that seals it, so the
  state's program identity is a digest over exactly the sealed content. A
  source adoption's runtime build hash attaches when the rebuilt binary
  exists; until then the state binds the base source tree and every changed
  file digest.

The runner retains the bundle — the proposal episode tree, the state
document as the candidate identity document, an artifact manifest over the
retained candidate files, and the candidate envelope — and completes it
through the `build-bundle` binary. The binding record cites the accepted
diagnosis-validator result for a workflow, instruction, or tool candidate
and the accepted candidate-check result for a source candidate. The parent
and child state documents and the bundle land in the layout `foe lineage`
resolves.

A live proposal tree's root log records the runtime program identity of the
self-improvement program. The runtime does not export the identity document
behind that hash, so a harness parent state cannot reproduce it, and the
checker's proposal-descent comparison reports the mismatch when it walks
such a bundle. The runner's tests exercise the complete chain over
synthetic proposal trees whose root log records the harness parent
identity; a live adoption's bundle still authenticates every retained file,
the accepted verifier result, and the envelope bindings by content address.

## Required implementation tests

The implementation must cover these cases before the configuration key is
accepted:

- a valid root and one valid descendant;
- a bundle whose binding record contradicts the claimed coordinates or the
  retained envelope;
- a bundle without a binding record, whose claim verifies with exact input
  binding reported open;
- a missing or modified evidence file;
- a child identity that differs from the accepted candidate identity;
- a verifier absent from the parent;
- a verifier executable changed before invocation;
- an ancestry cycle;
- two children of one parent;
- one program identity accompanied by two valid ancestry claims;
- verification with the evidence directory removed from its original
  machine path.
