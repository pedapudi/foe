# Foe Terminal-Bench evaluation

This package runs Foe through Harbor against a small, pinned subset of
Terminal-Bench 2.1. The subset supports development and confirmation before a
full benchmark run. It does not constitute an official Terminal-Bench score.

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

## Preview and run one assessed task

Every model-backed target prints its maximum model calls, input tokens, output
tokens, total-token cost proxy, and wall time. The preview makes no model
request:

```sh
bazel run //evals/terminal_bench:foe-smoke
```

Run the `fix-git` smoke case after reviewing that maximum:

```sh
bazel run //evals/terminal_bench:foe-smoke -- --confirm-spend
```

The runner uses `openai-codex/gpt-5.6-sol` with low reasoning effort. Override
the reasoning setting only when that setting is the subject of the comparison:

```sh
bazel run //evals/terminal_bench:foe-smoke -- \
  --reasoning-effort medium \
  --confirm-spend
```

## Run development and holdout cases

The development set contains four tasks:

- `cancel-async-tasks`
- `git-multibranch`
- `fix-git`
- `sqlite-db-truncate`

Preview their aggregate allowance:

```sh
bazel run //evals/terminal_bench:foe-development
```

Run one baseline attempt per development task:

```sh
bazel run //evals/terminal_bench:foe-development -- \
  --label baseline \
  --confirm-spend
```

The holdout set contains `sanitize-git-repo` and
`large-scale-text-editing`. Keep their trajectories outside the evidence given
to an improvement episode. Freeze the candidate source and comparison settings
before running them:

```sh
bazel run //evals/terminal_bench:foe-holdout
bazel run //evals/terminal_bench:foe-holdout -- \
  --label candidate-confirmation \
  --attempts 3 \
  --confirm-spend
```

One attempt per task gives a directional result. A candidate that improves the
development cases receives three attempts on each holdout task. The broader
Terminal-Bench calibration uses 10 to 20 frozen tasks with three attempts per
task, as specified in [`docs/evaluation.md`](../../docs/evaluation.md).

## Use the trajectories for improvement

Run a baseline from one clean worktree. Review only its four development
trajectories and task grades. Produce a compact diagnosis that identifies the
failed task, the relevant Foe events, the proposed source files, and the
expected measurable effect.

Apply one candidate change in a separate clean worktree. The change may be
implemented directly or by a bounded self-improvement workflow. The workflow
mechanism and its evidence requirements are specified in
[`docs/self-improvement.md`](../../docs/self-improvement.md). Run the same four
development tasks from the candidate worktree. Freeze the candidate before
opening the two holdout results.

The following sequence preserves the comparison boundary:

1. Run the development target from the baseline worktree.
2. Diagnose only the retained development evidence.
3. Implement and verify one candidate change.
4. Run the development target from the candidate worktree.
5. Freeze the candidate source, model settings, task list, and allowances.
6. Run the holdout target from both worktrees.
7. Retain or reject the candidate from paired task completion, estimated cost,
   and wall time. Until model pricing is integrated, input plus output tokens
   provide the cost proxy for trials with the same model route and settings.

## Retained evidence

Each confirmed command writes under `target/terminal-bench-jobs/`. One
timestamped run contains a `campaign.json` manifest and one Harbor job per
task. Harbor retains the task configuration, verifier result, exception data,
and aggregate token fields. The manifest records the provisional cost formula.

The Harbor trial's `agent/foe-episode/` directory is the complete native Foe
episode tree. It contains `episode.jsonl`, child episodes, spill values, and
renderings. The neighboring files include the generated Foe program, Foe
standard output, Foe standard error, the typed process exit status, and the
runtime conformance report. The adapter sums provider-reported input, output,
and cache-read tokens into the Harbor agent context. It also records Foe's
outcome and conformance status as Harbor agent metadata. Reports retain the
three token counts when input plus output tokens provide the cost proxy.

Keep raw jobs under ignored `target/` directories. Keep the private credential
state under `~/.cache/foe/terminal-bench/`. Git tracks the adapter, case
selection, allowances, documentation, and reviewed aggregate results. Git does
not track task containers, raw trajectories, credentials, or build output.
