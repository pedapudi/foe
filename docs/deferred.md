# Deferred features

This document lists features that the design anticipates and version 1 does
not implement. Each section states what the feature is, that it is absent,
and which log event types, event field values, or configuration keys are
reserved for it. A reserved event type has a variant in `crates/log/src/lib.rs`
so that a version 1 reader parses a later log; nothing in version 1 emits it.
A feature with nothing reserved is listed so that a reader does not search
for it.

## Workflow graphs and recovery nodes

A workflow graph is a declared dataflow of episodes: nodes name programs,
edges carry a node's outcome into the next node's task, and a recovery node
is a node that runs when a predecessor ends blocked or exhausted. The
runtime would schedule nodes, reserve each node's budget from the graph's
pool, and record each node's start, end, and any recovery transition.
Workflow graphs are not implemented. Reserved event types: `workflow/node-start`,
`workflow/node-end`, `workflow/recovery`, each carrying an unconstrained JSON
object. No configuration key is reserved.

## Context compaction

Context compaction replaces a prefix of the conversation with a summary when
the derived message list approaches the model's context limit, so that a
long episode continues with a shorter request. The summary would be recorded
in the log, and the derived message rule would substitute it for the events
it covers. Context compaction is not implemented; an episode whose requests
outgrow the model's context ends when the provider rejects the request.
Reserved event types: `compaction/start`, `compaction/summary`,
`compaction/end`, each carrying an unconstrained JSON object.

## Fork as a command and candidate slates

A fork seeds a new episode from a prefix of an existing log so that the new
episode continues from a chosen point with a different task or program. A
candidate slate runs several forks from the same prefix, so that the shared
prefix is paid once and the branches are causally independent, and then
selects among their outcomes. Seeding a log from a prefix is implemented and
specified under "Seeding" in [log-format.md](log-format.md); a command-line
`fork` subcommand and candidate slates are not implemented. Reserved field
value: `"fork"` for `context` in `spawn/start`, which names a child seeded
from the parent's log rather than started fresh. No configuration key is
reserved.

## Team task board

A team task board is a list of tasks shared by a lead and its members, held
in the lead's log, from which a member claims a task, reports progress, and
marks completion. The board would be folded from the lead's log in the same
way as the roster and the message queue. The task board is not implemented.
Reserved event type: `team/task`, carrying an unconstrained JSON object.

## Correlated request and response over the inbox

A correlated exchange lets one episode ask another a question and match the
answer to the question by identifier, so that a member can wait for a
specific reply rather than for any inbox item. The inbox would carry the
question with one source value and the answer with another, both holding the
same correlation identifier in `message_id`. Correlated exchanges are not
implemented. Reserved field values: `"request"` and `"response"` for `source`
in `inbox/item`.

## Sandbox backends beyond Landlock

Landlock is the Linux kernel facility that foe compiles grants into. Other
backends would enforce the same allow list by other means: bubblewrap builds
a mount namespace containing only the granted paths, gVisor runs the
executable under a user-space kernel, and seccomp filters the system calls an
executable may make. Each would be selected by configuration and recorded in
`episode/start`. No backend other than Landlock is implemented. No event type
or configuration key is reserved; `sandbox.mode` is the only sandbox key, and
`episode/start.sandbox` records only the Landlock version obtained.

## MCP as a tool source

The Model Context Protocol is a wire protocol by which a separate server
offers tools, described by JSON Schema, to a client that calls them. foe
would connect to a configured server, register each offered tool with a
declared effect, and call it over the protocol. MCP is not implemented; the
three tool sources are built-in tools, `tool_defs` executables, and host
tools, as specified under "tools" in [config.md](config.md). No event type or
configuration key is reserved.

## A WASM tool tier

A WebAssembly tool tier would run a tool compiled to WebAssembly inside the
episode process, with the runtime's capability handles as the tool's only
imports, so that a tool needs neither a separate process nor a kernel
sandbox. The WASM tool tier is not implemented. No event type or
configuration key is reserved.

## Fuzzy matching in `edit`

Fuzzy matching would let the built-in `edit` tool apply a replacement whose
anchor text differs from the file in whitespace or indentation, reporting the
difference to the model. The `edit` tool requires an exact match. Fuzzy
matching is not implemented. No event type or configuration key is reserved.

## Tree-sitter symbols

Tree-sitter is a parser library that produces a syntax tree for many
languages. A symbol tool built on it would let the model list the
definitions in a file or find the definition of a name without reading whole
files. Tree-sitter symbols are not implemented. No event type or
configuration key is reserved.

## Code mode

Code mode lets the model write a short program that calls several tools and
combines their results, which the runtime executes as one tool call, rather
than issuing each call as a separate model turn. Code mode is not
implemented. No event type or configuration key is reserved.

## A Bazel build

A Bazel build would define every crate, the browser bundle, and the Python
package as Bazel targets so that a repository that already builds with Bazel
can depend on foe without invoking Cargo. The build is Cargo only. No event
type or configuration key is reserved.

## A TypeScript SDK

A TypeScript SDK would do for a Node.js program what the Python package does:
build a configuration, launch the binary, and serve the host protocol with a
transport the program supplies. The TypeScript SDK is not implemented. No
event type or configuration key is reserved.

## Additional viewer axes

The viewer organizes episodes by lineage: the tree of parents and children
with the conversation of each. Further axes would organize the same logs by
program identity, so that every episode of one program is compared side by
side, and by tool, so that every call of one tool across episodes is listed
together. Axes other than lineage are not implemented. No event type or
configuration key is reserved.

## macOS sandboxing

On macOS the kernel facility comparable to Landlock is the sandbox profile
language used by the `sandbox-exec` mechanism. A macOS backend would compile
grants into such a profile. macOS sandboxing is not implemented; on macOS
`sandbox.mode: "best-effort"` applies no restriction and records
`landlock_abi: 0`, and `sandbox.mode: "required"` refuses to start. No event
type or configuration key is reserved.
