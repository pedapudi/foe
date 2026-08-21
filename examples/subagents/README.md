# Subagents

A parent program that delegates reading to child episodes and keeps editing
for itself. The parent lists the `spawn` tool, and `grants.spawn` names the
one child program it may start, `survey`. `programs.survey` is a complete
child configuration: its own instructions, tools, grants, and budget. It
omits `version`, `task`, `model`, and `sandbox`, which a child inherits. The
child's grants are a subset of the parent's; construction refuses a child
that reaches further than its parent.

## Paths to replace

- `/home/user/project`: a Python repository with `src/config.py` and
  `src/client.py`.
- `/home/user/.config/foe/anthropic.key`: a file whose whole contents are
  the API key.

## Run

```
foe --config examples/subagents/config.json
```

## The budget pool

Budget is one pool held by the root episode. The parent declares 40 model
calls, 400,000 tokens, and 1,800 seconds. When the parent spawns a child,
the child's declared budget, 8 calls and 60,000 tokens, is reserved from the
parent's remainder. While the child runs, the parent has 32 calls left to
spend. When the child settles, whatever it did not spend returns to the
parent. No path through the tree can spend more than the root declared.

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
{"seq": 9, "time": 1724200001000, "type": "budget/reserve", "data": { "child_id": "ep_9c21", "reserved": { "model_calls": 8, "tokens": 60000 } }}
{"seq": 10, "time": 1724200001001, "type": "spawn/start", "data": { "child_id": "ep_9c21", "program": "survey", "context": "fresh", "call_id": "tc_05" }}
```

The child's own log is at `children/ep_9c21/episode.jsonl` under the
parent's log directory; its `episode/start.parent_id` names the parent. When
the child finishes, the parent's log gains a `spawn/end` with the child's
outcome, a `budget/release` with what the child spent, and an `inbox/item`
with source `child` carrying the child's report, which enters the parent's
next request.

```json
{"seq": 31, "time": 1724200040000, "type": "budget/release", "data": { "child_id": "ep_9c21", "spent": { "model_calls": 5, "tokens": 38200 } }}
```

In the viewer, the left pane shows the parent with two children beneath it,
each with its own outcome, and the budget line counts the parent's calls
plus every reservation against 40.
