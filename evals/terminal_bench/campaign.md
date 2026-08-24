# Foe capability campaign record

This record defines the evaluation campaign that prepares Foe for a Terminal-Bench 2.1 submission. The campaign ends at a retained calibration result. A full 89-task benchmark run requires a separate decision.

Terminal-Bench 2.1 is pinned as `terminal-bench/terminal-bench-2-1@6`. The benchmark contains 89 container tasks and task-owned verifiers. Its maintainers require at least five trials per task for an official submission. [The Terminal-Bench 2.1 repository](https://github.com/harbor-framework/terminal-bench-2-1) specifies the submission protocol. [The release description](https://www.tbench.ai/news/terminal-bench-2-1) describes the task corrections and continuous validation.

## Campaign objective

The campaign must convert model capability into reliable Foe performance. The central test is a task that GPT-5.6 Sol can solve with `xhigh` reasoning through Foe, while the same Foe runtime usually fails with `low` reasoning.

The target improvement makes Sol with `low` reasoning solve that task reliably. The change may affect Foe source or a general workflow configuration. Task-specific instructions, benchmark identifiers, fixture values, and grader rules are excluded from candidate changes.

The reasoning ceiling is `xhigh`. The campaign does not run any model with `max` reasoning.

## Success criteria

The capability conversion succeeds when all of these conditions hold:

1. Unchanged Foe with Sol `low` completes no more than one of three attempts on the selected development task.
2. Unchanged Foe with Sol `xhigh` completes at least two of three attempts on that task.
3. Improved Foe with Sol `low` completes at least two of three attempts. Three completions are the preferred result.
4. The four confirmation tasks produce at least seven successful attempts across two attempts per task. Every confirmation task must succeed at least once.
5. The six development tasks retain six task-owned verifier successes on the frozen candidate.
6. Every assessed trial retains its task verifier result, available Foe episode, source identity, runtime identity, and conformance report.
7. Foe self-improvement produces and evaluates at least one identity-bound source or workflow candidate. A direct implementation follows when the workflow produces no valid change.

Task quality is the only candidate promotion metric. Provider-reported tokens,
estimated cost, wall time, cache use, outcome accuracy, and trace conformance
remain required diagnostics. Missing provider usage marks the resource record
as incomplete. It does not change the task-owned quality score.
Infrastructure exceptions receive the score assigned by the task framework.
They do not create a separate promotion gate.

The calibration gate is intentionally ambitious. Foe must complete at least ten of twelve calibration tasks and at least five of six calibration-holdout tasks on one frozen attempt per task. This result is 15 successes across 18 tasks. It is a directional estimate near the campaign's eventual 85 percent full-benchmark target.

Planning estimates bound campaign exposure before each confirmed command.
They do not reject a candidate that improves task quality. Development runs
use the service tier selected for that run and record it in the manifest. Foe
records usage without enforcing token ceilings during ordinary quality runs.

## Task sets

The task membership is stored in [`cases.json`](cases.json). Task selection used only the pinned task metadata, including category, difficulty, and resource limits. The selection was frozen before task instructions or trajectories were opened.

### Micro evaluation

The local micro evaluation covers five Foe contracts:

- containment after a denied write;
- typed evidence returned under a model-call limit;
- delegated quotation across child episodes;
- declared workflow provenance;
- continuity after context compaction.

These cases are inexpensive diagnostics. They test runtime contracts and do not estimate Terminal-Bench accuracy.

### Development evidence

Six Terminal-Bench tasks already have inspected trajectories. They remain development evidence:

- `cancel-async-tasks`;
- `git-multibranch`;
- `fix-git`;
- `sqlite-db-truncate`;
- `sanitize-git-repo`;
- `large-scale-text-editing`.

Eleven additional tasks form the capability-search set:

- `password-recovery`;
- `path-tracing-reverse`;
- `polyglot-rust-c`;
- `regex-log`;
- `regex-chess`;
- `write-compressor`;
- `fix-ocaml-gc`;
- `dna-assembly`;
- `feal-linear-cryptanalysis`;
- `model-extraction-relu-logits`;
- `gpt2-codegolf`.

The tasks from `regex-chess` through `dna-assembly` were added after Sol `low` solved both failures among the first four tasks. They were selected from task metadata at repository commit `7131e4375048a0e408a8fb404b5f499d726b695b`.

Each selected task is marked hard and represents a different implementation or reasoning demand. [The pinned task metadata](https://github.com/harbor-framework/terminal-bench-2-1/tree/7131e4375048a0e408a8fb404b5f499d726b695b/tasks) is the selection source.

Four reasoning-heavy tasks were added after `dna-assembly` failed to qualify as a stable reasoning gap. Unchanged Sol `low` completed one of three attempts. Unchanged Sol `xhigh` also completed one of three attempts. The additional tasks were frozen from the same pinned metadata before their instructions were opened.

Opening any capability-search result makes that task development evidence. These tasks can identify the Sol `low` and Sol `xhigh` capability gap. They cannot provide confirmation after inspection.

### Confirmation evidence

Four tasks remain closed until a candidate and its acceptance rule are frozen:

- `build-cython-ext`;
- `constraints-scheduling`;
- `custom-memory-heap-crash`;
- `path-tracing`.

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

`path-tracing` replaced the incompatible case before its task instruction,
container, or trajectory was opened. The retained campaign manifests contained
no prior `path-tracing` trial. It was the only unopened member of the frozen
capability-search set, which made the substitution rule independent of an
observed task result.

### Calibration evidence

The calibration set contains twelve tasks:

- `adaptive-rejection-sampler`;
- `break-filter-js-from-html`;
- `compile-compcert`;
- `db-wal-recovery`;
- `distribution-search`;
- `financial-document-processor`;
- `git-leak-recovery`;
- `make-mips-interpreter`;
- `query-optimize`;
- `reshard-c4-data`;
- `schemelike-metacircular-eval`;
- `train-fasttext`.

The calibration holdout contains six tasks:

- `circuit-fibsqrt`;
- `cobol-modernization`;
- `extract-elf`;
- `hf-model-inference`;
- `mcmc-sampling-stan`;
- `sparql-university`.

Calibration trajectories remain closed until the development and confirmation criteria pass. Calibration-holdout trajectories remain closed until the calibration result and decision rule are recorded.

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

1. Run deterministic capability probes in every selected task container before its first provider request. Keep each report scoped to its exact dataset task and image.
2. Run the local micro evaluation and repository tests. Correct runtime contract failures before benchmark work.
3. Run one Luna `low` attempt on capability-search tasks when a cheap trajectory can reveal obvious tool or adapter failures.
4. Run one Sol `low` attempt on the remaining capability-search tasks. Run Sol `xhigh` only on Sol `low` failures.
5. Repeat the most promising Sol gap three times at each reasoning setting. Freeze the selected task and gap criterion.
6. Produce typed trajectory diagnoses. Each diagnosis names its model setting and retained run. The digest groups verified results by task and model setting. It retains request growth, replayed results, final validation activity, bounded verifier failure classes, and log sequence numbers under fixed tree-wide bounds.
7. Run identity-bound self-improvement with Luna `high` for bounded diagnosis and Sol at no more than `xhigh` for source implementation. The workflow may produce a source change or an independent-audit workflow configuration.
8. Validate the generated candidate outside the self-improvement episode. Implement the diagnosed change directly when the generated candidate is absent, invalid, or unsupported by the evidence.
9. Re-run the selected capability task with Sol `low`. Reject candidates that fail the capability-conversion criteria.
10. Re-run the six development tasks. Require six successful task-owned verifier results. Record estimated cost, wall time, outcome accuracy, and trace integrity as diagnostics.
11. Freeze the candidate source tree, binary digest, model settings, acceptance rule, and confirmation task list.
12. Run two attempts on each confirmation task. Reject the candidate when the confirmation criteria fail.
13. Freeze one calibration attempt per task. Run the twelve calibration tasks, then record the result before opening the six calibration-holdout tasks.
14. Decide whether the evidence supports a full Terminal-Bench 2.1 run.

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

The self-improvement workflow has at most two model nodes. A Luna diagnosis
node reads the bounded trajectory digest and returns a typed causal
intervention. It has no source-tree inspection tool or access.

The diagnosis selects `implement-source` when the evidence activates a source
mechanism. A separate coding node then receives the diagnosis in a clean
context and maps it to source. The diagnosis selects `configure-workflow` when
an independent audit stage supplies the repeated quality gain. This path
returns a typed workflow setting without starting the coding node.

The diagnosis selects `insufficient-evidence` when the contrast isolates only
model capability or requires semantic information absent from the log. Every
candidate preserves the primary model route, reasoning effort, task
allowances, token policy, service tier, and task set. Resource changes are
recorded. Verified task quality governs candidate promotion.

The digest retains the final edit and a bounded sequence of later tool
results for each episode. Structured verifier reports contribute counts,
failure classes, bounded messages, and a content digest. Confirmation and
calibration holdout feedback remains closed. A diagnosis must cite evidence
that differs between failures and successes. It must also state a falsifying
observation, required product paths, and activation under the evaluated
program.

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
Its digest binds the setting to the evaluated source, binary, evidence, and
preserved execution controls.

The workflow is one candidate generator. The runner validates source changes
again after the episode. It validates workflow settings before a benchmark
run. A validated artifact survives an exhausted reporting outcome. Candidate
promotion remains an external evaluation decision. A failed artifact sets
`direct_implementation_required`. The campaign then proceeds with a direct
implementation.

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
