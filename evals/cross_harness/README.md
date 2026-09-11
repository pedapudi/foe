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

The runner prints its plan and launches nothing without `--confirm-spend`.
Every option is a flag; the runner sets one environment variable,
`CODEX_HOME`, on the Codex child process, because Codex locates its
credential and session files by it, and records the value.

```sh
python3 evals/cross_harness/run.py \
  --foe target/debug/foe --codex "$(command -v codex)" --credential ~/.codex/auth.json \
  --family autonomy --tasks evals/cross_harness/tasks/foe-tree \
  --arms foe-configured,codex-equivalent --attempts 1 \
  --route subscription --model gpt-5.6-sol --effort medium \
  --budget model_calls=40 --budget seconds=900 \
  --tool-root ~/.cargo/bin --tool-root ~/.cargo --tool-root ~/.rustup --tool-root /usr/lib/gcc \
  --out ~/.local/state/foe/cross-harness/pilot-1
python3 evals/cross_harness/run.py ... --confirm-spend
python3 evals/cross_harness/report.py --records ~/.local/state/foe/cross-harness/pilot-1/records
```

`--tool-root` names directories the foe documents add to their execute and
read grants so that a task's check suite can run its toolchain; the tasks
on foe's tree need cargo. The foe-as-shipped arms cannot take tool roots
and are recorded as not applicable for such tasks. `--budget KEY=VALUE`
overrides a task's ceiling for every attempt and is recorded.

## Tests

```sh
sh evals/cross_harness/run_unit_tests.sh
bazel test //evals/cross_harness:cross_harness_unit_test
```

Tests that exercise the built binary skip, naming the reason, when
`target/debug/foe` is absent.
