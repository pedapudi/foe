# Wrap a binary

Any executable with a command line becomes a tool by declaring it in
`tool_defs`. This example wraps a style checker in three lines of prose:
`exec` names the file, `description` tells the model how to call it, and
`instruction` tells the model when. The model's `args` list becomes the
argument vector, without a shell; standard output, standard error, and the
exit code come back as the result, and a non-zero exit is a result rather
than an error. The same entry serves as the verifier: `done_when.verify`
names it, so the episode completes only when the model finishes and the
checker prints nothing.

`style-check` in this directory is the wrapped file. It reports lines wider
than 88 columns and `import` lines whose name appears nowhere else in the
file, one finding per line as `path:line:column: rule message`. The same
three fields wrap `ruff`, `flake8`, or `cargo clippy`; this example ships its
own checker so that a run needs nothing installed.

## The verifier contract

A verifier is invoked with the candidate result as JSON on standard input and
an empty argument list. It reports findings as lines on standard output and
exits with status zero whether or not it found any; any other exit status is
a failure of the verifier itself and ends the episode as `failed`.
`style-check` therefore always exits zero, and it never reads standard input,
so the one file answers both uses. A general-purpose linter that exits
non-zero on findings is wrapped by a short script that runs it and maps that
status to zero.

The runtime hashes the file's contents into the program's identity, so
editing the checker changes the identity.

## The model block

The configuration names the `exec` provider and points it at `transport.py`
in this directory. That script answers each model request with fixed chunks
in the wire shape [docs/models.md](../../docs/models.md) defines, so the
example needs no credential and opens no connection while the runtime still
runs every tool call, the verifier, and the completion rule.

To run the same program against a provider, replace the `model` block with:

```json
"model": { "provider": "anthropic", "model": "claude-opus-5" }
```

and run `foe login anthropic` once. As in
[the minimal example](../minimal/README.md), that block names no key file, so
the key is read from `~/.config/foe/credentials/anthropic.json`, which the
login writes.

## Requirements

`/usr/bin/python3`, and a `foe` binary.

## Run

From the repository root:

```sh
cargo build --release --bin foe
examples/wrap-a-binary/run.sh
```

`run.sh` takes the binary as its first argument when it is somewhere else:

```sh
examples/wrap-a-binary/run.sh /absolute/path/to/foe
```

Each run creates `target/foe-wrap-a-binary-demo.XXXXXX/`, holding the
materialized configuration, the disposable project, and the episode log. The
runner prints a command that serves the viewer for that log.

`foe tools --config FILE` lists the resolved tools with each one's source, and
lists `style` as a configured executable. It resolves every path in the
document, so it is run against the configuration inside a run directory rather
than the checked-in one, whose `/home/user/project` exists on no machine:

```sh
target/release/foe tools --config target/foe-wrap-a-binary-demo.XXXXXX/config.json
```

## The project the runner builds

```text
target/foe-wrap-a-binary-demo.XXXXXX/
├── config.json                the configuration with /home/user/project replaced
├── project/
│   ├── src/report.py          one unused import and one line of 164 columns
│   ├── tools/style-check      the wrapped checker, copied from this directory
│   ├── tools/transport.py     the model answers, copied from this directory
│   └── support/chunks.py      copied from examples/support
└── episode/episode.jsonl
```

The transport process runs under the episode's sandbox, which grants it the
episode's read roots and the file it was started from. Both scripts are
therefore copied under the granted project directory; a script left in the
repository could not read the module it imports.

Before starting the episode the runner runs the checker itself and requires
exactly the two findings named above, so a run always begins from a source
file the checker rejects.

## What the run produces

The transport answers six requests, one turn each. The model runs the
checker, removes the unused import, and finishes. That finish is a candidate
rather than an outcome. The runtime runs `style-check` as the verifier, which
still reports the long line, so the findings arrive as an `inbox/item` with
source `verify` and the model gets another turn. It splits the long statement, runs
the checker to see it print nothing, and finishes again. This time the
verifier prints nothing and the episode completes. `retries: 2` allows the
feedback twice; a third set of findings would end the episode as `blocked`
with code `verification-unsatisfiable`.

A verifier run leaves no `tool/result`, because the runtime invoked it rather
than the model. Its evidence in the log is the `verify` inbox item, and its
absence is what a completed outcome means.

`run.sh` then checks the log and the project:

- The `request/header` carries the `style` schema among the tools, and the
  `instruction` text appears in the system prompt after the instruction
  sections.
- Two `tool/result` events name `style` and two name `edit`.
- The first checker call exits zero while reporting the unused import, which
  is the exit discipline a verifier requires.
- The second checker call comes after the second edit and its standard output
  is empty, so the model saw the file pass before finishing.
- Exactly two assistant messages stop with `end`, and exactly one `inbox/item`
  has source `verify`. The verify item names the long line and sits between
  the model's first finish and its second edit, so the verifier is what sent
  the model back.
- The last event is `episode/end` and its outcome kind is `completed`.
- The runner runs `style-check` over the finished project itself and requires
  empty output.

## In the viewer

The verifier pass appears between the model's first final turn and the
outcome, and the `verify` inbox item appears as a user message whose source is
labeled. Each `style` call shows its exit code and both output streams, and
each `edit` call shows its unified diff.
