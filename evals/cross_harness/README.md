# Cross-harness evaluation

Evaluations that compare foe with Codex CLI on the properties foe claims as
its own: bounded, truthful termination; coordinated teams under disjoint
write grants; containment by grants; enforced budgets; verifier-gated
completion; and typed state across compaction. [docs/evaluation.md](../../docs/evaluation.md)
"Cross-harness evaluation against Codex CLI" specifies the families, the
arms, the validity gates, and the task protocol. This directory holds the
instruments and the runners. Every module is standard-library Python with
its unit tests beside it, and no unit test needs a model credential, the
network, or a Codex login.

## Modules

| module | what it does |
|---|---|
| `containment_matrix.py` | runs eleven probe commands under three foe configurations and three Codex sandbox policies, without a model, and reports each cell as allowed, denied, or error against the documented expectation |
| `probe.py` | checks, without a model, that the host can run the evaluation: both sandboxes are live, versions are known, a decoy Codex home stays unread, and an optional compatible route answers |
| `trajectory.py` | the one trajectory schema both harnesses are reduced to: agents and their nesting, model calls with usage, shell commands with the paths they name, file changes, compactions, and the outcome |
| `normalize_foe.py` | fills the schema from an episode log tree, and runs `evals/trace_quality.py` for the foe-only conformance column |
| `normalize_codex.py` | fills the schema from Codex's session files, its `--json` event stream, and its last-message file |
| `metering_proxy.py` | a recording, metering proxy between a harness and an OpenAI-compatible endpoint; refuses a request once an attempt's budget is spent |
| `codex_budget_watcher.py` | follows Codex's session files during a run and stops the process tree when a token or wall-clock limit is crossed |
| `arms/foe_arm.py`, `arms/codex_arm.py` | one harness given one task in one workspace; both return the same result record |
| `contracts/graphs.py` | generates the bespoke foe documents: the autonomy graph, its ablation, and the teams graph in its configured, undivided, and sequential variants |
| `tasks/protocol.py` | what a task is, how a task directory is laid out, how a task is materialized, graded, and classified into a confusion cell |
| `tasks/policies.py` | degenerate policies that stand in for an arm, so the grader controls run without a model |
| `tasks/feature_removal.py` | authors a task from one committed feature of this repository |
| `tasks/constructions.py` | authors the contradictory, missing-capability, and non-terminating tasks |
| `run.py` | runs every selected task under every selected arm, grades each run, and writes one record per attempt |
| `report.py` | rates per arm, cells per task, and paired comparisons over the records |

## Tasks are recipes

A task directory holds `task.json` and `grader/`. The workspace the agent
sees is regenerated at run time from `git archive` of the base commit
`task.json` records plus `grader/workspace.patch`, so the tree under
`tasks/foe-tree/` holds no copy of the repository. `--keep-workspace` on
either authoring tool keeps the generated workspace for inspection, and
`.gitignore` excludes it and any grading build directory.

```sh
python3 evals/cross_harness/tasks/feature_removal.py author \
  --repo . --commit SHA --out evals/cross_harness/tasks/foe-tree/NAME --name NAME
python3 evals/cross_harness/tasks/constructions.py --help
```

## Running

Build the binary, then run the deterministic instruments; neither spends
credit:

```sh
cargo build -p foe
python3 evals/cross_harness/containment_matrix.py --foe target/debug/foe
python3 evals/cross_harness/probe.py --foe target/debug/foe --live
```

A run is one JSON document, as an episode is one contract document. The
runner takes the document and one flag; it prints every value the document
resolved to and every planned attempt with its ceilings, and launches
nothing without `--confirm-spend`. The report takes the same document.

```sh
python3 evals/cross_harness/run.py evals/cross_harness/runs/autonomy-pilot.json
python3 evals/cross_harness/run.py evals/cross_harness/runs/autonomy-pilot.json --confirm-spend
python3 evals/cross_harness/report.py evals/cross_harness/runs/autonomy-pilot.json
```

`runs/smoke.json` runs the example task under one foe arm and one Codex
arm; `runs/autonomy-pilot.json` runs the two cheapest tasks on foe's tree
under the four autonomy arms. A document's keys, with relative paths
resolved against the document's own directory:

| key | meaning |
|---|---|
| `tasks` | the directory of task directories; the family comes from their `task.json` files |
| `select` | task names to run; every task under `tasks` when omitted |
| `arms` | arm names; every arm of the family when omitted |
| `attempts` | independent attempts per task and arm; default 1 |
| `model` | `route` (`subscription` or `compatible`), `name`, `effort` (default `medium`); the compatible route adds `base_url` and `codex_wire_api` |
| `budget` | ceilings that replace the same keys of every task's budget |
| `tool_roots` | directories the foe documents add to their read and execute grants so a check suite can run its toolchain; the tasks on foe's tree need cargo, and the foe-as-shipped arms, which cannot take them, are recorded as not applicable |
| `harnesses` | `foe` (default the debug build under the checkout), `codex` (default the command on PATH), `credential` (default `~/.codex/auth.json`); the last two are needed only by a Codex arm |
| `out` | where attempts, records, and the report are written; default `~/.local/state/foe/cross-harness/<document stem>` |

The runner sets one environment variable, `CODEX_HOME`, on the Codex child
process, because Codex locates its credential and session files by it, and
records the value.

## Tests

```sh
sh evals/cross_harness/run_unit_tests.sh
bazel test //evals/cross_harness:cross_harness_unit_test
```

Tests that exercise the built binary skip, naming the reason, when
`target/debug/foe` is absent.
