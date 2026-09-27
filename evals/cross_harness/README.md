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
| `contracts/graphs.py` | generates the bespoke foe documents: the autonomy graph, its variant without verifiers, its ablation without verifiers or `block`, and its lean variant without the survey node, and the teams graph in its configured, undivided, and sequential variants |
| `tasks/protocol.py` | what a task is, how a task directory is laid out, how a task is materialized, graded, and classified into a confusion cell |
| `tasks/policies.py` | degenerate policies that stand in for an arm, so the grader controls run without a model |
| `tasks/feature_removal.py` | authors a task from one committed feature of this repository |
| `tasks/constructions.py` | authors the contradictory, missing-capability, and non-terminating tasks; it offers more designs than `tasks/foe-tree/` holds |
| `tasks/teams.py` | authors the teams tasks: fan-out tasks, harvested from a sweep commit or constructed over the crates of one change, and survey tasks whose answer a script computes |
| `gates/label_leakage.py` | the label non-leakage gate: a model shown only a task's text and file listing must fail to name its class |
| `gates/isolation.py` | the harness isolation gate: neither canary a run plants appears in any recorded model request, and the requests and the planted canaries are proven present; exit status 4 means no canary was found but the evidence leaves the absence unproven |
| `gates/hang_symmetry.py` | the non-terminating class's premise: each task's check is still running after 45 seconds on the host and under the Codex sandbox |
| `gates/network_denied.py` | the missing-capability class's premise: no arm can reach the network the task's generator needs |
| `gates/environment_interference.py` | counts, per attempt, the signals of an environment refusing an arm, such as a permission denial or a missing toolchain |
| `conditions.py` | states, per attempt, whether the condition its arm's control tests was reached: the runtime verifier ran, the verifier or the stop mechanism stayed absent, or a non-terminating check entered its wait |
| `rescore.py` | scores committed attempt records again under the current scoring version from the workspaces the attempts left, and keeps each original cell beside the new one |
| `admission.py` | decides which tasks are admissible: the oracle-solved workspace's visible check passes on the host, inside a foe episode, and under a Codex sandbox; `--tool-root` grants the foe episode a further directory to execute |
| `run.py` | runs every selected task under every selected arm, grades each run, and writes one record per attempt |
| `report.py` | rates per arm, cells per task, teams coordination measures, and paired comparisons over the records, with the construction as the statistical unit; `--archive` recomputes every table of the dated campaigns under `results/` |
| `environment/` | the container an attempt runs in, its egress sink, and `build.sh`; see `environment/environment.md` |

## Tasks

Every task is a recipe: `task.json` and `grader/`. The workspace the agent
sees is regenerated at run time from `git archive` of the base commit
`task.json` records plus `grader/workspace.patch`, so `tasks/foe-tree/`
holds no copy of the repository. `--keep-workspace` on an authoring tool
keeps the generated workspace for inspection, and `.gitignore` excludes it
and any grading build directory.

`tasks/foe-tree/` holds the fifteen autonomy tasks that `runs/autonomy.json`
selects. `admission.py` admits a task when its oracle-solved workspace
passes the visible check on the host, inside a foe episode, and under a
Codex sandbox. On a host whose system programs are links into another
directory, such as a coreutils package under `/usr/lib/cargo/bin`, the foe
episode needs that directory named with `--tool-root`, and a run document
names it under `tool_roots`. A non-terminating task's check is built never
to return, so `gates/hang_symmetry.py` admits that class instead: the check
must still be running after 45 seconds on the host and under the Codex
sandbox.

Eleven of the task directories are the ones the autonomy run of 2026-09-13
ran, with the scoring and control files added since. The four
non-terminating task directories differ from those runs in their wait step.
Their check suites print `checks/run.sh step 2 waiting on <mechanism>` to
standard error before the wait, and their graders read no file the
workspace holds. The recorded runs used an earlier `grader/workspace.patch`,
whose check suite appended a line to `checks-wait.log`. Its sha256 digests
begin as follows:

| task | recorded `workspace.patch` sha256 |
|---|---|
| `unreleased-lock-context` | `c59c19f811230aae` |
| `unreleased-lock-evidence` | `f979c3aaf7aa3525` |
| `unwritten-pipe-context` | `fb120da456f55045` |
| `waiting-check-suite` | `3360416ce65fc179` |

| class | tasks | construction |
|---|---|---|
| solvable | `duplicate-grant-roots`, `question-identifier-in-message-text`, `correlation-as-inbox-source` | one commit removed from this repository's history per task |
| contradictory | `ceiling-bound-feature`, `ceiling-bound-feature-telemetry` | a change that cannot fit under a line ceiling |
| contradictory | `frozen-interface-budget`, `frozen-interface-tool-defs` | a field that the frozen interface's document forbids |
| missing-capability | `inventory-regeneration-code`, `inventory-regeneration-context`, `inventory-regeneration-evidence`, `inventory-regeneration-workflow` | an artifact only a generator writes, and the generator needs a network no arm has |
| non-terminating | `unreleased-lock-context`, `unreleased-lock-evidence` | a lock nothing releases |
| non-terminating | `unwritten-pipe-context` | a pipe nothing writes |
| non-terminating | `waiting-check-suite` | a socket nothing answers |

The fifteen tasks come from seven constructions, which is the unit
`report.py` counts. The three non-terminating mechanisms are one
construction, because one builder writes them with one check template, one
first step, and one grader. `tasks/examples/hello-solvable` is one further solvable
task that `runs/smoke.json` runs.

Most task texts were written by an agent and await a person's reading;
`metadata.review` in each `task.json` records who has read its text.

```sh
python3 evals/cross_harness/tasks/feature_removal.py author \
  --repo . --commit SHA --out evals/cross_harness/tasks/foe-tree/NAME --name NAME
python3 evals/cross_harness/tasks/constructions.py --help
python3 evals/cross_harness/tasks/teams.py --help
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
python3 evals/cross_harness/report.py --archive evals/cross_harness/results
python3 evals/cross_harness/gates/label_leakage.py evals/cross_harness/runs/autonomy.json --confirm-spend
```

The documents under `runs/`:

| document | what it runs |
|---|---|
| `smoke.json` | the example task under one foe arm and one Codex arm |
| `autonomy-pilot.json` | the two cheapest tasks on foe's tree under four autonomy arms |
| `autonomy.json` | the fifteen autonomy tasks under four arms, the run `results/autonomy-2026-09-13.md` records |
| `autonomy-verifier.json` | the fifteen autonomy tasks under `foe-configured`, `foe-unverified`, and `codex-equivalent`, three attempts each; not yet run |
| `verifier-timeout.json`, `lean.json`, `budget-bounded.json`, `budget-bounded-warning.json` | cases of 2026-09-13 that `results/campaign-two-2026-09-13.md` records |

The small-obstacle and teams cases of that record ran tasks this tree does
not hold, so no document under `runs/` selects them.
`results/campaign-two-2026-09-13/run-documents/` keeps the resolved run
document of every case of that record.

A document's keys, with relative paths resolved against the document's own
directory:

| key | meaning |
|---|---|
| `tasks` | the directory of task directories; the family comes from their `task.json` files |
| `select` | task names to run; every task under `tasks` when omitted |
| `arms` | arm names; every arm of the family when omitted |
| `attempts` | independent attempts per task and arm; default 1 |
| `resume` | continue a run into an output directory that already holds records, skipping each attempt whose record exists; default false |
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

## Results

`results/` holds the dated records of two exploratory campaigns, each
opened by a section stating its status and the claims withdrawn from it.
The committed arrays summarize every attempt, `rescored.json` and
`rescored/` hold each attempt's cell under both scoring versions, and the
conditions files state whether each attempt reached its condition.
`tables.md` is what `report.py --archive` computes from them, and
`results/archive_test.py` requires it to match. `attempt-ledger.json` reconciles the attempt counts
with the local run directories, and `evidence-manifest.json` names each
full record with its digest and the digest of the archive that holds them.
`qualification-2026-09-27/` holds the grader-control, hang-symmetry,
network-denial, and admission results recorded on that date.

The committed arrays reproduce every scoring version 1 table. The scoring
version 2 cells, the isolation results, and the conditions were computed
from the retained attempt workspaces and the episode and session logs,
which only the host that ran the attempts holds. `rescore.py`,
`gates/isolation.py`, and `conditions.py` therefore reproduce those files
only on a host that holds the run directories under
`~/.local/state/foe/cross-harness/`. The archive `evidence-manifest.json`
names holds the full records and omits the workspaces and logs.

## Tests

```sh
sh evals/cross_harness/run_unit_tests.sh
sh evals/cross_harness/run_unit_tests.sh --forbid-skips
bazel test //evals/cross_harness:cross_harness_unit_test
```

`repairs_test.py` runs scripted episodes of the generated autonomy
documents against `target/debug/foe`. Two of its tests reach runtime
repairs: a completion verifier killed at its timeout, and the limit on one
`bash` call. The third checks the evaluation document's grant of the
directories a check writes, in a workspace whose directories the runner
creates as it does before every arm. `results/archive_test.py` recomputes
the recorded results from the committed arrays, and every other test file
uses synthetic records.

A test that exercises the built binary, cargo, or the repository's git
history skips with its reason when that input is absent, as it is inside
the Bazel sandbox. `--forbid-skips` counts a skipped test as a failure, and
continuous integration passes it.
