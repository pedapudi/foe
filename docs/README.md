# Documents

Each document answers one question. Read `design.md` first; the others
specify one piece each.

| document | question it answers |
|---|---|
| [design.md](design.md) | What does foe guarantee, and what structure delivers each guarantee? |
| [config.md](config.md) | What may a configuration document contain, and what does each key mean? |
| [log-format.md](log-format.md) | What does an episode log contain, and how is a model request derived from it? |
| [protocol.md](protocol.md) | How does a process that launched foe exchange lines with it? |
| [sdk.md](sdk.md) | How does a Python program build a configuration, run an episode, and supply a model transport? |
| [tools.md](tools.md) | What does each built-in tool do, and how are executables and host tools made into tools? |
| [sandbox.md](sandbox.md) | How do grants become kernel restrictions, and what happens when the kernel lacks them? |
| [viewer.md](viewer.md) | What does the viewer show, and how is it served and exported? |
| [landscape.md](landscape.md) | What do other agent runtimes do, and where does foe differ? |
| [deferred.md](deferred.md) | Which features have reserved event types or keys and no implementation? |

The repository root holds `README.md`, an overview, and `AGENTS.md`, the
rules for changing the repository. `examples/` holds one runnable
configuration per mechanism, each with a README.
