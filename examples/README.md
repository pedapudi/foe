# Examples

Each directory contains one configuration and a README that explains the
runtime mechanism it exercises. Configuration files use visible absolute
path markers such as `/home/user/project`. Replace each marker before running
a template against another repository.

Two examples include self-contained runners. They create disposable projects
under `target/`, use a deterministic local model transport, and check their
own results. They require no provider credential.

```sh
bazel run //examples/workflow
bazel run //examples/sandbox
bazel test //examples/...
```

Each `bazel run` command builds foe and leaves an inspectable episode under
`target/`. The `bazel test` command validates both examples under Bazel's test
directory. The sandbox example requires Linux with Landlock support.

| example | mechanism | execution |
|---|---|---|
| [workflow](workflow/) | declared tool and model nodes, typed branching, verification, and child logs | self-contained end-to-end runner |
| [sandbox](sandbox/) | a configured executable restricted by a required Landlock policy | self-contained end-to-end runner |
| [minimal](minimal/) | the smallest model-backed coding program | configuration template |
| [wrap-a-binary](wrap-a-binary/) | an executable used as a model tool and verifier | configuration template plus wrapper |
| [subagents](subagents/) | child programs with narrower grants and reserved budgets | configuration template |
| [team](team/) | child programs that exchange durable peer messages | configuration template |
| [host-transport](host-transport/) | a host process that supplies model chunks over the line protocol | Python runner with scripted responses |
| [exec-transport](exec-transport/) | an executable that translates foe requests to another model client | configuration template plus transport |

Every `config.json` is validated against the printed schema by the Rust test
suite. The integration tests also materialize each path marker and run
`foe plan`, which catches missing executables, invalid grant relationships,
and workflow graph errors.
