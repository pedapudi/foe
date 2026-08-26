# Program lineage and transition evidence

Status: implemented. The configuration parser accepts `program_lineage`,
and the identity computation omits it. The `foe-lineage` crate derives state
identity, verifies canonical evidence bundles, and checks ancestry through
state and evidence resolvers. The crate's `build-bundle` binary completes a
bundle assembled outside the runtime. A separate trusted source-adoption
checker captures reconstructable source evidence. It completes lineage after
the rebuilt binary reports the program used for external evaluation.
"Harness adoptions" specifies how the self-improvement and Terminal-Bench
runners use these records.

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
- an accepted verifier result that the retained adoption record binds to
  the child program identity.

A chain of valid transitions is a program lineage. The first state in the
chain is its root. Each ancestry claim has a state identity derived from
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
      "state_identity": "sha256:…"
    },
    "evidence": "sha256:…",
    "verification_log": "children/ep_9c21/episode.jsonl",
    "verification_seq": 74
  }
}
```

`parent.program_identity` is the program identity of the immediate
predecessor. `parent.state_identity` selects that predecessor's own
ancestry claim among those that can accompany one program identity.
`evidence` is the content address of the proposal episode's evidence
bundle. `verification_log` is the relative path of the episode log that
contains the authoritative verifier result. `verification_seq` identifies
the result inside that log.

The identity computation omits `program_lineage`. The omission avoids a
self-reference and preserves the existing identity function. A configuration
without `program_lineage` is a root state.

Configuration resolution copies `program_lineage` into the program recorded
by `episode/start`. The identity document omits it. The log therefore records
the ancestry claim while program identity retains its existing input.

The state identity is a SHA-256 digest over this canonical object:

```json
{
  "schema_version": 1,
  "program_identity": "sha256:…",
  "program_lineage": null
}
```

For a descendant, `program_lineage` contains the four members shown above.
The state identity therefore names program content and one ancestry claim.
It is derived and does not appear inside the object it hashes.

A state document is the canonical identity document paired with the optional
`program_lineage` object. A state resolver returns this pair for a requested
state identity.

Two state documents can contain the same resolved program and different
`program_lineage` objects. They share a program identity and make different
ancestry claims. Their state identities differ. A verified series groups
state documents by the root state identity after validating each claim.

## Evidence bundle

The evidence bundle makes a transition verifiable after files move between
machines. Its canonical manifest lists every retained file by relative path,
byte length, and SHA-256 digest. The manifest identifies the proposal log
and the adoption record. The `evidence` value is the SHA-256 digest of the
canonical manifest.

Manifest paths use forward slashes, contain no empty, `.` or `..` component,
and appear in byte order without duplicates. A checker rejects a manifest
that violates any of these rules before opening a listed file.

The bundle contains these files:

- every log and referenced spill file in the proposal episode tree;
- the canonical identity document for the proposed child program;
- the adoption record;
- every artifact manifest that the verifier assessed.

Source evidence exists before the descendant program identity is known. It
therefore uses a separate content-addressed source-candidate manifest. The
manifest retains the parent identity document, proposal episode tree,
accepted verification coordinates, verifier executable, and every source
entry. It also lists every retained regular file by path, length, and
SHA-256 digest.

A source artifact manifest has this canonical form:

```json
{
  "schema_version": 1,
  "base_source_tree": "git-tree-sha1:…",
  "candidate_identity": "sha256:…",
  "entries": [
    {
      "status": "present",
      "path": "crates/code/src/read.rs",
      "base": {
        "object_type": "blob",
        "mode": "100644",
        "identity": "git-blob-sha1:…"
      },
      "applied": {
        "object_type": "blob",
        "mode": "100755",
        "identity": "git-blob-sha1:…"
      },
      "sha256": "sha256:…",
      "content": "candidate-files/crates/code/src/read.rs"
    },
    {
      "status": "deleted",
      "path": "obsolete.rs",
      "base": {
        "object_type": "blob",
        "mode": "100644",
        "identity": "git-blob-sha1:…"
      }
    }
  ],
  "parent_identity_document": "parent-identity.json",
  "parent_program_identity": "sha256:…",
  "proposal_log": "episode/episode.jsonl",
  "verification_log": "episode/children/ep_audit/episode.jsonl",
  "verification_seq": 19,
  "verification_tool": "check",
  "verification_executable": "candidate-check",
  "verification_executable_sha256": "sha256:…",
  "files": [
    {
      "path": "candidate-check",
      "bytes": 2401,
      "sha256": "sha256:…"
    },
    {
      "path": "candidate-files/crates/code/src/read.rs",
      "bytes": 1204,
      "sha256": "sha256:…"
    },
    {
      "path": "episode/children/ep_audit/episode.jsonl",
      "bytes": 3104,
      "sha256": "sha256:…"
    },
    {
      "path": "episode/episode.jsonl",
      "bytes": 4201,
      "sha256": "sha256:…"
    },
    {
      "path": "parent-identity.json",
      "bytes": 892,
      "sha256": "sha256:…"
    }
  ]
}
```

Each present entry records the base object when one exists. It also records
the applied Git blob, file mode, SHA-256 digest, and retained content path.
A deleted entry records the removed base object. The checker accepts regular
blobs with modes `100644` and `100755`. It rejects symbolic links, trees,
submodules, special files, and symbolic links anywhere inside the bundle.

The verification executable is the exact regular file that produced the
accepted verifier result. Its manifest digest must equal the result's
`verifier_identity`. The verification tool and coordinates bind those bytes
to one result in one reachable episode. A symbolic link cannot supply the
verification executable.

The candidate identity hashes the base tree and ordered source entries.
Content, deletion, and executable-mode changes therefore produce different
identities. The clean evaluation tree must change exactly the recorded paths.
Its Git objects, modes, and regular bytes must match every entry.

The manifest contains no absolute path. A store may place the bundle
anywhere, provided that a resolver can retrieve it by content address.

The adoption record has this canonical form:

```json
{
  "schema_version": 1,
  "program_identity": "sha256:…",
  "identity_document_sha256": "sha256:…",
  "artifact_manifest_sha256": "sha256:…",
  "verification_log": "children/ep_9c21/episode.jsonl",
  "verification_seq": 74
}
```

The bundle builder writes the record. `program_identity` is the hash of
the canonical child identity document; the two digest members name the
retained identity document and artifact manifest by content;
`verification_log` and `verification_seq` are the coordinates of the
accepted verifier result. The manifest names the record in
`adoption_record` as it names the proposal log. The child configuration is
absent from the bundle because it contains the bundle address. An artifact
manifest is a checker-defined list of candidate files and their content
digests. "Exact input binding" states what the record establishes.

The `foe-lineage` crate carries a `build-bundle` binary so the canonical
form has one implementation for builders outside the runtime. Given a
bundle directory whose files are already retained, the relative paths of
the proposal log, the child identity document, and the artifact manifest,
and the verification coordinates, it computes the record's identity
members from the retained files, writes the adoption record and the
canonical manifest through the crate's builder, and prints the bundle's
content address.

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
the verifier's input; the adoption record of the evidence bundle carries
the candidate's identity members instead.

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
2. **Exact child binding.** The adoption record names the program identity
   recomputed from the child state's canonical identity document.
3. **Exact input binding.** The candidate the verifier judged is the one
   the bundle retains. The adoption record carries the pairing of the
   candidate's identity members with the accepted verification's
   coordinates that a checker can test.
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
resolver retrieves a state record by state identity. The evidence resolver
retrieves an evidence bundle by content address. The checker performs these
steps:

1. Hash the canonical identity document to recompute the program identity.
2. Recompute the state identity from the program identity and
   `program_lineage`.
3. Accept a state without `program_lineage` as a root.
4. Resolve the evidence bundle and verify its canonical manifest and files.
5. Validate the proposal log under [log-format.md](log-format.md).
6. Resolve `verification_log` within the bundle and read its
   `verification/result` event at `verification_seq`.
7. Require an accepted result, and require the adoption record to name the
   claimed verification coordinates.
8. Require the adoption record's identity-document and artifact-manifest
   digests to equal the corresponding files in the bundle.
9. Require the adoption record's program identity to equal the hash of the
   canonical child identity document.
10. Require the proposal tree's root log program identity to equal
   `parent.program_identity`.
11. Require `verification_log` to be the root log or a descendant linked by
    valid spawn or workflow provenance.
12. Resolve `parent.state_identity` and require its program identity to
    equal `parent.program_identity`.
13. Require the verifier episode's program identity to be reachable in the
    parent state's program tree.
14. Require the recorded verifier identity to match the verifier declared by
    the verifier episode's program.
15. Repeat from the parent while rejecting a repeated state identity as a
    cycle.

The check establishes a complete chain from the state to a chosen root. It
does not establish that the verifier measured the right property. Evaluator
quality remains part of the adoption policy and the evaluation record.

The command line exposes the checker. `foe plan --config FILE --states DIR
--evidence DIR` resolves the configuration, pairs its identity document
with the `program_lineage` claim it carries, resolves states from
`DIR/<hex>.json` by state identity and bundles from `DIR/<hex>` by
content address, and prints, below the plan report, the chain of program
identities from this program through its parents to the root with every
check the retained evidence leaves open; with `--json`, the same chain and
open checks are the plan object's `lineage` member. The report names
program identities only; the derived state identity stays internal to
the checker. Without `--states` and `--evidence`, a configuration that
carries a claim is reported as carrying it, unverified.

## Exact input binding

The `verification/result` event carries no digest of the verifier's input.
The log format freezes an implemented type's data, so such a member cannot
be added to the existing event.

The adoption record closes the resulting gap. The checker requires an
accepted `verification/result` at the coordinates that the claim and record
name. Steps 8 and 9 bind the record to the child identity document, artifact
manifest, and recomputed child identity. The bundle builder writes the record
after the verifier invocation. Its pairing has the attestation strength of
the process that assembled the bundle.

Source adoption adds an external check before these steps. The trusted
source-adoption checker validates the source manifest and clean candidate
tree. It then places that exact manifest in the lineage bundle as the
artifact manifest. The adoption record binds the source manifest to the
actual child identity document supplied by the rebuilt binary.

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
Each claim has a distinct state identity and is verified through its own
evidence bundle.

A state document names one parent. Merging several parent histories into one
claim requires a separate design because one successful verifier result does
not establish compatibility with every parent.

## Interaction with the python tool

A [python](code-mode.md) call is ordinary behavior inside one episode.
Its source is a tool argument recorded in the episode log. It does not alter
the program state or request header.

Promoting a useful program into the configured tool surface changes what the
model sees. The promoted tool therefore belongs in a child program state and
can be admitted through a transition.

## Harness adoptions

The identity-bound self-improvement runner
(`evals/terminal_bench/run_self_improvement.py`) produces workflow and source
candidates. A workflow candidate has a complete program document at proposal
time. The runner records its transition before accepting it. An adoption
failure rejects the workflow candidate, sets `direct_implementation_required`,
and makes the runner exit unsuccessfully.

The parent state's identity document is the evaluated self-improvement
program's own. The runner resolves the program it launches through
`foe plan --json` and retains the emitted identity document, which
rehashes to the program identity the proposal episode's root log records.

An adoption's state document contains the program document that runs under
the adoption. A workflow candidate yields the development program document
with the selected audit setting applied to its preserved base configuration.
The trusted `build-bundle` and ancestry-checker binaries come from the
invoking build. Candidate source cannot replace either executable.

A source candidate has no descendant program document at proposal time. The
self-improvement runner retains its accepted source manifest and proposal
tree as artifact evidence. Source evidence capture failure rejects the
candidate and makes the runner exit unsuccessfully. Successful capture marks
lineage as pending external evaluation.

Instruction revisions and tool definitions can be retained as explicit
proposals. The Terminal-Bench runner has no application path for either
kind. Automatic selection therefore chooses only source changes, workflow
configurations, or insufficient evidence.

A development program document fixes the run-supplied members — task
instruction, credential path, working directory, and per-task allowances —
at the runner's declared values, so the document is stable across
launches.

The workflow runner retains the proposal episode tree, child identity
document, and artifact manifest. It completes the bundle through
`build-bundle`. The adoption record cites the accepted diagnosis validator.
Parent and child state documents use the layout expected by the checker.
The structured ancestry report must start with the adopted program identity.
The result records digests of both trusted lineage executables.

The Terminal-Bench evaluation runner accepts source evidence explicitly.
Before provider spend, the trusted source-adoption checker validates the
source manifest and every retained file. It compares the manifest with the
clean candidate tree and computes the source-tree and supplied-binary digest
pair. The pair records two independently supplied inputs and provides no
build attestation. Promotion requires a controller-built binary from the
accepted source tree.

The evaluation command runs from an immutable controller checkout. Its
runner and source checker are separate from the writable candidate checkout.
The command records both executable paths and content digests. Candidate
changes to lineage, log, program, Cargo, or Bazel files cannot alter the
controller's judgment.

When source evidence comes from a retained self-improvement result, preflight
requires its source-bundle, source-candidate, base-tree, and parent-program
identities to match the bundle. A raw source bundle carries no capture-checker
claim because the bundle cannot authenticate who created it. A confirmed
campaign records the checker it actually invokes, copies the validated bundle
into its run directory before provider spend, and uses only that copy during
adoption.

The adapter retains `foe plan --json` immediately before the first provider
request. For the built-in workflow it supplies the same task, model,
credential, verifier, service tier, and sandbox mode to the plan and run
forms. After the episode, the trusted checker recomputes the reported program
identity. It requires the root `episode/start` to carry the planned program
and identity. It also requires the runtime build digest to equal the evaluated
binary digest.

The checker then creates the child state from the actual plan identity
document. It places the exact source manifest in the lineage evidence bundle
and binds it through the adoption record. The canonical ancestry checker
must accept the resulting state. Adoption failure invalidates the assessed
trial and makes the campaign exit unsuccessfully.

`campaign.json` records the source-bundle, source-candidate, adoption,
evidence, program, state, and parent identities. It records the evaluation
checker digest and the evaluated source and binary pair. Each
task-specific program may have a distinct program identity. Transfer-task
identity remains variable.

Source preflight uses the canonical proposal checker before provider spend.
The checker requires the verification episode to descend from the retained
proposal root through recorded spawn events. It also requires the parent
identity document to reach the child program identity. The child must declare
the result's verifier tool. A configured child verifier is authorized only
when the source manifest retains its exact executable bytes and binds their
digest to that result. Every other open verifier-authorization check prevents
external evaluation.

The controller source checkout and its trusted build output are separate
roots. The evaluation runner must resolve under the source root. The source
checker must resolve under the build-output root. Both roots must remain
outside the candidate source tree. The campaign record includes the committed
controller source-tree identity, runner digest, checker digest, and the build
root associated with that checker.

## Required implementation tests

The implementation must cover these cases before the configuration key is
accepted:

- a valid root and one valid descendant;
- a bundle whose adoption record contradicts the claimed coordinates or
  the retained candidate files;
- source evidence whose bytes, mode, blob, deletion, or path contradicts its
  manifest;
- a source evidence path containing a symbolic link or unsupported Git entry;
- a malformed source manifest or malformed base Git tree;
- a missing or modified evidence file;
- a child identity that differs from the accepted candidate identity;
- a verifier absent from the parent;
- a verifier executable changed before invocation;
- an ancestry cycle;
- two children of one parent;
- one program identity accompanied by two valid ancestry claims;
- verification with the evidence directory removed from its original
  machine path;
- a candidate that passes artifact checks but whose lineage adoption fails;
- an actual plan or launched program that differs from the adopted state;
- a retained result whose recorded source identities differ from its bundle;
- an unrelated proposal or verifier episode rejected before provider spend;
- replacement of the external source bundle after the campaign freezes it;
- candidate changes to the controller's lineage and build sources;
- a configured verifier in the source-audit child bound to its retained
  executable;
- a verifier executable represented by a symbolic link or changed after
  source evidence capture;
- an unrelated retained verifier that attempts to close the child verifier's
  authorization check;
- the generated source-candidate target with its checker outside the
  controller source root and inside the declared build-output root;
- an automatic source candidate that changes a Cargo manifest or another
  protected build file;
- a validation command that rewrites `Cargo.lock` after the initial source
  scan;
- a raw source bundle that claims a capture-checker digest.
