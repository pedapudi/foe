# Embed foe in a contract

An application that drives foe rather than a person driving it. `run.py` is
a defect triage service: it declares the contract in Python, supplies the
model call and one tool of its own, runs an episode per report, and decides
what happens to each report from the outcome it gets back.

This is the shape foe exists for. The application owns the model call, the
credentials, its own data, and every decision made about a result. foe owns
the episode: the system prompt, the tool schemas, the step loop, the budget,
the grants, the sandbox, and the log. `docs/sdk.md` documents the package
that joins them.

This directory has no `config.json`, because the configuration is Python.
`foe.ExecutionContract` is the configuration document that `docs/config.md` specifies,
built from typed arguments and validated before any process starts.

## Run

From the repository root, after `cargo build --release --bin foe`:

```sh
python3 examples/embed-an-execution-contract/run.py
```

A binary at another path is given as the single argument:

```sh
python3 examples/embed-an-execution-contract/run.py /absolute/path/to/foe
```

The package depends on the standard library alone, so `run.py` puts
`python/` on the import path and needs no installation step. The model backend
plays fixed responses, so the example needs no provider credential and makes
no network request. Each run creates `target/foe-embedding-demo.XXXXXX/`,
holding the reports and one episode log per report. The runner prints a
command that serves the viewer for the first episode.

## What the application supplies

**A model backend.** `model_backend_from` returns an asynchronous callable that
receives one request and yields chunk objects. The runtime never learns
which model answered, and the log records the route as `host`/`host`. A
model backend against a real endpoint has the same signature;
`foe.adapters.litellm.litellm_model_backend` is one.

**A tool the application implements.** `build_record` is an ordinary Python
function under `@foe.tool`. The decorator derives the tool's name,
description, and parameter schema from the function, and the package writes
that specification into the document under `host_tools`. When the model
calls it, the runtime emits a `host/tool-call` event and the package runs
the function in this process, where the application's build store lives. An
exception inside the function becomes an error result the model reads, and
the episode continues; the unknown build in the second report reaches that
path. `@build_record.render` sets the text the model sees in place of the
record.

**A contract.** `triage_contract` builds a `foe.ExecutionContract` naming the
instructions, the tools, the read grant, the budget, and the termination
condition. `tools=["read", "block", build_record]` mixes the two sources: a
string names a built-in tool, and a callable is a host tool. `block` is
listed because a contract running unattended needs the agent to be able to
report that a task cannot be done. `done_when=foe.Returns(Triage)` derives
the completion schema from the dataclass, so a completed episode's value is
an object with that shape. `contract.fingerprint(binary)` is the hash of all of
it, which the runner prints; the three episodes share it, because the task
does not participate in the fingerprint.

## What the application decides

`act_on` matches the outcome union. Three reports exercise three of its four
arms.

| report | what happens | outcome |
|---|---|---|
| `nested-brackets` | the report names a build the store holds, and the agent returns a triage | `Completed` |
| `unknown-build` | the store has no record of the build, and the agent calls `block` | `Blocked("goal-unreachable", …)` |
| `no-build-named` | the report names no build, and the agent tries recent identifiers until the budget is spent | `Exhausted("model_calls")` |

A contract that runs episodes unattended reaches all four arms in production.
Handling `Completed` alone would file the first report, drop the second in
silence, and retry the third forever.

## What the runner checks

- the first report ends `completed` with a value that constructs a `Triage`
  whose component is `parser`;
- its log holds one `host/tool-call` for `build_record`, and the
  `tool/result` after it carries the text `@build_record.render` produced,
  so the tool ran in the application's process;
- the second report ends `blocked` with the code `goal-unreachable` and the
  message the agent gave, and its log holds the error result that the
  unknown identifier produced, `{"error": "KeyError: 'b-9999'"}`;
- the third report ends `exhausted` with the limit `model_calls`.
