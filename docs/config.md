# Execution-contract document

An execution contract is the validated configuration Foe runs for one
episode: instructions, tools, permissions, budgets, completion rules, model
selection, child contracts, and workflow. Rust represents a source document as
`ContractDocument` and its resolved form as `ResolvedContract`. Schema fields
use `contract_*` where a prefix is needed. This specification defines every
configuration key, value domain, and default.

Three rules shape the format.

- Every value is a string, a number, a boolean, a list of strings, or an
  object no more than two levels deep.
- No templating, no file includes, no references to other documents, and no
  environment variables. The document plus the files it names by absolute
  path are the whole input.
- No fact appears in two places. Anything derivable from another key is
  derived rather than declared.

`foe plan --schema` emits a JSON Schema for this format, so an editor can
validate a document and offer completions. `foe plan --config FILE` prints
the resolved contract, its fingerprint, and every tool definition the contract's
reachable tree can invoke, without running anything.

What a run uses is the document `--config` names, else `.foe/contract.json`
in the working directory, else the built-in coding workflow the binary
carries. `--config` takes a file path or the name of a built-in document,
written `builtin:NAME`: `builtin:coding` is that coding workflow, and
`foe plan --config builtin:coding` resolves it as it resolves a file. A run
that reads `.foe/contract.json` because its command line named no document
prints one line on standard error naming the file and the document's `name`.
docs/design.md "The command line" states the rule and what the built-in
coding workflow does.

`foe init --repository PATH` writes a starting document for a repository to
`PATH/.foe/contract.json`, with a placeholder verifier at `PATH/.foe/verify`
that rejects every completion candidate until a person replaces it with a
real completion check; docs/design.md "The command line" states what the
command decides. The paths mean nothing to the runtime: the document names
the verifier by absolute path like any configured tool, and running it is
`foe "the task" --config PATH/.foe/contract.json` like any other document.

`crates/contract` implements this document: every rule stated here is a check
there, it holds the JSON Schema `foe plan --schema` prints, and it resolves a
document into the contract `episode/start.contract` records.

Resolution constructs the complete contract tree once. During
execution-contract construction, Foe captures each configured executable's
bytes, digest, source path, and invocation name. Every later invocation uses
the captured executable, so replacing, modifying, or deleting the source
cannot change the run. The tree also contains every nested contract and every
workflow model node with inherited settings and canonical paths. A grant
list is a set: two declared roots naming one canonical path, such as
`/bin` and `/usr/bin` on a merged-usr host, resolve to one grant. Planning,
fingerprinting, sandbox construction, reservation, and spawning use that same
tree.

## JSON Schema subset

Two keys hold a schema written by the document's author: `host_tools.*.params`
and `done_when.returns`. A workflow model node holds a contract of its own, so
its `done_when.returns` is a third. Every one of them is read as JSON Schema
Draft 2020-12, whose dialect URI is
`https://json-schema.org/draft/2020-12/schema`. A schema may omit `$schema`;
when it is present, in the schema or in any subschema, it names that URI.

The runtime implements these assertions and no others.

| keyword | applies to | meaning |
|---|---|---|
| `type` | any value | one type name, or a list of them; an integer satisfies `number` |
| `enum` | any value | the value is one of the listed values |
| `const` | any value | the value equals the listed value |
| `anyOf` | any value | the value satisfies at least one of the listed subschemas |
| `required` | an object | every named property is present |
| `properties` | an object | each named property is checked against its subschema |
| `additionalProperties` | an object | `false` closes the object to the properties `properties` names; a subschema types every property outside them |
| `items` | an array | every element is checked against the subschema |
| `minimum`, `maximum` | a number | the number is within the bounds, inclusive |
| `minLength`, `maxLength` | a string | the count of characters is within the bounds, inclusive |
| `minItems`, `maxItems` | an array | the count of elements is within the bounds, inclusive |

The annotation keywords `$schema`, `$comment`, `title`, `description`,
`default`, `examples`, `deprecated`, `readOnly`, and `writeOnly` carry no
assertion and are accepted anywhere.

Any other keyword is a construction error naming the configuration key, the
subschema, and the keyword. `pattern`, `format`, `oneOf`, `allOf`, `not`,
`$ref`, `$defs`, and `prefixItems` are therefore refused rather than ignored.
A declared constraint is either enforced at every value boundary or refused
before the episode starts, so a schema in a document that runs is a schema the
log evidences in full. An author who needs a shape outside the subset
expresses it in a `done_when.verify` tool, which is a contract and has no such
limit.

The subset covers every schema the Python package derives from a type
annotation, listed in [sdk.md](sdk.md#parameter-schemas), so a tool or a
typed return declared there always produces a schema the runtime enforces in
full.

The runtime checks a tool call's arguments against the tool's parameter
schema at dispatch, before the tool receives any capability handle, for
built-in, configured, and host tools alike. A violation is an error result
naming the failing property, which the model reads like any other result. The
synthesized `return` tool carries `done_when.returns` under its `value`
property, so the same check decides whether a returned value completes the
episode.

## A complete example

```json
{
  "version": 4,
  "name": "fix-parser-test",

  "instructions": {
    "10-role": "You are a coding agent working in a Python repository.",
    "20-style": "Prefer the smallest change that makes the test pass. Run the test after every edit."
  },

  "tools": ["read", "grep", "edit", "bash", "retrieve", "ruff", "check"],

  "tool_defs": {
    "ruff": {
      "exec": "/home/user/project/.venv/bin/ruff",
      "description": "Python linter. Usage: ruff check [--output-format json] <path>. Exits 1 when findings exist.",
      "instruction": "Run ruff on every .py file you edit before finishing."
    },
    "check": {
      "exec": "/home/user/project/scripts/check",
      "description": "Runs ruff and the test suite over the repository and prints one finding per line; prints nothing when clean."
    }
  },

  "grants": {
    "read":  ["/home/user/project"],
    "write": ["/home/user/project/src", "/home/user/project/tests"],
    "spawn": []
  },

  "budget": {
    "model_calls": 40,
    "input_tokens": 320000,
    "output_tokens": 80000,
    "seconds": 1800
  },

  "done_when": { "verify": "check", "retries": 2 },

  "context": { "compact": true },

  "model": { "provider": "anthropic", "model": "claude-opus-5" },

  "sandbox": { "mode": "best-effort" },

  "task": "tests/test_parser.py::test_nested_brackets fails. Make it pass without changing the test."
}
```

## The minimal document

```json
{
  "version": 4,
  "name": "hello",
  "instructions": { "role": "You are a coding agent." },
  "tools": ["read", "grep", "edit", "bash"],
  "grants": { "read": ["/home/user/project"], "write": ["/home/user/project"] },
  "budget": { "model_calls": 20 },
  "model": { "provider": "anthropic", "model": "claude-opus-5" },
  "task": "Fix the failing test in tests/parser_test.py."
}
```

The `model` block names no key file; the key is read from
`~/.config/foe/credentials/anthropic.json`, which `foe login anthropic`
writes. A host process that supplies the model backend omits the `model`
block.

## Keys

### `version`

Integer. Required. The configuration format version. This document describes
version 4.

### `name`

String. Required. A short name for the contract. Shown in the viewer and
written into the log. It participates in fingerprint.

### `instructions`

Object mapping section key to section text. Required, with at least one
entry.

The key is an identifier the author chooses. The runtime never interprets it.
It exists so that a section can be addressed later: replaced, removed, or
diffed by whatever produced the configuration. The value is literal text,
rendered into the system prompt unchanged.

Sections render in lexicographic order of their keys. That order is stable
across machines, which keeps the request prefix byte-identical across runs.
An author who needs a specific order prefixes keys with digits, as in
`10-role`, `20-style`.

Both key and text participate in fingerprint. Renaming a key changes the
rendered order, so it changes what the model sees, so it changes fingerprint.

### `tools`

List of strings. Required, with at least one entry.

Each string names a tool. The order of the list is the order in which eligible
tool schemas are sent to the model. It is also the order in which tool
instructions are appended to the system prompt. The order therefore
participates in fingerprint.

A name resolves against three sources, checked in this order:

1. Built-in tools: `read`, `grep`, `edit`, `bash`, `session`, `compose_tools`,
   `retrieve`, `block`, `spawn`, `wait`, `steer`, `notify`, `send`, `team`.
2. Entries in `tool_defs`.
3. Entries in `host_tools`.

A name that resolves in two sources, or in none, is an error at construction.

`foe plan` without `--config` lists every built-in tool with its
description. `foe plan --config FILE` reports the resolved set for a
document, with each tool's source.

### `tool_defs`

Object mapping tool name to a definition. Optional. Each definition describes
an executable that the model may invoke.

| field | type | required | meaning |
|---|---|---|---|
| `exec` | string | yes | absolute path to the executable |
| `description` | string | yes | what the tool does and how to call it; sent to the model in the tool schema |
| `instruction` | string | no | when to use it; appended to the system prompt |
| `network` | boolean | no | whether the executable may open TCP connections; default `false` |
| `timeout_seconds` | integer | no | wall-clock limit for one invocation; default 120 |
| `cwd` | string | no | absolute working directory; default is the first `read` root |

`exec` must be absolute. The runtime never searches a path list, because
which executable a name resolves to would then depend on the machine. A host
that wants `ruff` resolves it once and writes the absolute path.

The executable receives the model's `args` list as its argument vector,
without a shell. Standard input is `/dev/null`. Standard output and standard
error are captured. The exit code is reported as part of the result. An exit
code other than zero is a result rather than an error.

During execution-contract construction, Foe captures the configured
executable's bytes, digest, source path, and invocation name. The invocation
name is the final component of the configured `exec` path and must be valid
UTF-8; resolution rejects any other name, so the fingerprint records name
bytes exactly. The digest and invocation name participate in the fingerprint.
Every later invocation uses the captured executable. Source replacement,
modification, or deletion cannot change the run.

Declaring an entry in `tool_defs` permits the episode to execute that file.
The file does not need a `grants.execute` entry. An explicit execute grant
permits subprocesses of a tool, such as a compiler started by `bash`.

### `host_tools`

Object mapping tool name to a specification. Optional. Each entry describes
a tool that the host process implements and answers over the
[protocol](protocol.md). The specification is in the document so that
fingerprint is computable from the document alone; the host supplies only the
implementation.

| field | type | required | meaning |
|---|---|---|---|
| `description` | string | yes | sent to the model in the tool schema |
| `instruction` | string | no | appended to the system prompt |
| `params` | object | yes | JSON Schema for the arguments, in the subset above; dispatch checks every call against it |
| `effect` | string | yes | `pure`, `reads`, `writes`, `execs`, or `spawns` |

A host that does not implement a tool named here fails the first call to it.
The Python package generates these entries from decorated functions.

### `grants`

Object. Required. Names what the episode may reach.

| field | type | required | meaning |
|---|---|---|---|
| `read` | list of strings | yes, at least one | absolute directories the episode may read |
| `write` | list of strings | no | absolute directories the episode may write; default empty |
| `execute` | list of strings | no | absolute files or directories that a tool subprocess may read and execute; default empty |
| `spawn` | list of strings | no | names from `child_contracts` that board tasks may assign to child episodes; default empty |
| `bind` | list of integers | no | TCP ports, 1 to 65535, that a process of the episode may bind; default empty |
| `task_session` | boolean | no | permits `session start` with `lifetime: "task"`; default false |

Paths are prefixes. A grant on `/home/user/project` covers every path below
it. There is no pattern syntax.

`foe plan` warns when a reachable contract selects `bash` or `session`, uses
a sandbox mode, and leaves `grants.execute` empty. The warning does not make
the contract invalid because shell built-ins remain useful. On a host that
enforces the sandbox, an external command needs its absolute file or an
enclosing directory in `grants.execute`.

A `bind` grant lets a server the episode starts listen on the named ports;
[sandbox.md](sandbox.md) states how the kernel enforces it. It grants no
outbound reach: connecting stays tied to the configured model endpoint and to each
tool definition's `network` field.

A `task_session` grant permits a session process group to survive episode
settlement. The `session` call must also request `lifetime: "task"`. At
settlement the runtime transfers cleanup responsibility to the environment
that owns the foe invocation. [tools.md](tools.md#session) specifies the
lifecycle and the cleanup requirement.

The runtime opens each granted directory once when the episode starts, and
every read and write below it names a path relative to that open directory.
Containment therefore holds at the moment of use rather than at the moment of
a check: a symbolic link, a `..` component, or a directory component renamed
after the episode started cannot direct an operation outside the root, and no
interval exists in which a checked pathname can be repointed before it is
used. On Linux the kernel performs the resolution with `openat2` and
`RESOLVE_BENEATH`.

A read follows a symbolic link that stays inside a granted root and is denied
by one that leaves it. A write names an entry in a granted directory and
replaces that entry, so writing to a name that is a symbolic link replaces
the link rather than the file it points at.

An execute grant covers its named file or every file below its named
directory. It also grants read access because a process must read an
executable and its runtime files to start it. The grant remains available
inside a configured executable, so `bash` can start a compiler or build tool.
A directory entry is therefore a subtree grant: resolution accepts it
without examining what the subtree holds, no interpreter analysis narrows
it, and the resolved permissions `foe plan` reports name the directory
itself as the granted object. An author who wants the exact-executables
discipline lists files.

The kernel sandbox enforces the same grants on the episode process and on
every process it starts, which [sandbox.md](sandbox.md) specifies. The open
directories are what bounds the episode process itself where Landlock is
unavailable, which `sandbox.mode` `best-effort` permits and `off` requires.

The episode's own log directory is always writable and need not be listed.

A tool whose declared effect exceeds the grants is refused at construction.
An `edit` in `tools` with an empty `write` list is an error. A `spawn` in
`tools` with an empty `spawn` list is an error.

The built-in `block` tool derives its code enum from this grant and the
`tools` list. A contract that lists `spawn` and has a non-empty `spawn` grant
receives `child-blocked` in addition to the three general model-reported
codes. The resolved schema is shown to the model, enforced at dispatch, and
included in contract fingerprint.

The kinds present and the count of each participate in fingerprint. The paths
do not.

A nonempty `grants.spawn` list authorizes the `spawn` tool to add board tasks
for the named child contracts. The grant does not add a tool schema. The
contract must also list `spawn` in `tools` before its model can delegate.
Each added task uses the selected child contract's grants and budget.

### `budget`

Object. Required.

| field | type | required | default | meaning |
|---|---|---|---|---|
| `model_calls` | integer | yes | | maximum model requests, including retries; the last available ordinary request receives a system warning |
| `input_tokens` | integer | no | unlimited | provider-reported input allowance across all requests, including cache-read input |
| `output_tokens` | integer | no | unlimited | provider-reported output allowance across all requests, including reasoning when the provider includes it |
| `seconds` | integer | no | unlimited | wall-clock limit for the episode |
| `max_depth` | integer | no | 1 | how many levels of child episodes may exist below this one; 0 forbids spawning |
| `max_episodes` | integer | no | 8 | lifetime count of episodes in the tree, including this one |
| `max_concurrent` | integer | no | 4 | direct children of this episode running at once |
| `loop_threshold` | integer | no | 8 | consecutive identical tool calls, or identical assistant turns, that end the episode as blocked |

On resume, recorded model usage and child reservations are restored before
any queued team task or workflow node starts. Restoration occurs once per
pool, so executor startup cannot charge the same events twice. The seconds
allowance includes whole seconds elapsed since the recorded episode start,
including downtime. Restoration starts the remaining timer after subtracting
that elapsed time, so setup time is charged once. A fresh fork starts its own
wall-clock allowance.

`model_calls`, `input_tokens`, `output_tokens`, `seconds`, `max_depth`, and
`max_episodes` apply to the whole tree below this episode. A child's budget
is reserved from its parent's remainder. The child reports what its whole
subtree used. A child contract that declares a larger limit runs under its
reserved share.

The declared budget participates in contract fingerprint. A child reservation is
an episode input and leaves that declaration unchanged. The parent records
the reservation in `budget/reserve`. The child records the complete effective
allowance in `episode/start.effective_budget` and enforces that allowance.
Two executions of one declared child retain one contract fingerprint when their
available shares differ.

The runtime charges provider-reported input after each completed response.
It starts another request only while cumulative spend remains below the
allowance. Foe does not send a per-request input cap and does not infer the
next request's cost from earlier reports — provider accounting varies
within an episode — so the final request may cross the remaining allowance,
and the crossing ends the episode afterwards. Completion is checked before
exhaustion, so a response that finishes the task on the crossing request
completes the episode. Cached input remains part of `input_tokens`.

When the pool leaves one model call for an ordinary episode request, the
runtime includes a system inbox warning in that request. The warning directs
the model toward the highest-priority unfinished work and the configured
completion signal. It changes no allowance or completion rule.

For a provider that accepts a per-request output cap, the runtime clamps the
cap to the remaining `output_tokens`. This applies to ordinary requests,
retries, workflow recovery decisions, and compaction summaries. A configured
`model.max_output_tokens` can make the cap smaller.

The ChatGPT Codex backend used by `openai-codex` rejects a per-request output
cap. Foe omits the unsupported field and charges the usage reported after
each response. One response can cross the remaining output-token allowance.

The episode share a spawn asks for depends on whether the child can start
descendants at all. A child that can start none asks for one episode, so a
leaf does not hold its parent's whole allowance against its siblings. A
child that can start descendants asks for the `max_episodes` its own
contract declares. An entry in `grants.spawn` and a model node in the
child's `workflow` each make the child able to start descendants, and the
model node counts at every level of nested workflows.

`max_concurrent` and `loop_threshold` apply to one episode. `max_concurrent`
counts the direct children of the episode that declares it. A ready board
task remains queued while that many children run. The scheduler starts the
task after a running child returns capacity. A child applies its own value to
the team it leads. `max_episodes` bounds the number of episodes that can run
across the complete tree.

### `done_when`

Object. Optional. Declares when the episode is complete. When absent, the
episode completes when the model produces a turn with no tool calls.

| field | type | meaning |
|---|---|---|
| `verify` | string | name of a tool in `tools`; a turn with no tool calls or a non-error ordinary call to this tool supplies a candidate, which the tool accepts by returning no findings as a verifier |
| `retries` | integer | how many times findings are fed back; default 2 |
| `returns` | object | a JSON Schema in the subset above; the episode completes when the model calls the synthesized `return` tool with a conforming value |

`verify` and `returns` may both be present. The verifier then checks the
returned value.

A verifier is invoked once per candidate. A tool in `tool_defs` receives the
complete candidate as JSON on standard input and an empty argument list. The
executable accepts the candidate by exiting with status zero and printing
nothing. It reports findings by exiting with status zero and printing one
finding per line on standard output.

A host or built-in verifier declares exactly one parameter in its parameter
schema. Contract construction rejects any other parameter count. The runtime
calls the tool with an argument object that binds the complete candidate to
that parameter. The tool returns a list of finding strings. An error result
means that the verifier failed to judge the candidate.

A nonzero exit status, an end by signal, or a timeout means that the
executable verifier failed to judge the candidate. The episode ends as
`failed` with the exit code and both output streams as its error. A
general-purpose linter therefore needs a wrapper that reads the candidate,
runs the linter, prints its findings, and exits with status zero whether it
accepts the candidate or reports findings.

A `returns` schema may declare a `learned` member. The member is an array of
objects. Each object pairs a one-sentence `claim` string with an integer
`seq` that cites the supporting event in this episode's log. This shape is
the standard exit through which an episode exports observations. A schema
that requires `learned` must declare this non-empty array shape.

In an agent-loop contract, listing `learned` in the schema's `required` array
makes the citations a completion condition. Every tool result shown to that
episode starts with its log sequence as `[seq N]`. Before completion, the
runtime requires at least one observation. Each `seq` must name a successful
`tool/result` in the same episode. An inlined canonical value is
reconstructable from the event. A spilled canonical value must still be
readable as JSON at the single-component path and byte length in the event.
A recorded digest must also match the stored bytes.

An invalid citation returns a `system` inbox finding and the episode
continues. The runtime does not judge whether the result supports the claim.
The configured verifier, when present, runs only after every citation passes
these structural checks. An optional `learned` member remains an exported
observation that the runtime does not require for completion. The built-in
coding workflow requires one to eight observations from every model episode.

Without `returns`, a non-error ordinary call to the declared verifier asks
the runtime to verify the assistant text after the turn settles. Acceptance
completes the episode without another model request. The ordinary call and
the authoritative verifier invocation remain separate tool executions.

Every authoritative invocation is recorded as one `verification/result`
event in the episode's own log — the accepted run that completes the
episode, each findings run, and a failed run — carrying the verifier's
fingerprint at invocation; [log-format.md](log-format.md#verification)
specifies the event. The model never sees it: findings reach the model
only through the `verify` inbox item.

### `context`

Object. Optional. Whether and when the conversation is compacted: the
oldest steps replaced in the model's context by a summary and the
runtime's continuation state when the next request is projected to outgrow
the model's window. [compaction.md](compaction.md) specifies the policy.

| field | type | default | meaning |
|---|---|---|---|
| `compact` | boolean | `false` | whether the runtime compacts at all |
| `window_tokens` | integer | from the provider table | the model's context window in tokens |
| `reserve_tokens` | integer | 16384 | tokens kept free below the window; compaction runs when the projected request exceeds `window_tokens` minus `reserve_tokens` |
| `keep_recent_tokens` | integer | 20000 | approximate size of the most recent steps kept verbatim after a compaction |
| `margin_tokens` | integer | 2048 | added to the projection of the next request, absorbing estimation error |

`window_tokens` may be omitted when `compact` is true and the `model` block
names a model the provider table knows; [models.md](models.md) lists them.
For any other model, and under a host that supplies the model backend, it is
required, and its absence is a construction error naming the key. When
given, it must exceed `reserve_tokens` plus `keep_recent_tokens`.

The block participates in fingerprint.

```json
"context": { "compact": true }
"context": { "compact": true, "window_tokens": 128000, "keep_recent_tokens": 12000 }
```

### `model`

Object. Optional. When present, foe calls the configured model endpoint.
When absent, the host process must supply a model backend that answers model
requests over the [protocol](protocol.md).

| field | type | required | meaning |
|---|---|---|---|
| `provider` | string | yes | a provider name; [models.md](models.md) lists them |
| `model` | string | yes | the model identifier the provider expects |
| `max_output_tokens` | integer | no | per-request output limit; default is the provider's |
| any other key | string | per provider | a provider-specific option, such as `api_key_file`, `base_url`, `project`, or `location` |

The provider name is opaque to the configuration format. The model client
resolves it; `foe plan` reports the selected client, or says the name is
unknown and lists the known ones.

A child contract may declare its own `model` block. A child that omits the
block inherits the nearest ancestor's block. The same rule applies to model
nodes in a workflow and to descendants of those nodes.

A block may omit its credential field. A provider that requires a
credential then reads `~/.config/foe/credentials/<provider>.json`, the file
`foe login` writes. `compatible-http` reads only an explicitly named key file
and sends no authentication header when the option is absent. An explicit
`api_key_file`, `token_file`, or `credentials_file` replaces a convention
path. A resolved path is written into the block that
`episode/start.contract` records. Nothing is read from the environment.

One block per provider:

```json
{ "provider": "anthropic", "model": "claude-opus-5" }
{ "provider": "openai", "model": "gpt-5.6-sol", "reasoning_effort": "medium" }
{ "provider": "compatible-http", "model": "fixture-model", "base_url": "http://127.0.0.1:11434/v1" }
{ "provider": "openrouter", "model": "anthropic/claude-opus-5" }
{ "provider": "openai-codex", "model": "gpt-5.6-sol" }
{ "provider": "vertex", "model": "gemini-2.5-pro", "project": "my-project", "location": "us-east5" }
```

[models.md](models.md) specifies every option, the credential file shapes,
and what each provider cannot express. The `model` block does not
participate in fingerprint: the model is runtime infrastructure, and a system
that needs to record which model ran reads it from the log's
`request/header` events.

### `sandbox`

Object. Optional.

| field | type | default | meaning |
|---|---|---|---|
| `mode` | string | `best-effort` | `best-effort`, `required`, or `off` |

`best-effort` applies whatever the kernel supports and records the Landlock
version obtained. `required` refuses to start when Landlock is unavailable.
`off` applies no kernel restriction and records that fact.

The rules themselves are compiled from `grants` and `tool_defs`; there is
nothing else to declare.

### `child_contracts`

Object mapping contract name to a nested configuration. Optional. Each value
is a full contract document without `version`, `task`, or `sandbox`.
The `model` block is optional and follows the inheritance rule above. A name
listed in `grants.spawn` must appear here.

A child contract's grants must be a subset of its parent's, checked at
construction. A child may set `task_session` only when its parent sets it.
Each child contract's fingerprint participates in the parent's fingerprint. Its
tool list may differ from its parent's. The reachable-tool report from
`foe plan` covers the root, each descendant contract that
`grants.spawn` entry reaches, and each workflow model node. A declaration
no such path reaches stays in the resolved contract and is absent from the
report, because no episode can invoke it.

### `workflow`

Object. Optional. A declared graph of nodes that replaces the free loop
for this episode. [workflow.md](workflow.md) specifies every key under it
and every construction rule. The document's permissions and budget are the
ceiling the graph draws from. A tool node names a tool in `tools`. A
model node's contract is a child contract in the sense of `child_contracts` whose
tools, configured executables, host tool definitions, filesystem
grants, spawn grants with their descendant child contracts, and spend limits all
lie within the document's own. A child contract may carry a `workflow` of
its own. A model node may declare `empty` so that a blocked or exhausted
child contributes that value and downstream work continues. The graph
may contain at most 4,096 edge references across all nested workflows. The
count includes every `follows` entry, branch successor, and
`recovery.follows` entry. Construction checks this count before building
graph indexes. The graph participates in fingerprint as workflow.md "Fingerprint"
lists.

When this field is absent, planning and viewing project one terminal
`root-agent` node that follows the invocation task and runs in the root
episode. Execution uses the direct agent loop. The projection adds no
configuration value, fingerprint input, log event, budget reservation, or
child episode.

### `task`

String. Required. What this episode is to do. Written into the log as the
first inbox item. The task does not participate in the fingerprint. Two episodes
of the same contract with different tasks share a fingerprint.

## Fingerprint summary

The following participate in the fingerprint: `name`, `instructions`, `tools` and
their order, each entry of `tool_defs` including the executable's content
hash, the kinds and counts in `grants`, `budget`, `done_when`, `context`,
every entry of `child_contracts`, `workflow`, and the runtime's version and build.

The following do not participate: concrete paths in `grants`, `model`,
`sandbox`, and `task`.

## Errors

Every error at construction names the key that caused it and the rule it
violated. An error in an embedded schema also names the subschema and the
keyword. Construction fails before any process starts and before any log is
written. A document that passes construction will run; what remains uncertain
is the model's behavior and the world's.
