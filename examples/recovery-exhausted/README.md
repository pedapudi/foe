# Recovery exhausted

An episode that never gets an answer to its first request. The host response
reports a retryable endpoint error every time. The
runtime waits out with a delay that doubles from 500 milliseconds for as
long as the seconds budget funds the next delay. The contract grants ten
seconds, which funds five attempts; the sixth delay would not fit, so the
episode ends with the outcome `{"kind": "blocked", "code":
"recovery-exhausted", "message": "provider unavailable through 5 attempts
at step 1; the remaining seconds budget cannot fund another"}`, and the
process exits with code 2. The whole run takes about 8 seconds, almost all
of it spent in those delays. A failure that repeating cannot fix — a
transport loss, an interrupted stream — is bounded by a fixed attempt
ceiling instead; docs/design.md "Failure of a model request" states both
bounds.

`blocked` is the outcome kind for an episode the runtime stopped because it
had no way to continue, and its `code` says which way was missing.
[docs/log-format.md](../../docs/log-format.md) lists the nine codes; this
example produces `recovery-exhausted`, which covers a step whose request
retries were spent and a workflow that reached a recovery bound.

## Why this code

Three codes are the model's own report: `goal-unreachable`,
`ambiguous-task`, and `missing-capability` reach the log because the model
called the built-in `block` tool with that code. A fixed response can emit
such a call, which demonstrates the fixture's choice. The runtime decides the
remaining codes for itself.

`recovery-exhausted` needs nothing from the model. The host assembles no
answer, and the budget bound and the
backoff schedule that produce the outcome are the runtime's, so the log this
example writes is the log a real unreachable provider writes. That is also
the failure an operator meets most often when a run is handed to an
unattended machine: a host name that does not resolve, an endpoint behind a
firewall, a credential the provider rejects with a retryable status.
`examples/verification-unsatisfiable` demonstrates another code the runtime
decides on its own.

For a response error, the `retryable` flag decides between `blocked` and
`failed`. This fixture sets the flag, so the runtime retries until the budget
cannot fund another attempt, which is `blocked`. A response that reports the
same error with `retryable` false ends the episode as `failed` at the first
attempt, with the message as the error.

## Paths to replace

- `/home/user/project`: a directory with a `src` directory.

## Run

`run.sh` creates the project in a temporary directory and replaces the path
marker in `config.json`. The host supplies the retryable error from
`responses.py`. The runner checks the log the binary wrote.

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
- The four `request/retry` events record the delays 500, 1000, 2000, and
  4000 milliseconds, and each one is followed by the `model/request` it
  announces.
- No `assistant/message` and no `tool/result` was written.

## What to look for

The log holds five `model/request` events. Every one has `step` 1, because
the step never completed, and `attempt` counts 1 through 5. The first names
the task in `consumed`; the four after it consume nothing, because the task
was already taken into the request that is being retried. The `messages` of
all five are identical, which is what makes them attempts at one step rather
than five steps.

After each request comes one `assistant/chunk` holding the host's error
chunk. When a further attempt is permitted, the runtime waits the delay and
then writes one `request/retry` naming the attempt that failed, the `cause`,
and the `delay_ms` it waited, immediately before the attempt that follows.
The cause here is `provider`, because the contract reported an error;
`transport` names a stream that ended with no final chunk, `rate-limit`
names a message mentioning a rate limit or a 429, and `interrupted` names a
failure after text had already arrived.

The four delays double: 500, 1000, 2000, 4000. A reader who has not watched
the run learns from that shape alone that the endpoint was never reachable,
rather than briefly overloaded. The error message repeats unchanged in every
attempt, so it names the condition to fix.

There are five attempts and four retries. After the fifth failure the next
delay would outrun the remaining seconds budget, so the episode ends with
no delay waited and no retry recorded: a `request/retry` states that a
request is being retried, and the log format requires the attempt it
announces to follow it.

No `assistant/message` is written, because none was assembled. No
`tool/result` is written, because no tool ran. The episode read nothing from
the project it was granted. The last event is `episode/end` with the blocked
outcome.

In the viewer, the step shows its five attempts with the delay between them,
and the details region's outcome row reads
`blocked · recovery-exhausted`.
