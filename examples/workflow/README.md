# Workflow demo

This example runs a declared workflow from task input through a verified code
change. The runner creates a disposable Python project, materializes the
configuration with absolute paths, runs foe, and checks the resulting source
file and episode log.

The example uses a deterministic local model transport. It requires no model
credential and makes no provider request. The transport emits the same tool
calls that a model transport would emit, so the runtime still executes the
model nodes, tools, branch, verifier, child episodes, and log protocol.

The runner requires `/usr/bin/python3`.

## Run

From the repository root:

```sh
bazel run //examples/workflow
```

The target builds `//:foe` and runs `run.sh`. A binary at another path can run
the example directly:

```sh
examples/workflow/run.sh /absolute/path/to/foe
```

Each run creates `target/foe-workflow-demo.XXXXXX/`. The directory contains
the materialized configuration, the disposable project, and the complete
episode tree. The runner prints a command that serves the viewer for that
episode tree.

The same runner is a Bazel test target:

```sh
bazel test //examples/workflow:workflow_test
```

## Graph

```text
   task ──────┐
   survey ──► propose ──apply──► apply ■
              │
              └──nothing──► (no successor: the workflow ends)
```

The graph has three declared nodes. `foe plan --config FILE` prints the same
shape and reports the completion as `terminal apply`.

- `task` is the built-in source carrying the configuration task. It is not a
  declared node.
- `survey` runs the built-in `grep` tool over the disposable project.
- `propose` receives the task and grep result. It returns a typed plan and
  selects the `apply` branch.
- `apply` receives the plan and replaces the TODO implementation with
  `return left + right`. It is the terminal node.
- The `nothing` label lists no successor, so choosing it ends the workflow
  with the value `propose` produced.
- `check` is `apply`'s verifier rather than a node. Empty standard output
  accepts the result. Each output line would become a verifier finding and
  re-fire `apply`.

The root contract defines the maximum tools and paths available to child
nodes. This limit is the authority ceiling. Each model node runs as a child
contract with a subset of the root tools and grants. The root budget reserves
budget for each child and releases the unused reservation after the child
ends.

## Expected result

The source file begins as:

```python
def add(left: int, right: int) -> int:
    # TODO: Implement add.
    raise NotImplementedError
```

The `apply` node changes it to:

```python
def add(left: int, right: int) -> int:
    return left + right
```

The command exits zero and prints a completed outcome. `run.sh` then confirms
the source change and the presence of `workflow/branch` and
`workflow/node-end` events.

## Log evidence

The root `episode.jsonl` contains the graph events. Each model-node firing has
a child log under `children/`.

- `workflow/node-start` names the node, firing number, inputs, and child id.
- `workflow/node-end` records the value, rendering, duration, and error field.
- `workflow/branch` records the selected label and successor node.
- The `propose` child ends through the synthesized `return` tool.
- The `apply` child records the `edit` call, its diff, and its final sentence.

The viewer links each node firing to the child episode that produced its
value.
