# Team demo

A lead contract with two child contracts that talk to each other. The runner
creates a small Python project, runs the episode against a scripted model
transport, and checks the lead's log, both member logs, and the changed
file.

A team is the set of episodes spawned by one episode, the lead. The lead
lists `spawn` and `team`; `grants.spawn` names the two child contracts it may
start, `reviewer` and `tester`. Each member lists `send`, to message a
teammate by roster name, and `notify`, to report to the lead. Every child an
episode spawns is a member of that episode's team, so no further declaration
is needed. The lead makes the code change itself and then spawns both
members, who report back while the lead waits.

## Requirements

Linux, and `/usr/bin/python3` for the transport script, the project's own
checks, and the runner's checks. The demo needs no model credential and
makes no provider request: `model.provider` is `exec`, and the contract it
names answers every request with fixed chunks. The lead and both members run
that contract, one process per request.

The contract is `transport.py` in this directory. The runner copies it to
`tools/transport.py` inside the project it creates, together with the helper
module it imports from `examples/support`, and the configuration names the
copy. The copy is what makes the demo runnable: a model transport is a
contract the episode starts, and such a contract reads only inside the
episode's read roots, so a script left in this directory could not read its
helper module. Each member is granted the same read root as the lead, so the
one copy serves all three episodes.

## Run

From the repository root:

```sh
cargo build --release --bin foe
examples/team/run.sh
```

The runner takes the path of the binary as its only argument and defaults to
`target/release/foe`. Each run creates `target/foe-team-demo.XXXXXX/` holding
the materialized configuration, the project, and the episode log with both
member logs under `children/`. The last line the runner prints is the
command that serves the viewer for that log.

## What the run does

The project holds a command line in `src/cli.py` and a check script in
`tests/check.py` that expects a `--dry-run` flag the command line does not
yet have.

1. The lead edits `src/cli.py` to add the flag.
2. The lead spawns the reviewer with the file it changed and the tester with
   the command to run.
3. The tester runs `python3 -B tests/check.py`. The reviewer reads the
   changed file and asks the tester, with `send`, which checks cover it.
4. The tester answers with `send`. Each then reports to the lead with
   `notify` and ends.
5. The lead calls `wait`, which returns once both members have ended and
   their `spawn/end` and `budget/release` events are in the lead's log, and
   then finishes.

The members wait differently, because they wait for each other rather than
for a child. `wait` covers children alone, and no tool holds an episode
until a peer message arrives, so each member answers with a rotation of
three read-only calls until the message it needs is in its inbox. The
rotation exists so that no call repeats in three consecutive steps, which
would end the episode as `looping-tool-call`.

## The roster

The roster is the list of members with their phase. It exists only as
`team/roster` events in the lead's log, one per change of phase. A member
enters as `provisioning` when `spawn` is called, becomes `active` when its
`episode/start` is written, and becomes `failed` when its process ends
without an outcome. The `team` tool returns the roster folded from those
events, so the lead can see who is still active without any other state.

```json
{"seq": 21, "type": "team/roster", "data": { "member_id": "ep_598bf8c0", "name": "reviewer", "description": "Review the change to src/cli.py, which adds a --dry-run flag.", "phase": "provisioning" }}
{"seq": 28, "type": "team/roster", "data": { "member_id": "ep_598bf8c0", "name": "reviewer", "description": "Review the change to src/cli.py, which adds a --dry-run flag.", "phase": "active" }}
```

The description is the task the member was spawned with. A member's roster
name is the `name` the `spawn` call gives, and the contract name when it gives
none, which is how the reviewer addresses `send` to `tester`.

## The mailbox fold

A message between members passes through the lead's log. When the reviewer
calls `send` with `to: "tester"`, the reviewer's process reports the call to
the lead over the host protocol, and the lead's process appends a
`team/message`. The message is durable at that point, before any delivery.
The lead then writes the message into the tester's inbox as an `inbox/item`
with source `peer`, `from` set to the reviewer's episode id, and the same
`message_id`. After the tester has appended that item to its own log, the
lead appends `team/delivered`.

```json
{"seq": 30, "type": "team/message", "data": { "message_id": "tm_01", "from": "ep_598bf8c0", "to": "ep_bff3b86e", "content": [ { "type": "text", "text": "Which checks cover src/cli.py?" } ] }}
{"seq": 31, "type": "team/delivered", "data": { "message_id": "tm_01", "to": "ep_bff3b86e" }}
```

Folding the lead's log yields the whole mailbox: every `team/message` is
queued, and each one with a matching `team/delivered` is settled. A message
that is queued with no delivery record is redelivered when its target
restarts, and the target drops a duplicate by `message_id`. The roster, the
queue, and the delivery records are the only team state; there is no
database and no in-memory copy that the log does not also hold.

A member's `notify` call takes the other route: it becomes an `inbox/item`
with source `child` in the lead's log, which enters the lead's next request.
The runtime appends a second such item when the member ends, stating the
outcome. That is how both reports and both endings reach the lead.

## What to look for

The lead's log holds two `spawn/start` events and two `budget/reserve`
events, each taking the 12 calls the member contract declares. It holds a
`team/roster` event for each phase change of each member, and a
`team/message` and `team/delivered` pair for each of the two `send` calls.
It ends with four `inbox/item` events with source `child`, and a `spawn/end`
and a `budget/release` for each member. In each member's log under
`children/`, the `inbox/item` with source `peer` carries the sender's
episode id and the `message_id` from the lead's log:

```json
{"seq": 19, "type": "inbox/item", "data": { "source": "peer", "content": [ { "type": "text", "text": "tests/check.py covers src/cli.py with three cases, one of them the dry run." } ], "from": "ep_bff3b86e", "message_id": "tm_02" }}
```

The runner checks all of this. In the lead's log it requires two members
that entered the roster as `provisioning` and became `active`, with neither
recorded as `failed`. It requires one message each way between them, both
settled by a delivery record, and four messages from members, of which one
is the review and one the test result. In each member's log it requires one
peer message, sent by the other member and carrying an id the lead queued,
and an outcome of `completed`. It then checks that `src/cli.py` names the
flag and that the project's own checks pass.

In the viewer, the episodes region at the top of the left column shows the
tree: the lead with both members hanging under it on a solid connector.
Selecting the lead shows the two reports in its conversation as user
messages; selecting a member shows the peer message in the member's own
conversation. The trajectory region draws both members' spans inside the
lead's, which is where the two overlapping runs are visible.

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
then unnecessary, and the members decide for themselves what to ask each
other.
