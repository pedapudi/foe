# Code mode

Status: design. The built-in tool name `code` and the additive log event
`code/inner-call` are proposed here. The registry does not expose the tool,
and the log crate does not define the event.

## Purpose

Each ordinary tool turn places every result in the model's conversation. A
multi-step survey therefore spends a model request between decisions and
replays each intermediate result in later requests.

The `bash` tool can avoid some of that cost. A shell pipeline performs
several operations and emits a narrow result. Its inner operations remain
shell text, and it can use only the process authority granted to `bash`.

Code mode lets the model submit one bounded program that calls the episode's
tools as functions. The runtime records every inner call and exposes only the
program's returned value as the outer tool result.

The model's source program remains in the conversation as the `code` call's
arguments. Code mode removes inner results from the conversation. It does
not remove the source that produced them.

Programs opt in by listing `code` in `tools`. An episode that omits it pays
no request-schema or instruction cost.

## Outer call contract

The `code` tool has one argument:

```json
{ "program": "def main():\n    ..." }
```

The source defines a zero-argument `main` function. Inner dispatch is
unavailable while the evaluator loads the source. The runtime enables it only
while invoking `main`. A parse or load error therefore produces an outer
error result before any effect occurs.

The source uses a constrained Starlark dialect. The evaluator selection gate
below must pass before the runtime accepts this syntax.

The evaluator exposes one function for inner dispatch:

```text
call_tool(name, args)
```

`name` is an ordinary episode tool name. `args` is the same JSON object that
the model would send in a top-level call. A string name keeps configured
tools callable when their names are not valid language identifiers. Three
control tools are excluded:

- `code`, which prevents evaluator recursion;
- `block`, whose meaning depends on a model-issued top-level call;
- the synthesized `return` tool, whose meaning depends on the episode's
  completion contract.

Calling the configured completion verifier inside a program performs an
ordinary inner tool call. It does not signal episode completion. The model
must call that verifier at the top level when it wants the call to act as a
completion candidate.

`call_tool` returns a value with two fields:

```text
struct(value = <canonical JSON value>, is_error = <boolean>)
```

The program can inspect an error and continue, or call the evaluator's
`fail(message)` function to end the outer call with an error. Tool arguments pass
through the existing JSON Schema check before the tool receives a capability
handle.

The current tool contract has no result schema. Code mode therefore treats
canonical results as dynamic JSON values and makes no static type claim. A
future result-schema design can add validation without changing the outer
call contract.

This program counts matches without returning every match line:

```python
def main():
    result = call_tool("grep", {
        "pattern": "TODO",
        "path": ".",
        "limit": 100,
    })
    if result.is_error:
        fail(result.value["error"])
    return {
        "matches": result.value["matches"],
        "files": result.value["files"],
        "complete": result.value["complete"],
    }
```

`main` must return a JSON value. The outer canonical result contains that
value and a derivation summary:

```json
{
  "returned": { "matches": 17 },
  "derivation": {
    "complete": true,
    "inner_calls": 6,
    "errors": 0,
    "by_tool": { "grep": 2, "read": 4 }
  }
}
```

The rendered result shows the returned value and the derivation summary. It
uses the ordinary result budget and spill rules. Its subject states the call
count, error count, and returned byte count. The subject contains no text
supplied solely for display by the model.

An evaluator error or a call to `fail` sets `complete` to false, adds an
`error` string, and marks the outer tool result as an error. The derivation
still reports every inner call that completed before the error.

## Evaluation environment

The evaluator supplies a closed set of language features. It provides JSON
values, local variables, functions, conditionals, bounded loops, collection
operations, sorting, `call_tool`, and `fail`. It provides no filesystem,
process, network, environment, clock, randomness, module loading, or dynamic
code loading.

Evaluation is deterministic given the source and the sequence of inner tool
results. An inner tool can observe changing external state. The log records
that observed result, so replay does not repeat the observation.

The evaluator enforces four independent bounds:

- source bytes;
- evaluator steps;
- live evaluator memory;
- inner tool calls.

The episode's remaining `seconds` budget also bounds the outer call. Every
inner tool retains its own timeout and output limit. Exhausting any evaluator
bound returns an error that names the bound and reports the completed inner
call count.

The implementation must choose fixed defaults with generous headroom over
successful tasks that exercise several inner calls. The tool description states the values, so a
change to them changes program identity through the ordinary tool
specification and runtime build.

The evaluator spike must demonstrate fuel accounting, memory accounting,
cancellation, a disabled module loader, and the absence of ambient imports.
The runtime must not take an evaluator dependency before those properties are
tested.

## Dispatch and authority

The model sees `code` as a built-in tool. The runtime handles it as a
composite call because an ordinary tool receives capability handles for one
declared effect, while a code program can invoke tools with several effects.

The composite executor holds no direct filesystem or process API. For each
inner call, it asks the ordinary registry to resolve the name, validate the
arguments, select capability handles, and dispatch the implementation. The
registry remains the only path from source code to an effect.

Every inner call is synchronous and runs in source order. The outer `code`
call runs exclusively with respect to other top-level calls in the same
model turn. These rules preserve the existing effect order even when the
source selects tool names or arguments dynamically.

Code mode is not transactional. Effects completed before a later error
remain in the world and the log. The outer error result reports how many
inner calls completed, which gives the model enough information to inspect
the partial state.

Cancellation prevents further evaluator steps and begins ordinary tool
teardown for the current inner call. The implementation must prove that no
process started by that call survives teardown. The runtime closes the inner
call before it closes the outer call.

Stuck detection compares model-issued outer calls and results. It does not
treat repeated inner calls inside one program as consecutive model turns. A
model that submits the same failing source in consecutive turns still
reaches the ordinary looping threshold.

## Log representation

The model-issued `code` call remains in its `assistant/message`. Its ordinary
`tool/result` closes the outer call and is the only result from the program
that enters derived messages.

Before each inner dispatch, the runtime appends one additive event:

```json
{
  "type": "code/inner-call",
  "data": {
    "outer_call_id": "tc_code_1",
    "call_id": "tc_code_1_4",
    "index": 4,
    "name": "read",
    "args": { "path": "src/parser.rs" }
  }
}
```

The inner call produces an ordinary `tool/result` with the inner `call_id`.
A host tool also produces the existing `host/tool-call` event while it waits
for its host result. The new event records the nesting relationship without
changing any frozen version 2 event payload.

`code/inner-call` opens the same tool-call obligation that a call in an
`assistant/message` opens. Its ordinary `tool/result` closes that obligation.
The enclosing code call remains open until every inner call has closed.

The derived-message fold excludes a `tool/result` whose opening record was a
`code/inner-call`. Every other `tool/result` retains its existing behavior.
The full `model/request.messages` snapshot remains the final reconstruction
check.

Teardown and seeding close an open inner call before synthesizing the outer
code result. This nested closing order is required even though ordinary
sibling obligations retain their opening order.

Replay reads the outer result from the log and never evaluates the source.
The viewer nests inner calls below the outer code call. Statistics report
the outer duration as inclusive and each inner duration separately, so a
consumer can avoid adding both into one total.

The event uses the version 2 envelope and adds no field to a frozen payload.
A reader compiled before the new variant exists will reject the event. The
log crate must therefore define the variant before any runtime emits it.

## Program identity and promotion

The `code` tool specification, evaluator instructions, bounds, and runtime
build participate in the existing program identity. One source program is a
tool argument and therefore belongs to an episode log rather than the
program identity.

Registering a new named tool during an episode would change the request
header and model-visible vocabulary. That operation would violate the rule
that an episode uses one immutable program state. Episode-local tool
definition is outside this design.

A useful source program can become a configured tool in a later program
state. Promotion can package the source behind a configured executable, with
a declared schema and verifier. Those fields then participate in the child
state's identity. [Program lineage](lineage-identity.md) can record the
transition and its admission evidence.

## Evaluation requirements

Code mode should remain opt-in until five forms of evidence are available.

1. **Runtime conformance.** Tests cover argument validation, capability
   selection, effect ordering, host tools, partial effects, cancellation,
   nested obligation repair, replay, and execution with the filesystem
   removed.
2. **Evaluator confinement.** Tests attempt filesystem, process, network,
   clock, randomness, module loading, memory exhaustion, and non-termination.
3. **Quality on tasks that exercise code mode.** A development set contains tasks where programs
   issue several inner calls and return a smaller derived value. A holdout
   set measures whether the mechanism preserves or improves task score.
4. **Mixed-workload cost.** Tasks that benefit and tasks that do not benefit
   run together. Reports separate the fixed request cost of the `code` schema,
   source replay, inner canonical bytes, suppressed rendered bytes, model
   calls, latency, and estimated cost.
5. **Simpler alternative.** The same activation tasks run with instructions
   that tell the model to end shell pipelines with a narrowing operation.
   Code mode earns adoption only when it adds quality or efficiency beyond
   that instruction.

Any task-quality regression blocks adoption for the affected program. Token,
latency, and cost measurements rank quality-equivalent configurations. They
do not compensate for a lower task score.
