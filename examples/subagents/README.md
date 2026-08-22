# Subagents demo

A parent program that delegates reading to child episodes and keeps editing
for itself. The runner creates a small Python project, runs the episode
against a scripted model transport, and checks the parent's log, both
children's logs, and the edited files.

The parent lists the `spawn` and `wait` tools, and `grants.spawn` names the
one child program it may start, `survey`. `programs.survey` is a complete child
configuration: its own instructions, tools, grants, and budget. It omits
`version`, `task`, `model`, and `sandbox`, which a child inherits. The
child's grants are a subset of the parent's; construction refuses a child
that reaches further than its parent.

## Requirements

Linux, and `/usr/bin/python3` for the transport script and the runner's
checks. The demo needs no model credential and makes no provider request:
`model.provider` is `exec`, and the program it names answers every request
with fixed chunks. Both the parent and each child run that program, one
process per request.

The program is `transport.py` in this directory. The runner copies it to
`tools/transport.py` inside the project it creates, together with the helper
module it imports from `examples/support`, and the configuration names the
copy. The copy is what makes the demo runnable: a model transport is a
program the episode starts, and such a program reads only inside the
episode's read roots, so a script left in this directory could not read its
helper module.

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

1. The parent spawns two children, one per module, each with the task of
   reporting where its module reads the key.
2. Each child greps its module, reports what it found with `notify`, and
   ends. The report reaches the parent as a message.
3. The parent calls `wait`, which returns once every child it started has
   ended and the `spawn/end` and `budget/release` each one owes are in the
   parent's log. Both reports have arrived by then.
4. The parent applies the rename to both modules and finishes.

## Waiting for the children

`wait` is a built-in tool that takes no arguments. It returns when no child
of this episode is still running, or when the episode's `seconds` budget
runs out, and it reports an error in the second case. One call buys the
whole wait, so the parent spends one model call on waiting rather than one
per poll.

Without it a parent has no way to hold: `spawn` returns as soon as the child
starts, and an assistant turn with no tool calls completes the parent at
once. A parent that means to abandon its children still ends that way. The
episode's teardown then asks each child still running to end and waits for
its `spawn/end` and `budget/release`, so the log accounts for every
reservation the episode made whichever way the parent finishes.

## The grants

The parent may read the project and write `src` within it. Each child may
read the project and holds no write grant and no spawn grant, so a child
cannot edit and cannot start an episode of its own. The child's tool list is
narrower for the same reason: `read`, `grep`, and `notify`.

Each child receives its own kernel policy, compiled from its own grants.
The transport the children run is the copy under the project, and their
read grant covers the project, so it is reachable from every episode in the
tree. A child granted a narrower root, such as one subdirectory, could not
start the transport its inherited `model` block names.

## The budget pool

Budget is one pool held by the root episode. The parent declares 40 model
calls, 400,000 tokens, and 1,800 seconds. A `spawn` call names no amount, so
the reservation is what the child program declares: 8 calls and 60,000
tokens. Both reservations stand at once, so 16 of the parent's 40 calls are
held for its children while they run. When a child settles, the runtime
debits what the child spent and returns the rest, so the pool ends the run
down by 3 calls per child rather than by 8. No path through the tree can
spend more than the root declared.

Three structural caps sit beside the spend caps. `max_depth: 1` allows
children and forbids grandchildren. `max_episodes: 4` allows the parent and
three children over the life of the run. `max_concurrent: 2` allows two
children running at once. A `spawn` call that would pass any cap, or that
asks for more budget than remains, returns an error result naming the limit,
and no child starts; the model reads that result like any other.

## What to look for

Each `spawn` call produces, in the parent's log, a `budget/reserve` naming
the child and the amount taken from the remainder, then a `spawn/start` with
`context: "fresh"` and the id of the tool call that spawned it:

```json
{"seq": 12, "type": "budget/reserve", "data": { "child_id": "ep_ff6cef6d", "reserved": { "model_calls": 8, "tokens": 60000, "seconds": 1800 } }}
{"seq": 13, "type": "spawn/start", "data": { "child_id": "ep_ff6cef6d", "program": "survey", "context": "fresh", "call_id": "tc_spawn_config" }}
```

The child's own log is at `children/<child-id>/episode.jsonl` under the
parent's log directory, beside the configuration the child was launched
with. Its `episode/start.parent_id` names the parent, and its
`episode/start.program` is the child program: no write root, no spawn root,
and a budget of 8 calls against the parent's 40. When the child finishes,
the parent's log gains an `inbox/item` with source `child` carrying the
report, a second one stating that the child ended, a `spawn/end` with the
child's outcome, and a `budget/release` with what the child spent:

```json
{"seq": 35, "type": "budget/release", "data": { "child_id": "ep_ff6cef6d", "spent": { "model_calls": 3, "tokens": 0, "seconds": 0 } }}
```

The runner checks all of this. In the parent's log it requires two
reservations of the amount the `survey` program declares, two `spawn/start`
events with a fresh context, and one `wait` result that lands after all four
settlement events. It requires two children that ended `completed`, each
releasing less than it reserved, and four messages from children, of which
two are reports. In each child's log it requires a
parent id that names the root, grants without a write root and without a
spawn root, and a call budget below the parent's. It then checks that both
modules read `timeout_seconds` and that neither still reads `timeout`.

The tokens each child reports spending are zero, because the scripted
transport reports no usage. A run against a provider records real usage
here, and the release then returns the tokens the child did not use.

In the viewer, the episodes region at the top of the left column shows the
tree: the parent with both children hanging under it on a solid connector,
each with its own outcome. Selecting a child shows that child's
conversation. The details region counts the selected episode's model calls
and tokens against the budget its own `episode/start.program.budget`
declares, and the trajectory region above the main region draws each child's
span inside the parent's.

## Against a real model

Replace the `model` block with a provider block and the demo becomes an
ordinary configuration:

```json
"model": { "provider": "anthropic", "model": "claude-opus-5" }
```

The block names no key file, so the key is read from
`~/.config/foe/credentials/anthropic.json`, which `foe login anthropic`
writes; `examples/minimal` teaches the same convention. A block that names
`api_key_file` reads that file instead, which is what an operator does when
the credential lives somewhere a deployment dictates rather than in the home
directory. `docs/models.md` specifies both. The copied transport script is
then unnecessary, and the model chooses for itself how many children to
spawn and what to ask them.
