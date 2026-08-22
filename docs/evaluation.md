# Evaluation

foe has two evaluation layers. The deterministic layer checks runtime
guarantees from complete episode logs. The model-backed layer measures task
completion, reliability, and resource use under fixed benchmark conditions.

The layers answer different questions. Runtime conformance establishes that
the harness enforced and recorded its contract. Task benchmarks establish how
well a model and foe complete useful work together.

## Deterministic runtime conformance

The dependency-free suite under `evals/` runs scripted model responses through
the built foe binary. Each case produces an ordinary episode directory. The
trace evaluator then checks the episode log and every child log.

| guarantee | generated case | conformance condition |
|---|---|---|
| declared authority | granted and forbidden built-in reads | The forbidden read returns an error. |
| reconstructable evidence | every generated episode | Ordinary requests and tool results derive from prior events. |
| typed outcomes | four termination cases | Each outcome uses its closed variant and expected process exit. |
| hierarchical budgets | a workflow model-node child | Reservations, releases, and measured child spend agree. |
| workflow provenance | a model choice and terminal tool node | Firings and branches follow the declared graph. |
| compaction continuity | two reads and a forced compaction | Typed state preserves obligations and successful file calls. |

The default authority case uses foe's capability handles with the kernel
sandbox disabled. The case therefore runs on systems without Landlock. A
stronger optional case runs `/usr/bin/cat` under `sandbox.mode: required` and
requires the kernel to permit one read and deny another.

The runner also corrupts one trace for each guarantee. Each corruption must
produce a violation in the matching conformance dimension. These mutation
checks guard against an evaluator that accepts every input.

The termination cases cover completed, blocked, exhausted, and failed
episodes. The completed case also checks a declared return schema.

Run the portable suite:

```sh
bazel test //evals:conformance_tests
```

Run the generated episodes and print their report:

```sh
bazel run //evals:runtime-evals
```

Run the same evaluation with a Cargo build:

```sh
cargo build -p foe
python3 evals/run_runtime_evals.py --foe target/debug/foe
```

Require the Landlock executable probe:

```sh
bazel run //evals:runtime-evals -- --include-kernel-sandbox
```

Score an existing episode tree:

```sh
python3 evals/trace_quality.py --pretty .foe/ep_example
```

The JSON report uses `conformant` as the result for each guarantee.
`covered_episodes` states how many episode logs exercised that guarantee.
Assertion counts are diagnostic coverage. They are not task-quality scores.

The `observations` object reports facts that affect interpretation. These
facts include observed Landlock ABIs, denied capability calls, child counts,
workflow counts, and successful compactions.

## Model-backed task quality

Agent capability belongs to a specific model and harness configuration.
[Harness-Bench](https://arxiv.org/abs/2605.27922) evaluates configurations
under shared tasks, budgets, timeouts, and evaluators while preserving each
harness's execution behavior. foe results follow the same comparison unit.

Every reported configuration identifies:

- the foe program identity and runtime build hash;
- the provider, model identifier, and model options;
- the benchmark name, dataset version, and task identifier;
- the root token, model-call, episode, concurrency, and time limits;
- the sandbox mode and observed Landlock ABI;
- the number of independent attempts per task.

The benchmark's executable evaluator decides task completion. foe's outcome
and conformance report remain separate fields. This separation distinguishes a
correct artifact from the runtime path that produced it.

### Low-cost assessed suite

The model-backed micro evaluation runs five small tasks with executable
graders. Each external grader lives outside the episode's read and write
grants and is absent from its configuration. Code tasks expose a separate
visible verifier. Typed-return tasks expose only the return schema.

| task | assessed behavior | token limit | model-call limit |
|---|---|---:|---:|
| untrusted instruction containment | repairs code while ignoring an instruction embedded in generated project data | 6,000 | 4 |
| typed configuration evidence | returns a schema-checked finding with a path and JSON Pointer that resolve against the repository | 5,000 | 4 |
| delegated order quotation | combines reports from two read-only child episodes into a verified code repair | 24,000 | 16 |
| declared migration workflow | chooses and applies a safe migration through declared evidence, decision, and application nodes | 5,000 | 6 |
| compaction ledger continuity | resolves linked state after a required context compaction and cites the source of every final value | 16,000 | 10 |

Each attempt declares limits totaling 56,000 input-plus-output tokens and 40
model calls. The root budget includes child and compaction requests. The
report gives actual usage because one provider response can cross a remaining
token limit before foe records and enforces that limit.

Run one attempt per task with a configured provider credential:

```sh
bazel run //evals:micro -- --model openai/gpt-5.6-sol
```

Keep the workspaces, configurations, logs, and JSON report for inspection:

```sh
bazel run //evals:micro -- \
  --model openai/gpt-5.6-sol \
  --keep target/foe-micro-eval
```

Run one task while developing its fixture or mechanism check:

```sh
bazel run //evals:micro -- \
  --model openai/gpt-5.6-sol \
  --task compaction-ledger-continuity
```

The primary result is the strict success count. An attempt succeeds strictly
when all five component checks pass:

- `artifact_correct`: the external executable grader accepts the workspace or returned value;
- `outcome_correct`: foe records a completed outcome;
- `mechanism_exercised`: the required authority, typed evidence, child, workflow, or compaction evidence appears in the log;
- `trace_conformant`: the deterministic trace evaluator finds no contract violation;
- `within_budget`: reported usage is present and stays within the task's call and token limits.

The report preserves every component beside the strict result. A correct
workspace left by an exhausted episode therefore remains visible as artifact
success and outcome failure.

The default single attempt declares 56,000 tokens across its five tasks. Use
two attempts per task for an initial reliability result:

```sh
bazel run //evals:micro -- \
  --model openai/gpt-5.6-sol \
  --attempts 2
```

Two attempts declare 112,000 tokens and 80 model calls. The
`tasks_strict_in_every_attempt` field contains tasks that passed strictly on
every attempt. Use the larger external benchmarks below for capability claims
across broader task distributions.

The grader controls require no model credential. Every untouched fixture must
fail its grader, and every oracle artifact must pass:

```sh
bazel test //evals:micro_tasks_test
```

### Comparable metrics

Each benchmark report includes these metrics:

| metric | definition |
|---|---|
| task completion rate | attempts accepted by the benchmark evaluator divided by all launched attempts |
| reliable task rate | tasks whose every attempt passed, reported with the attempt count |
| outcome distribution | proportions of `completed`, `blocked` by code, `exhausted` by limit, and `failed` |
| conformance rate | attempts with no deterministic trace violation divided by launched attempts |
| successful-run tokens | median and 90th percentile of input plus output tokens among accepted attempts |
| successful-run calls | median and 90th percentile of model and tool calls among accepted attempts |
| successful-run duration | median and 90th percentile wall time among accepted attempts |
| policy-denial rate | forbidden actions denied divided by forbidden actions attempted in policy-bearing tasks |

Report cache-read tokens beside input and output tokens. Report incomplete and
infrastructure-failed attempts in the denominator and in the outcome
distribution. A separate infrastructure-failure field keeps deployment faults
visible.

Use at least three attempts per task for a comparison. Pair configurations by
task and model route. Keep task containers, evaluators, aggregate budgets, and
timeouts fixed. Report the per-task results so aggregate differences remain
auditable.

The [Inspect evaluation checklist](https://github.com/UKGovernmentBEIS/inspect_evals/blob/main/EVALUATION_CHECKLIST.md)
provides additional evaluator controls. Applicable controls include an oracle
run, negative controls, trajectory review, and proof that the scorer can
produce success and failure.

## Benchmarks selected for foe

### Harness-Bench

Status: recommended first integration.

[Harness-Bench](https://github.com/Qihoo360/harness-bench) contains 106
sandboxed offline tasks across eight workflow categories. Its protocol records
artifacts, traces, usage, and validator outputs under shared budgets and
timeouts. Executable oracles grade task completion where feasible.

Harness-Bench is a 2026 preprint. Treat cross-harness conclusions as
provisional until independent reproductions are available.

Start with its software-engineering, long-running autonomy,
permission-sensitive, and evidence-grounded categories. These categories
exercise foe's grants, bounded execution, complete logs, and recovery behavior.
Run the entire selected category so task choice cannot favor one
configuration.

Some process-quality fields use a model judge. Report those fields as
diagnostics. Executable completion remains the primary result.

### Terminal-Bench

Status: recommended terminal-capability integration.

The [Terminal-Bench repository](https://github.com/harbor-framework/terminal-bench)
defines task-specific containers and programmatic final-state tests. A fixed
smoke set from software engineering, system administration, security, and data
work exercises autonomous terminal use across varied environments.

Pin the dataset version and task identifiers in each report. Use three attempts
per task before a full run. Terminal-Bench completion alone does not establish
foe's trace or authority guarantees, so every attempt also receives the local
conformance evaluation.

### CompactBench

Status: recommended provisional compaction regression.

[CompactBench](https://github.com/compactbench/compactbench) probes which
decisions, facts, entities, and forbidden behaviors survive repeated context
replacement. Run its public suite with foe compaction enabled. Compare it with
an otherwise identical configuration whose context fits without compaction.

The project is recent and lacks peer-reviewed validation. Treat its results as
a regression signal. Keep foe's deterministic task, completion-condition, and
file-list checks as the conformance authority.

### AgentDojo

Status: recommended security integration after host-tool adapters exist.

[AgentDojo](https://github.com/ethz-spylab/agentdojo) combines benign tasks
with prompt-injection attacks in stateful tool environments. Its
[paper](https://arxiv.org/abs/2406.13352) reports task utility and attack
success separately. That separation fits foe's distinction between useful
completion and unauthorized effects.

Map AgentDojo tools to foe host tools with declared effects. Score benign task
completion, attack success, and foe policy denial as separate metrics. The
simulated services exercise tool authority and untrusted observations. They do
not replace the Landlock executable probe.

### SWE-bench Verified

Status: secondary coding baseline.

The [SWE-bench harness](https://www.swebench.com/SWE-bench/reference/harness/)
applies a candidate patch in a reproducible container and runs repository
tests. SWE-bench Verified measures repository-level issue resolution with a
widely used executable scorer.

Use SWE-bench after the harness-focused integrations. Its pass rate measures
coding outcome well. It provides limited direct pressure on declared
authority, typed outcomes, hierarchical budgets, and reconstructable logs.

### RE-Bench

Status: later comparison for episode trees.

[RE-Bench](https://github.com/METR/RE-Bench) contains seven open-ended machine
learning research-engineering environments with continuous scorers. Compare a
single foe episode with a foe episode tree under the same aggregate token,
model-call, episode-count, concurrency, and wall-time limits.

The benchmark requires substantial compute and covers one specialized domain.
Use it after the portable conformance suite and the broader task benchmarks.

## Adapter contract

An external benchmark adapter performs four operations:

1. It creates a foe configuration with absolute workspace paths and a root
   budget equal to the benchmark limit.
2. It runs foe inside the benchmark environment and retains the complete log
   tree as the trajectory artifact.
3. It submits the resulting workspace or patch to the benchmark's unchanged
   evaluator.
4. It joins benchmark completion, foe outcome, resource use, and conformance
   by dataset version, task identifier, and attempt identifier.

The benchmark evaluator runs outside the episode's write grant. An episode
therefore cannot alter its grader. Child programs reserve from the same root
budget, which keeps single-episode and multi-episode configurations comparable.

## Current limits

The deterministic cases use scripted responses, so they measure runtime
mechanics without measuring model judgment. The JSON Schema checker covers the
types, required fields, properties, arrays, items, and enums used by the
generated typed-return case. It is not a complete JSON Schema implementation.

The suite does not yet cover retries, teams, peer delivery, replay, forks,
workflow recovery, symlink escapes, or network denial. New cases should add a
passing trace, a targeted corruption, and one stated conformance condition.

Ordinary request messages are independently reconstructed. The compaction
checks link each summary to its recorded request and response. They do not yet
reconstruct the summarization prompt from the covered transcript.
