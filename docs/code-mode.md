# Code mode

Status: design. Nothing in this document is implemented. The built-in tool
name `code` is reserved by this document. One additive log field is
proposed and flagged below; no event type is reserved.

## The cost of one call per turn

A model turn issues tool calls, waits, and reads every result into
context. Composition therefore has two prices. Each step of a multi-step
procedure costs a model round trip, paid in latency and in the model-call
budget. And every intermediate result enters the context whether or not
any later step needs it, paid in tokens and in attention: a fifteen-file
survey whose answer is one count still deposits fifteen file listings into
the conversation.

Episodes already route around both prices through `bash`: the model writes
a throwaway program, runs it, and reads only its output. Recorded
trajectories show the same probe scripts written repeatedly across
episodes. The composition is real, but it is invisible to the tool layer —
untyped, unnamed in the account except as shell text, and unable to call
any tool other than the shell.

Code mode gives that composition a first-class form: the model writes a
program that calls its granted tools as functions, the runtime executes it
as one tool call, and only the program's return value enters context.

## The tool

`code` is a built-in tool. Its arguments are `program`, the source text,
and an optional `note`, one line naming what the program is for.

- **Tool functions.** Every tool available to the episode is exposed to
  the program as a function of the same name. Arguments are the same JSON
  shapes the model would send; the runtime checks them against the tool's
  parameter schema at dispatch, exactly as it checks a model-issued call.
  Results return to the program as values.
- **Dispatch is shared.** An inner call goes through the same registry,
  receives the same capability handles, and is recorded the same way as a
  model-issued call. Code mode adds a caller; it does not add a path
  around anything.
- **The return value.** Whatever the program returns is the tool result,
  under the same size cap and spill rule as any other result. Nothing else
  the program computed enters the model's context.
- **Errors.** An uncaught program error is an ordinary error result
  carrying the error and the count of inner calls that completed. Inner
  effects that happened before the error remain in effect and in the
  account, as with a failed shell pipeline.
- **Bounds.** Execution is bounded by a deterministic step allowance and a
  memory cap with fixed defaults, and by the episode's remaining `seconds`
  budget. A program that exhausts any bound produces an error result
  naming the bound.
- **Subject.** The result's subject line is the note, or the program's
  first comment line, followed by the inner-call tally:
  `count layout probes · 7 calls: 4 read, 2 grep, 1 bash · returned 212 bytes`.
- **Stuck detection.** The loop detector reads inner calls and the `code`
  calls themselves like any other calls; a model resubmitting the same
  failing program accumulates toward the threshold in the ordinary way.

## The evaluator

Requirements, in order of importance:

1. **No ambient authority.** The language exposes no filesystem, network,
   clock, environment, or randomness. The tool functions are the only
   imports. This holds the runtime's no-environment-variable invariant by
   construction and keeps the kernel sandbox the only authority boundary.
2. **Deterministic.** Same program, same inner results, same value.
3. **Preemptable.** A step allowance enforced by the evaluator, so a
   non-terminating program is an error result rather than a hung episode.
4. **In-process and small.** No subprocess, no runtime download, a
   dependency the workspace can carry.

The recommended first evaluator is Starlark: hermeticity and determinism
are its design goals rather than a configuration to maintain, models write
its Python-shaped syntax fluently, and a maintained Rust implementation
exists. The deferred WebAssembly tool tier ([deferred.md](deferred.md))
remains compatible as a second backend for compiled programs; the `code`
contract above does not name a language, and the configuration will record
which evaluator a program ran under. Taking the dependency is an explicit
decision to record with the implementation.

## The log

The account must stay complete while the context stays small; the two
requirements separate cleanly.

- **The program is recorded** in the `code` call's arguments, like any
  tool arguments. Replay does not re-execute it: inner results are read
  from the log, so replay remains deterministic whatever the evaluator
  does.
- **Inner calls are recorded** as ordinary tool call and result records,
  so obligation pairing, statistics, and the viewer read them with no
  special case. Each carries one additional optional field, `within`,
  naming the enclosing `code` call. This is the design's one log-format
  change: an additive optional field on existing records, plus one
  derived-message rule — records with `within` are excluded from the
  model's messages, whose only trace of the program is the `code` call's
  own result. The log format is frozen at v1; this addition is the
  design's cost and requires that freeze to be reopened for one field.
- **The account gains precision.** Today a composed probe is one `bash`
  result holding whatever the script printed. Under code mode the same
  probe is a program plus typed inner calls with subjects, each
  attributable, each counted.

## What code mode is under the runtime's claims

The workflow chapter's guarantee — a node's context is built from its
declared predecessors and nothing else — is a property of structure at
graph scale. Code mode is the same property at call scale: the program
sees every inner result, the model sees the return value, and the log sees
everything. Bounded context and a complete account stop being a tradeoff.

Authority is untouched. A program holds exactly the granted tool handles;
composing them creates no reach the grants did not already give, which is
what makes it safe for the model to do this at any time, without a
boundary event ([lineage-identity.md](lineage-identity.md)).

## Staging

Code mode is the first of three steps, each usable without the next.

1. **Composition** — this document. No persistence: the program lives and
   dies inside one call.
2. **Definition** — a future design. A named program registered for the
   remainder of the episode and callable as a tool, its definition
   recorded in the log, moving no state because it adds no authority.
3. **Promotion** — a definition that earns permanence enters `tool_defs`
   through the self-improvement workflow as a lineage transition
   ([lineage-identity.md](lineage-identity.md)), admitted by a verifier
   already in the lineage.
