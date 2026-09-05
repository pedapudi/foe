# Documents

Each document covers one subject. Read `design.md` first, then use the table
to find the relevant specification, report, or guide.

| document | subject |
|---|---|
| [build.md](build.md) | how to install foe, build it with Bazel, run the end-to-end demos, and use Cargo for Rust development |
| [design.md](design.md) | the guarantees foe makes and the structure that delivers each |
| [evaluation.md](evaluation.md) | how runtime conformance and model-backed task quality are measured |
| [self-improvement.md](self-improvement.md) | how foe evaluates and improves a disposable copy of its own source, including measured results and operating guidance |
| [evidence.md](evidence.md) | how portable evidence associates a proposed execution contract with an accepted verifier result |
| [code-mode.md](code-mode.md) | how the `python` tool composes ordinary tools while keeping intermediate results out of the model context |
| [config.md](config.md) | what an execution-contract document may contain and what each key means |
| [models.md](models.md) | which model endpoints exist, where their credentials live, and how `foe login` sets them up |
| [log-format.md](log-format.md) | what an episode log contains and how a model request is derived from it |
| [protocol.md](protocol.md) | how a process that launched foe exchanges lines with it |
| [sdk.md](sdk.md) | how a Python application builds a configuration, runs an episode, and supplies a model backend |
| [tools.md](tools.md) | what each built-in tool does and how executables and host tools become tools |
| [sandbox.md](sandbox.md) | how grants become kernel restrictions and what happens when the kernel lacks them |
| [viewer.md](viewer.md) | what the viewer shows and how it is served and exported |
| [landscape.md](landscape.md) | what other agent runtimes do and where foe differs |
| [deferred.md](deferred.md) | the features with reserved event types or keys and no implementation |
| [workflow.md](workflow.md) | how a declared graph of nodes runs, where the model keeps its judgment inside it, and how failures are recovered |
| [compaction.md](compaction.md) | when the model's context is compacted, where the conversation is cut, what the summary carries, and what compaction loses |
| [design-language.md](design-language.md) | the visual language the viewer follows |
| [brand/README.md](brand/README.md) | the name, the mark, the wordmark, the accent, and the rules for using them |

## Presentations

`presentations/` holds slide decks in the diastil dialect — self-contained
HTML files that present themselves when opened and stay editable in the
diastil editor or any text editor.

| deck | subject |
|---|---|
| [presentations/what-is-foe.dia.html](presentations/what-is-foe.dia.html) | an introduction: the problem foe solves and the concepts it prioritizes |
| [presentations/foe-overview.dia.html](presentations/foe-overview.dia.html) | the architecture and the feature set, one pass over the whole machine |
| [presentations/foe-isolation-and-permissions.dia.html](presentations/foe-isolation-and-permissions.dia.html) | the isolation and permissions model — why authority is an allow list, and how it is enforced |

The repository root holds `README.md`, an overview, and `AGENTS.md`, the
rules for changing the repository. `examples/` holds thirteen runnable
programs, one per mechanism. Each uses deterministic responses and checks its
own result; `examples/README.md` indexes them.
