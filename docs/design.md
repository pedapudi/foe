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
| [sdk.md](sdk.md) | the Python package |
| [tools.md](tools.md) | built-in tools, configured executables, and host tools |
| [sandbox.md](sandbox.md) | how grants compile into kernel restrictions |
| [viewer.md](viewer.md) | the trajectory viewer |
| [landscape.md](landscape.md) | the surrounding field of agent runtimes |
| [deferred.md](deferred.md) | features with reserved event types and no implementation |

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
  │    budget spent?  ──► Exhausted                                 │
  │    looping?       ──► Blocked                                   │
  │    done_when met? ──► Completed                                 │
  │    else           ──► next step                                 │
  └─────────────────────────────────────────────────────────────────┘
```

Three rules hold in every step. Each exists because its absence loses data.

- A response that ended because it hit the provider's output length limit has
  every tool call rejected. Streamed tool-call arguments are recovered by a
  tolerant parser, so a truncated call can parse and validate while missing
  its tail. The model receives one error per call and reissues them.
- Every tool call in the log has a result. When an episode is interrupted
  after a call and before its result, the log is completed during seeding with
  a synthetic error result for that call. A log that seeds a fork is therefore
  always well-formed.
- Tool calls are preflighted one at a time in the order the model issued
  them. Calls whose declared effect is `pure` or `reads` run concurrently.
  Calls that write, execute, or spawn run one at a time in issue order. Results
  are appended in issue order regardless of completion order.

### Termination

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
  params          JSON Schema for the arguments
  effect          pure | reads | writes | execs | spawns
}
```

The effect is the tool's declared interaction with the world. The registry
refuses a tool whose effect the grants do not cover, at program construction.
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

1. Built in: `read`, `grep`, `edit`, `bash`, `block`, and the spawn and team
   tools.
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

An episode with a `spawns` grant may start child episodes. A child is a
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
depth, lifetime episode count, and concurrency sit beside the spend caps.
Reaching any cap yields `Exhausted` on the child, which the parent receives as
an ordinary result.

Communication is an inbox append with a typed source. A parent steers a
running child by appending to the child's inbox. A child notifies its parent
the same way. A steer arrives in the child's next request; nothing
interrupts a request in flight.

A team is a set of episodes that share a lead. The lead's log holds the
roster and the queue of messages between members.

```
   lead log                                   member log
   ────────                                   ──────────
   team/roster    {member, name, phase}
   team/message   {id, from, to, content}  ──►  inbox/item {source: peer, message_id}
   team/delivered {id, to}                 ◄──  (written after the member's append)
```

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
three kinds of request on foe's standard input.

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

The binary has one running form and four forms that run nothing.

```
foe "task" [--config FILE] [--no-open]      run; serve the viewer; print the outcome
foe "task" --headless                       run; no viewer; print the outcome
foe --config FILE --host                    run under a host; stdout is the log (protocol.md)
foe view DIR [--serve]                      write a self-contained HTML file, or serve it
foe plan --config FILE [--json]             resolve the program, print it and its identity
foe tools [--config FILE]                   list tools, with sources when a config is given
foe schema                                  print the JSON Schema for the configuration
```

In every running form except `--host`, standard output receives exactly one
line when the episode ends: the outcome as JSON. A shell reads it with one
`read`; another program parses it with one `json.loads`. The exit code is 0
for `completed`, 2 for `blocked`, 3 for `exhausted`, and 1 for `failed`.
Progress goes to standard error. The log goes to the file.

A task given on the command line without `--config` uses a built-in coding
configuration: the four built-in tools, read and write on the current
directory, and a model from a file named on the command line with `--model`.

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
    serde only               loop,        │    read grep edit bash
                             registry,    ├── crates/transport (feature)
                             grants,      │    built-in model clients
                             budget,      │
                             identity,    └── crates/view ◄── view/ (browser bundle)
                             spawn,            projection, HTTP, SSE, export
                             teams,
                             exec,
                             landlock,           crates/cli ◄── all of the above
                             protocol

   python/foe    a thin host: builds config, runs the binary, serves the protocol
   examples/     runnable configurations, one mechanism each
```

`crates/log` depends on serde alone and defines every event type, including
the reserved ones. `crates/core` depends on `crates/log`. Tools depend on
`crates/core` for the tool trait and capability handles. Nothing depends on
`crates/view` except the binary.

## Size

The Rust source across `log`, `core`, `code`, and `view` stays under 6,000
lines, excluding tests and generated code. The browser bundle stays under
150 KB compressed. The built-in transport and the binary are budgeted
separately. Continuous integration enforces all three as tests.

The budget is a design constraint rather than an aspiration. A runtime that
other systems embed and audit earns trust in proportion to how little of it
there is to read.

## Status

Design complete. Implementation in progress. No interface is stable.
