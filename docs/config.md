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
program and its identity without running anything.

## A complete example

```json
{
  "version": 1,
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

  "budget": { "model_calls": 40, "tokens": 400000, "seconds": 1800 },

  "done_when": { "verify": "check", "retries": 2 },

  "model": {
    "provider": "anthropic",
    "model": "claude-opus-5",
    "api_key_file": "/home/user/.config/foe/anthropic.key"
  },

  "sandbox": { "mode": "best-effort" },

  "task": "tests/test_parser.py::test_nested_brackets fails. Make it pass without changing the test."
}
```

## The minimal document

```json
{
  "version": 1,
  "name": "hello",
  "instructions": { "role": "You are a coding agent." },
  "tools": ["read", "grep", "edit", "bash"],
  "grants": { "read": ["/home/user/project"], "write": ["/home/user/project"] },
  "budget": { "model_calls": 20 },
  "model": { "provider": "anthropic", "model": "claude-opus-5", "api_key_file": "/home/user/.config/foe/anthropic.key" },
  "task": "Fix the failing test in tests/parser_test.py."
}
```

When a host process supplies the model transport, the `model` block is
omitted.

## Keys

### `version`

Integer. Required. The configuration format version. This document describes
version 1.

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
   `steer`, `notify`, `send`, `team`.
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
| `params` | object | yes | JSON Schema for the arguments |
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
it. Symbolic links are resolved before the check, and a link that resolves
outside every granted root is denied. There is no pattern syntax.

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
| `tokens` | integer | no | unlimited | maximum input plus output tokens across all requests |
| `seconds` | integer | no | unlimited | wall-clock limit for the episode |
| `max_depth` | integer | no | 1 | how many levels of child episodes may exist below this one; 0 forbids spawning |
| `max_episodes` | integer | no | 8 | lifetime count of episodes in the tree, including this one |
| `max_concurrent` | integer | no | 4 | children running at once |
| `loop_threshold` | integer | no | 3 | consecutive identical tool calls, or identical assistant turns, that end the episode as blocked |

Every limit applies to the whole tree below this episode. A child's budget is
reserved from its parent's remainder.

### `done_when`

Object. Optional. Declares when the episode is complete. When absent, the
episode completes when the model produces a turn with no tool calls.

| field | type | meaning |
|---|---|---|
| `verify` | string | name of a tool in `tools`; the episode completes when the model finishes and this tool returns no findings |
| `retries` | integer | how many times findings are fed back; default 2 |
| `returns` | object | a JSON Schema; the episode completes when the model calls the synthesized `return` tool with a conforming value |

`verify` and `returns` may both be present. The verifier then checks the
returned value.

A verifier is invoked once per candidate, with the candidate as its input and
an empty argument list. For a tool in `tool_defs`, the candidate is passed
as JSON on standard input and the executable reports findings as lines on
standard output; an empty standard output means no findings, regardless of
exit code. For a host tool, the candidate is the single argument and the
returned value is a list of finding strings. A verifier is therefore a
program written to this contract; a general-purpose linter is wrapped by a
short script that reads the candidate, runs the linter, and prints its
findings.

### `model`

Object. Optional. When present, foe calls the model itself. When absent, the
host process must answer model requests over the [protocol](protocol.md).

| field | type | required | meaning |
|---|---|---|---|
| `provider` | string | yes | `anthropic` or `openai-compatible` |
| `model` | string | yes | the model identifier the provider expects |
| `api_key_file` | string | yes | absolute path to a file whose contents are the key |
| `base_url` | string | no | overrides the provider's default endpoint |
| `max_output_tokens` | integer | no | per-request output limit; default is the provider's |

The key is read from the named file. It is never read from the environment.
The `model` block does not participate in identity: the model is runtime
infrastructure, and a system that needs to record which model ran reads it
from the log's `request/header` events.

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
is a full configuration document without `version`, `task`, `model`, or
`sandbox`, which are inherited from the parent. A name listed in
`grants.spawn` must appear here.

A child program's grants must be a subset of its parent's, checked at
construction. Each child program's identity participates in the parent's
identity.

### `workflow`

Object. Optional. A declared graph of nodes that replaces the free loop
for this episode. [workflow.md](workflow.md) specifies every key under it
and every construction rule. The document's `tools`, `grants`, `budget`,
and `done_when` are the ceiling the graph draws from: a tool node names a
tool in `tools`, and a model node's program is a child program in the sense
of `programs`, checked to be a subset the same way. A child program may
carry a `workflow` of its own. The graph participates in identity as
workflow.md "Identity" lists.

### `task`

String. Required. What this episode is to do. Written into the log as the
first inbox item. The task does not participate in identity; two episodes of
the same program with different tasks share an identity.

## Identity summary

The following participate in identity: `name`, `instructions`, `tools` and
their order, each entry of `tool_defs` including the executable's content
hash, the kinds and counts in `grants`, `budget`, `done_when`, every entry of
`programs`, `workflow`, and the runtime's version and build.

The following do not: the paths in `grants`, `model`, `sandbox`, and `task`.

## Errors

Every error at construction names the key that caused it and the rule it
violated. Construction fails before any process starts and before any log is
written. A document that passes construction will run; what remains uncertain
is the model's behavior and the world's.
