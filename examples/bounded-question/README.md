# A question bounded by a deadline

Two recorders each write a header into one region's notes file, and neither
can choose the date the header records: nothing in the repository says which
date, and only the lead knows. Each recorder asks the lead and blocks on the
answer.

The two questions end differently. The lead replies to the eastern one,
naming the question's `message_id` in `reply_to`, and that reply reaches the
recorder as a `response` inbox item under the question's own identifier. The
lead never answers the western one, whose deadline passes; the runtime then
delivers the default the recorder named, marked as an item the runtime wrote
in a member's place. Exactly one answer reaches each recorder, so neither is
left waiting on the other, and both headers record the same date.

The response service is deterministic. The runner checks the two headers and
the coordination events behind them. It requires no credential and makes no
endpoint request.

## Requirements

The example runs on Linux and uses `/usr/bin/python3` for its check and its
runner assertions.

## Run

From the repository root:

```sh
cargo build --release --bin foe
examples/bounded-question/run.sh
```

The runner accepts the binary path as its only argument. It defaults to
`target/release/foe`. Each run creates a directory named
`target/foe-bounded-question.XXXXXX/` holding the materialized
configuration, the small project, and the episode tree.

The run waits one real second, which is the western question's deadline. A
deadline is wall-clock time and the example spends it rather than pretending
to, for the same reason the recovery-exhausted example waits its backoff.

## What the runner checks

- Each recorder was granted one directory of its own, which is what keeps
  two writers off one file.
- The lead was asked two questions and sent one message. Direction separates
  them: every message a team carries is durable in the lead's log, questions
  included.
- The answer carries the identifier of the question it answers.
- The eastern recorder received one `response` naming its own question, sent
  by the lead.
- The western recorder received one `response` naming its own question,
  written by the runtime, with no sender, no sooner than the deadline the
  question declared.
- Both headers record the same date, so the two routes reached one answer.
