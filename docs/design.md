# Design

foe is a runtime for autonomous coding agents. One invocation of foe runs
one bounded unit of work, called an episode, with no human in the loop. The
runtime owns the agent loop, an append-only log, capability grants, process
isolation, subagents, and a viewer. Every capability the agent can use is
granted explicitly in configuration; nothing is available by default.

This document states the problem foe solves, the properties it guarantees,
and the structure that delivers them. Companion documents specify each
piece.

| document | specifies |
|---|---|
| [log-format.md](log-format.md) | every event type, the request snapshot, and the replay guarantee |
| [protocol.md](protocol.md) | the line protocol between foe and a host process |
| [config.md](config.md) | every configuration key and the domain of its values |
| [models.md](models.md) | the model providers, their credentials, `foe login`, and the exec transport |
| [sdk.md](sdk.md) | the Python package |
| [tools.md](tools.md) | built-in tools, configured executables, and host tools |
| [sandbox.md](sandbox.md) | how grants compile into kernel restrictions |
| [viewer.md](viewer.md) | the trajectory viewer |
| [landscape.md](landscape.md) | the surrounding field of agent runtimes |
| [evaluation.md](evaluation.md) | runtime conformance checks and the model-backed benchmark protocol |
| [deferred.md](deferred.md) | features with reserved event types and no implementation |
| [workflow.md](workflow.md) | declared dataflow graphs, choice points, and recovery |
| [compaction.md](compaction.md) | when and how the model's context is compacted, and what survives the cut |
| [design-language.md](design-language.md) | the visual language the viewer follows |
| [viewer-study.md](viewer-study.md) | a historical record of the layouts weighed before the viewer's outline |

## The problem

Coding agents built for interactive use assume a person is present. The
person supplies the objective, approves consequential actions, notices when
the agent is stuck, and decides when the work is done. Remove the person and
each of those four jobs is unfilled. An interactive harness run with
approvals disabled does not become an autonomous agent; it becomes an
unsupervised one.

A runtime for hands-off execution has to fill each job mechanically.

| job the person did | what fills it without a person |
|---|---|
| supplies the objective | a task, fixed at launch, with a declared completion condition |
| approves consequential actions | an allow list of directories, executables, and child contracts, enforced by the kernel |
| notices when the agent is stuck | runtime detection of repeated calls and repeated reasoning, plus a vocabulary of blocking conditions the agent can report |
| decides when the work is done | a budget that ends the episode, a verifier that accepts the result, or a typed return |

Three further problems follow from running without supervision.

**Nobody watched, so the record must be complete.** An interactive transcript
is read by the person who was there. An autonomous run is read later by
someone who was not, or by software. The record therefore has to contain
every input the model received and every output it produced, in a form that
reconstructs the run without the process that made it.

**Nobody corrects course, so the cost must be bounded.** A person notices
when an agent spends an hour in a loop. A runtime has to enforce limits on
model calls, input tokens, output tokens, wall-clock time, recursion depth,
and the number of processes an agent may start. It has to hold those limits
as one pool across every subagent the run creates.

**Nobody reviews each action, so permission must be structural.** A per-action
prompt is how interactive harnesses contain risk. Without a person to answer
it, containment has to be declared once, before the run, as a list of what is
reachable, with everything else unreachable by construction.

foe is built to fill those jobs and meet those three requirements. A
conventional interactive harness, or a person, hands foe a task and a
configuration and receives a log and an outcome.

## Properties

Four properties determine every structural decision.

**Autonomous.** No step of an episode waits for a person. Termination is
mechanical: a budget is spent, a verification passes, a blocking condition is
recognized, or the model finishes. Steering input exists, and its producers
are other episodes.

**Token efficient.** What the model reads is a projection of what the log
stores. Tool results have a canonical value, which the log keeps in full, and
a rendered form, which the model sees. A failed result carries a typed code,
retry rule, and structured details beside its explanatory message. Request
prefixes are byte-stable across steps and across sibling episodes so that
provider caches hit.

**Auditable and replayable.** Every input to every model request is
reconstructable from the log, and every response is recorded in it. A contract
fingerprint hashes the stable inputs that shape model-visible behavior.
Computing it starts no process, opens no network connection, and reads no
credential.

**Governable.** Permission is an allow list. A configuration names the
directories an episode may read and write, the executables it may run, and the
child contracts it may spawn. Everything unnamed is unreachable. Where the
kernel supports it, the same list is enforced by the kernel for every process
the episode starts.

## Architecture

A running foe is a tree of processes sharing one directory tree of logs.

```
                         host process
                (Python package, orchestrator, or CLI shell)
          holds host tools, and credentials when it calls the model
                              │
             stdin: answers   │   stdout: the log, line by line
                              ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  foe episode  (root)                      Landlock ruleset A │
   │                                                              │
   │   config ──► registry ──► loop ──► log  ──► episode.jsonl    │
   │                 │           │                                │
   │           built-in tools    │  spawn                         │
   │           exec tools ───────┼──────────┐                     │
   │                             │          │                     │
   └─────────────────────────────┼──────────┼─────────────────────┘
                                 │          │
              ruleset B ⊂ A      │          │   ruleset C ⊂ A
                  ▼              │          ▼
        ┌──────────────┐         │   ┌──────────────────────────┐
        │ /usr/bin/ruff│         │   │ foe episode (child)      │
        │ argv, stdout │         │   │ children/ep_9c21/        │
        └──────────────┘         │   │   episode.jsonl          │
                                 │   └──────────────────────────┘
                                 ▼
                        <episode-dir>/
                          episode.jsonl
                          spill/
                          children/ep_9c21/episode.jsonl
```

Three facts about this picture carry the design.

A host that supplies the transport holds every credential, and the episode
process then has no key and no network. A model call is a request the
episode writes to its log and the host answers. A host that leaves the
model to a `model` block answers no such request; the episode process then
holds the credential that block names and reaches the provider itself.

Restrictions only narrow downward. The root episode runs under a ruleset
compiled from its grants. Every process it starts runs under a subset of
that ruleset. Nothing a child or a tool does can reach further than its
parent could.

What narrows is reach: read roots, write roots, and the depth still
available below. A child's tool list is its own and need not be a subset of
its parent's, so a child may be offered `edit` where its parent has only
`spawn`. Every such tool is still bounded by the child's grants, which lie
inside the parent's, and by the ruleset the child inherits. One document
declares every level, so the tool list of each level is the author's
choice rather than something the runtime derives.

The log directory is the whole state. Copying it copies the run. The viewer,
replay, forking, budget accounting, and team coordination all read it and
nothing else.

## The episode

An episode is one run of one contract against one task. It has a log, a
budget, a set of grants, and exactly one outcome.

```
Outcome =
  | Completed { value }             the contract's termination condition was met
  | Blocked   { code, message }     the agent recognized that it cannot proceed
  | Exhausted { limit }             a budget limit was reached
  | Failed    { error }             the runtime could not continue
```

`Blocked` carries a stable lower-kebab-case code chosen from a closed
vocabulary so that a supervising episode can route on it. The vocabulary is
listed in [log-format.md](log-format.md#blocked-codes).

A finished episode is never extended. An interrupted one — a log without
`episode/end` — is continued by launching over its directory, under "The
command line" below. A later episode may be seeded from a prefix of any
log, which is how replay and forking work.

The model's context is a projection of the log, and the projection is
bounded. When a configuration enables compaction and the next request is
projected to outgrow the model's window, the runtime summarizes the oldest
steps through one recorded model call. From then on the projection opens
with the task verbatim, the runtime's record of what the model must still
honor, and the summary, followed by the kept recent steps. The log keeps
every event the summary replaced. [compaction.md](compaction.md) specifies
the trigger, the cut, and what travels across it.

### One step

A step is one model request and the tool calls it produces.

```
  ┌─────────────────────────────────────────────────────────────────┐
  │ 1. assemble                                                     │
  │    if one request remains, enqueue the final-request warning    │
  │    header  = instructions + tool instructions + tool schemas    │
  │    messages = derived from the log + unconsumed inbox items     │
  │    write request/header if changed; write model/request         │
  ├─────────────────────────────────────────────────────────────────┤
  │ 2. stream                                                       │
  │    each chunk ──► assistant/chunk                               │
  │    on done    ──► assistant/message                             │
  │    on failure ──► request/retry, or interrupted message         │
  ├─────────────────────────────────────────────────────────────────┤
  │ 3. execute                                                      │
  │    stop == length ──► every call fails, none runs               │
  │    preflight each call in issue order: resolve, validate,       │
  │      check effect against grants                                │
  │    run pure/reads calls concurrently; others one at a time      │
  │    append tool/result in issue order                            │
  ├─────────────────────────────────────────────────────────────────┤
  │ 4. settle                                                       │
  │    block called?  ──► Blocked                                   │
  │    looping?       ──► Blocked                                   │
  │    done_when met? ──► Completed                                 │
  │    budget spent?  ──► Exhausted                                 │
  │    else           ──► next step                                 │
  └─────────────────────────────────────────────────────────────────┘
```

The settle order checks the budget last, so a turn that completes the task
on the last permitted model call completes the episode. The budget ends the
episode only when work remains. The last available ordinary request contains
a recorded system inbox item that directs the model toward the
highest-priority unfinished work. The item preserves the configured
completion rule and the finite allowance.

Three rules hold in every step. Each exists because its absence loses data.

- A response that ended because it hit the provider's output length limit has
  every tool call rejected. Streamed tool-call arguments are recovered by a
  tolerant parser, so a truncated call can parse and validate while missing
  its tail. The model receives one error per call and reissues them.
- Every pairing the log opens is closed before `episode/end`. A tool call
  owes a result, a reservation owes a release, a spawn owes an end, a
  compaction owes an end, and a retry owes the attempt it announces.
  [log-format.md](log-format.md#open-obligations) lists them and states the
  rule; the episode's teardown writes whatever the episode itself did not,
  and seeding applies the same repair to a copied prefix.
- Tool calls are preflighted one at a time in the order the model issued
  them. Calls whose declared effect is `pure` or `reads` run concurrently.
  Calls that write, execute, or spawn run one at a time in issue order. Results
  are appended in issue order regardless of completion order.

### The event model

Everything that happens to an episode from outside its own turns reaches
the model one way: as an `inbox/item` in the log. The inbox is the single
event queue and its `source` is the event's type — the task, a parent's
steer, a child's report or ending, a peer message, verifier findings,
runtime notices, and session exits. Items are appended the moment they
arrive and delivered only at request boundaries: the next `model/request`
names every newly delivered item in `consumed`, so nothing interrupts a
request in flight, and the `consumed` lists are a reconstructable account
of the event loop: what the model saw, and when, is derivable from the
log alone.

The cost model behind the loop is that model turns are the expensive
resource and wall-clock time the cheap one. `wait` is the sanctioned
trade between them: it spends wall-clock time so that no model turn is
spent polling. Bare, it blocks until every child has ended. With `until`,
it blocks until an arrival matches one of the named conditions, each in
outcome vocabulary: a child (by id, or `any`) reaching any outcome or a
named outcome kind, a session (by id, or `any`) exiting, or an inbox
arrival by source; `timeout_seconds` returns the call after that long
even if nothing matched. The result names only the condition met, or
`timeout`. The arrival itself reaches the model through the ordinary
inbox drain of the next request, so the `consumed` lists remain the
complete record. Blocking counts against the `seconds` budget like any
elapsed time, and `wait` is itself a tool call, so all blocking happens
where blocking already happens.

The same pieces form the verified-future pattern foe implements: `spawn`
returns the handle, `done_when.returns` is the future's type,
`done_when.verify` is the resolution predicate, and `wait` is the join.
The predicate self-repairs: findings return to the model for another
attempt, and retries spent reject the future into a typed `Blocked`. A
parent that spawns, continues its own work, and then waits is composing
futures whose resolution the log evidences end to end.

### Termination

An episode ends by writing `episode/end`, and before that it closes every
obligation its log still holds. A child still running is asked to end, and
the `spawn/end` and `budget/release` its reservation owes are awaited. A
tool call left without a result receives a synthetic error result. The
record of a completed run is therefore never mistakable for the record of
one killed mid-flight.

A process session normally ends at episode settlement. A contract with the
`task_session` grant may request task lifetime when it starts a session.
Settlement then records the process and process-group identifiers and
transfers cleanup responsibility to the environment that owns the foe
invocation. Every remaining group member retains its sandbox restrictions
after foe exits.

Ending a child that a model meant to keep is a poor answer, so a parent
that means to wait says so: the `wait` tool returns once every child it
started has ended, bounded by the episode's `seconds` budget. When it
returns because that budget ran out, it returns an error naming how many
children are still running, and the episode ends as exhausted at its next
step. A contract that declares no `seconds` gives `wait` no bound of its
own; the wait then lasts as long as the children do. A model that means to
abandon its children ends its turn as usual, and the teardown settles them.
With `until` conditions or `timeout_seconds`, `wait` returns as "The
event model" above specifies.

`seconds` is the one bound that every episode in the tree shares as a
single deadline rather than dividing between children. A child's
reservation caps its `seconds` at what the parent has left, so one deadline
ends every episode below it.

A contract need not declare `seconds`. An episode that declares none still
reaches an outcome, and two rules stand in place of the deadline.

The first is that every wait the runtime performs is cancellable. The stop
signal, which a host raises by sending `cancel` and a terminal raises by
interrupting, ends the wait and the episode holding it, at whatever depth
of the tree that episode sits.

The second is that a wait for an answer no process can give is never
entered. A host tool call that reaches a process with no host is answered
there with an error naming the tool, which
[protocol.md](protocol.md#children) states, so no episode waits on a line
that nothing above it would read.

What an episode without `seconds` gives up is the bound on an answer that
could still arrive: one owed by a live host, or one owed by a child that is
still working. An operator who wants that bound declares `seconds`. A
contract whose work legitimately outlasts any deadline its author could name
declares none, and ends when its host cancels it.

A contract's `done_when` field chooses how an episode completes.

| `done_when` | the episode completes when |
|---|---|
| absent | the model produces a turn containing no tool calls; the value is that turn's text |
| `{ "verify": TOOL, "retries": N }` | the model produces a turn with no tool calls or a non-error call to TOOL, then TOOL returns no findings as a verifier |
| `{ "returns": SCHEMA }` | the model calls a synthesized tool named `return` with a value conforming to SCHEMA |

The `verify` and `returns` forms combine: a returned value may be verified.
A contract author declares a schema only when the output has a known shape.
A verifier is a tool, so an author who can check a result without being able
to describe its shape declares the verifier alone.

In an agent-loop episode, a return schema that requires `learned` also
requires evidence citations. Each claim cites the sequence of a successful
tool result in the same episode. The runtime checks that the result exists
and that its canonical value remains reconstructable. A configured verifier
then judges semantic correctness. Without a verifier, semantic judgment
remains with the contract's model or a successor such as an independent
audit.

For a verifier without a return schema, a non-error ordinary call to the
declared verifier also signals completion. The runtime invokes the verifier
again after every tool effect in the turn has settled. Acceptance completes
the episode without a separate model request. Findings enter the inbox under
the same retry limit as findings after a turn with no tool calls.

### Failure of a model request

A request that fails before any byte arrives is retried with bounded backoff.
A request that fails after text arrived and before any tool call started is
discarded and retried. A request that fails after a tool call started is
recorded as an interrupted assistant message; its tool calls receive synthetic
error results, and the next step continues from there. Retries consume the
episode's request budget. There is no unbounded retry.

The bound depends on the cause. A provider-reported outage — a retryable
provider error or rate limit — is waited out with backoff rising to a
minute per delay, for as long as the seconds budget funds the next delay
and the model-call budget funds the next attempt; when the remaining
budget cannot fund another attempt, the step ends blocked with a message
naming the budget. Waiting costs only what the budget already meters, so
the budget, not an attempt count, is its bound. Every other cause —
transport loss, an interrupted stream — has a fixed attempt ceiling,
because repeating does not fix what it names.

The attempt ceiling is tested before the delay is computed, and the
`request/retry` event is written immediately before the attempt it
announces. A step whose last permitted attempt fails therefore ends at
once, with no delay waited and no retry recorded for a request that is
never made.

### Blocking conditions the runtime detects

The runtime recognizes two forms of lack of progress without model judgment.

- The same tool call, with identical arguments and an identical result,
  issued in three consecutive steps ends the episode with `looping-tool-call`.
- Three consecutive assistant turns with identical text end the episode with
  `looping-reasoning`.

Both thresholds are configurable in `budget`. The model reports the
conditions it can recognize and the runtime cannot, such as an ambiguous task
or a missing capability, by calling the built-in `block` tool with a code
from the closed vocabulary. A contract that lists `spawn` and has a non-empty
`grants.spawn` may also report `child-blocked` when its children prevent
further progress. The tool schema omits that code from other contracts.

## Execution contracts and fingerprints

An execution contract is the validated configuration Foe runs for one
episode: instructions, tools, permissions, budgets, completion rules, model
selection, child contracts, and workflow. Rust names the resolved object
`ResolvedContract`, and schemas use `contract_*` fields. The task is a
separate invocation input.

The `grants` object declares configured permissions. Contract construction
resolves those declarations with the exact tools, captured executables,
interpreters, loaders, credentials, and runtime paths needed for execution.
`foe plan` opens with a readiness summary — one line each for the model,
the granted read, write, and execute roots, the completion mechanism, the
limits, the sandbox mode, the workflow size when one is declared, and the
static warnings — then reports the resulting reachable tools and resolved
permissions. Each summary line projects the same resolved objects the
detailed report prints, so the two cannot disagree.
The episode log records the resolved permissions with the sandbox mode,
Landlock ABI, and process boundary that state what the host enforced.

Construction resolves the root contract, every `child_contracts` entry, and
every workflow model node into one immutable contract tree. It canonicalizes
paths, inherits model and sandbox settings, and validates descendant ceilings.
Planning, fingerprinting, budget reservation, sandbox construction, and
spawning all read this tree.

During execution-contract construction, Foe captures each configured
executable's bytes, digest, source path, and invocation name. Every later
invocation uses the captured executable. Replacing, modifying, or deleting the
source cannot change the run.

`fingerprint(contract)` is a SHA-256 over a canonical serialization of:

- the instruction sections, by key and text;
- each tool's name, description, instruction, and parameter schema, in the
  order listed; a configured tool also contributes its executable digest and
  invocation name;
- the permission shape, meaning the kinds and counts of grants;
- the budget and termination condition;
- every child contract's fingerprint;
- every model-visible string the runtime itself contributes, such as the
  description of the synthesized `return` tool and the text that frames
  verification findings;
- the runtime's version and build hash, plus the executable transport's
  content digest and invocation name when the model provider is `exec`.

The task, model route, sandbox mode, and paths in the resolved permission set
are excluded.
Two executions may use different values for those fields while retaining one
contract fingerprint. Runtime-contributed strings are included, so an upgrade
changes the fingerprint when it changes model-visible text.

Construction stores captured executables outside every declared write root.
References with the same digest and invocation name share one held inode. A
child checks each inherited descriptor against its sealed manifest. The digest
in the fingerprint and the bytes that run therefore come from one construction
observation.

`episode/start` records the composite contract fingerprint rather than every
executable digest. `foe plan --json` exposes the fingerprint document that
contains the individual digests and invocation names.

## Tools

A tool has a specification and an implementation. The specification is what
fingerprint hashes and what the model sees.

```
ToolSpec {
  name            unique within the contract
  description     shown to the model in the tool schema
  instruction     optional; appended to the system prompt after the instructions
  params          JSON Schema for the arguments, in the subset config.md lists
  effect          pure | reads | writes | execs | spawns
}
```

The effect is the tool's declared interaction with the world. The registry
refuses a tool whose effect the grants do not cover, at contract construction.
Dispatch checks a call's arguments against the tool's parameter schema before
the tool receives any handle, so a tool implementation never sees arguments
its own schema rejects. [config.md](config.md#json-schema-subset) lists the
assertions the runtime implements; a schema asking for more is a construction
error rather than a constraint the runtime silently drops.
At dispatch, the runtime passes the tool only the capability handles its
effect entitles it to. The handles are a filesystem reader holding the read
roots open, a writer holding the write roots open, an executor bounded to the
configured executable and explicit execute grants, and a spawner bounded to
the declared child contracts.
A tool that declares `reads` receives no writer. The reader and the writer
hold their roots open for the episode's lifetime, so containment holds when
an operation runs rather than when a pathname was last checked.

```
   grants                    registry (construction)          dispatch (per call)
   ──────                    ───────────────────────          ───────────────────
   read:  [/src]     ──►     read   effect=reads   ok    ──►  Reader(/src)
   write: [/scratch] ──►     edit   effect=writes  ok    ──►  Reader(/src) + Writer(/scratch)
   execute: [/tools] ──►     bash   effect=execs   ok    ──►  Executor(bash, env, cwd, /tools)
   spawn: []         ──►     spawn  effect=spawns  REFUSED
```

A `bind` grant appears in neither column: it names TCP ports rather than a
tool's effect, no tool requires it, and it reaches every process of the
episode through the compiled sandbox alone ([sandbox.md](sandbox.md)).
The `task_session` grant also appears in neither column. The session
capability checks it only when a `start` call requests task lifetime.

Tools come from three sources, resolved in this order at construction.
A name that resolves in two sources is an error.

1. Built in, fourteen of them: `read`, `grep`, `edit`, `bash`, `retrieve`, `session`,
   `python`, `block`, `spawn`, `wait`, `steer`, `notify`, `send`, and `team`.
2. Configured executables, declared in `tool_defs` with a path and a
   description. The runtime passes the model's `args` array as argv, captures
   stdout and stderr, and reports the exit code as data. A non-zero exit is a
   result rather than an error. Any executable that accepts arguments is a tool
   without modification.
3. Host tools, implemented by the process that launched foe and called over
   the [protocol](protocol.md).

Every tool returns a canonical value, which is JSON. A tool may also return a
rendered string. The log stores the canonical value. The model receives the
rendered string when present and a compact rendering of the value otherwise.
The separation is the runtime's main token lever: a search over a large tree
can record every match and show the model a count and the first twenty.

The runtime applies that lever to every tool through one budget. A tool
result is re-sent in every request after the step that produced it, so the
cost of a rendering is its size multiplied by the number of later requests.
The renderings of one model turn therefore share one character budget, which
the calls of that turn divide between them. A rendering over its part ends
with a notice stating what was removed. The runtime archives the complete
rendering as immutable episode evidence before it appends the shortened
result. A contract that declares `retrieve` receives an opaque cursor in the
notice and can read the archive in bounded segments. Its schema enters the
request header after the first archive is recorded. An episode that never
shortens a result does not carry that schema. Other execution contracts receive the
existing instruction to narrow and repeat the original call. The canonical
value is untouched. The cut is applied before the result is appended, so no
earlier turn is rewritten and a provider can reuse its key-value cache of the
prefix.
[tools.md](tools.md#the-turn-budget) specifies the division and the
notice.

## Subagents and teams

An episode with a `spawn` grant may start child episodes. A child is a
separate process with its own log, its own grants, and a budget reserved from
its parent's remaining budget. The child's log header names the parent.
The child may select a model or inherit the nearest ancestor's selection.

The parent writes the declared child contract unchanged. It writes the
effective runtime allowance and the expected declared-contract fingerprint in
the child's launch metadata. The child resolves that document and compares
its fingerprint before writing `episode/start` or executing a tool. A mismatch
fails the launch. The successful `episode/start` records the expected fingerprint
and the effective allowance. Different reservations for one declared child
therefore change its runtime allowance while preserving its contract fingerprint.
For forked context, the launch metadata also names the source log and boundary.
The child validates its fingerprint before it seeds that prefix under its own
contract evidence.

Before launch, the parent passes the captured executables from the selected
child's full declared contract tree. A sealed manifest maps every configuration
key to a deduplicated descriptor, digest, and invocation name. The child
constructs its contract from those retained bytes and checks the expected
fingerprint before writing an event. Sandbox permissions include only
executables reachable through the child's spawn grants and workflow nodes.
Source-path replacement, in-place modification, and deletion therefore cannot
change child fingerprint or execution.

Child creation separates identifier allocation from launch. Allocating an
identifier reserves no budget and starts no process. A parent appends the
event that names the child before launch can reserve budget or create the
process. A workflow therefore records `workflow/node-start` before its
spawner records `budget/reserve` and `spawn/start`.

```
   root   budget: 40 calls, 320k input, 80k output
    │
    ├── reserve 10 calls ──► child A   (spent 7, returned 3)
    │
    └── reserve 10 calls ──► child B
                              │
                              └── reserve 4 calls ──► grandchild B.1
```

Budget is a pool held by the root. Every spawn reserves from the parent's
remainder, and unspent reservation returns when the child settles. Model
calls, input tokens, and output tokens are separate dimensions. Structural
caps on depth, lifetime episode count, and concurrency sit beside them.

After each completed response, the runtime charges the provider-reported
input to the tree. It starts another request only while some input allowance
remains. Foe does not send a per-request input cap, so one response can cross
its remaining input allowance. Concurrent descendants can each cross their
reserved allowances. The runtime clamps a supported provider's output cap to
the remaining output allowance.

A spawn that would pass a cap fails as a tool call with a result naming the
limit, and no child starts; the model reads that result like any other.
A parent observes a child as settled only after it has appended `spawn/end`
and `budget/release` and returned the child's reservation to the pool, so
anything waiting on the child sees the account of it already closed.

Communication is an inbox append with a typed source. A parent steers a
running child by appending to the child's inbox. A child notifies its parent
the same way. A steer arrives in the child's next request; nothing
interrupts a request in flight. A parent that has delegated work calls
`wait` to hold until its children have ended, so that their reports are in
the request that follows.

A team is a set of episodes that share a lead. The lead's log holds the
roster and the queue of messages between members.

```
   lead log                                   member log
   ────────                                   ──────────
   team/roster    {member, name, phase}
   team/message   {id, from, to, content}  ──►  inbox/item {source: peer, message_id}
   team/delivered {id, to}                 ◄──  (written after the member's append)
```

Six built-in tools serve teams. `spawn`, `wait`, and `steer` act on an
episode's own children. `notify`, `send`, and `team` act on the team the
episode belongs to: in an episode with a parent they are host tool calls that the
parent answers, as the [protocol](protocol.md#children) describes; in a
root, `send` and `team` act on its own roster, and `notify` fails because
no parent exists.

A message is durable in the lead's log before delivery is attempted. The
member's receipt is recorded after the member has written the message to its
own log. Messages queued and never delivered are redelivered when a member
restarts, and the member drops duplicates by `message_id`. The roster, the
queue, and the delivery records are folded from the lead's log; no other team
state exists.

## Workspace notes

A workspace's durable notes live at `.foe/notes.md` under the first read
root. This is a convention over an ordinary file rather than a mechanism:
no runtime behavior attaches to the path. An entry carries one claim and
one citation, the episode id and the log sequence number of the event that
evidences the claim, so a later reader can weigh the note against the
record that produced it.

A contract whose instructions direct it reads the file when the file is
present. Whether the notes enter an episode's context is the launching
parent's judgment or the contract's declared instructions; the runtime never
injects them.

## Isolation

An episode runs as its own process. Children and configured executables run
as further processes. Restrictions only narrow at each spawn.

```
   host            no restriction applied by foe, ever
     │
     └─ episode    Landlock: read roots, write roots, execute roots, own log dir
          │        network: open when foe holds the transport; closed when the host does
          │
          ├─ tool  Landlock: subset of the episode's; network closed
          │
          └─ child Landlock: compiled from the child's own grants, which the
                   parent's registry already verified are a subset of its own
```

On Linux with Landlock available, the runtime compiles the grants into a
ruleset. Read roots become read rules, write roots become write rules, and
execute roots become read-and-execute rules. Each configured executable
becomes an execute rule on that exact file. The episode's log directory
becomes a write rule. When the kernel supports it, TCP access is removed from
executables. Denied accesses are captured from the audit log and written to
the episode log as `sandbox/denied` events. A blocked attempt therefore
becomes evidence in the record.

`sandbox.mode` controls behavior when Landlock is unavailable. `best-effort`,
the default, applies what the kernel supports and records which version it
got. `required` refuses to start. `off` applies nothing.

The process that launched foe is never restricted, because it holds the
transport and the credentials, and restricting it would break the host.

## The host protocol

foe writes its log to a file and echoes every event to standard output as it
is written. A host process that launched foe reads that stream and answers
two kinds of request on foe's standard input: model requests and host tool
calls.

```
   host                                          foe
   ────                                          ───
                                      ◄──  episode/start
                                      ◄──  inbox/item (task)
                                      ◄──  request/header
                                      ◄──  model/request {messages}
   model/chunk {text "I will"}        ──►
   model/chunk {tool_call_start}      ──►
   model/chunk {done}                 ──►
                                      ◄──  assistant/chunk ×n
                                      ◄──  assistant/message
                                      ◄──  host/tool-call {mutation_usage}
   tool/result {value, rendered, failure?} ──►
                                      ◄──  tool/result
                                      ◄──  model/request …
                                      ◄──  episode/end {outcome}
```

The answers are recorded as log events when foe receives them. The log is
therefore complete by construction, because every exchange with the host
passed through it. A host that supplies the transport keeps credentials in
its own process, and the episode process then has no network access of its
own.

## The command line

The binary has one running form and five forms that run nothing.

```
foe "task" [--config FILE] [--log-dir DIR] [--no-open]   run; serve the viewer; print the outcome
foe "task" [--model PROVIDER/MODEL] [--service-tier TIER] [--key-file PATH] [--verify PATH] [--sandbox MODE]   run the built-in coding workflow
foe "task" --headless                                    run; no viewer; print the outcome
foe "task" --fork SOURCE_DIR --at SEQ                    run a fresh episode seeded from a prefix of SOURCE_DIR's log
foe --config FILE --host [--log-dir DIR]                 run under a host; stdout is the log (protocol.md)
foe login [PROVIDER [--model MODEL]] [--status]          configure a provider's credential and the default model
foe init --repository PATH                               write a starting execution contract and a placeholder verifier into PATH/.foe
foe view DIR [--serve [--port N]]                        write a self-contained HTML file, or serve it
foe plan [--config FILE] [--json]                        print a readiness summary, then the resolved contract, its fingerprint, transport, reachable tools, resolved permissions, and static warnings; without --config, list the built-in tools
foe plan --schema                                        print the JSON Schema for the configuration
foe telemetry LOG... [--json]                            print what telemetry emission writes for finished logs
```

One declarative table in `crates/cli/src/main.rs` names every form, its
positional shape, and each option it accepts with that option's value
placeholder, its default, and its meaning. The parser and both help screens
read that table and nothing else, so an option the parser accepts is
documented and an option the table omits is refused. `foe --help`, which
`foe help` repeats, prints the running form's options and every other
command word; `foe <command> --help`, which `foe help <command>` repeats,
prints one command's options; both exit 0. An unrecognised option names
itself and the help that lists what its command takes, rather than
reprinting every form.

In every running form except `--host`, standard output receives exactly one
line when the episode ends: the outcome as JSON. A shell reads it with one
`read`; another process parses it with one `json.loads`. The exit code is 0
for `completed`, 2 for `blocked`, 3 for `exhausted`, and 1 for `failed`.
Progress goes to standard error. The log goes to the file.

The log directory is `--log-dir` when given and `.foe/<episode-id>` under
the current directory otherwise. A directory that already holds a log is
continued under the log's own episode id. One whose log ends at `seed/end`
— a prepared fork — or at an event boundary with every binding obligation
closed continues in place. An interrupted log, cut short mid-line or with
an obligation open, is repaired by seeding a copy at its last clean
boundary into a fresh directory beside it, named on standard error, which
the run then continues. Resuming requires the execution contract that ran.
A configuration whose fingerprint differs from the log's
`episode/start.contract_fingerprint` is refused with both fingerprints
named. A log ending at `seed/end` is
exempt from the resume comparison. An ordinary seeded `episode/start`
records its source's contract. A spawned child instead checks the expected
fingerprint in its launch metadata before reaching resume. A finished log — one with
`episode/end` — accepts nothing and is forked instead. A `child-launch.json`
beside the log, which a parent writes for a child, supplies the child's
id, its parent, its team lead, its expected contract fingerprint, and its
effective runtime allowance.

On resume, the `episode/start.contract_fingerprint` value and effective allowance take
precedence over launch metadata. A prepared spawned fork records its child
contract in that event, so resume compares the recorded child fingerprint before
continuing it. An ordinary command-line fork preserves its source contract in
the start event and remains exempt from that comparison at `seed/end`.

`--fork SOURCE_DIR --at SEQ` runs a fresh episode seeded from the source
log's events below SEQ under the seeding rules of
[log-format.md](log-format.md): the new episode draws a fresh id, its
`episode/start.fork_origin` names the source episode and the boundary, and
the task the launch carries — the positional task, or the document's task
under `--config` — is appended as a `system` inbox item after `seed/end`,
since the one `task` item per log is the copied one. The boundary's
validity is the seeding API's rule, surfaced as the seeding error states
it. The fork's directory is `--log-dir` when given, refused when it
already holds a log, and `.foe/<episode-id>` otherwise. A slate — several
forks from one prefix — is a caller-side loop over this form;
[deferred.md](deferred.md) states what first-class support would add and
the evidence that would justify it.

A task given with `--config` replaces the document's own `task`. A task given
without `--config` uses a built-in coding workflow. An implementation episode
changes the current directory. A fresh assessment episode independently checks
the task and implementation claim. It either accepts the artifacts or activates
a fresh repair episode with its typed findings.

The static workflow document is `crates/cli/src/builtin-coding.json`. The CLI
fills its task, model, current-directory grants, executable inventory, sandbox
mode, credential path, and optional verifier before resolving it as an ordinary
contract document.

The implementation and repair episodes have `read`, `grep`, `edit`, and
`bash`. The assessment episode has `read`, `grep`, and `bash`. It has no edit
tool. All three episodes may read and write the current directory because
builds and checks can create outputs. Each episode has a 60-call backstop. The
root holds their additive 180-call allowance. A run without a verifier has a
four-episode lifetime cap, including the root.

`--verify PATH` names an executable verifier for the built-in workflow.
The path is canonicalized and becomes a `tool_defs` entry named `check`
with execute permission on that file. All three episodes may call `check`
while working. The root declares
`done_when: {"verify": "check", "retries": 12}`.
The verifier therefore governs both an accepted assessment and a completed
repair. It runs in the working directory and receives the workflow completion
value as JSON on standard input. It prints one finding per line; exit 0 with
empty output is acceptance. Findings re-fire the nearest model episode. An
assessment can respond by activating repair, and a repair can correct its
artifacts. Without `--verify`, the assessment's typed branch governs
completion. With `--verify`, both corrective nodes may fire thirteen times.
The root lifetime cap grows to sixteen episodes so all twelve retries can run.

`--sandbox MODE` selects `best-effort`, `required`, or `off` for the built-in
workflow. The default is `best-effort`. A contract document declares
its own `sandbox.mode`, so `--sandbox` cannot accompany `--config`.

Before confinement, the CLI checks fixed standard paths for common compilers,
interpreters, and repository tools. All three episodes receive the recorded
result and its limited scope. The result does not claim that unexamined paths
lack an executable.

The implementation returns a typed handoff with its summary, changed paths,
validation observations, and unresolved risks. The assessment receives that
value and the original task in a fresh context. A repair receives both prior
values and the original task. The shared directory carries the artifacts.

Rendered predecessor sections entering one model node share the same
50,000-character bound as one model turn's tool results. The runtime rejects an
oversized handoff before starting the child and records `limit-exceeded` for
workflow recovery. The producer's complete value and rendering remain in the
workflow log. Tool nodes continue to bind complete canonical predecessor
values because that binding does not enter model context.

The task text and repository-defined checks govern all three stages. Their
allowed mutation scope is current filesystem state unless the task authorizes
changes to history, prior versions, archives, encoded representations, or
hidden implementation details. A task that requires live state must leave that
state operational after validation.

Assessment and repair validate observable behavior through the strongest
task-authorized interface available. For black-box or broadly parameterized
behavior, assessment tests materially different valid inputs through the same
public interface. The stages preserve task-required final state after their
checks finish.

All three completion schemas require one to eight `learned` observations. Each
observation is a one-sentence claim and the sequence of a successful tool
result in that episode's log. The runtime checks the citation and preserves
the completed value as the typed handoff. The assessment receives the
implementation observations and independently reproduces or challenges the
claims that bear on completion. Its value also contains findings and an
`accept` or `repair` branch. A repair must return no unresolved risk.
Each stage covers every completion-critical requirement with a `learned`
claim that cites a successful tool result. The runtime verifies the citation's
episode membership, success, and reconstructability. A configured verifier
judges semantic correctness.
[config.md](config.md#done_when) specifies the contract for any contract.

The model is the one named by `--model`, or the default model when `--model`
is absent. The default model is the `model` block in
`~/.config/foe/default-model.json`, which `foe login` writes. When that block
omits reasoning effort, GPT-5.6 Sol uses low effort for implementation and
xhigh effort for assessment and repair. An explicit reasoning effort applies
to all three episodes. Other models carry the root model options into every
stage.

`--service-tier TIER` sets the model request's `service_tier` field to
`default` or `priority` for all three episodes. When the option is absent, the
default model file's value remains in effect. Otherwise the provider applies
its own default.

`--key-file` names the provider credential file explicitly. It supplies an API
key file, OAuth token state, or Google credential according to the selected
provider. Without it, the provider's credential file under
`~/.config/foe/credentials/` is read. The home directory comes from the passwd
database, never from the environment.

`foe login` configures one provider: it asks for the credential, proves it
with one request, writes it under `~/.config/foe/credentials/` with mode
0600, and sets the default model when none is set. [models.md](models.md)
specifies the providers and the flows.

`foe init --repository PATH` writes a starting execution contract to
`PATH/.foe/contract.json` and a placeholder verifier to `PATH/.foe/verify`,
and refuses to run when either file exists. Each file lands by renaming a
completed temporary in the same directory. The document is the built-in
coding workflow over the canonicalized repository root, with the default
model `foe login` recorded when one exists — without one the document omits
`model` and runs under a host. The read and write grants cover the whole
root, `.git` included, because grants are additive allow lists with no
exclusion syntax and excluding `.git` would exclude root files. The execute
grants are the standard command directories and the root: directory
breadth, a usable starting point to narrow later. The budget carries
backstops in model calls, seconds, and episodes — safety floors, not
targets — with token allowances unlimited and the loop threshold at its
default. The placeholder verifier rejects every completion candidate with
one finding naming the file a person must replace, so a run against the
untouched document ends blocked rather than completed, and the verifier's
capture at contract construction keeps the active episode judging by the
captured bytes while a future run reads the file as it then exists. The
runtime reads configuration from no well-known location: only `foe init`
and the document it writes name these paths, and the report the command
prints states every one of these decisions.

Without `--headless` and without `--host`, the binary serves the viewer on
a loopback port chosen before the process restricts itself, opens it with
`/usr/bin/xdg-open` unless `--no-open` is given, and keeps serving for
three seconds after the episode ends so that an open page receives the
final events. `foe view DIR --serve` serves a finished directory for as
long as the process runs.

## The viewer

The viewer renders a log directory. It shows parent, child, and fork episodes, the
conversation derived from each log, each tool call with its rendered and
canonical forms, budget consumption, sandbox status, and the outcome.

```
   ┌─ episodes ──────────┬─ conversation ─────────────────────────────────┐
   │ ▾ root   completed  │  system   charter, 2 sections · 4 tools        │
   │   ├ A    completed  │  user     Fix the failing parser test.         │
   │   └ B    blocked    │  tool     read tests/parser_test.py  → 212 ln  │
   │         looping-    │  tool     grep "def parse"           → 3 hits  │
   │         tool-call   │  asst     The failure is in …                  │
   │                     │  tool     edit src/parser.py         → +4 −1   │
   │ budget  31/40 calls │  tool     bash pytest tests/         → exit 0  │
   │ sandbox landlock 7  │  asst     Done. The test passes.               │
   └─────────────────────┴────────────────────────────────────────────────┘
```

A running episode serves the viewer over HTTP on the loopback interface with
a per-run token sent as a request header, and streams new events over
server-sent events. A finished episode is rendered to a single self-contained
HTML file. Both paths use one renderer: the live page is a replay that has
not finished.

## Structure

```
   crates/log ◄─── crates/contract ◄─── crates/core ◄──┬── crates/code
    every event      the document,      loop,        │    read grep edit bash
    type,            resolution,        registry,    ├── crates/transport (feature)
    serde,           tool specs,        grants,      │    model clients, credentials
    serde_json       schema subset,     budget,      │
                     harness text,      spawn,       ├── crates/workflow
                     fingerprint,          teams,       │    graph scheduling and recovery
                     inspection    result budget,    │
                                        exec,        ├── crates/context
                                        landlock,    │    projection, cut, summarization prompt
                                        protocol,    │
                                        context seam └── crates/view ◄── view/ (browser bundle)
                                                          projection, HTTP, SSE, export

                     crates/evidence ◄── contract fingerprints and proposal logs

                                          crates/cli ◄── all of the above; plan reports

   python/foe    a thin host: builds config, runs the binary, serves the protocol
   examples/     one runnable example per job, each checking its own result
```

Two foundational specifications are implemented as crates that the rest of the
repository reads. `crates/log` defines what happened. It defines every
event type, including the reserved ones. It depends on serde, serde_json, and
thiserror, and on no crate of this repository.

`crates/contract` defines what was to run: the
contract document, the validation and resolution that turn it into the
contract `episode/start.contract` records, the specification of every tool the
model will see, and the fingerprint that hashes them. It runs nothing: no
process starts there, no grant is exercised, and no log is written. It sits
above `crates/log` rather than beside it, and states part of itself in the
log's vocabulary. The two specifications share two facts.

- The sandbox mode. `sandbox.mode` in the document and the mode in the
  `episode/start` sandbox record are one word over one closed set. A
  configured confinement and an observed confinement are the same fact, read
  before the run and after it.
- The continuation a compaction writes. The contract fingerprint hashes its shape: the
  fields of the carried state, the labels its rendered lines take, and the
  templates that render them. A contract is defined in part by how its
  conversation survives a compaction, because two contracts that differ only
  there put different text in front of the model.

`crates/contract` therefore depends on `crates/log`, and on no other crate of
this repository.

`crates/core` is the machine that applies both specifications, and depends on
both crates.
The line between it and `crates/contract` is resolution against execution: what
a name means and what a contract would be belong to the configuration, and
running it, guarding it, and charging it belong to the kernel. Tools depend
on `crates/core` for the tool trait and capability handles and on
`crates/contract` for what a tool declares. `crates/workflow` depends on
`crates/contract` for the graph type and the contract each model node runs, and
on `crates/core` for the log, the registry, the budget pool, and the spawner,
and runs an episode whose configuration declares a `workflow` in place of the
loop. `crates/context` depends on `crates/core` for the context policy trait
and implements it: the loop consults the policy before each request and lends
it one recorded model call. Nothing depends on `crates/view` except the
binary.

`crates/transport` owns each provider's authentication protocol whole: the
provider registry, the credential sources that turn a stored credential into
request headers, and — in `auth::login` — the acquisition that produces those
files in the first place. Verifying a key, the authorization-code flow with
PKCE and the loopback listener its browser half returns to, the
credential-file formats, and the default model file are all there, beside the
code that reads them back. What `foe login` adds is the conversation: which
questions each source needs, in what order, and how the answers are read from
a terminal.

`crates/telemetry` likewise owns what the enablement file means, the walk over
an episode tree that emission covers, and the preview of what emission would
write. The binary supplies only the path that file is found at, which is a
convention `crates/transport` owns, so the telemetry crate still depends on
`crates/log` alone.

`crates/evidence` verifies portable evidence for accepting a proposed
execution contract. It checks the bundle manifest, the candidate fingerprint
document, artifact evidence, the proposal episode tree, and the accepted
verification result. It depends on `crates/contract` for canonical hashing
and on `crates/log` for the episode record. Nothing in the runtime depends on
it.

## Size

The kernel is `log` and `core` — the log format, the loop, budgets, the
sandbox, and spawning — and its Rust source stays under 6,700 lines,
excluding tests and generated code. Its smallness is the product claim, so
it carries the tightest budget relative to its size. The number measures the
machine alone: what a contract is lives in `crates/contract`, which is budgeted
apart under 1,575 lines. The two are separate because a contract
document that gains a key must not buy room in the loop, and because the
claim the kernel's number supports is about the machine that runs a contract
rather than about the data model it runs.

The tool surface in `crates/code` is budgeted apart, under 1,825 lines on
the same terms. It is separate because it grows a tool at a time: a new
tool adds capability without touching the kernel, so room for tools must
not become room for the loop. The workflow executor in `crates/workflow`
stays under 1,050 lines. It schedules the graph, bounds text entering model
nodes, and routes failures through recovery. Inspection of a configured
contract tree remains in `foe_contract::inspect`, beside the model it analyses.
Both crates implement one shared rule: firing a model node starts that node's
episode. The executor realizes the rule as an ordinary spawn. Inspection reads
the same rule as reachability. The compaction policy in `crates/context` stays
under 500.

The viewer is budgeted apart from the runtime: `crates/view` under 600 lines,
and the browser bundle it serves under 150 KB compressed. It is separate
because it delivers a record of a run rather than running one, so a viewer
that grows must not force the runtime to shrink. The browser viewer's HTML,
TypeScript, and CSS count toward that compressed size and toward no line
budget at all.

The ceilings reserve 500 kernel lines, 75 contract lines, and 75 command-line
lines for captured executables and their headroom. The execution-contract
crate reads each reachable configured executable during construction.
The kernel materializes, confines, invokes, checks, and transfers those
snapshots across child process boundaries. The command line constructs the
root captured-executable tree before confinement. This mechanism adds no contract-document
key or log event.

The command line is budgeted apart from the runtime as well: `crates/cli`
under 1,425 lines. It is separate because it serves a person at a terminal
rather than an episode. What it holds is what belongs to a process rather
than to a run: argument parsing and the help derived from the command table,
the plan reports, the login conversation, the browser, the outcome line, and
the exit codes.

Telemetry is budgeted apart too: `crates/telemetry` under 1,000 lines. It is
separate because it reads a finished log rather than producing one. It holds
the enablement file's meaning, emission over a finished run's episode tree,
and the preview `foe telemetry` prints. Nothing in the runtime depends on it,
the crate depends on `log` alone, and an installation that never enables
telemetry carries none of its behavior.
See [docs/telemetry.md](telemetry.md).

Evidence is budgeted apart on the same terms: `crates/evidence` stays under
500 lines. It reads finished evidence and produces no runtime event. A bundle
check that gains a rule must not buy room in the runtime or execution-contract
crates. See [evidence.md](evidence.md).

Rust outside every line budget, in the built-in transport, is bounded by the
size of the binary it compiles into. Continuous integration enforces every
budget as a test.

The budget is a design constraint rather than an aspiration. A runtime that
other systems embed and audit earns trust in proportion to how little of it
there is to read.

## Status

The runtime, the binary, the viewer, and the Python package are
implemented. No interface is stable.
