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

The installed-agent program sets `sandbox.mode` to `off`. The Harbor Docker
container is the isolation boundary for the task. Disabling Landlock inside
the container removes host-kernel compatibility from the quality measurement.
The recorded episode must report sandbox mode `off` and no observed Landlock
ABI. A different sandbox record invalidates the trial as infrastructure data.

The self-improvement runner executes candidate changes on the host. Its
program retains `sandbox.mode: best-effort` because the Docker isolation
boundary does not cover that process.

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
model from that container. Each trial uses a fresh, unpredictable remote path.
The adapter scans retained episode events for the access token and refresh
token without reporting either value. A detected value invalidates the trial
as infrastructure evidence. This check detects accidental disclosure; it does
not isolate the credential from a malicious process with root access.

Use the pinned Terminal-Bench dataset for these runs. A task with untrusted
provenance could locate, read, or transmit credentials available to an
installed coding agent. A host-supplied transport is required when the task
itself is outside the trust boundary.

## Check installation without model spend

The evaluation targets build `//:foe-portable`, a static x86-64 Linux binary
linked with musl. This binary avoids a dependency on the task image's glibc
version.

Check one task image, the Harbor adapter, and the portable binary without a
model request:

```sh
bazel run //evals/terminal_bench:foe-install-check
```

The installation check validates the schema command and every command-line
option that the built-in workflow invocation uses. An incompatible binary
fails during setup before a model request. When Foe exits before creating an
episode log, the adapter reports the retained exit status and standard error.

## Probe each task container without model spend

The deterministic capability target runs Foe in the pinned `fix-git` task
container. It checks the executable path, working directory, process lifetime,
large-file tools, timeouts, package tooling, and terminal availability:

```sh
bazel run //evals/terminal_bench:foe-capability-probes
```

Task images have independent package sets and process behavior. A successful
probe for `fix-git` does not establish capabilities for another task. Probe
every selected task before its first provider-backed attempt:

```sh
bazel run //evals/terminal_bench:foe-capability-probes -- --task gpt2-codegolf
```

The target makes no provider request. It writes a typed report under
`target/terminal-bench-capability-probes/`.

The report describes the benchmark image as supplied. Do not install optional
utilities to make the report pass. The coding program receives the observed
working directory and fixed-path executable availability. A missing optional
utility therefore changes tool selection without invalidating the benchmark.
An installation failure or a missing task-required capability is an
infrastructure failure and must not contribute a quality score.

## Validate verifier-governed completion without model spend

Six modified scenarios expose a public completion checker as a configured
tool. One Sol-low coding episode combines a typed return with
`done_when.verify`. Foe rejects a completion claim when the checker reports
findings. The model receives those findings and can continue within the
episode's remaining allowance.

The public checker provides development feedback. Its executable and source
are present in the task container, so the modified lane is an open-book
evaluation. Harbor still runs the unaltered task-owned Terminal-Bench verifier
after Foe exits. That verifier is absent during the Foe episode. Only its
post-run result determines task quality. Results from this modified lane are
Foe-specific convergence evidence and do not contribute to a standard
Terminal-Bench score.

The selected scenarios cover six distinct completion conditions:

- `cancel-async-tasks` executes concurrency and cancellation-cleanup probes.
- `dna-assembly` checks physical template binding, complete annealed tracts,
  exact `oligotm` temperatures, and compatible assembly overhangs.
- `fix-git` checks that the lost commit reaches `master` in a clean worktree.
- `git-multibranch` pushes distinct public content through password-authenticated
  Git and requires the live HTTPS endpoints to serve both branches within three
  seconds. The public probe branches share history, which lets the external
  evaluator add its own main and development commits with ordinary pushes.
- `gpt2-codegolf` enforces the source-size and compilation requirements. It
  checks four public arg-max continuations that differ from the task-owned
  verifier input. One prompt exercises quoted uppercase text and adjacent
  punctuation.
- `large-scale-text-editing` checks the allowed Vim grammar and a temporary
  10,000-row sample. The task-owned verifier applies the script to all one
  million rows.

Validate every checker before a provider-backed run:

```sh
bazel run //evals/terminal_bench:foe-verifier-controls
```

Each control uses a fresh task container. It requires the untouched task
state to produce at least one finding. It then applies a separate oracle and
requires both an empty finding list and a score of `1.0` from the task-owned
verifier. The checker runs with an empty process environment, matching Foe's
configured-executable contract. The control agent makes no model request.

Preview one verifier-governed case:

```sh
bazel run //evals/terminal_bench:foe-verifier-cancel-async-tasks
```

Run the case after reviewing the maximum:

```sh
bazel run //evals/terminal_bench:foe-verifier-cancel-async-tasks -- \
  --label verifier-governed-cancel-async-tasks \
  --confirm-spend
```

Equivalent targets end in `foe-verifier-fix-git`,
`foe-verifier-git-multibranch`,
`foe-verifier-gpt2-codegolf`, and
`foe-verifier-large-scale-text-editing`. The DNA target is
`foe-verifier-dna-assembly`. These targets use the standard service tier and
low reasoning in one verifier-governed implementation
episode. The implementation retains 60 model calls as a loop backstop.

Targets beginning with `foe-built-in-verifier-` exercise the workflow that
the Foe binary constructs for a bare task. That workflow gives 60 calls to a
Sol-low implementation episode and 60 calls to a fresh Sol-high terminal
audit. The terminal audit owns `done_when.verify`. The adapter disables
Landlock because each Terminal-Bench task already runs in its Docker
container.

Preview the built-in workflow on one modified scenario:

```sh
bazel run //evals/terminal_bench:foe-built-in-verifier-fix-git
```

Run it after reviewing the maximum:

```sh
bazel run //evals/terminal_bench:foe-built-in-verifier-fix-git -- \
  --label built-in-verifier-fix-git \
  --confirm-spend
```

The same suffixes supported by `foe-verifier-` are supported by
`foe-built-in-verifier-`. The built-in targets retain the exact command-line
arguments used in `foe-invocation.json`. They reject runner-defined diagnosis,
escalation, workflow candidates, and hard token allowances because the binary
owns both model stages and their call backstops.

Run the built-in workflow on the frozen, unchanged twelve-task development
set with:

```sh
bazel run //evals/terminal_bench:foe-built-in-development
bazel run //evals/terminal_bench:foe-built-in-development -- \
  --label built-in-development \
  --confirm-spend
```

The preview reports the maximum estimated spend without making a model
request. These closed-book tasks do not expose the development checkers. The
implementation and terminal-audit episodes must select and run their own
task-relevant checks. Harbor runs the unchanged task-owned verifier after Foe
exits.

The configured executable's bytes participate in Foe's program identity. The
adapter also downloads the checker after the episode and compares its digest
with the source digest. A changed checker invalidates the trial as
infrastructure evidence.

When a run adds an audit-and-repair model, the implementation episode returns
its typed result without owning the completion condition. The terminal audit
owns `done_when.verify`. Both episodes retain the checker tool, so the
implementation may use it before handoff. A failed implementation check cannot
prevent the audit from receiving the task and inspecting the shared workspace.

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
The runner sets Harbor's outer agent timeout to the sum of every possible
model stage's Foe time backstop, plus five minutes for process settlement.
Harbor therefore cannot terminate a valid Foe episode before its declared
time allowance ends. [`cases.json`](cases.json) records each multiplier's
base timeout from the pinned task metadata.

The default route is `openai-codex/gpt-5.6-sol` with low reasoning effort.
Every model node requests the standard service tier by default. The runner
records the requested tier and credit multiplier beside its token-derived
cost estimate. Use `--service-tier priority` to request OpenAI Fast mode. Its
documented target is 1.5 times Standard speed, and GPT-5.6 consumes 2.5 times
the Standard ChatGPT credits. See the
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

The development target contains twelve tasks with inspected trajectories:

- `cancel-async-tasks`
- `git-multibranch`
- `fix-git`
- `sqlite-db-truncate`
- `sanitize-git-repo`
- `large-scale-text-editing`
- `gpt2-codegolf`
- `fix-ocaml-gc`
- `path-tracing-reverse`
- `regex-chess`
- `model-extraction-relu-logits`
- `dna-assembly`

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

The capability-search target contains five inspected diagnostic tasks. These
tasks may provide extra activation evidence without contributing to a
protected-set score:

```sh
bazel run //evals/terminal_bench:foe-capability-search
bazel run //evals/terminal_bench:foe-capability-search -- \
  --task regex-log \
  --label sol-low-search \
  --confirm-spend
```

The confirmation target contains eight tasks that stay closed until a candidate
and acceptance rule are frozen. Run two attempts per task:

```sh
bazel run //evals/terminal_bench:foe-confirmation
bazel run //evals/terminal_bench:foe-confirmation -- \
  --label candidate-confirmation \
  --attempts 2 \
  --confirm-spend
```

The twenty calibration tasks remain closed until the development and
confirmation criteria pass. The eight sealed-holdout tasks remain closed until
the calibration result and its decision rule are recorded:

```sh
bazel run //evals/terminal_bench:foe-calibration
bazel run //evals/terminal_bench:foe-calibration-holdout
```

[`campaign.md`](campaign.md) defines every task set, exposure rule, quality
gate, and success criterion.

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

The collector labels every diagnosis with its dataset, run label, token
policy, service tier, and complete execution configuration. The configuration
identifies diagnosis, unresolved-diagnosis, implementation, independent-audit,
and completion-verifier stages when present. It also records whether Foe's
built-in workflow constructed the model episodes. The collector groups
verified results by task and complete configuration so a diagnosis node can
compare failed and successful mechanisms. The file keeps up to four
input-growth landmarks and three entries from each ranked result list. Input
growth resets at each episode boundary. The four-landmark limit applies to the
complete episode tree. Completed outcomes retain their typed status and omit
the model-authored completion value. Failed, blocked, and exhausted outcomes
retain their actionable error, code, message, or limit. This avoids repeating
the same completion prose at the report, episode, and verification levels.
The collector accepts at most 24 diagnoses and 64 KiB of encoded evidence. It
accepts only development and opened capability-search tasks from `cases.json`.
Confirmation, calibration, and calibration-holdout evidence remains
unavailable to self-improvement.

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

Luna produces the bounded diagnosis from the supplied digest. Its only
ordinary tool is `block`, so it cannot inspect the candidate source or
retained run directories. Its typed result contains one causal contrast, one
intervention, and the controls and falsification condition needed to evaluate
that intervention.

The diagnosis chooses `implement-source` when failed and successful
trajectories isolate an activated source mechanism. It chooses
`configure-workflow` when an independent audit stage supplies a repeated
quality gain. It chooses `insufficient-evidence` when the contrast identifies
only model capability or requires semantic task knowledge absent from the
log. That choice ends the workflow before a coding episode and sets
`direct_implementation_required`.

`--candidate-kind source-change` asks whether an evidence-supported general
intervention should become behavior owned by Foe source. The objective must
name that source-owned behavior. The diagnosis returns
`insufficient-evidence` when either the intervention or its ownership is not
supported. `--candidate-kind workflow-configuration` restricts the result to
an independently audited workflow setting. The default `auto` retains the
evidence-driven choice between source, workflow configuration, and no
candidate.

Source diagnosis does not require a successful independent-audit setting.
That setting is required only when the diagnosis selects
`configure-workflow`.

When the diagnosis chooses `implement-source`, the configured coding model
acts with `read`, `grep`, `edit`, and `bash`. The diagnosis node's branch edge
controls whether the coding node fires. The coding node lists `task` and
`diagnose-runtime` under `follows`, which carries both values into its clean
context. The raw trajectory digest remains inside the diagnosis child. The
coding child locates the affected implementation, test, and specification
files. Its write authority covers runtime crates, specifications, and examples.
It cannot write evaluation code or benchmark material.

The coding child returns a typed handoff that names its changed paths,
validation observations, and unresolved risks. A fresh source-audit child
receives the objective, diagnosis, and handoff. It inspects the shared candidate
tree, repairs defects, and treats the checker as the completion authority. The
audit traces proposed resources through node return, workflow settlement, and
external evaluation before it accepts a lifecycle-dependent mechanism. Source
audit is a conditional Sol xhigh escalation. Task execution and source
implementation remain Sol low by default.

When the diagnosis chooses `configure-workflow`, the runner requires the
evidence to contain exactly one independent-audit setting with at least two
successful attempts. The runner copies that setting directly from the
evidence and writes `workflow-candidate.json` beside the retained result. The
candidate digest binds the setting to the evaluated source tree, binary,
evidence file, and preserved execution controls.

The diagnosis prompt targets four requests and has a 20-call, 1,800-second
loop backstop. The implementation has 28-call and 3,600-second safety
backstops. The independent source audit has 32-call and 3,600-second safety
backstops. Each model child ends as blocked after eight consecutive identical
tool calls or assistant turns.

The diagnosis preserves the primary model route, reasoning effort, task
allowances, token policy, service tier, and task set. Verified task quality is
the promotion metric. Tokens, cost, cache use, latency, outcome accuracy, and
conformance remain recorded diagnostics.

`--cargo` must name the pinned toolchain binary. A Rustup proxy is refused
because its result depends on process environment and may download a
toolchain. Before the first model request, the runner checks formatting,
workspace tests, Clippy, and line counts in the clean candidate worktree. A
formatting, test, or Clippy failure stops the run. Each line budget uses the
declared limit as its ceiling. A baseline count above its declared limit
becomes a no-growth ceiling for that budget, so an unrelated existing overage
cannot consume the self-improvement episode.

The candidate checker repeats formatting, workspace tests, Clippy, and line
counts under the supplied Cargo cache. It rejects a line count above the
recorded baseline ceiling. It also rejects benchmark identifiers added by the
candidate. An existing identifier in an edited document remains part of the
baseline and does not create a false finding. The coding child uses this
checker as the line-count authority because the repository script reports only
absolute limits. Its only generated-file write authority is the candidate's private
`target/foe-self-improvement-check` directory and the repository tests'
declared `target/test-scratch` directory. Read grants
cover Cargo and Rustup metadata and installed C headers. Execute grants cover
the pinned toolchain and candidate build directory. They also cover Cargo's
command shims for `fmt` and `clippy`. The result records content digests for
Cargo, Rustc, Rustfmt, and Clippy.
Executable tools cannot bind loopback listeners, so the in-episode workspace
check excludes the command-line, transport, and viewer packages whose test
suites include loopback servers. A second command runs the command-line unit
tests and skips only the login module, which owns the command-line loopback
fixtures. The checker also skips nested sandbox tests that cannot expand its
existing Landlock domain and the core session test that binds a loopback
listener. The runner repeats validation after the episode with the complete
workspace test suite.

The no-spend form generates the complete workflow document. It runs that
document through `foe plan`. A schema, authority, or construction error
therefore fails before the runner reports that the workflow is ready.

The coding child receives read-only access to the candidate worktree's Git
metadata. This access lets `git status`, `git diff`, and the independent
candidate checker operate when the candidate is a linked Git worktree.

The runner validates the artifact after Foe exits. A valid artifact remains
accepted when the episode exhausted its reporting budget after producing the
files. The diagnosis node's recorded branch remains authoritative when a
terminal coding child ends blocked, so the external full check still evaluates
source files that the child produced. A source candidate binds the base Git
tree and every changed file digest. A workflow candidate binds the
independent-audit setting and preserved controls. `direct_implementation_required`
is true when deterministic validation finds an error or the workflow produces
no candidate.

Apply a retained workflow candidate to any permitted task set with:

```sh
bazel run //evals/terminal_bench:foe-development -- \
  --workflow-candidate /absolute/path/to/workflow-candidate.json \
  --confirm-spend
```

The requested model, primary reasoning effort, service tier, and token policy
must match the candidate. The current Foe source tree and binary must also
match its recorded identity.

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
