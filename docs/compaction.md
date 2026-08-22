# Context compaction

A long episode produces more conversation than a model's context window
holds. Context compaction keeps such an episode running. When the next
request is projected to outgrow the window, the runtime replaces the oldest
steps in the model's context with a summary of them and a record of what
the model must still honor. This document specifies when that happens,
where the conversation is cut, what the summary contains, how it is logged
and derived, and what it costs.

One principle shapes every rule here. The log retains complete evidence.
The active model context is a bounded projection of that evidence. A
compaction changes the projection while preserving the task's obligations
and the runtime's facts; it removes nothing from the log.

## When it triggers

foe has no tokenizer. The provider reports, with every response, how many
input tokens the request it answered held. That number, together with what
has arrived since, projects the size of the next request:

```
projected = last.input + last.output
          + estimate(rendered tool results and inbox items after that response)
          + max_output + margin
```

`last` is the usage the most recent ordinary response reported. `estimate`
counts one token per four bytes of text. `max_output` is the smaller of
`model.max_output_tokens` and the remaining episode-wide output allowance.
When only one exists, it supplies the value. When neither exists, the value
is 0. `margin` is `context.margin_tokens`. Compaction runs, before the
request is assembled, when

```
projected > window_tokens - reserve_tokens
```

The reserve is headroom: compaction happens while the request still fits,
so that the provider never rejects one for its size. A response that
reports no usage, such as an interrupted one, does not project; the
projection waits for the next response that does. After a compaction, no
request is projected until the compacted request has been answered.

## Where it cuts

The cut is a step boundary. A step is one `model/request` and the tool
calls its response produced, and the cut point, `first_kept_seq`, is the
`seq` of the first request of a step. Everything before it is summarized;
everything from it on stays in the context verbatim. Because the cut falls
before a request, the kept suffix begins with a whole step and no tool
call is ever separated from its result. A step that was retried keeps its
first request, so the inbox items that request consumed stay where they
were.

Each step is sized by the byte length of its assistant text and its
rendered tool results, divided by four. Walking steps from the newest to
the oldest, the runtime keeps the longest suffix whose size fits
`keep_recent_tokens`, subject to two bounds: at least the newest step is
kept, and at least the oldest unsummarized step is summarized. An episode
with fewer than two unsummarized steps is never compacted. A later
compaction starts where the previous one kept from, so the steps an
earlier summary already covers are never read again.

## What the summary receives

The summary is one model call through the episode's ordinary transport.
Its system prompt is a constant the runtime owns; an author cannot change
it, and identity hashes it. The prompt tells the model that a transcript is
being condensed, names the five headings the output must carry, in order,
and forbids continuing the conversation, calling tools, or addressing
anyone.

Its one user message holds, first, the summary written at the previous
compaction when there was one, under the heading `# Earlier summary`, and
then the span being summarized under `# Transcript`. The span is rendered
as labeled plain text rather than as a conversation: each entry is
`[user]`, `[assistant]`, or `[result <tool>]` followed by its text, and a
tool call appears inside its assistant entry as `[call <name> <args>]`. An
image block renders as its media type. Nothing in the text has the shape
of a turn the model could continue.

## What the summary contains

`compaction/summary` carries two things.

The `summary` is the model's narrative under the headings Goal, Progress,
Decisions, Open items, and Next step. It is the only part of the event the
model wrote.

The `state` is built by the runtime from typed events; the model's output
has no part in it:

| field | source |
|---|---|
| `task` | the task inbox item, verbatim |
| `done_when` | the configured completion condition, rendered in one line |
| `outstanding_findings` | the text of the latest verifier report, which is unresolved while the episode runs |
| `files.read`, `files.written`, `files.edited` | the `path` argument of every `read`, `write`, and `edit` call in the covered span whose result was not an error, joined with every earlier summary's lists, sorted, without duplicates |
| `children` | every child that ended within the covered span, with the program its `spawn/start` named and its outcome, after those earlier summaries carried |
| `covered` | the first and last `seq` of the span this compaction summarized directly |
| `budget_remaining` | the episode's remaining model calls, input tokens, output tokens, and seconds when the compaction began |

The file lists are the structural record of what the model has touched.
The model is never asked for them, so they cannot drift from the log.

## How it is logged

A compaction writes this sequence, all at the step whose request it
precedes:

```
compaction/start     { step, covered, trigger: "threshold", projected_tokens, reserved }
request/header       { reason: "change", system: <the summarization prompt>, tools: [] }
model/request        { request_id: "cmp_NNNN", messages: [ <the one user message> ] }
assistant/chunk ×n
assistant/message    { request_id: "cmp_NNNN", text: <the narrative> }
compaction/summary   { step, summary, state, first_kept_seq, summary_request_seq }
compaction/end       { step, ok: true, usage, active_estimate }
request/header       { reason: "change", ... the ordinary header restored }
model/request        { request_id: "rq_NNNN", messages: [ <the compacted list> ] }
```

The summarization request is an ordinary request in every respect except its
id prefix. It draws its number from the same counter as the step's own
requests. It counts as one model call. Its reported input and output count
against their respective episode-wide allowances. The runtime clamps its
output cap before sending it. `reserved` in `compaction/start` is the budget
that was left when the compaction began. `active_estimate` is the estimated
size of the request that follows. [log-format.md](log-format.md) lists every
field.

## How it is derived

After the latest `compaction/summary`, the message list of a request is:

1. one `user` message holding the task text verbatim, from `state.task`;
2. one `user` message holding the continuation: the state as labeled
   lines, then the narrative;
3. the messages derived from events with `seq >= first_kept_seq` by the
   ordinary rules, with the items a kept request consumed included wherever
   those items lie.

The summarization request and its response contribute nothing to any
derivation; they are recognized by the `cmp_` prefix. Each `model/request`
records the list it sent, so a reader recomputes the list from the events
and compares.

The continuation message has one rendering, which the runtime, the viewer,
and any reader apply identically. A label is followed by a colon, then by
a scalar value after one space, or by a list as one `- ` item per line
below the label, or by `(none)` for an empty list:

```
## Continuation state

covered: seq 1 to 57
done_when: a turn with no tool calls or a non-error `check` call, then `check` reports no findings
outstanding_findings: (none)
files_read:
- src/parser.py
- tests/parser_test.py
files_written: (none)
files_edited:
- src/parser.py
children: (none)
budget_remaining: model_calls 22, input_tokens 280000, output_tokens 30400, seconds unlimited

## Summary

## Goal
…
```

## When it fails

A summarization that fails, or that returns an empty summary, is attempted
once: the runtime writes `compaction/end` with `ok: false` and the error,
writes no `compaction/summary`, and leaves the projection as it was. The
step's request then proceeds with the existing context when the projection
still fits within the window itself; when the projection passes the
window, the episode ends as `exhausted` with limit `input_tokens`. The next
step projects again and may attempt a new compaction.

## Identity

The `context` block participates in identity. So do the summarization
prompt, the transcript rendering, the continuation rendering, the fields of
the continuation state, and a policy version number that is raised
whenever any of those changes in meaning. Two episodes of the same program
therefore compact under the same rules, and a change to the rules changes
identity.

## Enabling it

```json
"context": { "compact": true }
```

`compact` defaults to false. `window_tokens` is the model's context window
in tokens; it may be omitted for a model the provider table knows, and is
required otherwise, including under a host that supplies the transport.
`reserve_tokens` defaults to 16384, `keep_recent_tokens` to 20000, and
`margin_tokens` to 2048. [config.md](config.md) specifies the block, and
[models.md](models.md) lists the windows the provider table knows.
`foe plan` prints the resolved policy in one line.

Compaction applies to the free agent loop. A workflow episode's own
requests are recovery decisions built from declared inputs, which never
grow; a model node's child episode compacts under its own `context` block.

## Limits

Compaction is lossy. The narrative is the model's account of the covered
span, and the model may omit what later matters. The log keeps every event
the summary replaced, so a reader can always see what was lost; the model
cannot. For structured work that must not lose intermediate results, a
workflow is the lossless alternative: each node's output is a typed value
carried by dataflow rather than by conversation.

The size estimates are approximations. A step's size and the projection
both count four bytes per token, which overestimates prose and
underestimates dense code; the margin absorbs the difference in ordinary
use. A single tool result larger than the window cannot be compacted
around.

An unchanged-file re-read notice, tracked as issue 19 and not yet
implemented, would answer a repeated `read` of an unchanged file with a
notice rather than its contents. Once the earlier read has been compacted
away, the contents are no longer in the model's context, so such a notice
must return the full contents again.
