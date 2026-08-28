# Foe Terminal-Bench evaluation

This package runs Foe through Harbor against a small, pinned subset of
Terminal-Bench 2.1. The subset supports development and confirmation before a
full benchmark run. It does not constitute an official Terminal-Bench score.
The [development evaluation record](evaluation-record.md) reports retained
aggregate results and promotion decisions. The [capability campaign
record](campaign.md) defines the staged evaluation and its success criteria.
The [cross-trajectory capability analysis](cross-trajectory-analysis.md)
maps retained failures to product changes and promotion gates.
The [campaign report](campaign-report.md) explains Foe's architecture,
advantages, protected quality results, trajectory patterns, and
self-improvement evidence from the completed campaign.

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
`~/.cache/foe/terminal-bench/openai-codex.json`. A serial trial receives this
private working copy and can return a refreshed credential before the next
trial. The original login file remains unchanged. The runner holds a file lock
for the complete campaign so local campaigns cannot race a token refresh. The
private copy stays outside Harbor job directories and Foe episode directories.

A parallel pair receives two private access-only credentials. Each file
contains the access token and expiry. The account identifier is included when
present. Each file omits the rotating refresh token. Its file mode prevents an
accidental write. The task identity may own the file and can change its mode,
so the mode does not provide an integrity boundary. Foe fails locally if the
credential reaches its sixty-second refresh margin. The adapter uses a
distinct remote path for each credential. It compares the bytes after the
trial and removes the remote file. A changed credential invalidates the trial
as infrastructure evidence. A task from untrusted provenance can read or
transmit the access token during its validity window.

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

Every installed-agent preflight runs `foe plan --schema`. This command checks
the installed binary without reading a default model or provider credential.
The later task program names its model and credential file explicitly.

## Validate verifier-governed completion without model spend

Three modified scenarios expose a public, read-only checker as a configured
tool. One Sol-low coding episode combines a typed return with
`done_when.verify`. Foe rejects a completion claim when the checker reports
findings. The model receives those findings and can continue within the
episode's remaining allowance.

The public checker provides development feedback. Harbor still runs the
unaltered task-owned Terminal-Bench verifier after Foe exits. Only the
task-owned verifier determines task quality. Results from this modified lane
are Foe-specific convergence evidence and do not contribute to a standard
Terminal-Bench score.

The selected scenarios cover three forms of completion:

- `cancel-async-tasks` executes concurrency and cancellation-cleanup probes.
- `fix-git` checks that the lost commit reaches `master` in a clean worktree.
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

Equivalent targets end in `foe-verifier-fix-git` and
`foe-verifier-large-scale-text-editing`. These targets use the default service
tier, low reasoning for implementation, and high reasoning for an independent
audit. The implementation retains 60 model calls. The audit receives 25
additional calls. These values are loop backstops.

The configured executable's bytes participate in Foe's program identity. The
adapter also downloads the checker after the episode and compares its digest
with the source digest. A changed checker invalidates the trial as
infrastructure evidence.

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

## Run resource-bounded task pairs

The default `--workers 1` mode executes one assessed task at a time. Use two
workers to run eligible tasks from one evaluation group as pairs:

```sh
bazel run //evals/terminal_bench:foe-development -- \
  --workers 2 \
  --confirm-spend
```

The runner starts a pair when all of these conditions hold:

- The case metadata reserves less than 8 GiB for each task.
- The pair reserves at most 8 GiB and four CPUs in total.
- Available host memory covers the pair's declared reservations plus 4 GiB
  for Harbor, Docker, and other host work. The host also has at least 100 GiB
  of free disk.
- Linux full-memory pressure stays below one percent over ten seconds.
- The host has not swapped pages out since the preceding execution.
- The access token covers every attempt and configured model stage. Its
  remaining lifetime also covers fifteen minutes of startup and five minutes
  of process settlement per attempt, plus Foe's sixty-second refresh margin.

A task that reserves 8 GiB runs alone. A failed pair admission runs its tasks
serially with the mutable credential. An out-of-memory result, an increased
swap-out counter, or excessive memory pressure makes every later task serial.
The campaign stops before starting an execution group when available memory
cannot cover its declared reservations plus 4 GiB of host headroom. It also
stops when free disk falls below 100 GiB.

Parallelism applies between independent assessed tasks. Diagnosis,
implementation, conditional escalation, and audit stages inside one task keep
their declared order. Harbor also runs attempts for each task with its
per-process concurrency set to one.

Finish adaptive development and capability-search runs before confirmation or
calibration-holdout evidence begins. Freeze the candidate and its execution
configuration before starting a holdout group. This ordering preserves the
holdout's role as evidence that did not influence the candidate.

The runner records token usage and estimated cost without enforcing token
ceilings. Model calls and wall time remain loop backstops. Use
`--hard-token-limits` only when a token boundary is the subject of the test.
The runner sets Harbor's outer agent timeout to the sum of every possible
model stage's Foe time backstop, plus five minutes for process settlement.
Harbor therefore cannot terminate a valid Foe episode before its declared
time allowance ends. [`cases.json`](cases.json) records each multiplier's
base timeout from the pinned task metadata.

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
and completion-verifier stages when present. It groups verified results by
task and complete configuration so a diagnosis node can
compare failed and successful mechanisms. The file keeps up to four
input-growth landmarks and three entries from each ranked result list. Input
growth resets at each episode boundary. The four-landmark limit applies to the
complete episode tree. The collector accepts at most 24 diagnoses and 64 KiB
of encoded evidence. It accepts only development and opened capability-search
tasks from `cases.json`. Confirmation, calibration, and calibration-holdout
evidence remains unavailable to self-improvement.

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

Luna produces the bounded diagnosis from the supplied digest. Its ordinary
tools are `block` and the generated candidate validator, so it cannot inspect
the candidate source or retained run directories. Its typed result contains
one causal contrast, one intervention, and the controls and falsification
condition needed to evaluate that intervention.

The diagnosis node's completion verifier is a generated executable declared
through `done_when.verify`. The runner writes it before the episode; it
applies the same identity-bound candidate validation the runner applies
after the episode, judging the returned typed diagnosis. A workflow,
instruction, or tool candidate is therefore accepted inside the episode as
an authoritative `verification/result` event in the diagnosis child's log,
and findings return to the same child. A source diagnosis and a typed
abstention pass this verifier: a source candidate is judged by the
implementation node's candidate check, and an abstention proposes nothing.

The diagnosis chooses `implement-source` when failed and successful
trajectories isolate an activated source mechanism. It chooses
`configure-workflow` when an independent audit stage supplies a repeated
quality gain. It chooses `revise-instructions` when the repeated causal
difference is procedural guidance that one instruction section of the
retained program document can carry. It chooses `define-tool` when one
missing executable tool explains the gap. It chooses `insufficient-evidence`
when the contrast identifies only model capability or requires semantic task
knowledge absent from the log. That choice ends the workflow before a coding
episode and sets `direct_implementation_required`.

When the diagnosis chooses `implement-source`, the configured coding model
acts with `read`, `grep`, `edit`, and `bash`. The diagnosis node's branch edge
controls whether the coding node fires. The coding node lists `task` and
`diagnose-runtime` under `follows`, which carries both values into its clean
context. The raw trajectory digest remains inside the diagnosis child. The
coding child locates the affected implementation, test, and specification
files. Its write authority covers runtime crates, specifications, and examples.
It cannot write evaluation code or benchmark material.

When the diagnosis chooses `configure-workflow`, the runner validates the
typed independent-audit setting. It writes `workflow-candidate.json` beside
the retained result. The candidate digest binds the setting to the evaluated
source tree, binary, evidence file, and preserved execution controls. The
setting must appear in at least two successful attempts in the supplied
evidence.

When the diagnosis chooses `revise-instructions`, the runner validates the
typed revision against the retained `program.json`: the named section key
resolves to exactly one instruction section, and the old text occurs exactly
once in it. It writes `instruction-candidate.json` beside the retained
result, with the same identity and evidence bindings a workflow candidate
carries.

When the diagnosis chooses `define-tool`, the typed result carries the tool's
name, description, executable content, and that content's digest, because the
diagnosis episode holds no write authority. The runner checks that the digest
matches the content and the description is nonempty, writes
`tool-candidate.json`, and retains the executable beside it as
`tool-candidate-executable`. The candidate records the name, description, and
digest under the same identity and evidence bindings.

The diagnosis prompt targets four requests and has a 20-call, 1,800-second
loop backstop. The implementation has 28-call and 3,600-second safety
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
recorded baseline ceiling. The coding child uses this checker as the line-count
authority because the repository script reports only absolute limits. Its only
generated-file write authority is the candidate's private
`target/foe-self-improvement-check` directory. Read grants
cover Cargo and Rustup metadata and installed C headers. Execute grants cover
the pinned toolchain and candidate build directory. They also cover Cargo's
command shims for `fmt` and `clippy`. The result records content digests for
Cargo, Rustc, Rustfmt, and Clippy.
Executable tools cannot bind loopback listeners, so the
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
files. A source candidate binds the base Git tree and every changed file
digest. A workflow candidate binds the independent-audit setting and preserved
controls. `direct_implementation_required` is true when deterministic
validation finds an error or the workflow produces no candidate.

The runner records every accepted candidate as a lineage transition under
the retained run directory's `lineage/` tree. It writes the evidence
bundle — the episode tree, the adoption's state document as the child
identity document, and an artifact manifest over the retained candidate
files — and completes the bundle through the lineage crate's
`build-bundle` binary, invoked with the pinned toolchain, which writes the
adoption record and canonical manifest and prints the bundle's content
address. The record cites the accepted diagnosis-validator result for a
workflow, instruction, or tool candidate, and the accepted candidate-check
result for a source candidate. The parent state is the evaluated
program's identity document, retained from the resolved plan. The parent
and child state documents land in `lineage/states/` and the bundle in
`lineage/evidence/`, the layout the checker's resolvers read. The result's
`adoption` member records the addresses, identities, and verification
coordinates.
[`docs/lineage-identity.md`](../../docs/lineage-identity.md) "Harness
adoptions" states the one state-document rule: every adoption materializes
the program document that will run under it.

Apply a retained workflow candidate to any permitted task set with:

```sh
bazel run //evals/terminal_bench:foe-development -- \
  --workflow-candidate /absolute/path/to/workflow-candidate.json \
  --service-tier default \
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
source and whether token estimates were measurements or hard limits. It also
records the requested worker limit, maximum scheduled concurrency, every
execution group, process start counts, task resource reservations, credential
mode, credential expiry bounds, admission fallbacks, host resource snapshots,
and each group's timestamps and makespan. Concurrent tasks retain distinct
Harbor job names and result paths.

The runner writes the manifest atomically after every execution group. A
terminal interrupt terminates every active Harbor process group and removes
the temporary access-only credentials. A process-start failure also terminates
workers that already started. The runner updates the manifest during error and
interrupt cleanup. Completed and partially retained task records remain in the
manifest. A task whose Harbor process started retains that status even when its
result is incomplete. Tasks whose processes did not start receive an explicit
`not_started` record. The runner exits unsuccessfully.

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
