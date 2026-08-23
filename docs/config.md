# Configuration

An episode is configured by one JSON document. This document specifies every
key, the domain of every value, and every default. The Python package
generates this document; a person may also write it by hand.

Three rules shape the format.

- Every value is a string, a number, a boolean, a list of strings, or an
  object no more than two levels deep.
- No templating, no file includes, no references to other documents, and no
  environment variables. The document plus the files it names by absolute
  path are the whole input.
- No fact appears in two places. Anything derivable from another key is
  derived rather than declared.

`foe schema` emits a JSON Schema for this format, so an editor can validate a
document and offer completions. `foe plan --config FILE` prints the resolved
program, its identity, and every tool definition the program's reachable
tree can invoke, without running anything.

`crates/config` implements this document: every rule stated here is a check
there, it holds the JSON Schema `foe schema` prints, and it resolves a
document into the program `episode/start.program` records.

## JSON Schema subset

Two keys hold a schema written by the document's author: `host_tools.*.params`
and `done_when.returns`. A workflow model node holds a program of its own, so
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
expresses it in a `done_when.verify` tool, which is a program and has no such
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
  "version": 2,
  "name": "fix-parser-test",

  "instructions": {
    "10-role": "You are a coding agent working in a Python repository.",
    "20-style": "Prefer the smallest change that makes the test pass. Run the test after every edit."
  },

  "tools": ["read", "grep", "edit", "bash", "ruff", "check"],

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
  "version": 2,
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
writes. When a host process supplies the model transport, the `model` block
is omitted.

## Keys

### `version`

Integer. Required. The configuration format version. This document describes
version 2.

### `name`

String. Required. A short name for the program. Shown in the viewer and
written into the log. It participates in identity.

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

Both key and text participate in identity. Renaming a key changes the
rendered order, so it changes what the model sees, so it changes identity.

### `tools`

List of strings. Required, with at least one entry.

Each string names a tool. The order of the list is the order in which tool
schemas are sent to the model and the order in which tool instructions are
appended to the system prompt. The order therefore participates in identity.

A name resolves against three sources, checked in this order:

1. Built-in tools: `read`, `grep`, `edit`, `bash`, `block`, `spawn`,
   `wait`, `steer`, `notify`, `send`, `team`.
2. Entries in `tool_defs`.
3. Entries in `host_tools`.

A name that resolves in two sources, or in none, is an error at construction.

`foe tools` lists every built-in tool with its description. `foe tools
--config FILE` lists the resolved set for a document, with each tool's
source.

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

The executable is hashed into identity by content. A replaced binary at the
same path changes identity.

Declaring an entry in `tool_defs` is what permits the episode to execute that
file. There is no separate execute grant.

### `host_tools`

Object mapping tool name to a specification. Optional. Each entry describes
a tool that the host process implements and answers over the
[protocol](protocol.md). The specification is in the document so that
identity is computable from the document alone; the host supplies only the
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
| `spawn` | list of strings | no | names from `programs` the episode may start; default empty |

Paths are prefixes. A grant on `/home/user/project` covers every path below
it. There is no pattern syntax.

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

The kernel sandbox enforces the same grants on the episode process and on
every process it starts, which [sandbox.md](sandbox.md) specifies. The open
directories are what bounds the episode process itself where Landlock is
unavailable, which `sandbox.mode` `best-effort` permits and `off` requires.

The episode's own log directory is always writable and need not be listed.

A tool whose declared effect exceeds the grants is refused at construction.
An `edit` in `tools` with an empty `write` list is an error. A `spawn` in
`tools` with an empty `spawn` list is an error.

The kinds present and the count of each participate in identity. The paths
do not.

### `budget`

Object. Required.

| field | type | required | default | meaning |
|---|---|---|---|---|
| `model_calls` | integer | yes | | maximum model requests, including retries |
| `input_tokens` | integer | no | unlimited | provider-reported input allowance across all requests, including cache-read input |
| `output_tokens` | integer | no | unlimited | provider-reported output allowance across all requests, including reasoning when the provider includes it |
| `seconds` | integer | no | unlimited | wall-clock limit for the episode |
| `max_depth` | integer | no | 1 | how many levels of child episodes may exist below this one; 0 forbids spawning |
| `max_episodes` | integer | no | 8 | lifetime count of episodes in the tree, including this one |
| `max_concurrent` | integer | no | 4 | direct children of this episode running at once |
| `loop_threshold` | integer | no | 3 | consecutive identical tool calls, or identical assistant turns, that end the episode as blocked |

`model_calls`, `input_tokens`, `output_tokens`, `seconds`, `max_depth`, and
`max_episodes` apply to the whole tree below this episode. A child's budget
is reserved from its parent's remainder. The child reports what its whole
subtree used. A child program that declares a larger limit runs under its
reserved share.

The runtime charges provider-reported input after each completed response.
It starts another request only while cumulative spend remains below the
allowance. Foe does not send a per-request input cap and does not infer the
next request's cost from earlier reports — provider accounting varies
within an episode — so the final request may cross the remaining allowance,
and the crossing ends the episode afterwards. Completion is checked before
exhaustion, so a response that finishes the task on the crossing request
completes the episode. Cached input remains part of `input_tokens`.

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
program declares. An entry in `grants.spawn` and a model node in the
child's `workflow` each make the child able to start descendants, and the
model node counts at every level of nested workflows.

`max_concurrent` and `loop_threshold` apply to one episode. `max_concurrent`
counts the direct children of the episode that declares it, so a child with
its own children answers to its own value. The number of episodes running at
once anywhere in the tree is bounded instead by `max_episodes`, which every
episode in the tree draws from.

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

A verifier is invoked once per candidate, with the candidate as its input and
an empty argument list. For a tool in `tool_defs`, the candidate is passed
as JSON on standard input. The executable accepts the candidate by exiting
with status zero and printing nothing. It reports findings by exiting with
status zero and printing one finding per line on standard output. Any other
exit status, an end by signal, or a timeout is a failure of the verifier
rather than a judgment of the candidate: the episode ends as `failed` with
the exit code and both output streams as its error. For a host tool or a
built-in tool, the candidate is the single argument and the returned value
is a list of finding strings; an error result is likewise a failure of the
verifier. A verifier is therefore a program written to this contract; a
general-purpose linter is wrapped by a short script that reads the
candidate, runs the linter, prints its findings, and exits with status zero
whether or not it found any.

Without `returns`, a non-error ordinary call to the declared verifier asks
the runtime to verify the assistant text after the turn settles. Acceptance
completes the episode without another model request. The ordinary call and
the authoritative verifier invocation remain separate tool executions.

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
For any other model, and under a host that supplies the transport, it is
required, and its absence is a construction error naming the key. When
given, it must exceed `reserve_tokens` plus `keep_recent_tokens`.

The block participates in identity.

```json
"context": { "compact": true }
"context": { "compact": true, "window_tokens": 128000, "keep_recent_tokens": 12000 }
```

### `model`

Object. Optional. When present, foe calls the model itself. When absent, the
host process must answer model requests over the [protocol](protocol.md).

| field | type | required | meaning |
|---|---|---|---|
| `provider` | string | yes | a provider name the build knows; [models.md](models.md) lists them |
| `model` | string | yes | the model identifier the provider expects |
| `max_output_tokens` | integer | no | per-request output limit; default is the provider's |
| any other key | string | per provider | a provider-specific option, such as `api_key_file`, `base_url`, `project`, or `exec` |

The provider name is opaque to the configuration format. Whether a build
knows it is decided where the transport is composed; `foe plan` reports the
resolved transport, or says the name is unknown and lists the known ones.

A child program may declare its own `model` block. A child that omits the
block inherits the nearest ancestor's block. The same rule applies to model
nodes in a workflow and to descendants of those nodes.

A block may omit its credential field. The transport then reads
`~/.config/foe/credentials/<provider>.json`, the file `foe login` writes,
with the home directory taken from the passwd database. An explicit
`api_key_file`, `token_file`, or `credentials_file` replaces it. The path
that was used is written into the block that `episode/start.program`
records. Nothing is read from the environment.

One block per provider:

```json
{ "provider": "anthropic", "model": "claude-opus-5" }
{ "provider": "openai", "model": "gpt-5.6-sol", "reasoning_effort": "medium" }
{ "provider": "openai-compatible", "model": "llama3.1", "base_url": "http://127.0.0.1:11434/v1", "api_key_file": "/home/user/.config/foe/ollama.key" }
{ "provider": "openrouter", "model": "anthropic/claude-opus-5" }
{ "provider": "openai-codex", "model": "gpt-5.6-sol" }
{ "provider": "vertex", "model": "gemini-2.5-pro", "project": "my-project", "location": "us-east5" }
{ "provider": "exec", "model": "openai/gpt-5", "exec": "/home/user/project/tools/litellm-transport", "api_key_file": "/home/user/project/.secrets/openai.key" }
```

[models.md](models.md) specifies every option, the credential file shapes,
and what each provider cannot express. The `model` block does not
participate in identity: the model is runtime infrastructure, and a system
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

### `programs`

Object mapping program name to a nested configuration. Optional. Each value
is a full configuration document without `version`, `task`, or `sandbox`.
The `model` block is optional and follows the inheritance rule above. A name
listed in `grants.spawn` must appear here.

A child program's grants must be a subset of its parent's, checked at
construction. Each child program's identity participates in the parent's
identity. Its tool list may differ from its parent's. The effective tool
authority `foe plan` reports covers the root, each descendant program a
`grants.spawn` entry reaches, and each workflow model node. A declaration
no such path reaches stays in the resolved program and is absent from the
report, because no episode can invoke it.

### `workflow`

Object. Optional. A declared graph of nodes that replaces the free loop
for this episode. [workflow.md](workflow.md) specifies every key under it
and every construction rule. The document's authority and budget are the
ceiling the graph draws from: a tool node names a tool in `tools`, and a
model node's program is a child program in the sense of `programs` whose
tools, configured executable authority, host tool definitions, filesystem
grants, spawn grants with their descendant programs, and spend limits all
lie within the document's own. A child program may carry a `workflow` of
its own. The graph participates in identity as workflow.md "Identity"
lists.

### `task`

String. Required. What this episode is to do. Written into the log as the
first inbox item. The task does not participate in identity; two episodes of
the same program with different tasks share an identity.

## Identity summary

The following participate in identity: `name`, `instructions`, `tools` and
their order, each entry of `tool_defs` including the executable's content
hash, the kinds and counts in `grants`, `budget`, `done_when`, `context`,
every entry of `programs`, `workflow`, and the runtime's version and build.

The following do not: the paths in `grants`, `model`, `sandbox`, and `task`.

## Errors

Every error at construction names the key that caused it and the rule it
violated. An error in an embedded schema also names the subschema and the
keyword. Construction fails before any process starts and before any log is
written. A document that passes construction will run; what remains uncertain
is the model's behavior and the world's.
