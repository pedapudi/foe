# Foe capability campaign record

This record defines the evaluation campaign that prepares Foe for a Terminal-Bench 2.1 submission. The campaign ends at a retained calibration result. A full 89-task benchmark run requires a separate decision.

Terminal-Bench 2.1 is pinned as `terminal-bench/terminal-bench-2-1@6`. The benchmark contains 89 container tasks and task-owned verifiers. Its maintainers require at least five trials per task for an official submission. [The Terminal-Bench 2.1 repository](https://github.com/harbor-framework/terminal-bench-2-1) specifies the submission protocol. [The release description](https://www.tbench.ai/news/terminal-bench-2-1) describes the task corrections and continuous validation.

## Campaign objective

The campaign must convert model capability into reliable Foe performance. The central test is a task that GPT-5.6 Sol can solve with `xhigh` reasoning through Foe, while the same Foe runtime usually fails with `low` reasoning.

The target improvement makes Sol with `low` reasoning solve that task reliably. The change must improve a general harness mechanism. Task-specific instructions, benchmark identifiers, fixture values, and grader rules are excluded from candidate changes.

The reasoning ceiling is `xhigh`. The campaign does not run any model with `max` reasoning.

## Success criteria

The capability conversion succeeds when all of these conditions hold:

1. Unchanged Foe with Sol `low` completes no more than one of three attempts on the selected development task.
2. Unchanged Foe with Sol `xhigh` completes at least two of three attempts on that task.
3. Improved Foe with Sol `low` completes at least two of three attempts. Three completions are the preferred result.
4. The four confirmation tasks produce at least seven successful attempts across two attempts per task. Every confirmation task must succeed at least once.
5. Previously successful development tasks retain their task-owned verifier result. Their aggregate estimated cost may increase by at most 15 percent.
6. The converted Sol `low` configuration costs less than unchanged Sol `xhigh` on the selected task.
7. Every assessed trial retains a complete Foe episode, provider usage, task verifier result, source identity, runtime identity, and conformance report.
8. No assessed trial has an infrastructure exception, malformed trace, or disagreement between a successful task verifier and Foe completion.
9. Foe self-improvement produces and evaluates at least one identity-bound candidate. A direct implementation follows when the workflow produces no valid change.

The calibration gate is intentionally ambitious. Foe must complete at least ten of twelve calibration tasks and at least five of six calibration-holdout tasks on one frozen attempt per task. This result is 15 successes across 18 tasks. It is a directional estimate near the campaign's eventual 85 percent full-benchmark target.

Provider spend through the calibration gate should remain below 75 US dollars.
The spend limit uses the retained token-derived cost estimate. Development
runs request the `priority` service tier to reduce elapsed time. Their records
also carry the documented 2.5-times ChatGPT credit multiplier for GPT-5.6.
The service tier does not change the token-derived comparison metric. Foe
records usage without enforcing token ceilings during development runs. The
[OpenAI speed documentation](https://developers.openai.com/codex/speed)
specifies the speed target and credit multiplier.

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

Twelve additional tasks form the capability-search set:

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
- `gpt2-codegolf`;
- `path-tracing`.

The tasks from `regex-chess` through `dna-assembly` were added after Sol `low` solved both failures among the first four tasks. They were selected from task metadata at repository commit `7131e4375048a0e408a8fb404b5f499d726b695b`.

Each selected task is marked hard and represents a different implementation or reasoning demand. [The pinned task metadata](https://github.com/harbor-framework/terminal-bench-2-1/tree/7131e4375048a0e408a8fb404b5f499d726b695b/tasks) is the selection source.

Four reasoning-heavy tasks were added after `dna-assembly` failed to qualify as a stable reasoning gap. Unchanged Sol `low` completed one of three attempts. Unchanged Sol `xhigh` also completed one of three attempts. The additional tasks were frozen from the same pinned metadata before their instructions were opened.

Opening any capability-search result makes that task development evidence. These tasks can identify the Sol `low` and Sol `xhigh` capability gap. They cannot provide confirmation after inspection.

### Confirmation evidence

Four tasks remain closed until a candidate and its acceptance rule are frozen:

- `build-cython-ext`;
- `constraints-scheduling`;
- `custom-memory-heap-crash`;
- `vulnerable-secret`.

Each candidate receives two attempts per confirmation task. Raw confirmation trajectories remain outside self-improvement evidence until the candidate disposition is recorded.

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
7. Run identity-bound self-improvement with Luna `high` for bounded diagnosis and Terra `high` for implementation. The coding node receives only the typed diagnosis and acts as a full coding agent.
8. Validate the generated candidate outside the self-improvement episode. Implement the diagnosed change directly when the generated candidate is absent, invalid, or unsupported by the evidence.
9. Re-run the selected capability task with Sol `low`. Reject candidates that fail the capability-conversion criteria.
10. Re-run the six development tasks. Compare successful completion, estimated cost, wall time, and trace integrity.
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

The self-improvement workflow has two model nodes. A Luna diagnosis node reads
the bounded trajectory digest and returns a typed causal intervention. It has
no source-tree inspection tool or access. A separate Terra coding node
receives the diagnosis and a clean context, then maps the intervention to source. The
intervention must improve the lower-cost evaluated configuration. It preserves
the model route, reasoning effort, task allowances, token policy, and task set.
Higher-cost successful settings supply diagnostic contrasts.

The digest retains the final edit and a bounded sequence of later tool
results for each episode. Structured verifier reports contribute counts,
failure classes, bounded messages, and a content digest. Confirmation and
calibration holdout feedback remains closed. A diagnosis must cite evidence
that differs between failures and successes. It must also state a falsifying
observation, required product paths, and activation under the evaluated
program.

A Foe runtime failure or nonconformant trace invalidates the trial as accuracy
evidence. A request without provider usage invalidates exact cost and token
claims. The retained campaign record names each invalid trial. The required
attempt count is completed with a replacement trial.

The coding node has Foe's standard coding tools: `read`, `grep`, `edit`, and
`bash`. Its write authority covers runtime crates, specifications, and
examples. It cannot change evaluation code, benchmark material, model
routes, reasoning settings, task allowances, token policy, or task selection.
Its verifier uses a pinned Cargo binary. The verifier runs formatting,
workspace tests, clippy, and line-budget checks.

The evidence file names the evaluated Git tree and Foe binary digest. It labels every trajectory with the run and model setting that produced it. The workflow refuses a source or binary mismatch before making a model request. The candidate checker requires a Rust implementation change, a Rust regression test, and an affected specification.

The workflow is one candidate generator. The runner validates changed files again after the episode. Its result binds the base Git tree and changed file content into one candidate artifact digest. A validated artifact survives an exhausted reporting outcome. Candidate promotion remains an external evaluation decision. A failed artifact sets `direct_implementation_required`. The campaign then proceeds with a direct implementation.

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
