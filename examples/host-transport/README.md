# Host transport

A configuration with no `model` block, run by a Python program that supplies
the model transport. When `model` is absent, foe has no credentials and no
network; each model call is a `model/request` event written to standard
output, and the process that launched foe answers it with `model/chunk`
lines on standard input. The Python package in `python/` does that exchange.

`run.py` gives it a transport that ignores the request and plays two fixed
responses: a `read` tool call on the first step and a final sentence on the
second. The episode that results is a complete log with every request and
every response recorded, produced without any model.

A transport that calls a real model has the same signature: an asynchronous
callable that receives one request and yields chunk objects in the shape
`docs/protocol.md` defines under `model/chunk`. `docs/sdk.md` describes the
package and its reference adapter.

This example demonstrates one seam, the model call. For an application that
also supplies its own tools and acts on the outcome, see
[`../embed-in-a-program/`](../embed-in-a-program/).

## Run

From the repository root, after `cargo build --release --bin foe`:

```sh
python3 examples/host-transport/run.py
```

A binary at another path is given as the single argument:

```sh
python3 examples/host-transport/run.py /absolute/path/to/foe
```

The package depends on the standard library alone, so `run.py` puts
`python/` on the import path and needs no installation step. Each run
creates `target/foe-host-transport-demo.XXXXXX/`, holding the materialized
configuration, the disposable project, and the episode log. The runner
prints the outcome, `Completed(value='The README describes the project and
how to build it.')`, and a command that serves the viewer for that log.

## What to look for

The `request/header` event carries the route `host`/`host`, because the host
owns the real route and foe does not know it. The first `model/request` has
`consumed: [1]`, the task, and one user message. The `assistant/chunk`
events that follow are the transport's chunks, recorded by foe as they
arrived; the `assistant/message` assembled from them has one tool call and
`stop: "tool"`. A `tool/result` for the `read` call follows, and the second
`model/request` carries three derived messages: user, assistant, tool. The
second `assistant/message` has `stop: "end"` and no tool calls, so the
episode ends `completed` with that text as the value.

In the viewer, the conversation pane shows the same sequence, and the header
line shows the route `host`.

## What the runner checks

- the first `request/header` names the route `host`/`host`;
- the first `model/request` consumed the task alone;
- the first `assistant/message` has `stop: "tool"` and one tool call, and
  the `tool/result` after it is the `read` call's;
- the second `model/request` carries the three derived messages user,
  assistant, and tool;
- the second `assistant/message` has `stop: "end"` and no tool calls;
- the outcome is `completed` with the sentence the transport produced.
