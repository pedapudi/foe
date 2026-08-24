# Lineage identity

Status: design. Nothing in this document is implemented. The configuration
key `lineage` is reserved by this document. No event type is reserved.

## The ceiling in the current identity

The identity of a program is a SHA-256 over a canonical serialization of
everything that shapes the model's behavior: instruction sections, tool
specifications, the grant policy's kinds and counts, the runtime-contributed
strings, and the runtime's version and build hash
([design.md](design.md)). Two identities are equal when and only when the
model would see the same text. Identity names one state.

That definition caps growth in two ways.

- **No two states are related.** A program and an improved successor share
  no identifier. Every series keyed by identity — an evaluation curve, a
  telemetry group — ends at the first adopted improvement, and the runtime
  has no way to state the sentence its self-improvement mechanism exists to
  make true: this is the same program, improved.
- **Change happens only at launch.** A new state requires a new
  configuration constructed outside the runtime. The interval between
  states is unbounded and the transition itself is unrecorded: nothing
  connects the new configuration to the episode that motivated it or to
  the check that justified it.

The sandbox and the budgets are backstops: floors that hold regardless of
what the program becomes, sealed per episode, enforced by the kernel.
Identity is an account. An account of a changing program must be able to
name the change; the current account can only name unrelated states.

## Design

A program is a lineage of states.

- **State.** A resolved configuration. Its identity is `identity(program)`
  exactly as specified today: a pure content hash, unchanged by this
  design.
- **Lineage.** A configuration may carry a `lineage` object with three
  members. `genesis` is the identity of the lineage's first state.
  `parent` is the identity of the immediately preceding state.
  `transition` is the evidence for the step from parent to this state: the
  id of the episode that proposed the change and the name and content hash
  of the verifier that admitted it. A configuration without `lineage` is a
  genesis state, and its own identity is the `genesis` value of every
  descendant.
- **Exclusion from the hash.** `lineage` is excluded from identity's
  hashed material, alongside resolved paths and the task. Identity answers
  one question — would the model behave the same — and ancestry does not
  change the answer. Two behaviorally identical states reached by
  different histories share an identity and differ in lineage, which is
  the correct reading of both.
- **Series.** Anything that groups by identity today may group by
  `lineage.genesis` instead to see one program across its states. An
  evaluation reports a curve over a lineage; an adoption decision cites a
  transition.

## Transitions

A transition is a parent state, a change to it, and evidence. Five rules
govern it.

1. **States are immutable while in use.** A transition takes effect only
   in episodes launched from the child state. Every episode runs its whole
   life against one state, with the sandbox policy sealed at start and the
   budgets fixed. The backstops hold per state; nothing widens a running
   episode.
2. **Evidence is mandatory.** `transition` names the proposing episode,
   whose log records what motivated the change and what the episode was
   permitted to do, and the admitting verifier, whose recorded run is the
   justification for adoption.
3. **The admitting verifier precedes the proposal.** The verifier named in
   `transition` must exist in the parent state — as a `done_when.verify`
   tool, a `tool_defs` entry, or a workflow tool node. A verifier that
   first appears in the proposing episode cannot admit that episode's own
   proposal. Verifiers therefore grow by the same rule as everything else:
   each is admitted by verifiers already in the lineage.
4. **Authority changes ride transitions.** Wider grants, new executables,
   and new child programs appear only in a child state, and only episodes
   launched from that state receive them, each sealing its own kernel
   policy at start. Vocabulary that adds no authority — tool composition
   inside an episode ([code-mode.md](code-mode.md)) — is below this line
   and moves no state.
5. **Unattended transitions are legal.** The two-node self-improvement
   workflow ([self-improvement.md](self-improvement.md)) is a transition
   function: an evaluator node produces findings, a model node produces
   the change, `done_when.verify` admits it, and the next episode launches
   from the child state. No rule requires a person between states; the
   rules above are what stand in for one.

## Verifying a lineage

Two checks, both computable without running anything.

- **State check.** Recompute `identity` over the configuration; it must
  equal the recorded value. This is today's check, unchanged.
- **Ancestry check.** Walk `parent` links back to `genesis`. For each
  transition, resolve the named episode's log, confirm the log's recorded
  identity equals the parent state, and confirm the log records a
  successful run of the named verifier at the named content hash. A
  lineage claim that fails any step is unsupported by the account and is
  reported as such.

The lineage field is a claim in a document; the ancestry check binds the
claim to episode logs, which are the account everything else already
trusts.

## What is retired and what is preserved

Retired: the equation of improvement with a new unrelated program, and the
restriction of change to manually constructed launches.

Preserved: the identity function and its guarantee, byte for byte; the
reproducibility of every state; the frozen log format, because `lineage`
travels inside the configuration that `episode/start` already records in
its `program` member; and the backstops, which this design never touches —
each state's episodes run under a policy sealed at start, however fast the
lineage moves.

## Open questions

- **Evidence portability.** A transition names an episode id; resolving it
  to a log requires knowing where logs live. A content address for the
  proposing log would make lineages verifiable across machines and is the
  likely resolution.
- **Branching.** Two episodes may propose transitions from one parent.
  Both children are valid states sharing a parent; nothing in this design
  merges branches. Whether a lineage identifies one preferred head, and
  who chooses it, is unresolved.
- **Ancestry length.** A long-lived lineage accumulates transitions
  without bound. Whether a state may summarize ancestry behind a
  checkpoint, and what such a checkpoint must prove, is unresolved.
