# Log format

Every episode writes one append-only log. The log is the source of truth for
the episode: the model's request history, the viewer, replay, forking,
budget accounting, and team state are all derived from it. This document
specifies the log completely. Nothing that reaches a model request may exist
outside it.

Stability: the event envelope, the event types marked implemented, and the
seeding rules are frozen at version 1. Adding an event type is compatible.
Changing an existing type's data requires a new log version.

## Directory layout

```
<episode-dir>/
  episode.jsonl          the log
  spill/                 tool output too large to inline, named by call id
  children/<child-id>/   child episodes, each with this same layout
```

An episode directory is self-contained. Copying it copies everything needed
to view, replay, or fork the episode and its descendants.

## Envelope

One JSON object per line.

```json
{"seq": 17, "time": 1724200000123, "type": "tool/result", "data": {}}
```

| field | type | meaning |
|---|---|---|
| `seq` | integer | position in the log, starting at 0, contiguous |
| `time` | integer | milliseconds since the Unix epoch |
| `type` | string | event type, from the list below |
| `data` | object | the event payload |

Every `data` value is JSON that round-trips byte-for-byte. A writer validates
this before appending.

## Event types

Status is one of: **implemented** in version 1, or **reserved**, meaning the
type is defined so that future logs remain readable by version 1 tools, and
nothing emits it.

### Lifecycle

`episode/start` — implemented. Always `seq` 0. Exactly one per log.

```json
{
  "id": "ep_8f3a",
  "parent_id": null,
  "fork_origin": null,
  "team_id": null,
  "program": {},
  "identity": "sha256:…",
  "task": "Fix the failing parser test.",
  "runtime": { "version": "0.1.0", "build": "sha256:…" },
  "sandbox": { "mode": "best-effort", "landlock_abi": 7 }
}
```

`parent_id` names the spawning episode. `fork_origin` is
`{ "episode_id": "…", "seq": N }` when the log was seeded from a prefix of
another log. `team_id` names the lead episode when this episode is a team
member. `program` is the resolved configuration with `task` removed.
`landlock_abi` is 0 when Landlock was unavailable.

`episode/end` — implemented. Always the last event.

```json
{ "outcome": { "kind": "completed", "value": {} } }
```

The outcome is one of:

```json
{ "kind": "completed", "value": ANY }
{ "kind": "blocked",   "code": "looping-tool-call", "message": "…" }
{ "kind": "exhausted", "limit": "tokens" }
{ "kind": "failed",    "error": "…" }
```

`seed/end` — implemented. Marks the end of events copied from another log.
Data is empty. Present only in a seeded log, where it follows the copied
events and precedes the first live event.

### Requests

`request/header` — implemented. The parts of a request that change rarely:
the system prompt, the tool schemas, and the model route. Written with
`reason: "initial"` before the first request, and with `reason: "change"`
before any request whose header differs from the previous one. Not written
when unchanged.

```json
{
  "reason": "initial",
  "system": "…",
  "tools": [ { "name": "read", "description": "…", "parameters": {} } ],
  "model": { "provider": "anthropic", "model": "claude-opus-5" }
}
```

`model/request` — implemented. One per model call. Carries the messages the
model received and a pointer to the header in effect.

```json
{
  "step": 3,
  "attempt": 1,
  "request_id": "rq_01",
  "header_seq": 1,
  "consumed": [2, 9],
  "messages": []
}
```

`consumed` lists the `seq` of every `inbox/item` that entered this request
for the first time. `messages` is the full derived message list, in the
form defined under [Derived messages](#derived-messages).

`request/retry` — implemented. A model request failed and will be retried.

```json
{ "step": 3, "attempt": 1, "cause": "transport", "delay_ms": 500 }
```

`cause` is one of `transport`, `rate-limit`, `provider`, `interrupted`.

### Assistant output

`assistant/chunk` — implemented. One streamed fragment. Kept so that a replay
reproduces the stream and the viewer reproduces token-level timing.

```json
{ "step": 3, "request_id": "rq_01", "chunk": { "kind": "text", "delta": "I will" } }
```

Chunk kinds: `text`, `thinking`, `tool_call_start` with `id` and `name`,
`tool_call_delta` with `id` and `delta`, `tool_call_end` with `id`.

`assistant/message` — implemented. The assembled response for one request.

```json
{
  "step": 3,
  "request_id": "rq_01",
  "text": "I will read the test first.",
  "tool_calls": [ { "id": "tc_01", "name": "read", "args": { "path": "tests/parser_test.py" } } ],
  "stop": "tool",
  "usage": { "input": 4120, "output": 88, "cache_read": 3900 },
  "interrupted": false
}
```

`stop` is one of `end`, `tool`, `length`, `interrupted`. When `stop` is
`length`, every tool call in the message receives a `tool/result` with
`is_error: true` and no execution. When `interrupted` is true, the text is
the prefix that arrived before the failure.

### Tools

`tool/result` — implemented. Exactly one per tool call, matched by `call_id`.

```json
{
  "step": 3,
  "call_id": "tc_01",
  "name": "read",
  "value": {},
  "rendered": "1\timport pytest\n…",
  "is_error": false,
  "spill": null,
  "duration_ms": 4,
  "synthetic": false
}
```

`value` is the canonical result. `rendered` is the text the model received.
`spill` names a file under `spill/` when the canonical value was too large to
inline; the inlined `value` is then a locator object. `synthetic` is true
when the result was written by the seeding step or by request failure
recovery rather than by running the tool.

`host/tool-call` — implemented. A tool call that resolves to a host tool,
emitted so that the host can execute it. The result arrives as an ordinary
`tool/result`.

```json
{ "step": 3, "call_id": "tc_02", "name": "mutation_usage", "args": {} }
```

### Inbox

`inbox/item` — implemented. One message addressed to this episode. The task
itself is the first inbox item.

```json
{
  "source": "task",
  "content": [ { "type": "text", "text": "Fix the failing parser test." } ],
  "from": null,
  "message_id": null
}
```

`source` is one of:

| source | producer |
|---|---|
| `task` | the launcher; exactly one per episode, at `seq` 1 |
| `parent` | the spawning episode, steering this one |
| `child` | a child episode, notifying this one |
| `peer` | a team member, via the lead's queue; `from` and `message_id` are set |
| `verify` | the runtime, carrying findings from a `done_when` verifier |
| `system` | the runtime, for text it must show the model, such as a budget warning |

The values `request` and `response` are reserved for correlated exchanges.

### Budget and spawn

`budget/reserve` — implemented. A child's budget was reserved from this
episode's remainder.

```json
{ "child_id": "ep_9c21", "reserved": { "model_calls": 6, "tokens": 60000 } }
```

`budget/release` — implemented. A child settled and returned its unspent
reservation.

```json
{ "child_id": "ep_9c21", "spent": { "model_calls": 4, "tokens": 41200 } }
```

`spawn/start` — implemented.

```json
{ "child_id": "ep_9c21", "program": "survey", "context": "fresh", "call_id": "tc_05" }
```

`context` is `fresh` or `fork`. `call_id` names the tool call that spawned the
child.

`spawn/end` — implemented.

```json
{ "child_id": "ep_9c21", "outcome": { "kind": "completed", "value": {} } }
```

### Teams

These events appear only in a lead's log.

`team/roster` — implemented. Written on every change to a member's phase.

```json
{ "member_id": "ep_a1", "name": "reviewer", "description": "…", "phase": "active" }
```

`phase` is one of `provisioning`, `active`, `failed`.

`team/message` — implemented. A message queued for delivery.

```json
{ "message_id": "tm_07", "from": "ep_a1", "to": "ep_b2", "content": [] }
```

`team/delivered` — implemented. The target recorded the message in its own
log.

```json
{ "message_id": "tm_07", "to": "ep_b2" }
```

Messages with a `team/message` and no matching `team/delivered` are
redelivered when the target restarts. The target deduplicates by
`message_id`.

`team/task` — reserved, for a shared task board.

### Sandbox

`sandbox/denied` — implemented. An access the kernel refused, captured from
the audit log when the kernel supports it.

```json
{ "pid": 4120, "comm": "ruff", "path": "/etc/shadow", "access": "read" }
```

### Reserved

`compaction/start`, `compaction/summary`, `compaction/end` — context
summarization.

`workflow/node-start`, `workflow/node-end`, `workflow/recovery` — declared
dataflow graphs.

## Derived messages

The message list for a request is computed from the log by one rule, applied
by the runtime, the viewer, and the Python package identically.

1. Begin with an empty list.
2. Walk events in `seq` order.
3. An `inbox/item` whose `seq` appears in the `consumed` list of the request
   being built becomes a `user` message. Consecutive items merge into one
   message with concatenated content blocks.
4. An `assistant/message` with `interrupted: false` becomes an `assistant`
   message carrying its text and tool calls.
5. An `assistant/message` with `interrupted: true` becomes an `assistant`
   message carrying its text, with `tool_calls` as recorded.
6. Each `tool/result` becomes a `tool` message carrying `rendered`.
7. Events of any other type contribute nothing.

An assistant message whose request failed and was discarded before any tool
call started is never written, so it never appears.

The message list is exactly what `model/request.messages` records. A reader
that recomputes it from the preceding events and finds a difference has found
a runtime defect.

## Seeding

A new log may begin with a prefix copied from an existing log. Forking and
replay both use this.

Given a source log and a boundary `seq` N:

1. Write a fresh `episode/start` at `seq` 0 with a new `id`, `fork_origin`
   set to the source episode and N, and all other fields copied.
2. Copy source events with `seq` in `[1, N)`, renumbering `seq` to be
   contiguous.
3. For every tool call in a copied `assistant/message` that has no
   `tool/result` among the copied events, append a `tool/result` with
   `is_error: true`, `synthetic: true`, and a rendered text stating that the
   result was not recorded.
4. Append `seed/end`.
5. Continue with live events.

Copied `team/*` events belong to the source episode and are excluded from the
new episode's team fold. A fold reads team events only when the log's own
`episode/start.team_id` matches or the log is itself the lead.

A replay is a seed at N equal to the source log's length, with the model
responses replayed from `assistant/chunk` events rather than requested.

## Blocked codes

The `code` of a `blocked` outcome is one of the following. The list is closed
in version 1. A supervising episode routes on it.

| code | meaning |
|---|---|
| `looping-tool-call` | the same call with the same result repeated across consecutive steps |
| `looping-reasoning` | the same assistant text repeated across consecutive steps |
| `goal-unreachable` | the model reported that the task cannot be completed as stated |
| `ambiguous-task` | the model reported that the task admits incompatible readings |
| `missing-capability` | the task needs a tool or grant the program lacks |
| `verification-unsatisfiable` | `done_when` retries were spent with findings still present |
| `child-blocked` | a child episode was blocked and the parent cannot proceed |
| `recovery-exhausted` | request retries were spent |

The model reports `goal-unreachable`, `ambiguous-task`, and
`missing-capability` by calling the built-in `block` tool with the code and a
message. The runtime detects the rest.

## Exhausted limits

The `limit` of an `exhausted` outcome is one of `model_calls`, `tokens`,
`seconds`, `depth`, `episodes`, `concurrency`.

## Size

An episode log grows with its conversation. `assistant/chunk` events
approximately double the stored assistant text. `model/request.messages`
repeats the derived history on every request. Both are accepted costs of
making the log complete without a reader having to implement the derivation.
A log reader that wants the compact form reads `assistant/message` and
`tool/result` and ignores the rest.
