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
| approves consequential actions | an allow list of directories, executables, and child programs, enforced by the kernel |
| notices when the agent is stuck | runtime detection of repeated calls and repeated reasoning, plus a vocabulary of blocking conditions the agent can report |
| decides when the work is done | a budget that ends the episode, a verifier that accepts the result, or a typed return |

Three further problems follow from running without supervision.

**Nobody watched, so the record must be complete.** An interactive transcript
is read by the person who was there. An autonomous run is read later by
someone who was not, or by a program. The record therefore has to contain
every input the model received and every output it produced, in a form that
reconstructs the run without the process that made it.

**Nobody corrects course, so the cost must be bounded.** A person notices
when an agent spends an hour in a loop. A runtime has to enforce limits on
model calls, tokens, wall-clock time, recursion depth, and the number of
processes an agent may start, and it has to hold those limits as one pool
across every subagent the run creates.

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
a rendered form, which the model sees. Request prefixes are byte-stable across
steps and across sibling episodes so that provider caches hit.

**Auditable and replayable.** Every input to every model request is
reconstructable from the log, and every response is recorded in it. The
identity of a program, which is a hash over everything that shapes the model's
behavior, is computable from the configuration alone, with no process, network,
or credential.

**Governable.** Permission is an allow list. A configuration names the
directories an episode may read and write, the executables it may run, and the
child programs it may spawn. Everything unnamed is unreachable. Where the
kernel supports it, the same list is enforced by the kernel for every process
the episode starts.

## Architecture

A running foe is a tree of processes sharing one directory tree of logs.

```
                         host process
                (Python package, orchestrator, or CLI shell)
                   holds model credentials and host tools
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

The host holds every credential. The episode process has no key and, when
the host supplies the transport, no network. A model call is a request the
episode writes to its log and the host answers.

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

An episode is one run of one program against one task. It has a log, a
budget, a set of grants, and exactly one outcome.

```
Outcome =
  | Completed { value }             the program's termination condition was met
  | Blocked   { code, message }     the agent recognized that it cannot proceed
  | Exhausted { limit }             a budget limit was reached
  | Failed    { error }             the runtime could not continue
```

`Blocked` carries a stable lower-kebab-case code chosen from a closed
vocabulary so that a supervising episode can route on it. The vocabulary is
listed in [log-format.md](log-format.md#blocked-codes).

Episodes never resume. A later episode may be seeded from a prefix of an
earlier log, which is how replay and forking work.

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
episode only when work remains.

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

### Termination

An episode ends by writing `episode/end`, and before that it closes every
obligation its log still holds. A child still running is asked to end, and
the `spawn/end` and `budget/release` its reservation owes are awaited. A
tool call left without a result receives a synthetic error result. The
record of a completed run is therefore never mistakable for the record of
one killed mid-flight.

Ending a child that a model meant to keep is a poor answer, so a parent
that means to wait says so: the `wait` tool returns once every child it
started has ended, bounded by the episode's `seconds` budget. When it
returns because that budget ran out, it returns an error naming how many
children are still running, and the episode ends as exhausted at its next
step. A program that declares no `seconds` gives `wait` no bound of its
own; the wait then lasts as long as the children do. A model that means to
abandon its children ends its turn as usual, and the teardown settles them.

`seconds` is the one bound that every episode in the tree shares as a
single deadline rather than dividing between children. A child's
reservation caps its `seconds` at what the parent has left, so one deadline
ends every episode below it. Without that bound, an episode that
waits on something that never arrives, such as a host tool call the host
never answers, waits without end, and every ancestor waiting on it does
too.

A program's `done_when` field chooses how an episode completes.

| `done_when` | the episode completes when |
|---|---|
| absent | the model produces a turn containing no tool calls; the value is that turn's text |
| `{ "verify": TOOL, "retries": N }` | the model produces a turn with no tool calls and TOOL returns no findings; findings are fed back for up to N further attempts |
| `{ "returns": SCHEMA }` | the model calls a synthesized tool named `return` with a value conforming to SCHEMA |

The `verify` and `returns` forms combine: a returned value may be verified.
A program author declares a schema only when the output has a known shape.
A verifier is a tool, so an author who can check a result without being able
to describe its shape declares the verifier alone.

### Failure of a model request

A request that fails before any byte arrives is retried with bounded backoff.
A request that fails after text arrived and before any tool call started is
discarded and retried. A request that fails after a tool call started is
recorded as an interrupted assistant message; its tool calls receive synthetic
error results, and the next step continues from there. Retries consume the
episode's request budget. There is no unbounded retry.

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
from the closed vocabulary.

## Programs and identity

A program is the configuration of an episode with the task removed:
instructions, tools, grants, budget, termination condition, and child
programs. Two episodes of the same program differ only in their task and in
the model's responses.

`identity(program)` is a SHA-256 over a canonical serialization of:

- the instruction sections, by key and text;
- each tool's name, description, instruction, and parameter schema, in the
  order listed;
- the grant policy, meaning the kinds and counts of grants, and never the
  resolved paths;
- the budget and termination condition;
- every child program's identity;
- every model-visible string the runtime itself contributes, such as the
  description of the synthesized `return` tool and the text that frames
  verification findings;
- the runtime's version and build hash.

Resolved paths are excluded so that running the same program against a
different directory yields the same identity. Runtime-contributed strings are
included so that upgrading foe changes identity when and only when the model
would see different text.

`identity` reads files named in the configuration in order to hash them. It
executes nothing and opens no socket. A system that records which program
produced which result, such as an evaluation harness, can therefore compute
identity on a machine that never runs the program.

## Tools

A tool has a specification and an implementation. The specification is what
identity hashes and what the model sees.

```
ToolSpec {
  name            unique within the program
  description     shown to the model in the tool schema
  instruction     optional; appended to the system prompt after the instructions
  params          JSON Schema for the arguments, in the subset config.md lists
  effect          pure | reads | writes | execs | spawns
}
```

The effect is the tool's declared interaction with the world. The registry
refuses a tool whose effect the grants do not cover, at program construction.
Dispatch checks a call's arguments against the tool's parameter schema before
the tool receives any handle, so a tool implementation never sees arguments
its own schema rejects. [config.md](config.md#json-schema-subset) lists the
assertions the runtime implements; a schema asking for more is a construction
error rather than a constraint the runtime silently drops.
At dispatch, the runtime passes the tool only the capability handles its
effect entitles it to. The handles are a filesystem reader bounded to the read
roots, a writer bounded to the write roots, an executor bounded to the
declared executables, and a spawner bounded to the declared child programs.
A tool that declares `reads` receives no writer.

```
   grants                    registry (construction)          dispatch (per call)
   ──────                    ───────────────────────          ───────────────────
   read:  [/src]     ──►     read   effect=reads   ok    ──►  Reader(/src)
   write: [/scratch] ──►     edit   effect=writes  ok    ──►  Reader(/src) + Writer(/scratch)
                             bash   effect=execs   ok    ──►  Executor(bash, env, cwd)
   spawn: []         ──►     spawn  effect=spawns  REFUSED
```

Tools come from three sources, resolved in this order at construction.
A name that resolves in two sources is an error.

1. Built in, eleven of them: `read`, `grep`, `edit`, `bash`, `block`,
   `spawn`, `wait`, `steer`, `notify`, `send`, and `team`.
2. Configured executables, declared in `tool_defs` with a path and a
   description. The runtime passes the model's `args` array as argv, captures
   stdout and stderr, and reports the exit code as data. A non-zero exit is a
   result rather than an error. Any program with a command line is a tool
   without modification.
3. Host tools, implemented by the process that launched foe and called over
   the [protocol](protocol.md).

Every tool returns a canonical value, which is JSON. A tool may also return a
rendered string. The log stores the canonical value. The model receives the
rendered string when present and a compact rendering of the value otherwise.
The separation is the runtime's main token lever: a search over a large tree
can record every match and show the model a count and the first twenty.

## Subagents and teams

An episode with a `spawn` grant may start child episodes. A child is a
separate process with its own log, its own grants, and a budget reserved from
its parent's remaining budget. The child's log header names the parent.

```
   root   budget: 40 calls, 400k tokens
    │
    ├── reserve 10 calls ──► child A   (spent 7, returned 3)
    │
    └── reserve 10 calls ──► child B
                              │
                              └── reserve 4 calls ──► grandchild B.1
```

Budget is a pool held by the root. Every spawn reserves from the parent's
remainder, and unspent reservation returns when the child settles. No path
through the tree can spend more than the root's total. Structural caps on
depth, lifetime episode count, and concurrency sit beside the spend caps. A
spawn that would pass any cap fails as a tool call with a result naming the
limit, and no child starts; the model reads that result like any other.

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

## Isolation

An episode runs as its own process. Children and configured executables run
as further processes. Restrictions only narrow at each spawn.

```
   host            no restriction applied by foe, ever
     │
     └─ episode    Landlock: read roots, write roots, own log dir, exec files
          │        network: open when foe holds the transport; closed when the host does
          │
          ├─ tool  Landlock: subset of the episode's; network closed
          │
          └─ child Landlock: compiled from the child's own grants, which the
                   parent's registry already verified are a subset of its own
```

On Linux with Landlock available, the runtime compiles the grants into a
ruleset. Read roots become read rules, write roots become write rules, each
configured executable becomes an execute rule on that exact file, and the
episode's own log directory becomes a write rule. When the kernel supports
it, TCP access is removed from executables, and denied accesses are captured
from the audit log and written to the episode log as `sandbox/denied` events.
A blocked attempt is therefore evidence in the record rather than a silent
error.

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
   tool/result {value, rendered}      ──►
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
foe "task" [--model PROVIDER/MODEL] [--key-file PATH]    run the built-in coding configuration
foe "task" --headless                                    run; no viewer; print the outcome
foe --config FILE --host [--log-dir DIR]                 run under a host; stdout is the log (protocol.md)
foe login [PROVIDER [--model MODEL]] [--status]          configure a provider's credential and the default model
foe view DIR [--serve [--port N]]                        write a self-contained HTML file, or serve it
foe plan --config FILE [--json]                          resolve the program, print it, its identity, and its transport
foe tools [--config FILE]                                list tools, with sources when a config is given
foe schema                                               print the JSON Schema for the configuration
```

In every running form except `--host`, standard output receives exactly one
line when the episode ends: the outcome as JSON. A shell reads it with one
`read`; another program parses it with one `json.loads`. The exit code is 0
for `completed`, 2 for `blocked`, 3 for `exhausted`, and 1 for `failed`.
Progress goes to standard error. The log goes to the file.

The log directory is `--log-dir` when given and `.foe/<episode-id>` under
the current directory otherwise. A directory that already holds a log, as
one seeded by a fork does, is continued. A `lineage.json` beside the log,
which a parent writes for a child, supplies the child's id, its parent, and
its team lead.

A task given with `--config` replaces the document's own `task`. A task
given without `--config` uses a built-in coding configuration: the tools
`read`, `grep`, `edit`, and `bash`, read and write on the current
directory, and a budget of 40 model calls. Its model is the one named by
`--model`, or the default model when `--model` is absent. The default
model is the `model` block in `~/.config/foe/default-model.json`, which
`foe login` writes. `--key-file` names the key file explicitly; without it the
provider's credential file under `~/.config/foe/credentials/` is read. The
home directory comes from the passwd database, never from the environment.

`foe login` configures one provider: it asks for the credential, proves it
with one request, writes it under `~/.config/foe/credentials/` with mode
0600, and sets the default model when none is set. [models.md](models.md)
specifies the providers and the flows.

Without `--headless` and without `--host`, the binary serves the viewer on
a loopback port chosen before the process restricts itself, opens it with
`/usr/bin/xdg-open` unless `--no-open` is given, and keeps serving for
three seconds after the episode ends so that an open page receives the
final events. `foe view DIR --serve` serves a finished directory for as
long as the process runs.

## The viewer

The viewer renders a log directory. It shows the episode tree by lineage, the
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
   crates/log ◄─────────── crates/core ◄──┬── crates/code
    serde, serde_json,       loop,        │    read grep edit bash
                             registry,    ├── crates/transport (feature)
                             grants,      │    built-in model clients
                             budget,      │
                             identity,    ├── crates/workflow
                             spawn,       │    graph scheduling, recovery, plan report
                             teams,       │
                             exec,        ├── crates/context
                             landlock,    │    projection, cut, summarization prompt
                             protocol,    │
                             workflow     └── crates/view ◄── view/ (browser bundle)
                             config,           projection, HTTP, SSE, export
                             context seam
                                                 crates/cli ◄── all of the above

   python/foe    a thin host: builds config, runs the binary, serves the protocol
   examples/     one runnable example per job, each checking its own result
```

`crates/log` depends on serde, serde_json, and thiserror, and on no crate of
this repository, and defines every event type, including
the reserved ones. `crates/core` depends on `crates/log`. Tools depend on
`crates/core` for the tool trait and capability handles. `crates/workflow`
depends on `crates/core` for the configuration types, the log, the
registry, the budget pool, and the spawner, and runs an episode whose
configuration declares a `workflow` in place of the loop. `crates/context`
depends on `crates/core` for the context policy trait and implements it:
the loop consults the policy before each request and lends it one recorded
model call. Nothing depends on `crates/view` except the binary.

## Size

The runtime is `log`, `core`, and `code`, and its Rust source stays under
6,000 lines, excluding tests and generated code. The workflow executor in
`crates/workflow` stays under 1,000 lines on the same terms, and the
compaction policy in `crates/context` under 500.

The viewer is budgeted apart from the runtime: `crates/view` under 600 lines,
and the browser bundle it serves under 150 KB compressed. It is separate
because it delivers a record of a run rather than running one, so a viewer
that grows must not force the runtime to shrink. The browser viewer's HTML,
TypeScript, and CSS count toward that compressed size and toward no line
budget at all.

Rust outside every line budget, in the built-in transport and in the
command-line binary, is bounded by the size of the binary it compiles into.
Continuous integration enforces every budget as a test.

The budget is a design constraint rather than an aspiration. A runtime that
other systems embed and audit earns trust in proportion to how little of it
there is to read.

## Status

The runtime, the binary, the viewer, and the Python package are
implemented. No interface is stable.
