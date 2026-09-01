# Host protocol

A host is a process that launches foe and talks to it over standard input
and standard output. The Python package is a host. An orchestrator that runs
many episodes is a host. This document specifies the exchange completely.

The protocol has one design rule: foe's output stream is its log. Every line
foe writes to standard output is a log event, byte-identical to the line
appended to `episode.jsonl`. A host that reads standard output has read the
log. The host answers a small set of those events by writing lines to foe's
standard input, and foe records each answer as a further log event. No
exchange between foe and its host exists outside the log.

## Framing

Both directions carry one JSON object per line, terminated by a single line
feed. Lines never contain a raw line feed inside a string; JSON escaping
handles it. A reader treats a line that fails to parse as a fatal protocol
error and terminates the episode with `failed`.

## Launch

```
foe --config <path> --host [--log-dir <path>]
```

`--host` selects this protocol. Standard output then carries the log, and
standard input carries the host's answers. Without `--host`, standard output
carries one JSON line at the end, the outcome, so that a shell or another
program invoking foe reads a single result; the log still goes to the file.
The two modes are exclusive, and this document describes `--host` only.

The host supplies a configuration file. foe validates it, writes
`episode/start`, and begins. When the configuration has no `model` block,
the host supplies the model transport and must answer `model/request` events.
When the configuration has a `model` block, foe uses its built-in transport
and emits `model/request` events for the record only.

Standard error carries diagnostics for a person. A host never parses it.

## foe to host

Every log event, in `seq` order, as written. Two event types require a host
answer, and one ends the exchange.

### `model/request`

Emitted once per model call when the host supplies the transport. The host
performs the request and streams the response back as `model/chunk` lines,
ending with a `done` or `error` chunk.

```json
{"seq": 4, "time": 1724200000123, "type": "model/request", "data": {
  "step": 1, "attempt": 1, "request_id": "rq_01", "header_seq": 1,
  "consumed": [1], "messages": [ { "role": "user", "content": [ { "type": "text", "text": "…" } ] } ],
  "max_output_tokens": 2048
}}
```

The header referenced by `header_seq` carries the system prompt, the tool
schemas, and the model route. The host combines it with `messages` to form
the provider request. The host applies `max_output_tokens` as a provider
output cap. A host-specific cap may make it smaller. When the host supplies
the transport, the route's `provider` and `model` are both the word `host`.
The runtime does not know which model the host calls, and the log records
the route the runtime saw.

### `host/tool-call`

Emitted when the model calls a tool that the host registered. The host runs
the tool and answers with one `tool/result` line. The runtime has already
checked the arguments against the tool's declared `params` schema, so a call
that reaches the host conforms to the schema the host declared.

```json
{"seq": 9, "time": 1724200000456, "type": "host/tool-call", "data": {
  "step": 2, "call_id": "tc_03", "name": "mutation_usage", "args": { "mutation_id": "m_41" }
}}
```

### `episode/end`

Emitted last. The host reads the outcome and may close standard input. foe
exits with code 0 when the outcome is `completed`, 2 when `blocked`, 3 when
`exhausted`, and 1 when `failed`.

## Host to foe

Four line types. Any other `type` is a protocol error.

### `model/chunk`

One fragment of a streamed model response. The `request_id` must match an
outstanding `model/request`.

```json
{"type": "model/chunk", "request_id": "rq_01", "chunk": { "kind": "text", "delta": "I will" }}
```

| `kind` | fields | meaning |
|---|---|---|
| `text` | `delta` | a fragment of assistant text |
| `thinking` | `delta` | a fragment of reasoning text |
| `thinking_signature` | `signature` | closes the current reasoning block with the provider's replay token; at most one per block |
| `tool_call_start` | `id`, `name` | a tool call began |
| `tool_call_delta` | `id`, `delta` | a fragment of that call's JSON arguments |
| `tool_call_end` | `id` | that call's arguments are complete |
| `done` | `stop`, `usage` | the response ended; `stop` is `end`, `tool`, or `length` |
| `error` | `message`, `retryable` | the request failed; `retryable` tells foe whether to retry |

`usage` is `{ "input": N, "output": N, "cache_read": N }`. `input`
includes `cache_read`. `output` includes reasoning when the provider counts
reasoning as output. A host that cannot report a field sends 0.

An `error` chunk carries no usage. Provider work that ends in an error is
therefore outside the runtime's token account. Each retry still recomputes
its output cap from the allowance that remains.

foe records every chunk as an `assistant/chunk` event, assembles the
`assistant/message` on `done`, and applies the length rule when `stop` is
`length`. When a stream that has already begun a tool call ends without a
`done` chunk, because an `error` chunk arrived, because the host sent
`cancel`, or because the budget's `seconds` elapsed, foe writes the partial
`assistant/message` with `stop` set to `interrupted`. That is a fourth
value a reader of the log meets and a host never sends.

### `tool/result`

The result of a host tool call. The `call_id` must match an outstanding
`host/tool-call`.

```json
{"type": "tool/result", "call_id": "tc_03", "value": { "count": 3 }, "rendered": "3 references", "is_error": false}
```

`value` is the canonical result and is required. `rendered` is optional;
when absent, foe renders `value` compactly. `is_error` defaults to false.
foe records the answer as a `tool/result` event.

### `inbox/item`

A message for the episode. The host uses this to steer a running episode or
to deliver a team message.

```json
{"type": "inbox/item", "source": "parent", "content": [ { "type": "text", "text": "Stop after the first failing test." } ], "from": "ep_root", "message_id": null}
```

`source` is `parent`, `child`, or `peer`. foe records the line as an
`inbox/item` event and includes it in the next request.

### `cancel`

Stops the episode. foe aborts any outstanding request, closes every
obligation its log left open, writes `episode/end` with outcome `failed`
and error `cancelled`, and exits. Closing the obligations records started
tool calls as interrupted with synthetic results and sends `cancel` to
every child, so a cancelled tree stops from the root down.

```json
{"type": "cancel"}
```

## Ordering and concurrency

foe has at most one outstanding `model/request` at a time. It may have
several outstanding `host/tool-call` lines at once, when the calls' declared
effects permit concurrent execution. The host may answer them in any order.

The host may send `inbox/item` and `cancel` at any time, including while a
`model/request` is outstanding. An `inbox/item` that arrives during a request
enters the next request.

A `model/chunk` or `tool/result` that names an unknown or already-settled id
is a protocol error.

## Timeouts

foe waits for a `model/chunk` and for a `tool/result` up to the `seconds`
remaining in the episode's budget. When the budget's `seconds` elapse with
an answer outstanding, foe ends the episode as `exhausted` with limit
`seconds`, and the host tool call still waiting receives an error result
naming the tool. A `cancel` ends an outstanding wait the same way, with an
error result naming the tool and the reason.

A program that declares no `seconds` gives its waits no wall-clock bound.
Two rules keep such an episode from waiting on an answer that can never
arrive. A configuration with a `host_tools` entry is refused at
construction when the process has no host, because the tool named there has
no implementation to register. A call forwarded to a process with no host
is answered there with an error, which the [Children](#children) section
states.

What remains unbounded is a call a live host has received and not yet
answered. The host owes that answer. foe waits for it, and `cancel` is what
ends the wait when the host decides to give no answer. A program that means
to wait a long time therefore does one of two things: it declares a
`seconds` budget large enough for the longest answer it expects, or it
declares none and relies on its host to answer or to cancel.

## Children

A child episode is a further foe process. The parent foe process is the
child's host: it launches the child, reads the child's standard output, and
forwards the child's `model/request` and `host/tool-call` events to its own
host, tagged with the child's id. The root host therefore sees every request
in the tree and answers each one. Answers carry the same tag so that the root
parent can route them down.

The parent also passes read-only descriptors for every configured
executable reachable from the selected child program. A sealed manifest
associates each descriptor with its configuration key and digest. The child
constructs its identity and executor from those descriptors, so it never
reopens the parent's configured executable paths. It retains close-on-exec
copies and closes the inherited descriptor numbers before starting any model
transport, tool, verifier, or descendant.

```json
{"type": "model/chunk", "request_id": "rq_01", "episode_id": "ep_9c21", "chunk": {}}
```

`episode_id` is absent or null for the root episode. A host that does not
support children rejects configurations with a non-empty `spawn` grant; foe
treats the `spawn` grant as unavailable and fails any spawn tool call.

A process started without `--host` has no host to forward to. A
`host/tool-call` that reaches such a process, from its own child or from any
episode below it, is answered there with an error naming the tool, and the
answer carries the tag of the episode that made the call. Nothing above that
process could answer, so the episode that called learns at once rather than
waiting.

An episode sends `cancel` to every child still running when it ends,
whatever its outcome, and waits for each child's `episode/end` before
writing its own. On a host with delegated cgroup v2, the parent also empties
the child's recursive process boundary before it publishes settlement. A
detached descendant therefore cannot outlive the child reservation.

The parent writes the child's episode and task cgroup paths in
`lineage.json` beside the child log. These paths are runtime launch metadata.
They do not participate in program identity or enter the episode log. A
child enters the prepared boundary before its runtime code executes. The
child refuses lineage metadata whose episode path differs from its current
cgroup or whose task path lies outside the invocation hierarchy.

A child's `notify`, `send`, and `team` tools are host tools from the child's
point of view, and the parent foe process is the host that implements them.
A child's call to `notify` arrives at the parent as a `host/tool-call`; the
parent appends an `inbox/item` with source `child` to its own log and
answers with a `tool/result`. A call to `send` arrives the same way at the
lead, which appends `team/message` to its log, delivers the message to the
target member as an `inbox/item` with source `peer`, and appends
`team/delivered` when the member's log has recorded it. These calls are
never forwarded above the parent. A message from a member therefore reaches
the lead's log through the same two line types every other host exchange
uses.

## Versioning

The first line foe writes is the `episode/start` event, and its
`runtime.version` identifies the protocol version. A host that does not
recognize the version sends `cancel` and reports the mismatch.
