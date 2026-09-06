# Wrap a binary

Any executable with a command line becomes a tool by declaring it in
`tool_defs`. This example wraps a style checker in three lines of prose:
`exec` names the file, `description` tells the model how to call it, and
`instruction` tells the model when. The model's `args` list becomes the
argument vector, without a shell; standard output, standard error, and the
exit code come back as the result, and a non-zero exit is a result rather
than an error. The same entry serves as the verifier: `done_when.verify`
names it. A model finish and a non-error call to that tool each propose a
candidate. The episode completes when the checker prints nothing.

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

The runtime hashes the file's contents into the contract fingerprint, so
editing the checker changes the fingerprint.

## Responses supplied by the host

`responses.py` defines five deterministic answers. `run_with_host.py` supplies
them through the host protocol, so the example needs no credential and opens
no network connection. The binary still runs every tool call, verifier, and
completion rule.

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

`foe plan --config FILE` reports the resolved tools with each one's source,
and lists `style` as a configured executable. It resolves every path in the
document, so it is run against the configuration inside a run directory rather
than the checked-in one, whose `/home/user/project` exists on no machine:

```sh
target/release/foe plan --config target/foe-wrap-a-binary-demo.XXXXXX/config.json
```

## The project the runner builds

```text
target/foe-wrap-a-binary-demo.XXXXXX/
├── config.json                the configuration with /home/user/project replaced
├── project/
│   ├── src/report.py          one unused import and one line of 164 columns
│   └── tools/style-check      the wrapped checker, copied from this directory
└── episode/<episode-id>/episode.jsonl
```

Before starting the episode the runner runs the checker itself and requires
exactly the two findings named above, so a run always begins from a source
file the checker rejects.

## What the run produces

The host answers five requests, one turn each. The first response runs the
checker. That tool call proposes completion because `style` is also the
verifier. The runtime runs `style-check` again and returns both findings in
an `inbox/item` with source `verify`.

The next responses remove the unused import and finish. The verifier returns
the remaining long-line finding. A later response splits the long statement and calls
the checker again. The successful checker call proposes completion, and the
separate verifier run accepts it. The episode ends without another model
request. `retries: 2` allows two sets of findings. A third set would end the
episode as `blocked` with code `verification-unsatisfiable`.

A verifier run leaves no `tool/result`, because the runtime invokes it
separately from the model. Its findings appear in a `verify` inbox item. An
empty result permits a completed outcome without adding an inbox item.

`run.sh` then checks the log and the project:

- The `request/header` carries the `style` schema among the tools, and the
  `instruction` text appears in the system prompt after the instruction
  sections.
- Two `tool/result` events name `style` and two name `edit`.
- The first checker call exits zero while reporting the unused import, which
  is the exit discipline a verifier requires.
- The second checker call comes after the second edit and its standard output
  is empty.
- Exactly one assistant message stops with `end`, and exactly two
  `inbox/item` events have source `verify`. The first follows the model's
  checker call. The second follows the model's finish. Both precede the edit
  that resolves their findings.
- No model request follows the successful checker call.
- The last event is `episode/end` and its outcome kind is `completed`.
- The runner runs `style-check` over the finished project itself and requires
  empty output.

## In the viewer

The two verifier findings appear as user messages whose source is labeled.
Each `style` call shows its exit code and both output streams. Each `edit`
call shows its unified diff. The successful final checker call is followed
directly by the completed outcome.
