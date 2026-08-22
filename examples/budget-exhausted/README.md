# Budget exhausted

An episode that reaches the end of the allowance its configuration declared.
The budget permits four model calls, and the model asks for one more tool
call on every turn, so the fourth answer is the last one the runtime pays
for. The episode ends with the outcome `{"kind": "exhausted", "limit":
"model_calls"}`, and the process exits with code 3.

`exhausted` is one of the four outcome kinds a foe episode can end with,
beside `completed`, `blocked`, and `failed`. It carries one fact: a declared
or derived execution limit was reached. For a configured budget, the work
stops at a boundary in the execution contract. An operator can raise the
limit, narrow the task, or decide that the work is complete enough. A
`workflow_firings` outcome means that runtime execution exceeded the
structural total accepted during construction. That outcome requires
investigation because construction and execution use the same calculation.
The seven limits are `model_calls`, `tokens`, `seconds`,
`depth`, `episodes`, `concurrency`, and `workflow_firings`;
[docs/log-format.md](../../docs/log-format.md) lists them. The first six come
from `budget`. The workflow firing allowance is derived from the graph's
`max_fires` declarations and nesting structure.

The example needs no provider credential and no network. Its model is the
`exec` provider pointed at `never-finishing-transport` in this directory, a
program that answers every request with one more `read` call.
[docs/models.md](../../docs/models.md) specifies that provider and the chunk
lines the program writes. Each answer reads a different module. A call that
returns an identical result in `budget.loop_threshold` consecutive steps ends
the episode as `blocked` with the code `looping-tool-call`, and this example
is about the declared limit rather than the loop detector.

## Paths to replace

- `/home/user/project`: a directory with a `src` directory holding more
  modules than the budget allows turns, a `tools` directory, and a `support`
  directory.
- `/home/user/project/tools/never-finishing-transport`: a copy of this
  directory's `never-finishing-transport`, marked executable.
- `/home/user/project/support/chunks.py`: a copy of
  `examples/support/chunks.py`, which the transport imports.

Both copies lie inside the read root the configuration grants. An executable
the episode starts runs under the episode's sandbox with an empty
environment. It reads no path outside the read roots and executes no file
other than its own, so a transport left in this directory could not import
the helper it shares with the other examples. `support` sits beside `tools` in the project as it
does in `examples`, so the import path is the same in both places.

## Run

`run.sh` creates the project in a temporary directory, replaces the path
markers in `config.json`, runs the episode headless, and checks the log it
wrote.

```
cargo build --release --bin foe
sh examples/budget-exhausted/run.sh
```

The runner asserts what this example claims.

- The outcome is `exhausted`, and the limit it names is `model_calls`.
- The exit code is 3.
- The log holds exactly as many `model/request` events as the budget
  allowed, each one a step of its own and each one a first attempt.
- Every `assistant/message` stopped at a tool call, so no turn ever finished.
- The calls differ from one another, which is what keeps the loop detector
  out of this outcome.

## What to look for

`episode/start` records the resolved program, and its
`program.budget.model_calls` is the 4 that ends the run. The log then holds
four steps. Each step is a `model/request` with `step` counting from 1 and
`attempt` 1, four `assistant/chunk` events, three of them the tool call and
the fourth the stop the transport reported, one `assistant/message` whose
`stop` is `tool`, and one `tool/result` for the file that was read. The path in each result differs from the one before it.

No fifth `model/request` appears. The runtime checks the budget before it
assembles a request and again after a step settles, so the limit is reached
between steps and no request is started that the budget cannot cover.

The last event is `episode/end` with `{"kind": "exhausted", "limit":
"model_calls"}`. The four steps before it look exactly like the steps of a
run that completed; the ending is the whole difference. The `usage` in every
`assistant/message` is zero, because the transport reports no token counts,
and this configuration declares no `tokens` limit for them to count against.

In the viewer, the details region counts four model calls against four, and
its outcome row reads `exhausted · model_calls`.
