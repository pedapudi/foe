# Foe Terminal-Bench evaluation

This package runs Foe through Harbor against a small, pinned subset of
Terminal-Bench 2.1. The subset supports development and confirmation before a
full benchmark run. It does not constitute an official Terminal-Bench score.
The [development evaluation record](evaluation-record.md) reports retained
aggregate results and promotion decisions. The [capability campaign
record](campaign.md) defines the staged evaluation and its success criteria.

The dataset reference is `terminal-bench/terminal-bench-2-1@6`. The
[Harbor Hub dataset record](https://hub.harborframework.com/datasets/terminal-bench/terminal-bench-2-1/6)
contains 89 tasks. Terminal-Bench 2.1 corrected 28 tasks and added continuous
validation according to the
[release description](https://www.tbench.ai/news/terminal-bench-2-1).

The adapter implements Harbor's installed-agent interface. Harbor downloads a
task image, uploads a statically linked Foe binary, runs one Foe episode, and
executes the task-owned verifier. Harbor documents the custom interface in its
[agent integration guide](https://harborframework.com/docs/agents).

## Prerequisites

The host needs Bazel, Docker, Docker Compose 2, and Harbor 0.22.0. The user who
runs Bazel must have access to the Docker daemon. A newly added `docker` group
membership becomes effective in a fresh login shell.

Install Harbor with `uv`:

```sh
uv tool install harbor==0.22.0
```

Authenticate Foe once:

```sh
bazel run //:foe -- login --status
```

The runner copies `openai-codex.json` to
`~/.cache/foe/terminal-bench/openai-codex.json`. Every trial receives that
private working copy. A refreshed credential returns to the working copy
before the next trial. The original login file remains unchanged. The runner
holds a file lock so two local campaigns cannot race a token refresh. The
private copy stays outside Harbor job directories and Foe episode directories.

The task container must receive the provider credential because Foe calls the
model from that container. Use the pinned Terminal-Bench dataset for these
runs. A task with untrusted provenance could read or transmit credentials
available to an installed coding agent.

## Check installation without model spend

The evaluation targets build `//:foe-portable`, a static x86-64 Linux binary
linked with musl. This binary avoids a dependency on the task image's glibc
version.

Check one task image, the Harbor adapter, and the portable binary without a
model request:

```sh
bazel run //evals/terminal_bench:foe-install-check
```

## Probe the container without model spend

The deterministic capability target runs Foe in the pinned `fix-git` task
container. It checks the executable path, working directory, process lifetime,
large-file tools, timeouts, package tooling, and terminal availability:

```sh
bazel run //evals/terminal_bench:foe-capability-probes
```

The target makes no provider request. It writes a typed report under
`target/terminal-bench-capability-probes/`.

## Preview and run one assessed task

Every model-backed target prints planning token estimates and an estimated
cost. The preview makes no model request:

```sh
bazel run //evals/terminal_bench:foe-smoke
```

Run the `fix-git` smoke case after reviewing that maximum:

```sh
bazel run //evals/terminal_bench:foe-smoke -- --confirm-spend
```

The runner records token usage and estimated cost without enforcing token
ceilings. Model calls and wall time remain loop backstops. Use
`--hard-token-limits` only when a token boundary is the subject of the test.

The default route is `openai-codex/gpt-5.6-sol` with low reasoning effort.
Luna and Terra are available for inexpensive development diagnosis:

```sh
bazel run //evals/terminal_bench:foe-smoke -- \
  --model openai-codex/gpt-5.6-luna \
  --reasoning-effort low \
  --confirm-spend
```

The supported reasoning settings end at `xhigh` for this campaign.

## Run the staged task sets

The development target contains six tasks with inspected trajectories:

- `cancel-async-tasks`
- `git-multibranch`
- `fix-git`
- `sqlite-db-truncate`
- `sanitize-git-repo`
- `large-scale-text-editing`

Preview or run one attempt per development task:

```sh
bazel run //evals/terminal_bench:foe-development
```

Run one baseline attempt per development task:

```sh
bazel run //evals/terminal_bench:foe-development -- \
  --label baseline \
  --confirm-spend
```

The capability-search target contains eight development tasks. Opening a result
makes that task development evidence:

```sh
bazel run //evals/terminal_bench:foe-capability-search
bazel run //evals/terminal_bench:foe-capability-search -- \
  --task regex-log \
  --label sol-low-search \
  --confirm-spend
```

The confirmation target contains four tasks that stay closed until a candidate
and acceptance rule are frozen. Run two attempts per task:

```sh
bazel run //evals/terminal_bench:foe-confirmation
bazel run //evals/terminal_bench:foe-confirmation -- \
  --label candidate-confirmation \
  --attempts 2 \
  --confirm-spend
```

The calibration targets remain closed until the development and confirmation
criteria pass:

```sh
bazel run //evals/terminal_bench:foe-calibration
bazel run //evals/terminal_bench:foe-calibration-holdout
```

[`campaign.md`](campaign.md) defines every task set, exposure rule, cost gate,
and success criterion.

## Use the trajectories for improvement

Every completed trial contains `agent/foe-diagnostics.json`. This bounded
digest reports request growth, replayed tool results, repeated calls, failures,
verifier outcomes, and log sequence numbers.

Collect diagnoses from one or more retained development runs. The command
requires a clean source tree and the exact evaluated binary:

```sh
bazel run //evals/terminal_bench:collect-diagnostics -- \
  --run-dir "$PWD/target/terminal-bench-jobs/development-20260823T120000Z" \
  --output "$PWD/target/foe-trajectory-evidence.json"
```

Create a clean candidate worktree at the evaluated commit. Run the
self-improvement workflow from that worktree:

```sh
bazel run //evals/terminal_bench:self-improve -- \
  --candidate /path/to/clean/foe-candidate \
  --evidence "$PWD/target/foe-trajectory-evidence.json" \
  --keep "$PWD/target/foe-self-improvement" \
  --confirm-spend
```

Terra with `high` reasoning runs both nodes. The coding node receives only the
diagnosis and acts with `read`, `grep`, `edit`, and `bash`. The workflow writes
`direct_implementation_required: true` when it produces no valid candidate.

Candidate validation and promotion occur outside the workflow. The workflow
mechanism and evidence requirements are specified in
[`docs/self-improvement.md`](../../docs/self-improvement.md).

## Retained evidence

Each confirmed command writes under `target/terminal-bench-jobs/`. One
timestamped run contains a `campaign.json` manifest and one Harbor job per
task. Harbor retains the task configuration, verifier result, exception data,
aggregate token fields, and estimated cost. The manifest records the pricing
source and whether token estimates were measurements or hard limits.

The Harbor trial's `agent/foe-episode/` directory is the complete native Foe
episode tree. It contains `episode.jsonl`, child episodes, spill values, and
renderings. The neighboring files include the generated Foe program, Foe
standard output, Foe standard error, the typed process exit status, and the
runtime conformance report. The adapter sums provider-reported input, output,
and cache-read tokens into the Harbor agent context. It also records Foe's
outcome and conformance status as Harbor agent metadata. Cost estimation uses
the provider-reported uncached-input, cached-input, and output usage for each
request.

Keep raw jobs under ignored `target/` directories. Keep the private credential
state under `~/.cache/foe/terminal-bench/`. Git tracks the adapter, case
selection, allowances, documentation, and reviewed aggregate results. Git does
not track task containers, raw trajectories, credentials, or build output.
