# foe Harness-Bench development and confirmation evaluation

This package runs foe against four development tasks from Harness-Bench. Bazel
downloads the benchmark at commit
`1025086a446653702b80cfb48babbeec35db6b2c` and verifies its archive digest.
The runner copies each task fixture into a fresh workspace. It uses the task's
unchanged prompt, setup hook, and programmatic grader.

The four tasks cover untrusted instructions, SQLite migration safety, a local
paginated API, and cross-package Python repair. They form a diagnostic sample.
Their results do not constitute an official Harness-Bench score.

A separate target runs two confirmation tasks that remain outside the
self-improvement workflow's authority. The tasks cover flaky-test diagnosis
and offline answers that must identify insufficient evidence. Run the
confirmation target only after freezing a candidate and its comparison
configuration. The adapter creates an isolated Python environment with
`pytest` for the flaky-test task. Both the model-visible test tool and the
unchanged grader use that environment. The test wrapper resolves the Python
executable relative to its own location, so its content and Foe program
identity remain stable across retained attempt directories.

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

Each attempt retains its workspace, Foe program, complete episode tree,
negative-control grade, final programmatic grade, usage, trace result, and
process outcome. Grading runs after Foe exits. The grader source remains
outside Foe's read and write authority.

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

Create a clean disposable worktree before launching self-improvement:

```sh
git worktree add -b experiment/evidence-guided-runtime-improvement \
  /tmp/foe-evidence-guided-runtime-improvement HEAD
```

Preview the optimization allowance:

```sh
bazel run //evals/harness_bench:self-improve -- \
  --candidate /tmp/foe-evidence-guided-runtime-improvement \
  --evidence "$PWD/target/foe-self-improvement-evidence.json"
```

Launch one bounded workflow after reviewing the preview:

```sh
bazel run //evals/harness_bench:self-improve -- \
  --candidate /tmp/foe-evidence-guided-runtime-improvement \
  --evidence "$PWD/target/foe-self-improvement-evidence.json" \
  --keep target/foe-evidence-guided-runtime-improvement \
  --confirm-spend
```

The workflow has one deterministic evidence node and two model nodes. A
three-request diagnosis node reads the assessed evidence and returns a typed,
file-specific proposal. A fresh nine-request coding node receives the proposal
without the raw benchmark evidence. This separation prevents broad diagnostic
tool output from entering every implementation request.

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
reruns remain external acceptance gates. The measured development results and
candidate disposition are recorded in
[`docs/harness-benchmark-campaign.md`](../../docs/harness-benchmark-campaign.md#foe-only-development-experiment).
