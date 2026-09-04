# Verification unsatisfiable

An episode that reports its work as finished three times while the check it
must pass reports the same finding each time. The configuration declares
`done_when: { "verify": "todo-check", "retries": 2 }`, so the runtime runs
the verifier on every candidate, feeds the findings back twice, and ends the
episode blocked with the code `verification-unsatisfiable` and a message
stating that `todo-check` still reports one finding after two retries. The
process exits with code 2.

This is the shape of failure an operator meets most often in practice. The
model states that the TODO comment is gone and the file still holds it, and
a contract settles the disagreement. `examples/recovery-exhausted` shows an
episode blocked because the provider never answered. This one shows an
episode blocked while every part of the machinery worked, which is the harder
case to recognize without a log.

A verifier is any executable written to the contract in
[docs/config.md](../../docs/config.md): it receives the candidate as JSON on
standard input, it accepts by exiting zero and printing nothing, and it
reports findings by exiting zero and printing one per line. Any other exit
status is a failure of the verifier itself and ends the episode as `failed`.
`todo-check` in this directory is such a contract in two lines: it greps the
source directory for TODO comments and maps grep's exit status 1, meaning no
match, onto the acceptance the contract asks for.

The episode holds `edit` and a write grant on `src`, so it could make the
change the check asks for. Nothing in the configuration prevents completion.
The candidate the verifier judges here is the text of a finishing turn; a
configuration that also declares `done_when.returns` has the returned value
judged instead.

## Paths to replace

- `/home/user/project`: a Python repository with a `src` directory holding a
  module with a TODO comment and a `tools` directory.
- `/home/user/project/tools/todo-check`: a copy of this directory's
  `todo-check`, marked executable, with the path inside it pointing at the
  project's `src`.

The verifier copy lies inside the read root the configuration grants. Its
materialized path points at the disposable project's source directory.

## Run

`run.sh` creates the project in a temporary directory and replaces the path
markers in `config.json` and `todo-check`. The host supplies completion claims
from `responses.py`. The runner checks both the log and the project.

```
cargo build --release --bin foe
sh examples/verification-unsatisfiable/run.sh
```

The runner asserts what this example claims.

- The outcome is `blocked` with the code `verification-unsatisfiable`, and
  its message names the verifier and the retries it spent.
- The exit code is 2.
- Three answers finished their turns without calling a tool, and their texts
  differ.
- Two `inbox/item` events with the source `verify` carry the finding back.
- The module still holds the TODO comment that every answer claimed to have
  removed.

## What to look for

Each step is a `model/request`, the `assistant/chunk` events of one text
answer, and an `assistant/message` whose `stop` is `end` and whose
`tool_calls` are empty. A turn of that shape is what makes a candidate, and
the verifier runs on each one.

Between the steps stand two `inbox/item` events with the source `verify`.
Each carries the runtime's framing sentence, the verifier's name, and the
finding line, which is the grep output naming the file, the line number, and
the comment. The next `model/request` names that item in `consumed`, so the
log shows the findings entering the conversation.

The verifier's own run leaves no `tool/result`, because a `done_when`
verifier is invoked by the runtime rather than called by the model. The log
holds no `tool/result` at all here, since no answer called a tool.

The last event is `episode/end` with the blocked outcome, and its message
names the verifier and the number of retries that were spent. Raising
`done_when.retries` would buy more attempts at the same disagreement; the
outcome tells an operator to change the work, the check, or the instructions
rather than to rerun.

In the viewer, the verify items appear in the trajectory between the steps,
and the details region's outcome row reads
`blocked · verification-unsatisfiable`.
