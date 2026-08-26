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
task image, uploads a statically linked Foe binary, and executes the task-owned
verifier after Foe exits. Adapter-generated targets run one coding episode.
Built-in targets invoke the implementation and terminal-audit workflow
constructed by the Foe binary. Harbor documents the custom interface in its
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

The installation checks require no provider login. Each installation worker
receives a distinct private file containing an empty JSON object. The runner
does not read, initialize, or lock the campaign OAuth state for these checks.

Authenticate Foe once before a provider-backed run:

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

The adapter scans every retained regular Foe artifact for exact access-token,
refresh-token, and API-key values. These artifacts include the program or
invocation, standard streams, episode tree, checker copy, and conformance
reports. A detected value invalidates the trial as infrastructure evidence.
The scan reports only whether a match exists. It does not isolate a credential
from a task process that can read the file.

## Check installation without model spend

The evaluation targets build `//:foe-portable`, a static x86-64 Linux binary
linked with musl. This binary avoids a dependency on the task image's glibc
version.

Check one task image, the Harbor adapter, and the portable binary without a
model request:

```sh
bazel run //evals/terminal_bench:foe-install-check
```

The adapter validates the installed command surface with `foe plan --schema`.
The runner rejects the check when Harbor records an errored or incomplete
trial, even when Harbor produced a readable result file.

The built-in coding workflow uses Foe's task-oriented command-line surface.
Validate that surface in the task container without a model request:

```sh
bazel run //evals/terminal_bench:foe-built-in-install-check
```

This check also requires the full command-line surface used by the
verifier-governed built-in lane. An incompatible binary fails during
installation. When Foe exits before creating an episode log, the adapter
reports the retained exit status and the final 2,000 characters of standard
error.

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
An installation failure or a missing task-required capability invalidates the
execution configuration claim. The retained task-owned score remains
unchanged.

## Validate verifier-governed completion without model spend

Six modified scenarios expose a public, read-only checker as a configured
tool. The adapter-generated execution path uses one coding episode with Sol
and low reasoning. That episode combines a typed return with
`done_when.verify`. Foe rejects a completion claim when the checker reports
findings. The model receives those findings and can continue within the
episode's remaining allowance.

The public checker provides development feedback. Harbor still runs the
unaltered task-owned Terminal-Bench verifier after Foe exits. Only the
task-owned verifier determines task quality. Results from this modified lane
are Foe-specific convergence evidence and do not contribute to a standard
Terminal-Bench score.

The selected scenarios cover six forms of completion:

- `cancel-async-tasks` executes concurrency and cancellation-cleanup probes.
- `dna-assembly` checks the declared primers, annealing, temperatures, and
  assembly overhangs.
- `fix-git` checks that the lost commit reaches `master` in a clean worktree.
- `git-multibranch` pushes distinct content to both branches and probes both
  live HTTPS endpoints.
- `gpt2-codegolf` compiles the size-bounded source and checks four public
  continuations.
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

Run the case after reviewing the planning estimate:

```sh
bazel run //evals/terminal_bench:foe-verifier-cancel-async-tasks -- \
  --label verifier-governed-cancel-async-tasks \
  --confirm-spend
```

Equivalent targets end in `foe-verifier-fix-git` and
`foe-verifier-large-scale-text-editing`. Additional targets cover
`dna-assembly`, `git-multibranch`, and `gpt2-codegolf`. These targets use the
priority service tier and low reasoning. They construct an adapter-owned
single-episode program with the public checker as its completion verifier.

Targets beginning with `foe-built-in-verifier-` invoke the coding workflow
constructed by the Foe binary. The workflow gives 60 model calls to an
implementation episode and 60 calls to a fresh terminal audit. The campaign
manifest records the resolved reasoning effort for the terminal audit. This
keeps source candidates with different audit effort in distinct diagnostic
configurations. The terminal audit owns `done_when.verify`. The adapter
disables Landlock because each Terminal-Bench task already runs in its Docker
container.

Preview or run one built-in verifier-governed scenario:

```sh
bazel run //evals/terminal_bench:foe-built-in-verifier-fix-git
bazel run //evals/terminal_bench:foe-built-in-verifier-fix-git -- \
  --label built-in-verifier-fix-git \
  --confirm-spend
```

Every `foe-verifier-` suffix has a corresponding
`foe-built-in-verifier-` target. The built-in targets reject
runner-defined diagnosis, escalation, workflow candidates, and hard token
allowances. They require `openai-codex/gpt-5.6-sol`, because the built-in
workflow supplies the evaluated low-to-high reasoning policy for that model.
They use the runner's selected service tier. The binary owns both model
stages and their call backstops. The adapter retains the exact command-line
arguments in `foe-invocation.json`. The
adapter-generated execution path retains its resolved input in
`foe-program.json`.

The configured executable's bytes participate in Foe's program identity. The
adapter also downloads the checker after the episode and compares its digest
with the source digest. A changed checker invalidates the trial as
infrastructure evidence.

When a configured workflow adds an audit-and-repair model, the implementation
episode returns its typed result without owning the completion condition. The
terminal audit owns `done_when.verify`. Both episodes retain the checker tool,
so the implementation may use it before handoff. A failed implementation check
cannot prevent the audit from inspecting and repairing the shared workspace.

Every assessed result retains Harbor's task-owned score. The runner records
`configuration_claim_valid` separately. Runtime, trace, credential, checker,
and resolved-program integrity failures set this field to `false` and make the
runner return a nonzero status. Such a result remains evidence about task
quality, but it cannot support a claim about the requested Foe configuration.
For a built-in target, the runner reconstructs the root program from the
episode log and checks both workflow nodes, model policies, call backstops,
sandbox mode, data-flow edges, and completion ownership.

## Run the built-in workflow on closed-book tasks

The closed-book built-in target runs Foe's implementation and terminal-audit
episodes on the frozen twelve-task development set:

```sh
bazel run //evals/terminal_bench:foe-built-in-development
bazel run //evals/terminal_bench:foe-built-in-development -- \
  --label built-in-development \
  --confirm-spend
```

The preview reports estimated spend without making a model request. Actual
usage can exceed this estimate because token use is measured without a hard
allowance. These tasks do not expose the public development checkers. Both
episodes must select and run task-relevant checks from the task environment.
Harbor runs the unchanged task-owned verifier after Foe exits.

## Preview and run one assessed task

Every model-backed target prints planning token estimates and an estimated
cost. The preview makes no model request:

```sh
bazel run //evals/terminal_bench:foe-smoke
```

Run the `fix-git` smoke case after reviewing that planning estimate:

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
- The host has at least 14 GiB of available memory and 100 GiB of free disk.
- Linux full-memory pressure stays below one percent over ten seconds.
- The host has not swapped pages out since the preceding execution.
- The access token covers every attempt and configured model stage. Its
  remaining lifetime also covers fifteen minutes of startup and five minutes
  of process settlement per attempt, plus Foe's sixty-second refresh margin.

A task that reserves 8 GiB runs alone. A failed pair admission runs its tasks
serially with the mutable credential. An out-of-memory result, an increased
swap-out counter, or excessive memory pressure makes every later task serial.
The campaign stops before starting more work when available memory falls below
10 GiB or free disk falls below 100 GiB.

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
Campaign Bazel targets request the priority service tier. A direct invocation
of `run.py` defaults to the standard tier and must set `--service-tier
priority` to match the campaign. The runner records the requested tier and
credit multiplier beside its token-derived cost estimate. OpenAI documents
Fast mode as targeting 1.5 times Standard speed while GPT-5.6 consumes 2.5
times the Standard ChatGPT credits. See the
[OpenAI speed documentation](https://developers.openai.com/codex/speed).

Luna and Terra are available for inexpensive development diagnosis:

```sh
bazel run //evals/terminal_bench:foe-smoke -- \
  --model openai-codex/gpt-5.6-luna \
  --reasoning-effort low \
  --confirm-spend
```

The current capability campaign excludes these models. Every provider request
in its acceptance evidence uses GPT-5.6 Sol.

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
the implementation on either path. The spending preview includes the
conditional path's planning estimate. Actual cost includes only the branch
that fires. The deeper
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

## Run the campaign task sets

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
tasks supply additional evidence without contributing to a protected task score:

```sh
bazel run //evals/terminal_bench:foe-capability-search
bazel run //evals/terminal_bench:foe-capability-search -- \
  --task regex-log \
  --label sol-low-search \
  --confirm-spend
```

The confirmation target contains eight tasks. The [campaign record](campaign.md)
gives their exposure state, acceptance rule, and stopping rule. Run two attempts
per task:

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
outcomes, final post-edit tool results, bounded verifier failure loci, and log
sequence numbers across the episode tree.

Collect diagnoses from one or more retained eligible runs. The command
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
built-in workflow constructed the model episodes.

The collector reads the retained task-owned verifier report after Foe exits.
It does not use verifier output captured inside a model episode. The task model
therefore receives no task-owned grader or checker data from this collection
path.

The retained diagnosis, result, and verifier report must be regular files
inside one trial directory. The collector rejects symlinks. It also rejects a
task name, task checksum, reward, error, or verifier digest that differs
between the diagnosis and retained result.

The collector groups results by task and complete execution configuration. It
groups failures by task, typed outcome, artifact mismatch, and named failed
verifier checks. A repeated failure contrast requires two matching failed
episodes and one successful episode for the same task. Trial infrastructure
failures cannot enter a contrast.

The coarse profile remains the grouping key. Each failed attempt also carries
its verifier-report digest and bounded failure loci. A locus contains the
pytest assertion expression, its concrete rewritten assertion, normalized
source location, and concise assertion message when those fields are
available. The collector removes host paths, memory addresses, timestamps,
terminal formatting, parameter values, and the remaining traceback. The
concrete rewritten assertion supplies failure operands. A digest identifies
the stable source locus without including those operands.

Every attempt records total, retained, omitted, unlocated, and ambiguous
failure counts. A repeated contrast requires one unique locus for every failed
test. An attempt with an omitted, unlocated, duplicate, or ambiguous locus
cannot enter a contrast.

The file keeps up to four input-growth landmarks and three entries from each
ranked result list. Input growth resets at each episode boundary. An artifact
mismatch may retain bounded model-authored completion details under
`untrusted_completion_claim`.

The collector accepts at most 12 diagnoses and 48 KiB of compact JSON. The
`self_improvement_evidence` group in `cases.json` contains development,
capability-search, and opened confirmation tasks. Calibration and the sealed
holdout remain outside the evidence set.

Evidence schema 6 stores repeated contrasts under
`repeated_failure_contrasts`. Each entry contains one task, a coarse failure
profile, at least two failed-attempt records, and at least one successful
episode identity. Each failed-attempt record contains its episode identity,
verifier-report digest, completeness counts, and failure-locus list. A digest
identifies the complete contrast. The failed and successful episode sets must
be disjoint.

A malformed retained verifier report stops collection. A missing or partial
report remains visible in its trajectory diagnosis and cannot enter a repeated
failure contrast.

Create a clean candidate worktree at the evaluated commit. Run the
self-improvement workflow from that worktree:

```sh
bazel run //evals/terminal_bench:self-improve -- \
  --candidate /path/to/clean/foe-candidate \
  --evidence "$PWD/target/foe-trajectory-evidence.json" \
  --cargo /absolute/path/to/toolchain/bin/cargo \
  --cargo-home /absolute/path/to/cargo-home \
  --candidate-kind auto \
  --keep "$PWD/target/foe-self-improvement" \
  --confirm-spend
```

Create private feedback after an externally evaluated source candidate has a
failed trial:

```sh
bazel run //evals/terminal_bench:foe-source-candidate-assessment -- create \
  --source-bundle /path/to/candidate-campaign/source-candidate-bundle \
  --parent-campaign /path/to/parent-campaign \
  --candidate-campaign /path/to/candidate-campaign \
  --assessment /private/path/source-candidate-assessment.json \
  --diagnostics /private/path/candidate-assessment-diagnostics.json
```

The private assessment retains raw campaign fields, trial fields, and verifier
reports for evaluator audit. Canonical identities bind the parent and candidate
evaluations, both source trees, the source bundle, the rejected source
candidate, the prior typed diagnosis, and the exact verified source patch.
Assessment construction requires complete,
conformant campaigns and source adoptions. It rejects symbolic links, escaped
paths, trial errors, Boolean or nonfinite rewards, and conflicting identities.

The diagnostics file is a reproducible view of the private assessment. It
contains complete bounded failure loci and final validation timelines. It
also contains qualified parent and candidate success references. The view
excludes task text, task names, task checksums, rewards, campaign labels,
absolute artifact paths, and unstructured grader prose. Its canonical JSON
must fit within 48 KiB.

Pass the private assessment to a later source-generation run:

```sh
bazel run //evals/terminal_bench:self-improve -- \
  --candidate /path/to/clean/parent-source \
  --evidence "$PWD/target/foe-trajectory-evidence.json" \
  --candidate-assessment /private/path/source-candidate-assessment.json \
  --cargo /absolute/path/to/toolchain/bin/cargo \
  --cargo-home /absolute/path/to/cargo-home \
  --candidate-kind source-change \
  --keep "$PWD/target/foe-self-improvement" \
  --confirm-spend
```

The runner re-derives and validates the bounded diagnostics. The existing
trajectory tool node supplies them only to the fresh diagnosis node. The
implementation node receives the task and revised typed diagnosis. The audit
receives those values and the implementation handoff. Neither coding node can
read the assessment artifacts.

The revised diagnosis cites the assessment contrast, rejected candidate,
prior diagnosis, every failed attempt, every failed verifier, every failure
locus, and all qualified successes. It chooses `retain`, `narrow`, `replace`,
or `insufficient-evidence`. The generated diagnosis verifier checks these
citations before source implementation starts.

Assessment diagnostics and a canonical generation-context record enter a
later accepted source bundle before source capture. These files bind the
feedback to the later diagnosis without participating in the source-candidate
identity. The runner rejects a later source candidate whose identity equals
the rejected source candidate.

The runner emits a version 3 program. The campaign Bazel target supplies the
`priority` service tier for diagnosis, implementation, review, and
finalization. A direct invocation of `run_self_improvement.py` defaults to
`default` and must set `--service-tier priority` to match the campaign. The
model blocks
explicitly name the credential file from Foe's per-provider convention under
the passwd home directory. The retained plan and `episode/start.program`
therefore carry the same runtime-resolved model block. `--candidate-kind` can
restrict the diagnosis to `source-change`, `workflow-configuration`,
`instruction-revision`, or `tool-definition`. The diagnosis verifier enforces
the restriction before another model node can start. Every restricted run may
return `insufficient-evidence`.

The trusted source-candidate checker can capture evidence from runtime build
`sha256:ff7d062a57acf865e22d7781fb7e9c05ac95863e5a255fc3145d4479e0eebb59`,
whose plan output omitted the resolved OpenAI Codex token path. Capture adds
the controller-observed absolute path to the canonical parent plan. The
checker requires that path in every runtime-authored root and child program.
It also verifies each workflow child's runtime-reserved leaf budget and
recomputed identity before external evaluation may spend a model request.

Automatic selection chooses only `source-change`, `workflow-configuration`,
or `insufficient-evidence`. The Terminal-Bench runner can apply and evaluate
the first two kinds. An explicit instruction or tool run can retain a typed
proposal, but no Terminal-Bench application path exists for that proposal.

GPT-5.6 Sol with low reasoning produces the bounded diagnosis from the supplied
digest. Its ordinary tools are `block` and the generated candidate validator,
so it cannot inspect the candidate source or retained run directories. Its
typed result contains one causal contrast, one intervention, and the controls
and falsification condition needed to evaluate that intervention.

A candidate-producing diagnosis returns the digest of one repeated contrast.
It must cite every failed episode, verifier-report digest, and locus digest in
that contrast. It gives each attempt a local explanation and states one shared
mechanism. The diagnosis returns `insufficient-evidence` when heterogeneous
loci do not support one shared mechanism. The generated diagnosis verifier
resolves the contrast digest and enforces citation coverage before another
workflow node can start.

The diagnosis node's completion verifier is a generated executable declared
through `done_when.verify`. The runner writes it before the episode; it
applies the same identity-bound candidate validation the runner applies
after the episode, judging the returned typed diagnosis. A workflow,
instruction, or tool candidate is therefore accepted inside the episode as
an authoritative `verification/result` event in the diagnosis child's log,
and findings return to the same child. A source diagnosis and a typed
abstention pass this verifier: a source candidate is judged by the
implementation node's candidate check, and an abstention proposes nothing.

The diagnosis chooses `implement-source` when repeated retained failures
support one general, source-owned, falsifiable mechanism. Candidate generation
does not require prior evidence of transfer or task-quality improvement.
Unchanged external task evaluation decides promotion. The diagnosis chooses
`configure-workflow` when an independent audit stage supplies a repeated
quality gain. An explicit run can choose `revise-instructions` or `define-tool`
to retain a proposal for a future application mechanism. The diagnosis chooses
`insufficient-evidence` when the contrast identifies only model capability or
requires semantic task knowledge absent from the log. That choice ends the
workflow before a coding episode and sets `direct_implementation_required`.

When the diagnosis chooses `implement-source`, the configured coding model
acts with `read`, `grep`, `edit`, and `bash`. The diagnosis node's branch edge
controls whether the coding node fires. The coding node lists `task` and
`diagnose-runtime` under `follows`, which carries both values into its clean
context. The raw trajectory digest remains inside the diagnosis child. The
coding child locates the affected implementation, test, and specification
files. Its write authority covers runtime crates, specifications, and examples.
It cannot write evaluation code or benchmark material.

The implementation node returns a typed handoff with its summary, changed
paths, validation, and unresolved risks. A fresh source-review node receives
the task, diagnosis, and handoff. It uses the same model route and service tier
as implementation with `xhigh` reasoning. Its 44-request allowance cannot
consume the 16 requests reserved for a fresh finalization child. A review that
ends blocked or exhausted contributes a declared empty handoff, so finalization
still runs. The finalization child inspects the current source, runs the
candidate checker first, repairs remaining findings, and owns terminal
completion through `done_when.verify`, with four correction attempts. If
finalization ends after producing an artifact, the runner recovers the
diagnosis from its child episode and applies the external source checker to the
candidate.

When the diagnosis chooses `configure-workflow`, the runner validates the
typed independent-audit setting. It writes `workflow-candidate.json` beside
the retained result. The candidate digest binds the setting to the evaluated
source tree, binary, evidence file, and preserved execution controls. The
setting must appear in at least two successful attempts in the supplied
evidence.

When the diagnosis chooses `revise-instructions`, the runner validates the
typed revision against the retained `program.json`: the named section key
resolves to one instruction section, and the old text occurs once in it. It
writes `instruction-candidate.json` beside the retained
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
loop backstop. Implementation and audit each have 60-call, 3,600-second
safety backstops. Each model child ends as blocked after eight consecutive
identical tool calls or assistant turns.

The diagnosis preserves the primary model route, reasoning effort, task
allowances, token policy, service tier, and task set. A workflow candidate
binds the primary model, reasoning effort, service tier, token policy,
workflow owner, and completion-governance mode. Task identity remains variable
so the evaluation can measure transfer. Verified task quality is the promotion
metric. Tokens, cost, cache use, latency, outcome accuracy, and conformance
remain recorded diagnostics.

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
recorded baseline ceiling. Automatic source candidates cannot change Cargo,
Bazel, module, toolchain, package, or build-script metadata. The checker
compares that metadata with the trusted baseline and rescans repository
status, source bytes, and build metadata after validation. The coding child
uses this checker as the line-count authority because the repository script
reports only absolute limits. Its only generated-file write authority is the candidate's private
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

The no-spend form generates the complete workflow document in a temporary
directory. It runs that document through `foe plan`. A schema, authority, or
construction error therefore fails before the runner reports that the
workflow is ready. The requested retained directory is created only after
`--confirm-spend` is supplied. A preview removes the empty candidate validation
directories it created while resolving program grants.

The coding child receives read-only access to the candidate worktree's Git
metadata. This access lets `git status`, `git diff`, and the independent
candidate checker operate when the candidate is a linked Git worktree.

The runner validates the artifact after Foe exits. A valid artifact remains
accepted when the episode exhausted its reporting budget after producing the
files. A source candidate binds the base Git tree and complete changed Git
entries. Each entry retains object type, mode, blob identity, bytes, or a
deletion. A workflow candidate binds the independent-audit setting and
preserved controls.

Workflow acceptance requires successful lineage adoption. An adoption failure
sets `direct_implementation_required` and makes the self-improvement process
exit unsuccessfully. Source artifact acceptance requires successful evidence
capture by the trusted source checker. External evaluation completes source
lineage after a rebuilt binary exists.

The candidate checker establishes repository conformance. Quality promotion
uses only the unchanged task-owned Terminal-Bench grader, which runs outside
the candidate's authority. Tokens, estimated cost, cache use, and latency are
recorded without deciding candidate acceptance.

The self-improvement runner records accepted workflow candidates under the
retained run directory's `lineage/` tree. Its evidence bundle contains the
episode tree, child identity document, and artifact manifest. Trusted
`build-bundle` and ancestry-checker binaries come from the controller
checkout's Bazel action. The writable candidate checkout is a separate input.
The result records their content digests. The structured ancestry report must
begin with the adopted program identity.

An accepted source candidate retains a `source-candidate-bundle` directory.
Its manifest binds the parent identity, proposal verification, source objects,
and every retained file. The bundle includes the generated candidate checker
as a regular file. Its digest binds the accepted result in the finalization
child to the exact executable that produced it. The result reports
`pending-external-evaluation` because source bytes cannot determine a Foe
program identity.
[`docs/lineage-identity.md`](../../docs/lineage-identity.md) "Harness
adoptions" states the one state-document rule: every adoption materializes
the program document that will run under it.

Apply a retained workflow candidate to any permitted task set with:

```sh
bazel run //evals/terminal_bench:foe-development -- \
  --workflow-candidate /absolute/path/to/workflow-candidate.json \
  --service-tier priority \
  --confirm-spend
```

The requested model, primary reasoning effort, service tier, and token policy
must match the candidate. Workflow ownership and completion governance must
also match. Task identity may vary. The current Foe source tree and binary
must match the candidate's recorded identity.

Run the source evaluation target from a separate immutable controller
checkout. Name an absolute Bazel executable outside the candidate tree:

```sh
cd /absolute/path/to/controller-checkout
bazel run //evals/terminal_bench:foe-source-candidate -- \
  --source-root /absolute/path/to/candidate/Cargo.toml \
  --source-adoption /absolute/path/to/self-improvement/result.json \
  --controller-bazel /absolute/path/to/bazel \
  --built-in-workflow \
  --service-tier priority \
  --confirm-spend
```

The target supplies two controller roots. The source root contains the
evaluation runner and has a recorded committed Git tree. The build-output root
contains the Bazel-built source checker and records that checker's digest.
Both roots remain separate from the candidate checkout. This separation lets
the generated Bazel target use trusted outputs under `bazel-out` without
treating candidate-controlled build products as controller evidence.

The argument may also name the `source-candidate-bundle` directory. Before
provider spend, the trusted checker validates the manifest and its regular
files. It compares the recorded Git objects, modes, bytes, and deletions with
the clean candidate tree. The target's default binary supplies only the
no-spend diagnostic preflight. A confirmed campaign builds `//:foe-portable`
from the clean accepted tree with the named controller Bazel executable. It
retains and evaluates only that output.

The controller rejects a source result whose recorded bundle, candidate,
base-tree, or parent-program identity differs from the bundle. A raw bundle
cannot claim a capture-checker identity. The campaign records the trusted
checker it actually invokes. The controller also rejects incomplete proposal
trees, spawn programs that do not resolve to their child identity, unrelated
verification episodes, and unauthorized verifiers. The finalization child
must declare the accepted verifier tool. Its recorded verifier identity must
equal the retained candidate checker's digest. A confirmed campaign copies the
validated bundle into its run directory before provider spend. Later adoption
uses this frozen copy.

The running form is the only command that materializes the built-in workflow.
After that command ends, the adapter reads the resolved program and task from
the root `episode/start`. It adds the program format version, runs the same
installed binary with `foe plan --config`, and retains the resulting JSON
report. The verifier and credential paths remain present until planning ends.
The adapter requires the reconstructed plan to equal the recorded program,
task, and identity. The trusted checker also requires the root episode to
carry the rebuilt binary digest. It creates the child state from the checked
identity document. The canonical ancestry checker must accept the transition.

`campaign.json` records the controller source and build-output roots, the
committed controller source tree, and the runner and checker paths and digests.
It records the source candidate and one completed adoption per trial. The
source-build record contains the command, Bazel path, version, and digest,
protected build-graph identity, complete build-log digest, source-tree
identity, and output digest. Each adoption includes the adoption, evidence,
program, state, and parent identities. It also records the checker digest and
evaluated source and binary pair. A failed adoption invalidates the trial and
makes the campaign exit unsuccessfully. The task record sets
`direct_implementation_required`.

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

For an adapter-generated program, the adapter runs `foe plan` inside the task
container before its first provider request. An invalid program is a setup
error with zero model spend. For the built-in workflow, the adapter performs
the post-run reconstruction described above because no standalone command
materializes that workflow. Either planning failure invalidates the task
record and must be corrected before a repeated result is complete.

The Harbor trial's `agent/foe-episode/` directory is the complete native Foe
episode tree. It contains `episode.jsonl`, child episodes, spill values, and
renderings. The neighboring files include the generated Foe program, plan
report, Foe standard output, Foe standard error, the typed process exit status,
and the runtime conformance report. The adapter sums provider-reported input, output,
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
