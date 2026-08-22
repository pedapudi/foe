# Workflows

A workflow is a declared graph of nodes through which information flows in
one direction per edge. The runtime enforces the graph. The model keeps its
judgment at three places the graph leaves open: inside every model node,
at every choice point the graph declares, and at every failure. This
document specifies the graph, the three places, and the guarantee that
holds across all of them.

Status: implemented. The configuration types and construction rules are in
`crates/core/src/workflow.rs`; the executor is `crates/workflow`;
`examples/workflow` runs one graph.

## Why a graph, and why agency inside it

A free agent loop carries every tool result forward in one growing context.
That is the right shape when the next step depends on judgment about
everything so far. It is the wrong shape for work whose structure is known
in advance. Three examples: a grounding phase whose fifteen tool results
are noise by the time a proposal is written; a verification step that must
never see the data it verifies against; a pipeline whose stages an auditor
must be able to check.

A graph gives four things the loop cannot.

- **A flow guarantee.** A node's context is built from its declared
  predecessors and nothing else. "The proposal node never saw the board
  data" becomes a property of the graph rather than a property of the
  prompt.
- **Bounded context.** A node's output reaches its successors and no one
  else. Intermediate results stay in the logs and out of later requests.
- **An auditable trace.** Every firing is a logged event naming the node,
  its inputs, and its output. An auditor checks the trace against the
  declared edges.
- **Governable control flow.** What may happen, and in what order, is a
  declaration.

A graph with no agency inside it is a script, and a script that calls a
model fails the first time the model's output does not fit the script's
expectation. The design therefore gives the model authority over every
choice the graph declares as a choice, and gives the graph authority over
which choices exist. The model never adds an edge, reads a node it does not
follow, invents a node, extends a budget, or skips a verification. The model
decides, within those bounds, what to do.

## The graph

```json
"workflow": {
  "nodes": {
    "manifest": { "tool": "list_mutation_points" },
    "survey":   { "tool": "grep",
                  "args": { "pattern": { "$node": "manifest", "pointer": "/top_symbol" } },
                  "follows": ["manifest"],
                  "max_fires": 3 },
    "propose":  { "model": {
                    "name": "propose",
                    "instructions": { "10-role": "Propose one experiment grounded in the survey." },
                    "tools": ["read", "grep"],
                    "grants": { "read": ["/home/user/project"] },
                    "budget": { "model_calls": 8, "max_episodes": 1 },
                    "done_when": { "returns": { "type": "object", "properties": {} } }
                  },
                  "follows": ["task", "manifest", "survey"],
                  "branches": { "accept": ["derive"], "widen": ["survey"] },
                  "max_fires": 3 },
    "derive":   { "tool": "derive_patches",
                  "args": { "experiment": { "$node": "propose" } },
                  "follows": ["propose"],
                  "terminal": true }
  },
  "recovery": { "max_interventions": 3 }
}
```

```
   task ────────────────────────┐
   manifest ──► survey ──► propose ──► derive ■
       │                     ▲  │
       └─────────────────────┘  └──── widen ───► survey
```

A `workflow` key in a configuration replaces the free loop for that
episode. The configuration's authority and budget become the workflow's
ceiling. Every node draws from that ceiling and none exceeds it.

The invocation task, the configuration's `task` string, enters the graph
through one built-in source named `task`. A node that lists `task` in its
`follows` receives the task text as a section labeled `task`, placed first
among its sections; a tool node binds it with `{ "$node": "task" }` and
receives the text as a JSON string. The source holds its value before the
first firing and is produced exactly once, so it never re-fires a node and
imposes no order: a node that follows only `task` fires at the start. A
node that follows nothing receives no task text. `task` is a reserved name;
a node named `task` is a construction error naming the rule. `foe plan`
lists the source when any node follows it.

### Structural bounds

Construction measures a workflow and every workflow node nested within it.
Four measurements bound validation, plan output, and execution.

| measurement | how it is counted | ceiling |
|---|---|---|
| nodes | every declared node, including workflow nodes and their inner nodes | 256 |
| edges | every distinct source-target pair, including an edge from `task` | 1024 |
| nested depth | zero for the episode's graph, then one for each enclosing workflow node | 8 |
| possible firings | each node's effective firing allowance, with nested allowances multiplied as specified below | 4096 |

The foe runtime supplies these ceilings. They are fixed for one runtime
build and cannot be changed by configuration. Construction rejects a graph
that exceeds a ceiling. The error names the applicable `workflow` key, the
measured count, and the ceiling.

A node's effective firing allowance is its `max_fires`, or one when
`max_fires` is absent. A node without a nested workflow contributes that
allowance to `possible_firings`. A workflow node contributes its allowance
for its own firings, plus that allowance multiplied by the inner graph's
`possible_firings`.

```
node contribution = effective max_fires * (1 + inner possible_firings)
```

The inner term is zero for a tool or model node. Summing every node
contribution gives the workflow's accepted firing allowance. This calculation
accounts for a nested graph running again whenever its containing node
re-fires.

One runtime counter draws from the accepted allowance before every firing.
Every workflow node nested within the episode shares the root graph's counter.
Firings caused by cycles, verification findings, and recovery actions draw
from the same allowance. A firing beyond the accepted allowance ends the
episode as `exhausted` with limit `workflow_firings`.
A model node can start a child episode whose program declares another
workflow. The child episode validates and enforces its own firing allowance.

`foe plan` reports every measurement, its ceiling, and `foe runtime` as the
ceiling source. It also reports the firing calculation. The JSON report
places the same data under `workflow.structure` while preserving the
elementary cycles under `workflow.cycles`.

### Nodes

A node carries thirteen keys at most, and any key outside the two tables
below is refused at construction. One of the first three names the node's
kind, and exactly one of them is present.

| kind | fires | produces |
|---|---|---|
| `tool` | one call to a named tool with bound arguments | the tool's canonical value |
| `model` | one child episode whose task is built from the node's inputs | the episode's outcome value |
| `workflow` | one nested workflow | its terminal node's value |

A `tool` node also takes `args`, specified under "Tool nodes" below. No
other key accompanies a kind.

The nine remaining fields apply to a node of any kind.

| field | type | meaning |
|---|---|---|
| `follows` | list of node names | the nodes whose outputs this node receives, and `task` for the invocation task; default empty |
| `followed_by` | list of node names | the same edges written from the other end; the union of both forms is the edge set |
| `verify` | tool name | a verifier run on the node's output; non-empty findings re-fire the node with the findings attached |
| `retries` | integer | how many times `verify` findings re-fire the node; default 2 |
| `branches` | object | a choice point; see below |
| `max_fires` | integer | how many times this node may fire in one episode; default 1 for an acyclic position, required for a node on a cycle |
| `terminal` | boolean | completing this node completes the workflow; at least one node is terminal |
| `empty` | any JSON | the value this node contributes when recovery skips it; without it, skip is not offered |
| `recovery` | object | widens what this node's recovery decision reads; its one key is `follows`, a list of further node names. See "Recovery" below |

`branches` is the one key whose own keys an author invents: each is a label
the model may choose, specified under "Choice points" below. Every other
key in this document is a fixed name.

A `follows` entry and a `followed_by` entry that name the same edge are one
edge. The graph is the union. Duplicate edges are not an error.

`foe schema` prints the JSON Schema of the whole configuration, including
`workflow`, `workflow_node`, and the recovery blocks, with
`additionalProperties` false throughout. It is the machine-readable form of
this document and of [config.md](config.md), and an editor validates and
completes against it.

### Tool nodes

`args` is the argument object for the call. A value of the form
`{ "$node": NAME }` is replaced by that node's canonical output; with
`"pointer"`, by the value at that JSON Pointer within it. NAME must be one
of the node's inputs: a name in its `follows`, or a node whose
`followed_by` names it. No other substitution exists. A tool node has no
judgment of its own; when its call fails, recovery decides. For a tool
declared in `tool_defs`, an exit code other than zero or a timeout is a
failure of the node, because no model reads the code the way the loop's
model does; the exit code and both output streams are the error recovery
sees.

### Model nodes

A model node is a full episode. Its `model` block is a child program in the
sense of [config.md](config.md#programs). Construction applies these ceiling
rules recursively.

- Every named tool appears in the workflow program's `tools`.
- A configured tool uses the same executable. Its working directory,
  network access, and timeout are no wider than the workflow definition.
- A host tool's description, instruction, parameter schema, and effect match
  the workflow definition.
- Read and write roots lie inside the workflow roots. Every spawn grant and
  its same-named descendant program appear in the workflow ceiling.
- Every budget dimension is at most the workflow program's value. An omitted
  token or time limit draws from the workflow's remaining shared allowance.
- The model and sandbox are inherited from the workflow program.

Configured-tool descriptions and instructions may differ because they
change model-visible behavior without changing process authority. A model
node may declare its own return schema and termination retries. Its verifier
must be one of its contained tools, and all retries consume its contained
budget.

The node's inputs become the child's task, one section per input, labeled
with the input's name and carrying its rendered output. The `task` section
comes first when the node follows `task`. The child runs the
ordinary agent loop with the ordinary tools, and its outcome value is the
node's output.

Inside the node the agent has everything the loop gives it: it reads, edits,
runs commands, calls subagents if granted, and decides when it is done. The
graph bounds what enters the node and what leaves it. It does not bound what
happens within.

A model node that feeds a tool node declares `done_when.returns` so that
the tool node's bindings have a shape to bind to. A model node that feeds
only other model nodes may leave the shape open; its text is its output.
The workflow executor validates a completed model node value against its
declared `done_when.returns` before recording `workflow/node-end`. A mismatch
is a recoverable node failure, and the recorded node value is null.

### Choice points

`branches` declares a choice the model makes.

```json
"branches": { "accept": ["derive"], "widen": ["survey"], "stop": [] }
```

Each key is a label; each value is the list of successors that fire when
that label is chosen. A node with `branches` must produce a value
containing a field `branch` naming one of the labels. For a model node,
`branch` is a field of the returned value and the runtime adds it to the
`returns` schema as a required enum over the labels. For a tool node, the
tool's canonical value carries it. A label with an empty successor list
ends the workflow along that path.

The runtime fires the successors under the chosen label and no others. A
successor not under any label is an ordinary edge and fires regardless.
The labels are hashed into identity. The choice is logged as
`workflow/branch`.

This is where control-flow agency lives. The agent decides whether to
widen the survey or accept its proposal. The graph decides that those are
the two things it may decide.

### Firing

A node fires when every one of its inputs has produced a value and at
least one edge into it has carried a fresh value since this node last
fired. A node with no edge into it, or with only the edge from `task`,
fires once, at the start. A branch edge counts as an edge into its target,
so a node that any `branches` label lists is never a graph source, even
when nothing else points at it. An edge from a
node without `branches` is fresh after every firing of its source; an edge
from a node with `branches` is fresh only when the chosen label lists the
target, or when no label lists it. A node waits while any ancestor of it
is running or is itself about to fire, so that a node with two inputs fed
by one re-fired ancestor fires once, with both inputs fresh. For an
acyclic graph this is topological order, and nodes with no pending
dependency between them fire concurrently. For a graph with a cycle, it is
dataflow: a node on the cycle fires again when an input is fresh again.

Cycles are permitted. What bounds them is `max_fires` on every node that
lies on a cycle, which the runtime requires at construction. The aggregate
firing allowance bounds node execution across nested workflows in the same
episode. The episode budget bounds model requests, tokens, child episodes,
and time. `foe plan` reports every cycle and the bound that closes it. A
node that would fire beyond its `max_fires` ends the episode as `blocked`
with `recovery-exhausted`.

A cycle needs a node outside it to start it. Because a branch edge makes
its target a successor, a label that points back at the graph's only
source leaves that source with a predecessor and no node ready at the
start. Such a graph passes construction and `foe plan`, and the episode
ends immediately as `failed` with `the workflow stalled: no node is ready
and no terminal node completed`. Place the loop's entry in a node that no
branch label names, and let that node feed the cycle.

Firing a node a second time re-fires every node downstream of it, because
their inputs became fresh. Recovery uses this. Model nodes share the
episode's whole-tree `budget.max_concurrent` leases with every other child
episode. A ready model node waits while no slot remains. Tool nodes whose
effects are `pure` or `reads` may run concurrently. Tool nodes whose effects
are `writes`, `execs`, or `spawns` run one at a time in node-start order
across the workflow and every nested workflow.

### Completion

The workflow completes when a terminal node completes, or when a chosen
branch label has no successors. The episode's `done_when`, when present,
verifies the terminal value: a `returns` schema it must conform to, a
`verify` tool that must report no findings, or both. Findings re-fire the
terminal node's nearest model ancestor with the findings attached, up to
`done_when.retries` times; findings that remain go to recovery at the
terminal node. Firings still running when the workflow completes receive
ten seconds to finish. The executor then cancels them and records a
`workflow/node-end` with a null value and a cancellation error before
`episode/end`. The same bound applies when the workflow ends as blocked,
exhausted, or failed. When the graph has no terminal node and no empty-branch
path, the episode runs until a budget or firing bound is spent. This graph
shape can implement a supervisor loop and is reported by `foe plan`. A
graph whose nodes have all fired without completing, with nothing left to
fire, ends as `failed` with a message saying so.

A nested `workflow` node runs its graph inside the episode, over the same
log, with each inner node named by its path, `outer/inner`. The outer
node's inputs gate its firing; the inner graph's source nodes start from
nothing. The inner graph's completing value is the node's value; an inner
episode-level `done_when` does not apply.

## The flow guarantee, stated exactly

A model node's child episode receives, and only receives: its own
instructions, the tools and grants its `model` block declares, the text the
runtime contributes for every episode, the rendered outputs of the nodes in
its `follows` list, and the invocation task when `task` is among them. The
child's log records all of it as its task
and its inbox. A reader who wants to prove that node C never received node
A's output checks two things: that A is not in C's `follows`, and that no
path of `follows` edges reaches C from A through a node that forwards A's
content. The first is a construction-time fact; the second is the trace.

The guarantee covers model context. It does not by itself cover the
filesystem. Two nodes with write access to the same directory can
communicate through it. A workflow that needs isolation between nodes grants
model nodes disjoint write roots. `foe plan` lists every pair of nodes whose
write roots overlap. A `writes`, `execs`, or `spawns` tool node uses the
enclosing workflow program's write roots for this conservative report.

## Recovery

Every node has recovery. Nothing declares it. It fires when a node cannot
proceed, and it is the second place agency lives.

### When it fires

| condition | recovery fires |
|---|---|
| the node's tool call errored, timed out, or produced output that violated the tool's own schema | yes, except for settled failures |
| the node's `verify` findings remain after `retries` | yes |
| a model node ended `blocked` | yes, with the code |
| a model node ended `failed` | yes |
| the workflow's `done_when` findings remain | yes, at the terminal node |
| a tool node's bound argument is absent from its predecessor's value | yes |
| the executable does not exist, the path is denied, the budget is spent, or a child exceeded a structural cap | no; the episode ends with the matching outcome |

The last row names settled failures: a second attempt cannot change them,
so a model call to decide what to do about them would spend budget to learn
nothing.

### What it sees

A recovery decision is a model call whose context is built the same way as
any node's: from declared inputs only. Its inputs are the failed node's
inputs, the failed node's output or error, the findings when there are
any, and the names of the nodes it may act on. It does not see the outputs
of nodes the failed node does not follow.

```json
"propose": { "model": {}, "recovery": { "follows": ["manifest", "survey", "prior_attempts"] } }
```

A node may widen what its recovery decision sees by declaring a `recovery`
block with its own `follows`. The widening is a declaration in the graph,
so an audit sees that the reach was granted.

The instruction that frames the failure and the permitted actions belongs
to the runtime. An author does not write it and cannot change it. It is
hashed into identity, so a runtime upgrade that rewords it changes every
workflow's identity, which is what a reworded instruction should do.

### What it may do

The action set is closed. The decision names one action and the runtime
performs it.

| action | effect | permitted when |
|---|---|---|
| `retry(node)` | re-fire `node` and everything downstream of it | `node` is the failed node or an ancestor of it |
| `amend(node, note)` | re-fire `node` with `note` appended to its inputs as a section labeled `recovery` | same |
| `skip` | the failed node contributes its `empty` value and its successors fire | the node declares `empty` |
| `abort(code, message)` | end the episode as `blocked` with that code | always |

`retry` and `amend` reach only ancestors because reaching elsewhere would
create a flow the graph never declared. `amend` is how a recovery decision
passes judgment downstream: the note is visible to the re-fired node and to
the trace.

### What bounds it

- `recovery.max_interventions`, default 3, caps recovery actions per
  episode.
- `max_fires` caps every node, including re-fires that recovery causes
  and re-fires that `verify` findings cause. A node that may be re-fired
  declares a `max_fires` that admits the re-fires; a node at its bound is
  not offered to `retry` or `amend`.
- The accepted firing allowance caps aggregate firings across every nested
  workflow in the episode.
- The episode budget caps model requests, tokens, child episodes, and time.

Reaching `recovery.max_interventions` or scheduling a node beyond its
`max_fires` ends the episode as `blocked` with `recovery-exhausted`.
Exceeding the accepted firing allowance ends it as `exhausted` with
`workflow_firings`. Spending the episode budget ends it as `exhausted`
with the applicable budget limit. A recovery decision that itself fails
ends the episode with `recovery-failed`: a request that errors, a response
with no call to `recover`, or a call naming an action or a node that was
not offered. Recovery never recurses: a failure inside a recovery decision
is terminal.

A recovery decision is one model request in the episode's own log. Its
context is an `inbox/item` with source `system`; its `model/request`
carries that one message and nothing from earlier decisions; its answer is
an `assistant/message` and a `tool/result` for the `recover` call. The
applied action is the `workflow/recovery` event. A `skip` records the
`empty` value as the node's output: successors name the recovery event as
their input, and when the empty value carries a `branch` field the label it
names is the one chosen.

`"recovery": { "enabled": false }` at the workflow level disables it; a
node failure then ends the episode with the node's outcome.

## Agency, summarized

Three places, one rule.

| place | the model decides | the graph decides |
|---|---|---|
| inside a model node | everything the agent loop allows | what enters, what leaves, and the node's grants and budget |
| at a choice point | which label | which labels exist and where each leads |
| at a failure | which action | which actions exist and which nodes they may reach |

Enforcement of the declared flow never depends on the model. That is the
requirement that motivated the graph, and it holds at every one of the
three places because each one offers a closed set and validates the choice
against it.

## Log events

A workflow episode's own log carries these; each model node's firing is a
child episode under `children/` with its own log, and
`workflow/node-start` names the child. [log-format.md](log-format.md)
gives each event's fields.

| event | data |
|---|---|
| `workflow/node-start` | `{ node, fire, inputs: [seq of the producing node-end events], child_id? }` |
| `workflow/node-end` | `{ node, fire, value, rendered, error?, duration_ms }` |
| `workflow/branch` | `{ node, fire, label, successors }` |
| `workflow/recovery` | `{ node, fire, cause, action, target?, note?, intervention }` |

A model node's firing also produces the ordinary `spawn/start`,
`budget/reserve`, `spawn/end`, and `budget/release` events, because it is
a child episode and nothing else.

## Identity

The following participate in identity: every node's name and kind, the
edge set, every `branches` declaration, every tool node's `args` with
bindings, every model node's program identity, `verify`, `retries`,
`max_fires`, `terminal`, `empty`, every `recovery.follows` widening,
`recovery.max_interventions`, the structural ceilings, the possible-firing
calculation, and the runtime's recovery instruction.

## Relationship to the rest of foe

A workflow episode is an episode. It has one log, one budget pool, one
outcome, and one identity. Its model nodes are child episodes and obey
every rule of [subagents](design.md#subagents-and-teams). Its tool nodes
dispatch through the ordinary registry with the ordinary effect checks and
effect-based serialization.
The parent reserves descendant capacity for a workflow-bearing child program
even when the program has no explicit spawn grant. This rule applies through
every level of nested workflows.
The viewer renders the graph with each firing linked to its child log, so
a reader moves from the graph to the conversation that produced a value in
one step.

An agentic episode may invoke a workflow as a tool, and a workflow node may
be an agentic episode. An author picks the shape per phase: a graph where
flow must be guaranteed, a loop where judgment must be free.

## Deferred within this specification

Fan-out over a list (`map`), a node that fires on a timer, and a
`workflow` node that references a workflow in another file. Each has an
obvious place in the grammar and none is needed by the first consumer.
