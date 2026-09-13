# Cross-harness evaluation

Evaluations that compare foe with Codex CLI on the properties foe claims as
its own: bounded, truthful termination; coordinated teams under disjoint
write grants; containment by grants; enforced budgets; verifier-gated
completion; and typed state across compaction. [docs/evaluation.md](../../docs/evaluation.md)
"Cross-harness evaluation against Codex CLI" specifies the families, the
arms, the validity gates, and the task protocol. This directory holds the
instruments, the tasks, and the runners. Every module is standard-library
Python with its unit tests beside it, and no unit test needs a model
credential, the network, or a Codex login.

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
| `tasks/constructions.py` | authors the contradictory, missing-capability, and non-terminating tasks, five of each |
| `tasks/teams.py` | authors the teams tasks: fan-out tasks from sweep commits, and survey tasks whose answer a script computes |
| `tasks/coherent.py` | authors two coherent controls by construction: one change over the modules of one crate, with the table in the crate root every module must agree with; every file it touches lies in one directory, so no division into workers exists |
| `gates/label_leakage.py` | the label non-leakage gate: a model shown only a task's text and file listing must fail to name its class |
| `gates/isolation.py` | the harness isolation gate: neither canary a run plants appears in any recorded model request |
| `run.py` | runs every selected task under every selected arm, grades each run, and writes one record per attempt |
| `report.py` | rates per arm, cells per task, teams coordination measures, and paired comparisons over the records |
| `environment/` | the container an attempt runs in, its egress sink, and `build.sh`; see `environment/environment.md` |

## Tasks

Every task is a recipe: `task.json` and `grader/`. The workspace the agent
sees is regenerated at run time from `git archive` of the base commit
`task.json` records plus `grader/workspace.patch`, so `tasks/foe-tree/`
holds no copy of the repository. `--keep-workspace` on an authoring tool
keeps the generated workspace for inspection, and `.gitignore` excludes it
and any grading build directory.

`tasks/foe-tree/` holds thirty-one task directories, of which twenty-three
are admissible and eight are suppressed. `admission.py` decides which:
a task is admissible when its oracle-solved workspace passes the visible
check on the host, inside a foe episode, and under a Codex sandbox.

| family and class | admissible | suppressed |
|---|---:|---:|
| autonomy, solvable | 4 | 6 |
| autonomy, contradictory | 5 | 0 |
| autonomy, missing-capability | 5 | 0 |
| autonomy, non-terminating | 5 | 0 |
| teams, fan-out | 0 | 2 |
| teams, survey | 2 | 0 |
| teams, coherent | 2 | 0 |

Every suppressed task runs a crate suite on `foe-core`, `foe-log`, or the
command-line crate, whose own tests exercise sandboxing and therefore fail
inside one. No fan-out is admissible on this host: a change that spreads over
directories a delegation could grant separately reaches one of those crates,
and both fan-outs are suppressed for that reason. The two constructed tasks
are the coherent controls, because every file their change touches lies in one
crate directory, so no two workers can be given directories that do not
overlap. The suppressed directories stay in the tree so that a later host,
such as the container, can re-admit them.

Every task text was written by an agent and awaits a person's reading;
`metadata.review` in each `task.json` says so. One task,
`bazel-lock-regeneration`, presumes that `bazel` is absent from the arm's
search path and is refused by name on a host that has it; the container
image holds no bazel.

```sh
python3 evals/cross_harness/tasks/feature_removal.py author \
  --repo . --commit SHA --out evals/cross_harness/tasks/foe-tree/NAME --name NAME
python3 evals/cross_harness/tasks/constructions.py --help
python3 evals/cross_harness/tasks/teams.py --help
python3 evals/cross_harness/tasks/coherent.py --help
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
nothing without `--confirm-spend`. The report and the gates take the same
document.

```sh
python3 evals/cross_harness/run.py evals/cross_harness/runs/autonomy-pilot.json
python3 evals/cross_harness/run.py evals/cross_harness/runs/autonomy-pilot.json --confirm-spend
python3 evals/cross_harness/report.py evals/cross_harness/runs/autonomy-pilot.json
python3 evals/cross_harness/gates/isolation.py evals/cross_harness/runs/autonomy-pilot.json
python3 evals/cross_harness/gates/label_leakage.py evals/cross_harness/runs/autonomy-full.json --confirm-spend
```

`runs/smoke.json` runs the example task under one foe arm and one Codex
arm; `runs/autonomy-pilot.json` runs the two cheapest tasks on foe's tree
under the four autonomy arms; `runs/autonomy-full.json` selects every
autonomy task. A document's keys, with relative paths resolved against the
document's own directory:

| key | meaning |
|---|---|
| `tasks` | the directory of task directories; the family comes from their `task.json` files |
| `select` | task names to run; every task under `tasks` when omitted |
| `arms` | arm names; every arm of the family when omitted |
| `attempts` | independent attempts per task and arm; default 1 |
| `model` | `route` (`subscription` or `compatible`), `name`, `effort` (default `medium`); the compatible route adds `base_url` and `codex_wire_api` |
| `budget` | ceilings that replace the same keys of every task's budget |
| `tool_roots` | directories the foe documents add to their read and execute grants so a check suite can run its toolchain; the tasks on foe's tree need cargo; the foe-as-shipped arms, which cannot take them, are recorded as not applicable when a run or a task names any |
| `harnesses` | `foe` (default the debug build under the checkout), `codex` (default the command on PATH), `credential` (default `~/.codex/auth.json`); the last two are needed only by a Codex arm |
| `out` | where attempts, records, the report, and the gate reports are written; default `~/.local/state/foe/cross-harness/<document stem>` |
| `grader_timeout` | seconds one grade script may run |
| `source_root` | a path inside the foe checkout the binary was built from; the binary's own path when omitted |
| `foe_config_dir` | foe's configuration directory, where the run plants its foe canary; default `~/.config/foe` |

Every run plants two canary sentences and records them in `run.json`: the
Codex arm writes one into each attempt's fresh `CODEX_HOME` as
`config.toml` under `developer_instructions`, the user configuration file
`--ignore-user-config` states it does not load, and the runner writes the
other into foe's configuration directory as `AGENTS.md`, a file foe never
reads. `gates/isolation.py` then greps every recorded model request of the
run for both. The runner sets one environment variable, `CODEX_HOME`, on
the Codex child process, because Codex locates its credential and session
files by it, and records the value.

## Tests

```sh
sh evals/cross_harness/run_unit_tests.sh
bazel test //evals/cross_harness:cross_harness_unit_test
```

Tests that exercise the built binary or cargo skip, naming the reason,
when `target/debug/foe` or cargo is absent.
