# Foe capability campaign record

This record defines the evaluation campaign that prepares Foe for a Terminal-Bench 2.1 submission. The campaign ends at a retained calibration result. A full 89-task benchmark run requires a separate decision.

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

GPT-5.6 Sol with low reasoning is the primary coding model. Conditional
escalation may use Sol with at most `xhigh` reasoning. Provider requests use
the standard service tier unless a recorded run explicitly selects another
tier. Token use, estimated cost, cache use, and
latency are measurements. Task quality is the only candidate promotion metric
until every quality gate passes.

Candidate changes may affect Foe source, a general workflow configuration, an
instruction section of the retained self-improvement program, or a declared
executable tool. Task-specific instructions, benchmark identifiers, fixture
values, and grader rules are excluded from candidate changes.

## Success criteria

The release must satisfy all of these quality conditions:

1. Convert at least three reproducible harness-limited development failures
   into task-verifier successes.
2. Preserve every development-task success that the baseline established.
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
use conditional model escalation or bounded work by Luna or Terra. It must
preserve task quality.

Planning estimates bound campaign exposure before each confirmed command.
They do not reject a candidate that improves task quality. Development runs
use the standard service tier and record it in the manifest. Foe records
usage without enforcing token ceilings during ordinary quality runs.

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
7. Run identity-bound self-improvement. Use a bounded cheaper model for
   diagnosis only when it preserves the diagnosis contract. Use Sol `low` for
   implementation and at most Sol `xhigh` for conditional escalation.
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

The self-improvement workflow has at most three model nodes. A Luna diagnosis
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
