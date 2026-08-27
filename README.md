<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/foe-lockup-dark.svg">
  <img alt="foe" src="docs/brand/foe-lockup-light.svg" width="420">
</picture>

# foe

foe is a runtime for autonomous coding agents. One invocation runs one
bounded unit of work, called an episode, against one task and ends with
exactly one outcome: completed, blocked, exhausted, or failed.

## Autonomous

No step of an episode waits for a person. An interactive harness relies on a
person for four jobs; foe fills each one mechanically.

- The person supplies the objective. foe takes a task fixed at launch, with a
  declared completion condition.
- The person approves consequential actions. foe takes an allow list of
  directories, executables, and child programs, enforced by the kernel.
- The person notices when the agent is stuck. foe detects repeated tool calls
  and repeated reasoning, and gives the model a closed vocabulary of blocking
  conditions to report.
- The person decides when the work is done. foe ends the episode on a spent
  budget, a passing verifier, or a typed return.

Everything the model received and produced is written to one append-only log.
The log is the only state: the viewer, replay, forking, budget accounting, and
team coordination are derived from it. Nothing is reachable unless a
configuration grants it.

## The name

A foe is a unit of energy equal to 10^51 ergs, the energy released by one
core-collapse supernova. One foe is one bounded release of work.

## Status

The runtime, the binary, the viewer, and the Python package are
implemented. No interface is stable.

## Install and build

The source installer builds the Bazel target with the pinned Rust toolchain
and installs the binary under `~/.local/bin`:

```sh
gh api -H "Accept: application/vnd.github.raw+json" \
  repos/pedapudi/foe/contents/install.sh | sh
```

A checkout uses Bazel as its primary build interface:

```sh
bazel build //:foe
```

The public target `//:foe` produces `bazel-bin/crates/cli/foe`. The build
requires neither Node.js nor a JavaScript package manager. Rust contributors
can also use Cargo directly. [docs/build.md](docs/build.md) specifies the
installer, Bazel targets, and contributor commands.

## Running an episode

```
foe login anthropic
foe "describe what this repository does"
```

The first command asks for an API key, checks it, stores it under
`~/.config/foe/`, and sets the default model. The second runs the built-in
coding configuration against the current directory. The providers are
`anthropic`, `openai`, `openai-compatible`, `openrouter`, `openai-codex`,
`vertex`, and `exec`, a program of your own; [docs/models.md](docs/models.md)
describes each.

`foe --help` prints the command set and the options a bare `foe` takes;
`foe <command> --help` prints one command's options, each with the value it
takes, its default, and what it does.

A configuration of your own is one JSON document. This is the smallest one
that runs with the built-in model transport.

```json
{
  "version": 3,
  "name": "hello",
  "instructions": { "role": "You are a coding agent." },
  "tools": ["read", "grep", "edit", "bash"],
  "grants": { "read": ["/home/user/project"], "write": ["/home/user/project"] },
  "budget": { "model_calls": 20 },
  "model": { "provider": "anthropic", "model": "claude-opus-5" },
  "task": "Fix the failing test in tests/parser_test.py."
}
```

```
foe --config hello.json
```

foe validates the document, writes `episode.jsonl` under
`.foe/<episode-id>` in the current directory or under `--log-dir`, serves a
viewer on the loopback interface while the episode runs, and prints the
outcome as one JSON line on standard output when it ends. The exit code is
0 when the outcome is completed, 2 when blocked, 3 when exhausted, and 1
when failed. Under `--host`, standard output carries the log instead and a
host process answers model requests; see [docs/protocol.md](docs/protocol.md).
`examples/` holds thirteen examples, each of which runs. Every one builds a
disposable project, answers the model from a script rather than a provider,
checks its own result, and leaves an episode to read, so none of them needs a
credential or a network. Three of them end in the outcomes that are not
success, because a program that runs unattended has to recognise those too:

```sh
sh examples/minimal/run.sh          # any example with a run.sh
python3 examples/embed-in-a-program/run.py
bazel test //examples/...           # the three with Bazel targets
```

## Evaluation

The dependency-free conformance suite runs deterministic episodes through the
built binary. It checks authority denial, reconstructable evidence, all four
outcome variants, shared child budgets, workflow provenance, and compaction:

```sh
bazel test //evals:conformance_tests
```

The suite requires no model credential. [docs/evaluation.md](docs/evaluation.md)
specifies the conformance checks and the model-backed benchmark protocol.

The model-backed micro evaluation runs five assessed tasks with combined
declared limits of 44,800 input tokens and 11,200 output tokens. Each strict
success requires an accepted artifact, a completed outcome, the intended
harness mechanism, a conformant trace, and reported usage within budget. It
calls a real provider, so it prints the largest spend it can incur and
launches nothing until `--confirm-spend` is given:

```sh
bazel run //evals:micro -- --model openai/gpt-5.6-sol
bazel run //evals:micro -- --model openai/gpt-5.6-sol --confirm-spend
```

## Embedding

A host program launches the binary, reads the log from standard output, and
answers model requests and host tool calls on standard input. The Python
package in `python/` does this and exposes a transport interface so that the
host keeps the model credentials. See [docs/sdk.md](docs/sdk.md).

## Size

Eleven numbers bound the source. Nine are line budgets over Rust, excluding
tests and generated code. The kernel stays under 5,250 lines. The program
contract stays under 1,400, tools under 1,770, workflow under 1,000, context
under 500, view under 600, the command line under 1,300, telemetry under 1,000,
and lineage under 500. The separate tool budget allows capability growth while
keeping the kernel ceiling fixed. The kernel measures the machine. The program
contract measures the data model. Their separate budgets keep a document key
from enlarging the loop budget.
The viewer is budgeted apart from the runtime because it delivers a record of a
run rather than running one; its HTML, TypeScript, and CSS count toward no line
budget at all. The command line is budgeted apart from the runtime because it
serves a person at a terminal rather than an episode. Telemetry is budgeted
apart because it reads a finished log rather than producing one, and no part of
the runtime depends on it. `scripts/loc.sh` counts the nine Rust budgets, and
continuous integration fails a build over any of them. Another bound limits
the browser bundle to 150 KB compressed. The last bound is the stripped release
binary with that bundle embedded. It measured 5,404,568 bytes on 2026-08-22.
Continuous integration builds the bundle before measuring and fails a build
over 8 MiB.

## Documents

| document | answers |
|---|---|
| [docs/build.md](docs/build.md) | how to install, build with Bazel, run the end-to-end demos, and use Cargo for Rust development |
| [docs/design.md](docs/design.md) | what foe guarantees and the structure that delivers it |
| [docs/evaluation.md](docs/evaluation.md) | how runtime conformance and model-backed task quality are measured |
| [docs/self-improvement.md](docs/self-improvement.md) | how foe evaluates and improves its own source, including measured results and operating guidance |
| [docs/lineage-identity.md](docs/lineage-identity.md) | design: content-addressed evidence that relates immutable program states |
| [docs/code-mode.md](docs/code-mode.md) | the `python` tool: bounded model-written programs that compose granted tools through the registry |
| [docs/config.md](docs/config.md) | every configuration key, its domain, and its default |
| [docs/models.md](docs/models.md) | the model providers, where credentials live, `foe login`, and the exec transport |
| [docs/log-format.md](docs/log-format.md) | every log event, the derived message rule, and seeding |
| [docs/telemetry.md](docs/telemetry.md) | what telemetry derives from an episode log when enabled, the schema it emits, and what it never emits |
| [docs/protocol.md](docs/protocol.md) | the line protocol between foe and the process that launched it |
| [docs/sdk.md](docs/sdk.md) | the Python package |
| [docs/tools.md](docs/tools.md) | built-in tools, configured executables, and host tools |
| [docs/sandbox.md](docs/sandbox.md) | how grants compile into kernel restrictions |
| [docs/viewer.md](docs/viewer.md) | the trajectory viewer |
| [docs/landscape.md](docs/landscape.md) | where foe sits among agent runtimes |
| [docs/deferred.md](docs/deferred.md) | features with reserved names and no implementation |
| [docs/workflow.md](docs/workflow.md) | declared graphs, the judgment the model keeps inside one, and recovery |
| [docs/compaction.md](docs/compaction.md) | when the context is compacted, where it is cut, and what the summary carries |
| [docs/design-language.md](docs/design-language.md) | the visual language the viewer follows |
| [docs/brand/README.md](docs/brand/README.md) | the name, the mark, the wordmark, the accent, and their use |

`docs/README.md` lists the same documents with the question each answers.
`AGENTS.md` states the rules for changing this repository.

## License

Apache-2.0. See [LICENSE](LICENSE).
