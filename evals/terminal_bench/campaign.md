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

Provider spend through the calibration gate should remain below 75 US dollars. The spend limit is a campaign decision gate. Foe records usage without enforcing token ceilings during development runs.

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

Planning token estimates provide a spend preview. They do not stop ordinary development episodes. Model calls and wall time are high backstops for loops and stalled tools. `--hard-token-limits` is reserved for explicit budget-boundary tests.

## Execution sequence

1. Run deterministic capability probes in a pinned task container. Record harness support before spending model requests.
2. Run the local micro evaluation and repository tests. Correct runtime contract failures before benchmark work.
3. Run one Luna `low` attempt on capability-search tasks when a cheap trajectory can reveal obvious tool or adapter failures.
4. Run one Sol `low` attempt on the remaining capability-search tasks. Run Sol `xhigh` only on Sol `low` failures.
5. Repeat the most promising Sol gap three times at each reasoning setting. Freeze the selected task and gap criterion.
6. Produce typed trajectory diagnoses. Each diagnosis names its model setting and retained run. The digest groups verified results by task and model setting. It retains request growth calculated within each episode, replayed results, failures, and log sequence numbers under fixed tree-wide bounds.
7. Run identity-bound self-improvement with Luna `high` for bounded diagnosis and Terra `high` for implementation. The coding node receives only the typed diagnosis and acts as a full coding agent.
8. Validate the generated candidate outside the self-improvement episode. Implement the diagnosed change directly when the generated candidate is absent, invalid, or unsupported by the evidence.
9. Re-run the selected capability task with Sol `low`. Reject candidates that fail the capability-conversion criteria.
10. Re-run the six development tasks. Compare successful completion, estimated cost, wall time, and trace integrity.
11. Freeze the candidate source tree, binary digest, model settings, acceptance rule, and confirmation task list.
12. Run two attempts on each confirmation task. Reject the candidate when the confirmation criteria fail.
13. Freeze one calibration attempt per task. Run the twelve calibration tasks, then record the result before opening the six calibration-holdout tasks.
14. Decide whether the evidence supports a full Terminal-Bench 2.1 run.

## Self-improvement contract

The self-improvement workflow has two model nodes. A Luna diagnosis node reads
the bounded trajectory digest and returns a typed causal intervention. A
separate Terra coding node receives the diagnosis and a clean context. The
intervention must improve the lower-cost evaluated configuration. It preserves
the model route, reasoning effort, task allowances, token policy, and task set.
Higher-cost successful settings supply diagnostic contrasts.

The coding node has Foe's standard coding tools: `read`, `grep`, `edit`, and
`bash`. It may change runtime crates, specifications, examples, and repository
build files. It cannot change evaluation code, benchmark material, model
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
