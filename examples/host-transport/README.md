# Host transport

A configuration with no `model` block, run by a Python program that supplies
the model transport. When `model` is absent, foe has no credentials and no
network; each model call is a `model/request` event written to standard
output, and the process that launched foe answers it with `model/chunk`
lines on standard input. The Python package in `python/` does that
exchange. `run.py` gives it a transport that ignores the request and plays a
script: a `read` tool call on the first step and a final sentence on the
second. The episode that results is a complete log with every request and
every response recorded, produced without any model.

A transport that calls a real model has the same signature: an asynchronous
callable that receives one request and yields chunk objects in the shape
`docs/protocol.md` defines under `model/chunk`. `docs/sdk.md` describes the
package and its reference adapter.

## Paths to replace

- `/home/user/project`: a directory containing a `README.md`; in
  `config.json` and in `run.py`.
- `/usr/local/bin/foe` in `run.py`: the absolute path of the foe binary.

## Run

```
cd python && uv run python ../examples/host-transport/run.py
```

The script prints the outcome, `Completed(value='The README describes the
project and how to build it.')`, and leaves the log at
`examples/host-transport/episode/episode.jsonl`.

## What to look for

The `request/header` event carries the route `host`/`host`, because the
host owns the real route and foe does not know it. The first `model/request` has
`consumed: [1]`, the task, and one user message. The
`assistant/chunk` events that follow are the script's chunks, recorded by
foe as they arrived; the `assistant/message` assembled from them has one
tool call and `stop: "tool"`. A `tool/result` for the `read` call follows,
and the second `model/request` carries three derived messages: user,
assistant, tool. The second `assistant/message` has `stop: "end"` and no
tool calls, so the episode ends `completed` with that text as the value.

In the viewer, the conversation pane shows the same sequence, and the
header line shows the route `host`.
