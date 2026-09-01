# Cross-trajectory capability analysis

This historical analysis records Foe evaluation evidence retained through
2026-08-24T003342Z. It identifies product changes that can improve accuracy,
cost, latency, and self-improvement. It also defines the tests that must pass
before each change influences calibration claims.

The score authority is Terminal-Bench 2.1 at the dataset revision pinned in
[`cases.json`](cases.json). Terminal-Bench 2.1 contains 89 container tasks and
requires at least five trials per task for an official submission. Its
[dataset repository](https://github.com/harbor-framework/terminal-bench-2-1)
defines the submission rules. Its
[release record](https://www.tbench.ai/news/terminal-bench-2-1) explains the
corrections and continuous validation added to 28 tasks.

This analysis supports development decisions. The task distribution includes
38 `gpt2-codegolf` trials and 19 `git-multibranch` trials because those tasks
received focused experiments. The aggregate pass rate therefore does not
estimate full-benchmark accuracy.

## Retained evidence

The audit read every retained trial result under seven local worktrees. It
then read every available root and child `episode.jsonl`, structured verifier
report, and campaign manifest. The final included run was the Luna-diagnosis
`gpt2-codegolf` run named
`luna-high-diagnosis-sol-low-correctable-backstop-20260824T003342Z`.

Raw jobs remain local because they contain task workspaces, credentials copied
for container use, large tool results, and benchmark material. This file and
the bounded diagnostic implementation are the Git-retained evidence. The
diagnostic schema is implemented in
[`trajectory_diagnostics.py`](trajectory_diagnostics.py).

| Evidence property | Count |
| --- | ---: |
| trial result records | 113 |
| trials with a Foe root log | 103 |
| root logs with a recorded outcome | 102 |
| task-verifier passes among logged assessed trials | 68 |
| task-verifier failures among logged assessed trials | 35 |
| trials without a Foe log | 10 |
| root outcomes reported as completed | 86 |
| root outcomes reported as exhausted | 14 |
| root outcomes reported as failed | 2 |

Five missing-log trials were early installation checks. Four missing-log
trials came from an invalid development run before the adapter setup was
corrected. One missing-log trial exercised a pre-streaming search candidate.
These records remain infrastructure evidence and do not support model-quality
claims.

The 103 logged assessed trials used 1,315 model calls. Provider reports record
14,233,794 input tokens, 653,414 output tokens, and 7,287,808 cached-input
tokens. Estimated cost totals $32.14 under the price table recorded with each
run. Some early runs did not record a price, so this total is a lower bound on
the retained campaign spend.

Foe prices each request from provider-reported usage. The campaign table uses
the official per-million-token rates for
[GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol),
[GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra),
and [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna).
The calculation also applies each published cached-input rate and long-context
multiplier.

## Configuration failures obscured useful work

Seven task-verifier passes ended with Foe reporting exhaustion. Five were
`fix-git` runs whose final permitted request produced an accepted repository.
Earlier task programs also divided a fixed root call allowance between
diagnosis and implementation. One implementation produced an accepted
repository on its final child call and then reported exhaustion.

The evaluation adapter gives every implementation at least 60 model calls
and 1,800 seconds. Diagnosis and repair allowances are additive. Input and
output tokens are measured without hard allowances during efficacy tests.
Eight identical calls or turns establish a loop. These settings preserve
strict hard limits for explicit boundary tests through
`--hard-token-limits`.

A retained rerun tested the corrected composition. Luna used five of six
diagnosis calls, and Sol used ten implementation calls. Foe completed after
15 of 66 permitted calls. The task verifier failed on semantic output, so the
remaining defect is independent of budget admission.

The product default for repeated calls is now eight. Model-backed examples
use 40 calls and 900 seconds with measured token use. These changes reduce the
chance that copied examples reproduce the evaluation failures.

## Completion claims often exceed the validation evidence

Thirty-two logged trials disagreed between Foe outcome and task-verifier
result. Seven were accepted artifacts reported as exhausted. Twenty-five were
failed artifacts reported as completed.

Nineteen completed-but-failed trials contained an edit. Eighteen of those
trials ran at least one successful `bash` command after the final edit. A
successful command therefore provides weak evidence unless the command tests
the required behavior.

The failed `gpt2-codegolf` rerun illustrates the gap. The implementation
compiled `gpt2.c`, checked its byte count, and exercised error paths. It did
not establish the required continuation. The task verifier ran the real
prompt and observed twenty repetitions of `Damien` instead of the expected
license text.

General instructions to run tests cannot close this gap. Foe needs a
challenge-oriented validation stage that asks whether the strongest available
check distinguishes a plausible wrong implementation. The stage should run in
a fresh child so failed exploration does not consume its context. It should
activate for tasks whose implementation reports only compilation, formatting,
or error-path checks.

The first experiment uses a model workflow rather than a kernel rule. A fresh
Sol `high` or `xhigh` child receives the task, the implementation claim, and a
bounded validation summary. It audits the shared workspace and repairs any
defect it finds. Promotion requires improved verifier accuracy on activation
tasks and no verifier regression on the six development tasks. Tokens,
estimated cost, and elapsed time remain recorded measurements.

## Selective reasoning converts capability at lower cost

The retained source tree at `git-tree-sha1:47ac3952a004a79513ac609beed51ead738290b2`
provides the cleanest `gpt2-codegolf` comparison.

| Configuration | Valid attempts | Passes | Mean estimated cost |
| --- | ---: | ---: | ---: |
| Sol `low` in one episode | 3 | 1 | $0.371 |
| Sol `xhigh` in one episode | 1 | 1 | $1.829 |
| Sol `xhigh` diagnosis, then fresh Sol `low` implementation | 3 | 3 | $0.857 |
| Luna `high` diagnosis, then fresh Sol `low` implementation | 1 | 0 | $0.384 |

The three successful split runs exclude one provider failure and include its
replacement. Their mean cost is 53.1 percent below the retained direct Sol
`xhigh` attempt.

The diagnosis content explains the accuracy difference. Sol `xhigh`
reconstructed the raw checkpoint layout, the lexicographic transformer-block
order, tensor offsets, and tokenizer identifiers. Luna identified the raw
float count but marked tensor ordering as unresolved. The Sol `low`
implementation then guessed a conventional layout and produced incorrect
tokens.

Universal Sol `xhigh` diagnosis is too expensive. A four-task development run
passed every task but cost approximately twice as much as direct Sol `low`.
The run was stopped before the remaining two tasks because it already violated
the 15 percent cost-regression gate.

The next accuracy experiment uses conditional reasoning:

```text
task
  |
  v
Luna diagnosis
  |-- implementation facts resolved --> Sol low implementation
  `-- critical fact unresolved --------> Sol xhigh diagnosis
                                            |
                                            v
                                      Sol low implementation
```

The existing workflow branch mechanism can express this graph. Luna returns
one of two branches and a typed report. It selects deeper diagnosis when any
fact needed to choose an implementation remains unresolved. Both
implementation paths start with a clean context.

The selected `gpt2-codegolf` task activates the deeper branch. The first gate
requires three successful attempts from Sol `low` implementation. The second
gate runs the six development tasks and rejects a task-owned verifier
regression. Cost and latency measurements identify later Pareto opportunities
after the accuracy gate passes.

## Environment discovery remains wasteful

The logs contain 114 `bash` results with exit status 127, 59 with exit status
1, and 36 with exit status 128. Some statuses are expected probes. Repeated
attempts to use absent commands also appear across unrelated tasks. Missing
commands included Python, `file`, `xxd`, repository administration tools, and
language-specific build tools.

Several adapter defects amplified this pattern. Early programs used `/` as
their first read root, which changed the relative working directory. Early
portable binaries required a newer C library than some task images. The
administration-command path omitted standard system directories. Preflight
validation, a corrected working directory, a portable binary, and a standard
administration path address those defects.

The remaining opportunity is a compact environment manifest produced before
the model starts. The manifest should name the working directory, compiler and
interpreter availability, package manager, Git worktree state, and persistent
process support. The adapter can derive these facts under its existing
authority and record them as workflow input. This avoids a new tool schema on
every request.

A deterministic capability probe already established one hard ceiling:
background processes do not survive across Foe `bash` calls. The executor
terminates the process group when each call ends. Tasks that require a local
server, database, or concurrent client cannot rely on a daemon started by a
prior call. Persistent process handles need an activation task and lifecycle
tests before calibration opens tasks in those categories.

## Tool-result replay is the largest measured efficiency cost

The retained tool results contain 1,571,386 rendered `bash` characters,
697,495 rendered `edit` characters, 425,346 rendered `read` characters, and
143,864 rendered `grep` characters. These counts measure initial renderings.
Subsequent requests replay many results.

The largest measured single-result replay was a 23,614-character disassembly
carried into twenty later requests, for 472,280 replayed characters. A broad
repository search carried 48,948 characters into nine later requests. Four
`sanitize-git-repo` trials each echoed a 43,308-character edit diff after the
model had already supplied the replacement text in its tool call.

Accuracy work has priority because the selected task already fits within
context and fails semantically. The first replay change should bound large
edit confirmations while keeping small diffs intact. Log-backed queries and
archived slices can then let a model recover omitted evidence without
re-executing tools. Each mechanism needs an activation task and paired cost
measurement before promotion.

## Self-improvement needs a deterministic decision boundary

Five provider-backed self-improvement runs used 77 model calls, 3,444,245
input tokens, 39,503 output tokens, and an estimated $1.853. Three runs
reached implementation. One candidate passed the source checker. No candidate
improved the selected benchmark task.

The earlier evidence digest omitted successful validation that followed
initial command failures. It also omitted the semantic detail from task
verifier failures. One optimizer therefore proposed behavior already present
in the evaluated source. Another produced a valid change to login handling
that had no causal connection to `gpt2-codegolf`.

The diagnostic schema now retains the final edit, bounded later validation
results, verifier failure classes and messages, model settings, and source and
binary identities. The next self-improvement run should use these stages:

1. A deterministic tool builds the bounded cross-trajectory digest.
2. Luna returns a causal diagnosis with a falsification condition and required
   product paths.
3. A deterministic gate rejects stale evidence, unsupported causality, and
   changes outside the allowed product surface.
4. Terra implements an accepted diagnosis in a clean source copy.
5. A pinned external checker validates source, tests, specifications, and
   candidate identity.
6. A fresh repair child receives bounded checker findings when the candidate
   is structurally close but invalid.
7. The selected task evaluates the candidate through the same paired protocol
   used for direct changes.

The workflow must stop before implementation when the causal gate fails. A
failed generated candidate triggers direct implementation, as required by the
campaign contract. This rule lets self-improvement accelerate development
without making it a prerequisite for product progress.

## Changes ordered by expected frontier value

| Order | Change | Primary frontier effect | Activation evidence | Promotion gate |
| ---: | --- | --- | --- | --- |
| 1 | conditional Luna-to-Sol diagnosis routing | accuracy | `gpt2-codegolf` requires checkpoint-layout inference | three selected-task passes, then no development verifier regression |
| 2 | deterministic environment manifest | latency, cost, and reliability | 114 missing-command results | fewer missing-command probes with no task regression |
| 3 | conditional fresh validation and repair child | accuracy | 25 completed-but-failed trials | activation-task gain and no development verifier regression |
| 4 | persistent process handles | capability | background process fails the deterministic probe | server survives across calls, teardown is bounded, trace remains conformant |
| 5 | staged self-improvement with a causal gate | development velocity | five runs produced no effective candidate | one candidate generated from source-revision- and runtime-matched evidence changes an activated mechanism and passes paired evaluation |
| 6 | bounded edit confirmation | token cost and latency | repeated 43,308-character edit renderings | paired savings when the bound activates and no accuracy loss |
| 7 | log-backed result queries and archived slices | long-horizon token cost | individual results replay up to 472,280 characters | lower price-weighted cost on long tasks with equivalent verifier accuracy |

Conditional diagnosis is first because it has repeated task-level evidence
and uses an existing Foe abstraction. Persistent processes are the highest
known capability gap. Replay control follows the accuracy mechanisms because
the selected failure is caused by incorrect reasoning rather than context
exhaustion.

## Campaign gates after implementation

The campaign proceeds through the task sets frozen in
[`campaign.md`](campaign.md):

1. Run three selected-task attempts with the conditional diagnosis workflow.
2. Run all six development tasks and reject a task-owned verifier regression.
3. Freeze the candidate source, binary, model settings, and acceptance rule.
4. Run two attempts on each of four unopened confirmation tasks. At least
   seven of eight attempts must pass, and every task must pass once.
5. Run one frozen attempt on each of twelve calibration tasks. At least ten
   must pass.
6. Record the calibration result before opening six calibration-holdout tasks.
   At least five must pass.

The campaign does not run the full 89-task benchmark until these gates pass.
Model calls and wall time serve as generous backstops. Provider-reported token
use, estimated cost, and elapsed time remain measurements for Pareto selection
after the quality gates pass.

## Local evidence locations

The 113 trial records are distributed across these ignored job roots:

- `/home/sunil/git/foe-capability-calibration/target/terminal-bench-jobs`;
- `/home/sunil/git/foe-frontier-trajectory-fixes/target/terminal-bench-jobs`;
- `/home/sunil/git/foe-gpt2-low-direct/target/terminal-bench-jobs`;
- `/home/sunil/git/foe-gpt2-typed-handoff/target/terminal-bench-jobs`;
- `/home/sunil/git/foe-terminal-bench-adapter/target/terminal-bench-jobs`;
- `/home/sunil/git/foe-terminal-bench-aging-retrieval/target/terminal-bench-jobs`;
- `/home/sunil/git/foe-terminal-bench-output-bounds/target/terminal-bench-jobs`.

Self-improvement result records are under
`/home/sunil/git/foe-capability-calibration/target/gpt2-codegolf-self-improvement*`
and
`/home/sunil/git/foe-gpt2-typed-handoff/target/current-source-gpt2-*-self-improvement-run`.
These directories remain local. The campaign record, diagnostic schema, and
this analysis carry the durable conclusions.
