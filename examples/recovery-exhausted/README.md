# Recovery exhausted

An episode that never gets an answer to its first request. The transport
reports a retryable provider error every time, the runtime retries five
times with a delay that doubles from 500 milliseconds to 8 seconds, and the
episode ends with the outcome `{"kind": "blocked", "code":
"recovery-exhausted", "message": "5 attempts at step 1 failed"}`. The
process exits with code 2. The whole run takes about 15 seconds, almost all
of it spent waiting between attempts.

`blocked` is the outcome kind for an episode the runtime stopped because it
had no way to continue, and its `code` says which way was missing.
[docs/log-format.md](../../docs/log-format.md) lists the nine codes; this
example produces `recovery-exhausted`, which covers a step whose request
retries were spent and a workflow that reached a recovery bound.

## Why this code

Three codes are the model's own report: `goal-unreachable`,
`ambiguous-task`, and `missing-capability` reach the log because the model
called the built-in `block` tool with that code. A scripted transport can
emit such a call, and the outcome would then be whatever the script named,
which demonstrates the script rather than the runtime. The remaining codes
the runtime decides for itself.

`recovery-exhausted` is the one of those that needs nothing from the model
at all. The transport answers no request, and the retry ceiling and the
backoff schedule that produce the outcome are the runtime's, so the log this
example writes is the log a real unreachable provider writes. That is also
the failure an operator meets most often when a run is handed to an
unattended machine: a host name that does not resolve, an endpoint behind a
firewall, a credential the provider rejects with a retryable status.
`examples/verification-unsatisfiable` demonstrates the other code the
runtime decides without the model's help.

The distinction between `blocked` and `failed` is the `retryable` flag on
the error chunk. This transport sets it, so the runtime retries and reaches
its ceiling, which is `blocked`. A transport that reports the same error
with `retryable` false ends the episode as `failed` at the first attempt,
with the message as the error.

## Paths to replace

- `/home/user/project`: a directory with a `src` directory and a `tools`
  directory.
- `/home/user/project/tools/unreachable-provider-transport`: a copy of this
  directory's `unreachable-provider-transport`, marked executable, beside a
  copy of `examples/support/chunks.py`, which it imports. Both lie under the
  read root, because the transport runs under the episode's sandbox with an
  empty environment and can open nothing outside it.

## Run

`run.sh` creates the project in a temporary directory, replaces the path
markers in `config.json`, runs the episode headless, and checks the log it
wrote.

```
cargo build --release --bin foe
sh examples/recovery-exhausted/run.sh
```

The runner asserts what this example claims.

- The outcome is `blocked` with the code `recovery-exhausted`, and its
  message names five attempts at step 1.
- The exit code is 2.
- The five `model/request` events all belong to step 1 and carry the same
  messages.
- The five `request/retry` events record the delays 500, 1000, 2000, 4000,
  and 8000 milliseconds.
- No `assistant/message` and no `tool/result` was written.

## What to look for

The log holds five `model/request` events. Every one has `step` 1, because
the step never completed, and `attempt` counts 1 through 5. The first names
the task in `consumed`; the four after it consume nothing, because the task
was already taken into the request that is being retried. The `messages` of
all five are identical, which is what makes them attempts at one step rather
than five steps.

After each request comes one `assistant/chunk` holding the transport's error
chunk, then one `request/retry` with the `cause` and the `delay_ms` the
runtime waited. The cause here is `provider`, because the program reported
an error; `transport` names a stream that ended with no final chunk,
`rate-limit` names a message mentioning a rate limit or a 429, and
`interrupted` names a failure after text had already arrived.

The five delays double: 500, 1000, 2000, 4000, 8000. A reader who has not
watched the run learns from that shape alone that the endpoint was never
reachable, rather than briefly overloaded. The error message repeats
unchanged in every attempt, so it names the condition to fix.

No `assistant/message` is written, because none was assembled. No
`tool/result` is written, because no tool ran. The episode read nothing from
the project it was granted. The last event is `episode/end` with the blocked
outcome.

In the viewer, the step shows its five attempts with the delay between them,
and the outcome line names the code.
