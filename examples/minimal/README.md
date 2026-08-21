# Minimal

The smallest configuration that runs with the built-in model transport. It
grants one directory for reading and writing, lists the four built-in coding
tools (`read`, `grep`, `edit`, `bash`), sets a budget of twenty model calls,
and names a provider and a model. `done_when` is absent, so the episode
completes when the model produces a turn with no tool calls, and that turn's
text is the outcome value.

The `model` block names no key file. The key is read from
`~/.config/foe/credentials/anthropic.json`, which `foe login anthropic`
writes; the log records that path in `episode/start.program.model`. A block
that names `api_key_file` reads that file instead. `docs/models.md`
describes both.

## Paths to replace

- `/home/user/project`: the repository the agent works in.

## Run

```
foe login anthropic
foe --config examples/minimal/config.json
```

`foe plan --config examples/minimal/config.json` prints the resolved program
and its identity without running anything.

## What to look for

In the log, `seq` 0 is `episode/start` and `seq` 1 is the `inbox/item` with
source `task`. One `request/header` with reason `initial` precedes the first
`model/request`; no further header appears, because the system prompt and the
tool schemas do not change. Each step is a `model/request`, a run of
`assistant/chunk` events, one `assistant/message`, and one `tool/result` per
tool call. The last event is `episode/end` with an outcome of kind
`completed`, and the process exits with code 0.

In the viewer, the left pane shows one episode, the budget line counts model
calls against 20, and the sandbox line shows the Landlock version the kernel
provided, or 0 when it provided none.
