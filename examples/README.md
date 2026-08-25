# Examples

Every example runs. Each creates a disposable project under `target/`,
answers the model from a script rather than a provider, checks its own
result, and leaves an episode log to read. None needs a credential, a
network, or a repository of your own.

Eleven examples are started by `run.sh` and two by `run.py`:

```sh
sh examples/minimal/run.sh
python3 examples/embed-in-a-program/run.py
```

`scripts/examples.sh` runs all thirteen against one binary and reports how
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
| drive foe from my own program | [embed-in-a-program](embed-in-a-program/) |
| have foe evaluate and improve its own source, test, and specification | [self-extension](self-extension/) |
| reach a model foe has no provider for | [exec-transport](exec-transport/), [host-transport](host-transport/) |

## When a run does not succeed

An episode ends in one of four ways, and a program that runs unattended has
to recognise all of them. These three examples produce the ones that are not
success, so that the log an operator will one day have to read is one they
have seen before.

| outcome | what it means | example |
|---|---|---|
| `exhausted` | the program did not break; it ran out of the allowance the configuration gave it | [budget-exhausted](budget-exhausted/) |
| `blocked` · `recovery-exhausted` | the provider failed every attempt the retry ceiling allowed | [recovery-exhausted](recovery-exhausted/) |
| `blocked` · `verification-unsatisfiable` | the model reported the work finished and the declared verifier disagreed, repeatedly | [verification-unsatisfiable](verification-unsatisfiable/) |

## What each one exercises

| example | mechanism |
|---|---|
| [minimal](minimal/) | the smallest model-backed coding program |
| [wrap-a-binary](wrap-a-binary/) | an executable serving as both a model tool and a `done_when` verifier |
| [subagents](subagents/) | child programs under narrower grants, with budget reserved from the parent's pool and returned |
| [team](team/) | children exchanging durable peer messages through their lead |
| [workflow](workflow/) | declared tool and model nodes, typed branching, verification, and recovery |
| [sandbox](sandbox/) | a configured executable under a required Landlock policy |
| [embed-in-a-program](embed-in-a-program/) | the Python SDK: a program supplying the model, its own host tools, and acting on the outcome |
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

[`embed-in-a-program`](embed-in-a-program/) carries no configuration file,
because the Python package builds the document in memory. Its program is
the `foe.Program` that `triage_program` returns in `run.py`.

Every example that runs without a provider names the `exec` provider and
points it at a transport script. [`support/README.md`](support/) explains
where such a script may run and what it may read, which is narrower than it
looks.

## Against a real model

Each README gives the two-line `model` block that points its example at a
provider. A block that names no key file reads the credential
`foe login <provider>` writes, at
`~/.config/foe/credentials/<provider>.json`. Naming `api_key_file` is for
deployments that dictate where a credential lives.

## What the build checks

The Rust integration tests validate every `config.json` against the printed
schema, materialize its markers, and run `foe plan`, which catches a missing
executable, a grant that cannot hold, and a workflow graph that cannot run.
`cargo test --workspace` runs them. `bazel test //examples/...` runs the
workflow, sandbox, and self-extension examples themselves, so each of those
three READMEs describes a log the build produces. Running the other ten is
what checks that their "What to look for" sections still hold.
