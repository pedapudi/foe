# Log format

Every episode writes one append-only log. The log is the source of truth for
the episode: the model's request history, the viewer, replay, forking,
budget accounting, and team state are all derived from it. This document
specifies the log completely. Nothing that reaches a model request may exist
outside it.

Stability: the event envelope, the required fields of implemented event
types, and the seeding rules are frozen at version 3. Adding an event type
or an optional field with a reader default is compatible. Removing or
changing a required field requires a new log version.

## Directory layout

```
<episode-dir>/
  episode.jsonl          the log
  spill/                 canonical values and complete result renderings too large to inline
    renderings/          complete renderings, named by their SHA-256 digest
  children/<child-id>/   child episodes, each with this same layout
```

An episode directory is self-contained. Copying it copies everything needed
to view, replay, or fork the episode and its descendants.

## Writers

Each log has exactly one writer: the process running that episode. A parent
writes its own log and never a child's. A team member never appends to the
lead's log; a message from a member reaches the lead over the host protocol,
and the lead's process appends the `team/message` event. Concurrent episodes
therefore never contend for a file, and a reader tailing many logs needs no
lock.

The writer appends each event with a single write call and flushes it before
echoing the same bytes to standard output. It forces the file to disk after
`episode/start`, after every `tool/result` whose tool declared an effect
other than `pure` or `reads`, and before `episode/end`. A crash between
those points loses at most the events since the last forced write, and the
seeding rules repair whatever the lost events would have closed.

An append error can occur after the file or its mirror received the bytes.
The caller therefore treats the log as interrupted. Child launch ordering
ensures that an append error before an owning event returns leaves no child
process or budget reservation to settle.

## Envelope

One JSON object per line.

```json
{"seq": 17, "time": 1724200000123, "type": "tool/result", "data": {}}
```

| field | type | meaning |
|---|---|---|
| `seq` | integer | position in the log, starting at 0, contiguous |
| `time` | integer | milliseconds since the Unix epoch |
| `version` | integer | the log format version; stated on the first event, absent after |
| `type` | string | event type, from the list below |
| `data` | object | the event payload |

Every `data` value is JSON that round-trips byte-for-byte. A writer validates
this before appending.

The first event states `version`, the format version of the log; later
events state none. A log whose first event states no version is a version 3
log: version 3 writers are the first to state the version, so absence
identifies a version 3 log written before they did. A reader that finds a
stated version it does not read refuses the log with an error naming both
versions. It reads the version from the first line before parsing events,
so the refusal does not depend on knowing the shapes a later version
writes. The field is optional with a reader default, which the stability
rule above admits into the frozen version 3 envelope.

## Event types

Status is one of: **implemented** in version 3, or **reserved**, meaning the
type is defined so that future logs remain readable by version 3 tools, and
nothing emits it.

### Lifecycle

`episode/start` — implemented. Always `seq` 0. Exactly one per log.

```json
{
  "id": "ep_8f3a",
  "parent_id": null,
  "fork_origin": null,
  "team_id": null,
  "contract": {},
  "contract_fingerprint": "sha256:…",
  "task": "Fix the failing parser test.",
  "runtime": { "version": "0.2.0", "build": "sha256:…" },
  "sandbox": {
    "mode": "best-effort",
    "landlock_abi": 7,
    "resolved_permissions": {
      "read": [
        { "path": "/lib", "reason": "shared-library lookup" }
      ],
      "execute": [
        {
          "path": "/opt/tools/check",
          "reason": "selected configured tool contract.tool_defs.check",
          "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        }
      ],
      "connect_tcp": ["configured model endpoint in contract"]
    },
    "process_boundary": {
      "kind": "cgroup-v2",
      "subtree_cleanup": "enforced"
    }
  },
  "effective_budget": {
    "model_calls": 20,
    "input_tokens": 160000,
    "output_tokens": 40000,
    "seconds": 900,
    "max_depth": 1,
    "max_episodes": 4,
    "max_concurrent": 2,
    "loop_threshold": 8
  }
}
```

`parent_id` names the spawning episode. `fork_origin` is
`{ "episode_id": "…", "seq": N }` when the log was seeded from a prefix of
another log. `team_id` names the lead episode when this episode is a team
member. `contract` is the resolved configuration with `task` removed.
`landlock_abi` is 0 when Landlock was unavailable. `resolved_permissions`
records the filesystem and network permissions compiled for the episode.
Each filesystem entry gives the reason it is present. A captured executable
entry may include the SHA-256 digest of its construction-time bytes.
`connect_tcp` gives every reason outbound TCP remains available.

The configured path identifies the source that Foe captured. The digest
identifies the bytes that later invocations use. Foe does not reopen that
source after contract construction.

`mode` and `landlock_abi` state the filesystem and network enforcement the
host provided. `process_boundary` states whether descendant-process cleanup
is enforced by cgroup v2 or observed through a process group. Its optional
`reason` explains why cgroup ownership was unavailable.

`effective_budget` is the allowance the episode enforces. Its fields have the meanings and defaults in
[config.md](config.md#budget). A spawned episode can have an effective
allowance below `contract.budget`; this leaves `contract_fingerprint`
unchanged. On resume, the recorded allowance takes precedence over the
mutable launch metadata beside the log.

`episode/end` — implemented. Always the last event.

```json
{ "outcome": { "kind": "completed", "value": {} } }
```

The outcome is one of:

```json
{ "kind": "completed", "value": ANY }
{ "kind": "blocked",   "code": "looping-tool-call", "message": "…" }
{ "kind": "exhausted", "limit": "input_tokens" }
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
  "messages": [],
  "max_output_tokens": 2048
}
```

`consumed` lists the `seq` of every `inbox/item` that entered this request
for the first time. `messages` is the full derived message list, in the
form defined under [Derived messages](#derived-messages).
`max_output_tokens` is the cap the model route receives after the runtime has
considering the remaining episode-wide output allowance. It is omitted when
neither the configuration nor the budget supplies a cap. A provider can lack
an equivalent request field; [models.md](models.md) records those cases.

`request/retry` — implemented. A model request failed and is being retried.
`attempt` names the attempt that failed and `delay_ms` the delay waited
before the next one. The event is written immediately before the
`model/request` of that next attempt, so a retry is never recorded for an
attempt that is not made; see [Open obligations](#open-obligations).

```json
{ "step": 3, "attempt": 1, "cause": "transport", "delay_ms": 500 }
```

`cause` is one of `transport`, `rate-limit`, `provider`, `interrupted`.
`interrupted` names a request that failed after text had arrived and before
any tool call had started; the partial text is discarded and the request is
retried. A request that fails after a tool call started is never retried:
it is recorded as an `assistant/message` with `interrupted: true`, and the
next step continues from there.

### Assistant output

`assistant/chunk` — implemented. One streamed fragment. Kept so that a replay
reproduces the stream and the viewer reproduces token-level timing.

```json
{ "step": 3, "request_id": "rq_01", "chunk": { "kind": "text", "delta": "I will" } }
```

Chunk kinds: `text`, `thinking`, `thinking_signature` with `signature`,
`tool_call_start` with `id` and `name`, `tool_call_delta` with `id` and
`delta`, `tool_call_end` with `id`. A `thinking_signature` chunk closes the
current reasoning block with the provider's replay token; providers that
issue none never send it.

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

The message also carries `thinking`, a list of reasoning blocks
`{ "text": "…", "signature": "…" }` assembled from `thinking` and
`thinking_signature` chunks in order, and omitted when empty. The caller
replays these blocks to the same model route, where a provider may require
them for a turn that continues after a tool call. The caller omits them for
any other route.

`usage.input` includes cache-read input. `usage.output` includes reasoning
when the provider reports reasoning inside its output count.

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
  "subject": "src/parser.rs lines 1–6 of 42",
  "duration_ms": 4,
  "synthetic": false
}
```

`value` is the canonical result, kept whole. `rendered` is the text the
model received, which the turn budget may have shortened: the results of one
model turn share a character budget, and a rendering over its part of that
budget ends with a notice stating what was removed and naming the call that
shows it. [tools.md](tools.md#the-turn-budget) specifies the division and
the notice. The cut is applied before the event is appended, so the
rendering in the log is the rendering every request carries, and no earlier
event is ever rewritten. A reader that wants the whole result reads
`value`.

An error result written by the runtime has `is_error: true` and a
`failure` object:

```json
{
  "code": "capability-denied",
  "message": "read: /private is outside this tool's filesystem permissions; review grants.read and grants.write",
  "retryable": false,
  "details": { "path": "/private" }
}
```

The code vocabulary and retry rule are specified in
[tools.md](tools.md#failures). The message is explanatory text. Runtime
decisions use the other fields. Successful results omit `failure`. The
field is optional when reading so logs written before typed failures remain
valid. An older error without the field retains its `is_error` value.

When `done_when.returns` requires `learned`, `rendered` starts with
`[seq N]`, where N is this event's `seq`. A `learned` observation cites that
number. The prefix is runtime metadata and lies outside any complete
tool-owned rendering stored by `tool/rendering-archive`.

`subject` is one line the tool writes after it has run, naming what the
call acted on and what came of it. It differs from `rendered` in who reads
it: `rendered` is what the model received, and `subject` is what a person
reads in a list of calls. The tool is the only thing that can state it,
because the arguments say what was attempted and only the tool knows the
outcome, so on failure the subject names what failed. It is one line of at
most 120 characters, held to that where the field is written — a line past
the limit ends in an ellipsis, so a cut is never silent — and it never
reaches the model: it appears in no tool schema and nowhere in the system
prompt. The field is optional and absent when a tool states none, which is
the case for host tools and for every log written before tools stated it.

`spill` names a file under `spill/` when the canonical value was too large to
inline; the inlined `value` is then a locator object, and `rendered` states
the file and carries the rendering. `synthetic` is true
when the result was written by the seeding step or by request failure
recovery rather than by running the tool: a call left without a result when
the episode was interrupted receives a result with `synthetic: true` and
`is_error: true`. The rejection of every call in a response that hit the
output length limit is `is_error: true` with `synthetic: false`, because the
runtime produced that result in the ordinary course of the step. At episode
settlement the runtime also writes one result with `synthetic: true` for
each surviving process session. An episode-lifetime result records the
implicit stop. A task-lifetime result records that the process group had a
live member when ownership passed to the enclosing task
environment. The group leader may already have exited. The result carries
`lifetime: "task"`, `disposition: "released_to_task_environment"`, `pid`,
and `process_group`. Its `call_id` names no call, so it closes nothing; see
[Open obligations](#open-obligations).

`tool/rendering-archive` — implemented. The turn budget shortened one tool
rendering. This event immediately precedes that call's `tool/result`.

```json
{
  "step": 3,
  "call_id": "tc_01",
  "file": "renderings/8d969eef6ecad3c29a3a629280e686cff8ca…txt",
  "digest": "sha256:8d969eef6ecad3c29a3a629280e686cff8ca…",
  "bytes": 54830
}
```

The file contains the complete UTF-8 rendering before the turn budget cut
it. Its name is `renderings/<digest-hex>.txt` under `spill/`. Equal
renderings in one episode share one file. The writer synchronizes the file
before appending this event. The event carries the digest of the file bytes
and their length.

A reader verifies the length and SHA-256 digest before using the archive.
The file path must be the content-addressed name derived from `digest`.
`tool/result.rendered` remains the text that entered the model context.
Archive events contribute nothing to derived messages.

`host/tool-call` — implemented. A tool call that resolves to a host tool,
emitted so that the host can execute it. The result arrives as an ordinary
`tool/result`.

```json
{ "step": 3, "call_id": "tc_02", "name": "mutation_usage", "args": {} }
```

`tool/inner-call` — implemented. One inner dispatch a composing tool
performed through the registry while its own model-issued call ran. The
built-in `python` tool is the one composing tool;
[code-mode.md](code-mode.md) specifies it, and the generic event name is
shared by design with any future composing tool.

```json
{
  "outer_call_id": "tc_01",
  "call_id": "tc_01_4",
  "index": 4,
  "name": "read",
  "args": { "path": "src/parser.rs" }
}
```

`outer_call_id` names the model-issued call being composed. `call_id` is
the inner call's own id; `index` counts the outer call's inner dispatches
from 0. The event opens the same tool-call obligation a call in an
`assistant/message` opens, and the inner call's ordinary `tool/result`,
which follows with the inner `call_id` and the outer call's step, closes
it. That result never enters derived messages: the outer result alone
reaches the model. A host tool dispatched this way also produces its
`host/tool-call` event as usual. The event is additive; no frozen
version 3 payload changed for it, and a reader compiled before the
variant existed rejects a log that carries it.

### Verification

`verification/result` — implemented. One authoritative verifier
invocation, exactly one event per invocation: the accepted run that
completes the work, a findings run that re-fires it, or a failed run.
The episode's `done_when.verify` writes it in that episode's own log, and
a workflow node's `verify` writes it in the workflow episode's log with
the node's step context. The event contributes nothing to derived
messages; the model sees findings only through the `verify` inbox item.

```json
{
  "step": 3,
  "tool": "check",
  "verifier_fingerprint": "sha256:…",
  "status": "findings",
  "findings": ["tests/parser_test.py::test_nested_brackets fails"],
  "candidate_sha256": "sha256:…",
  "duration_ms": 412
}
```

`status` is one of `accepted`, `findings`, and `failed`. `findings` holds
the same strings the `verify` inbox item carries, and is empty for
`accepted` and for `failed`. `error` is present only for `failed` and
states why the verifier could not judge; the episode then ends as
`failed`. `verifier_fingerprint` identifies what ran. For a `tool_defs`
executable it is the SHA-256 digest committed during contract construction.
Every invocation uses those committed bytes. For a built-in or host tool it
is the runtime build hash from `episode/start.runtime.build`.

`candidate_sha256` records what was judged: `sha256:` followed by the
SHA-256 digest of the canonical JSON — compact, object keys sorted, raw
UTF-8 — of the exact candidate value handed to the verifier. The runtime writes
it on every verification it records. The field is additive to the frozen
format: a log written before it existed omits it, a reader treats
absence as no claim about what was judged, and serialization omits an
absent field, so no frozen version 3 payload changed.

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
| `session` | the runtime, when it observes that a process session's process has ended |

When the current pool has one model call left for an ordinary request, the
runtime appends one `system` item before deriving that request. The content
directs the model toward the highest-priority unfinished work and the
configured completion signal. The request records the item's sequence in
`consumed`. The item changes no budget and completes no episode.

A `session` item is written once per session lifetime, on exit only: its
text is the session subject line — the id, the exit status, and the
lifetime — and `from` is the session id. The runtime observes exits before
deriving a request, while a turn's tool calls run, and at settlement; a
session's output never enters the inbox. The value is additive to the
frozen format: the `inbox/item` payload is unchanged, and a reader compiled
before the value existed rejects a log that carries it, as for an added
event type.

The values `request` and `response` are reserved for correlated exchanges.

### Budget and spawn

`budget/reserve` — implemented. A child's budget was reserved from this
episode's remainder.

```json
{ "child_id": "ep_9c21", "reserved": { "model_calls": 6, "input_tokens": 48000, "output_tokens": 12000, "episodes": 3 } }
```

`budget/release` — implemented. A child settled and returned its unspent
reservation.

```json
{ "child_id": "ep_9c21", "spent": { "model_calls": 4, "input_tokens": 38400, "output_tokens": 2800, "episodes": 2 } }
```

An amount holds five optional fields: `model_calls`, `input_tokens`,
`output_tokens`, `seconds`, and `episodes`. An absent field means the
dimension is unlimited. In `reserved`, `episodes` is how many episodes the
child's subtree may hold, the child itself included. It is the child's
`budget.max_episodes` after the parent's share is applied. In `spent`,
`episodes` is how many episodes that subtree held, so a leaf child reports
one. A parent adds each released count to a lifetime total that never
returns to the pool. This is how one `max_episodes` bounds a whole tree.

`spawn/start` — implemented.

```json
{ "child_id": "ep_9c21", "contract": "survey", "context": "fresh", "call_id": "tc_05" }
```

`context` is `fresh` or `fork`. `call_id` names the tool call that spawned the
child. The parent appends this event before creating the child process. A
process-creation failure is recorded as a failed `spawn/end`, followed by the
matching `budget/release`.

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

`sandbox/denied` — reserved. An access the kernel refused, captured from
the audit log. Reading Landlock audit records requires the `CAP_AUDIT_READ`
capability or access to the audit daemon's log, which an unprivileged foe
process does not have, so nothing emits this event in version 3. The type
is defined so that a privileged deployment or a later version can emit it
and a version 3 reader will render it.

```json
{ "pid": 4120, "comm": "ruff", "path": "/etc/shadow", "access": "read" }
```

### Workflows

These events appear in the log of an episode whose configuration declares
a `workflow`, which [workflow.md](workflow.md) specifies. Each firing of a
model node is a child episode under `children/` with its own log. A node
inside a nested workflow node is named by its path, `outer/inner`.

`workflow/node-start` — implemented. One firing of a node begins. `fire`
counts the node's firings from 1. `inputs` lists the `seq` of the events
that produced the values the node receives: the `workflow/node-end` of
each predecessor, the `workflow/recovery` that skipped one, or the
`inbox/item` at seq 1 for the built-in `task` source. `child_id`
names the child episode of a model node. It is absent when the model node's
predecessor sections exceed the 50,000-character rendered-predecessor bound in
[workflow.md](workflow.md#bounded-model-handoffs). That firing ends before a
child is allocated.

```json
{ "node": "survey", "fire": 1, "inputs": [4], "child_id": "ep_9c21" }
```

`workflow/node-end` — implemented. The firing ended. `value` is the node's
canonical output and `rendered` the text its successors receive. When the
firing failed, `error` states why and `value` is null. A typed failure also
appears in `failure`, using the structure specified for `tool/result`. An
optional model node whose child ended blocked or exhausted is the one
exception: `error` states the child outcome, and `value` and `rendered` carry
the node's declared `empty` output.

An oversized model handoff records a failed end for the receiving model node.
The log retains complete predecessor values and renderings. A completed
predecessor carries them in its node-end event. A skipped predecessor derives
them from the contract's declared `empty` value and its recovery event. The
receiving end's error names the producing events and their rendered sizes.

```json
{ "node": "survey", "fire": 1, "value": {}, "rendered": "…", "duration_ms": 1200 }
```

`workflow/branch` — implemented. A node with `branches` chose a label.

```json
{ "node": "propose", "fire": 1, "label": "accept", "successors": ["derive"] }
```

`workflow/recovery` — implemented. A recovery decision was made and applied.
`cause` names what failed, `action` is `retry`, `amend`, `skip`, or
`abort`, `target` names the node a retry or amend re-fires, `note` carries
the text an amend appends, and `intervention` counts decisions in this
episode from 1. When a typed failure triggered recovery, `failure` retains
its code, message, retryability, and structured details.

```json
{ "node": "derive", "fire": 1, "cause": "operation-failed", "action": "retry", "target": "survey", "intervention": 1 }
```

### Compaction

These events appear when an episode's `context` block enables compaction
and the projected size of the next request crosses the threshold.
[compaction.md](compaction.md) specifies the policy; this section specifies
the record. Between `compaction/start` and `compaction/summary` lie a
`request/header` carrying the summarization prompt with no tools, a
`model/request` whose `request_id` starts with `cmp_`, its chunks, and its
`assistant/message`, all in the ordinary forms. A `request/header` restoring
the step's own header follows `compaction/end`.

`compaction/start` — implemented. `covered` is the span this compaction
summarizes directly: from the previous compaction's `first_kept_seq`, or 1,
to the event before the cut. `projected_tokens` is the projection that
crossed the threshold. `reserved` is the budget the episode had left, which
the summarization call draws on.

```json
{
  "step": 14,
  "covered": { "first_seq": 1, "last_seq": 57 },
  "trigger": "threshold",
  "projected_tokens": 186240,
  "reserved": { "model_calls": 22, "input_tokens": 280000, "output_tokens": 30400 }
}
```

`compaction/summary` — implemented. `summary` is the model's narrative.
`state` is built from typed events: the task verbatim, the completion
condition in one line, the latest verifier report, the file lists, the
children that ended in the covered span, the covered range, and the budget
remaining. `first_kept_seq` is the `seq` of the `model/request` that opens
the kept suffix. `summary_request_seq` names the `cmp_` request.

```json
{
  "step": 14,
  "summary": "## Goal\n…",
  "state": {
    "task": "Fix the failing parser test.",
    "done_when": "a turn with no tool calls",
    "outstanding_findings": [],
    "files": { "read": ["src/parser.py"], "written": [], "edited": ["src/parser.py"] },
    "children": [],
    "covered": { "first_seq": 1, "last_seq": 57 },
    "budget_remaining": { "model_calls": 22, "input_tokens": 280000, "output_tokens": 30400 }
  },
  "first_kept_seq": 58,
  "summary_request_seq": 61
}
```

`compaction/end` — implemented. `usage` is what the summarization response
reported, zero when none arrived. `active_estimate` is the estimated token
count of the request that follows. When `ok` is false, `error` states why,
no `compaction/summary` was written, and the projection is unchanged.

```json
{ "step": 14, "ok": true, "usage": { "input": 150200, "output": 610, "cache_read": 0 }, "active_estimate": 21800 }
```

## Open obligations

Six pairs of event types stand in a fixed relation: one event opens an
obligation and a later event closes it, the two matched by a key.

| opened by | closed by | key |
|---|---|---|
| a tool call in `assistant/message`, or `tool/inner-call` | `tool/result` | the call id |
| `request/retry` | the `model/request` of the attempt it announces | the step and that attempt's number |
| `compaction/start` | `compaction/end` | the step |
| `spawn/start` | `spawn/end` | the child id |
| `budget/reserve` | `budget/release` | the child id |
| `team/message` | `team/delivered` | the message id |

Three rules hold for every pair.

1. An event that closes an obligation names one that an earlier event
   opened.
2. It closes that obligation once. A second closing event for the same key
   is invalid until an opening event reopens it, which a reissued tool call
   does.
3. A log whose `episode/end` leaves an obligation open is invalid.

A queued team message is the one pair the three rules do not bind. A
`team/message` with no `team/delivered` is a message the target never
recorded. The lead offers such a message again when the target restarts,
which records a second delivery for one message, and no event records a
message given up on. An undelivered message may therefore stand at
`episode/end`, and a message may be delivered more than once.

One closing-shaped event is exempt from the first rule: a `tool/result`
with `synthetic: true` whose call id names no call closes nothing and
opens nothing. It is the runtime's account of work it settled itself: the
implicit stop or task-environment release of a process session surviving at
settlement. Only the runtime writes synthetic results. The second rule still binds it: a call
already closed cannot be closed again.

A log that stops without `episode/end` is a different record from a log
that ends with an obligation open. The first was cut short: the process
died between two appends, the log states nothing about what it left open,
and the seeding rules below repair it when the log is copied. The second
asserts a complete episode whose account does not balance, which no writer
may produce. A writer that would leave an obligation open closes it first,
with the events under [Seeding](#seeding).

A `request/retry` is the pairing with no repair, because no event records
an attempt that was never made. A writer therefore appends `request/retry`
only immediately before the `model/request` it announces.

## Derived messages

The message list for a request is computed from the log by one rule, applied
by the runtime, the viewer, and the Python package identically.

1. Begin with an empty list.
2. Walk events in `seq` order.
3. A `model/request` contributes one `user` message built from the
   `inbox/item` events its `consumed` list names, in the order listed, with
   their content blocks concatenated. The message is placed at the request's
   position, so an item received while an earlier request was in flight
   appears after that request's assistant message. An `inbox/item` is
   written when it is received and never moved; its `seq` records arrival
   and its consumption records order.
4. An `assistant/message` with `interrupted: false` becomes an `assistant`
   message carrying its text and tool calls.
5. An `assistant/message` with `interrupted: true` becomes an `assistant`
   message carrying its text, with `tool_calls` as recorded.
6. Each `tool/result` becomes a `tool` message carrying `rendered`, except
   one whose opening record is a `tool/inner-call`: an inner result
   contributes nothing, because the outer composing call's result is the
   only account of the contract that reaches the model.
7. Events of any other type contribute nothing.

A `model/request` whose `request_id` starts with `cmp_`, and the
`assistant/message` answering it, contribute nothing: they are a
summarization exchange, and the request records its prompt rather than a
derived list.

After the latest `compaction/summary` before the request, rule 2 changes.
The list opens with one `user` message holding `state.task` verbatim and
one `user` message holding the continuation message, which
[compaction.md](compaction.md) renders from `state` and `summary`. The walk
then begins at the summary's `first_kept_seq`. An `inbox/item` below that
boundary still contributes through a request at or above it that names
the item in `consumed`, as rule 3 provides.

An assistant message whose request failed and was discarded before any tool
call started is never written, so it never appears.

A workflow episode's own requests are recovery decisions, and each one is
built from declared inputs alone: its `messages` hold the one `user`
message made from the `system` inbox item it consumes, and the assistant
messages and tool results of earlier decisions in the same log are
excluded. A reader derives such a request by applying rule 3 to that
request only.

The message list is the one `model/request.messages` records. A reader
that recomputes it from the preceding events and finds a difference has found
a runtime defect.

## Seeding

A new log may begin with a prefix copied from an existing log. Forking and
replay both use this.

Given a source log and a boundary `seq` N:

1. Write a fresh `episode/start` at `seq` 0 with a new `id` and `fork_origin`
   set to the source episode and N. As the first event of a new log, it
   states the seeding writer's format version, whatever the source stated.
   An ordinary fork copies the other fields.
   A spawned fork records the spawned child's declared contract, contract
   fingerprint, and effective runtime allowance. It copies the remaining fields.
2. Copy source events with `seq` in `[1, N)`, renumbering `seq` to be
   contiguous.
3. Drop a copied `request/retry` that the boundary separated from the
   attempt it announces, since no copied event can close it.
4. Close every obligation the copied events left open, in the order the
   opening events appear, with one refinement: an open inner call closes
   immediately before the outer composing call it nests under, so the
   nested account balances before the outer synthetic result is read. A
   tool call receives a `tool/result` with
   `is_error: true`, `synthetic: true`, and a rendered text stating that
   the result was not recorded. A `compaction/start` receives a
   `compaction/end` with `ok: false`. A `spawn/start` receives a
   `spawn/end` whose outcome is `failed`, and a `budget/reserve` receives a
   `budget/release` naming the whole reservation as spent. The whole
   reservation is named because no writer that reaches this step can learn
   what the child actually spent: a new episode does not host the child,
   and a teardown that reaches this step waited for the child's own
   settlement and did not receive it. Charging the reservation is the
   conservative reading, so a synthetic release never understates a
   subtree.
5. Copy each rendering archive whose archive event and matching tool result
   were copied. Verify the source before copying and the destination after
   copying. Do not copy an archive referenced only at or after N.
6. Append `seed/end`.
7. Continue with live events.

The destination contains its own archive files. Retrieval from a seeded log
does not open the source episode. A missing archive, an unexpected path, a
length mismatch, or a digest mismatch makes seeding fail with the archive
event and violated rule named.

Copied `team/*` events belong to the source episode and are excluded from the
new episode's team fold. A fold reads team events only when the log's own
`episode/start.team_id` matches or the log is itself the lead.

The command line reaches seeding in two ways. The running form's
`--fork SOURCE_DIR --at SEQ` seeds a fresh directory from the source's
prefix at N equal to SEQ and runs it: the new `episode/start` draws a
fresh id, its `fork_origin` names the source, and the task the launch
carries is appended as a live `system` inbox item after `seed/end`,
because rule 1 copies the `task` item and the format admits one per log.
Launching with `--log-dir DIR` where DIR holds a log without `episode/end`
resumes that episode under the execution contract that ran it. The launch is
refused, with both fingerprints named, when the given configuration's
fingerprint differs from `episode/start.contract_fingerprint`. A log ending at
`seed/end` instead uses the execution contract recorded by its source's
`episode/start`. A log that ends at an event
boundary with every binding obligation closed, including one ending at
`seed/end`, is appended to as it stands; one cut short mid-line or with a
binding obligation open is seeded at N equal to its count of complete
events into a fresh directory beside it, which the run then continues.

A replay is a seed at N equal to the source log's length, with the model
responses replayed from `assistant/chunk` events rather than requested.

## Blocked codes

The `code` of a `blocked` outcome is one of the following. The list is closed
in version 3. A supervising episode routes on it.

| code | meaning |
|---|---|
| `looping-tool-call` | the same call with the same result repeated across consecutive steps |
| `looping-reasoning` | the same assistant text repeated across consecutive steps |
| `goal-unreachable` | the model reported that the task cannot be completed as stated |
| `ambiguous-task` | the model reported that the task admits incompatible readings |
| `missing-capability` | the task needs a tool or grant the contract lacks |
| `verification-unsatisfiable` | `done_when` retries were spent with findings still present |
| `child-blocked` | a child episode was blocked and the parent cannot proceed |
| `recovery-exhausted` | request retries were spent, or a workflow reached a recovery bound |
| `recovery-failed` | a workflow's recovery decision itself failed |

The model reports `goal-unreachable`, `ambiguous-task`, and
`missing-capability` by calling the built-in `block` tool with the code and a
message. A contract that lists `spawn` and has a non-empty `grants.spawn` may
also report `child-blocked`. The runtime detects the looping and verification
codes. The workflow executor produces the recovery codes.

## Exhausted limits

The `limit` of an `exhausted` outcome is one of `model_calls`,
`input_tokens`, `output_tokens`, `context_window`, `seconds`, `depth`,
`episodes`, `concurrency`. `context_window` means that the projected request
exceeded the model window and the compaction attempt did not produce a usable
summary. It is independent of the contract's input-token allowance.

## Size

An episode log grows with its conversation. `assistant/chunk` events
approximately double the stored assistant text. `model/request.messages`
repeats the derived history on every request. Both are accepted costs of
making the log complete without a reader having to implement the derivation.
A log reader that wants the compact form reads `assistant/message` and
`tool/result` and ignores the rest.
