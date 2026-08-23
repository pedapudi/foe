# Terminal-Bench development evaluation record

This record describes the Foe evaluation work completed on 2026-08-23. It
preserves aggregate evidence and promotion decisions. Raw jobs remain under
ignored `target/terminal-bench-jobs/` directories because they contain large
task artifacts and complete model trajectories.

## Evaluation authority and scope

The runs use `terminal-bench/terminal-bench-2-1@6`. The pinned Harbor Hub
record lists 89 tasks. The Terminal-Bench 2.1 release corrected 28 tasks and
introduced continuous validation. These facts come from the
[official dataset record](https://hub.harborframework.com/datasets/terminal-bench/terminal-bench-2-1/6)
and [release description](https://www.tbench.ai/news/terminal-bench-2-1).

Harbor installs the Foe binary inside each task container and runs the
task-owned verifier after Foe exits. This follows Harbor's
[installed-agent contract](https://www.harborframework.com/docs/agents).
The official
[Terminal-Bench 2.1 leaderboard](https://www.tbench.ai/leaderboard/terminal-bench/2.1)
remains the authority for a full comparative score. The six cases in this
record support local development decisions. They do not constitute an
official benchmark result.

Every model-backed run used `openai-codex/gpt-5.6-sol` with low reasoning
effort. The provisional estimated-cost metric is provider-reported input plus
output tokens. This proxy is valid for the comparisons here because the model
route and model settings remain fixed. Input, output, and cache-read tokens
remain separate in the retained trial records.

Foe episode time runs from `episode/start` to the final recorded event. Harbor
trial time also includes container and verifier overhead. The tables use Foe
episode time because the evaluated harness controls that interval.

## Four-case development comparison

The unmodified adapter runtime used binary
`sha256:587d8416f112c46e1fcfbb7dd668ad311da1e6547b74aa19f78cf13faba12101`
and source tree `git-tree-sha1:f28be6e8b437afc5be965928b09784402f719591`.
The candidate bundle used binary
`sha256:f6d5d9b542c4e55e4bdb0c75a57ff0a4870967081fb5d2ecbb8c98a6f93c11fd`
and source tree `git-tree-sha1:7fc033221436ff392710524a0fec89a3a2c76d4a`.
The bundle enabled deterministic tool-result aging and added the standard
system administration directories to the fixed shell path.

| task | unmodified accepted | candidate accepted | unmodified cost proxy | candidate cost proxy | change |
|---|---:|---:|---:|---:|---:|
| `cancel-async-tasks` | 1/1 | 1/1 | 10,601 | 7,765 | -26.8% |
| `fix-git` | 1/1 | 1/1 | 57,665 | 52,350 | -9.2% |
| `git-multibranch` | 1/1 | 1/1 | 108,434 | 72,123 | -33.5% |
| `sqlite-db-truncate` | 1/1 | 1/1 | 16,488 | 11,587 | -29.7% |
| **total** | **4/4** | **4/4** | **193,188** | **143,825** | **-25.6%** |

The unmodified run reported 186,175 input tokens, 7,013 output tokens, 63,488
cache-read tokens, 33 model calls, and 192.6 seconds of Foe episode time. The
candidate reported 135,614 input tokens, 8,211 output tokens, 31,744 cache-read
tokens, 32 model calls, and 210.3 seconds. The cost proxy improved while Foe
episode time increased by 9.2%. One attempt per task supports a directional
bundle result. It does not attribute the task-level differences to either
candidate mechanism.

Both `fix-git` workspaces passed the task verifier after Foe reported
`exhausted` on the model-call allowance. The final state was correct, while
the harness outcome classified the episode as unfinished. Future completion
work should address that mismatch through verifier-aware completion or a
measured final-request policy. Raising every allowance would weaken resource
control and would leave the completion signal undefined.

## Fixed shell path

The focused `git-multibranch` comparison used three trials for each
configuration. Every unmodified trajectory encountered at least one missing
administration command. No candidate trajectory encountered that error or
issued a path-recovery command.

| configuration | accepted | mean cost proxy | mean model calls | mean Foe episode time |
|---|---:|---:|---:|---:|
| unmodified runtime | 3/3 | 88,977 | 9.67 | 75.1 s |
| runtime with administration directories | 3/3 | 40,781 | 7.67 | 61.0 s |

Two candidate trials never activated context aging. One candidate trial
recorded an aging checkpoint. The eliminated command failures are direct
mechanistic evidence for the shell-path change. Model sampling and the
additional opt-in feature make the full 54.2% cost reduction and 18.7%
episode-time reduction directional evidence.

[Pull request #81](https://github.com/pedapudi/foe/pull/81) contains only the
shell-path change on top of `main`. It is suitable for review as an efficacy
and compatibility fix.

## Deterministic aging and archived retrieval

The initial four-case aging run accepted 4/4 tasks and used a 135,526-token
cost proxy, 29.8% below the unmodified four-case run. A three-trial
`git-multibranch` confirmation produced a different result.

| configuration | accepted | mean cost proxy |
|---|---:|---:|
| unmodified runtime | 3/3 | 88,977 |
| aging runtime | 2/3 | 90,706 |

The failed aging trial contained an incorrect deployment rather than a
missing aged observation. A verification-instruction variant also passed two
of three trials, so it provided no accuracy improvement. No evaluated episode
called `retrieve`. The runs therefore establish neither an accuracy-safe
aging policy nor an efficacy result for archived retrieval.

The aging and retrieval implementation remains on
`eval/terminal-bench-aging-retrieval`. It should remain outside `main` until a
task set activates checkpoints and retrieval in repeated trials without an
accuracy regression.

## Streaming repository search

One `sanitize-git-repo` candidate trial issued two broad `grep` calls in the
same model step. Each search read every visited file into a complete in-memory
buffer under the earlier implementation. The Foe process ended with exit 137
before either result was recorded, and the task failed.

The search implementation now opens each file through the descriptor-bound
reader and passes the stream to `grep-searcher`. Three confirmation trials
each issued multiple broad `grep` calls in at least one model step. All three
processes completed, all task verifiers passed, and all traces conformed.

| implementation | accepted | process result | successful-run cost proxies |
|---|---:|---|---|
| whole-file search | 0/1 | exit 137 after 1,326 tokens | none |
| streaming search | 3/3 | exit 0 in every trial | 154,709; 241,868; 292,144 |

The cost values cannot support an efficiency comparison because the failed
process stopped after its first model response. The repeated activation and
the code-level regression test support the resource-safety claim.

[Pull request #82](https://github.com/pedapudi/foe/pull/82) contains only the
streaming capability and search changes on top of `main`. It is suitable for
review as a reliability fix.

## Tighter result bounds

A candidate with smaller per-result limits accepted all four development
tasks and reduced the single-run cost proxy by 21.9%. No shortening notice
appeared in those trajectories, so the proposed bounds never activated. A
three-trial `git-multibranch` comparison reduced mean cost by 3.3% while mean
model calls increased from 9.67 to 11.33.

The result-bounds candidate is rejected. Its observed savings lack a causal
path to the changed mechanism. A future bounds experiment needs a task fixture
that produces a shortening notice and verifies that the model can recover the
required omitted segment.

## Holdout observations

The unmodified and candidate `large-scale-text-editing` trials both passed.
Their cost proxies were 14,279 and 8,647. The candidate recorded no aging
checkpoint, so the one-attempt difference is sampling evidence rather than an
aging result.

The unmodified `sanitize-git-repo` trial passed with a cost proxy of 183,781.
The pre-streaming candidate failed with exit 137. The three streaming
confirmations passed with a mean cost proxy of 229,574. These samples support
the streaming reliability fix. They do not show a holdout cost improvement.

## Decisions and remaining work

The evidence supports three repository changes:

1. Review the pinned Harbor adapter in
   [pull request #80](https://github.com/pedapudi/foe/pull/80).
2. Review the fixed administration-command path in
   [pull request #81](https://github.com/pedapudi/foe/pull/81).
3. Review streaming repository search in
   [pull request #82](https://github.com/pedapudi/foe/pull/82).

All three pull requests remain unmerged. Context aging, archived retrieval,
and tighter result bounds remain experimental. A subsequent efficacy study
should use repeated cases that activate the proposed mechanism, preserve a
hidden confirmation set, and report accepted-task cost distributions rather
than a single aggregate.

The `read` tool still allocates a complete file before selecting its bounded
line window. Streaming `grep` removes the failure observed in the holdout
trial. A separate windowed-reader design is required to give `read` the same
memory bound without changing its line-offset contract.
[Issue #83](https://github.com/pedapudi/foe/issues/83) tracks that work and its
stream-boundary tests.

## Local evidence index

The aggregate numbers above were recalculated from these ignored directories:

- unmodified development:
  `target/terminal-bench-jobs/development-baseline-valid-20260823T070848Z`
- unmodified `git-multibranch` confirmations:
  `target/terminal-bench-jobs/git-multibranch-baseline-confirmation-20260823T072535Z`
- aging development:
  `target/terminal-bench-jobs/development-aging-retrieval-20260823T074725Z`
- aging confirmations:
  `target/terminal-bench-jobs/aging-retrieval-confirmation-20260823T075616Z`
- administration-path confirmations:
  `target/terminal-bench-jobs/standard-admin-path-20260823T081937Z`
- candidate development:
  `target/terminal-bench-jobs/development-standard-admin-path-20260823T082530Z`
- unmodified holdout:
  `target/terminal-bench-jobs/holdout-baseline-20260823T083143Z`
- pre-streaming holdout:
  `target/terminal-bench-jobs/holdout-standard-admin-path-20260823T083740Z`
- streaming-search confirmations:
  `target/terminal-bench-jobs/sanitize-streaming-grep-20260823T084754Z`

The unmodified directories are under the adapter worktree. Candidate
directories are under the aging and retrieval worktree. Each campaign
manifest records the source-tree digest, runtime-binary digest, task list,
allowances, model route, and reasoning effort.
