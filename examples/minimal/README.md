# Minimal

The smallest configuration that runs an episode. It grants one directory for
reading and writing, lists the four built-in coding tools (`read`, `grep`,
`edit`, `bash`), sets a budget of twenty model calls, and names a provider and
a model. `done_when` is absent, so the episode completes when the model
produces a turn with no tool calls, and that turn's text is the outcome value.

`run.sh` creates a small Python package whose `bracket_depth` function returns
the depth left at the end of the string rather than the greatest depth it
reached, runs foe against it, and checks the episode log and the repaired
package.

## The model block

The configuration names the `exec` provider and points it at `transport.py`
in this directory. That script answers each model request with fixed chunks
in the wire shape [docs/models.md](../../docs/models.md) defines, so the
example needs no credential and opens no connection while the runtime still
runs every tool call, log event, and completion rule.

To run the same program against a provider, replace the `model` block with:

```json
"model": { "provider": "anthropic", "model": "claude-opus-5" }
```

and run `foe login anthropic` once. That block names no key file, so the key
is read from `~/.config/foe/credentials/anthropic.json`, which the login
writes, and the log records the resolved path in
`episode/start.program.model`. A block that names `api_key_file` reads that
file instead, which is worth doing when the key belongs to one project rather
than to the account running foe. [docs/models.md](../../docs/models.md)
describes both.

## Requirements

`/usr/bin/python3`, and a `foe` binary. Nothing else: the tests the episode
runs use only the Python standard library.

## Run

From the repository root:

```sh
cargo build --release --bin foe
examples/minimal/run.sh
```

`run.sh` takes the binary as its first argument when it is somewhere else:

```sh
examples/minimal/run.sh /absolute/path/to/foe
```

Each run creates `target/foe-minimal-demo.XXXXXX/`, holding the materialized
configuration, the disposable project, and the episode log. The runner prints
a command that serves the viewer for that log.

`foe plan --config FILE` prints the resolved program and its identity without
running anything. It resolves every path in the document, so it is run
against the configuration inside a run directory rather than the checked-in
one, whose `/home/user/project` exists on no machine:

```sh
target/release/foe plan --config target/foe-minimal-demo.XXXXXX/config.json
```

## The project the runner builds

```text
target/foe-minimal-demo.XXXXXX/
├── config.json               the configuration with /home/user/project replaced
├── project/
│   ├── brackets.py           bracket_depth, returning the wrong depth
│   ├── test_brackets.py      three tests, of which test_nested_brackets fails
│   ├── tools/transport.py    the model answers, copied from this directory
│   └── support/chunks.py     copied from examples/support
└── episode/episode.jsonl
```

The transport process runs under the episode's sandbox, which grants it the
episode's read roots and the file it was started from. Both scripts are
therefore copied under the granted project directory; a script left in the
repository could not read the module it imports.

The runner refuses to continue when `test_nested_brackets` passes before the
episode starts, because an episode that repairs nothing proves nothing.

## What the run produces

The transport answers six requests, one turn each: `grep` for the function,
`bash` to run the tests and see the failure, `read` for the function body,
`edit` to track the greatest depth reached, `bash` to run the tests again, and
a final turn of text with no tool call. The last turn ends the episode,
because the configuration declares no other completion rule.

`run.sh` then checks the log and the project:

- `seq` counts from 0 without a gap, `seq` 0 is `episode/start`, and `seq` 1
  is the `inbox/item` with source `task`.
- Exactly one `request/header` exists, its reason is `initial`, it precedes
  the first `model/request`, and every `model/request` points at it. The
  system prompt and the tool schemas do not change, so no second header is
  written.
- Six `model/request` events, against the budget of twenty.
- Every tool call has exactly one `tool/result`, matched by `call_id` and
  naming the same tool, and the five calls are `grep`, `bash`, `read`,
  `edit`, `bash` in that order.
- The first `bash` result exits non-zero and the second exits zero, so the
  edit is what turned the failing test green.
- The last event is `episode/end`, its outcome kind is `completed`, and its
  value is the text of the final turn.
- `brackets.py` returns the greatest depth, and `test_brackets` passes when
  the runner runs it again itself.

## In the viewer

The episodes region lists one episode, because the run directory holds one
log and no children. The details region below it counts six model calls
against twenty. It states the Landlock version and the process cleanup
mechanism the host provided. The conversation tab shows the `edit` call with
its unified diff, and both `bash` calls with their exit codes.
