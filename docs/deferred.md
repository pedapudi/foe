# Deferred features

This document lists features that the design anticipates and version 2 does
not implement. Each section states what the feature is, that it is absent,
and which log event types, event field values, or configuration keys are
reserved for it. A reserved event type has a variant in `crates/log/src/lib.rs`
so that a version 2 reader parses a later log; nothing in version 2 emits it.
A feature with nothing reserved is listed so that a reader does not search
for it.

## Candidate slates

A candidate slate runs several forks from one prefix, so that the shared
prefix is materialized once and the branches are causally independent, and
then selects among their outcomes. Forking itself is implemented: the
running form's `--fork SOURCE_DIR --at SEQ` seeds a new episode from a
prefix of an existing log, under "Seeding" in
[log-format.md](log-format.md). A slate today is a caller-side loop over
that form: N launches from one source and boundary produce N independent
episodes, each paying its own copy of the prefix. First-class support
would add shared-prefix materialization paid once and selection among the
outcomes. It waits for evidence that either is needed: a measured
prefix-materialization cost that the per-launch copy makes significant,
or a consumer that needs foe to witness the selection among outcomes. No
event type or configuration key is reserved.

## Team task board

A team task board is a list of tasks shared by a lead and its members, held
in the lead's log, from which a member claims a task, reports progress, and
marks completion. The board would be folded from the lead's log in the same
way as the roster and the message queue. The task board is not implemented.
Reserved event type: `team/task`, carrying an unconstrained JSON object.

## Correlated request and response over the inbox

A correlated exchange lets one episode ask another a question and match the
answer to the question by identifier, so that a member can wait for a
specific reply rather than for any inbox item. The inbox would carry the
question with one source value and the answer with another, both holding the
same correlation identifier in `message_id`. Correlated exchanges are not
implemented. Reserved field values: `"request"` and `"response"` for `source`
in `inbox/item`.

## Cancellation of a running child

Cancellation would let a parent end a child episode it no longer needs —
the tool that would sit beside `spawn` and `wait` — and with it a
first-completion-wins composition: wait until any child completes, cancel
the rest, which is what select and race are. The teardown already ends
surviving children when the episode ends; what is absent is ending one
child mid-episode by the model's choice. Cancellation is not implemented.
No event type or configuration key is reserved. The evidence that would
justify building it is a consumer needing first-completion-wins — a
trajectory that waits on `any`, then pays for children whose results it
discards.

## Event-conditioned workflow edges

An event-conditioned edge would fire a workflow node on an inbox arrival —
a session exit, a child's report — rather than on a predecessor's value,
so that a mechanical reaction to an event costs no model turn. Workflows
condition firing on two things today: a branch label a node chose and the
`skip_when_verified` guard. Event-conditioned edges are not implemented.
No event type or configuration key is reserved. The evidence that would
justify building them is trajectories showing model turns spent on
mechanical reactions — a turn whose whole content is reading an arrival
and issuing the one call it always issues.

## Session output watermarks into the inbox

An output watermark would post a `session`-source inbox item when a
session's accumulated output crosses a threshold or matches a pattern, so
a model could wait on a server's readiness line instead of polling for it.
The inbox carries a session's exit and nothing else of its lifetime;
output reaches the model only through `poll`. Watermarks are not
implemented. No event type or configuration key is reserved; the `session`
source value carries exit items only. The evidence that would justify
building them is measured turns wasted on sleep-then-poll cycles against
a session that has not yet produced what the model is waiting for.

## Sandbox backends beyond Landlock

Landlock is the Linux kernel facility that foe compiles grants into. Other
backends would enforce the same allow list by other means: bubblewrap builds
a mount namespace containing only the granted paths, gVisor runs the
executable under a user-space kernel, and seccomp filters the system calls an
executable may make. Each would be selected by configuration and recorded in
`episode/start`. No backend other than Landlock is implemented. No event type
or configuration key is reserved; `sandbox.mode` is the only sandbox key, and
`episode/start.sandbox` records only the Landlock version obtained.

## MCP as a tool source

The Model Context Protocol is a wire protocol by which a separate server
offers tools, described by JSON Schema, to a client that calls them. foe
would connect to a configured server, register each offered tool with a
declared effect, and call it over the protocol. MCP is not implemented; the
three tool sources are built-in tools, `tool_defs` executables, and host
tools, as specified under "tools" in [config.md](config.md). No event type or
configuration key is reserved.

## A WASM tool tier

A WebAssembly tool tier would run a tool compiled to WebAssembly inside the
episode process, with the runtime's capability handles as the tool's only
imports, so that a tool needs neither a separate process nor a kernel
sandbox. The WASM tool tier is not implemented. No event type or
configuration key is reserved.

## Fuzzy matching in `edit`

Fuzzy matching would let the built-in `edit` tool apply a replacement whose
anchor text differs from the file in whitespace or indentation, reporting the
difference to the model. The `edit` tool requires an exact match. Fuzzy
matching is not implemented. No event type or configuration key is reserved.

## Tree-sitter symbols

Tree-sitter is a parser library that produces a syntax tree for many
languages. A symbol tool built on it would let the model list the
definitions in a file or find the definition of a name without reading whole
files. Tree-sitter symbols are not implemented. No event type or
configuration key is reserved.

## Default adoption of the python tool

The built-in `python` tool lets the model write a short program that calls
several tools and returns a combined value; inner results remain in the
log and never re-enter the conversation. [code-mode.md](code-mode.md)
specifies the implemented tool and the `tool/inner-call` event. What is
deferred is default adoption: the built-in coding workflow does not list
the tool until the task-quality, cost, and simpler-alternative evidence
that document names exists.

## Conditional workflow audit skipping

Conditional audit skipping would omit a review node after an earlier verifier
accepted the implementation. Foe runs every declared review node. No event
type or configuration key is reserved. A cost-sensitive deployment may
propose this behavior after Foe passes its quality gates and paired held-out
evaluation shows that skipping the review preserves task quality.

## Program lineage

Program lineage relates immutable program states through content-addressed
proposal evidence and a verifier declared by the parent state.
[lineage-identity.md](lineage-identity.md) specifies it. The configuration
key `program_lineage`, the state identity, the evidence-bundle checker,
and the ancestry checker are implemented: the key's shape in `foe-program`,
the rest in `foe-lineage`. The implemented `verification/result` event
carries no digest of the verifier's input; the bundle's adoption record
closes that gap with the attestation strength lineage-identity.md "Exact
input binding" states.

## Bazel targets for the browser bundle and the Python package

Bazel is the primary build interface: every Rust crate is a Bazel target,
`//:foe` builds the binary, and [build.md](build.md) specifies the targets.
The browser bundle and the Python package are not Bazel targets. The
bundle is checked into `view/dist` and consumed as a filegroup, and the
Python package builds through its own tooling. A reproducibility check
comparing the Bazel and Cargo binaries is also absent. No event type or
configuration key is reserved.

## A TypeScript SDK

A TypeScript SDK would do for a Node.js program what the Python package does:
build a configuration, launch the binary, and serve the host protocol with a
transport the program supplies. The TypeScript SDK is not implemented. No
event type or configuration key is reserved.

## Additional viewer axes

The viewer organizes episodes by lineage: one tree of parents and children
per root episode, with the conversation, the raw events, the diffs, the
declared workflow, and the statistics of the selected episode beside it.
Two further axes
would organize the same logs differently. One is program identity, so that
every episode of one program is compared side by side. The other is the
individual tool call, so that every call of one tool across episodes is
listed together; the statistics view already totals durations by tool name,
and listing the calls themselves is what is absent. Neither axis is
implemented. No event type or configuration key is reserved.

## macOS sandboxing

On macOS the kernel facility comparable to Landlock is the sandbox profile
language used by the `sandbox-exec` mechanism. A macOS backend would compile
grants into such a profile. macOS sandboxing is not implemented; on macOS
`sandbox.mode: "best-effort"` applies no restriction and records
`landlock_abi: 0`, and `sandbox.mode: "required"` refuses to start. No event
type or configuration key is reserved.

## Anonymous executable images

Linux `O_TMPFILE` can hold construction-committed executable bytes in an
unnamed regular inode. The inode can be granted through Landlock and invoked
through `/proc/self/fd`, unlike a memfd, which Landlock rejects as a rule
target. Tests with a shell script and a copied dynamically linked shell
succeeded under Landlock ABI 7. A copied coreutils executable rejected the
anonymous form because it compares its requested utility name with
`/proc/self/exe`, whose target has no configured basename. BusyBox and other
self-path-dependent executables have the same class of requirement. Foe
therefore uses a named private image that preserves the configured basename.
Anonymous images remain deferred until they can preserve self-path semantics
for common executables. No event type or configuration key is reserved.

## Unicode normalization in the scrubber

The scrubber removes invisible characters (zero-width spaces and joiners,
the byte-order mark, the soft hyphen, the directional marks) before any
other layer runs, so a value split by them is matched whole. It does not
apply compatibility normalization (NFKC), which would additionally fold
full-width and other confusable code points onto their ASCII forms. The
two scrubbed fields are written by tools rather than by a model and are
ASCII in every recorded trajectory, so the folding has no input to act on
today, and a correct implementation means taking on a Unicode data table
dependency. NFKC folding is deferred until either field can carry
model-authored text. No event type or configuration key is reserved.

## Checksum validation for provider tokens

Payment-card-shaped digit runs are masked only when they pass the Luhn
checksum, which keeps version strings and issue numbers out of the mask.
GitHub tokens carry a comparable embedded checksum (a CRC32 over the token
body, base62-encoded in the last six characters), which would let the
detector drop its length heuristic. Validating it requires reference
vectors verified against real token structure, which cannot be taken from
documentation alone; until then GitHub tokens are matched by prefix and
length like every other provider prefix. No event type or configuration
key is reserved.
