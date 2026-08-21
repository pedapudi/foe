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

The design and the specifications are complete. Implementation is in
progress. No interface is stable.

## Running an episode

A configuration is one JSON document. This is the smallest one that runs
with the built-in model transport.

```json
{
  "version": 1,
  "name": "hello",
  "instructions": { "role": "You are a coding agent." },
  "tools": ["read", "grep", "edit", "bash"],
  "grants": { "read": ["/home/user/project"], "write": ["/home/user/project"] },
  "budget": { "model_calls": 20 },
  "model": { "provider": "anthropic", "model": "claude-opus-5", "api_key_file": "/home/user/.config/foe/anthropic.key" },
  "task": "Fix the failing test in tests/parser_test.py."
}
```

```
foe --config hello.json
```

foe validates the document, writes `episode.jsonl` into a fresh directory,
echoes every event to standard output, and serves a viewer on the loopback
interface until the episode ends. The exit code is 0 when the outcome is
completed, 2 when blocked, 3 when exhausted, and 1 when failed.
`examples/` holds one runnable configuration per mechanism.

## Embedding

A host program launches the binary, reads the log from standard output, and
answers model requests and host tool calls on standard input. The Python
package in `python/` does this and exposes a transport interface so that the
host keeps the model credentials. See [docs/sdk.md](docs/sdk.md).

## Size

Two numbers bound the runtime. The Rust source of the log, core, tool, and
viewer crates stays under 6,000 lines, excluding tests and generated code;
`scripts/loc.sh` counts and continuous integration fails over budget. The
stripped release binary has a size that is measured at release and recorded
here; until then, continuous integration fails a build over 8 MiB.

## Documents

| document | answers |
|---|---|
| [docs/design.md](docs/design.md) | what foe guarantees and the structure that delivers it |
| [docs/config.md](docs/config.md) | every configuration key, its domain, and its default |
| [docs/log-format.md](docs/log-format.md) | every log event, the derived message rule, and seeding |
| [docs/protocol.md](docs/protocol.md) | the line protocol between foe and the process that launched it |
| [docs/sdk.md](docs/sdk.md) | the Python package |
| [docs/tools.md](docs/tools.md) | built-in tools, configured executables, and host tools |
| [docs/sandbox.md](docs/sandbox.md) | how grants compile into kernel restrictions |
| [docs/viewer.md](docs/viewer.md) | the trajectory viewer |
| [docs/landscape.md](docs/landscape.md) | where foe sits among agent runtimes |
| [docs/deferred.md](docs/deferred.md) | features with reserved names and no implementation |

`docs/README.md` lists the same documents with the question each answers.
`AGENTS.md` states the rules for changing this repository.

## License

Apache-2.0. See [LICENSE](LICENSE).
