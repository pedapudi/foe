# Workflows

A workflow is a declared graph of nodes through which information flows in
one direction per edge. The runtime enforces the graph. The model keeps its
judgment at three places the graph leaves open: inside every model node,
at every choice point the graph declares, and at every failure. This
document specifies the graph, the three places, and the guarantee that
holds across all of them.

Status: specified, with log event types reserved. Not implemented.
Tracked as foe issues #1 and #2.

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
                    "budget": { "model_calls": 8 },
                    "done_when": { "returns": { "type": "object", "properties": {} } }
                  },
                  "follows": ["manifest", "survey"],
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
   manifest ──► survey ──► propose ──► derive ■
       │                     ▲  │
       └─────────────────────┘  └──── widen ───► survey
```

A `workflow` key in a configuration replaces the free loop for that
episode. The configuration's `tools`, `grants`, `budget`, and `done_when`
become the workflow's ceiling: every node draws from them and none exceeds
them.

### Nodes

A node is one of three kinds.

| kind | fires | produces |
|---|---|---|
| `tool` | one call to a named tool with bound arguments | the tool's canonical value |
| `model` | one child episode whose task is built from the node's inputs | the episode's outcome value |
| `workflow` | one nested workflow | its terminal node's value |

Every node has these fields.

| field | type | meaning |
|---|---|---|
| `follows` | list of node names | the nodes whose outputs this node receives; default empty |
| `followed_by` | list of node names | the same edges written from the other end; the union of both forms is the edge set |
| `verify` | tool name | a verifier run on the node's output; non-empty findings re-fire the node with the findings attached |
| `retries` | integer | how many times `verify` findings re-fire the node; default 2 |
| `branches` | object | a choice point; see below |
| `max_fires` | integer | how many times this node may fire in one episode; default 1 for an acyclic position, required for a node on a cycle |
| `terminal` | boolean | completing this node completes the workflow; at least one node is terminal |
| `empty` | any JSON | the value this node contributes when recovery skips it; without it, skip is not offered |

A `follows` entry and a `followed_by` entry that name the same edge are one
edge. The graph is the union. Duplicate edges are not an error.

### Tool nodes

`args` is the argument object for the call. A value of the form
`{ "$node": NAME }` is replaced by that node's canonical output; with
`"pointer"`, by the value at that JSON Pointer within it. NAME must be one
of the node's inputs: a name in its `follows`, or a node whose
`followed_by` names it. No other substitution exists. A tool node has no
judgment of its own; when its call fails, recovery decides.

### Model nodes

A model node is a full episode. Its `model` block is a child program in the
sense of [config.md](config.md#programs): instructions, tools, grants,
budget, and termination, each a subset of the workflow's. The node's inputs
become the child's task, one section per predecessor, labeled with the
predecessor's name and carrying its rendered output. The child runs the
ordinary agent loop with the ordinary tools, and its outcome value is the
node's output.

Inside the node the agent has everything the loop gives it: it reads, edits,
runs commands, calls subagents if granted, and decides when it is done. The
graph bounds what enters the node and what leaves it. It does not bound what
happens within.

A model node that feeds a tool node declares `done_when.returns` so that
the tool node's bindings have a shape to bind to. A model node that feeds
only other model nodes may leave the shape open; its text is its output.

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

A node fires when every node in its `follows` list has produced a value
since this node last fired. For an acyclic graph this is topological order,
and nodes with no pending dependency between them fire concurrently. For a
graph with a cycle, it is dataflow: a node on the cycle fires again when its
inputs are fresh again.

Cycles are permitted. What bounds them is `max_fires` on every node that
lies on a cycle, which the runtime requires at construction, and the
episode's budget, which bounds everything. `foe plan` reports every cycle
and the bound that closes it.

Firing a node a second time re-fires every node downstream of it, because
their inputs became fresh. Recovery uses this.

### Completion

The workflow completes when a terminal node completes, or when a chosen
branch label has no successors. The episode's `done_when`, when present,
verifies the terminal value; findings re-fire the terminal node's nearest
model ancestor with the findings attached, under the recovery rules below.
When the graph has no terminal node and no empty-branch path, the episode
runs until its budget is spent and ends as `exhausted`, which is a
legitimate shape for a supervisor loop and is reported as such by
`foe plan`.

## The flow guarantee, stated exactly

A model node's child episode receives, and only receives: its own
instructions, the tools and grants its `model` block declares, the text the
runtime contributes for every episode, and the rendered outputs of the
nodes in its `follows` list. The child's log records all of it as its task
and its inbox. A reader who wants to prove that node C never received node
A's output checks two things: that A is not in C's `follows`, and that no
path of `follows` edges reaches C from A through a node that forwards A's
content. The first is a construction-time fact; the second is the trace.

The guarantee covers model context. It does not by itself cover the
filesystem. Two model nodes granted write access to the same directory can
communicate through it. A workflow that needs isolation between nodes grants
them disjoint write roots, and `foe plan` lists every pair of model nodes
whose write roots overlap, so that an author who wants the guarantee to
extend to the filesystem can see where it does not.

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
- `max_fires` caps every node, including re-fires that recovery causes.
- The episode budget caps everything.

When a bound is reached, the episode ends as `blocked` with
`recovery-exhausted`, carrying the findings never resolved. A recovery
decision that itself fails ends the episode with `recovery-failed`.
Recovery never recurses: a failure inside a recovery decision is terminal.

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

Reserved in version 1; emitted when the workflow executor ships. A workflow
episode's own log carries these; each model node's firing is a child
episode under `children/` with its own log, and `workflow/node-start`
names the child.

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
`recovery.max_interventions`, and the runtime's recovery instruction.

## Relationship to the rest of foe

A workflow episode is an episode. It has one log, one budget pool, one
outcome, and one identity. Its model nodes are child episodes and obey
every rule of [subagents](design.md#subagents-and-teams). Its tool nodes
dispatch through the ordinary registry with the ordinary effect checks.
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
