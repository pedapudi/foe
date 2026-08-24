# Foe Terminal-Bench evaluation

This package runs Foe through Harbor against a small, pinned subset of
Terminal-Bench 2.1. The subset supports development and confirmation before a
full benchmark run. It does not constitute an official Terminal-Bench score.
The [development evaluation record](evaluation-record.md) reports retained
aggregate results and promotion decisions. The [capability campaign
record](campaign.md) defines the staged evaluation and its success criteria.
The [cross-trajectory capability analysis](cross-trajectory-analysis.md)
maps retained failures to product changes and promotion gates.

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
Every model node requests the `priority` service tier by default. OpenAI calls
this setting Fast mode. The documented target is 1.5 times Standard speed, and
GPT-5.6 consumes 2.5 times the Standard ChatGPT credits. The runner records
the requested tier and credit multiplier beside its token-derived cost
estimate. Use `--service-tier default` for a Standard run. See the
[OpenAI speed documentation](https://developers.openai.com/codex/speed).

Luna and Terra are available for inexpensive development diagnosis:

```sh
bazel run //evals/terminal_bench:foe-smoke -- \
  --model openai-codex/gpt-5.6-luna \
  --reasoning-effort low \
  --confirm-spend
```

The supported reasoning settings end at `xhigh` for this campaign.

A task can run as two model episodes with a typed handoff. The first episode
uses `read`, `grep`, and `bash` to collect static and runtime evidence. It
returns facts, implementation steps, and verification steps. Facts include
observed constraints, evidence, uncertainty, and implementation blockers. The
second episode receives only the task and that return value in a fresh
context. It holds the full coding tool set:

```sh
bazel run //evals/terminal_bench:foe-capability-search -- \
  --task gpt2-codegolf \
  --diagnosis-model openai-codex/gpt-5.6-luna \
  --diagnosis-reasoning-effort high \
  --confirm-spend
```

The diagnosis allowance is added to the task's implementation allowance. The
default is a twenty-call hard backstop with a four-request planning target.
Each stage receives the task's full time backstop. An early typed return
releases the remaining work immediately. The runner prices each child from
the model route recorded in its episode log. Omitting
`--diagnosis-model` preserves the single-episode coding program.

A cheap diagnosis can conditionally request deeper reasoning from the primary
model before implementation:

```sh
bazel run //evals/terminal_bench:foe-capability-search -- \
  --task gpt2-codegolf \
  --diagnosis-model openai-codex/gpt-5.6-luna \
  --diagnosis-reasoning-effort high \
  --unresolved-diagnosis-reasoning-effort xhigh \
  --confirm-spend
```

The Luna episode chooses direct implementation only when repository evidence
resolves every implementation-critical fact. Otherwise a read-only Sol
`xhigh` episode resolves the uncertainty. A fresh Sol `low` episode performs
the implementation on either path. The spending preview includes the maximum
conditional path. Actual cost includes only the branch that fires. The deeper
diagnosis has a twenty-call backstop because it may need to derive and validate
an implementation-critical fact before returning its typed report. Its prompt
uses six requests as a soft planning target. It can continue when a named fact
still prevents implementation. The coding episode owns exhaustive validation
and repair.

The implementation can receive a fresh higher-reasoning repair episode while
the primary implementation remains at low reasoning. The repair episode does
not require a separate diagnosis episode:

```sh
bazel run //evals/terminal_bench:foe-capability-search -- \
  --task gpt2-codegolf \
  --escalation-reasoning-effort xhigh \
  --escalation-model-calls 25 \
  --confirm-spend
```

The repair episode receives the task and the earlier episode's completion
claim in a fresh context. It audits the shared workspace before completing.
Its allowance is additive. The implementation retains its full capacity when
repair is enabled. A diagnosis episode can still precede both children when
the task benefits from a cheaper model's typed analysis. Conditional unresolved
diagnosis and post-implementation repair are separate experiments, so the
runner refuses a command that enables both.

The task registry uses at least 60 model calls and 1,800 seconds for every task.
These values serve only as loop and stall backstops. Actual use determines
spend. The adapter permits eight consecutive identical tool calls or assistant
turns before classifying a loop. The runner records actual calls, time, token
usage, and estimated cost. Input and output token allowances remain absent
unless `--hard-token-limits` is supplied for an explicit budget-boundary test.
The spend preview estimates auxiliary episode tokens from the task's per-call
planning average and prices each model route separately.

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

The capability-search target contains twelve development tasks. Opening a result
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

Every completed trial contains `agent/foe-diagnostics.json`. The report names
request growth, replayed tool results, repeated calls, failures, verifier
outcomes, final post-edit tool results, bounded verifier failure classes, and
log sequence numbers across the episode tree.

Collect diagnoses from one or more retained development runs. The command
requires a clean source tree and the exact evaluated binary:

```sh
bazel run //evals/terminal_bench:collect-diagnostics -- \
  --run-dir "$PWD/target/terminal-bench-jobs/development-20260823T120000Z" \
  --output "$PWD/target/foe-trajectory-evidence.json"
```

The collector labels every diagnosis with its dataset, run label, model, and
reasoning setting. It groups verified results by task and model setting so a
diagnosis node can compare failed and successful configurations. The file
keeps up to four input-growth landmarks and three entries from each ranked
result list. Input growth resets at each episode boundary. The four-landmark
limit applies to the complete episode tree. The collector accepts at most 24
diagnoses and 64 KiB of encoded evidence. It accepts only development and
opened capability-search tasks from `cases.json`. Confirmation, calibration,
and calibration-holdout evidence remains unavailable to self-improvement.

Create a clean candidate worktree at the evaluated commit. Run the
self-improvement workflow from that worktree:

```sh
bazel run //evals/terminal_bench:self-improve -- \
  --candidate /path/to/clean/foe-candidate \
  --evidence "$PWD/target/foe-trajectory-evidence.json" \
  --cargo /absolute/path/to/toolchain/bin/cargo \
  --cargo-home /absolute/path/to/cargo-home \
  --keep "$PWD/target/foe-self-improvement" \
  --confirm-spend
```

Luna with `high` reasoning produces the bounded diagnosis from the supplied
digest. Its only ordinary tool is `block`, so it cannot inspect the candidate
source or retained run directories. Terra with `high` reasoning receives that
diagnosis and acts with `read`, `grep`, `edit`, and `bash`. The coding child
locates the affected implementation, test, and specification files.
Its write authority covers runtime crates, specifications, and examples. It
cannot write evaluation code or benchmark material.

The diagnosis prompt targets four requests and has a 20-call, 1,800-second
loop backstop. The implementation has 28-call and 3,600-second safety
backstops.

The lower-cost evaluated configuration is the candidate configuration. The
diagnosis preserves its model route, reasoning effort, task allowances, token
policy, and task set. A higher-cost successful configuration supplies causal
evidence. It is not a permitted replacement. The proposed change must affect
the explicit program recorded in the evidence. A change to a built-in default
has no effect when that program supplies the setting itself.

`--cargo` must name the pinned toolchain binary. A Rustup proxy is refused
because its result depends on process environment and may download a
toolchain. The checker runs formatting, workspace tests, clippy, and the line
budget under the supplied Cargo cache. Read grants cover Cargo and Rustup
metadata and installed C headers. Execute grants cover the pinned toolchain
and candidate build directory. They also cover Cargo's command shims for
`fmt` and `clippy`. The result records content digests for Cargo, Rustc,
Rustfmt, and Clippy. Executable tools cannot bind loopback listeners, so the
in-episode check excludes the command-line, transport, and viewer packages
whose tests bind loopback servers. It also skips nested sandbox tests that
cannot expand the checker's existing Landlock domain. The runner repeats
validation after the episode with the complete workspace test suite.

The no-spend form generates the complete workflow document. It runs that
document through `foe plan`. A schema, authority, or construction error
therefore fails before the runner reports that the workflow is ready.

The coding child receives read-only access to the candidate worktree's Git
metadata. This access lets `git status`, `git diff`, and the independent
candidate checker operate when the candidate is a linked Git worktree.

The runner validates the artifact after Foe exits. A valid artifact remains
accepted when the episode exhausted its reporting budget after producing the
files. The result binds the base Git tree and every changed file digest into
one candidate artifact digest. `direct_implementation_required` is true when
deterministic validation finds an error or the workflow changes no files.

Capability conversion still requires a separate benchmark rerun. Candidate
promotion remains outside the workflow. The workflow mechanism and evidence
requirements are specified in
[`docs/self-improvement.md`](../../docs/self-improvement.md).

## Retained evidence

Each confirmed command writes under `target/terminal-bench-jobs/`. One
timestamped run contains a `campaign.json` manifest and one Harbor job per
task. Harbor retains the task configuration, verifier result, exception data,
aggregate token fields, and estimated cost. The manifest records the pricing
source and whether token estimates were measurements or hard limits.

The adapter runs `foe plan` against the task-specific program inside the task
container before its first provider request. An invalid program is a setup
error with zero model spend. It is excluded from task accuracy and must be
replaced before a repeated result is complete.

The Harbor trial's `agent/foe-episode/` directory is the complete native Foe
episode tree. It contains `episode.jsonl`, child episodes, spill values, and
renderings. The neighboring files include the generated Foe program, Foe
standard output, Foe standard error, the typed process exit status, and the
runtime conformance report. The adapter sums provider-reported input, output,
and cache-read tokens into the Harbor agent context. It also records Foe's
outcome and conformance status as Harbor agent metadata. Cost estimation uses
the provider-reported uncached-input, cached-input, and output usage for each
request.

A runtime `failed` outcome or a nonconformant trace makes the trial invalid as
model-accuracy evidence. A model request without provider usage makes the
resource measurement incomplete. The runner records both classes and exits
unsuccessfully, so a repeated result cannot silently include either class.

Keep raw jobs under ignored `target/` directories. Keep the private credential
state under `~/.cache/foe/terminal-bench/`. Git tracks the adapter, case
selection, allowances, documentation, and reviewed aggregate results. Git does
not track task containers, raw trajectories, credentials, or build output.
