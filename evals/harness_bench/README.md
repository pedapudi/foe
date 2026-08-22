# Foe Harness-Bench development evaluation

This package runs Foe against four development tasks from Harness-Bench. Bazel
downloads the benchmark at commit
`1025086a446653702b80cfb48babbeec35db6b2c` and verifies its archive digest.
The runner copies each task fixture into a fresh workspace. It uses the task's
unchanged prompt, setup hook, and programmatic grader.

The four tasks cover untrusted instructions, SQLite migration safety, a local
paginated API, and cross-package Python repair. They form a diagnostic sample.
Their results do not constitute an official Harness-Bench score.

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

Each attempt retains its workspace, Foe program, complete episode tree,
negative-control grade, final programmatic grade, usage, trace result, and
process outcome. Grading runs after Foe exits. The grader source remains
outside Foe's read and write authority.

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
It excludes grader source and benchmark fixtures.

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

The workflow has one deterministic evidence node and one terminal model node.
The model node may change runtime source, adjacent Rust tests, and affected
specifications. It cannot write evaluation code, benchmark adapters, tasks,
graders, budgets, or model routes. The generated checker also rejects a
candidate without a Rust regression test and a specification update.

The workflow's checker validates candidate shape. Repository tests, formatting,
clippy, line budgets, and matched evaluation reruns remain external acceptance
gates.
