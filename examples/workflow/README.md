# Workflow

A declared graph of three nodes in place of the free agent loop. The
`workflow` key names the nodes and the edges between them; the runtime
fires each node when its inputs are ready and routes every failure through
a recovery decision. [docs/workflow.md](../../docs/workflow.md) specifies
the graph; this example runs one.

```
   survey ──► propose ──► apply ■
              │
              └── nothing ──► (ends)
```

- `survey` is a tool node. It calls the built-in `grep` with fixed
  arguments and produces the list of TODO comments in the Python files
  under the first read root. The arguments name no path, so the program's
  identity is the same in every directory.
- `propose` is a model node. It receives the grep output as the section
  `## survey` of its task, reads the code around a TODO it chooses, and
  returns a plan. Its `branches` declare a choice point: the label `apply`
  fires the next node, and the label `nothing` ends the workflow with the
  plan as its value. The runtime adds `branch` to the node's `returns`
  schema as a required field whose values are the two labels.
- `apply` is a model node with write access. It receives the plan as the
  section `## propose`, makes the change, and finishes. Its `verify` runs
  the configured `check` executable on its output; findings re-fire the
  node once, with the findings attached as a `## findings` section, before
  recovery is asked. `terminal: true` makes its value the episode's.

The root `instructions`, `tools`, `grants`, and `budget` are the ceiling.
A tool node names a tool in the root `tools`; a model node's program is a
child program whose grants lie within the root grants; each model node's
budget is reserved from the root pool when the node fires. The root
instructions reach no node; validation requires one entry, and the entry
states what the document is.

## Paths to replace

- `/home/user/project`: a Python repository with a `src` directory and a
  virtual environment at `.venv` with `ruff` installed.
- `/home/user/project/tools/ruff-check`: a copy of
  `examples/wrap-a-binary/ruff-check`, marked executable, with the path
  inside it pointing at `.venv/bin/ruff`.
- `/home/user/.config/foe/anthropic.key`: a file whose whole contents are
  the API key.

## Run

```
cp examples/wrap-a-binary/ruff-check /home/user/project/tools/ruff-check
chmod +x /home/user/project/tools/ruff-check
foe plan --config examples/workflow/config.json
foe --config examples/workflow/config.json
```

`foe plan` prints the resolved program and, below it, the graph: every
node with its kind and inputs, every edge, every cycle with the
`max_fires` that bounds it, every pair of model nodes whose write roots
overlap, and the terminal node. This graph has no cycle and no overlap.

## What to look for

The episode log holds no model requests of its own until a node fails.
It holds one `workflow/node-start` and one `workflow/node-end` per firing,
in dataflow order: `survey`, then `propose`, then `apply`. The
`node-start` of a model node names the child episode under `children/`
whose log is that node's firing: its task is the sections built from its
inputs, its `request/header` shows the `return` tool with the `branch`
field, and its `episode/end` carries the value the node produced.

Between `propose` and `apply` a `workflow/branch` event records the label
chosen and the successors it fired. When `check` reports findings after
`apply`, the node's second firing starts with `fire: 2`, and its child's
task ends with a `## findings` section.

When a node fails in a way a second attempt could change, the episode log
gains one model request: a `request/header` whose system prompt is the
runtime's recovery instruction and whose one tool is `recover`, an
`inbox/item` with source `system` carrying the failed node's inputs and
error, and an `assistant/message` choosing an action. The
`workflow/recovery` event records the action and the node it acts on.
`recovery.max_interventions: 2` allows two such decisions; a third failure
ends the episode as `blocked` with code `recovery-exhausted`.

In the viewer, each node's firing links to its child episode, so a reader
moves from the graph to the conversation that produced a value in one
step.
