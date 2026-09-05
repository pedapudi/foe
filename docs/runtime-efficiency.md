# Shared runtime mechanics reduce repeated work

Status: proposed

This document proposes an internal architecture that reduces repeated runtime
work. Any implementation must preserve the behavior specified by
[design.md](design.md) and its companion specifications. A behavior change
requires an update to the relevant specification in the same commit.

The proposal covers event consumption, waiting, model endpoints, process
supervision, workflow and team scheduling, episode construction, viewer state
derived from events, and repeated tool observations. It requires no change to
execution-contract keys, log events, host-protocol messages, or completion
rules.

## Problem statement: repeated runtime work grows with an episode

Several runtime paths repeat work as an episode grows.

- Team operations clone the event list and recompute the complete team state
  from that copy.
- Some runtime waits check state on fixed intervals.
- Each direct model request opens a separate network connection and performs
  another secure-connection handshake.
- One-shot commands and process sessions implement overlapping launch,
  output, deadline, cancellation, and process-group behavior.
- Workflow and team schedulers make related capacity decisions through
  separate mechanisms.
- Planning, fingerprint reporting, and runtime construction assemble related
  episode resources separately.
- Browser analyses build separate views from the same event array.
- Repeated successful file observations can send unchanged content through
  later model requests.

Repeated scans, timer checks, connection setup, and resending unchanged tool
results consume more resources as logs, conversations, and child counts grow.
Fixed-interval checks also make progress depend on periodic observation rather
than a recorded state change.

The proposal must reduce repeated work while preserving the append-only log,
deterministic coordination, explicit authority, and repository line budgets.
A shared mechanism should reduce both repeated work and implementation
complexity. Line count alone does not justify a new abstraction.

## Motivation: autonomous runs require bounded internal work

An autonomous runtime cannot depend on an operator to recover from an internal
deadlock or notice accumulating resource use. Runtime waits and resource use
must remain bounded and observable throughout each episode.

The log already records a durable order for model turns, tool results,
workflow-node events, team tasks, messages, and outcomes. The proposal lets
each runtime consumer apply every appended event once to its derived state.
Recomputing state from the full log and checking timers repeatedly consumes
processor time without recording new evidence.

Connection setup adds latency to every direct model request. Resending
unchanged tool results increases the model input carried by later requests.
Both costs accumulate across child episodes.

foe obtains model responses in two ways. An episode can call a configured
endpoint directly, or its host process can answer the request. Direct
execution supports OAuth-backed response endpoints, managed-cloud endpoints,
and compatible HTTP endpoints. A host can provide the same endpoint classes
through its own backend.

The Python host package lets downstream applications embed foe. A downstream
application named `zicato` uses the package for a proposer with host tools,
cancellation, logs, completion verification, and direct model configuration.
Regression tests must cover its managed-cloud endpoint. The package supports
additional consumers under the same embedding contract.

## Design principles preserve evidence and explicit boundaries

**Recovery uses the durable log.** The runtime derives its in-memory state
from the log and may discard that state. A restarted process reconstructs the
state from the log once.

**Each consumer updates its derived state once per appended event.** This
derived state is its projection. A state query reads the projection without
folding the full log again.

**A recorded change wakes waiting consumers.** The runtime publishes a change
notification when recorded state changes. A consumer then inspects that
state. Timer checks remain for declared deadlines, request backoff, and
external resources without change notifications.

**Deterministic functions select an action before the runtime performs it.**
These functions determine readiness, ordering, and capacity. The runtime
launches a process, appends an event, or performs network I/O only after
selecting one action.

**Workflow and team scheduling share only common coordination mechanisms.** A
workflow runs a declared dependency graph. A team board assigns dynamically
created work to team members. Both can share capacity and wake-up mechanisms
while retaining distinct schemas, events, and state types.

**Authority remains explicit.** Connection reuse, process reuse, and cached
observations cannot widen filesystem, executable, network, credential, or
child-contract grants.

**External contracts remain stable.** Optimizations preserve event order,
model-visible tool order, budget charges, cancellation, retry behavior,
completion, replay, and host-protocol behavior.

**Measurements justify implementation complexity.** The runtime adopts an
optimization only when a repeatable workload shows a reduction that exceeds
measurement variance in latency, processor work, memory, log bytes, or
model-visible bytes. Maintainers remove the optimization when its measured
benefit disappears or its compatibility cost exceeds that benefit.

**A crate represents an ownership boundary.** Keep shared code in an internal
module until independent consumers require a dependency boundary and
repository line budgets permit a separate crate.

## Compatibility requirements preserve observable behavior

| Area | Required behavior |
|---|---|
| Episode log | One append-only log remains sufficient for replay, resume, budget accounting, and audit. |
| Single-member team | Every episode remains a team of one without extra events, model calls, child processes, or task-board polling. |
| Team coordination | The lead remains the single writer that assigns each board task to at most one child in deterministic order. |
| Workflows | Declared graph semantics, branch freshness, recovery, and node events remain specific to workflows. |
| Model endpoints | Direct execution continues to support OAuth-backed, managed-cloud, and compatible HTTP endpoints. |
| Hosted execution | A host can supply any supported backend while the episode holds no credential and requires no network access. |
| Tools | Structured tools, executable tools, host tools, and bounded tool composition keep their present canonical and rendered results. |
| Processes | Sandbox narrowing, captured executables, process-group cleanup, output evidence, and task-lifetime authorization remain enforced. |
| Viewer | Live and static rendering produce the same projection for the same ordered events. |
| Embedding | The Python host package retains host tools, cancellation, logs, direct configuration, and completion behavior. |

## Design advances live state from recorded events

The proposal keeps the log as the durable record. It adds a live event feed
that publishes the sequence number of each recorded event after append
completes. Runtime consumers maintain small projections and apply only events
they have not processed.

```
                                      ┌─ core loop and inbox
event ──► validate ──► append ──► feed├─ team board and messages
                         │            ├─ workflow capacity wait
                         │            └─ viewer stream
                         ▼
                    episode.jsonl

configured endpoint ──► reusable client ──► response decoder created per request

process request ──► common supervisor ──┬─ one-shot command
                                       └─ process session
```

The proposed feed uses each notification to report that state may have
changed. After waking, consumers check the newest sequence number and derived
state. The receiver retains the newest unseen sequence, so consumers process
every recorded transition even when notifications coalesce.

### A live event feed advances runtime projections

The proposal adds a change channel to the in-process log owner. The channel
publishes the newest recorded event sequence. The owner continues to hold the
writer and the in-memory event vector.

Its internal interface provides these operations:

- append an event under the existing single-writer lock;
- borrow all events for request derivation and callers that require complete
  history;
- borrow events starting at a caller-provided sequence for incremental
  consumers;
- subscribe to changes in the newest recorded sequence.

In the proposed feed, an append validates and serializes the typed event once.
The writer then follows the existing ordering rule: write and flush the log
file, then write and flush any mirror. The log owner stores the event in
memory and publishes its sequence number after all required writes succeed.
If the mirror fails after the file write, the file can contain an event that
memory lacks. The episode fails, and a restarted process reconstructs its
projection from the file.

The event feed combines the existing log owner with live synchronization, so
the proposal places it in `crates/core`. The log-format crate, `crates/log`,
retains typed events, deterministic validation and folds, seeding, and file
I/O. This dependency arrangement keeps replay independent of an asynchronous
runtime.

Under this proposal, each event consumer owns a function that updates its
derived state from one event. This function is its reducer. The reducer stores
its state and the next expected sequence. It applies each unseen event once
and advances that sequence. Replay from a complete log uses the same function.

The proposal splits the team crate's existing full-log fold into
initialization and single-event application. Each `Team` caches the resulting
board, roster, message queue, delivery set, and next expected sequence. Every
team inspection or scheduling operation advances this cache from unseen
events while holding the lock that serializes team operations.

The proposal retains borrowed access to ordered events for model-message
derivation and loop detection. An index for either operation requires its own
measurement because maintaining that index adds append work and memory to
every episode.

### Change notifications replace runtime polling

Under the proposal, each event append publishes a log revision. The shared
budget pool publishes a revision after every reservation, charge, or release.
The process supervisor publishes process-group liveness changes. Each source
uses a monotonic revision so a receiver detects progress after notifications
coalesce.

The proposed `wait` implementation subscribes to relevant revision receivers
before checking its predicate. This order ensures that a revision occurring
between the check and the sleep remains observable. It also selects over the
episode stop signal, the budget deadline, and any `timeout_seconds` deadline.

A bare wait checks whether every added team task has settled and no child
reservation remains. A conditional wait inspects unconsumed inbox arrivals,
including matching child outcomes and process-session exits. The tool repeats
the applicable check after every revision.

Team scheduling wakes when a child settles, a dependency changes, or capacity
returns. Workflow scheduling wakes when a running node completes or shared
capacity returns. Neither scheduler uses a timer to discover an internal
state change.

Tests of elapsed-time rules use the async runtime's virtual clock. Readiness
tests assert that no scheduler wake-up occurs while every relevant revision
remains unchanged.

### A reusable endpoint client owns connection lifetime

`crates/transport` retains three separate concerns:

- a wire codec builds one request and decodes one response stream;
- a credential source produces request authorization and refreshes it;
- an endpoint client owns connection establishment, pooling, cancellation,
  and response framing.

Under the proposal, a process constructs one endpoint client for each
configured direct backend and shares that client across requests to the same
backend. The client handle remains process-local. Connections are keyed by
scheme, authority, and security configuration. Each request creates its own
response decoder because decoder state belongs to one response stream.

The client reuses secure connections when the peer permits reuse. It carries
response bytes through a bounded asynchronous stream to the response decoder.
This path starts no dedicated operating-system thread for one request and uses
no unbounded queue between the socket and the model-call sink. The client
discards a stale pooled connection. The existing request-retry policy alone
determines whether the request may run again.

Transport configuration remains closed and explicit. The client reads no
environment variable, discovers no proxy, follows no redirect, and loads no
ambient credential or certificate store. Cancellation stops response-stream
processing. The protocol implementation returns the underlying connection to
the pool only when it confirms safe reuse. Otherwise, it closes the
connection.

Each credential source remains the sole owner of refresh and storage rules.
The endpoint client requests authorization for each attempt and stores no
credential state. An OAuth credential source writes a refreshed token to its
configured file and retains it in memory. A managed-cloud credential source
retains its minted access token only in memory. Secrets do not enter log
events or connection-pool keys.

The provider table continues to select a codec, credential source, endpoint,
and verification behavior. Compatible HTTP endpoints remain table-driven.
Managed-cloud routing remains an endpoint and credential concern rather than
a separate transport abstraction.

A long-lived host owns its own backend instances and may reuse connections
across episodes. The host protocol continues to carry request and stream
events without knowing how that host reached its endpoint.

### One process supervisor enforces launch and lifetime rules

`crates/core` gains one internal process supervisor for one-shot commands and
sessions. Tool composition already executes through the ordinary executor and
therefore benefits without another process path.

The shared launch description contains the captured executable, arguments,
working directory, cleared environment, standard-input source, passed file
descriptors, sandbox policy, network permission, and process-boundary
placement. It contains no unresolved executable name or path search.

The supervisor performs these operations in one place:

- apply the narrowed sandbox before user code starts;
- create and retain the process group while the runtime owns it;
- pump standard output and standard error into bounded memory or evidence
  files;
- publish process-group liveness changes;
- respond to cancellation and deadline expiration;
- terminate the process group with the specified grace period when its
  lifetime rule requires termination;
- reap the group leader before reporting final exit when the runtime retains
  ownership.

A one-shot command awaits the final status and returns captured output. A
session retains the supervised handle and exposes bounded output reads and
input writes. At episode settlement, the session adapter stops an
episode-lifetime process and releases an authorized task-lifetime process to
the invocation-owned task environment. The adapters retain their distinct
result schemas and tool behavior.

On a platform without asynchronous child-exit notification, one blocking
waiter may observe the process-group leader. Session liveness still follows
the entire process group, including members that outlive the leader. If the
platform provides no process-group liveness notification, the supervisor
alone performs a periodic liveness check. Other runtime consumers await the
supervisor's change receiver.

### Scheduling shares capacity mechanics while domains keep their semantics

Workflow readiness remains in `crates/workflow`. It depends on graph edges,
branch labels, fresh inputs, recovery, and firing limits. Team readiness
remains in `crates/team`. It depends on board order, dependency outcomes, and
task status.

Both schedulers use a small internal capacity interface from `crates/core`.
The interface provides nonblocking attempts to reserve child capacity or
obtain exclusive permission for an effect that must run alone. It also
provides a receiver for the next capacity change. Of these schedulers, only
the workflow scheduler requests exclusive-effect permission. That permission
spans nested workflows within one episode.

Each domain computes ready work in its specified deterministic order. Each
scheduler preserves its domain's required event order around capacity
acquisition. A workflow records `workflow/node-start` before `budget/reserve`
and `spawn/start`. A capacity refusal leaves the item ready and awaits the
capacity receiver. Completion releases capacity and publishes a revision.

The team scheduler holds the lead's operation lock through the launch
decision and recorded task transition. This assigns a task to at most one
child. The proposal can shorten the locked section only after measurement
shows that its duration limits throughput and a durable intermediate state
preserves the single-writer guarantee.

No public task type spans workflows and teams. No task-board model enters the
workflow crate. This limited reuse removes polling and duplicated capacity
handling without weakening either domain.

### Episode construction reuses one resolved execution context

Before execution, the existing resolver builds an immutable execution
contract and captures its reachable executables. The proposal retains that
result once in each root command path and passes references to planning,
fingerprint reporting, and runtime construction.

At the command-line composition boundary, the proposal adds an internal
factory that constructs episodes from shared resources. The factory owns the
resolved contract tree, captured executables, build identity, endpoint client,
root budget pool, and stop source. A caller supplies only episode-specific
state: identity, task, log directory, parent metadata, reserved allowance, and
an optional seeded prefix.

The factory constructs the registry, capability handles, context policy,
child router, process sessions, and loop parameters through one path. Direct
execution supplies a shared endpoint client. Hosted execution supplies the
protocol transport. Planning reads the immutable inputs and performs no
credential read, process start, network access, or log append.

Each child process continues to validate its launch metadata and rebuild
capability handles from that metadata. The factory centralizes construction
only within one process, so it cannot transfer a parent's authority across a
process boundary.

The factory begins as an internal command-line module. A separate crate is
justified only when another binary requires the same composition boundary
without depending on command-line behavior.

### One browser projection serves live and static views

The server continues to deliver ordered events and episode-tree metadata.
Under the proposal, the browser normalizes that payload into one indexed
episode-state structure. This structure is the episode projection. The
projection indexes episode lifecycle events, model requests, rendered
conversation messages, tool call and result pairs, workflow firings, team
task and roster revisions, budget changes, and causal links. Individual views
derive layout and presentation from these indexes. No individual view parses
the event array.

For an episode that the command-line process launched, that process bridges
the live event receiver to the viewer server. A standalone viewer serves a
directory written by another process, so it may retain bounded file checks.
The viewer crate gains no dependency on the runtime kernel.

Static viewing applies the complete event list through the projection reducer.
Live viewing initializes from the same payload and applies each streamed event
through the same reducer. If a streamed event does not follow that episode's
projection cursor, the browser requests the missing suffix after the cursor
and applies no later event until the gap closes.

Projection tests feed one fixture to the reducer as a complete list and under
every possible partition into incremental batches. Every partition must
produce the same normalized episode projection. Rendering tests use that
projection for the conversation, workflow, team, statistics, trajectory, and
causal views.

### Proposed compact renderings must preserve reconstructable tool evidence

The structured coding tools remain available. A bounded composition tool
combines several tool calls and returns one derived value while omitting
intermediate renderings from model context. The implemented tool is named
`compose_tools`. Evaluation determines whether the built-in coding workflow
offers it by default.

An implemented file read records its resolved path, selected range, complete
selected content, and SHA-256 file version in its canonical result. Under
this proposal, a later read with the same path, range, and version may cite
the sequence of the earlier `tool/result` event rather than repeat the content
in its rendered result. The later canonical result continues to record the
complete observation.

A compact rendering may cite an earlier observation while the model-visible
request still contains that observation. If compaction removes the earlier
content, request derivation replaces the compact citation with the complete
observation from the later canonical result. Every request therefore contains
the content needed to interpret each observation.

Before adopting compact renderings, run the same evaluation tasks with compact
rendering enabled and disabled under otherwise identical conditions. The
comparison measures model-visible bytes and task quality across repeated
inspection, external file mutation, shell-written files, compaction, resume,
and fork.

Request snapshots and streamed response events remain complete. A generalized
content-addressed event store and response-chunk coalescing remain outside
this proposal. Either feature requires separate measurements showing that log
size or replay time exceeds a declared operational limit.

## Proposed changes remain within existing code ownership and size budgets

| Owner | Responsibility in this design | Constraint |
|---|---|---|
| `crates/log` | Typed events, validation, pure folds, seeding, and file I/O | Gains no async runtime dependency. |
| `crates/core` | Live event revisions, budget revisions, process supervision, registry dispatch, settlement, and child capacity | Shared mechanics replace polling and duplicate process code within the kernel budget. |
| `crates/transport` | Endpoint clients, wire codecs, credentials, and endpoint selection | Connection reuse replaces one-request connections within the transport budget. |
| `crates/team` | Board semantics, roster, peer messages, deterministic assignment, and the incremental team reducer | Team concepts remain isolated within the team budget. |
| `crates/workflow` | Graph readiness, firing, recovery, and workflow events | Workflow concepts remain isolated within the workflow budget. |
| `crates/cli` | Pure planning and root episode construction | Shared construction replaces repeated command-path wiring within the command-line budget. |
| `crates/view` and `view/` | Event delivery, normalized browser projection, and rendering | The server and compressed browser bundle retain their separate budgets. |
| `python/foe` | Host process, host tools, cancellation, and host-owned backends | Wire compatibility and downstream integration tests govern changes. |

This proposal requires no new crate. Each implementation change reports the
number of production lines added or removed and identifies the repeated
behavior it replaces. A change that increases production code without
deleting repeated behavior must meet a recorded benchmark threshold that
justifies the increase.

## Proposed failure handling preserves recovery guarantees

An event notification may be coalesced or observed late. Each runtime consumer
reads the recorded sequence and applies every unseen event. Notification
timing therefore does not change the final state.

The episode process may stop after appending an event to the log and before
advancing an in-memory projection. On resume, the runtime reads the durable
log and rebuilds each projection once.

A pooled connection can close between requests. The endpoint client discards
it. Existing retry classification, attempt limits, budget checks, and retry
events govern another attempt.

A credential refresh can fail while other requests await it. Every waiter
receives the same failure result. A later request may retry according to the
credential source's existing rules.

A supervised process may exit as cancellation arrives. Reaping the child and
publishing its final status are idempotent. The tool or session adapter
records one final result, as the existing obligation rules require.

A live viewer can miss an event batch. A sequence gap causes resynchronization
from the last accepted sequence. Static replay remains the reference result.

## Reproducible benchmarks gate each proposed change

Implementation begins with reproducible baselines. Each benchmark uses
deterministic local endpoints and tools and waits on no external service.
Before runtime code changes, its record fixes the command, input, environment
identity, warm-up policy, repetition count, raw observations, summary
statistic, observed variance, and numerical thresholds for required
improvements and permitted regressions. Every claimed improvement must exceed
the observed run-to-run variance.

| Workload | Measurements | Acceptance condition |
|---|---|---|
| Fifty direct model turns over one local secure endpoint | connection count, handshake count, request latency, processor time | Connection count, handshake count, and request latency meet their recorded reduction thresholds. Processor time meets its recorded regression threshold. Request and response event types, ordering, and model-visible content remain unchanged. |
| A team with one, eight, and thirty-two ready tasks under a fixed child-capacity limit | assignment order, child count, makespan, wake-ups, processor time | Each ready task is assigned once to one owner. Repeated runs produce the same assignment order. Child count never exceeds the configured limit, and timer-driven wake-ups are zero. Makespan and processor time meet their recorded regression thresholds. |
| Logs containing one thousand, ten thousand, and one hundred thousand events | append time, team-state-query time, replay time, peak memory | Append and live team-state-query times meet their recorded size-scaling thresholds. Replay time per event and peak memory per event meet their recorded thresholds. |
| One-shot commands and sessions under exit, timeout, cancellation, and inherited pipes | final status, cleanup, output evidence, wake-ups | The runtime publishes one final status and reaps each direct child once. No process remains after its authorized lifetime ends. Output evidence satisfies the applicable tool schema. Ordinary exit produces zero timer-driven checks. |
| One event fixture loaded by complete replay, by every incremental batch partition, and after an injected sequence gap | normalized projection, rendered view data, resynchronization start sequence | Every incremental run produces the same normalized projection and rendered data as complete replay. Gap recovery starts after the last accepted sequence, applies every missing event once, and produces the same result. |
| Paired repeated-file-inspection tasks with external mutation, shell writes, compaction, resume, and fork | model-visible bytes, canonical evidence, task quality | Compact renderings meet the recorded byte-reduction threshold. Canonical evidence reconstructs every referenced observation. No task-quality metric regresses under the [model-backed evaluation protocol](evaluation.md#model-backed-task-quality). |
| The `zicato` Python-host proposer run with host tools and a managed-cloud endpoint | protocol transcript, cancellation, logs, completion | The proposer completes. Its transcript satisfies the [host-protocol specification](protocol.md). Its cancellation, episode-log, completion-verification, and managed-cloud endpoint checks pass. |

Every change runs these checks:

- Rust formatting, lint, tests, and line budgets: `cargo fmt --all --check`,
  `cargo clippy --all-targets -- -D warnings`, `cargo test --workspace`, and
  `scripts/loc.sh`.
- Executable examples: `cargo build -p foe`, followed by
  `scripts/examples.sh target/debug/foe`.
- Browser build and tests from `view/`: `pnpm install --frozen-lockfile`,
  `pnpm build`, and `pnpm test`.
- Python host tests from `python/`: `uv sync --all-extras`, followed by
  `uv run pytest`.
- Deterministic runtime conformance: `bazel run //evals:runtime-evals`.
- Release size: `cargo build --release -p foe`, followed by the stripped-binary
  size assertion in `.github/workflows/ci.yml`.

Coordination changes must add no model request. Transport and process changes
must add no authority.

## Execution plan follows dependency order

Each numbered item produces a reviewable pull request. An item may be split
when its tests and implementation form a smaller independent change.

1. **Record runtime baselines.** Add deterministic benchmark fixtures for log
   scaling, team capacity, direct endpoint reuse, process lifetime, browser
   projection, and repeated file observations. Record a separate baseline for
   the downstream host integration. Create the complete baseline record
   defined under Reproducible benchmarks for each workload before changing
   runtime code.

2. **Publish event and budget revisions.** Extend the in-process log and
   budget pool with monotonic change receivers. Add borrowed suffix access to
   events. Preserve the file and mirror ordering rules. Prove recovery from a
   process stop between file append and projection update.

3. **Make team state incremental.** Split the full team fold into
   initialization and per-event application. Protect the cached team
   projection with the existing operation lock. Before inspection, message
   delivery, scheduling, or wait evaluation, apply the unseen event suffix
   under that lock. Remove full-log clones from the team path.

4. **Reuse endpoint connections.** Add a reusable endpoint client that uses
   the existing wire-codec and credential-source interfaces. Add local
   endpoint conformance tests for every configured wire format and for
   managed-cloud routing. Verify direct and hosted deployment, credential
   refresh, cancellation, stale connections, bounded streaming, and retry
   classification.

5. **Unify process supervision.** Create one internal supervisor that owns the
   shared launch description, output pumps, exit publication, cancellation,
   termination, and child reaping. Adapt one-shot execution and sessions while
   preserving their tool schemas and evidence. Remove duplicate lifetime loops
   after conformance tests verify both adapters under exit, timeout,
   cancellation, inherited-pipe, and cleanup cases.

6. **Remove timer-driven internal waits.** Make child settlement, inbox waits,
   and session exits await their revision sources. Retain declared deadlines
   and request backoff. Use the virtual clock for every remaining elapsed-time
   rule.

7. **Share scheduler capacity mechanics.** Keep workflow readiness and team
   readiness in their owning crates. Route both executors through the kernel's
   capacity receiver and ordered acquisition. Remove the workflow's deferred
   timer check. Retain the lead's serialized team assignment.

8. **Construct episodes through one factory.** Retain the resolved contract
   tree and captured executables across planning and root construction. Move
   registry, transport, context, child router, session, and loop assembly into
   one internal module. Verify that planning remains free of credentials,
   processes, network access, and log writes.

9. **Build one browser projection.** Introduce the normalized event reducer
   and migrate one view at a time. For each view, compare complete replay with
   every incremental batch partition of the projection fixture. Remove that
   view's separate event parser only after its normalized projection and
   rendered data are equal in both modes.

10. **Qualify compact unchanged observations.** Run the same tasks with compact
    rendering enabled and disabled. Implement compact renderings only if they
    meet the recorded byte-reduction threshold and produce no task-quality
    regression. Keep bounded tool composition available and evaluate its
    default use separately.

11. **Remove superseded mechanisms.** Delete each fixed-interval constant only
    after no internal waiter uses it. Delete duplicate process helpers only
    after every caller uses the shared supervisor. Run every repository check
    listed under Reproducible benchmarks and the downstream integration
    scenario. Update each authoritative specification in the same commit that
    changes its observable behavior.

If an item's measurements fail its acceptance conditions, work that depends
on the item stops. Independent items may continue. Implementation adds a
shared abstraction only after measurements show that it removes repeated work
and satisfies compatibility and size thresholds.
