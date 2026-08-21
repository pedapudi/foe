# Exec transport

A configuration whose model is a program of your own. When `model.provider`
is `exec`, foe starts the file named by `model.exec` once per model request,
writes one `model/request` line to its standard input, and reads
`model/chunk` lines from its standard output. The program holds its own
credential and speaks to whatever it likes; foe records the exchange in the
log exactly as it records a built-in client. `docs/models.md` specifies the
two line shapes.

The directory holds two such programs.

| file | what it answers with |
|---|---|
| `scripted-transport.py` | fixed chunks, so the example runs without a credential and without network |
| `litellm-transport` | a real provider, through `litellm.completion` with streaming on |

`config.json` names the first one. The runner creates a disposable project,
copies the program into it, materializes the configuration with absolute
paths, runs foe, and checks the episode log.

The runner requires `/usr/bin/python3`.

## Run

From the repository root:

```sh
bazel run //examples/exec-transport
```

The target builds `//:foe` and runs `run.sh`. A binary at another path can
run the example directly:

```sh
examples/exec-transport/run.sh /absolute/path/to/foe
```

Each run creates `target/foe-exec-transport-demo.XXXXXX/`, holding the
materialized configuration, the disposable project, and the episode log.
The runner prints a command that serves the viewer for that log.

## What the program receives

The configuration's `model` block is the whole interface.

```json
"model": {
  "provider": "exec",
  "exec": "/home/user/project/tools/scripted-transport.py",
  "model": "exec-transport-demo",
  "readme": "/home/user/project/README.md"
}
```

`provider`, `exec`, `model`, and `max_output_tokens` are read by the
runtime. Every other key travels to the program as `options` on the request
line, which is how a program learns where its own credential or its own data
lives. This example uses `readme` for that: the program answers the first
request with a `read` call on the path the option names, and the second with
one sentence of text.

The program runs under the episode's sandbox, narrowed as for a configured
tool. It may read the episode's read roots, it may execute its own file, it
may open TCP connections, and it starts with an empty environment. Every
other file it opens must therefore lie under a read root. `run.sh` copies
both `scripted-transport.py` and `examples/support/chunks.py` into the
project directory for that reason, and `model.exec` names the copy.

## Answering a real provider instead

`litellm-transport` translates the request into a `litellm.completion` call
with streaming on, relays text and tool-call fragments as chunks, and ends
with a `done` chunk carrying the stop reason and the token counts. `litellm`
routes by the model name's prefix, so `openai/gpt-5.6-sol` reaches OpenAI
and `anthropic/claude-opus-5` reaches Anthropic.

Three edits point the configuration at it, all within the read root that the
sandbox permits:

- copy `litellm-transport` beside a virtual environment that has `litellm`
  installed, and change its first line to that environment's interpreter;
- set `model.exec` to the copy and `model.model` to a routed model name;
- replace the `readme` option with `api_key_file`, naming a file whose whole
  contents are the key. The program reads it as `options.api_key_file`.

## What to look for

The `request/header` event carries the route `exec` and the model name from
the configuration. The `assistant/chunk` events are the program's chunks,
recorded by foe as they arrived; they arrive together when the program
exits, because the executor captures its output whole. The
`episode/start.program.model` block names the program and every option
passed to it. In the viewer, the header line shows the route `exec`.

## What the runner checks

- the `request/header` route is `exec` with the model name `exec-transport-demo`;
- the `tool/result` for the `read` call carries the project README's text,
  so the program's tool call reached the runtime and the grant permitted it;
- the outcome is `completed` with the sentence the program's second answer
  produced.
