# Wrap a binary

Any executable with a command line becomes a tool by declaring it in
`tool_defs`. This example wraps the Python linter `ruff` in three lines of
prose: `exec` names the file, `description` tells the model how to call it,
and `instruction` tells the model when. The model's `args` list becomes the
argument vector, without a shell; standard output, standard error, and the
exit code come back as the result, and a non-zero exit is a result rather
than an error. The same entry serves as the verifier: `done_when.verify`
names it, so the episode completes only when the model finishes and `ruff`
prints nothing.

`ruff-check` in this directory is the wrapped file. It is a short shell
script that runs `ruff check` over `src` with quiet, one-line output, and
appends any arguments the model passes. A verifier is invoked with the
candidate result as JSON on standard input and reports findings as lines on
standard output with exit status 0; any other exit status fails the
verification and ends the episode as `failed`. `ruff` reads nothing from
standard input and exits 1 when it finds violations, so the script maps
that status to 0 and lets only a `ruff` failure exit non-zero. The same
script therefore answers both uses. Copy it to
`/home/user/project/tools/ruff-check` and mark it executable. The runtime
hashes the file's contents into the program's identity, so editing it
changes the identity.

## Paths to replace

- `/home/user/project`: a Python repository with a `src` directory and a
  virtual environment at `.venv` with `ruff` installed.
- `/home/user/project/tools/ruff-check`: where the script is copied; the
  path inside the script to `.venv/bin/ruff` as well.
- `/home/user/.config/foe/anthropic.key`: a file whose whole contents are
  the API key.

## Run

```
cp examples/wrap-a-binary/ruff-check /home/user/project/tools/ruff-check
chmod +x /home/user/project/tools/ruff-check
foe --config examples/wrap-a-binary/config.json
```

`foe tools --config examples/wrap-a-binary/config.json` lists the resolved
tools with each one's source; `ruff` is listed as a configured executable.

## What to look for

The `request/header` event shows the `ruff` schema among the tools and the
`instruction` text appended to the system prompt after the instruction
sections. Each model call of `ruff` produces a `tool/result` whose value
holds the exit code and both output streams.

When the model produces a turn with no tool calls, the runtime runs the
verifier. Findings come back as an `inbox/item` with source `verify`, which
enters the next request as a user message, and the model gets another turn.
`retries: 2` allows this twice. A third set of findings ends the episode as
`blocked` with code `verification-unsatisfiable`. An empty verifier output
ends the episode as `completed`.

In the viewer, a verifier pass appears between the model's final turn and
the outcome, and a `verify` inbox item appears as a user message whose
source is labeled.
