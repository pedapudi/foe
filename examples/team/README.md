# Team

A lead program with two child programs that talk to each other. A team is
the set of episodes spawned by one episode, the lead. The lead lists
`spawn`, `send`, and `team`; `grants.spawn` names the two programs it may
start, `reviewer` and `tester`. Each member lists `send`, to message a
teammate by roster name, and `notify`, to report to the lead. Every child an
episode spawns is a member of that episode's team, so no further
declaration is needed. The lead makes the code change itself and then
spawns both members, who report back while the lead waits.

## Paths to replace

- `/home/user/project`: a Python repository with `src/cli.py` and a `tests`
  directory.
- `/home/user/.config/foe/anthropic.key`: a file whose whole contents are
  the API key.

## Run

```
foe --config examples/team/config.json
```

## The roster

The roster is the list of members with their phase. It exists only as
`team/roster` events in the lead's log, one per change of phase. A member
enters as `provisioning` when `spawn` is called, becomes `active` when its
`episode/start` is written, and becomes `failed` when its process ends
without an outcome. The `team` tool returns the roster folded from those
events, so the lead can see who is still active without any other state.

```json
{"seq": 14, "time": 1724200002000, "type": "team/roster", "data": { "member_id": "ep_a1", "name": "reviewer", "description": "You review a change for correctness. …", "phase": "active" }}
```

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
{"seq": 22, "time": 1724200009000, "type": "team/message", "data": { "message_id": "tm_07", "from": "ep_a1", "to": "ep_b2", "content": [ { "type": "text", "text": "Which tests cover src/cli.py?" } ] }}
{"seq": 23, "time": 1724200009004, "type": "team/delivered", "data": { "message_id": "tm_07", "to": "ep_b2" }}
```

Folding the lead's log yields the whole mailbox: every `team/message` is
queued, and each one with a matching `team/delivered` is settled. A message
that is queued with no delivery record is redelivered when its target
restarts, and the target drops a duplicate by `message_id`. The roster, the
queue, and the delivery records are the only team state; there is no
database and no in-memory copy that the log does not also hold.

A member's `notify` call takes the other route: it becomes an `inbox/item`
with source `child` in the lead's log, which enters the lead's next request.
That is how both reports reach the lead.

## What to look for

In the lead's log: two `spawn/start` events, a `team/roster` event for each
phase change of each member, a `team/message` and `team/delivered` pair for
every `send`, and two `inbox/item` events with source `child` carrying the
reports. In each member's log under `children/`, the `inbox/item` with
source `peer` appears with the sender's id and the `message_id` from the
lead's log.

In the viewer, the episode tree shows the lead with two children, and the
lead's conversation shows the two reports as user messages labeled with
their source.
