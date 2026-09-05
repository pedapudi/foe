# Subagents demo

A parent contract that delegates reading to child episodes and keeps editing
for itself. The runner creates a small Python project, runs the episode
with deterministic host responses, and checks the parent's log, both children's
logs, and the edited files.

The parent lists the `spawn` and `wait` tools, and `grants.spawn` names the
one child contract it may start, `survey`. `child_contracts.survey` is a complete child
configuration: its own instructions, tools, grants, and budget. It omits
`version`, `task`, `model`, and `sandbox`, which a child inherits. The
child's grants are a subset of the parent's; construction refuses a child
that reaches further than its parent.

## Requirements

Linux, and `/usr/bin/python3` for the response functions and runner checks.
The demo needs no credential and makes no endpoint request. `responses.py`
selects parent or child behavior from the tools in each request. This
selection stays deterministic when both children run concurrently.

## Run

From the repository root:

```sh
cargo build --release --bin foe
examples/subagents/run.sh
```

The runner takes the path of the binary as its only argument and defaults
to `target/release/foe`. Each run creates `target/foe-subagents-demo.XXXXXX/`
holding the materialized configuration, the project the episode edits, and
the episode log with both child logs under `children/`. The last line the
runner prints is the command that serves the viewer for that log.

## What the run does

The project holds two modules that read a configuration key named
`timeout`. The task is to rename that key to `timeout_seconds` in both.

1. The parent adds two board tasks, one per module. Both tasks are ready, so
   the runtime assigns each one to a child that reports where its module reads
   the key.
2. Each child greps its module, reports what it found with `notify`, and
   ends. The report reaches the parent as a message.
3. The parent calls `wait`. It returns after both tasks have settled and each
   child's `spawn/end` and `budget/release` are in the parent log. Both reports
   have arrived by then.
4. The parent applies the rename to both modules and finishes.

## Waiting for the children

`wait` is a built-in tool that takes no arguments. It returns when every
added task has settled and no child reservation remains. The episode's
`seconds` budget bounds the wait. One call buys the whole wait, so the parent
spends one model call on waiting rather than one per poll.

Without it a parent has no way to hold: `spawn` returns after recording and
scheduling the task, and an assistant turn with no tool calls completes the
parent at once. A parent that means to abandon its children still ends that way. The
episode's teardown then asks each child still running to end and waits for
its `spawn/end` and `budget/release`, so the log accounts for every
reservation the episode made whichever way the parent finishes.

## The grants

The parent may read the project and write `src` within it. Each child may
read the project and holds no write grant and no spawn grant, so a child
cannot edit and cannot start an episode of its own. The child's tool list is
narrower for the same reason: `read`, `grep`, and `notify`.

Each child receives its own kernel policy, compiled from its own grants. The
host response functions run outside that policy. The child policy covers only
the tools the child asks the binary to execute.

## The budget pool

Budget is one pool held by the root episode. The parent declares 40 model
calls, 320,000 input tokens, 80,000 output tokens, and 1,800 seconds. A
`spawn` call names no amount, so the reservation is what the child contract
declares: 8 calls, 48,000 input tokens, and 12,000 output tokens. Both
reservations stand at once, so 16 of the parent's 40 calls are held for its
children while they run. When a child settles, the runtime debits what the
child spent and returns the rest. The pool ends the run down by 3 calls per
child rather than by 8.

Three structural caps sit beside the spend caps. `max_depth: 1` allows
children and forbids grandchildren. `max_episodes: 4` allows four episodes
in the whole tree over the life of the run, which here is the parent and
three children; it is a reservation dimension like the others, and a child
that may start none of its own asks for one. `max_concurrent: 2` allows two
children running at once, counting one episode's direct children alone. A
third ready task would remain queued until one child returned capacity. A
task that cannot reserve another structural or spend limit settles as
exhausted and records the limit in its outcome.

## What to look for

Each `spawn` call first records a queued `team/task` revision. Assignment
then produces a `budget/reserve` naming the child and the amount taken from
the remainder. A `spawn/start` follows with `context: "fresh"` and the id of
the tool call that added the task:

```json
{"seq": 12, "type": "budget/reserve", "data": { "child_id": "ep_ff6cef6d", "reserved": { "model_calls": 8, "input_tokens": 48000, "output_tokens": 12000, "seconds": 1800, "episodes": 1 } }}
{"seq": 13, "type": "spawn/start", "data": { "child_id": "ep_ff6cef6d", "contract": "survey", "context": "fresh", "call_id": "tc_spawn_config" }}
```

The child's own log is at `children/<child-id>/episode.jsonl` under the
parent's log directory, beside the configuration the child was launched
with. Its `episode/start.parent_id` names the parent, and its
`episode/start.contract` is the child contract: no write root, no spawn root,
and a budget of 8 calls against the parent's 40. When the child finishes,
the parent's log gains an `inbox/item` with source `child` carrying the
report, a second one stating that the child ended, a `spawn/end` with the
child's outcome, and a `budget/release` with what the child spent:

```json
{"seq": 35, "type": "budget/release", "data": { "child_id": "ep_ff6cef6d", "spent": { "model_calls": 3, "input_tokens": 0, "output_tokens": 0, "seconds": 0, "episodes": 1 } }}
```

The runner checks all of this. In the parent's log it requires two
reservations of the amount the `survey` contract declares, two `spawn/start`
events with a fresh context, and one `wait` result that lands after all four
settlement events. It requires two children that ended `completed`, each
releasing less than it reserved, and four messages from children, of which
two are reports. In each child's log it requires a
parent id that names the root, grants without a write root and without a
spawn root, and a call budget below the parent's. It then checks that both
modules read `timeout_seconds` and that neither still reads `timeout`.

The input and output tokens each child reports spending are zero because the
host responses report no usage. The release returns the unused allowance.

In the viewer, the episodes region at the top of the left column shows the
tree: the parent with both children hanging under it on a solid connector,
each with its own outcome. Selecting a child shows that child's
conversation. The details region counts the selected episode's model calls,
input tokens, and output tokens against the limits its own
`episode/start.contract.budget` declares. The trajectory region above the
main region draws each child's span inside the parent's.
