# Exec transport

A configuration whose model is a program of your own. When `model.provider`
is `exec`, foe starts the file named by `model.exec` once per model request,
writes one `model/request` line to its standard input, and reads
`model/chunk` lines from its standard output. The program holds its own
credential and speaks to whatever it likes; foe records the exchange in the
log exactly as it records a built-in client. `docs/models.md` specifies the
two line shapes.

`litellm-transport` in this directory is the whole program: it translates
the request into a `litellm.completion` call with streaming on, relays text
and tool-call fragments as chunks, and ends with a `done` chunk carrying the
stop reason and the token counts. Its first line names the interpreter of a
virtual environment in which `litellm` is installed. `litellm` routes by the
model name's prefix, so `openai/gpt-5` reaches OpenAI and
`anthropic/claude-opus-5` reaches Anthropic; the key file named in
`model.api_key_file` reaches the program as `options.api_key_file`.

## Paths to replace

- `/home/user/project`: a directory containing a `README.md`, a `tools`
  directory, and a virtual environment at `.venv` with `litellm` installed;
  in `config.json` and in the first line of `litellm-transport`.
- `/home/user/project/tools/litellm-transport`: where the program is copied.
- `/home/user/project/.secrets/openai.key`: a file whose whole contents are
  the key. It lies under the read root because the program runs under the
  episode's sandbox, with an empty environment, and can read nothing else.

## Run

```
cp examples/exec-transport/litellm-transport /home/user/project/tools/litellm-transport
chmod +x /home/user/project/tools/litellm-transport
foe --config examples/exec-transport/config.json
```

`foe plan --config examples/exec-transport/config.json` prints a `model`
line naming the program and the fact that foe reads no credential for it.

## What to look for

The `request/header` event carries the route `exec`/`openai/gpt-5`. The
`assistant/chunk` events are the program's chunks, recorded by foe as they
arrived; they arrive together when the program exits, because the executor
captures its output whole. The `episode/start.program.model` block names
the program and the key file. In the viewer, the header line shows the
route `exec`.
