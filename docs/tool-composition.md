# Tool composition

The built-in `compose_tools` tool runs model-written Python source in an isolated
interpreter process whose only capability is calling this episode's tools.
The runtime records every inner call in the log and shows the model only
the value the source returned. `crates/code/src/python.rs` implements the
tool; `crates/core` implements inner dispatch and recording;
[log-format.md](log-format.md) specifies the `tool/inner-call` event.

## Purpose

Each ordinary tool turn places every result in the model's conversation. A
multi-step survey therefore spends a model request between decisions and
replays each intermediate result in later requests.

The `bash` tool can avoid some of that cost. A shell pipeline performs
several operations and emits a narrow result. The built-in coding workflow
can start `/usr/bin/python3` through `bash` for workspace scripting. Those
processes receive the permissions granted to `bash`.

The `compose_tools` tool lets the model submit one bounded source that calls the
episode's tools as functions. The runtime records every inner call and
exposes only the source's returned value as the outer tool result.

Use `compose_tools` when later calls depend on earlier results or when several
large results can be reduced before returning to the model. Independent small
calls stay at the top level, where the runtime can execute read-only calls
concurrently. The composition interpreter receives no workspace access.

The model's source remains in the conversation as the `compose_tools`
call's arguments. The tool removes inner results from the conversation. It
does not remove the source that produced them.

Execution contracts opt in by listing `compose_tools` in `tools`. An episode that omits it
pays no request-schema or instruction cost. The built-in coding workflow
does not list it; [the adoption evidence below](#adoption-evidence) states
what would change that.

## Outer call schema

The `compose_tools` tool has two arguments:

```json
{ "source": "def main():\n    ...", "timeout_seconds": 60 }
```

The source defines a zero-argument `main` function returning a
JSON-serializable value. `timeout_seconds` defaults to 120, the `bash`
default, and is reduced to the episode's remaining wall-clock budget when
that is smaller.

The runtime starts `/usr/bin/python3 -I -`. Isolated mode ignores every
`PYTHON*` environment variable and the user site directory, and the
environment is empty besides. The process receives on standard input a
foe-owned shim followed by the source, so a syntax error anywhere produces
an outer error result before any statement runs. The shim exposes exactly
two functions and holds inner dispatch closed until it invokes `main`, so
top-level statements in the source cannot call tools while the source
loads.

- `call_tool(name, args)` performs one inner dispatch and returns
  `{"value": ..., "is_error": bool}`. `name` is an ordinary episode tool
  name, which keeps configured tools callable when their names are not
  valid identifiers. `args` is the same JSON object the model would send
  in a top-level call.
- `fail(message)` ends the outer call as an error carrying the message.

Three control tools are excluded from inner dispatch, refused with an
error result before any event is written:

- `compose_tools`, which prevents interpreter recursion;
- `block`, whose meaning depends on a model-issued top-level call;
- the synthesized `return` tool, whose meaning depends on the episode's
  completion rule.

Calling the configured completion verifier inside the source performs an
ordinary inner tool call. It does not signal episode completion. The model
must call that verifier at the top level when it wants the call to act as
a completion candidate.

The tool has no result schema, so the source treats every `value` as a
dynamic JSON value. This source counts matches without returning
every match line:

```python
def main():
    result = call_tool("grep", {"pattern": "TODO", "path": ".", "limit": 100})
    if result["is_error"]:
        fail(result["value"]["error"])
    return {"matches": result["value"]["matches"], "files": result["value"]["files"]}
```

The outer canonical result contains the returned value, a derivation
summary, and a bounded capture of the process's own standard output and
standard error as diagnostics:

```json
{
  "returned": { "matches": 17 },
  "derivation": { "complete": true, "inner_calls": 2, "errors": 0, "by_tool": { "grep": 2 } },
  "stdout": "",
  "stderr": ""
}
```

The rendered result opens with the call and error counts, shows the
returned value, and appends each non-empty diagnostic stream. Its subject
states the call count, error count, and returned byte count. The subject
contains no text supplied by the model. The ordinary result budget and
spill rules apply.

A call to `fail`, an uncaught exception, an exhausted bound, or an
interpreter that exits before `main` returns marks the outer result as an
error. The canonical value is then
`{"error": {"message", "derivation", "stdout", "stderr"}}`: the derivation
still reports every inner call that completed, with `complete` false, and
an uncaught exception's message carries the traceback. A missing
interpreter is an ordinary error result naming the expected path.

## Confinement

The interpreter runs through the ordinary executor with a policy of its
own in place of the per-executable narrowing: read on `/usr` alone — the
interpreter's installation prefix, world-readable system files — execute
on the interpreter, write on nothing, and no network. No workspace root,
home directory, or credential file is granted. The sandbox's baseline
loader, system, and device paths apply as they do to every process.
Landlock enforces the policy where the kernel offers it; as everywhere in
[sandbox.md](sandbox.md), `best-effort` mode applies what the kernel
offers and applies nothing when Landlock is absent. The source's one door
to the world is the dispatch socket the shim holds on file descriptor 3.

Five bounds hold, each a constant in `crates/code` beside the other tool
bounds, and the tool description states their values:

- **Source size**: 64 KiB, checked before the interpreter starts.
- **Memory**: RLIMIT_AS at 512 MiB, set by the shim's first statement as
  both the soft and the hard limit, which a process without privilege
  cannot raise. The shim rather than the spawner sets it because the
  runtime forbids unsafe code and therefore installs no between-fork-and-
  exec hook.
- **Inner calls**: 100 per source. The call past the bound is not
  dispatched; the outer call ends as an error naming the bound.
- **Timeout**: `timeout_seconds`, bounded by the episode's remaining
  `seconds` budget. Every inner tool retains its own timeout and output
  limit.
- **Cancellation**: the executor owns the process group and kills the
  whole group on timeout or cancellation, so nothing the source started
  survives the call. Episode settlement closes an inner call left open
  before the outer call, as [log-format.md](log-format.md#seeding)
  specifies.

## Dispatch and permissions

The model sees `compose_tools` as a built-in tool. The runtime handles it as a
composite call because an ordinary tool receives capability handles for
one declared effect, while one source can invoke tools with several
effects.

The tool holds no direct filesystem API and dispatches nothing itself. The
agent loop builds a composer for the one call it recognizes by the tool's
name, and for each inner call the composer asks the ordinary registry to
resolve the name, validate the arguments against the tool's parameter
schema, select capability handles, and dispatch the implementation — the
same path a model-issued call takes. The registry remains the only path
from source code to an effect. A dispatch outside the agent loop, such as
a workflow node's direct tool call, carries no composer, and the tool then
returns an error.

Every inner call is synchronous and runs in source order. The outer
`compose_tools` call declares the `execs` effect and therefore runs exclusively
with respect to other top-level calls in the same model turn. These rules
preserve the existing effect order even when the source selects tool
names or arguments dynamically.

The tool is not transactional. Effects completed before a later error
remain in the world and the log. The outer error result reports how many
inner calls completed, which gives the model enough information to inspect
the partial state.

Stuck detection compares model-issued outer calls and results. Inner calls
never enter it, because looping detection matches results to the calls an
`assistant/message` issued. A model that submits the same failing source
in consecutive turns still reaches the ordinary looping threshold.

## Log representation

The model-issued `compose_tools` call remains in its `assistant/message`. Before
each inner dispatch the runtime appends one `tool/inner-call` event, and
the inner call's ordinary `tool/result` follows; the generic event name is
shared by design with any future composing tool.
[log-format.md](log-format.md) specifies the event, the obligation it
opens, its exclusion from derived messages, and the nested closing order
at settlement and seeding. The event is implemented in log format version 3.

Replay reads the outer result from the log and never runs the source.
Statistics that add durations must treat the outer duration as inclusive
of the inner ones.

## Contract fingerprint and promotion

The `compose_tools` specification, its bounds, and the runtime build
participate in the execution contract fingerprint, as every built-in tool's
specification does. The Python source is a tool argument and therefore
belongs to an episode log rather than the contract fingerprint.

Registering a new named tool during an episode would change the request
header and model-visible vocabulary, violating the rule that an episode
uses one immutable execution contract. Episode-local tool definition is
outside this design. Useful Python source can become a configured tool in a
later execution contract, packaged behind a `tool_defs` executable with a
declared schema and verifier;
[evidence.md](evidence.md) can record the transition and
its admission evidence.

## Adoption evidence

The tool stays out of the built-in coding workflow until five forms of
evidence exist.

1. **Runtime conformance.** Tests cover argument validation, capability
   selection, effect ordering, partial effects, cancellation, nested
   obligation repair, and replay. The runtime test suite carries these.
2. **Interpreter confinement.** Tests attempt filesystem reads outside the
   policy, environment access, memory exhaustion, and non-termination. The
   runtime test suite carries these; network and clock isolation follow
   from the policy and are exercised by the sandbox tests.
3. **Quality on tasks that exercise composition.** A forced capability control
   proves that one source can complete a dependent call chain and return a
   smaller derived value. A mixed workload then measures natural selection and
   task quality without naming a mechanism in its task prompts.
4. **Mixed-workload cost.** Tasks that benefit and tasks that do not run
   together. Reports separate the fixed request cost of the `compose_tools`
   schema, source replay, inner canonical bytes, suppressed rendered
   bytes, model calls, latency, and estimated cost.
5. **Simpler alternative.** The same tasks run with instructions that tell
   the model to end shell pipelines with a narrowing operation. The tool
   earns default adoption only when it adds quality or efficiency beyond
   that instruction.

Any task-quality regression blocks adoption for the affected contract.
Token, latency, and cost measurements rank quality-equivalent
configurations; they do not compensate for a lower task score.

## The Starlark alternative

An earlier form of this design evaluated model-written sources in an embedded Starlark
interpreter rather than a subprocess; `spikes/starlark-confinement`
demonstrates its fuel accounting, memory accounting, cancellation, and
closed module loader. The subprocess form ships because it adds no
evaluator dependency to the runtime and confines with the same kernel
mechanism as every other process. The spike becomes relevant again if a
deployment needs this composition where no interpreter binary or Landlock
is available, or needs deterministic step accounting that a wall-clock
timeout cannot give.
