# foe Harness-Bench development and confirmation evaluation

This package runs foe against four development tasks from Harness-Bench. Bazel
downloads the benchmark at commit
`1025086a446653702b80cfb48babbeec35db6b2c` and verifies its archive digest.
The Bazel repository rule applies `knowledge_qa_claim_scoring.patch`. The
runner records the patch digest with the benchmark commit. The patch prevents
the offline question grader from treating quoted rejected evidence as a
fabricated answer. It also makes the displayed component weights match the
scoring formula.

The runner copies each task fixture into a fresh workspace. It uses the task's
prompt, setup hook, and patched programmatic grader. It also grades an
untouched workspace as a negative control, which must fail before an agent
result can be interpreted. The offline question grader has an additional
author-written oracle control. The control quotes rejected evidence and must
receive a perfect score. A corrupted answer must still activate the score cap.

The four tasks cover untrusted instructions, SQLite migration safety, a local
paginated API, and cross-package Python repair. They form a diagnostic sample.
Their results do not constitute an official Harness-Bench score.
Harness-Bench is an external diagnostic fixture source. Its results do not
support comparisons with other harnesses.

A separate target runs two confirmation tasks that remain outside the
self-improvement workflow's permissions. The tasks cover flaky-test diagnosis
and offline answers that must identify insufficient evidence. A candidate is
one source commit paired with one comparison configuration. Run the
confirmation target only after freezing both. The adapter creates an isolated
Python environment with `pytest` for the flaky-test task. Both the model-visible
test tool and the unchanged grader use that environment. The test wrapper
resolves the Python executable relative to its own location, so its Foe contract
fingerprint remains stable across retained attempt directories.

## Review the maximum spend

The command without `--confirm-spend` performs no model call. It prints each
task's input-token, output-token, model-call, and wall-time limits.

```sh
bazel run //evals/harness_bench:foe-development
```

Select one task while checking the adapter:

```sh
bazel run //evals/harness_bench:foe-development -- \
  --task 083-monorepo-interface-repair
```

## Run and retain the assessed workspaces

The retained directory must be absent or contain no attempt with the selected
task and attempt number.

```sh
bazel run //evals/harness_bench:foe-development -- \
  --model openai-codex/gpt-5.6-sol \
  --keep target/foe-harness-bench-development \
  --confirm-spend
```

Set one reasoning effort for every selected task when measuring the quality,
token, and latency tradeoff of that model option:

```sh
bazel run //evals/harness_bench:foe-development -- \
  --model openai-codex/gpt-5.6-sol \
  --reasoning-effort medium \
  --keep target/foe-harness-bench-medium-reasoning \
  --confirm-spend
```

Each attempt retains its workspace, Foe contract, complete episode tree,
negative-control grade, final programmatic grade, usage, trace result, and
process outcome. Grading runs after Foe exits. The grader source remains
outside Foe's read and write permissions.

The development and confirmation reports identify the evaluated Foe source
with the Git tree object of a clean checkout. They identify the executable
with its SHA-256 digest. A runner refuses a checkout with tracked or untracked
changes before it launches a model request.

The retained directory is local evaluation evidence. Keep raw trajectories,
copied task workspaces, negative controls, Python environments, build output,
and provider signatures under an ignored directory such as `target/`. Store
long-lived local evidence in a compressed archive with a checksum manifest.
Git tracks the runner, tests, configuration, documentation, and reviewed
aggregate results.

## Run the frozen confirmation comparison

Preview each three-attempt configuration before launching model calls:

```sh
bazel run //evals/harness_bench:foe-confirmation -- \
  --attempts 3 \
  --reasoning-effort medium \
  --keep target/foe-harness-bench-confirmation-medium
```

After reviewing the maximum, add `--confirm-spend`. Repeat the command with
`--reasoning-effort low` and a distinct retained directory. The two tasks,
two settings, and three attempts produce twelve attempts. Do not give the
confirmation reports to self-improvement until the candidate disposition is
recorded.

## Prepare evidence for self-improvement

Run the model-backed micro evaluation and retain its report before collecting
the combined evidence.

```sh
bazel run //evals/harness_bench:collect-evidence -- \
  --micro-report target/foe-micro-eval/report.json \
  --harness-report target/foe-harness-bench-development/report.json \
  --output target/foe-self-improvement-evidence.json
```

The evidence contains component failures, grader findings, request-token
progression, bounded tool-result replay attribution, and the final outcomes.
It excludes grader source, benchmark fixtures, and completed return values.
The collector writes compact JSON and rejects reports larger than 20,000
bytes. This bound keeps the model input focused on assessed failures and
resource use.

The micro and Harness-Bench reports must name the same source tree and runtime
binary fingerprint. The collector rejects a missing value or mismatch and
carries the evaluated pair into the evidence file. A prior self-improvement
result supplied with `--optimization-result` must carry the same pair.

Create a clean sibling worktree before launching self-improvement:

```sh
git worktree add -b experiment/evidence-guided-runtime-improvement \
  ../foe-evidence-guided-runtime-improvement HEAD
```

Preview the optimization allowance:

```sh
bazel run //evals/harness_bench:self-improve -- \
  --candidate ../foe-evidence-guided-runtime-improvement \
  --evidence "$PWD/target/foe-self-improvement-evidence.json"
```

Launch one bounded workflow after reviewing the preview:

```sh
bazel run //evals/harness_bench:self-improve -- \
  --candidate ../foe-evidence-guided-runtime-improvement \
  --evidence "$PWD/target/foe-self-improvement-evidence.json" \
  --keep target/foe-evidence-guided-runtime-improvement \
  --confirm-spend
```

The workflow has one deterministic evidence node and two model nodes. A
three-request diagnosis node reads the assessed evidence and returns a typed,
file-specific proposal. A fresh nine-request coding node receives the proposal
without the raw benchmark evidence. This separation prevents broad diagnostic
tool output from entering every implementation request.

Before creating an episode directory, the runner validates the candidate and
runtime against the evidence. The candidate checkout must be clean. Its Git
tree must match the evidence source tree. The Foe binary that runs the workflow
must match the evidence runtime digest. A mismatch consumes no model budget and
reports both values.

The coding node may change runtime source, adjacent Rust tests, and affected
specifications. It cannot write evaluation code, benchmark adapters, tasks,
graders, budgets, or model routes. It receives the four coding tools available
to a default Foe coding episode: source reads, search, structured edits, and a
contained shell. The generated checker adds a deterministic acceptance tool.

The generated checker requires a runtime source change, a Rust regression
test, and an affected specification. It also rejects trailing whitespace,
benchmark-task identifiers, and violations of repository line budgets. The
workflow allows at most 12 model calls, 300,000 input tokens, 20,000 output
tokens, and 1,200 seconds. Its result records measured token use and the typed
episode outcome.

Compilation, repository tests, formatting, clippy, and matched evaluation
reruns remain external acceptance gates. Record reviewed aggregate results and
the candidate disposition on the campaign branch. Keep the underlying raw
evidence in its checksummed local archive.
