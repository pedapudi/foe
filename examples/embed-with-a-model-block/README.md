# Embed foe with a model block

A Python application that supplies a tool and no model backend. The contract
carries a `model` block, so foe reaches the model through its own client,
holds the credential, and writes each request to the log for the record
alone. The application declares `record_finding` as a host tool, and every
call the model makes to it arrives in the Python process over the host
protocol.

This is the second of the two ways an application can embed foe through the
Python package. [`../embed-an-execution-contract/`](../embed-an-execution-contract/)
is the first: no `model` block, and the application answers every model
request with its model backend. An application whose model abstraction
carries plain text cannot express foe's tool calls faithfully through that
callback, and this shape is what it uses instead. `docs/sdk.md` under "Who
calls the model" states the ownership rule.

This directory has no `config.json`, because the configuration is Python.
`foe.ExecutionContract` is the configuration document that `docs/config.md`
specifies, built from typed arguments and validated before any process
starts.

## Run

From the repository root, after `cargo build --release --bin foe`:

```sh
python3 examples/embed-with-a-model-block/run.py
```

A binary at another path is given as the single argument:

```sh
python3 examples/embed-with-a-model-block/run.py /absolute/path/to/foe
```

The package and loopback endpoint depend on the standard library alone.
`run.py` puts `python/` and `examples/support/` on the import path, so the
example needs no installation step. The `model` block names the
`compatible-http` provider and supplies the loopback endpoint without a
key. Every request passes through foe's HTTP client without reaching an
external network. Each run creates
`target/foe-model-block-demo.XXXXXX/`, holding the disposable project and
the episode log. The runner prints a command that serves the viewer for it.

## Against a deployed endpoint

Replace the provider, model, base URL, and optional credential path in the
block. The application and its host tool remain unchanged. `docs/models.md`
lists every provider and its options.

## What the application supplies

**A tool it implements.** `record_finding` is an ordinary Python function
under `@foe.tool`. The decorator derives the tool's name, description, and
parameter schema from the function, and the package writes that
specification into the document under `host_tools`. When the model calls
it, the runtime emits a `host/tool-call` event and the package runs the
function in this process, where the application's findings list lives.
`@record_finding.render` sets the text the model sees in place of the
returned object.

**No model backend.** `contract.run` and `contract.start` take a
`model_backend` argument only for a contract with no `model` block. This
contract has a block, so passing a model backend is refused before the binary
starts.

**A supervisor's view of the episode.** `start` returns once foe has
written `episode/start`, so `handle.pid` and `handle.runtime` hold the
process id and the build identity before the first model request. The
runner prints all three. An application that enforces a wall-clock budget
of its own, or records which build produced a log, reads them there.

## What to look for

The first `request/header` event names the route
`compatible-http`/`fixture-model`. Three `model/request` events follow. The
host answers none of them. The `assistant/chunk` events after each request
are what foe's HTTP client received.

The two tools resolve in different processes. The `read` call is a built-in
tool, run inside the episode under its Landlock ruleset, and its
`tool/result` carries the README's text. The `record_finding` call leaves
the episode as a `host/tool-call` event and comes back as a `tool/result`
the package wrote, carrying the rendering the application produced.

## What the runner checks

- the first `request/header` names the route
  `compatible-http`/`fixture-model`;
- three `model/request` events were written;
- three unauthenticated requests reached the loopback endpoint with the
  expected path, model, and accumulated tool results;
- exactly one `host/tool-call`, for `record_finding`;
- the `read` result holds the README's text, and the `record_finding`
  result holds the text `@record_finding.render` produced, so the tool ran
  in the application's process;
- the application's findings list holds the one finding the model recorded;
- the outcome is `completed` with the sentence the configured endpoint produced.
