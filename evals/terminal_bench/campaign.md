# Foe capability campaign record

This record defines the evaluation campaign that prepares Foe for a
Terminal-Bench 2.1 submission. The campaign ends with retained calibration and
sealed-holdout dispositions. A full 89-task benchmark run requires a separate
decision.

Terminal-Bench 2.1 is pinned as `terminal-bench/terminal-bench-2-1@6`. The benchmark contains 89 container tasks and task-owned verifiers. Its maintainers require at least five trials per task for an official submission. [The Terminal-Bench 2.1 repository](https://github.com/harbor-framework/terminal-bench-2-1) specifies the submission protocol. [The release description](https://www.tbench.ai/news/terminal-bench-2-1) describes the task corrections and continuous validation.

Results below from retired protected sets remain as historical evidence. They
do not satisfy the confirmation, calibration, or holdout criteria defined in
this record.

## Campaign objective

The campaign must produce one frozen Foe release that converts at least three
reproducible harness-limited failures into successes. The release must reach at
least 85 percent quality on the twenty-task calibration set. It must also
demonstrate repeatable self-improvement that transfers beyond each activation
case.

Every provider request uses GPT-5.6 Sol and the standard service tier. Low
reasoning is the primary coding setting. Conditional escalation may use at
most `xhigh` reasoning. Token use, estimated cost, cache use, and latency
are measurements. Task quality is the only candidate promotion metric until
every quality gate passes.

Candidate changes may affect Foe source, a general workflow configuration, an
instruction section of the retained self-improvement program, or a declared
executable tool. Task-specific instructions, benchmark identifiers, fixture
values, and grader rules are excluded from candidate changes.

## Success criteria

The release must satisfy all of these quality conditions:

1. Convert at least three reproducible harness-limited development failures
   into task-verifier successes.
2. Pass at least eleven of the twelve development tasks.
3. Lose no assessed attempt to an avoidable configuration, executable,
   sandbox, allowance, credential, or container error.
4. Pass every local runtime-contract evaluation.
5. Produce at least fourteen successes across two attempts on each of the
   eight confirmation tasks. Every confirmation task must succeed at least
   once.
6. Pass at least seventeen of the twenty calibration tasks.
7. Pass at least seven of the eight sealed holdout tasks. A second attempt may
   resolve no more than two ambiguous first-attempt failures.
8. Retain each task verifier result, Foe episode, source identity, runtime
   identity, and conformance report.
9. Qualify credential-safe two-worker execution on a matched four-task batch.
   It must preserve scores and evidence while reducing makespan by at least
   one third.

The self-improvement evidence must satisfy all of these conditions:

1. Identity-bound cross-trajectory evidence must produce at least two accepted
   improvements.
2. At least one improvement must change source, add a regression test, and
   update every affected specification.
3. The other improvement may change source, a tool, or a general workflow
   configuration.
4. One declared workflow must perform diagnosis, implementation,
   `done_when.verify` correction, and external evaluation.
5. Each improvement must raise task quality on its activation cases and
   transfer beyond those cases.
6. A failed self-improvement attempt triggers a direct implementation and a
   repair to the failed self-improvement mechanism. The repaired workflow must
   then run again.

Task quality is the only candidate promotion metric. Provider-reported tokens,
estimated cost, wall time, cache use, outcome accuracy, and trace conformance
remain required diagnostics. Missing provider usage marks the resource record
as incomplete. It does not change the task-owned quality score.
Infrastructure exceptions receive the score assigned by the task framework.
They also violate the requirement to eliminate avoidable infrastructure
failures when the failure originates in Foe or its evaluation adapter.

After every quality condition passes, matched successful tasks must show at
least a 20 percent reduction in median estimated cost. The cost comparison may
use conditional Sol reasoning escalation. It must preserve task quality.

Planning estimates bound campaign exposure before each confirmed command.
They do not reject a candidate that improves task quality. Development runs
use the standard service tier and record it in the manifest. Foe records
usage without enforcing token ceilings during ordinary quality runs.

A retained qualification remains valid while the evaluated source tree,
portable binary, task registry, model configuration, and task semantics remain
unchanged. Documentation edits, diagnostic collectors, and controller safety
checks do not invalidate task-quality evidence. A changed runtime or
model-visible program requires only the smallest gate whose result can change.
Every reuse decision records the retained manifest digest and the identities
that establish equivalence.

### Incremental improvement disposition

The release quality gates decide whether one combined Foe release advances to
confirmation, calibration, or sealed holdout. They do not decide whether an
individual improvement remains available for review.

A focused change remains on a reviewable branch when unchanged external
Terminal-Bench evaluation records any task-quality improvement over its bound
parent. The campaign records the number of attempts and the uncertainty of the
comparison. A result from one attempt can justify preserving the change for
review, but it cannot establish repeatability. Repository validation and
source review must still pass before the change can enter Foe.

The pull request for an incremental improvement contains the general Foe
mechanism, its regression tests, affected specifications, and the external
activation evidence. It excludes task-specific instructions, grader rules,
fixture values, raw trajectories, workspaces, and credentials. A focused
change can merge independently when its implementation is sound, even when the
combined release has not passed every campaign quality gate.

Rejected combined releases do not discard their successful components. The
campaign gives each externally validated component one of three dispositions:

- send a focused pull request for a sound general mechanism;
- retain the branch for more activation evidence when the mechanism remains
  uncertain;
- reject the component when source review finds a correctness defect that the
  evaluated score did not expose.

## Confirmation-quality recovery

The frozen candidate passed eleven of twelve closed-book development tasks.
Its confirmation attempt was rejected after eight successes from eleven
scored attempts. Five remaining attempts could raise the total only to
thirteen, below the required fourteen successes from sixteen attempts.
Calibration and sealed-holdout tasks remain unopened.

The campaign returns to development before opening another protected task. A
revised candidate must satisfy these conditions:

1. Convert three reproducible harness-limited failures into task-owned grader
   successes.
2. Produce two accepted, identity-bound improvements through the declared
   self-improvement workflow. One improvement must change Foe source, add a
   regression test, and update every affected specification.
3. Show at least two successes from three unchanged Terminal-Bench attempts
   on each activation case.
4. Transfer each accepted improvement to a development task absent from its
   diagnosis corpus.
5. Pass at least eleven of the twelve development tasks.
6. Freeze one combined candidate before confirmation. The candidate must
   produce at least fourteen successes from sixteen attempts and succeed at
   least once on every confirmation task.
7. Provide credential-safe execution of two independent assessed trials. A
   matched batch must reduce makespan by at least one third while preserving
   task scores, trace conformance, credential isolation, and complete evidence.

The first new coverage after confirmation uses five tasks from the frozen
calibration set:

- `llm-inference-batching-scheduler`;
- `modernize-scientific-stack`;
- `multi-source-data-merger`;
- `nginx-request-logging`;
- `polyglot-c-py`.

At least four of these five tasks must succeed on their first assessed
attempt. This result decides whether to run the other fifteen calibration
tasks. It does not replace the requirement for seventeen successes across the
full calibration set. The sealed holdout stays closed until the calibration
disposition and its candidate identity are recorded.

## Evidence recording protocol

One coordinator owns the campaign record. Workers return artifacts and typed
results to the coordinator. They never edit this file concurrently. The
coordinator appends a result and commits the record before making the next
candidate or task-exposure decision.

Every campaign objective records the following sections in order:

1. The objective, terminal decision, promotion rule, retry rule, and
   sequential stopping rule.
2. The frozen evidence boundary, including the `cases.json` digest and the
   exposure state of every task set.
3. The source tree, binary, program, runner, checker, model, reasoning,
   service-tier, sandbox, and allowance identities.
4. Repository validation, micro evaluation, binary installation, container
   capability probes, checker negative controls, and checker oracle controls.
5. One row for every development, activation, transfer, confirmation,
   calibration, and holdout attempt.
6. Failure diagnoses, candidate generation, independent review, correction,
   repository validation, and external task-quality disposition.
7. Parallel execution measurements, cumulative resource accounting, an
   evidence index, and the final goal disposition.

Each attempt row records these fields:

- start and end time in UTC;
- run label, task, attempt number, evaluation lane, worker, host slot, and
  concurrency cohort;
- source-tree, binary, program, runner, checker, and candidate digests;
- model, reasoning effort, service tier, token policy, model-call allowance,
  and time allowance for every model stage;
- Foe outcome, task-owned score, trace conformance, infrastructure status,
  and credential-exposure scan;
- model calls, input tokens, cached-input tokens, output tokens, estimated
  cost, wall time, and usage completeness;
- retained evidence path, evidence digest, and storage location.

The record gives failed, blocked, exhausted, interrupted, and incomplete runs
the same fields as successful runs. An infrastructure failure remains in the
resource accounting even when it is excluded from a quality comparison. Raw
episodes, workspaces, spill files, container artifacts, and credentials stay
outside Git. Git retains the Markdown record, task registry, checker sources,
runner changes, corpus manifests, and content digests.

### Parallel execution

The assessed runner uses one worker by default and accepts at most two workers.
It holds one exclusive lock on the renewable OAuth credential state for the
complete run. Parallel workers receive separate access-only credentials that
omit the refresh token. The runner returns to serial execution when the access
token lifetime or host capacity cannot cover a parallel cohort.

The concurrency implementation uses one campaign coordinator. It gives each
trial an isolated job directory and access-only credential. The adapter checks
that each credential is unchanged after the trial and scans retained evidence
for credential exposure. The runner tests cover unique output paths, complete
result collection, cancellation, lease expiry, and a worker failure while
another worker completes.

At most two workers may run provider-free capability probes or verifier
controls within a task set that is already open. Workers may also analyze
different retained trajectories in parallel. Development and sealed holdout
never run concurrently because the candidate must be frozen before the
holdout opens.

Credential-safe assessed concurrency starts with at most two trials. An
eight-gibibyte task runs alone. Two concurrent trials may declare at most
eight gibibytes of memory in total. The scheduler starts a cohort only when
the host has at least fourteen gibibytes of available memory and one hundred
gibibytes of free disk. It stops admission after memory pressure, swap-out,
an out-of-memory termination, or less than ten gibibytes of available memory.

Every concurrent cohort records its cap, task membership, resource
reservations, host resource snapshots, process starts, makespan, and
invalidated attempts. A matched serial cohort supplies the reference for any
runtime claim. Concurrency remains identical between a baseline and candidate
comparison.

The first assessed concurrency qualification uses four already-open
development tasks selected from recorded resource metadata before execution.
It runs the same frozen binary and program once serially and once with two
workers. The parallel command uses `--require-parallel`, so failed admission
stops without making a model request. The parallel cohort must produce the
same task scores and integrity classifications. Its makespan must be no more
than two thirds of the serial makespan. Failure returns assessed execution to
one worker while the concurrency defect is corrected and qualified again.

## Task sets

The task membership is stored in [`cases.json`](cases.json). Task selection used only the pinned task metadata, including category, difficulty, and resource limits. Every protected task was frozen before its instructions, workspace, or trajectory was opened. Tasks exposed by earlier campaign runs remain historical evidence and are excluded from the protected sets.

### Micro evaluation

The local micro evaluation covers five Foe contracts:

- containment after a denied write;
- typed evidence returned under a model-call limit;
- delegated quotation across child episodes;
- declared workflow provenance;
- continuity after context compaction.

These cases are inexpensive diagnostics. They test runtime contracts and do not estimate Terminal-Bench accuracy.

### Development evidence

The development set contains twelve tasks with inspected trajectories:

- `cancel-async-tasks`;
- `git-multibranch`;
- `fix-git`;
- `sqlite-db-truncate`;
- `sanitize-git-repo`;
- `large-scale-text-editing`;
- `gpt2-codegolf`;
- `fix-ocaml-gc`;
- `path-tracing-reverse`;
- `regex-chess`;
- `model-extraction-relu-logits`;
- `dna-assembly`.

Five other inspected tasks remain available for diagnostic work:

- `password-recovery`;
- `polyglot-rust-c`;
- `regex-log`;
- `write-compressor`;
- `feal-linear-cryptanalysis`.

These five tasks do not contribute to a protected-set score. They may provide
extra activation evidence for a change proposed from the development set.

### Confirmation evidence

Eight tasks remain closed until a candidate and its acceptance rule are frozen:

- `build-pov-ray`;
- `caffe-cifar-10`;
- `configure-git-webserver`;
- `count-dataset-tokens`;
- `crack-7z-hash`;
- `dna-insert`;
- `log-summary-date-ranges`;
- `overfull-hbox`.

Each candidate receives two attempts per confirmation task. Raw confirmation trajectories remain outside self-improvement evidence until the candidate disposition is recorded.

The OpenAI Codex route rejected `vulnerable-secret` because its requests
triggered the provider's cybersecurity policy. Two frozen attempts were
blocked at the initial request or first follow-up. One authorization-scope
repair attempt was blocked at the initial request. The provider returned
`invalid_request`, and none of the three attempts produced a gradable artifact.

This provider restriction prevents the case from measuring Foe quality on the
selected route. The case remains in `provider_policy_incompatible` for route
compatibility testing. It does not contribute to the candidate's confirmation
score.

### Calibration evidence

The calibration set contains twenty tasks:

- `chess-best-move`;
- `code-from-image`;
- `extract-moves-from-video`;
- `feal-differential-cryptanalysis`;
- `fix-code-vulnerability`;
- `gcode-to-text`;
- `install-windows-3.11`;
- `largest-eigenval`;
- `llm-inference-batching-scheduler`;
- `mailman`;
- `make-doom-for-mips`;
- `merge-diff-arc-agi-task`;
- `modernize-scientific-stack`;
- `mteb-leaderboard`;
- `multi-source-data-merger`;
- `nginx-request-logging`;
- `openssl-selfsigned-cert`;
- `polyglot-c-py`;
- `portfolio-optimization`;
- `pypi-server`.

The sealed holdout contains eight tasks:

- `protein-assembly`;
- `pytorch-model-recovery`;
- `raman-fitting`;
- `rstan-to-pystan`;
- `sam-cell-seg`;
- `torch-pipeline-parallelism`;
- `tune-mjcf`;
- `winning-avg-corewars`.

The protected tasks were selected using only names, categories, difficulties,
resource limits, and timeouts from the pinned metadata. Their instructions,
workspaces, and trajectories remained unopened at selection.
[The pinned task metadata](https://github.com/harbor-framework/terminal-bench-2-1/tree/7131e4375048a0e408a8fb404b5f499d726b695b/tasks)
is the selection source.

Calibration trajectories remain closed until the development and confirmation
criteria pass. Sealed-holdout trajectories remain closed until the calibration
result and its decision rule are recorded.

## Service-tier correction for the recorded 2026-08-25 results

The GPT-2, regex-chess, initial DNA assembly, and source self-improvement runs
below recorded `service_tier: priority` in their manifests. Their evaluated
transport did not include that field in OpenAI Responses requests. The
provider therefore selected the effective tier.

[The OpenAI Responses reference](https://developers.openai.com/api/reference/resources/responses/methods/create)
defines `service_tier` as a request field and reports the tier used in the
response object.

The task-owned quality scores, provider-reported token counts, and estimated
API costs remain valid. These runs provide no evidence about Fast-mode
latency or ChatGPT credit consumption. Their retained directory labels record
the requested configuration and remain unchanged.

Runtime binary
`sha256:26f151b998d07438a5a21a14115a14aa5109030c98c5f0ca756d8d9c413ebd25`
is the first evaluated candidate in this record that sends the configured
service tier in the request body.

## Verifier-governed GPT-2 development result

On 2026-08-25, three verifier-governed development attempts used source tree
`git-tree-sha1:81d736a1b13dc98863e566567240455fdb2b17ad` and runtime binary
`sha256:effa80a6a824912c61aee119bd0f322805e864c415eabad63d6dd6b9df8cdabc`.
The manifests requested the `priority` service tier. The correction above
governs the effective tier. The primary model was GPT-5.6 Sol with low
reasoning.

The low-only attempt received 60 model-call capacity and no token ceiling. It
used 59 calls while testing many checkpoint layouts. The completion verifier
continued to reject repeated incorrect tokens. Foe ended as exhausted, and
the unchanged task-owned grader awarded `0.0`.

Two matched attempts added a fresh Sol `xhigh` independent audit with 60
calls. Both audits received the low-effort child value through a declared
workflow edge. Both audits repaired the checkpoint layout, passed the public
completion verifier, completed, and received `1.0` from the unchanged grader.

The two successful attempts used 73 model calls, 1,442,052 input tokens,
890,368 cached-input tokens, and 46,021 output tokens. Their combined
estimated cost was $3.483303. The low-only attempt has one response without a
usage record, so its complete resource total is unavailable.

The identity-bound self-improvement workflow read the three retained
diagnoses. Its first diagnosis selected the measured audit intervention but
copied an unsupported six-call value into its typed setting. Commit `7a045e2`
moved that factual binding into the deterministic evidence resolver. The
repaired workflow then used one Luna-low request and produced the
accepted workflow candidate
`sha256:d8ce2e7acbb626c18b552619d7c53bb7be0ad7eeff7496211395c05e10b23bbb`.
The candidate binds the measured 60-call Sol `xhigh` audit to the evaluated
source, binary, evidence digest, and preserved low-effort controls.

The generated candidate then ran on unchanged, closed-book Terminal-Bench
without the public completion verifier. The low child used 14 calls, and the
independent audit used 29 calls. The unchanged task-owned grader awarded
`1.0`. The run used 1,328,448 input tokens, 899,072 cached-input tokens, and
36,790 output tokens. Its estimated cost was $2.812933.

The retained evidence is stored under these local paths:

- `target/terminal-bench-jobs/priority-verifier-gpt2-sol-low-only-20260825T005757Z`;
- `target/terminal-bench-jobs/priority-verifier-gpt2-low-xhigh-audit-20260825T002522Z`;
- `target/terminal-bench-jobs/priority-verifier-gpt2-low-xhigh-audit-repeat-20260825T003908Z`;
- `target/priority-gpt2-verifier-workflow-evidence.json`;
- `target/priority-gpt2-workflow-self-improvement-fixed`;
- `/home/sunil/git/foe-gpt2-self-improvement-candidate/target/terminal-bench-jobs/priority-self-improved-gpt2-vanilla-20260825T012154Z`.

This result establishes one accepted self-improvement and one converted
development failure. The `regex-chess` and `dna-assembly` results below test
whether the same intervention transfers to other tasks.

## Verifier-governed regex-chess development result

On 2026-08-25, three closed-book development attempts used source tree
`git-tree-sha1:86b6c3c860c90842738167d0736985a762c22c93` and runtime binary
`sha256:effa80a6a824912c61aee119bd0f322805e864c415eabad63d6dd6b9df8cdabc`.
The manifests requested the `priority` service tier. The correction above
governs the effective tier. The primary model was Sol `low` with 60 calls and
no token ceiling.

The low-only attempt completed after 30 calls. It passed the supplied game
fixture and several targeted positions. The unchanged grader found malformed
FEN output and incorrect castling state in three other games, so it awarded
`0.0`.

Two matched attempts added a fresh Sol `xhigh` independent audit with 60
calls. Both audits generated broader legal-position probes, found semantic
defects, repaired the generator, and received `1.0` from the unchanged grader.
The attempts used 35 and 45 calls. Together they used 1,648,448 input tokens,
1,005,056 cached-input tokens, and 55,875 output tokens. Their estimated cost
was $4.093090.

The three trajectory diagnoses formed a 54,575-byte identity-bound evidence
file. One Luna `low` request used 18,339 input tokens and 703 output
tokens to diagnose the repeated audit contrast. The self-improvement runner
then produced the accepted workflow candidate
`sha256:8237704f9221b80d7219a43d07eacacac9cc8d6753fb4a420cc993da771460b5`.
The candidate binds the 60-call Sol `xhigh` audit to the evaluated source,
binary, evidence digest, and low-effort execution controls.

This result repeats the GPT-2 self-improvement mechanism on a different task.
It remains one general workflow improvement because both generated candidates
encode the same intervention.

The retained local evidence is stored under these paths:

- `target/terminal-bench-jobs/priority-regex-chess-sol-low-baseline-20260825T014203Z`;
- `target/terminal-bench-jobs/priority-regex-chess-low-xhigh-audit-20260825T014812Z`;
- `target/terminal-bench-jobs/priority-regex-chess-low-xhigh-audit-repeat-20260825T020145Z`;
- `target/priority-regex-chess-workflow-evidence.json`;
- `target/priority-regex-chess-self-improvement`.

## Verifier-governed DNA assembly development result

On 2026-08-25, a Sol `low` baseline and two generated-candidate attempts used
the same source tree and runtime binary as the regex-chess result. The
manifests requested the `priority` service tier. The correction above governs
the effective tier. All token allowances were measurement-only.

The low-only baseline completed after eight calls and received `0.0`. Its
vector primer pair differed by 6.88 degrees Celsius, beyond the public
five-degree limit. The run used 50,157 input tokens, 12,288 cached-input
tokens, and 3,437 output tokens. Its estimated cost was $0.225131.

The regex-derived workflow candidate passed its first transfer attempt. The
low child used eight calls and explicitly reported that `oligotm` validation
remained unresolved. The audit installed `primer3`, measured every annealing
tract, repaired all eight primers, and received `1.0` from the unchanged
grader. The attempt used 43 calls and cost an estimated $2.192926.

The repeated candidate attempt received `0.0`. Its audit selected a 45-base
reverse binding sequence whose preceding four overhang bases also matched the
template. The actual annealed tract was 49 bases, beyond the public 45-base
limit. The audit's reconstruction omitted this overlap and therefore accepted
an invalid artifact.

The candidate has transferred once beyond its activation task. Its one success
from two DNA attempts does not establish a reproducible third conversion.

The public completion checker now validates the complete annealed tract and
returns failures through `done_when.verify`. Its SHA-256 digest is
`a93cc0ff4964ef3a9e0096288f06f0d991ef5a4b80fd25f7723374c4b0a59450`.
An untouched workspace produced a finding. An independent oracle passed the
checker and received `1.0` from the unchanged task-owned grader.

Two convergence attempts used source tree
`git-tree-sha1:edbf696b434b3027f6a55c5a21e18d81e2b30fea`. Each attempt gave
Sol `low` and a fresh Sol `xhigh` audit 60 calls. Both received `1.0` from the
unchanged task-owned grader. The first used 28 calls. One response lacked
provider usage, so its recorded 275,518 input tokens, 144,896 cached-input
tokens, and 14,385 output tokens are incomplete. The repeat used 30 calls,
372,011 input tokens, 128,000 cached-input tokens, and 23,912 output tokens.
Its estimated cost was $1.505484.

The verifier-governed workflow is reproducible at two successes from two
attempts. Together with the GPT-2 and regex-chess results, this establishes
three development failures converted into repeated successes by changes to
Foe's execution contract. The DNA result also shows transfer of the generated
audit intervention beyond its GPT-2 and regex-chess activation tasks.

The retained local evidence is stored under these paths:

- `target/terminal-bench-capability-probes/dna-assembly-20260825T022413Z`;
- `target/terminal-bench-jobs/priority-dna-assembly-sol-low-baseline-20260825T022447Z`;
- `target/terminal-bench-jobs/priority-self-improved-dna-assembly-20260825T022725Z`;
- `target/terminal-bench-jobs/priority-self-improved-dna-assembly-repeat-20260825T024247Z`;
- `target/terminal-bench-jobs/priority-verifier-dna-low-xhigh-audit-20260825T030304Z`;
- `target/terminal-bench-jobs/priority-verifier-dna-low-xhigh-audit-repeat-20260825T031056Z`;
- `target/terminal-bench-verifier-controls/controls-20260825T025942Z`.

## Source-level self-improvement result

On 2026-08-25, identity-bound trajectory evidence asked the self-improvement
workflow to change Foe source. The requested behavior made the built-in
terminal audit authoritative whenever the command names a completion
verifier.

The first attempt changed the expected implementation, test, integration-test,
and specification files. Its model child did not return the required typed
branch value. The runner therefore rejected the result as `no-candidate` and
set `direct_implementation_required` to true.

The runner was repaired to validate a source artifact after a blocked or
incomplete reporting outcome. A fresh source copy then repeated the
self-improvement workflow. The repeated run used ten model calls, 226,145
input tokens, 61,440 cached-input tokens, and 4,264 output tokens. It took
122.686 seconds and cost an estimated $0.688019.

The repeated run changed four files:

- `crates/cli/src/run.rs`;
- `crates/cli/src/run_test.rs`;
- `crates/cli/tests/integration.rs`;
- `docs/design.md`.

The external candidate validator accepted artifact
`sha256:e99aa7f2031b11a07703451cff34bbb8b58b1d1041b82519719a89946842c25c`
with no finding. The result classified the candidate as `source-change` and
set `direct_implementation_required` to false. The accepted source commit is
`027236a4328ca706d966fe6e368e1b06187cbf10`.

The retained results are stored under these paths:

- `target/priority-verify-audit-source-self-improvement/result.json`;
- `target/priority-verify-audit-source-self-improvement-repeat-fixed/result.json`;
- `/home/sunil/git/foe-verify-audit-self-improvement-repeat`.

This result establishes independent reproduction of the source artifact after
a failure in the self-improvement reporting path. The Terminal-Bench
validation below establishes task-quality activation and one transfer case
for the resulting behavior.

## Built-in terminal-audit candidate validation

On 2026-08-25, the source-generated built-in workflow candidate ran in two
fresh Terminal-Bench containers. The evaluated source tree was
`git-tree-sha1:443b119a93324a59a8fd5338830c30bf01e742ab`. The runtime binary
was
`sha256:26f151b998d07438a5a21a14115a14aa5109030c98c5f0ca756d8d9c413ebd25`.

The Harbor adapter invoked the binary with a bare task, `--verify`,
`--sandbox off`, and `--service-tier priority`. The built-in workflow gave 60
calls to a Sol-low implementation episode and 60 calls to a fresh Sol-high
terminal audit. The terminal audit owned `done_when.verify`.

| task | task score | model calls | input tokens | cached-input tokens | output tokens | estimated cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fix-git` | 1.0 | 15 | 102,146 | 35,328 | 5,104 | $0.383483 |
| `dna-assembly` | 1.0 | 38 | 456,532 | 210,432 | 18,279 | $1.434153 |

The `fix-git` audit independently confirmed the implementation without
editing it. The DNA implementation returned with exact melting-temperature
validation unresolved because `oligotm` was unavailable. The audit installed
`primer3`, repaired the primer design, and obtained an empty checker finding
list before completing.

Both unchanged task-owned graders awarded `1.0`. Both Foe outcomes were
completed, both checker digests were unchanged, and both conformance reports
contained no violation. Every model response reported usage. Harbor recorded
no trial exception or infrastructure failure.

The retained evidence is stored under these paths:

- `/home/sunil/git/foe-gpt2-self-improvement-candidate/target/terminal-bench-jobs/priority-built-in-verifier-fix-git-20260825T045425Z`;
- `/home/sunil/git/foe-gpt2-self-improvement-candidate/target/terminal-bench-jobs/priority-built-in-verifier-dna-assembly-20260825T045704Z`.

These runs validate the source-generated built-in workflow on one
independent-check case and one corrective-audit case. They do not satisfy the
twelve-task development gate or establish a second transferable
self-improvement.

### Additional built-in workflow results

Three later attempts used source tree
`git-tree-sha1:4858f789438011592c80a7df2bad54aa4305886e` and the same runtime
binary. The public completion checker remained available to both model
episodes. Harbor applied the unchanged task-owned verifier only after Foe
exited.

| task | task score | model calls | input tokens | cached-input tokens | output tokens | estimated cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `cancel-async-tasks` | 1.0 | 10 | 28,165 | 1,536 | 5,074 | $0.208610 |
| `large-scale-text-editing` | 1.0 | 25 | 129,659 | 46,080 | 8,713 | $0.527008 |
| `gpt2-codegolf` | 0.0 | 34 | 805,339 | 475,648 | 21,884 | $1.946703 |

The concurrency audit confirmed cancellation cleanup without changing the
implementation. The text-editing audit found that direct execution had
changed the supplied input. It restored the input and repeated the full
million-row validation before completing. Both task-owned verifiers awarded
`1.0`.

The GPT-2 attempt passed its public checker and reported an approximate
non-ASCII tokenizer as an unresolved risk. The task-owned verifier exercised
a different quoted uppercase prompt and awarded `0.0`. The public checker had
not activated the tokenizer risk. This attempt is an artifact-to-outcome
mismatch and evidence for conditional repair after an audit reports unresolved
risks.

The GPT-2 checker then gained a fourth public prompt in the same quoted
uppercase and adjacent-punctuation category. Its exact text differs from the
task-owned verifier input. A fresh untouched-workspace negative control and an
oracle control passed. The retained control report is
`target/terminal-bench-verifier-controls/controls-20260825T053601Z/verifier-controls.json`.

A rerun with the strengthened checker produced no score. During the terminal
audit, `bash` printed the trial credential into a model-visible tool result.
The run was stopped and is invalid infrastructure evidence. The public checker
source was also read, which is permitted in this open-book development lane.
The task-owned verifier remained absent during the episode.

The adapter now gives each trial an unpredictable credential path and checks
retained episode events for exact access-token or refresh-token values. A
detected value invalidates the trial without placing the value in a report.
This detection protects evaluation claims from contaminated trajectories. A
host-supplied transport remains necessary for running untrusted task content
without placing a provider credential in the task container.

The retained valid attempts are:

- `/home/sunil/git/foe-built-in-terminal-audit/target/terminal-bench-jobs/priority-built-in-verifier-cancel-async-tasks-20260825T051223Z`;
- `/home/sunil/git/foe-built-in-terminal-audit/target/terminal-bench-jobs/priority-built-in-verifier-large-scale-text-editing-20260825T051456Z`;
- `/home/sunil/git/foe-built-in-terminal-audit/target/terminal-bench-jobs/priority-built-in-verifier-gpt2-codegolf-20260825T052123Z`.

The stopped run is retained locally at
`/home/sunil/git/foe-built-in-terminal-audit/target/terminal-bench-jobs/priority-built-in-verifier-gpt2-tokenization-check-20260825T053647Z`.
It contains credential material and must remain private.

## Cost accounting

Every provider response records input, cached-input, and output tokens. Estimated cost is calculated request by request. The calculation applies the model's uncached-input, cached-input, output, and long-context rates.

The pricing manifest records the source URL with each route. As of 2026-08-23, the recorded per-million-token rates are:

| Model | Uncached input | Cached input | Output |
| --- | ---: | ---: | ---: |
| GPT-5.6 Luna | $0.20 | $0.02 | $1.20 |
| GPT-5.6 Terra | $2.00 | $0.20 | $12.00 |
| GPT-5.6 Sol | $4.00 | $0.40 | $20.00 |

The official model pages publish these rates and the long-context multipliers: [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra), and [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

Planning token estimates provide a spend preview while ordinary development
episodes run without token ceilings. Model calls and wall time are high
backstops for loops and stalled tools. Every task receives at least 60 calls
and 1,800 seconds. Eight identical calls or turns establish a loop. Diagnosis
and repair allowances are added to the implementation allowance.
`--hard-token-limits` is reserved for explicit budget-boundary tests.

## Execution sequence

1. Run deterministic capability probes in every selected task container before
   its first provider request. Scope each report to its dataset task and image.
2. Run the local micro evaluation and repository tests. Correct runtime
   contract failures before benchmark work.
3. Establish at least three reproducible development failures with Sol `low`.
   Use Sol `xhigh` only to determine whether the model can solve a failed case.
4. Add a public, read-only completion checker when a development task supports
   one. Validate each checker with an untouched-workspace negative control and
   an independently produced oracle before model spend.
5. Run the checker through `done_when.verify`, then run the resulting candidate
   against the unchanged task-owned verifier.
6. Produce typed trajectory diagnoses. Each diagnosis names its model setting,
   retained run, source identity, and runtime identity. It includes request
   growth, replayed results, final validation activity, verifier failures, and
   cited log sequences.
7. Run identity-bound self-improvement. Use Sol `low` for diagnosis and
   implementation. Use Sol `xhigh` for the independent source audit and at
   most Sol `xhigh` for conditional escalation.
8. Validate each generated candidate outside the self-improvement episode.
   Implement the diagnosed change directly when the generated candidate is
   absent, invalid, or unsupported. Repair and repeat the self-improvement
   workflow when it failed to produce the candidate.
9. Re-run each activation task with Sol `low`. Reject a candidate that does not
   improve task-verifier quality.
10. Re-run all twelve development tasks. Preserve every baseline success and
    record cost, wall time, outcome accuracy, and trace integrity.
11. Freeze the candidate source tree, binary digest, model settings, and
    acceptance rule. Run the provider-free installation check against the
    exact binary before its first assessed task.
12. Run two attempts on each of the eight confirmation tasks. Require fourteen
    successes and at least one success per task.
13. Record the confirmation decision before opening calibration trajectories.
    Run one attempt on each of the twenty calibration tasks and require
    seventeen successes.
14. Record the calibration decision before opening sealed-holdout trajectories.
    Require seven successes across the eight holdout tasks under the stated
    retry rule.
15. Reduce median estimated cost by at least 20 percent on matched successful
    tasks without reducing quality.
16. Decide whether the evidence supports a full Terminal-Bench 2.1 run.

## Recorded verifier-governed development result

On 2026-08-24, three modified Terminal-Bench scenarios tested Foe's
configured completion verifier. Every trial used GPT-5.6 Sol with low
reasoning and the default service tier. The evaluated Foe binary was
`sha256:50d99136ed988f8c4a1b4524f1274c29814aaa5635097f5c09427d7721f05644`.

Each public checker passed an untouched-workspace negative control and a
separate oracle control. The task-owned grader accepted every oracle. The
retained control report is
`target/terminal-bench-verifier-controls/controls-20260824T045804Z/verifier-controls.json`.

The first configuration used a low-reasoning implementation child followed by
a fresh high-reasoning audit child. The second configuration used one
low-reasoning episode with the same `done_when.verify` checker.

| Task | Implementation plus audit result | Implementation plus audit calls | Implementation plus audit cost | Single episode result | Single episode calls | Single episode cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `cancel-async-tasks` | 1.0 | 13 | $0.263318 | 1.0 | 5 | $0.074008 |
| `fix-git` | 1.0 | 21 | $0.475483 | 1.0 | 10 | $0.208192 |
| `large-scale-text-editing` | 1.0 | 15 | $0.246779 | 1.0 | 8 | $0.119688 |
| **Total** | **3 accepted** | **49** | **$0.985581** | **3 accepted** | **23** | **$0.401888** |

Both configurations produced three completed Foe outcomes, three conformant
traces, and three task-owned grader scores of `1.0`. The checker digest was
unchanged in every trial. The audit children changed no artifact.

The single-episode configuration used 62.0 percent fewer input tokens, 66.4
percent fewer output tokens, and 73.3 percent fewer cached-input tokens. Its
estimated cost was 59.2 percent lower. Total Harbor time fell from 627 seconds
to 280 seconds.

The text-editing case established that the completion gate changed behavior.
The first script transformed all one million rows but failed when the checker
reapplied it to the final workspace. Foe returned the finding to the same
episode. The episode made its substitutions idempotent, passed the checker,
and then passed all five task-owned tests.

The task-owned report includes `test_apply_macros_runs`, which executes the
submitted script before testing byte equality. The gate therefore prevented a
failure that the task-owned grader would exercise. This is direct evidence
for verifier-governed completion on the activated case.

The retained single-episode runs are:

- `target/terminal-bench-jobs/verifier-governed-single-episode-cancel-async-tasks-20260824T051737Z`;
- `target/terminal-bench-jobs/verifier-governed-single-episode-fix-git-20260824T051903Z`;
- `target/terminal-bench-jobs/verifier-governed-single-episode-large-scale-text-editing-20260824T051504Z`.

These three attempts establish development evidence for the mechanism. They
do not estimate accuracy across Terminal-Bench. The verifier-governed lane
retains the single-episode configuration until repeated confirmation shows a
quality benefit from a separate audit stage.

## Self-improvement contract

The self-improvement workflow has at most three model nodes. A Sol diagnosis
node reads the bounded trajectory digest and returns a typed causal
intervention. It has no source-tree inspection tool or access.

The diagnosis selects `implement-source` when the evidence activates a source
mechanism. Its branch edge controls whether a separate coding node fires. The
coding node lists `task` and `diagnose-runtime` under `follows`, which carries
both values into a clean context. The coding node returns changed paths,
validation observations, and unresolved risks as a typed handoff. A fresh
source-audit node inspects and repairs the shared candidate. Its checker owns
completion. The diagnosis selects `configure-workflow` when exactly one
independent audit setting supplies the repeated quality gain. The runner copies
that setting from the evidence without starting either coding node.

The diagnosis selects `revise-instructions` when the repeated causal
difference is procedural guidance that one instruction section of the
retained program document can carry. The typed result names the target
document, one section key, the exact old text, and the exact new text.
The diagnosis selects `define-tool` when one missing executable tool
explains the gap. The typed result names the tool, its description, its
executable content, and that content's digest. Both paths end the workflow
without starting the coding node.

The diagnosis selects `insufficient-evidence` when the contrast isolates only
model capability or requires semantic information absent from the log. Every
candidate preserves the primary model route, reasoning effort, task
allowances, token policy, service tier, and task set. Resource changes are
recorded. Verified task quality governs candidate promotion.

The digest retains the final edit and a bounded sequence of later tool results
for each episode. The collector reloads each retained task-owned verifier
artifact after Foe exits. It accepts only regular files confined to the trial
directory. The retained task identity, checksum, reward, error, and verifier
digest must agree. Task execution therefore cannot receive grader data through
the diagnostic path.

Structured verifier reports contribute counts, failure classes, and a content
digest. Each failed attempt also carries bounded assertion loci. A locus
contains its normalized location, assertion expression, concise message, and
digest. Host paths, addresses, timestamps, terminal formatting, parameter
values, and raw traceback text stay outside the self-improvement evidence.

Each attempt records total, retained, omitted, unlocated, and ambiguous
failure counts. A repeated contrast requires one unique bounded locus for
every failed test. Partial or ambiguous failure evidence cannot enter a
contrast.

The task registry admits development, capability-search, and opened
confirmation artifacts. Calibration and sealed-holdout artifacts remain
excluded. Confirmation artifacts stay protected until their candidate
disposition is recorded and the registry is updated.

A candidate-producing diagnosis selects one repeated contrast by its digest.
It must cite every failed episode, verifier-report digest, and failure-locus
digest in that contrast. It must explain each local assertion and state one
shared mechanism. Heterogeneous loci that do not support one mechanism require
an insufficient-evidence result. The diagnosis also states a falsifying
observation and the activation path under the evaluated program.

A request without provider usage invalidates exact cost and token claims. The
retained campaign record identifies each incomplete resource record. Runtime
failures, nonconformant traces, and outcome disagreements remain diagnostic
facts beside the task-owned quality score.

The coding node has Foe's standard coding tools: `read`, `grep`, `edit`, and
`bash`. Its write authority covers runtime crates, specifications, and
examples. It cannot change evaluation code, benchmark material, model
routes, reasoning settings, task allowances, token policy, or task selection.
Its verifier uses a pinned Cargo binary. The verifier runs formatting,
workspace tests, Clippy, and line-budget checks.

The evidence file names the evaluated Git tree and Foe binary digest. It
labels every trajectory with the run, token policy, service tier, and complete
execution configuration that produced it. The workflow refuses a source or
binary mismatch before making a model request. A source candidate requires a
Rust implementation change, a Rust regression test, and an affected
specification. A workflow candidate contains one independent-audit setting.
An instruction candidate contains one revision whose old text occurs exactly
once in the named section of the named program document. A tool candidate
contains one tool_defs entry with the executable's content digest, and the
executable file is retained beside the candidate. Every candidate's digest
binds its content to the evaluated source, binary, evidence, and preserved
execution controls.

The diagnosis node declares a runner-generated validator as its completion
verifier through `done_when.verify`. The validator judges the returned typed
diagnosis with the same identity-bound candidate validation the runner
applies after the episode, so an accepted workflow, instruction, or tool
candidate is an authoritative `verification/result` event in the diagnosis
episode's log, and findings return to the same episode. A source diagnosis
and a typed abstention pass the validator; a source candidate is judged by
the coding node's candidate check.

The workflow is one candidate generator. The runner validates source changes
again after the episode. It validates workflow settings, instruction
revisions, and tool definitions when the episode returns them, and validates
a workflow setting again before a benchmark run applies it. A validated
artifact survives an exhausted reporting outcome. Candidate
promotion remains an external evaluation decision. A failed artifact sets
`direct_implementation_required`. The campaign then proceeds with a direct
implementation.

The runner records every accepted candidate as a lineage transition: the
evidence bundle completed through the lineage crate's `build-bundle`
binary, whose adoption record cites the accepted verifier result, and the
parent and adoption state documents in the layout the checker's resolvers
read.
[`docs/lineage-identity.md`](../../docs/lineage-identity.md) "Harness
adoptions" states the one state-document rule: every adoption materializes
the program document that will run under it.

## Recorded capability conversion and diagnosis sufficiency

On 2026-08-24, five single-episode `gpt2-codegolf` attempts measured the
capability difference between Sol reasoning settings. Every attempt used the
default service tier and the same Foe binary:
`sha256:50d99136ed988f8c4a1b4524f1274c29814aaa5635097f5c09427d7721f05644`.

The three low-reasoning attempts received no task-owned grader credit. Each
episode completed after its local checks passed, but the submitted program
failed the hidden behavioral grader. The two xhigh-reasoning attempts both
received full task-owned grader credit.

| Configuration | Quality result | Complete resource records | Model calls | Input tokens | Cached-input tokens | Output tokens | Estimated cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Sol low, one episode | 0 of 3 | 3 | 32 | 234,848 | 108,544 | 14,250 | $0.833634 |
| Sol xhigh, one episode | 2 of 2 | 2 | 44 | 1,507,411 | 945,152 | 39,267 | $3.412437 |
| Sol low implementation followed by Sol high audit | 3 of 3 | 2 | 79 | 1,567,635 | 896,512 | 43,734 | $3.917777 |

The low-reasoning implementation in the audit workflow observed repetitive
sample output and reported unresolved checkpoint-layout risks. The fresh
high-reasoning audit inspected the tensor layout and tokenizer, repaired the
program, and passed the hidden grader. This trial shows that the audit stage
can convert an observed implementation failure into a correct artifact. A
second independent trial also received full grader credit. It used 36 model
calls and cost an estimated $1.683867. Both traces were conformant, and both
completed Foe outcomes agreed with the graders.

A third audit trial also received full task-owned grader credit. Harbor killed
the agent after 900 seconds while Foe was still inside its declared 3,600-second
workflow allowance. Harbor recorded `AgentTimeoutError`, incomplete usage, and
a nonconformant partial trace. Its task quality remains part of the result. Its
resource use is excluded from the exact totals. The runner gives Harbor an
outer timeout equal to all possible Foe stage allowances plus five minutes.

The identity-bound evidence digest contained the five single-episode
trajectories. Its evaluated source identity was
`git-tree-sha1:267cbce2b9da0d6eff3970acb335986050db0ead`. The retained file is
`target/current-source-gpt2-trajectory-evidence.json`.

Two self-improvement attempts used this evidence before the diagnosis gained
an evidence-sufficiency choice. The first attempt consumed 14 model calls and
an estimated $1.782781. The second attempt consumed 13 model calls and an
estimated $1.624444. Neither attempt changed a source file. Both coding
episodes tried to infer a runtime change from a contrast that varied reasoning
effort and model-call opportunity without exposing the failed semantic
assertion.

After the diagnosis gained an `insufficient-evidence` branch, a Luna
high-reasoning diagnosis selected that branch in one request. The request used
13,316 input tokens and 2,526 output tokens. Its estimated cost was $0.005694.
The workflow spawned no coding episode and changed no candidate file. The
diagnosis stated that the evidence isolated a model-capability difference and
did not identify an enforceable Foe mechanism.

This result validates abstention from an unsupported source candidate. It
does not satisfy the campaign requirement for a verified self-improvement.
The campaign therefore requires a direct product improvement and a new
identity-bound contrast that activates a specific Foe mechanism.

The retained self-improvement result is
`target/current-source-gpt2-self-improvement-sufficiency-run/result.json`.
The successful audit workflow is retained under
`target/terminal-bench-jobs/current-source-default-workflow-gpt2-attempt-1-20260824T060432Z`
and
`target/terminal-bench-jobs/sol-low-high-audit-gpt2-confirmation-2-20260824T062550Z`.
The excluded timeout trial is retained under
`target/terminal-bench-jobs/sol-low-high-audit-gpt2-confirmation-3-20260824T063905Z`.

## Recorded identity-bound workflow contrast

On 2026-08-24, a matched contrast used source tree
`git-tree-sha1:fec3eaa8cb39c6e005fa787aa6c46d0ce48d821e` and binary
`sha256:b2a4ba85d8858b5b3bfd860e31d345ee8d9fe06b6784075004c1a4891a54fe43`.
Every attempt used GPT-5.6 Sol with low primary reasoning, the default service
tier, measurement-only token accounting, and the same Docker task image.

The bare configuration scored zero in three attempts. All three Foe episodes
completed and produced conformant traces. Each artifact compiled and ran, but
the task-owned grader rejected its generated text. The three attempts used 31
model calls, 277,424 input tokens, 68,608 cached-input tokens, and 15,879
output tokens. Their estimated cost was $1.180287.

The workflow configuration added one fresh Sol-high independent audit with a
60-call backstop. It scored one in all three attempts. Every Foe outcome
completed, every trace conformed, and Harbor recorded no task exception. The
audit repaired checkpoint-layout defects in all three trajectories. It also
ran multiple prompts and stronger compiler or sanitizer checks after the last
edit.

One audited attempt has complete provider usage. It used 37 model calls,
571,138 input tokens, 248,320 cached-input tokens, and 19,441 output tokens.
Its estimated cost was $1.779420. Two audited attempts each contain one
provider retry without a usage record. Their quality scores remain valid, but
their exact token and cost totals are unknown.

The retained runs are:

- `target/terminal-bench-jobs/workflow-contrast-bare-sol-low-20260824T072501Z`;
- `target/terminal-bench-jobs/workflow-contrast-independent-audit-20260824T074142Z`.

The bounded self-improvement input contains the three bare failures and two
audited successes. The remaining audited trajectory stays in the raw archive.
The identity-bound digest is
`target/gpt2-workflow-contrast-trajectory-evidence.json`.

## Recorded workflow self-improvement and candidate validation

On 2026-08-24, Foe read the identity-bound workflow contrast and selected a
general independent audit after implementation. One GPT-5.6 Luna request with
high reasoning produced the workflow candidate. The request used 20,144 input
tokens and 1,686 output tokens. It took 31.632 seconds and cost an estimated
$0.006052.

The candidate retained GPT-5.6 Sol with low reasoning for implementation. It
added a fresh Sol-high audit with a 60-call backstop. The candidate preserved
the default service tier and measurement-only token policy.

The candidate is
`sha256:1ce8dd0b3c1ce3f16e305d20e9d27848b1c8212d9636dccfc7aa7cfdc4da233a`.
It binds source tree
`git-tree-sha1:fec3eaa8cb39c6e005fa787aa6c46d0ce48d821e`, runtime binary
`sha256:b2a4ba85d8858b5b3bfd860e31d345ee8d9fe06b6784075004c1a4891a54fe43`,
and the evidence digest. The external candidate validator accepted it without
findings. The self-improvement workflow required no direct source change.

The generated candidate and its episode are under
`target/gpt2-workflow-self-improvement-generated-candidate/`. The candidate
was then applied to six development tasks without changing its bound model or
workflow settings.

| Development task | Official score | Model calls | Harbor time | Estimated cost |
| --- | ---: | ---: | ---: | ---: |
| `cancel-async-tasks` | 1.0 | 10 | 141 seconds | $0.216334 |
| `git-multibranch` | 1.0 | 31 | 423 seconds | $1.089766 |
| `fix-git` | 1.0 | 17 | 185 seconds | $0.503534 |
| `sqlite-db-truncate` | 1.0 | 11 | 164 seconds | $0.318202 |
| `sanitize-git-repo` | 1.0 | 20 | 357 seconds | incomplete |
| `large-scale-text-editing` | 1.0 | 19 | 277 seconds | $0.425199 |

All six task-owned graders accepted the candidate. Every Foe outcome was
completed, and every trace conformed. One `sanitize-git-repo` request lacked
provider usage. The other five tasks recorded at least 618,823 input tokens,
207,360 cached-input tokens, 41,212 output tokens, and $2.553036 in estimated
cost. The full development run took 1,547 seconds.

The first three confirmation tasks produced six accepted attempts. The
provider-policy substitution described above then produced two accepted
`path-tracing` attempts.

| Confirmation task | Accepted attempts | Model calls | Input tokens | Cached-input tokens | Output tokens | Harbor time | Estimated cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `build-cython-ext` | 2 | 111 | 3,023,186 | 2,034,688 | 36,657 | 1,353 seconds | $5.501007 |
| `constraints-scheduling` | 2 | 19 | 89,709 | 11,264 | 13,184 | 336 seconds | $0.581966 |
| `custom-memory-heap-crash` | 2 | 48 | 442,191 | 103,424 | 15,656 | 662 seconds | $1.709558 |
| `path-tracing` | 2 | 76 | 1,229,682 | 748,032 | 34,184 | 1,181 seconds | $2.909493 |
| **Total** | **8** | **254** | **4,784,768** | **2,897,408** | **99,681** | **3,532 seconds** | **$10.702023** |

All eight task-owned graders accepted the candidate. Harbor recorded no task
exception. Every Foe outcome was completed, every trace conformed, and every
provider response reported usage. The result passes the campaign's
confirmation gate with every selected task represented.

The retained development run is
`/home/sunil/git/foe-workflow-configuration-self-improvement/target/terminal-bench-jobs/workflow-candidate-development-20260824T083844Z`.
The retained confirmation runs are
`/home/sunil/git/foe-workflow-configuration-self-improvement/target/terminal-bench-jobs/workflow-candidate-confirmation-20260824T092334Z`
and
`/home/sunil/git/foe-workflow-configuration-self-improvement/target/terminal-bench-jobs/workflow-candidate-confirmation-path-tracing-20260824T101914Z`.

## Recorded calibration result

On 2026-08-24, the frozen workflow candidate ran once on each of the twelve
calibration tasks. Every untouched task-owned grader awarded a score of `1.0`.
Harbor recorded no task exception, and all twelve Foe traces conformed.

| Calibration task | Model calls | Harbor time | Estimated cost |
| --- | ---: | ---: | ---: |
| `adaptive-rejection-sampler` | 28 | 682 seconds | $1.878429 |
| `break-filter-js-from-html` | 11 | 170 seconds | incomplete |
| `compile-compcert` | 36 | 1,484 seconds | $3.075176 |
| `db-wal-recovery` | 11 | 125 seconds | $0.204456 |
| `distribution-search` | 12 | 141 seconds | $0.240216 |
| `financial-document-processor` | 32 | 354 seconds | $1.263138 |
| `git-leak-recovery` | 10 | 175 seconds | $0.263158 |
| `make-mips-interpreter` | 53 | 750 seconds | $4.133114 |
| `query-optimize` | 29 | 1,183 seconds | $1.142188 |
| `reshard-c4-data` | 30 | 987 seconds | $1.532546 |
| `schemelike-metacircular-eval` | 35 | 1,110 seconds | $2.457325 |
| `train-fasttext` | 37 | 2,619 seconds | $1.326511 |

The run used 324 model calls and took 9,780 seconds. Eleven tasks reported at
least 7,460,679 input tokens, 4,393,472 cached-input tokens, 174,502 output
tokens, and $17.516257 in estimated cost. Seven `break-filter-js-from-html`
calls lacked provider usage, so complete resource totals are unavailable.

Eleven Foe outcomes were completed. `break-filter-js-from-html` produced the
required artifact before five request attempts failed at step 6. Its untouched
grader accepted the artifact, while Foe reported `blocked`. This outcome
disagreement remains a product diagnostic and does not alter the official
quality score.

The calibration result is twelve accepted tasks from twelve attempts. It
exceeds the required ten accepted tasks and qualifies the frozen candidate for
the sealed calibration holdout. No calibration trajectory or grader output
enters the candidate before holdout.

The retained calibration run is
`/home/sunil/git/foe-workflow-configuration-self-improvement/target/terminal-bench-jobs/workflow-candidate-calibration-20260824T104111Z`.

## Recorded calibration-holdout result

On 2026-08-24, the frozen workflow candidate ran once on each of the six
sealed calibration-holdout tasks. Five untouched task-owned graders awarded a
score of `1.0`. Harbor recorded no task exception, and all six Foe traces
conformed.

| Calibration-holdout task | Official score | Model calls | Harbor time | Estimated cost |
| --- | ---: | ---: | ---: | ---: |
| `circuit-fibsqrt` | 1.0 | 15 | 436 seconds | $0.640790 |
| `cobol-modernization` | 1.0 | 34 | 495 seconds | $1.662385 |
| `extract-elf` | 0.0 | 12 | 249 seconds | $0.457190 |
| `hf-model-inference` | 1.0 | 18 | 407 seconds | $0.568374 |
| `mcmc-sampling-stan` | 1.0 | 25 | 808 seconds | $1.502595 |
| `sparql-university` | 1.0 | 11 | 145 seconds | $0.358942 |
| **Total** | **5.0** | **115** | **2,540 seconds** | **$5.190275** |

Every provider response reported usage. The run used 1,704,770 input tokens,
787,968 cached-input tokens, and 60,394 output tokens. Every Foe outcome was
completed. Five outcomes agreed with the task-owned grader.

The `extract-elf` implementation and audit calculated 548 returned words from
700 words in the supplied ELF image. The audit treated this calculation as
evidence that coverage exceeded 75 percent. It generated no second valid ELF
fixture. The hidden grader generated another ELF and found none of its
reference addresses in the submitted output. One of two task-owned tests
passed.

This failure identifies a transfer-validation gap in the audit contract. The
audit verified its own interpretation against one supplied artifact. It did
not test whether the program interface generalized to a materially different
valid input. The post-holdout audit instruction now requires two valid inputs
and generation of a second fixture when the workspace supplies only one. That
instruction has not received a provider-backed quality evaluation.

The calibration-holdout result passes the required five accepted tasks from
six attempts. Across development, confirmation, calibration, and
calibration-holdout evaluation, the self-improvement workflow candidate
received 31 task-owned successes in 32 attempts over 28 distinct tasks. This
aggregate is a candidate result rather than a full Terminal-Bench estimate.

The retained calibration-holdout run is
`/home/sunil/git/foe-workflow-configuration-self-improvement/target/terminal-bench-jobs/workflow-candidate-calibration-holdout-20260824T132632Z`.

The campaign has met its development, confirmation, calibration, and
calibration-holdout quality gates. A full 89-task run remains outside this
campaign and requires a separate decision.

## Recorded self-improvement failure analysis

On 2026-08-23, two retained `gpt2-codegolf` self-improvement attempts failed
to produce a valid candidate. The first attempt used four diagnosis calls,
155,911 input tokens, and an estimated $0.167263. Its diagnosis child returned
20 tool results and exhausted before returning the required typed value. It
changed no files.

The second attempt used 24 calls, 1,192,041 input tokens, and an estimated
$0.642229. Its diagnosis child completed after eight calls. Its implementation
child used all 16 remaining calls, changed five implementation files, changed
no test or specification, and never called the candidate checker. The child
reported that Cargo was unavailable in its sandbox. The generated files did
not form a valid candidate.

The retained evidence file placed model and reasoning labels in a separate
run summary. Each diagnosis entry lacked those labels. The diagnosis model
therefore could not associate three Sol `low` failures with three Sol `xhigh`
successes. Reconstructing the association from the retained run summaries
showed zero verified successes in three Sol `low` attempts and three verified
successes in three Sol `xhigh` attempts.

These observations establish three runner defects. The evidence handoff lost
the causal contrast between model settings. The coding child lacked a usable
Rust validation environment. The structural checker could accept files
without compiling or testing them. The self-improvement contract above
addresses each defect before another provider-backed attempt.

## Recorded deterministic finding

On 2026-08-23, the capability probe ran in the pinned `fix-git` task container without a provider request. Foe had a standard executable path, task working directory, package manager, large-file grep, windowed read, and enforced tool timeouts.

The container had no Python interpreter. The portable probe transport therefore uses POSIX shell. A background process did not survive across Foe `bash` calls. Standard input was not a terminal. The image had no available loopback probe utility, so loopback support remains unmeasured.

The first probe assessment accepted any reported working directory. Inspection showed that the adapter placed `/` first in the read grants. Relative tool paths therefore resolved from `/` rather than the task image's working directory.

The adapter now queries the container's effective working directory before it writes the Foe program. That directory is the first read root. The corrected probe compares the observed `bash` directory with the recorded first read root.

The retained local report is under `target/terminal-bench-capability-probes/`. Raw jobs and credentials remain outside Git.

On 2026-08-24, the same probe ran in the pinned `gpt2-codegolf` image. The working directory, workspace writes, package authority, large-file operations, and timeouts passed. The standard executable-path check failed because the image did not provide both `git` and `sh` through its path. This contrast proves that a capability report cannot be generalized across task images.

## Recorded built-in verifier workflow result

On 2026-08-25, five modified development scenarios evaluated one source tree
and portable binary. The source tree identity was
`git-tree-sha1:0f7c20a852a691ff21bcd368e2052a519e39ae8c`. The binary digest was
`sha256:08a685d0528b531afff71b752c502dbffe8f0e74488ad9304c5da8afa72464a6`.
Every model request used GPT-5.6 Sol and the standard service tier.

The built-in workflow gave the task to a low-reasoning implementation child.
A fresh high-reasoning audit child then owned `done_when.verify`. Both children
could read the public completion checker. The unchanged task-owned grader ran
after Foe exited and remained unavailable to both children.

| Task | Task-owned score | Model calls | Input tokens | Cached-input tokens | Output tokens | Harbor time | Estimated cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `cancel-async-tasks` | 1.0 | 9 | 22,947 | 3,584 | 3,517 | 119 seconds | $0.149226 |
| `dna-assembly` | 1.0 | 46 | 957,734 | 668,160 | 25,022 | 721 seconds | $1.926000 |
| `fix-git` | 1.0 | 15 | 77,558 | 7,168 | 4,146 | 133 seconds | $0.367347 |
| `gpt2-codegolf` | 1.0 | 27 | 315,756 | 100,864 | 14,354 | 495 seconds | $1.186994 |
| `large-scale-text-editing` | 1.0 | 13 | 43,453 | 4,096 | 3,952 | 205 seconds | $0.238106 |
| **Total** | **5.0** | **110** | **1,417,448** | **783,872** | **50,991** | **1,673 seconds** | **$3.867673** |

Every Foe outcome was completed. Every trace conformed with zero violations.
Every public checker retained its recorded digest. The credential scanner
found no provider credential in an episode log. Every model call reported
usage.

The `gpt2-codegolf` checker included quoted uppercase-token cases and tensor
layout probes. A prior low-reasoning implementation passed a weaker public
checker and received zero task-owned credit. The workflow result therefore
establishes one reproducible harness-limited failure conversion. The other
four cases establish transfer across process cleanup, Git recovery, sequence
design, and bulk editing. Evidence for three converted baseline failures
remains an open campaign criterion.

Three attempts were excluded from quality counts. Two reached a provider
response whose code was `service_unavailable_error`. The OpenAI response
decoder treated that code as permanent, so each episode ended before a usable
quality observation. The decoder now classifies that code as transient under
the existing bounded retry mechanism. Its exact wire event is a regression
fixture.

The third excluded attempt made no model request. The Harbor installer called
the removed `foe schema` command after the command-line interface consolidated
schema output under `foe plan --schema`. The installer now uses the supported
form. A provider-free installation trial passed in the pinned task container
before the five scored runs.

The identity-bound evidence file is
`/home/sunil/git/foe-terminal-audit-transient-retry/target/standard-built-in-verifier-five-case-evidence.json`.
It contains five typed trajectory diagnoses. Completed outcomes retain their
typed status and omit repeated model-authored completion prose. This keeps the
complete evidence set inside the 64 KiB canonical encoding limit.

The retained scored runs are under
`/home/sunil/git/foe-terminal-audit-transient-retry/target/terminal-bench-jobs/`.
Their labels begin with `standard-built-in-verifier-` and end with
`transient-retry`. Raw jobs, logs, and credentials remain outside Git.

## Recorded live-environment completion conversion

On 2026-08-25, the built-in workflow failed `git-multibranch` in two
closed-book attempts. Both Foe outcomes were completed, both traces conformed,
and the unchanged task-owned grader awarded zero credit.

The evaluated source tree was
`git-tree-sha1:0f7c20a852a691ff21bcd368e2052a519e39ae8c`. The portable binary
was `sha256:08a685d0528b531afff71b752c502dbffe8f0e74488ad9304c5da8afa72464a6`.
Every provider request used the standard service tier.

The first attempt used 29 model calls and cost an estimated $1.067835. The
second used 21 model calls and cost an estimated $0.613216. Both workflows
authored container configuration and exercised temporary SSH, Git, and HTTPS
probes. Neither workflow left the required services and repositories available
to the post-run grader.

The public `git-multibranch` completion checker ran against the same binary.
That open-book attempt received full task-owned credit, used 23 model calls,
and cost an estimated $0.749144. The result isolated live machine state as the
quality mechanism. It did not reveal the task-owned grader to either model
episode.

An identity-bound evidence digest combined the two closed-book failures, the
public-checker success, and successful `fix-git` and
`path-tracing-reverse` trajectories. The file is
`/home/sunil/git/foe-terminal-audit-transient-retry/target/git-multibranch-verifier-contrast-evidence.json`.

The source self-improvement workflow used Luna with low reasoning for diagnosis
and Sol with low reasoning for implementation. It selected a source change and
produced implementation, Rust test, and specification edits. The run used 17
model calls, 387,183 input tokens, 221,696 cached-input tokens, and 3,429 output
tokens. Its estimated cost was $0.722055.

External validation rejected the autonomous candidate. Its Rust test did not
compile, and its proposed `session` tool mechanism could not preserve services
after episode settlement. The in-episode checker also ran a loopback listener
test under Landlock, which produced an unrelated denial. The checker omits
listener tests during the confined candidate check. Full post-episode
validation still runs the complete workspace suite.

A second identity-bound attempt reached its in-episode completion gate after
the listener exclusion. The coding child again added `session` to the built-in
tool surface. The confined check accepted the candidate because it excluded
the entire command-line package. External validation then failed the existing
unit test that requires the four-tool built-in surface. The attempt used 10
model calls and cost an estimated $0.820957. It produced no accepted
self-improvement.

The confined checker now runs the command-line unit tests separately and skips
only the login module that binds loopback listeners. A source candidate that
changes built-in workflow behavior must therefore preserve the remaining
command-line invariants before its coding child can complete. Post-episode
validation remains the final candidate authority.

A third identity-bound attempt exercised the command-line completion gate. The
gate returned compiler and workflow-ceiling findings to the coding child. Two
correction requests repaired the compiler error but left the workflow ceiling
invalid. Foe ended blocked with `verification-unsatisfiable`, and external
validation agreed that the candidate was invalid. The attempt used 16 model
calls, 515,272 input tokens, 283,648 cached-input tokens, and 3,778 output
tokens. Its estimated cost was $1.019905. The retained episode is
`/home/sunil/git/foe-live-state-source-candidate-cli-gate/target/live-state-source-candidate-cli-gate-run-2/episode`.

Source self-improvement now separates implementation from acceptance. The
implementation child returns a typed candidate handoff. A fresh source-audit
child inspects the diff, source ownership, tests, specifications, and resource
lifecycle. The audit uses Sol with xhigh reasoning because source modification
activates the campaign's conditional escalation rule. Its checker owns
completion and has four correction attempts. This structure prevents the
implementation child from certifying its own architectural hypothesis.

The first independently audited source candidate passed repository validation.
It gave the built-in audit a `session` tool and promised to retain services
through in-episode verification. The implementation handoff identified cleanup
and external environment teardown as unresolved risks, but the low-reasoning
source audit accepted the candidate without repair. The source workflow used
24 model calls and cost an estimated $1.164434.

The unchanged closed-book `git-multibranch` task rejected that candidate. The
audit started SSH and HTTPS services, validated them successfully, and stopped
both sessions before returning. The task-owned grader then failed
`test_multi_branch_https_deploy`. The attempt used 21 model calls and cost an
estimated $0.596874. Its Foe outcome was completed and its trace conformed. The
retained task run is
`/home/sunil/git/foe-live-state-source-candidate-independent-audit/target/terminal-bench-jobs/standard-autonomous-source-audit-live-state-git-multibranch-20260825T095536Z`.

Repository validity therefore did not establish task-quality improvement. The
candidate remains rejected. The source-audit stage now uses the allowed xhigh
reasoning escalation when a diagnosis selects a source change.

The source candidate audited with xhigh reasoning preserved the four-tool
built-in surface and strengthened the terminal-audit instruction. Its source
workflow used 20 model calls and cost an estimated $1.414493. Repository
validation accepted the artifact. The unchanged closed-book task awarded zero
credit because the audit validated temporary substituted paths and left no
task-visible service running. That task attempt used 15 model calls and cost an
estimated $0.514239.

The same candidate then ran once with the public completion checker. That
attempt launched the actual task entrypoint, observed SSH and Nginx processes
reparented to PID 1, and received full task-owned credit. It used 28 model calls
and cost an estimated $0.685828. The collector combined the failed closed-book
and successful verifier-governed trajectories under source tree
`git-tree-sha1:f59396ab8951576779a2d004b8581614c1c89178` and runtime binary
`sha256:f1acce966eeef2b489578ee33833e86255333911cf8e48cfae16d426fa80404d`.
The identity-bound feedback is
`/home/sunil/git/foe-live-state-source-candidate-xhigh-audit/target/live-state-generation-feedback.json`.

A source generation informed by those trajectories identified the executor
invariant that a completed `bash` call kills its process group. The candidate
therefore requires a task-provided or environment-owned lifecycle that survives
the call. It also requires task-visible state to remain available at workflow
completion for an external observer. The source workflow used 37 model calls,
2,857,018 input tokens, 1,889,792 cached-input tokens, and 17,581 output tokens.
It cost an estimated $4.929350.

The generated source artifact was
`sha256:000bf2c72fa1f21ea226e2bc7c4bf414d0a49f84a494f2a6a017d5c11374b94c`.
Its frozen source tree was
`git-tree-sha1:002999637ff48568b130f33ef353af9898c3486f`, and its portable
binary was
`sha256:b0d89609b3072cd8a0d01c51694f0b3a4a077b02544c82043eba8c89a3de776b`.
The unchanged closed-book `git-multibranch` task awarded full credit in two
attempts. The attempts used 98,868 and 141,122 input tokens. Their estimated
costs were $0.524993 and $0.658365. The unrelated `fix-git` transfer case also
awarded full credit. It used 89,962 input tokens and cost an estimated
$0.400402. Every attempt completed without an exception.

This evidence establishes one transferable autonomous source improvement. The
feedback loop needed two source generations because repository validation
could not observe post-settlement task quality. The generated source patch
overlaps the smaller direct candidate, so source review should select one
implementation rather than merge both.

The direct source candidate adds one general rule to both built-in model roles.
A task that requires a service or live machine state must apply its
configuration, exercise the public interface, and leave the required state
available at completion. An image definition, unapplied configuration, or
stopped temporary probe does not satisfy that contract.

The corrected candidate used source tree
`git-tree-sha1:a9e148bd22e43566b392f9f3099a7e2a91b4e13b` and portable binary
`sha256:76205f53b18ac2e07018fa3f092790159f917470d8e39334bf953313609cdca8`.
It received full task-owned credit in two closed-book `git-multibranch`
attempts. The attempts used 22 and 23 model calls and cost an estimated
$0.747664 and $0.760219.

The same candidate preserved full task-owned credit on `fix-git` and
`path-tracing-reverse`. The `fix-git` attempt used 18 model calls and cost an
estimated $0.491566. One `path-tracing-reverse` request lacked provider usage,
so its exact token and cost totals are unavailable. Every candidate outcome
completed, every trace conformed, and no credential appeared in a retained
episode.

Current `main` then received unrelated Python-tool and lineage changes. A local
integration tree combined those changes, the direct source candidate, and the
open built-in workflow dependencies. Its source identity was
`git-tree-sha1:427c272e2994a726128e1580e76caae801f497fc`. Its portable binary
was `sha256:3ac9fdf8dd20c67a0ee913df63db0fd6bacaccc154907cfea4159d194f50d077`.

The integration binary passed an installation-only compatibility check without
a model request. It then received full closed-book `git-multibranch` credit in
one attempt. The attempt used 22 model calls, 133,676 input tokens, 26,624
cached-input tokens, and 9,965 output tokens. Its estimated cost was $0.638158,
and its trace conformed with zero violations.

The live-environment source change therefore converts one reproducible
harness-limited failure and transfers across unrelated task families. Both the
direct implementation and the artifact produced from identity-bound feedback
satisfy the task-quality criterion. The smaller direct implementation remains
the preferred source-review candidate.

The retained candidate runs are under
`/home/sunil/git/foe-live-environment-self-improvement/target/terminal-bench-jobs/`.
The autonomous quality-confirmation runs are under
`/home/sunil/git/foe-live-state-generation-feedback-candidate/target/terminal-bench-jobs/`.
The current-main integration run is under
`/home/sunil/git/foe-live-state-evaluation/target/terminal-bench-jobs/standard-current-main-integration-live-state-git-multibranch-20260825T092540Z`.

## Recorded frozen-candidate validation

On 2026-08-25, a local integration candidate combined the campaign branch
with the live-environment completion rule and its open runtime dependencies.
The source tree identity was
`git-tree-sha1:2fbeddc6173fe764824960931884815a37dd443a`. The portable binary
was
`sha256:f3d38553a3b3766bf928b1b4686cc802b532ab3691aff52459160427275d28cb`.
The dependency set provided the built-in terminal-audit workflow, selected
sandbox mode, selected service tier, provider-specific credentials, and
bounded retry for unavailable provider responses.

An earlier local candidate omitted the built-in terminal-audit dependency.
Its assessed `cancel-async-tasks` invocation failed during installation because
the binary lacked the required `--service-tier` and `--sandbox` options. The
failure occurred before any provider request. It is excluded from task-quality
counts and recorded as an avoidable campaign-process failure.

The provider-free installation check had existed as a separate target. The
failed invocation showed that an optional check could be skipped while
assembling a local candidate from open changes. The execution sequence now
requires the exact frozen binary to pass that check before any assessed task.

The corrected binary passed the installation check in the pinned `fix-git`
container. The check made no provider request. Its retained job is
`/home/sunil/git/foe-frozen-standard-quality-candidate/target/terminal-bench-jobs/standard-frozen-candidate-install-check-20260825T105934Z`.

The corrected binary then received full task-owned credit on the
verifier-governed `cancel-async-tasks` case. GPT-5.6 Sol used low reasoning and
the standard service tier. The attempt used 13 model calls, 39,832 input
tokens, 3,072 cached-input tokens, and 4,779 output tokens. Its estimated cost
was $0.243849.

The implementation child used seven calls. The independent terminal-audit
child used six calls and ran the public completion checker. The unchanged
task-owned verifier awarded 1.0 after Foe exited. The Foe outcome was completed,
the credential scan passed, and every provider response reported usage.

Trace conformance passed with zero violations. The report contains 27 passing
declared-authority assertions, 29 passing hierarchical-budget assertions,
7,793 passing reconstructable-evidence assertions, 11 passing typed-outcome
assertions, and 12 passing workflow-provenance assertions. Landlock was off
inside the task container as required by the campaign configuration.

The retained assessed job is
`/home/sunil/git/foe-frozen-standard-quality-candidate/target/terminal-bench-jobs/standard-frozen-candidate-validated-verifier-cancel-async-tasks-20260825T105954Z`.

The same frozen binary then ran all six verifier-governed development cases.
Every case used GPT-5.6 Sol with low reasoning and the standard service tier.
Every unchanged task-owned verifier awarded full credit.

| Task | Score | Calls | Implementation calls | Audit calls | Input tokens | Cached-input tokens | Output tokens | Estimated cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `cancel-async-tasks` | 1.0 | 13 | 7 | 6 | 39,832 | 3,072 | 4,779 | $0.243849 |
| `dna-assembly` | 1.0 | 40 | 11 | 29 | 619,123 | 387,072 | 21,001 | $1.503053 |
| `fix-git` | 1.0 | 18 | 12 | 6 | 114,712 | 28,160 | 4,597 | $0.449412 |
| `git-multibranch` | 1.0 | 30 | 12 | 18 | 331,114 | 174,592 | 17,219 | $1.040305 |
| `gpt2-codegolf` | 1.0 | 35 | 15 | 20 | at least 599,164 | at least 319,488 | at least 19,649 | incomplete |
| `large-scale-text-editing` | 1.0 | 12 | 8 | 4 | 42,736 | 11,776 | 3,847 | $0.205490 |

One `gpt2-codegolf` provider response omitted usage. The retained diagnostics
contain the measured totals from the other 34 calls. The task score, outcome,
and trace remain complete.

Every Foe outcome was completed. Every conformance report was valid with zero
violations. The `git-multibranch` trace records five denied capability calls
that the workflow recovered from. The denials did not prevent task completion,
but cross-trajectory analysis must classify them before the protected sets are
opened.

The modified development subset therefore preserves all six previously
established successes for the exact frozen candidate. The result also repeats
the live-environment activation success and the behavioral GPT-2 correction.
It does not replace the required twelve-task closed-book development run.

The retained job labels begin with
`standard-frozen-candidate-validated-verifier-` under
`/home/sunil/git/foe-frozen-standard-quality-candidate/target/terminal-bench-jobs/`.

## Recorded twelve-task closed-book development result

The same frozen source tree and binary ran every development task once without
a public completion checker. Every request used GPT-5.6 Sol, low primary
reasoning, the standard service tier, and measurement-only token accounting.
The built-in workflow gave its independent audit the runtime-owned high
reasoning default. The task-owned verifier remained unavailable until Foe
exited.

| Task | Score | Calls | Input tokens | Cached-input tokens | Output tokens | Estimated cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `cancel-async-tasks` | 1.0 | 9 | 23,365 | 4,096 | 4,150 | $0.161714 |
| `dna-assembly` | 0.0 | 26 | 295,687 | 143,360 | 17,732 | $1.021292 |
| `fix-git` | 1.0 | 17 | 94,202 | 22,016 | 5,523 | $0.408010 |
| `fix-ocaml-gc` | 1.0 | 26 | 780,718 | 394,240 | 5,452 | $1.812648 |
| `git-multibranch` | 1.0 | 25 | 191,425 | 64,000 | 13,519 | $0.805680 |
| `gpt2-codegolf` | 1.0 | 26 | 292,566 | 91,136 | 18,113 | $1.204434 |
| `large-scale-text-editing` | 1.0 | 12 | 51,161 | 8,192 | 6,733 | $0.309813 |
| `model-extraction-relu-logits` | 1.0 | 10 | 41,922 | 7,680 | 4,876 | $0.237560 |
| `path-tracing-reverse` | 1.0 | 26 | 897,884 | 483,328 | 10,806 | $2.067675 |
| `regex-chess` | 1.0 | 44 | 855,321 | 500,736 | 22,431 | $2.067254 |
| `sanitize-git-repo` | 1.0 | 19 | 337,300 | 124,928 | 9,028 | $1.080019 |
| `sqlite-db-truncate` | 1.0 | 12 | 55,996 | 7,168 | 5,926 | $0.316699 |
| **Total** | **11.0** | **252** | **3,917,547** | **1,850,880** | **124,289** | **$11.492800** |

The result is 91.7 percent task quality. Every attempt produced a gradable
artifact without an infrastructure exception. Every Foe trace conformed with
zero violations. The unchanged `git-multibranch` success confirms that the
live-environment completion rule transfers into the frozen integration
candidate. The unchanged GPT-2 success confirms behavioral correction without
the public checker.

The DNA attempt completed, but the task-owned verifier rejected its artifact.
The audit treated a fixed suffix of each primer as its annealing tract. The
task semantics also count matching bases from the four-base assembly overhang.
For the EGFP pair, the audit reported a 2.933593 degree difference. The
task-owned verifier measured the complete tracts and found a 5.813507 degree
difference, beyond the permitted five degrees. This attempt is an
artifact-outcome mismatch and the only development quality failure.

The retained jobs are the six directories whose names begin with
`standard-frozen-candidate-closed-book-` under
`/home/sunil/git/foe-frozen-standard-quality-candidate/target/terminal-bench-jobs/`.

## Recorded task-derived audit source candidate

An identity-bound evidence file combined the failed closed-book DNA attempt,
the successful verifier-governed DNA attempt, and a successful closed-book
GPT-2 control. It bound source tree
`git-tree-sha1:2fbeddc6173fe764824960931884815a37dd443a` and runtime binary
`sha256:f3d38553a3b3766bf928b1b4686cc802b532ab3691aff52459160427275d28cb`.
The evidence file is
`/home/sunil/git/foe-frozen-standard-quality-candidate/target/frozen-candidate-dna-verifier-contrast-evidence.json`.

The first generated self-improvement program failed configuration validation
before any provider request. Its diagnosis node declared the
`validate-candidate` executable, while the containing program omitted that
tool from its ceiling. Commit `b5124d7` declares the same tool and executable
definition at both levels. The unit suite and a provider-free generated-program
validation cover the corrected ceiling.

The corrected workflow used Luna low for bounded diagnosis, Sol low for source
implementation, and Sol xhigh for independent source audit. It used 26 model
calls, 898,645 input tokens, 411,648 cached-input tokens, and 11,330 output
tokens. Its estimated cost was $2.195093. The independent candidate checker
accepted the source, adjacent Rust regression test, and design specification.
The lineage evidence address is
`sha256:00729319538df3534d75465255cc9de8ec6ea192fc1d03b2ac1d601289b74ad2`.

The source candidate requires the terminal audit to derive and execute a
task-based semantic checklist. Its commit is `26b4aae`, its source tree is
`git-tree-sha1:ce059a3aa62a9d34293bacd385285be6c98276ff`, and its portable
binary is
`sha256:d85dcc6dbe6ce33b480bf114bbef0e2def0b0008ec5f36233735e5a7faac97ee`.
The binary passed the provider-free Harbor installation check.

The unchanged closed-book DNA activation attempt received zero task-owned
credit. It used 30 model calls, 442,937 input tokens, 208,384 cached-input
tokens, and 25,561 output tokens. Its estimated cost was $1.532786. Foe
completed with a conforming trace and no infrastructure exception.

The audit installed the exact `oligotm` executable and repaired all eight
primers. Its validator still measured only the explicit binding arm. For the
vector pair, the audit reported a 2.994163 degree difference. The task-owned
verifier included matching overhang suffix bases in each complete annealed
tract and measured a 6.169264 degree difference. The source candidate therefore
does not improve task quality and is rejected from promotion.

The self-improvement episode is retained at
`/home/sunil/git/foe-dna-audit-instruction-improvement/target/dna-audit-source-improvement-standard-tier/`.
The closed-book activation job is retained at
`/home/sunil/git/foe-dna-audit-instruction-improvement/target/terminal-bench-jobs/standard-self-improved-dna-audit-closed-book-first-20260825T131533Z/`.

## Recorded confirmation capability probes

The exact frozen binary ran deterministic capability probes in each of the
eight confirmation containers before the first assessed attempt. The probes
made no provider request. All eight reports completed successfully.

Every container permitted package installation, workspace writes, a
one-million-line file write, a bounded large-file read, and a large-file grep.
Every container started in the task working directory. Each container enforced
the requested tool timeout. Five minimal containers lacked the usual standard
path entries, so the adapter continued to provide absolute executable paths.

No container supplied an interactive terminal. A background process started
by one `bash` call did not survive that call. The probe image lacked the
executable needed for a loopback connection test, so loopback support remains
unmeasured. These results describe the task environment and Foe executor. They
do not expose any task instruction or verifier rule.

The first probe invocation used `foe schema`, which the current command line
interpreted as a task. It failed before any provider request because no model
was configured. The capability adapter and assessed adapter had duplicated the
schema command. Commit `7be3e64` gives both adapters one tested helper that
uses `foe plan --schema`. All eight successful probes used the corrected
helper against the unchanged frozen binary.

The retained reports are the directories dated from `20260825T133141Z` through
`20260825T133452Z` under
`/home/sunil/git/foe-frozen-standard-quality-candidate/target/terminal-bench-capability-probes/`.

## Recorded confirmation first pass

The frozen source tree and binary ran once on each confirmation task. Every
request used GPT-5.6 Sol, low primary reasoning, the standard service tier,
and measurement-only token accounting. The built-in workflow gave its fresh
terminal audit the runtime-owned high reasoning setting. The task-owned
verifier remained unavailable until Foe exited.

| Task | Score | Calls | Input tokens | Cached-input tokens | Output tokens | Estimated cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `build-pov-ray` | 1.0 | 24 | 389,438 | 165,376 | 8,586 | $1.134118 |
| `caffe-cifar-10` | 1.0 | 38 | 1,846,823 | 1,288,192 | 9,312 | $2.936041 |
| `configure-git-webserver` | 1.0 | 21 | 251,437 | 82,432 | 11,541 | $0.939813 |
| `count-dataset-tokens` | 1.0 | 17 | 110,888 | 20,992 | 5,504 | $0.478061 |
| `crack-7z-hash` | 1.0 | 27 | 348,474 | 171,520 | 5,591 | $0.888244 |
| `dna-insert` | 0.0 | 17 | 134,525 | 39,424 | 7,049 | $0.537154 |
| `log-summary-date-ranges` | 1.0 | 9 | 117,995 | 63,488 | 4,060 | $0.324623 |
| `overfull-hbox` | 1.0 | 12 | 87,858 | 15,872 | 4,092 | $0.376133 |
| **Total** | **7.0** | **165** | **3,287,438** | **1,847,296** | **55,735** | **$7.614186** |

Every attempt completed without an infrastructure exception. Every Foe
outcome was completed, and every trace conformed with zero violations. The
first pass therefore records seven successes from eight protected tasks.

The candidate remains unchanged for the second confirmation attempt. Its
acceptance rule requires at least fourteen successes from sixteen attempts,
with every task succeeding at least once. First-pass trajectories remain
excluded from self-improvement evidence until the second pass and candidate
disposition are recorded.

The retained jobs are under
`/home/sunil/git/foe-frozen-standard-quality-candidate/target/terminal-bench-jobs/standard-frozen-candidate-confirmation-first-pass-20260825T133543Z/`.

## Recorded frozen-candidate confirmation disposition

The second confirmation attempt began on the standard service tier. The
unchanged `build-pov-ray` task awarded zero credit. The attempt completed with
a conforming trace and no infrastructure exception.

An operator then selected the priority service tier for the remaining work.
Service tier is fixed at episode construction, so the active standard-tier
Caffe attempt was stopped without a score. The partial episode used eleven
model calls, 273,079 input tokens, 97,792 cached-input tokens, and 1,143 output
tokens. Its estimated cost was $1.154293. These resources remain campaign
spend and do not contribute to assessed quality.

The priority-tier Caffe attempt restarted from an empty task container. Its
initial dataset download reached the 1,200-second tool timeout. Foe preserved
the partial archive, resumed the download with `wget -c`, built Caffe, trained
for 500 iterations, and completed its independent audit. The untouched grader
awarded full credit.

The next priority-tier `configure-git-webserver` attempt completed with a live
Nginx service and a conforming trace. The untouched grader awarded zero
credit. Harbor recorded no infrastructure exception.

| Task | Service tier | Score | Calls | Input tokens | Cached-input tokens | Output tokens | Estimated cost |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `build-pov-ray` | standard | 0.0 | 22 | 475,780 | 208,896 | 8,329 | $1.317674 |
| `caffe-cifar-10` | priority | 1.0 | 36 | 1,950,295 | 1,363,456 | 9,218 | $3.077098 |
| `configure-git-webserver` | priority | 0.0 | 26 | 242,690 | 108,544 | 17,143 | $0.922862 |

The two passes produced eight successes from eleven scored attempts. Five
second attempts remained. Their maximum possible contribution was five
successes, which limited the candidate to thirteen successes from sixteen
attempts. The acceptance rule requires fourteen. Sequential stopping
therefore rejected the frozen candidate without spending the remaining five
complete attempts.

The stopped `count-dataset-tokens` setup completed six model responses and had
a seventh request in flight. The recorded responses used at least 22,310 input
tokens, 4,608 cached-input tokens, and 1,092 output tokens. Their estimated
cost was at least $0.112923. The run received no task score.

This disposition opens the confirmation trajectories for failure diagnosis
and self-improvement evidence. The calibration and sealed-holdout tasks remain
closed. A revised candidate must begin confirmation again after its activation
and transfer cases pass.

The standard-tier second-pass jobs are under
`/home/sunil/git/foe-frozen-standard-quality-candidate/target/terminal-bench-jobs/standard-frozen-candidate-confirmation-second-pass-20260825T151936Z/`.
The priority-tier jobs are under
`/home/sunil/git/foe-frozen-standard-quality-candidate/target/terminal-bench-jobs/frozen-candidate-confirmation-second-attempt-priority-20260825T154345Z/`.

## Recorded DNA completion-evidence experiments

Three closed-book DNA attempts evaluated source tree
`git-tree-sha1:9963c76c70f0cd1cc37bee2e37e02cff364bb4dd` and portable binary
`sha256:95cdb872165ec8f405e3ce8c6589ab6e37c8f7ff0c53cf3ad9c33e6d49cb120b`.
Every request used the priority service tier. One attempt received full
task-owned credit. Two attempts completed with conforming traces while their
artifacts failed the task-owned grader.

Two modified attempts used the same source tree and binary with the public DNA
checker declared through `done_when.verify`. Both attempts received full
task-owned credit. The checker remained unchanged, and the private grader
remained unavailable until Foe exited. This result measures convergence under
an author-supplied public checker. It does not establish closed-book quality.

The closed-book evidence is retained in these directories:

- `/home/sunil/git/foe-dna-self-improvement-candidate/target/terminal-bench-jobs/conditional-repair-reaudit-dna-activation-20260825T210938Z`;
- `/home/sunil/git/foe-dna-self-improvement-candidate/target/terminal-bench-jobs/conditional-reaudit-dna-third-sample-20260825T220952Z`.

The verifier-governed evidence is retained at
`/home/sunil/git/foe-dna-self-improvement-candidate/target/terminal-bench-jobs/verifier-governed-conditional-reaudit-dna-20260825T213729Z`.

An identity-bound self-improvement run then considered a general workflow
change from this contrast. The Luna diagnosis used four model calls and cost an
estimated $0.0143. It returned `insufficient-evidence` because one task family
did not support a general rule for deriving executable acceptance checks. No
candidate was produced. This abstention preserved the evidence gate, but it did
not satisfy the campaign requirement for an accepted transferable
self-improvement.

A separate closed-book attempt added a Terra-high requirement diagnosis before
Sol-low implementation and a Sol-xhigh terminal audit. The attempt used 48
model calls, 646,640 input tokens, 393,216 cached-input tokens, and 33,491
output tokens. Its estimated cost was $1.671825. Foe completed with a conforming
trace and no infrastructure exception, while the unchanged task-owned grader
awarded zero credit.

The terminal audit repaired the public BsaI flank requirement and simulated the
assembly. It reported primer melting temperatures from explicit binding arms.
The task-owned grader included a matching assembly-overhang suffix in the
complete annealed tract. The `snap_rev` tract reached 72.76066 degrees C, beyond
the public 72 degree maximum. Additional model review therefore did not correct
the repeated semantic omission.

The failed requirement-diagnosis attempt is retained at
`/home/sunil/git/foe-dna-self-improvement-candidate/target/terminal-bench-jobs/typed-requirement-diagnosis-dna-activation-20260825T222335Z`.

## Task-derived acceptance-checker evaluation

The evaluation runner can now ask a model to translate public task requirements
into an executable checker before implementation. A fixed runner installs the
generated source and requires it to reject a copy of the untouched workspace.
A Sol-low implementation child and a Sol-xhigh terminal audit each complete
through that checker under `done_when.verify`. The generated checker and fixed
runner are retained with content digests.

This lane addresses the observed gap between validation prose and executable
evidence. The model that writes the checker receives no task-owned grader. The
unchanged grader remains the quality authority after Foe exits. An activation
success requires repeated full credit on DNA. Transfer requires full credit on
an unrelated task selected before its trajectory is inspected.

The workflow constructor, runner negative control, pre-implementation workspace
digest, Harbor argument propagation, and evidence-integrity checks pass
locally. The installed checker is bound to its typed value in the root episode
log. A portable runner completed a provider-free installation check in the DNA
task container, which lacked `/usr/bin/python3`. Foe's configuration
constructor accepts the four-node program. The assessed results below reject
the mechanism for lack of repeatability.

## Recorded task-derived acceptance-checker disposition

Two assessed DNA attempts used source tree
`git-tree-sha1:321a31058e806d29003c674b5bc77c76f852bc3b` and runtime binary
`sha256:65b0b0b8d9fab850e24939a6df1b907476fdfb5a2ef6df127b898e7d0a8f07bb`.
Both used GPT-5.6 Sol low for implementation and GPT-5.6 Sol xhigh for checker
derivation and terminal audit. Every request used the priority service tier.
Both attempts completed with conforming traces and no infrastructure failure.

The first attempt received full task-owned credit. It used 37 model calls,
634,536 input tokens, 269,312 cached-input tokens, and 49,495 output tokens.
Its estimated cost was $2.558521. The checker modeled each annealing segment
as the longest primer suffix that matched the template. The terminal audit
corrected melting-temperature and assembly defects before completion.

The repeated attempt received zero task-owned credit. It used 36 model calls,
683,729 input tokens, 348,160 cached-input tokens, and 54,563 output tokens.
Its estimated cost was $2.572800. The generated checker treated only bases
after the four-base assembly overhang as the annealing segment. The terminal
audit installed `oligotm`, repaired the primers under that interpretation,
and accepted the checker result.

The task-owned grader also counted an assembly-overhang suffix when those
bases matched the adjacent template. It measured the input primer pair at
67.736454 and 61.056077 degrees C. Their 6.680377 degree difference exceeded
the permitted five degrees. The audit had received the generated checker
source and requirement interpretation as a declared predecessor value. The
checker author and audit therefore shared the same semantic error.

One success followed by one failure rejects the task-derived checker candidate
for lack of repeatability. The retained runs are:

- `target/terminal-bench-jobs/task-derived-checker-dna-activation-20260825T231830Z`;
- `target/terminal-bench-jobs/task-derived-checker-dna-repeat-20260825T233922Z`.

The revised workflow removes the checker-author value from the terminal
audit's declared inputs. The audit receives the public task and implementation
result. It can still invoke the immutable checker, whose installation precedes
implementation. This change tests whether explicit information-flow
separation prevents a generated semantic error from anchoring the independent
audit.

## Recorded independent-audit checker disposition

Two revised DNA attempts used source tree
`git-tree-sha1:3066e4f545880905ba2aaf9cc0772dcbc8a91436` and runtime binary
`sha256:65b0b0b8d9fab850e24939a6df1b907476fdfb5a2ef6df127b898e7d0a8f07bb`.
Both used GPT-5.6 Sol low for implementation and GPT-5.6 Sol xhigh for checker
derivation and terminal audit. Every request used the priority service tier.
Both attempts completed with conforming traces and no infrastructure failure.

The first revised attempt received full task-owned credit. It used 35 model
calls, 610,641 input tokens, 244,224 cached-input tokens, and 54,061 output
tokens. Its estimated cost was $2.644578. The terminal audit repaired the
primers after independent physical and assembly checks.

The repeated attempt received zero task-owned credit. It used 46 model calls,
833,010 input tokens, 483,328 cached-input tokens, and 54,920 output tokens.
Its estimated cost was $2.690459. The generated checker accepted the result,
and the terminal audit independently installed `oligotm`, repaired three
primers, reconstructed the assembly, and accepted its own validation.

The terminal audit searched for the longest primer suffix that appeared
anywhere in the corresponding template. It did not require extra matching
overhang bases to be adjacent to the selected annealing-arm occurrence. The
audit therefore measured the input primer pair at 67.983932 and 63.059994
degrees C, whose difference was 4.923938 degrees.

The task-owned grader evaluated the matching overhang suffix beside the
selected annealing arm. It measured the same pair at 67.938466 and 62.922277
degrees C. Their 5.016189 degree difference exceeded the permitted five
degrees.

Removing the checker-author value prevented the generated interpretation from
entering the terminal audit's declared context. The independent audit still
constructed a different incorrect acceptance rule. One success followed by
one failure therefore rejects the revised candidate for lack of repeatability.
No transfer attempt is warranted.

The retained runs are:

- `target/terminal-bench-jobs/task-derived-checker-independent-audit-dna-activation-20260826T000538Z`;
- `target/terminal-bench-jobs/task-derived-checker-independent-audit-dna-repeat-20260826T002511Z`.

Task-derived checkers remain research evidence. Author-supplied public
completion checkers remain the primary development mechanism because their
meaning can be validated before provider spend. The unchanged task-owned
grader remains the quality authority for every assessed result.

## Author-supplied completion-checker qualification

All six author-supplied completion checkers ran inside their pinned
`terminal-bench/terminal-bench-2-1@6` task containers on 2026-08-26. The
qualification made no provider request. Each negative control ran against an
untouched task workspace and produced at least one finding. Each author oracle
then ran in a fresh workspace. Every public checker accepted its oracle, and
every unchanged task-owned grader awarded `1.0`.

| Task | Negative-control finding | Checker SHA-256 |
| --- | --- | --- |
| `cancel-async-tasks` | `/app/run.py` is absent | `ad680e6f7f790cb40356f001ea0d0e8b7cbed34475b64fca8f74c695674add53` |
| `dna-assembly` | `/app/primers.fasta` is absent | `a93cc0ff4964ef3a9e0096288f06f0d991ef5a4b80fd25f7723374c4b0a59450` |
| `fix-git` | the required commit is unreachable from `master` | `67ef316d3f6216dc2c5ae131a7da4e0f24de433a12fcc602eee1bcd7a032b7a2` |
| `git-multibranch` | the live SSH service refuses the connection | `60f4eae6f3f4d2b6af4651095f7ad5b3b9c8d22d6c5a0fc6471db2e6986d5e8c` |
| `gpt2-codegolf` | `/app/gpt2.c` is absent | `bd2c54625e4da5c491ff571a48594b6db1c71d8422308f8a8722e6c2bbef7b7b` |
| `large-scale-text-editing` | `/app/apply_macros.vim` is absent | `979316eaaf8877068202974ebb0bd08e5969dca8b96d6384a1207accf32982c7` |

The qualification report has SHA-256 digest
`7a6752148bc3b526f35f0246305b597d28871dade6d36b3f7e534710ad027d7c`.
It is retained at
`target/terminal-bench-verifier-controls/controls-20260826T072545Z/verifier-controls.json`
in the campaign worktree. The report records checker and oracle digests,
negative-control findings, Harbor exit codes, and task-owned rewards.

## Cross-trajectory evidence-capacity qualification

The identity-bound collector ran against seven retained diagnoses from source
tree `git-tree-sha1:2fbeddc6173fe764824960931884815a37dd443a` and runtime
binary
`sha256:f3d38553a3b3766bf928b1b4686cc802b532ab3691aff52459160427275d28cb`.
The corpus contains two `build-pov-ray` attempts, one
`configure-git-webserver` attempt, three `dna-assembly` attempts, and one
`dna-insert` attempt. Every task belongs to the explicit
`self_improvement_evidence` group.

The first schema-4 encoding required 71,964 bytes and was rejected by the
48 KiB handoff limit. The nominal 24-diagnosis count bound therefore did not
bound a real corpus. The collector now retains one failed completion claim per
attempt, keeps verification results from the episode that supplied the root
outcome, and removes transport-only fields from result summaries. The count
limit is twelve diagnoses.

The same seven diagnoses now encode to 45,081 bytes. The evidence document has
SHA-256 digest
`0abed0224dd2d9ccd4428196565ac3b0c8464504b83edf73679dcce69fe00f57`.
It is retained at
`target/terminal-bench-diagnostics/opened-confirmation-and-dna-evidence.json`
in the campaign worktree.

The document contains no repeated failure contrast. `build-pov-ray` supplies
one success and one failure. The three `dna-assembly` attempts all failed and
supply no matching success. This result validates evidence transport while
correctly withholding autonomous candidate generation. A subsequent
self-improvement input must contain two matching failures and one same-task
success before the workflow can spend a model call.

## Two-worker execution implementation

The campaign runner supports one or two assessed workers. One worker remains
the default. A parallel cohort receives separate access-only OAuth files that
contain no refresh token. Foe rejects a request when the access token enters
its refresh window. The runner rechecks the complete required lifetime when it
issues each credential.

The runner admits two tasks only when their declared reservations total at
most four CPUs and eight GiB of memory. An eight-GiB task runs alone. Host
admission requires at least fourteen GiB of available memory and one hundred
GiB of free disk. Swap activity, full-memory pressure, or an out-of-memory
result disables later parallel cohorts. Ten GiB of available memory is the
minimum for any further task.

SIGINT and SIGTERM terminate every active Harbor process group. Cleanup removes
the temporary credentials. The campaign manifest is replaced atomically after
each execution group and during cancellation or error recovery. It distinguishes
tasks whose Harbor process started from tasks that never started.

An adversarial review found four defects. The runner did not handle SIGTERM,
could signal a process group after its process exited, wrote the manifest only
at campaign end, and checked lease expiry before the point of issuance. The
reviewed implementation corrects all four defects. Documentation describes the
credential file mode as accidental-write prevention and the post-run digest as
mutation detection. The task identity may read or change its access-only file,
so the integrity claim rests on post-run detection rather than the file mode.

The integrated branch passes 105 evaluator tests, the Bazel evaluator target,
the full Rust workspace tests, clippy with warnings denied, every executable
example, and all line-budget checks. The implementation has made no provider
request. The required matched four-task serial and two-worker qualification
remains pending. Assessed parallel execution cannot contribute campaign quality
evidence until that qualification preserves all four scores, trace results,
credential checks, and retained records while reducing makespan by one third.

## Two-worker installation preflight

The first four-task installation preflight exposed two adapter defects without
making a provider request. The assessed adapter invoked the removed `foe
schema` form rather than `foe plan --schema`. Harbor recorded an errored trial
for each task. The campaign runner still returned success because it treated a
readable Harbor result as complete without checking the completed and errored
trial counts.

The adapters now share one tested schema-probe command. Campaign completion
requires zero errored trials and requires the completed count to equal the
total count. The original failed artifacts remain under
`target/terminal-bench-jobs/qualification-install-serial-20260826T074847Z`.

The corrected `fix-git` smoke installation completed without an exception at
`target/terminal-bench-jobs/corrected-install-smoke-20260826T075345Z`. The
matched four-task serial and two-worker installation cohorts then completed
all four trials with zero errors. Both used portable binary
`sha256:ff7d062a57acf865e22d7781fb7e9c05ac95863e5a255fc3145d4479e0eebb59`.

The serial manifest is retained at
`target/terminal-bench-jobs/qualification-install-serial-corrected-20260826T075406Z/campaign.json`.
Its SHA-256 digest is
`996bcf9e1227baeefa4ee7934d92f1843c247a8d4f3106bd7d9c04c92ea9b66c`.
The four execution groups took 62.625 seconds in total.

The two-worker manifest is retained at
`target/terminal-bench-jobs/qualification-install-parallel-corrected-20260826T075519Z/campaign.json`.
Its SHA-256 digest is
`f62a254aadb0ecb2deff5afa8fe17c28231fe31fce5fdc6a5cd9e07d9a2576d2`.
The two execution groups took 32.305 seconds in total. The 48.4 percent
reduction exceeds the one-third makespan target for installation mechanics.
The result does not qualify assessed concurrency because installation-only
trials produce no task score, Foe episode, or conformance report.

A local command-surface diagnosis also invoked `foe schema` with an explicit
model. It was terminated at workflow startup before any model request. Its
six-event account is retained at
`target/terminal-bench-preflight/accidental-schema-task-20260826`.

## Verifier failure-locus evidence qualification

Three retained `dna-assembly` attempts from 2026-08-26 exercise the
failure-locus evidence contract. They evaluated source tree
`git-tree-sha1:8c5e2b72580507cc49881ed11d83209bc0c26c0e` with runtime binary
`sha256:ff7d062a57acf865e22d7781fb7e9c05ac95863e5a255fc3145d4479e0eebb59`.
Every attempt used the built-in workflow, GPT-5.6 Sol with low reasoning, and
the priority service tier.

Two completed attempts received zero task-owned credit. Both have the same
coarse profile: an artifact-outcome mismatch in
`test_outputs.py::test_primers` with `AssertionError`. Their retained Common
Test Report Format (CTRF) artifacts identify different assertions:

| Episode | Verifier artifact SHA-256 | Stable locus | Assertion |
| --- | --- | --- | --- |
| `ep_ff14aa09` | `8a687e9240a719d4d8f222441d5782674bd8c85d40cfebc117404f504577395d` | `sha256:83bdf345ca8ce6972feeb670ba0b3abfb45ad3b63ce65b75ac3d5b93a1821c07` | `tests/test_outputs.py:116`, `abs(fwd_tm - rev_tm) <= 5` |
| `ep_1164414b` | `b26c75aa24c7b3b5f28319724dda415799cce5bb073cab956b7fc134437494b6` | `sha256:1f250d2468444abe8ba559196f90927c3443ce3d460cc340b881c848b9b7438e` | `tests/test_outputs.py:99`, `15 <= len(extra_r) <= 45` |

The second verifier observed an effective reverse annealing length of 46
bases. The first verifier observed a primer melting-temperature difference of
5.813507 degrees C. The compact evidence keeps the stable assertions and
messages. It excludes those observed-value expansion lines and the remaining
traceback.

Episode `ep_01be21a9` received full task-owned credit under the same execution
configuration. The collector therefore produces one repeated same-task
contrast with two distinct failed-attempt loci and one successful episode.
Grouping remains coarse enough to acquire the contrast, while diagnosis sees
the assertion heterogeneity. The contrast digest is
`sha256:4a84ee05fafe94143a1b266d1faac110de591f5b28791de25bddca0efc4b92a2`.

Each failed attempt has one total and retained failed test. Both attempts have
zero omitted, unlocated, and ambiguous failures. The collector rejects an
attempt from contrast construction when any completeness count is nonzero or
the locus digests are not unique.

The diagnosis validator requires citations for both failed episodes, both
verifier artifact digests, and both locus digests. It also requires a local
explanation for each attempt and one shared mechanism. An unsupported shared
mechanism permits only an insufficient-evidence result. A missing or ambiguous
locus prevents construction of the contrast before a model request.

The retained attempts are:

- `/home/sunil/git/foe-terminal-bench-quality-campaign/target/terminal-bench-jobs/built-in-closed-book-development-dna-assembly-20260826T101346Z`;
- `/home/sunil/git/foe-terminal-bench-quality-campaign/target/terminal-bench-jobs/built-in-closed-book-development-dna-assembly-third-20260826T103457Z`;
- `/home/sunil/git/foe-terminal-bench-quality-campaign/target/terminal-bench-jobs/built-in-closed-book-development-dna-assembly-repeat-20260826T102503Z`.

The regression fixtures contain only the failed assertion region from each
CTRF artifact. Additional tests cover missing and malformed verifier output,
stale result metadata, changed verifier digests, symlinked artifacts, bounded
status fields, ambiguous assertions, host-state normalization, and protected
task groups. This qualification made no provider request.

## Standard-tier acceptance-evidence source candidate evaluation

An autonomous source-improvement run generated a proposal that requires the
built-in terminal audit to cite final-artifact evidence for every supplied
requirement. The proposal changed runtime code, a regression test, and the
affected specifications. Its retained artifact binds the exact parent source
tree and six changed-file digests. The source checker rejected that artifact
because `cargo fmt --all -- --check` failed. The retained result therefore has
`candidate_acceptance.accepted: false`, `source_candidate: null`, and
`direct_implementation_required: true`.

A direct mechanical formatting pass preserved the proposal's behavior and
made the full source checker pass. The resulting source commit is `ecc1dd9`,
its Git tree is
`git-tree-sha1:88c933437ab04272fdbbfbfc1d6fdf3b41a41a60`, and its portable
binary is
`sha256:675a206b7371b756c35699c19dc1a3fb2715b5d6fc91dc5dfa634ecc8c36ea34`.
The quality evaluation therefore measures a proposal that required direct
mechanical repair. It does not establish an autonomously accepted source
improvement. The implementation remains an evaluation candidate. Two dense
production blocks use `rustfmt::skip`, and the kernel occupies its complete
5,250-line allowance. Promotion requires task-quality evidence before source
cleanup.

Two standard-tier launch attempts found adapter defects before producing task
quality evidence. The first runner still required the priority service tier.
The second synthesized `foe plan TASK --model ...`, although `foe plan`
accepts only a program document. The second attempt created no episode and
reported no model usage. The retained directories are:

- `target/terminal-bench-jobs/standard-acceptance-evidence-dna-activation-20260826T172633Z`;
- `target/terminal-bench-jobs/standard-acceptance-evidence-dna-activation-fixed-20260826T172811Z`.

The adapter now runs the built-in workflow through its supported command. It
then reconstructs a program document from the root `episode/start` and invokes
`foe plan --config` while the verifier and credential paths still exist. The
adapter rejects a reconstructed plan whose program, task, or identity differs
from the root start. Plan failures retain their exit status and standard
error. The evaluation suite passes 180 Python tests and both Bazel evaluation
targets after this repair.

The first valid `dna-assembly` activation attempt received task score 1.0.
It used GPT-5.6 Sol with low reasoning for implementation, high reasoning for
the built-in audit, and the standard service tier. It used 51 model calls,
755,286 input tokens, 487,936 cached-input tokens, and 27,789 output tokens.
Estimated cost was $1.820354, and the execution group took 751.081 seconds.
The trace passed 13,843 assertions across declared authority, hierarchical
budgets, reconstructable evidence, typed outcomes, and workflow provenance.
No infrastructure, credential, or conformance failure was recorded.

The mechanism affected the result rather than only its reporting. The
implementation child used 19 calls and returned a candidate with an unresolved
`oligotm` validation risk. The audit child used 32 calls, installed the missing
Primer3 utility, repaired the primer sequences, and ran a final independent
check after its last edit. All eleven acceptance-evidence entries cite that
final result at sequence 3,187. The unchanged task-owned grader then accepted
the workspace. The verifier-governed lane supplies corrective findings during
the run, so this result measures convergence under declared verification. It
does not establish closed-book task quality by itself.

The retained campaign is under
`/home/sunil/git/foe-dna-assessment-feedback-source-improvement/target/terminal-bench-jobs/standard-acceptance-evidence-adapter-reconstruction-20260826T173536Z`.
The campaign manifest SHA-256 is
`aaafc2faccb32138f49314d49fc8d53be1b871c48805f5cf2b4e2552d2839714`.
The retained plan equals the root program, task, and identity.

Two unchanged activation repeats also received task score 1.0. They completed
with zero trial errors and conformant traces. The first repeat used 30 model
calls, 379,532 input tokens, 227,328 cached-input tokens, and 18,755 output
tokens. Its estimated cost was $1.074847. The second used 35 model calls,
763,965 input tokens, 498,688 cached-input tokens, and 26,171 output tokens.
Its estimated cost was $1.784003.

The two repeats are retained under
`/home/sunil/git/foe-dna-assessment-feedback-source-improvement/target/terminal-bench-jobs/standard-acceptance-evidence-activation-repeat-20260826T174843Z`.
Their campaign manifest SHA-256 is
`0d90e4fd0edf44c32b79f8941c6ee7679467e3a90e8960ab9d2293a2fbcd42f8`.
Across all three candidate attempts, the score is 3.0 of 3.0. The exact parent
source tree supplied the diagnosis corpus and scored 1.0 of 3.0. Those parent
attempts used the priority service tier, while the candidate attempts used the
standard service tier.

The acceptance-evidence mechanism activated in both repeats. In each case,
the implementation returned a defective `primers.fasta`. The independent
audit repaired the primer architecture, annealing regions, or junctions. It
then cited one successful final-artifact check for all eleven requirements.
The unchanged task-owned grader accepted both workspaces. The candidate has
passed its frozen activation gate and advances to an unrelated transfer case.

The unrelated `gpt2-codegolf` transfer case also received task score 1.0. The
implementation child used 16 calls. It produced a compilable 3,860-byte C
program and reported that its generated tokens were wrong. The audit then used
its complete 60-call allowance to repair checkpoint layout, tokenization, and
inference behavior. Its final call edited and compiled `gpt2.c`. The task-owned
grader accepted that exact workspace.

Foe reported `exhausted: model_calls` because the audit had no request left to
run its checker after the final edit. The task score governs candidate quality,
so the result qualifies as transfer. The inaccurate terminal outcome remains a
completion-efficiency defect. The source-improvement workflow's separate
16-call finalization child addresses the same failure mechanism for future
source candidates.

The transfer used 76 model calls, 3,081,993 input tokens, 2,514,432
cached-input tokens, and 35,906 output tokens. Its estimated cost was
$3.994137. The trace passed 37,942 assertions and recorded no conformance,
credential, or infrastructure failure. The completion checker retained its
SHA-256 digest
`bd2c54625e4da5c491ff571a48594b6db1c71d8422308f8a8722e6c2bbef7b7b`.

The transfer campaign is retained under
`/home/sunil/git/foe-dna-assessment-feedback-source-improvement/target/terminal-bench-jobs/standard-acceptance-evidence-transfer-gpt2-20260826T180749Z`.
Its campaign manifest SHA-256 is
`467f1480c7481a7beb6003bfbcdfb10e9df6070cfaa4fe6bc605d9d0a1968340`.
This result establishes task-quality transfer for the directly normalized
proposal on an unrelated C inference scenario. It does not isolate an
incremental transfer gain over the parent, which has also passed this task in
an earlier built-in-workflow attempt. A new source-improvement run must pass
its source checker without direct repair before the campaign counts an
autonomous source improvement.

Engineering review preserved the evaluated behavior while making its
completion gate readable and correcting spilled-result validation. The gate
now reads archived canonical process output before checking exit status. A
regression test proves that a spilled nonzero process result cannot support a
successful completion claim. The refactored candidate is commit `199e38a` with
Git tree `git-tree-sha1:e456865ad62a99478af0438cae536c3b7808daf1`. Its
portable binary is
`sha256:4f3d545980fc12ce975441d09b5f1d094966c4406e46e847e6b2fb01fea6925a`.
The full workspace tests, clippy checks, example suite, and line-budget check
pass. The readable implementation requires a 5,350-line kernel allowance and
occupies 5,316 lines.

One standard-tier `dna-assembly` sanity run of the refactored candidate
received task score 1.0 with no exception. Foe completed after 36 model calls
and cited one successful final-artifact check for all eleven requirements. It
used 531,849 input tokens, 331,264 cached-input tokens, and 27,666 output
tokens. Estimated cost was $1.488166. The trace passed 7,124 assertions with no
violation. The implementation child used nine calls, and the independent
audit used 27 calls to install Primer3 and repair the candidate before its
verifier-owned completion.

The sanity run is retained under
`/home/sunil/git/foe-final-artifact-evidence/target/terminal-bench-jobs/standard-final-artifact-current-main-dna-20260826T183754Z`.
Its campaign manifest SHA-256 is
`5dba8f3d57bfaa5cd416a5a7b5f92d67e9134a7882c8e6248ec3b40f251ca9d7`.
This run confirms that the cleanup and spill correction preserve the
activation-case quality result. It remains evidence for the directly
normalized proposal because its source ancestry still contains the mechanical
repair.

The source-improvement workflow also reserves its final verification capacity.
Its 60-call audit allocation is divided between a 44-call independent review
and a fresh 16-call finalization child. A blocked or exhausted review
contributes a typed empty handoff. The finalization child therefore still runs,
checks the candidate before editing, repairs findings, and completes only
through `done_when.verify`. The root call allowance remains unchanged. A
provider-free generated program passes `foe plan` with five possible firings
and the declared 44-plus-16 allocation.

## Reserved-finalization autonomous source improvement

The source-improvement workflow reran against the exact parent source tree
`git-tree-sha1:8c5e2b72580507cc49881ed11d83209bc0c26c0e`. It used the bound
cross-trajectory report and private candidate assessment from the rejected
source candidate. The runtime binary matched the report at
`sha256:ff7d062a57acf865e22d7781fb7e9c05ac95863e5a255fc3145d4479e0eebb59`.
All model nodes used the standard service tier.

The diagnosis child completed in one model call. The implementation child
used 46 calls and passed the source checker. The independent review used its
complete 44-call allowance and ended exhausted after introducing a clippy
failure. Its declared empty value allowed the workflow to continue. The fresh
finalization child ran the source checker on its first call. It repaired the
reported defect and completed through `done_when.verify` after ten calls.

The resulting source candidate passed with zero checker findings. Its result
records `candidate_acceptance.accepted: true`, a non-null source candidate,
and `direct_implementation_required: false`. The bundle identity is
`sha256:fb05444920bbf92d862edeca3d3451759f55d4f8a196a63a56fe161749e1669e`.
The candidate identity is
`sha256:7a2786902748b31aa16713c3d638d5d1702e43400244ced9d68598b28c933941`.
The run used 101 model calls, 7,416,184 input tokens, 6,090,752 cached-input
tokens, and 55,861 output tokens. Estimated cost was $8.855249. It completed
in 1,433.009 seconds.

The accepted source is commit `8765e74` with Git tree
`git-tree-sha1:1f79451d5c717464f453b1843d819f4458d0590a`. It changes runtime
source, regression tests, the built-in workflow, and both affected
specifications. Its portable binary is
`sha256:b420f4a2a6a3db278bd3f4b75d277e813bfb74fe812a9ab336f2f16a35e50f0c`.
The retained self-improvement result is under
`/home/sunil/git/foe-identity-bound-dna-source-improvement/target/dna-finalization-autonomous-source-improvement-retry`.
Its result SHA-256 is
`6c5316ee73225c563013ea8bc32ce1e070f9a44e5da80b65133481d92b32a725`.

An unchanged task-owned `dna-assembly` grader accepted the exact source and
binary pair with score 1.0. The adopted run used 29 model calls, 491,323 input
tokens, 295,936 cached-input tokens, and 24,528 output tokens. Estimated cost
was $1.390482. The trace passed 6,421 assertions with no violation. The
accepted completion contained eleven requirement-specific citations to
successful checks after the final edit.

The activation run is retained under
`/home/sunil/git/foe-dna-finalization-autonomous/target/terminal-bench-jobs/standard-autonomous-finalization-dna-20260826T192112Z`.
Its campaign manifest SHA-256 is
`9449bd1e586fa7005ddd9111ca0f2460c3317d6647f9df2ed6fd97afdf5d1445`.
The source adoption verified its launched program and recorded adoption
identity
`sha256:89f8a1386c0ac66f35dfc49baac5679b7daf3adaafe1b00824134d7929eac4bf`.

Two frozen activation repeats also received task score 1.0 with no exception.
The first repeat completed after 34 model calls. It used 614,952 input tokens,
393,216 cached-input tokens, and 23,566 output tokens. Estimated cost was
$1.515550. Its trace passed 6,103 assertions. The second completed after 47
model calls. It used 899,426 input tokens, 696,832 cached-input tokens, and
23,314 output tokens. Estimated cost was $1.555389. Its trace passed 9,516
assertions. Both traces had zero violations.

The repeats are retained under
`/home/sunil/git/foe-dna-finalization-autonomous/target/terminal-bench-jobs/standard-autonomous-finalization-dna-repeat-20260826T193131Z`.
Their campaign manifest SHA-256 is
`24376caf7317d4aa1db34c8a0bb326ec5299672e5f9798b2fef2a58ecf4697ba`.
The exact autonomous source candidate has scored 3.0 of 3.0 on its activation
case and advances to the unrelated transfer case.

The unrelated `gpt2-codegolf` transfer case also received task score 1.0 with
no exception. The implementation child used 16 model calls, and the terminal
audit used 43. The audit replaced degenerate repeated-token behavior with a
sampler that passed the unchanged public-continuation checker. Foe completed
with requirement-specific evidence after the final edit.

The transfer used 1,872,508 input tokens, 1,387,008 cached-input tokens, and
31,257 output tokens. Estimated cost was $3.121943. The trace passed 39,685
assertions with no violation. The transfer is retained under
`/home/sunil/git/foe-dna-finalization-autonomous/target/terminal-bench-jobs/standard-autonomous-finalization-gpt2-transfer-20260826T195233Z`.
Its campaign manifest SHA-256 is
`af29e1083042665f56fc42bd86f10ed7a3ecd3fe437b0449c5afd9c06efcb90d`.

The candidate therefore satisfies the source-improvement transfer gate: it is
identity-bound, accepted without direct repair, includes source, test, and
specification changes, scores 3.0 of 3.0 on its activation case, and scores
1.0 on an unrelated task. The reviewed implementation remains the production
candidate because it enforces the same mechanism with stronger exact checks.

Direct review found two correctness gaps in the autonomously generated gate.
It treats a nonzero process result as successful evidence because process exit
status is data rather than a tool error. It also places the final-artifact
boundary after declared writes while an executable call can change files. The
reviewed implementation in pull request 114 covers both cases and has a
readable validation path. The autonomous source remains an evaluated
self-improvement artifact. It is not the proposed production implementation.

## Priority frozen-release activation and verifier feedback

One candidate combines the unconditional built-in terminal audit, the live
task-state completion rule, and final-artifact acceptance evidence. Its commit
is `4669797`, and its Git tree is
`git-tree-sha1:cda2cf060df78de9dfac45cccffa656a1082e984`. The portable binary
is `sha256:0442fcfd2d5563c688397a5c5d9f053f474f2c737798435d564a08d9d766dcca`
and is 6,079,656 bytes. Repository tests, clippy, the example suite, and line
budgets pass.

A provider-free installation run loaded the portable binary in the
`dna-assembly`, `git-multibranch`, and `gpt2-codegolf` images. It used two
installation workers and recorded no exception. The retained directory is
`/home/sunil/git/foe-priority-frozen-release/target/terminal-bench-jobs/priority-frozen-activation-install-20260826T202043Z`.
Its campaign manifest SHA-256 is
`dd5ab8c122fefc4f8da5e405e7cff5e26a5b0b5be741abb87394b818880cf602`.

Deterministic capability probes then ran in all three images. Each image
provided a writable task directory, package installation, large-file search,
windowed reads, and enforced tool timeouts. The DNA and GPT-2 images lacked
one or more executables in the fixed standard-path probe. All three images
killed a background shell process after its tool call. These observations
change model-visible tool selection but do not invalidate the task
configuration. The retained capability reports have these SHA-256 digests:

- `dna-assembly`: `7b39a9e8725cde6078181b90d37a6cbb50a4d7b17c3c875af3e39ddfd7007683`;
- `git-multibranch`: `ab0a9edd2ee982cb9ae8749d7c4cd5cb85cd37e8bd2317e958ce11d137e1bc9b`;
- `gpt2-codegolf`: `e8ea4f66a3562014873148c70d39f9869b5f5106ad6781110bcab1584287c887`.

The first unchanged Terminal-Bench pass used GPT-5.6 Sol, low implementation
reasoning, high audit reasoning, the priority service tier, sandbox mode
`off`, and measurement-only token accounting. The results were:

| Task | Score | Calls | Input | Cache read | Output | Estimated cost | Trace assertions |
|---|---:|---:|---:|---:|---:|---:|---:|
| `dna-assembly` | 0.0 | 31 | 393,312 | 212,480 | 21,107 | $1.230460 | 7,094 |
| `git-multibranch` | 1.0 | 35 | 340,751 | 164,864 | 19,522 | $1.159934 | 32,008 |
| `gpt2-codegolf` | 1.0 | 49 | 1,047,590 | 671,232 | 28,497 | $2.343865 | 34,964 |

Every trace conformed, and no trial recorded an infrastructure failure. The
run requested two assessed workers. The resource policy selected serial
execution because available memory was below its fixed 14,336 MiB parallel
threshold. This run therefore provides no two-worker qualification evidence.
The retained directory is
`/home/sunil/git/foe-priority-frozen-release/target/terminal-bench-jobs/priority-frozen-activation-first-pass-20260826T202241Z`.
Its campaign manifest SHA-256 is
`aa3b72cc826a2370243c19eba30cda6ac67262de692a66b9da1e9a048d4315ef`.

The DNA audit cited one comprehensive command for every task requirement and
reported completion. The unchanged task-owned grader rejected the input primer
pair. The audit removed a fixed thirteen-base prefix before computing melting
temperatures. The grader also counted overhang suffix bases that matched the
template. Under that definition, the pair differed by 5.263745 degrees
Celsius, above the permitted five degrees. This is a semantic false acceptance
rather than an infrastructure or runtime-evidence failure.

The public DNA completion checker models the grader's overlap rule. Its fresh
negative and oracle controls both passed. The controls are retained under
`/home/sunil/git/foe-priority-frozen-release/target/terminal-bench-verifier-controls/priority-frozen-dna/controls-20260826T205408Z`.
The control report SHA-256 is
`cd34417996444c54c33d295cbd7fd92e6cf4b179da57c672b76eb2ff11cf6d9d`.

The same frozen release then ran `dna-assembly` with that checker assigned to
the terminal audit's `done_when.verify`. The audit called the checker six
times. Intermediate findings identified an unavailable `oligotm` executable,
an invalid BsaI flank, a 46-base annealing tract, and a primer pair whose
melting temperatures differed by more than five degrees. The audit installed
the missing package and repaired each finding. Its final checker call and
authoritative completion verification returned no findings. The unchanged
task-owned grader awarded score 1.0.

The governed run used 57 model calls, 929,237 input tokens, 684,544 cached
input tokens, and 24,955 output tokens. Its estimated cost was $1.751690. The
trace passed 6,825 assertions with no violation. The retained directory is
`/home/sunil/git/foe-priority-frozen-release/target/terminal-bench-jobs/priority-frozen-dna-done-when-20260826T205457Z`.
Its campaign manifest SHA-256 is
`0eb2f4558c4b91b39cc46ea6df8cdcd058e30eb699bbdf44c180a1f76908da4b`.

Two additional unchanged DNA attempts both received score 0.0 without an
exception. One attempt used 25 model calls, 285,677 input tokens, 137,216
cached-input tokens, and 20,606 output tokens. Its estimated cost was
$1.060850, and its trace passed 7,701 assertions. The other attempt used 38
model calls, 436,083 input tokens, 244,224 cached-input tokens, and 19,275
output tokens. Its estimated cost was $1.250626, and its trace passed 9,163
assertions. Both traces had zero violations.

The repeated failures exercised two related melting-temperature boundaries.
One primer had a grader temperature of 72.019132 degrees Celsius, above the
permitted maximum. Another primer pair differed by 6.021674 degrees Celsius,
above the permitted five degrees. Both audits reported completion after checks
that used a narrower annealing-region interpretation.

The repeats are retained under
`/home/sunil/git/foe-priority-frozen-release/target/terminal-bench-jobs/priority-frozen-dna-closed-book-repeats-20260826T211031Z`.
Their campaign manifest SHA-256 is
`450f09d176077861521845d6c5a51ce51d00e831331128886ebd17e6eab28241`.
The frozen release therefore scored 0.0 across three priority-tier closed-book
DNA attempts. It did not pass its activation gate. The verifier-governed pass
remains evidence for corrective convergence under a declared semantic
checker.

## Priority cross-trajectory source-improvement attempt

A cross-trajectory report combined all three closed-book DNA failures with the
verifier-governed success. It was bound to source tree
`git-tree-sha1:cda2cf060df78de9dfac45cccffa656a1082e984` and runtime binary
`sha256:0442fcfd2d5563c688397a5c5d9f053f474f2c737798435d564a08d9d766dcca`.
The report SHA-256 is
`81f20e31c7b319695cce155d6646478ed9672c93d6936eee3f2e6810553fb9e0`.

Foe used the report in its declared source-improvement workflow. Every model
request used GPT-5.6 Sol and the priority service tier. Diagnosis and initial
implementation used low reasoning. The independent source audit used xhigh
reasoning. Token limits were measurement-only.

The diagnosis child completed in one call. The implementation child used 25
calls and proposed moving a configured verifier to the workflow boundary. The
source audit rejected that proposal because the three activation failures had
no configured verifier. It also found that moving the gate would weaken the
existing repair path.

The source audit used its 44-call allowance to replace the rejected proposal.
It required each passed acceptance claim to quote an exact excerpt from its
cited tool result. A fresh finalization child used 11 calls and completed
through the source validator. The resulting source, regression tests, and
specifications passed the workspace tests, clippy checks, examples, formatting
check, and line budgets.

The self-improvement run used 81 model calls, 8,201,596 input tokens,
6,641,664 cached-input tokens, and 34,468 output tokens. Its estimated cost was
$9.585754, and it completed in 917.141 seconds. The retained result SHA-256 is
`554e5310939b9ba9c95a5f988fcc3f2f72f4fba02f6bee30a300773efdd06d6d`.
The bundle identity is
`sha256:dfad42707edf33a84ab8dfdaf958181a8e736c82b1b2ce9b7b661d20e8b58185`.
The candidate identity is
`sha256:d91b9f0c36596397a2ed7a5b08d9adf9b9afddd68615dba1b453586454146b2d`.

The external controller rebuilt the candidate as source tree
`git-tree-sha1:e3053cdd0c966afa54d893c7303cea9a6de03a90` and portable binary
`sha256:ce72d3732fcd5807713934992795b07aaf12fdce76c567acd83beabb40931570`.
The unchanged task-owned grader then rejected both completed activation
attempts. The attempts used 34 and 21 model calls and cost $1.298761 and
$0.849888. Their traces passed 7,801 and 4,478 assertions without violations.

Both audits quoted concrete operands from their cited results. Both still used
a fixed primer-tail boundary and reported a pair difference below five
degrees Celsius. The grader included a matching overhang suffix in the
annealing tract and measured 5.263745 degrees Celsius. Exact observation
binding improved provenance while leaving the semantic interpretation
unchanged.

The candidate could no longer reach the required two successes in three
attempts after its second failure. The campaign stopped the third attempt
under the sequential-stopping rule. The two completed attempts are retained
under
`/home/sunil/git/foe-priority-dna-self-improvement/target/terminal-bench-jobs/priority-observed-evidence-activation-controlled-20260826T215119Z`.
The cancelled campaign manifest SHA-256 is
`b3b50b2b558475b40d1082913a7b005ed9f1023b13540d7f0cfd1c6eebf81a3b`.
The autonomous candidate is rejected for promotion.

## Failure-operand diagnosis and final-artifact audit candidate

Review of the rejected autonomous candidate found that the trajectory report
retained each pytest source assertion but omitted its concrete rewritten
assertion. The diagnosis could see `abs(fwd_tm - rev_tm) <= 5`. It could not
see the measured value `5.263745 <= 5`. The missing operands prevented a
causal comparison between the audit's accepted interpretation and the
task-owned verifier's rejected interpretation.

The trajectory collector now retains one bounded pytest rewritten assertion
when the verifier supplies an unambiguous value. The value does not
participate in the stable failure-locus identity. The source-improvement
diagnosis receives the value, while its generalized implementation handoff
and candidate source remain unable to copy task-specific failure details.

The revised parser recovered these operands from the three retained DNA
failures:

- `5.263745 <= 5`;
- `72.019132 <= 72`;
- `6.021674000000004 <= 5`.

The failure-locus hashes remained unchanged. The Terminal-Bench unit suite
passed all 182 tests, including identity stability and disclosure controls.
Commit `6148220` contains the collector, workflow instruction, tests, and
documentation.

A direct Foe candidate then changed the built-in terminal audit's final-state
acceptance rule. When a requirement measures a portion of a final value that
matches supplied input, the audit aligns the complete final value against the
input. It includes every contiguous matching extension in the measured
portion. The audit treats implementation-asserted boundaries as unverified
until an independent observation establishes them.

The candidate changes one checked-in workflow instruction, three regression
assertions, and the design specification. It adds no runtime mechanism. Its
commit is `3aee3e7`, and its Git tree is
`git-tree-sha1:cb1efa794591cbbe9733ec86a7779bfbe0ff9ecd`. The evaluated binary
is `sha256:b6910a230b5c935e2140f767a8e15fe0cadb90a51190b4c0cd2d7a78b8073c72`.
Workspace tests, clippy, examples, formatting, and line budgets pass.

Three priority-tier closed-book DNA attempts evaluated the unchanged task and
task-owned grader. All three received score 1.0 without an exception. Their
measurements were:

| Attempt | Calls | Input | Cache read | Output | Estimated cost | Trace assertions |
|---|---:|---:|---:|---:|---:|---:|
| `RGcMngE` | 38 | 822,635 | 577,536 | 29,451 | $1.800430 | 11,045 |
| `TZtHJ2e` | 34 | 462,302 | 236,032 | 24,346 | $1.486413 | 6,729 |
| `UJRapEQ` | 25 | 292,462 | 161,280 | 18,689 | $0.963020 | 3,861 |

Every trace had zero conformance violations. The first audit found that the
original forward primers exceeded the 72-degree maximum under every
contiguous input match. It also found that the original primer pairs exceeded
the five-degree difference limit. The audit repaired the primers and checked
both the intended and maximal-match interpretations before completion. The
task-owned grader accepted the resulting artifact.

The three attempts are retained under
`/home/sunil/git/foe-input-derived-final-artifact-audit/target/terminal-bench-jobs/priority-input-derived-boundary-activation-20260826T221840Z`.
Their campaign manifest SHA-256 is
`b15e154fcfb244541301df8a7b6c3f9b8008a6bd99d3227fff293f8d410e199f`.
The candidate converted the matched closed-book DNA result from three failures
to three successes.

An unchanged closed-book `git-multibranch` transfer attempt also received
score 1.0 without an exception. It used 20 model calls, 148,853 input tokens,
62,976 cached-input tokens, and 16,426 output tokens. Its estimated cost was
$0.697218. The retained directory is
`/home/sunil/git/foe-input-derived-final-artifact-audit/target/terminal-bench-jobs/priority-input-derived-boundary-transfer-20260826T224856Z`.
Its campaign manifest SHA-256 is
`b68a43e95dcda0580ccf78b19798b860217a0eb23b98a58037259f02ffa626d6`.

The candidate satisfies one campaign conversion criterion and one unrelated
transfer check. It does not count as an autonomous improvement because direct
source review selected and implemented it. The failed autonomous attempt and
the repaired trajectory sensor establish the next self-improvement test: a
future candidate must derive its intervention from concrete failure operands
and pass unchanged external activation cases.

## Joint-decomposition failure on `dna-insert`

The first `dna-insert` transfer attempt used the frozen final-artifact audit
candidate but invoked the bare single-episode runner. The attempt received
score 0.0. Its result is excluded from the active campaign because the scored
release requires the built-in implementation and terminal-audit workflow. One
model call lacked provider usage, so its token totals and estimated cost are
also incomplete. The retained directory is
`/home/sunil/git/foe-input-derived-final-artifact-audit/target/terminal-bench-jobs/priority-final-artifact-dna-insert-transfer-20260826T230614Z`.
Its campaign manifest SHA-256 is
`82d46463e3b6d42e4c94954fbe62b8974776cf379a14c0b4d28cdcb0e0074899`.

The scored development, confirmation, calibration, and sealed-holdout Bazel
targets now select the built-in workflow in their target definitions. A caller
cannot omit the workflow flag while using a scored target. Commit `cf3c486`
contains the target and documentation correction.

A governed `dna-insert` attempt then used the same source tree and binary as
the successful `dna-assembly` candidate. The implementation used low
reasoning. The terminal audit used high reasoning. Every model request used
GPT-5.6 Sol and the priority service tier. Token limits were measurement-only.
The unchanged task-owned grader awarded score 0.0 without an exception.

The run used 25 model calls, 245,492 input tokens, 103,424 cached-input tokens,
and 16,960 output tokens. Its estimated cost was $0.948842. The trace had no
conformance violation. The retained directory is
`/home/sunil/git/foe-input-derived-final-artifact-audit/target/terminal-bench-jobs/priority-final-artifact-dna-insert-governed-20260826T231042Z`.
Its campaign manifest SHA-256 is
`90bc92f9971748af417c61da4571825c17cfa4d183c5858d83be874770a3a75f`.

The terminal audit found three possible boundaries between the supplied
insert and the primer annealing regions. Its final check combined a reverse
primer measurement from one boundary with a forward primer measurement from
another boundary. Under the task-owned grader's single decomposition of the
complete primer pair, two nucleotides assigned to the reverse annealing region
by the audit belonged to the matching insert. The resulting primer
temperatures differed by 5.787026 degrees Celsius, above the permitted five
degrees.

This failure identifies a general audit requirement. When multiple properties
depend on a shared decomposition of a final value, the audit must evaluate the
properties under one jointly consistent decomposition. Measurements from
different candidate decompositions cannot establish that the final value
satisfies the combined requirements. A repeated closed-book attempt is needed
before this result counts as a reproducible activation failure.

## Fresh execution evidence and decomposition experiments

The fresh-execution evidence candidate binds every passed requirement to
successful executable results produced after the final write. A configured
verifier requires one successful preflight result. A run without a configured
verifier requires two executable results from separate turns. The runtime
reconstructs each cited call from the episode log before allowing completion.

The candidate is commit `ada29bd`. Its Git tree is
`git-tree-sha1:52c5ee22c9fc5b60b70d1d0572979ed9c7964942`. The evaluated
binary is
`sha256:da256fa374979df27450a5a048df725e01ca7dfff10d4d2850dbc695d303a700`.
Workspace tests, clippy checks, examples, formatting, and line budgets pass.

Two priority-tier closed-book `dna-insert` attempts produced different task
scores. Both used the unchanged task-owned grader.

| Attempt | Score | Calls | Input | Cache read | Output | Estimated cost | Trace assertions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| First | 1.0 | 22 | 185,366 | 87,040 | 14,078 | $0.709680 | 16,877 |
| Repeat | 0.0 | 28 | 278,583 | 146,944 | 13,508 | $0.855494 | 7,997 |

Both traces had zero conformance violations. The repeated audit cited two
fresh executable checks. Both checks encoded the same decomposition of the
primer pair. The task-owned grader used one complete decomposition and found
a melting-temperature difference above five degrees Celsius. Fresh execution
evidence established provenance without establishing semantic independence.

The attempts are retained under:

- `/home/sunil/git/foe-dna-evidence-boundary-direct-candidate/target/terminal-bench-jobs/priority-evidence-boundary-dna-insert-first-20260827T012543Z`;
- `/home/sunil/git/foe-dna-evidence-boundary-direct-candidate/target/terminal-bench-jobs/priority-evidence-boundary-dna-insert-repeat-20260827T013224Z`.

Their campaign manifest SHA-256 digests are
`8f925922dd5a59fba9be73e008d8c6679e1cee8e127effc554594ca869f993e0`
and
`fee8e92d6f50639b0b8064ef7d077f2275e33737915d25f56668205fccd473aa`.
The candidate fails the repeatability gate and remains rejected for
promotion.

One priority-tier variant raised the audit from high to xhigh reasoning. It
received score 0.0 after 28 calls. The run used 297,941 input tokens, 152,064
cached-input tokens, and 22,458 output tokens. Its estimated cost was
$1.093494. The trace passed 9,651 assertions with zero violations. Increasing
reasoning effort left the shared-decomposition defect intact.

The xhigh attempt is retained under
`/home/sunil/git/foe-dna-evidence-boundary-xhigh-audit-candidate/target/terminal-bench-jobs/priority-evidence-boundary-xhigh-audit-dna-insert-20260827T014336Z`.
Its campaign manifest SHA-256 is
`28c7dfb186963db340edd490e1a416cae4fc556544019c5ed1955f9301b3f609`.

A three-review-stage experiment then added a fresh xhigh terminal challenge.
The challenge received the original task and the audit's typed acceptance map.
It found three supported insertion boundaries. It still measured the forward
and reverse regions independently and accepted an artifact that failed the
task-owned grader. The attempt received score 0.0 after 39 calls. It used
453,770 input tokens, 201,728 cached-input tokens, and 35,283 output tokens.
Its estimated cost was $1.794519. The trace passed 13,099 assertions with zero
violations.

The three-stage experiment is retained under
`/home/sunil/git/foe-terminal-evidence-challenge-candidate/target/terminal-bench-jobs/priority-terminal-evidence-challenge-dna-insert-20260827T020138Z`.
Its campaign manifest SHA-256 is
`61c366e5924353f2e0fbbd638ceaf8b03ff0f01bdc0459fab56c3fb1291422bf`.
Commit `f938b3d` identifies the rejected candidate.

A smaller experiment retained two workflow stages and strengthened the audit
contract. It required each interpretation to partition one complete
consumer-visible value and derive every dependent measurement from that
partition. The audit instead selected maximal forward and reverse binding
regions independently. The task-owned grader measured a 5.828321-degree
difference and awarded score 0.0.

The attempt used 25 calls, 270,661 input tokens, 137,216 cached-input tokens,
and 17,662 output tokens. Its estimated cost was $0.941906. The trace passed
12,344 assertions with zero violations. The retained directory is
`/home/sunil/git/foe-joint-consumer-decomposition-candidate/target/terminal-bench-jobs/priority-joint-consumer-decomposition-dna-insert-20260827T022055Z`.
Its campaign manifest SHA-256 is
`b56a134882a5397e9e59c57c5c56178ad8ce6d6d5c3ff76222fe90cd5a583cee`.
Commit `346acf4` identifies the rejected candidate.

These experiments reject further task-specific prompt specialization. The
models found the ambiguity and built substantial checks, but their checks
continued to share an incorrect semantic decomposition. The next experiment
uses the qualified public checker through `done_when.verify` and measures
corrective convergence separately from standard closed-book quality.

## Verifier-governed `dna-insert` correction

The qualified public `dna-insert` checker requires one shared interpretation
for the inserted sequence and both annealing regions. Its SHA-256 is
`34b6d43b3cc9eda9ef0111911751847c17ba26ee124cb6bc7a2d2e74c6f4e22b`.
The checker passes its author oracle and the unchanged task-owned grader also
accepts that oracle. The checker fails an untouched workspace.
The qualification report is retained at
`target/terminal-bench-verifier-controls/controls-20260826T234648Z/verifier-controls.json`.
Its SHA-256 is
`2f7d24d0b20b8340769947ef93cdc2ceb0fda24ab70676bec669fd14c605c0ed`.

One priority-tier run supplied the checker to the fresh-execution evidence
candidate through the terminal audit's `done_when.verify`. Four typed returns
were rejected before completion. The recorded findings covered unsuccessful
checker evidence and evidence that no longer ended at the latest executable
or write result. The audit continued repairing the artifact after each
rejection. Its final authoritative verification recorded no finding.

The unchanged task-owned grader awarded score 1.0 without an exception. The
run used 60 model calls, 1,095,778 input tokens, 759,808 cached-input tokens,
and 30,596 output tokens. Its estimated cost was $2.259723. The trace passed
28,003 assertions with zero violations. Foe completed on the final available
model call because completion is checked before budget exhaustion.

The retained directory is
`/home/sunil/git/foe-dna-evidence-boundary-direct-candidate/target/terminal-bench-jobs/priority-evidence-boundary-dna-insert-done-when-20260827T023046Z`.
Its campaign manifest SHA-256 is
`787a128c8ac9ac9e5ede3be82ca0a38242e57bced247acedc81488ab3edb2537`.

This result establishes corrective convergence under a declared semantic
checker. It remains outside standard Terminal-Bench scoring because the
checker changes the information available during execution. The result also
isolates the closed-book limitation: structural evidence checks improve
provenance, while semantic correctness requires either a trusted verifier or
a model-generated check that independently captures the task's true
acceptance rule.

## Autonomous source-review capacity failure and workflow repair

An identity-bound source-improvement attempt used the repeated `dna-insert`
contrast and retained assessment feedback for a rejected source candidate. All
model requests used GPT-5.6 Sol. The attempt used the default service tier, so
it does not contribute to the priority-tier campaign gates.

Diagnosis completed in one model call. Initial source implementation completed
in 47 calls and produced changes to runtime source, regression tests, and two
specifications. The mutating source reviewer then spent its complete 44-call
allowance. The verifier-owned finalizer spent its complete 16-call allowance
and ended exhausted. The external source checker rejected the artifact because
the kernel contained 5,361 lines against an allowed 5,350 lines. No other
acceptance finding remained.

The attempt used 108 model calls, 7,178,220 input tokens, 5,788,160 cached-input
tokens, and 48,969 output tokens. Its estimated cost was $8.854884, and it ran
for 1,279.629 seconds. The retained directory is
`target/dna-joint-decomposition-actionable-confinement-self-improvement`.
The candidate remains rejected.

This failure identifies a workflow-owned capacity defect. Source repair had
two owners after implementation. The reviewer could consume most acceptance
capacity while continuing to mutate the candidate. The finalizer then had too
little capacity to satisfy the authoritative checker.

The repaired workflow preserves the 140-call root allowance and the existing
five-episode bound. Its independent reviewer has no source-write authority. It
returns bounded findings within 20 calls and can write only validation output.
The finalizer is the sole source-repair owner after review and has 40 reserved
calls. A blocked or exhausted review still contributes its declared empty
handoff, so finalization retains its complete allowance.

A no-spend preview validated the repaired program against source tree
`git-tree-sha1:cb1efa794591cbbe9733ec86a7779bfbe0ff9ecd` and runtime binary
`sha256:b6910a230b5c935e2140f767a8e15fe0cadb90a51190b4c0cd2d7a78b8073c72`.
The preview selected GPT-5.6 Sol with low reasoning for diagnosis and
implementation, xhigh reasoning for review and finalization, and the priority
service tier. It made no model request. An autonomous source-improvement run
must establish whether the repaired ownership structure produces an accepted
candidate.

### Priority-tier implementation handoff failure

The priority-tier source-improvement run completed diagnosis in one request.
The implementation child changed twelve source, test, schema, and specification
files. It then exhausted its 60-call allowance before returning the typed
implementation handoff. Strict workflow propagation ended the root episode, so
the read-only reviewer and verifier-owned finalizer never started.

The attempt used 61 model calls, 3,260,584 input tokens, 2,802,688 cached-input
tokens, and 10,932 output tokens. Its estimated cost was $3.171299, and it ran
for 456.818 seconds. The external source checker found one formatting defect.
The retained directory is
`target/priority-read-only-review-source-improvement`. The candidate remains
rejected.

This result narrows the workflow defect. Separating review from repair protects
finalization capacity only when implementation returns a value. The workflow
now gives the implementation node a declared empty value for blocked and
exhausted endings. Review and finalization can inspect the shared source tree
and apply the authoritative checker even when the implementation summary is
missing. The source checker still rejects an absent or invalid artifact.

## Autonomous repository-validation candidate rejection

The repaired self-improvement workflow repeated the same identity-bound source
request with the priority service tier. Diagnosis used one call, implementation
used 30, read-only review used seven, and verifier-owned finalization used one.
The workflow completed in 483.273 seconds. The external source checker accepted
the candidate with no finding.

The source-improvement run used 39 model calls, 1,033,187 input tokens, 618,496
cached-input tokens, and 14,359 output tokens. Its estimated cost was $2.193342.
The retained directory is
`target/priority-incomplete-handoff-recovery-source-improvement`. The source
bundle identity is
`sha256:5a40dfc2191718fbbfba59b1c66679f0dc1bc25f46277fd4f9e900ee0b767150`,
and the source candidate identity is
`sha256:cd3280514a613c3d040822d861aba51744c06a2b061971bfaa24483ee14611b8`.

The candidate required terminal audits to distinguish repository-native
validation from auditor-authored checks. It changed runtime source, regression
tests, the built-in workflow, and two specifications. A separate controller
rebuilt Git tree
`git-tree-sha1:8d1080552b238db369634b46f0b4cc78b31a2547` into portable binary
`sha256:979601fa0f00aef64cfb5e0e116f79bfd2301cc54ab40169c9da846ff07d6512`.

One unchanged priority-tier `dna-insert` activation attempt received score 0.0
without an exception. It used 32 model calls, 504,191 input tokens, 292,864
cached-input tokens, and 17,069 output tokens. Its estimated cost was $1.303834.
The trace passed 10,164 assertions with no violation. The campaign manifest is
`target/terminal-bench-jobs/priority-autonomous-native-validation-dna-insert-activation-20260827T032258Z/campaign.json`.
Its SHA-256 is
`55217667749cb584176b9ecf79507c14e6e544bcc0a5158b7334f489d4b4eabc`.

The audit reported that no repository-native check existed. It cited one
auditor-authored script for every semantic requirement. The script measured
the reverse annealing region at 60.954190 degrees Celsius. The unchanged grader
measured the complete reverse annealing region at 59.311917 degrees Celsius.
The resulting pair differed by 5.828321 degrees Celsius, above the permitted
five degrees.

The candidate is rejected. Its mechanism never constrained the activation
case because the audit classified native validation as unavailable. At this
decision point, the controller excluded completed artifact rejections from
source-change evidence because they did not isolate a Foe source mechanism.
Later confirmation evidence distinguished false acceptance by Foe's built-in
terminal audit from the implementation error. The controller now admits that
repeated, Foe-owned mechanism as a source hypothesis. Unchanged external task
evaluation remains the promotion authority.

## Development gate for the final-artifact and live-state release

The candidate combines four general changes. Terminal audits require evidence
from final artifacts. Their inspection boundary derives from the final
artifact rather than an implementation summary. Audits require required live
services to remain available. The OpenAI transport retries the provider's
`service_unavailable_error` response through the existing bounded recovery
policy.

The repository test suite, Clippy, example suite, line-count check, and
portable-binary build passed before task evaluation. The exact portable binary
also passed provider-free installation in the `dna-assembly`,
`git-multibranch`, and `gpt2-codegolf` task containers.

One provider-free admission check stopped because host memory was below the
declared ten-gibibyte minimum. It made no model request. Idle Bazel servers
from completed worktrees retained more than five gibibytes. Shutting down
those named servers restored more than fourteen gibibytes of available
memory. No repository output or retained task evidence was removed.

An initial DNA attempt used binary
`sha256:896c09d885bc607ed3d4b1b202340e6d7ad5be2eeb5f2f296c9622e3d2a7ccbc`.
The provider returned `service_unavailable_error`, which the transport did
not classify as retryable. The runtime failed before producing task-quality
evidence. That failure produced the bounded retry change and is excluded from
the frozen-release quality count.

The frozen release has these identities:

- source commit
  `c149a340960dd80d27ee7d45664989fbaa37544e`;
- source tree
  `git-tree-sha1:54c16fadf8446dd9de3bce95ebc205cf13ed4034`;
- portable binary
  `sha256:9225331114a53308b8dee8891ef66d36ba968a1c1e78220f076ebfbaf7cbb9f7`;
- task registry
  `sha256:067fbaed267283bf44abd588e6309bb8efe0224d343f9ffe3d7427f2c7f74158`;
- campaign runner
  `sha256:a6af2f565c69a36b473581064b54871ae8143956d11b6fad6ff00f96d177f2bd`;
- Harbor adapter
  `sha256:9d34fb4a0467bdacdf36e5f19ba7d2f12398402a71c44c56e8452922060770b6`;
- diagnostic collector
  `sha256:dade331cd8267ac22aba83e8815030afe8007368a97d7aed506423a8609d0813`.

Every request used GPT-5.6 Sol and the priority service tier. Implementation
used low reasoning. The runtime-owned terminal audit used high reasoning.
Token limits remained measurement-only. Landlock remained off inside the
task containers. The task-owned verifiers remained unavailable until Foe
exited.

The release passed all three activation cases and eleven of the twelve
development tasks:

| Task | Score | Calls | Input | Cache read | Output | Estimated cost | Usage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `cancel-async-tasks` | 1.0 | 10 | 41,236 | 4,608 | 9,762 | $0.343595 | complete |
| `dna-assembly` | 1.0 | 44 | 691,968 | 409,088 | 28,932 | $1.873795 | complete |
| `fix-git` | 1.0 | 23 | 174,320 | 72,192 | 7,471 | $0.586809 | complete |
| `fix-ocaml-gc` | 1.0 | 30 | 1,014,307 | 500,736 | 8,506 | $2.424698 | complete |
| `git-multibranch` | 1.0 | 15 | 109,520 | 36,352 | 15,447 | $0.616153 | complete |
| `gpt2-codegolf` | 1.0 | 50 | 1,443,797 | 963,584 | 36,127 | $3.028826 | complete |
| `large-scale-text-editing` | 1.0 | 15 | 84,785 | 28,160 | 11,587 | $0.469504 | complete |
| `model-extraction-relu-logits` | 1.0 | 15 | at least 78,558 | at least 24,576 | at least 10,165 | at least $0.429058 | one call omitted |
| `path-tracing-reverse` | 1.0 | 44 | 1,910,590 | 1,329,664 | 12,226 | $3.100090 | complete |
| `regex-chess` | 1.0 | 72 | 2,613,901 | 1,961,472 | 50,206 | $4.398425 | complete |
| `sanitize-git-repo` | 0.0 | 30 | 1,453,185 | 973,824 | 21,508 | $2.737134 | complete |
| `sqlite-db-truncate` | 1.0 | 14 | 56,521 | 18,432 | 8,989 | $0.339509 | complete |

The twelve tasks used 362 model calls. Recorded usage is at least 9,672,688
input tokens, 6,322,688 cached-input tokens, 220,926 output tokens, and
$20.347595. One successful `model-extraction-relu-logits` request omitted
provider usage. Its other fourteen requests provide the lower bound shown in
the table. The task attempts occupied 7,205 seconds in the serial lane.

Every attempt produced a task-owned score without an exception. Every Foe
account was conformant with zero violations. Eleven Foe outcomes agreed with
their task-owned scores. The `sanitize-git-repo` outcome was completed, while
the external score was 0.0. Its diagnostic record marks the artifact and
outcome mismatch.

The sanitization removed the identified credentials from tracked files and
reachable history. It also expired reflogs and pruned unreachable objects.
Two verifier tests passed. The remaining verifier test tried to resolve the
secret-bearing original commit before comparing unchanged paths. Object
pruning made that hard-coded commit unavailable, so the comparison never ran.
The 0.0 remains in the quality result. Foe receives no task-specific change
for this verifier assumption.

The quality result is 11/12. The frozen release advances to the two-worker
qualification and confirmation gates without a source or configuration
change. Confirmation tasks remain unopened at this decision point.

Raw episodes, workspaces, and credentials remain in local-only storage under
the `foe-terminal-bench-quality-release` evidence worktree. Git retains these
campaign-manifest SHA-256 digests:

- DNA activation:
  `59a2f9a97f017454b1e57351098ac37de618a3c6c9e778213b32ef0bc472f480`;
- live-state activation:
  `28188ca549f3004a7d69942931ba9c2a04fbdcab2c9fea8be01ba2553e818153`;
- GPT-2 activation:
  `6ab54906c28e79514a63b80f3cee54f95f456c3e22f548039e530869873b6a41`;
- remaining development tasks:
  `0414fde9af51caf021623bfdaca3fc7a189e5edcd4f47cc34c0b1d71c3cedef6`.

## Access-only credential release requalification

Two-worker execution gives each worker an isolated access-only OAuth token
file. The prior binary rejected those files during configuration because it
required a refresh token before any request needed renewal. The revised
transport accepts an access token through its recorded expiry. It reports a
local renewal error if the token later expires without refresh authority.

The revised release has these identities:

- source commit
  `bdae366634b85d12518e777d865f7bb07ba7e88e`;
- source tree
  `git-tree-sha1:df82cf9c80eaefedc06fdd7a25aab35bf045af29`;
- portable binary
  `sha256:701e7546a52fdad91692faa807c0a2e9ef2bbcc1870a1f9dd54cdecb08a8dcff`;
- task registry
  `sha256:067fbaed267283bf44abd588e6309bb8efe0224d343f9ffe3d7427f2c7f74158`.

The workspace tests, Clippy checks, example suite, line-count check, and
portable-binary build passed. A provider-free task-container check installed
and planned the exact binary. A separate expired-token probe reached the
recorded access-only renewal error and preserved a complete episode account.

Every assessed request used GPT-5.6 Sol and the priority service tier.
Implementation used low reasoning. The runtime-owned terminal audit used high
reasoning. Landlock remained off inside the task containers. Token use
remained measurement-only.

The release passed all twelve development tasks:

| Task | Score | Calls | Input | Cache read | Output | Estimated cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `cancel-async-tasks` | 1.0 | 7 | 20,939 | 1,536 | 5,846 | $0.195146 |
| `dna-assembly` | 1.0 | 41 | 952,661 | 618,496 | 31,436 | $2.212778 |
| `fix-git` | 1.0 | 14 | 94,577 | 27,136 | 6,600 | $0.412618 |
| `fix-ocaml-gc` | 1.0 | 27 | 752,632 | 324,608 | 8,200 | $2.005939 |
| `git-multibranch` | 1.0 | 15 | 102,936 | 20,480 | 14,761 | $0.633236 |
| `gpt2-codegolf` | 1.0 | 35 | 593,280 | 306,176 | 21,292 | $1.696726 |
| `large-scale-text-editing` | 1.0 | 18 | 119,151 | 45,568 | 11,257 | $0.537699 |
| `model-extraction-relu-logits` | 1.0 | 25 | at least 148,201 | at least 65,536 | at least 13,831 | at least $0.633494 |
| `path-tracing-reverse` | 1.0 | 43 | 2,885,297 | 2,019,328 | 20,600 | $4.683607 |
| `regex-chess` | 1.0 | 69 | at least 1,427,456 | at least 927,232 | at least 26,725 | at least $2.906289 |
| `sanitize-git-repo` | 1.0 | 24 | at least 717,230 | at least 349,696 | at least 21,800 | at least $2.046014 |
| `sqlite-db-truncate` | 1.0 | 10 | 50,364 | 4,096 | 10,849 | $0.403690 |

The twelve tasks used 328 model calls. Recorded usage totals at least 7,864,724
input tokens, 4,709,888 cached-input tokens, 193,197 output tokens, and
$18.367239. Twenty retryable provider errors omitted usage. Every successful
response reported usage, and every episode settled all recorded model calls.

Every task received score 1.0. Every account passed conformance with zero
violations. The sanitization task converted the preceding release's only
development failure. The revised run changed the contaminated working-tree
files while preserving repository history outside the requested edits.

The four-task manifest has SHA-256 digest
`72767aee1787337b814a6613c1e7c7f99b59c7c89ac58da922d07ba56ca1e041`.
The eight-task manifest has SHA-256 digest
`dacbea45a0834ea04769f5ef90dcaeb12e1365383f405c66b88404065076621b`.
Together they constitute the 12/12 development qualification for this exact
release.

### Two-worker result

The first two-worker request selected serial execution because available
memory was below the declared parallel threshold. Its manifest has SHA-256
digest
`1f17c20bc85cdb2d344bf890f241c5248cb04857e06d2681db26dce4e9068619`.
That run provides four additional task successes and no concurrency evidence.

The campaign runner now supports required parallel admission. The option
stops before Harbor or provider execution when two workers cannot start. A
provider-free low-memory check made no model request and produced manifest
digest
`46ad7f4b4255d3bf1d1f2adcfc43d7c46cac855c738d3552b1645548acdb6063`.
The controller change does not alter the evaluated release or its model-visible
program.

A strict two-worker run then used isolated access-only credentials and the
priority service tier. All four tasks received score 1.0. Every account passed
conformance, the parent credential remained unchanged, and no worker received
refresh authority. The manifest has SHA-256 digest
`2c4f7852b4cfa32704e9ae6cfb60c149631b3dbeac803c921772420f247e55af`.

The serial batch completed in 935.817721 seconds. The strict two-worker batch
completed in 818.906006 seconds. The 12.493 percent reduction misses the
required one-third reduction. Eighteen retryable provider overload responses
occurred during concurrent execution and increased task durations.

The result qualifies two-worker credential isolation, score preservation, and
evidence preservation. It leaves the makespan criterion open. Another matched
four-task run requires a specific overload-reduction mechanism or a different
predeclared workload rationale. Repeating the same configuration would spend
provider capacity without testing a changed hypothesis.

## Confirmation rejection for the access-only credential release

The confirmation gate evaluated the same source tree and portable binary. It
used GPT-5.6 Sol with low reasoning for implementation and high reasoning for
the built-in terminal audit. Every provider request used the priority service
tier. Token use remained measurement-only, and the tasks ran serially.

The gate stopped after twelve scored attempts because the required fourteen
successes from sixteen attempts had become unreachable. The remaining four
attempts could raise nine successes to no more than thirteen. The controller
interrupted the next unscored task and left the final task unstarted.

| Task | Score | Calls | Input | Cache read | Output | Estimated cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `build-pov-ray` | 1/2 | 80 | at least 1,047,513 | at least 567,808 | at least 16,452 | at least $2.474983 |
| `caffe-cifar-10` | 2/2 | 90 | 4,452,855 | 2,343,424 | 34,877 | $10.072634 |
| `configure-git-webserver` | 1/2 | 46 | 389,020 | 89,600 | 32,095 | $1.875420 |
| `count-dataset-tokens` | 2/2 | 58 | 578,633 | 220,160 | 36,612 | $2.254196 |
| `crack-7z-hash` | 2/2 | 44 | 439,037 | 210,944 | 11,571 | $1.228170 |
| `dna-insert` | 1/2 | 45 | at least 151,869 | at least 31,744 | at least 13,721 | at least $0.767618 |

The twelve scored attempts used 363 model calls. Recorded usage totals at
least 7,058,927 input tokens, 3,463,680 cached-input tokens, 145,328 output
tokens, and $18.673020. Four retryable provider errors omitted usage.

The interrupted `log-summary-date-ranges` attempt used six additional model
calls. Five calls reported 29,765 input tokens, 7,168 cached-input tokens, and
1,008 output tokens. The attempt has no score and does not contribute to the
quality result. `overfull-hbox` remained unstarted.

Every scored attempt produced a task-owned result without an infrastructure
exception. All twelve scored accounts passed 235,657 conformance assertions
with no violation. The three failures were completed outcomes whose external
task verifiers rejected their artifacts.

The failed `build-pov-ray` attempt built an official source ZIP. The task
required provenance from the Unix distribution archive. A successful attempt
selected the required archive and preserved its provenance file.

The failed `configure-git-webserver` attempt exercised a live clone, push,
hook, and HTTP request successfully. Its audit then reset the bare repository
and emptied the web root during cleanup. The external verifier observed HTTP
404. The successful attempt preserved the requested repository and web
artifacts after exercising them.

The failed `dna-insert` attempt accepted an alternative circular alignment
that moved two matching boundary bases between the declared insert and the
input-derived annealing regions. Its forward and reverse melting temperatures
differed by 6.531905 degrees Celsius. The successful attempt preserved the
declared insert as one semantic operand and remained within the five-degree
limit.

The release is rejected at confirmation. These paired successful and failed
trajectories become identity-bound evidence for targeted self-improvement.
They do not authorize another unchanged qualification or confirmation run.
Only a source candidate that changes one diagnosed mechanism may repeat its
affected activation and transfer cases.

The retained campaign manifest has SHA-256 digest
`c4f0401121c347169ee86ccd983148032f331e39e116e03914df3de63cb4be10`.
Raw trajectories, task workspaces, and verifier artifacts remain in the local
evidence worktree.

## Confirmation failure repeatability

One additional serial attempt ran for each of two confirmation failures. The
source tree, portable binary, task definitions, model settings, and service
tier remained unchanged. `configure-git-webserver` received score 1.0, which
makes its observed result two successes from three attempts. No further
unchanged webserver attempt is justified.

The additional `dna-insert` attempt received score 0.0. The two retained
failures and one success establish a repeated false-acceptance contrast for
the exact frozen release. All three outcomes came from the unchanged task
verifier.

| Task | Score | Calls | Input | Cache read | Output | Estimated cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `configure-git-webserver` | 1/1 | 21 | 280,093 | 93,696 | 16,196 | $1.106986 |
| `dna-insert` | 0/1 | 19 | 168,586 | 67,072 | 12,908 | $0.691045 |

The two attempts used 40 model calls, 448,679 input tokens, 160,768
cached-input tokens, and 29,104 output tokens. Their estimated cost was
$1.798031. Both accounts passed 40,372 conformance assertions with no
violation. Neither attempt had an infrastructure exception or missing usage.

The repeated DNA failure exposed a second boundary interpretation. The final
primer pair contained an input-derived region, the declared insert, and
another input-derived region. Overlapping bases allowed the insert to be
located at more than one offset in the concatenated value.

The terminal audit declared one offset unique. It measured a 28-base reverse
annealing region and reported 58.041322 degrees Celsius. The external verifier
located the earlier complete insert occurrence. It measured a 26-base reverse
region at 55.857347 degrees Celsius, below the required minimum.

The implementation and audit artifacts encoded the same concatenated
sequence. The audit moved the complete insert between primer overhangs without
changing the verifier-visible decomposition. The causal defect is therefore
false acceptance of one ambiguous operand boundary. It is not an audit edit
that converted a passing artifact into a failing artifact.

The diagnosis collector retained only the three DNA trajectories. It bound
them to the frozen source tree, runtime binary, execution configuration, and
verifier reports. The evidence has SHA-256 digest
`7bf0fdd2ce9b7cac0355c3f141ea27c8ffabe23c9bea453480efbfad3ca2d13e`.
Its repeated-failure contrast identity is
`sha256:12a4a949dced15e394cc485abe766cd50cc13cea8a76ddfed157f40fe2c2817d`.

The self-improvement controller formerly rejected every source hypothesis
derived from completed artifact failures. The repeated audit false acceptance
isolates Foe-owned behavior, so that blanket exclusion prevented a supported
improvement. The controller now permits this source hypothesis. It still
requires two failed episodes, one successful episode, complete verifier loci,
one shared mechanism, repository validation, and unchanged external task
evaluation.

This controller change does not alter the evaluated release or its
model-visible program. It therefore does not invalidate any completed
qualification. The repeatability campaign manifest has SHA-256 digest
`83b8acfb95fc3df4295478856aaae5c46d20e6940c5ea576b3b6ddac8df70600`.

## Identity-bound source candidate for completion auditing

The self-improvement workflow consumed only the three retained `dna-insert`
trajectories. The corpus contained two failures and one success from the exact
frozen release. Its evidence report has SHA-256 digest
`7bf0fdd2ce9b7cac0355c3f141ea27c8ffabe23c9bea453480efbfad3ca2d13e`.

Every model request used GPT-5.6 Sol with xhigh reasoning and the priority
service tier. The workflow used 59 model calls over 1,259.183 seconds. It
recorded 6,221,102 input tokens, 4,453,888 cached-input tokens, 54,743 output
tokens, and $9.945271 in estimated cost. Token use remained measurement-only.

The diagnosis selected a source-level intervention for repeated false
acceptance by the built-in terminal audit. The implementation added a fresh
completion-falsification node after the existing audit. The node receives the
task and the preceding audit account. It must seek counterexamples, recompute
quantitative claims, and identify uncovered requirements before completion.
The node owns `done_when.verify` for the built-in workflow.

The source candidate changed the built-in workflow, its Rust integration,
tests, and the relevant specifications. The workflow's review found that the
new prompt could request `block` while the node lacked that tool. Finalization
added the tool and a regression test before the repository checker accepted
the candidate.

The first post-episode checker encountered `Text file busy` in an unrelated
executable-transport test. The transport tests use shared scratch executable
paths, so overlapping checker processes can expose that race. The unchanged
candidate passed the same complete repository checker immediately afterward.
No model request was repeated.

The retained revalidation record has SHA-256 digest
`74ff4c85dfa1fb051ee31ad5809fc9a25f64445a8394fd091c75fe1a04077b10`.
It binds the original result digest, checker digest, unchanged source
candidate, and successful checker outcome. The source candidate identity is
`sha256:9a81dae2872875a502c7d0dcba607c1867935b7c8d9a1504d3c36bacc4cb657b`.
The source bundle identity is
`sha256:e0a2fa055443a166bc8d51f27eac9882cf2aed8787638322a3c6b45c52b8f17e`.

The candidate remains pending external evaluation. Its activation gate runs
only `dna-insert`, where the frozen release succeeded once in three attempts.
The gate reuses every retained release qualification and uses the priority
service tier. Promotion requires improved task-owned quality and conformant
accounts. A passing activation then permits one transfer case that exercises
the same completion-audit mechanism.

## Rejection of the completion-falsification source candidate

The source-evaluation controller committed the accepted candidate bytes on an
isolated branch. It built and retained a portable binary with SHA-256 digest
`c5b3573f173a27efe7f2a91535393bc3845a895d9fbf2833251539035c6572b4`.
The evaluated source tree is
`git-tree-sha1:9fb92690ac718dd7b84216bb592f37c5a5a6c81c`.

The activation selected only `dna-insert`. Every request used GPT-5.6 Sol and
the priority service tier. Implementation used low reasoning. The built-in
audit and generated falsification stage used high reasoning. Token limits
remained measurement-only.

The first two attempts both received task-owned score 0.0. The candidate could
therefore finish with at most one success from three attempts. That result
could only equal the frozen release's one success from three attempts. The
controller stopped during the third attempt after its implementation child
completed and its audit began. No qualification or unrelated task was rerun.

The two scored attempts used 67 model calls. Recorded usage is at least
667,055 input tokens, 305,664 cached-input tokens, 47,788 output tokens, and
$2.523590 in estimated cost. Two calls in the second attempt omitted provider
usage. The interrupted attempt used twelve calls and recorded at least
$0.247168 in additional cost before cancellation.

Both failures exposed the same remaining mechanism. The fresh stage found
multiple valid locations for the inserted sequence. It still evaluated
dependent primer lengths and melting temperatures by combining boundaries from
different locations. The external verifier preserved one complete location
and measured pair differences of 6.127779 and 5.828321 degrees Celsius. Both
exceeded the five-degree requirement.

The source candidate is rejected for quality and receives no transfer run. A
replacement must preserve each complete interpretation as one tuple while it
checks dependent constraints. It must repair the artifact until every valid
interpretation passes or the serialization makes the interpretation unique.

The source-adoption lineage succeeded for both scored attempts. The campaign
runner nevertheless marked their configuration claims invalid because its
built-in-profile diagnostic hard-coded the preceding two-node workflow and
120-call root budget. A source candidate is expected to change those values.
The diagnostic must validate campaign invariants against the rebuilt program
without assuming the candidate's topology.

The retained campaign manifest has SHA-256 digest
`9825b147dc113c7fe8c7b625620a9919e5f0a12810c33ede081d6235663f10fd`.
It records cancellation, the exact rebuilt source and binary pair, and two
completed source adoptions. Raw episodes and task workspaces remain in the
local evidence worktree.

## Cross-version feedback for a rejected source candidate

The rejected completion-falsification candidate recreated the mechanism of an
earlier rejected completion-audit candidate on a different parent source tree.
The self-improvement controller already had an identity-bound assessment of
the earlier candidate. A source-tree equality check prevented the diagnosis
from using that assessment with the frozen release.

The controller now allows the assessed parent and generation parent to
differ. The existing assessment remains the only feedback format. Its verified
patch is historical evidence for the diagnosis and remains unavailable to
coding nodes. The diagnosis still cites every assessed failure and success,
chooses a typed disposition, and generalizes its implementation handoff. The
generation context records both source-tree identities. Exact rejected
candidate identity and assessment-literal checks remain unchanged.

This consolidation avoids a second negative-memory schema, citation format,
and source-bundle binding. A no-spend plan must re-derive the private
assessment and expose the source-tree relationship before provider spend. The
plan must also select GPT-5.6 Sol, measurement-only token accounting, xhigh as
the maximum review effort, and the priority service tier.

The no-spend plan re-derived assessment diagnostics with identity
`sha256:dfbee599eb2697cdcd121cc79c33292ac899f8de1d93fb04999524235fba88e1`.
It recorded assessed parent tree
`git-tree-sha1:cb1efa794591cbbe9733ec86a7779bfbe0ff9ecd` and generation parent
tree `git-tree-sha1:df82cf9c80eaefedc06fdd7a25aab35bf045af29` as separate values. It
selected the required models, effort ceiling, token policy, and priority
service tier. No model request was made.

The controller and documentation changes do not alter the frozen release,
task registry, model-visible evaluated program, or task semantics. Completed
release qualification therefore remains valid. The next provider spend is one
identity-bound source-generation workflow. A resulting candidate receives
only its affected activation gate before any transfer or qualification work.

## Assessment-guided independent-evidence candidate

The source-improvement workflow used the rejected-candidate assessment across
the two recorded parent source trees. The diagnosis classified the earlier
instruction-level completion check as falsified. It proposed a runtime-owned
gate that requires two successful evidence events for each terminal-audit
claim when no authoritative verifier is configured.

Every model request used GPT-5.6 Sol and the priority service tier. Diagnosis
and implementation used low reasoning. Read-only review and finalization used
xhigh reasoning. The workflow used 42 model calls over 507.614 seconds. It
recorded 1,341,770 input tokens, 919,040 cached-input tokens, 18,177 output
tokens, and $2.422076 in estimated cost.

The repository checker accepted the source, regression tests, and affected
specifications with no finding. The candidate changes seven files. Its source
identity is
`sha256:1891321b8becdab3c9eb4dc29fdf05ba46ff6f23b32503cc25baf3a444f41ddc`,
and its source-bundle identity is
`sha256:0a88484ec5f99028ed96fd21f27b30e109c7fb191d48e75740f1015094c8ba88`.
The retained result has SHA-256 digest
`6d3d5a3c58f55161c8f5cc1bbd2391052e9d397a37a2b4b223ca04c7aaf3eebb`.

The candidate requires distinct event sequences and distinct model-supplied
method names. It also requires the audit to explain why both results measure
the same claim. The runtime can reconstruct the events and check their status.
It cannot establish that two method descriptions represent semantically
independent analyses. External task quality therefore remains the acceptance
authority.

## Rejection of the independent-evidence candidate

The source-evaluation controller rebuilt source tree
`git-tree-sha1:4b1cbc555e890a32addefb04b65a36a0847548ee` as portable binary
`sha256:4f60fbf4c3254e9dac5e6509f26f08c3b5adbc9d7ae67feec13190539be2ae9d`.
It evaluated only `dna-insert` with the unchanged task and task-owned grader.
Implementation used low reasoning, and the built-in terminal audit used high
reasoning. Every request used the priority service tier.

Both completed attempts received task score 0.0. They used 585,021 input
tokens, 215,552 cached-input tokens, 43,251 output tokens, and $2.429117 in
estimated cost. The already-started third attempt was cancelled after nine
requests with reported usage. It had recorded 61,602 input tokens, 23,040
cached-input tokens, and 3,357 output tokens.

The first audit cited two successful final checks and reported a melting-
temperature difference of 4.627650 degrees Celsius. The external verifier
preserved the earlier complete insert occurrence and measured 6.127779
degrees. The second audit also cited two successful checks. The external
verifier measured 5.828321 degrees, above the allowed maximum.

Both audits derived their evidence from the same incorrect insert boundary.
Distinct successful events and different method labels therefore supplied
correlated evidence for the same false interpretation. The candidate did not
improve on the frozen parent's one success from three attempts. It receives no
transfer run and no production pull request.

The first audit also supplied the correct 64 hexadecimal file-version digits
to `edit` three times without the `sha256:` prefix. Each call was rejected
before the audit recovered by adding the prefix. Pull request 110 makes this
unambiguous input equivalent to the canonical qualified version. The tool
continues to record and compare the complete SHA-256 value.

The cancelled campaign is retained under
`/home/sunil/git/foe-terminal-bench-quality-release/target/terminal-bench-jobs/priority-independent-audit-evidence-dna-activation/priority-independent-audit-evidence-dna-activation-20260827T145619Z`.
Its campaign manifest has SHA-256 digest
`e8fc02525b816d4c51040fabd50ab488feaa792c0f10579fc46ec5bbd5d1afe8`.

## Standard service tier after 2026-08-27

Provider-backed campaign runs after this decision use the standard service
tier. The Bazel campaign targets and the direct runner now select
`service_tier=default`. Earlier records retain their original tier because the
tier is part of each run's measured conditions.

No provider-backed run was active when the selection changed. The retained
frozen release and its completed qualifications remain unchanged. Every future
candidate manifest must record the standard tier before model spend begins.

## Model-facing tool usability audit

A read-only audit examined the canonical child episodes from scored
Terminal-Bench attempts. It inspected the built-in coding tools, configured
executables, team tools, return values, and workflow recovery. The audit found
three model-facing interfaces with retained trajectory evidence.

The edit tool rejected an exact 64-digit lowercase SHA-256 value unless the
model added the `sha256:` prefix. The corpus contains 83 such rejections in 40
child episodes and 31 scored trials. One `fix-ocaml-gc` episode repeated the
same correct value three times before recovering. Pull request 110 accepts the
bare and qualified encodings of the same complete digest. It continues to
reject malformed, truncated, different, and stale values without writing.

Seven scored trials began by asking the read tool to inspect the current
directory. The tool returned `Is a directory`, although its read capability
already permits bounded directory enumeration. At least three episodes spent
the next model request on a shell listing. Six of the seven trials eventually
succeeded. The one failed trial was a `dna-insert` source-candidate activation
whose retained verifier evidence identifies an unrelated primer-boundary
error. Pull request 119 lets the existing read tool return a bounded, sorted
listing of immediate entries. The result preserves descriptor-bound traversal
and the existing read grant.

One successful `fix-git` episode embedded U+0000 in a Bash command. Process
creation rejected the argument with the low-level message `nul byte found in
provided data`. The next complete model request repeated the audit with a
valid command. Pull request 120 validates the command before process creation
and explains that shell syntax such as `printf '\\0'` can create the byte in a
process stream. It does not rewrite the command because a replacement could
change shell token boundaries.

The audit found four additional consistency opportunities without scored
Terminal-Bench activation evidence. A configured executable can default an
omitted argument list to an empty list. Workflow recovery can derive the only
eligible target when the model omits it. Team messages can accept the member
identifier returned by team creation. Session waits can use one canonical
representation for a numeric session identifier. These changes remain
unimplemented until relevant trajectories or a dedicated design review
justify their effect on the model-visible surface.

Strict interfaces remain strict where they protect authority, target
selection, replay integrity, or output conformance. This includes exact edit
text, stale-version rejection, granted paths, absolute executable paths,
episode-local retrieval cursors, completion evidence references, recovery
choices among multiple nodes, and returned JSON schemas. All 127 retained
return calls succeeded, so the required `value` wrapper remains unchanged.

The usability fixes are independently reviewable. Their activation cases
already succeeded after recovery, so they do not establish a task-quality
gain by themselves. The next scored release candidate will include them only
after review. It will also contain a change that targets a repeatable external
task failure. This rule prevents a full candidate qualification from measuring
only reduced friction on tasks that already pass.

The release manifest will record the exact source commit, source tree, binary
digest, program identity, service tier, and included pull-request heads before
the first provider request. Completed activation evidence for live-state
preservation and final-artifact boundaries remains valid for the exact source
changes that produced it. The campaign will run only the affected activation
and transfer cases for new behavior before reopening the confirmation gate.

## Standard-tier live-state and tool-usability release gate

A combined source candidate starts from main commit
`89dd279b01f9e7b98082afa9debb7836f5eff271`. It contains the reviewed heads
of pull requests 99, 100, 110, 117, 119, and 120. The candidate therefore
combines bounded provider retry, live-state preservation, final-artifact
evidence, bare edit-version input, directory reads, and literal-NUL shell
diagnostics.

The merge reconciles live-state and final-artifact audit instructions. The
terminal audit must inspect every final requirement and preserve required
services and machine state after its checks. The candidate has these immutable
identities:

- source commit
  `1d90a79b059db864f9b3fe98f7b156abe152120c`;
- source tree
  `git-tree-sha1:f108a36aceee4c3dfeba84d29bdc0823bfca52d9`;
- portable binary
  `sha256:d6a97374ea95c5efece642b60bf7938abadae59214b894937bbdc2880bb4f6ed`.

The workspace test suite, Clippy, deterministic examples, recorded program
identities, line-count check, and Bazel portable build passed. The kernel uses
5,316 of 5,350 lines. The tools use all 1,770 allowed lines. The program
contract uses 1,399 of 1,400 lines. The exact portable binary passed
provider-free capability probes in the `dna-insert`, `dna-assembly`,
`git-multibranch`, and `fix-ocaml-gc` containers.

Three serial task attempts used GPT-5.6 Sol. Implementation used low
reasoning, and the built-in terminal audit used high reasoning. Every request
used the standard service tier. Token limits remained measurement-only.
Landlock remained off inside each task container.

| Task | Score | Calls | Input | Cache read | Output | Estimated cost | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `dna-insert` | 0.0 | 25 | 321,004 | 99,840 | 13,425 | $1.193092 | 414.542 |
| `git-multibranch` | 1.0 | 21 | 199,769 | 58,368 | 20,850 | $1.005951 | 488.601 |
| `dna-assembly` | 1.0 | 29 | 385,189 | 190,464 | 24,908 | $1.353246 | 697.092 |
| **Total** | **2/3** | **75** | **905,962** | **348,672** | **59,183** | **$3.552289** | **1,600.235** |

Every attempt produced a task-owned score without an exception. Every Foe
account conformed with zero violation, and every model response reported
usage. The successful `git-multibranch` result left the SSH and HTTPS services
live after the terminal audit. The task-owned grader accepted the externally
observable repository and web state.

The successful `dna-assembly` trajectory read `/app` and the current directory
through the new directory interface. It also supplied a complete bare SHA-256
digest to `edit`. Both interfaces succeeded without a tool error, and the
unchanged task-owned grader awarded full credit. A separate `fix-ocaml-gc`
attempt would repeat this interface evidence on another task that already
succeeds, so it is omitted.

The `dna-insert` attempt was an incorrect activation choice for these changes.
The terminal audit measured one insertion boundary and accepted a primer pair
whose forward and reverse temperatures differed by no more than five degrees
under that interpretation. The task-owned grader selected an overlapping
insert occurrence and measured a 5.397779 degree difference. The retained
`done_when.verify` result already establishes correction when the qualified
public checker supplies this semantic distinction. The closed-book failure
adds no evidence that the candidate regressed live-state, final-artifact, or
tool behavior.

The three campaign manifests have these SHA-256 digests:

- `dna-insert`:
  `0dab5121ad69f2efdadf4aca80c6eca21e7389546b1aee6f1884c4f79aa9b84d`;
- `git-multibranch`:
  `a5420dd59459202b0cc42293170de2dc8ecebbc9f5274261fdfb017965dd2a12`;
- `dna-assembly`:
  `4c443553314477af7279db51072d508663ac7ae06e63e3041a0036ba7ef561c0`.

The candidate advances to the remaining ten development tasks. The two
successful activation results count toward the twelve-task development total
because they use the exact candidate, unchanged task-owned graders, and the
same standard-tier execution contract. Confirmation remains closed until the
combined twelve-task result reaches at least eleven successes.

## Twelve-task development result

The exact portable binary completed the remaining development tasks serially.
Every provider-backed request used the standard service tier. Implementation
used low reasoning, and the built-in terminal audit used high reasoning. Token
limits remained measurement-only, and Landlock remained off inside each task
container.

| Task | Score | Calls | Input | Cache read | Output | Estimated cost | Wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `cancel-async-tasks` | 1.0 | 11 | 31,071 | 1,536 | 4,785 | $0.214454 | 149.008 |
| `fix-git` | 1.0 | 22 | 194,031 | 59,392 | 8,273 | $0.727773 | 224.875 |
| `sqlite-db-truncate` | 1.0 | 13 | 97,065 | 15,360 | 10,459 | $0.542144 | 253.210 |
| `sanitize-git-repo` | 0.0 | 24 | 854,327 | 463,872 | 27,490 | $2.297169 | 701.316 |
| `large-scale-text-editing` | 1.0 | 11 | 49,672 | 3,072 | 8,010 | $0.347829 | 289.933 |
| `gpt2-codegolf` | 1.0 | 48 | 1,670,917 | 1,183,232 | 40,644 | $3.236913 | 1,140.084 |
| `fix-ocaml-gc` | 1.0 | 22 | 579,420 | 213,504 | 6,723 | $1.683526 | 1,813.906 |
| `path-tracing-reverse` | 1.0 | 44 | 2,385,539 | 1,761,792 | 16,984 | $3.539385 | 459.573 |
| `regex-chess` | 1.0 | 32 | 700,901 | 439,296 | 28,447 | $1.791078 | 854.703 |
| `model-extraction-relu-logits` | 0.0 | 13 | 51,039 | 12,800 | 6,699 | $0.292056 | 207.837 |

Together with `git-multibranch` and `dna-assembly`, the frozen candidate
scored 10/12. The twelve score-valid attempts used 290 model calls, 7,198,940
input tokens, 4,402,688 cached-input tokens, 204,272 output tokens, an
estimated $17.031523, and 7,280.138 wall seconds. Resources remain diagnostic;
task quality governs promotion. Every Foe account conformed with no recorded
violation.

The `sanitize-git-repo` implementation changed only the three contaminated
working-tree files. The terminal audit broadened the requested repair into a
rewrite of all 108 Git commits and removed the original commit object. The
task-owned verifier accepted secret removal and replacement, then failed
because the baseline commit required for its unchanged-file comparison no
longer resolved. This is a harness-quality failure. A terminal audit must make
the smallest repair supported by recorded evidence and preserve baseline
identities that an external verifier can compare.

The `model-extraction-relu-logits` implementation recovered every row from
the supplied model. The audit independently established a complete one-to-one
match for that instance. The implementation also recorded assumptions about
the finite scan range and breakpoint separation. The task-owned verifier used
a different weight matrix and found three missing rows out of thirty. This is
a transfer failure. An audit must challenge stated assumptions with varied
instances or invariant reasoning when the requested artifact must generalize
beyond the supplied instance.

The first `model-extraction-relu-logits` attempt made no score-valid progress.
The provider rejected the task text as a possible cybersecurity request before
the model loop began. The task is an authorized model-extraction exercise in
an isolated container. The replacement attempt appended one fixed statement
that records this authorization and confinement. The runner exposes no
free-form prompt override. The replacement reached the unchanged task-owned
verifier, so its 0.0 score is the development result.

The serial ten-task campaign manifest has SHA-256 digest
`03bb6fd4851fec57bbccc33727a0c86bdb24e90d6ab27fe4b5422ff21459d5e9`.
The authorized replacement manifest has SHA-256 digest
`8802dc1889a135761b1b533b4f4e5cf9c494bae7c4e7f3c41d43f9eab6a9ef80`.
Raw episodes and task workspaces remain under
`/home/sunil/git/foe-live-artifact-tool-usability/target/terminal-bench-jobs/standard-live-artifact-tool-usability-development-remaining-ten-20260827T163746Z`
and
`/home/sunil/git/foe-live-artifact-tool-usability/target/terminal-bench-jobs/standard-live-artifact-tool-usability-model-extraction-authorized-retry-20260827T182109Z`.

The 10/12 result does not open confirmation. A source candidate must address
the two observed audit defects without task-specific instructions. It receives
activation attempts on `sanitize-git-repo` and
`model-extraction-relu-logits` before any confirmation task. Previously
qualified task results remain attached to the frozen binary that produced
them and are not rerun during the focused activation gate.

## Scoped repair and numerical transfer

The two development failures produced one general terminal-audit candidate.
The audit must reproduce a defect before changing the workspace and limit its
repair to that evidence. It must also challenge assumptions that govern
transfer beyond the supplied instance. The change affects only the built-in
audit instructions, their specification, and regression assertions.

The first candidate used source commit
`d80c5bcf8340fb34d314bc917fbae035c2296b75`, source tree
`git-tree-sha1:0a4a210a255f34f20ec5be86c773cf7354ec6212`, and portable binary
`sha256:3e4b6bea34c6974852d097bd00fb8cd737bf4f830ddfa729871543780792d4ec`.
It failed both activation tasks.

| Task | Score | Calls | Input | Cache read | Output | Estimated cost | Wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `sanitize-git-repo` | 0.0 | 22 | 850,153 | 433,664 | 23,954 | $2.318502 | 761.711 |
| `model-extraction-relu-logits` | 0.0 | 22 | 135,286 | 53,248 | 10,998 | $0.569411 | 293.728 |

The sanitize audit still interpreted the repository as all 108 reachable Git
commits. It removed the baseline commit required by the unchanged-file test.
The model-extraction audit did exercise controlled models with 1, 3, 7, and
25 hidden units. The hidden verifier missed one row, compared with three on
the predecessor. Width variation did not cover the numerical conditions that
control breakpoint discovery.

The retained manifests have SHA-256 digests
`bae84d552d8133957fd6360c588b46d2ff039b378a82308ee9a320b1981d011d`
and
`673a94a4fee00f7059db2d653b7b5ca524e75505d95d574a6833c64f6176265c`.
The result is retained as negative and partial-credit evidence. It is not a
promotable candidate.

The revised audit gives file, directory, and repository tasks a default
mutation scope of current filesystem content. Version-control history changes
require task language that explicitly names history, commits, refs, object
databases, reflogs, provenance, or prior versions. Transfer checks derive
algorithm-sensitive bounds, thresholds, sample counts, ranges, scales,
distributions, and near-degenerate cases. Varying only shape or count does not
establish numerical generalization.

The revised candidate used source commit
`18dce062f19045e2713d1c7c8c7657653cf005d3`, source tree
`git-tree-sha1:5f9fa88548a1964539b7d4eb22eecc57bb44faa3`, and portable binary
`sha256:f17fa16aaf7670348e7caa82d4f5f1dee32ed6825ac78949f0ec4af2d87263af`.
Both activation tasks passed their unchanged task-owned verifiers.

| Task | Score | Calls | Input | Cache read | Output | Estimated cost | Wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `sanitize-git-repo` | 1.0 | 18 | 571,848 | 225,280 | 18,887 | $1.854124 | 618.281 |
| `model-extraction-relu-logits` | 1.0 | 17 | 92,471 | 22,528 | 10,717 | $0.503123 | 278.700 |

The passing sanitize audit preserved Git history. It marked history-wide
cleanup as unmet, reported the remaining exposure, and verified only the
three contaminated working-tree files. This trajectory directly activates
the filesystem-scope rule.

The passing model-extraction artifact recovered every hidden row used by the
task-owned verifier. Its terminal audit did not report a numerical stress
matrix. The score is valid quality evidence, while attribution to the transfer
instruction remains unproven. A repeated activation or a transfer case must
establish reliability before making that causal claim.

The passing manifests have SHA-256 digests
`90de764fe30d6e49b3e303398b5e73af864e11aaaac3ead5d179ebb5ba952b68`
and
`3b3bacd833623fa99120d352408dfb2928a3ce74e183ed4933434eb5cc87db8c`.
Raw evidence remains under
`/home/sunil/git/foe-audit-evidence-repair-transfer/target/terminal-bench-jobs`.

Pull request 121 carries only the audit change against `main`. Its head is
`b8c0aab6c975211ac6b24d14f872d3dbc49344a5`, with source tree
`git-tree-sha1:84184b1f319fc1d8ab07eea58e889098fe430e53`. The clean branch passes
the workspace suite, Clippy, deterministic examples, and line-count check.
External scores came from the aggregate candidate above because that exact
binary also contains the previously reviewed release changes.

This is a directly implemented improvement. It does not count toward the two
required autonomous self-improvements. The candidate remains available for
review because it converted two externally verified development failures.
Before confirmation, it must preserve success on development tasks where the
terminal audit previously performed substantial repairs. The regression gate
starts with `gpt2-codegolf`, `path-tracing-reverse`, and `regex-chess`.

The exact revised candidate passed all three substantial-repair regression
tasks on the standard service tier.

| Task | Score | Calls | Input | Cache read | Output | Estimated cost | Wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gpt2-codegolf` | 1.0 | 46 | 1,220,140 | 838,656 | 33,777 | $2.536938 | 835.655 |
| `path-tracing-reverse` | 1.0 | 39 | 2,063,533 | 1,376,768 | 19,174 | $3.681247 | 479.019 |
| `regex-chess` | 1.0 | 78 | 1,915,451 | 1,491,456 | 37,280 | $3.038162 | 1,037.512 |

Every result used the same source commit, source tree, portable binary,
models, reasoning efforts, token policy, sandbox policy, and task-owned
verifier as the two passing activation tasks. Every account conformed with no
recorded violation. The campaign manifest has SHA-256 digest
`7cf7c60158a8a959ea945e01e03177de04c6c43c4bfa653b7bf8ddfe8dfd0403`.

The repair-scope rule therefore preserved three trajectories where the audit
had previously replaced a substantial implementation. The exact candidate is
5/5 on newly executed development tasks.

The exact candidate then ran the seven remaining development tasks. Every
request used the standard service tier. The run was serial, used
measurement-only token accounting, and disabled Landlock inside the task
containers.

| Task | Score | Calls | Input | Cache read | Output | Estimated cost | Wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `cancel-async-tasks` | 1.0 | 19 | 154,603 | 68,096 | 15,950 | $0.692266 | 358.846 |
| `fix-git` | 1.0 | 18 | 147,739 | 47,104 | 9,045 | $0.602282 | 225.249 |
| `sqlite-db-truncate` | 1.0 | 12 | 92,126 | 7,680 | 7,187 | $0.484596 | 178.423 |
| `large-scale-text-editing` | 1.0 | 17 | 218,369 | 96,768 | 16,269 | $0.850491 | 427.561 |
| `fix-ocaml-gc` | 1.0 | 31 | 1,165,806 | 710,144 | 12,020 | $2.347106 | 1,679.004 |
| `git-multibranch` | 1.0 | 21 | 210,714 | 52,224 | 23,937 | $1.133590 | 532.031 |
| `dna-assembly` | 0.0 | 42 | 621,942 | 409,600 | 28,510 | $1.583408 | 726.818 |

All seven attempts reached the unchanged task-owned grader. Every Foe account
conformed, and the campaign recorded no credential, container, executable,
sandbox, allowance, or other infrastructure failure. The campaign manifest
has SHA-256 digest
`fa7e568acb26124d618d2b512bf6bcc130db63be9265b1de6e51603bfdd87ca9`.
Raw evidence remains under
`/home/sunil/git/foe-audit-evidence-repair-transfer/target/terminal-bench-jobs/standard-audit-scope-transfer-development-remaining-seven-20260827T200106Z`.

The exact candidate scored 11/12 across the two activation tasks, three
substantial-repair regression tasks, and seven remaining tasks. The twelve
attempts used 358 model calls, 8,474,742 input tokens, 5,346,304 cached-input
tokens, 232,753 output tokens, and an estimated $19.307333. Their recorded
wall time totaled 7,377.099 seconds. The numerical development criterion is
satisfied.

The `dna-assembly` failure is an artifact-outcome mismatch. Foe completed and
cited one successful terminal-audit result for every requirement. The
task-owned grader applied the submitted primers and found that the resulting
sequence did not match the required output.

The cited audit aligned every primer against a template and checked melting
temperatures. Its assembly check then concatenated fixed template coordinates
that described the intended product. It did not derive both fragment
boundaries from the final primer sequences. This method could accept an
incorrect primer while reporting an exact reconstruction. The failure is a
general dependency error in substitute validation: a semantic check must
derive the behavior under test from the final artifact rather than combining
separately validated assumptions with an expected result.

Confirmation remains closed while this failure supplies evidence for the
second required autonomous improvement. The current source tree and binary
remain frozen. Additional `dna-assembly` attempts may establish a repeated
failure contrast without invalidating any of the eleven passing results.

Two initial repeat attempts did not reach Foe or the task-owned grader. They
used the evaluated source branch's stale controller. That controller invoked
`foe schema` without a model argument inside the container, and the installed
binary correctly rejected the command because the container had no default
model. Harbor recorded two `NonZeroAgentExitCodeError` setup exceptions. No
provider request occurred. These attempts are infrastructure-invalid, and
their avoidable setup failure remains part of the campaign integrity record.

The replacement command kept the exact frozen source and binary while using
the current immutable controller and credential-safe adapter. Its no-spend
preflight named the correct binary digest, source tree, standard service tier,
serial execution, and built-in workflow before either attempt began. Both
attempts then passed the unchanged task-owned grader.

| Task | Attempt | Score | Calls | Input | Cache read | Output | Estimated cost | Wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `dna-assembly` | 2 | 1.0 | 26 | 260,666 | 124,928 | 20,110 | $0.995123 | 489.999 |
| `dna-assembly` | 3 | 1.0 | 34 | 468,542 | 281,600 | 26,394 | $1.388288 | 635.600 |

Both accounts conformed. The controller recorded no credential, container,
executable, sandbox, allowance, or other infrastructure failure. The
credential-safe repeat manifest has SHA-256 digest
`3d024019c7d6628aa7b0fc9dd056da372154daea39aae21cf7b59d2e6da38cc1`.
Raw evidence remains under
`/home/sunil/git/foe-audit-evidence-repair-transfer/target/terminal-bench-jobs/standard-audit-scope-transfer-dna-repeatability-credential-safe-20260827T212005Z`.

The frozen candidate is 2/3 on `dna-assembly`. The one false acceptance still
identifies a valid audit defect. It does not supply the two matching failures
required for an identity-bound repeated-failure contrast. The campaign will
not treat that single failure as sufficient evidence for an autonomous source
change. The 11/12 development disposition remains unchanged.

## Autonomous candidate review and self-improvement repair

The current-version DNA corpus contains no validated repeated-failure
contrast, so the self-improvement controller rejected it before provider
spend. The campaign preserved that evidence gate. A second no-spend plan used
the retained repeated `dna-insert` contrast and evaluator-owned assessment
against their exact parent source tree and binary. The plan selected GPT-5.6
Sol, low diagnosis and implementation reasoning, xhigh source review, the
standard service tier, and measurement-only token accounting.

The autonomous workflow produced a source candidate in 28 model calls and
256.785 seconds. It used 495,525 input tokens, 258,560 cached-input tokens,
8,707 output tokens, and an estimated $1.225424. The repository checker
accepted changes to CLI source, regression assertions, and two specifications.
The source candidate identity was
`sha256:fd75200d420541c2b2253c8939e85bc61960a5960343b2672f9b9d766428906e`,
and its source-bundle identity was
`sha256:3e747e3fcb0293e37621b84a98c1b747ae3b405b9664aea0e34e476c63cdaa9a`.
The retained result has SHA-256 digest
`ee551abc866adfc8f0f9dad81e68e069dc37787d5d1eb4eac72779838276617a`.

The candidate is rejected before external task evaluation. It added audit
instructions that require one artifact-bound derivation. The independent
xhigh source review found that the return schema and settlement gate still
could accept inconsistent semantic claims. The finalization child returned no
typed value and left that P1 finding unresolved. The repository checker
covered source shape, tests, formatting, Clippy, and line budgets, so it could
not adjudicate the semantic finding. The controller nevertheless marked the
candidate accepted. External evaluation would have spent protected task
attempts on a mechanism that its own independent review had already rejected.

This failed autonomous attempt triggered a direct repair to the
self-improvement mechanism. Finalization now returns one typed disposition for
each independent-review finding. It must copy each finding exactly and mark it
`fixed` or `unresolved`. Missing, duplicated, unexpected, and unresolved
findings reject the candidate after the episode. A review that exhausts adds a
finding that requires finalization to perform the missing semantic review.
The repository checker remains the authority for mechanical conformance; it no
longer implies that semantic review findings were resolved.

The Terminal-Bench evaluation unit suite and Python compilation pass. A
retrospective check rejects the retained candidate because its finalization
returned no typed value. The revised workflow passes a no-spend `foe plan`
against the same identity-bound evidence and parent. Campaign commit
`f0bc01b` contains the controller, regression tests, and operator
documentation. The repair changes no evaluated Foe runtime, so the 11/12
development result remains valid. The next provider-backed action is one
rerun of the autonomous workflow with this review-resolution contract.

The review-resolution rerun exercised the stronger gate and rejected its
candidate before external task evaluation. Independent review identified four
semantic defects in the implementation's model-declared artifact identity and
derivation graph. The finalization child replaced that design with
runtime-owned audit state and added source, tests, and specification changes.
It then exhausted its 40-request allowance before formatting the source and
returning the required typed finding dispositions. The external repository
checker found an unformatted Rust diff. The retained result records no adopted
source candidate.

The rejected run used 82 model calls over 1,331.510 seconds. It consumed
5,383,743 input tokens, 4,129,792 cached-input tokens, and 60,011 output tokens
at an estimated cost of $7.867941. Its substantive correction reached the
candidate checker immediately before exhaustion. A fixed 40-request
finalization ceiling therefore acted as a quality limit rather than a loop
backstop.

Source finalization now reserves 60 model requests and 3,600 seconds. The root
workflow reserves the corresponding 160 requests. Token use remains measured
without constraining admission. The Terminal-Bench evaluation unit suite and
Python compilation pass with this allowance. Campaign commit `49f8df1`
contains the allowance, regression expectation, and operator documentation.
The next autonomous attempt uses the same evidence, parent source identity,
models, and standard service tier. Only the finalization backstop changes.

The 60-request finalization attempt is also rejected. Its implementation
introduced a model-declared derivation graph. Independent review found that
the runtime checked graph shape without recomputing claims, allowed graph
branches unrelated to a conclusion, lost freshness after later verification
or settlement changes, and activated the graph only through one return schema.
The finalization child attempted a broader runtime evaluator and consumed all
60 reserved requests without returning typed finding dispositions.

The full workflow used 127 model calls over 1,899.581 seconds. It consumed
9,770,321 input tokens, 8,195,584 cached-input tokens, and 81,609 output tokens
at an estimated cost of $11.209362. The external repository checker also
reported 5,505 kernel lines against the historical candidate allowance of
5,350. No source candidate was adopted, and no Terminal-Bench activation was
run.

This result rejects another allowance increase. The graph proposal requires a
general language for machine-evaluable semantic claims, which exceeds the
observed failure and adds substantial kernel surface. More correction capacity
would preserve the same mis-scoped hypothesis. A smaller workflow-level
intervention should test whether a second fresh audit catches variable false
acceptance before any runtime semantic language is considered.

The attempt exposed one further self-improvement defect. Independent review
examined the implementation diff before finalization, while finalization made
substantial unreviewed changes and then judged its own finding dispositions.
The workflow now ends with a separate read-only assessment of the finalized
diff. That assessment uses `xhigh` reasoning, cannot write source, runs the
repository checker, and rejects every finding, unresolved risk, missing typed
return, or exhaustion. Campaign commit `660d633` contains the workflow stage,
acceptance gate, tests, and operator documentation. This repair affects future
self-improvement attempts and does not change the evaluated 11/12 release.

The first autonomous second-audit attempt produced a mechanically clean source
candidate. Its initial independent review found that the cloned terminal audit
had replaced important quality instructions and that several specifications
still described two episodes. Finalization preserved the full original audit
role, appended the fresh-review duties, updated every identified
specification, passed the repository checker, and returned exact `fixed`
dispositions for both findings.

The final read-only assessment did not start. The workflow retained
`max_episodes: 5` after adding a fifth model child, while the allowance counts
the root episode as well. Foe ended with `exhausted: episodes`, and the
controller rejected the candidate because no final assessment value existed.
This is an evaluation-controller defect rather than candidate evidence. The
run used 55 model calls over 704.218 seconds, 1,706,676 input tokens, 905,216
cached-input tokens, and 29,467 output tokens at an estimated cost of
$4.157266.

The source-program builder now derives `max_episodes` as one root plus the
number of declared model nodes. A regression asserts both the resulting value
of six and the derivation itself. The evaluation unit suite and Python
compilation pass. Campaign commit `dd198b3` contains the fix and operator
documentation. The replacement attempt keeps the same evidence, parent source,
objective, models, and standard service tier. Its retained program records five
model nodes and an episode allowance of six before the first model request.

The replacement autonomous workflow completed its final read-only assessment
and produced a mechanically accepted source candidate. The candidate added a
second fresh audit after the existing implementation and audit children. The
new audit received the task and both prior typed returns, retained write access
to the shared workspace, and became the sole terminal node. The external
verifier remained attached only to that terminal node.

The implementation, regression tests, and affected specifications changed
together. Initial review found four defects: an unreachable episode allowance,
missing host-integration coverage, an incomplete episode-count description, and
stale completion language. Finalization resolved all four findings. The final
read-only assessment found no remaining source defect or unresolved risk. The
repository checker passed.

The autonomous run used 101 model calls over 1,108.958 seconds. It consumed
4,655,870 input tokens, 3,188,736 cached-input tokens, and 45,771 output tokens
at an estimated cost of $8.059450. The source candidate identity was
`sha256:8b1d277ff41a71665e8853cebfd20204e0085cc5088ddbae59f031238e60b0a1`.
Its source-bundle identity was
`sha256:db5bec0c81192b7cc2c6445e2e067b244b2bfdde7f2befc80c28ebf2263decb6`.
The retained result has SHA-256 digest
`75b07d7fd2f3ac9c8a7e739b7d317c2c56eb2c5d07ff56cfc1c22a7cf7d39033`.

An exact portable binary built from the retained source candidate then ran
three unchanged `dna-insert` attempts. Every request used the standard service
tier. Execution was serial, token limits were measurement-only, and Landlock
was disabled inside each Terminal-Bench container.

| Attempt | Score | Calls | Input | Cache read | Output | Estimated cost |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1.0 | 32 | 323,146 | 140,800 | 22,829 | $1.242284 |
| 2 | 0.0 | 20 | 167,509 | 40,448 | 22,538 | $0.975183 |
| 3 | 0.0 | 31 | 332,754 | 151,552 | 25,712 | $1.299669 |

All three attempts reached the unchanged task-owned grader. Every account
conformed. The controller recorded no credential, container, executable,
sandbox, allowance, or other infrastructure failure. The assessed binary had
SHA-256 digest
`905583f51db318241ba015417f9cd100de40f4fb1748bfb4d33d69b3f70115d2`.
Its source tree was
`git-tree-sha1:15aa73f4cfbce6e9e1cbc99dc90176c0620839b9`. The campaign manifest
has SHA-256 digest
`dc1a4be5f6b7ceb3da4b9af052ea1392b39722f3b78cf4ba9ca500d13d17c412`.
Raw evidence remains under
`/home/sunil/git/foe-second-independent-audit-candidate/target/terminal-bench-jobs/standard-autonomous-second-audit-dna-activation-20260827T233755Z`.

The source candidate scored 1/3, which matches its identity-bound parent. It
does not establish an activation improvement, so the campaign rejects it and
does not spend an unrelated transfer attempt.

Both failed final audits invoked the required `oligotm` executable and reported
passing Tm differences. They selected the annealed portions through maximal
alignment of each primer against the circular input. The task-owned grader
instead split the concatenated final primers around the inserted sequence and
computed Tm from the two remaining flanks. That construction produced Tm
differences of 5.172678 and 5.828321 degrees Celsius.

The first and second audits made the same semantic boundary choice. A fresh
context therefore removed trajectory dependence but did not create an
independent validation method. More copies of the same audit role would add
cost without addressing the correlated error. The next development candidate
must use external verifier feedback during completion or introduce a genuinely
independent deterministic construction. A declared-verifier development lane
can test the first mechanism without changing the closed-book Terminal-Bench
quality authority.

The exact 11/12 aggregate candidate then ran one modified `dna-insert` attempt
with the qualified public checker assigned to the terminal audit's
`done_when.verify`. The source tree remained
`git-tree-sha1:5f9fa88548a1964539b7d4eb22eecc57bb44faa3`. The portable binary
remained
`sha256:f17fa16aaf7670348e7caa82d4f5f1dee32ed6825ac78949f0ec4af2d87263af`.
Every request used the standard service tier. Token limits were
measurement-only, and Landlock was disabled inside the task container.

The checker rejected three intermediate artifacts. Each finding identified a
task-consistent boundary at which the paired melting temperatures differed by
more than five degrees Celsius. The reported differences were 5.268284,
5.787026, and 5.020077 degrees Celsius. The terminal audit repaired the final
artifact after every finding. Its fourth ordinary checker call returned no
finding, and the authoritative completion verification also accepted.

The unchanged task-owned grader awarded score 1.0 without an exception. Foe
completed after 61 model calls. The attempt used 780,564 input tokens, 494,592
cached-input tokens, and 24,299 output tokens at an estimated cost of
$1.827705. Its makespan was 683.548 seconds. The Foe account conformed with no
violation, and the checker digest remained
`34b6d43b3cc9eda9ef0111911751847c17ba26ee124cb6bc7a2d2e74c6f4e22b`.

The campaign manifest has SHA-256 digest
`8f2de52677cec819444214fdf1bccd12054d9945195c0ae73fd8c6b91a842b57`.
Raw evidence remains under
`/home/sunil/git/foe-audit-evidence-repair-transfer/target/terminal-bench-jobs/standard-aggregate-dna-insert-done-when-20260828T001456Z`.

This result establishes the causal value of verifier-governed correction on
the exact aggregate candidate. It does not repair the rejected closed-book
second-audit candidate, and one attempt does not establish repeatability. The
next self-improvement input should contrast the three rejected checker states
with the final accepted state. That contrast supplies denser evidence than the
task score alone and identifies trusted semantic feedback as the successful
mechanism.
