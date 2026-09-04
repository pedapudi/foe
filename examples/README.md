# Examples

Every example runs. Each creates a disposable project under `target/`,
uses a deterministic model response, checks its own result, and leaves an
episode log to read. None needs a deployment credential, an external
network, or a repository of your own.

Eleven examples are started by `run.sh` and three by `run.py`:

```sh
sh examples/minimal/run.sh
python3 examples/embed-an-execution-contract/run.py
```

`scripts/examples.sh` runs all fourteen against one binary and reports how
long each took. Continuous integration runs it, and it is the slow tier of
the test suite: about fifteen seconds, of which the recovery-exhausted
example is eight, because that example waits the whole retry backoff rather
than pretending to.

```sh
cargo build -p foe
scripts/examples.sh target/debug/foe
```

The workflow, sandbox, and self-extension examples also have Bazel targets.
The targets run against the release binary, and the build executes their
deterministic forms as tests:

```sh
bazel run //examples/workflow
bazel test //examples/...
```

A run prints the command that opens its episode in the viewer. The sandbox
example needs Linux with Landlock; the rest run anywhere foe builds.

## Start here

| I want to | example |
|---|---|
| fix a failing test without watching it | [minimal](minimal/) |
| give the agent a tool that knows nothing about foe | [wrap-a-binary](wrap-a-binary/) |
| stop when a checker says the work is done rather than when the model says so | [wrap-a-binary](wrap-a-binary/) |
| delegate reading to cheap children and make the change myself | [subagents](subagents/) |
| have children report to each other rather than only to me | [team](team/) |
| fix the order of the work and let the model choose only within it | [workflow](workflow/) |
| prove the agent cannot read what I did not grant | [sandbox](sandbox/) |
| drive foe from my own contract | [embed-an-execution-contract](embed-an-execution-contract/) |
| keep my tools in Python and let foe call the model | [embed-with-a-model-block](embed-with-a-model-block/) |
| have foe evaluate and improve its own source, test, and specification | [self-extension](self-extension/) |
| reach a model foe has no provider for | [exec-transport](exec-transport/), [host-transport](host-transport/) |

## When a run does not succeed

An episode ends in one of four ways, and a contract that runs unattended has
to recognise all of them. These three examples produce the ones that are not
success, so that the log an operator will one day have to read is one they
have seen before.

| outcome | what it means | example |
|---|---|---|
| `exhausted` | the contract did not break; it ran out of the allowance the configuration gave it | [budget-exhausted](budget-exhausted/) |
| `blocked` · `recovery-exhausted` | the provider failed every attempt the retry ceiling allowed | [recovery-exhausted](recovery-exhausted/) |
| `blocked` · `verification-unsatisfiable` | the model reported the work finished and the declared verifier disagreed, repeatedly | [verification-unsatisfiable](verification-unsatisfiable/) |

## What each one exercises

| example | mechanism |
|---|---|
| [minimal](minimal/) | the smallest coding contract with host-owned responses |
| [wrap-a-binary](wrap-a-binary/) | one executable used as an episode tool and completion verifier |
| [subagents](subagents/) | child contracts under narrower grants, with budget reserved from the parent's pool and returned |
| [team](team/) | children exchanging durable peer messages through their lead |
| [workflow](workflow/) | declared tool and model nodes, typed branching, verification, and recovery |
| [sandbox](sandbox/) | a configured executable under a required Landlock policy |
| [embed-an-execution-contract](embed-an-execution-contract/) | the Python SDK: a contract supplying the model, its own host tools, and acting on the outcome |
| [embed-with-a-model-block](embed-with-a-model-block/) | the Python SDK with the model left to foe: a `model` block beside Python host tools, and the episode's process identity |
| [self-extension](self-extension/) | a direct episode and an evaluator-to-terminal-node workflow improving a disposable copy of foe's source, test, and specification |
| [exec-transport](exec-transport/) | an executable translating foe's requests for another model client |
| [host-transport](host-transport/) | a host process supplying model chunks over the line protocol |
| [budget-exhausted](budget-exhausted/) | a limit reached while the work is unfinished |
| [recovery-exhausted](recovery-exhausted/) | the retry ceiling, its growing delay, and what the log records |
| [verification-unsatisfiable](verification-unsatisfiable/) | `done_when`, the `verify` inbox source, and a condition the work never meets |

## Reading a configuration

A configuration carries visible absolute path markers such as
`/home/user/project`. Each runner replaces them through
[`support/materialize.py`](support/) before running, so a marker is what you
edit when you point an example at a repository of your own, and nothing you
have to fix before an example will run.

`foe plan --config FILE` resolves every grant and every executable path
against the filesystem, so it does not accept a configuration that still
holds its markers. It reports
`grants.read[0]: names an existing path: /home/user/project` and exits 1.
Run it against the materialized configuration a runner leaves in its run
directory.

[`embed-an-execution-contract`](embed-an-execution-contract/) and
[`embed-with-a-model-block`](embed-with-a-model-block/) carry no
configuration file, because the Python package builds the document in
memory. Their contracts are the `foe.ExecutionContract` that
`triage_contract` and `review_contract` return in each `run.py`.

The executable-transport example starts a response process for each request.
The host-transport example supplies responses over the protocol. The
model-block embedding example uses a loopback HTTP endpoint to exercise the
built-in client without reaching an external network.

## Against a real model

[`docs/models.md`](../docs/models.md) specifies the configuration for each
runtime-owned endpoint category. A root configuration without a `model` block
delegates each request to its host process.

## What the build checks

The Rust integration tests validate every `config.json` against the printed
schema, materialize its markers, and run `foe plan`, which catches a missing
executable, a grant that cannot hold, and a workflow graph that cannot run.
`cargo test --workspace` runs them. `bazel test //examples/...` runs the
workflow, sandbox, and self-extension examples themselves, so each of those
three READMEs describes a log the build produces. Running the other eleven is
what checks that their "What to look for" sections still hold.
