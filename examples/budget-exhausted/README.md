# Budget exhausted

An episode that reaches the end of the allowance its configuration declared.
The budget permits four model calls, and the model asks for one more tool
call on every turn, so the fourth answer is the last one the runtime pays
for. The episode ends with the outcome `{"kind": "exhausted", "limit":
"model_calls"}`, and the process exits with code 3.

`exhausted` is one of the four outcome kinds a foe episode can end with,
beside `completed`, `blocked`, and `failed`. It carries one fact: a resource
limit was reached. The contract did not break. Every model request was
answered, every tool call returned a result, and the work stopped at the
declared or derived resource boundary. An operator who reads this outcome
raises the limit, narrows the task, or decides the work is not worth more
calls. The limits that end an episode this way are `model_calls`,
`input_tokens`, `output_tokens`, `context_window`, `seconds`, `depth`,
`episodes`, and `concurrency`;
[docs/log-format.md](../../docs/log-format.md) lists them.

The example needs no endpoint credential and no network. The host answers
every request with one more `read` call from `responses.py`. Each answer reads
a different module. A call that
returns an identical result in `budget.loop_threshold` consecutive steps ends
the episode as `blocked` with the code `looping-tool-call`, and this example
is about the declared limit rather than the loop detector.

## Paths to replace

- `/home/user/project`: a directory with a `src` directory holding more
  modules than the budget allows turns.

## Run

`run.sh` creates the project in a temporary directory and replaces the path
marker in `config.json`. The host runner supplies deterministic responses and
checks the log the binary wrote.

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

`episode/start` records the resolved contract, and its
`contract.budget.model_calls` is the 4 that ends the run. The log then holds
four steps. Each step is a `model/request` with `step` counting from 1 and
`attempt` 1, four `assistant/chunk` events, three of them the tool call and
the fourth the stop the host reported, one `assistant/message` whose
`stop` is `tool`, and one `tool/result` for the file that was read. The path in each result differs from the one before it.

No fifth `model/request` appears. The runtime checks the budget before it
assembles a request and again after a step settles, so the limit is reached
between steps and no request is started that the budget cannot cover.

The last event is `episode/end` with `{"kind": "exhausted", "limit":
"model_calls"}`. The four steps before it look exactly like the steps of a
run that completed; the ending is the whole difference. The `usage` in every
`assistant/message` is zero because the host responses report no token counts.
This configuration declares no input-token or output-token limit.

In the viewer, the details region counts four model calls against four, and
its outcome row reads `exhausted · model_calls`.
