# Documents

Each document covers one subject. Read `design.md` first; the others
specify one piece each.

| document | subject |
|---|---|
| [design.md](design.md) | the guarantees foe makes and the structure that delivers each |
| [config.md](config.md) | what a configuration document may contain and what each key means |
| [log-format.md](log-format.md) | what an episode log contains and how a model request is derived from it |
| [protocol.md](protocol.md) | how a process that launched foe exchanges lines with it |
| [sdk.md](sdk.md) | how a Python program builds a configuration, runs an episode, and supplies a model transport |
| [tools.md](tools.md) | what each built-in tool does and how executables and host tools become tools |
| [sandbox.md](sandbox.md) | how grants become kernel restrictions and what happens when the kernel lacks them |
| [viewer.md](viewer.md) | what the viewer shows and how it is served and exported |
| [landscape.md](landscape.md) | what other agent runtimes do and where foe differs |
| [deferred.md](deferred.md) | the features with reserved event types or keys and no implementation |
| [workflow.md](workflow.md) | how a declared graph of nodes runs, where the model keeps its judgment inside it, and how failures are recovered |
| [design-language.md](design-language.md) | the visual language the viewer follows |

The repository root holds `README.md`, an overview, and `AGENTS.md`, the
rules for changing the repository. `examples/` holds one runnable
configuration per mechanism, each with a README.
