# Foe architecture and Terminal-Bench campaign report

Date: 2026-08-31

## Executive summary

Foe is a runtime for unattended coding agents. It combines a model loop,
declared permissions and effect controls, typed outcomes, bounded episode
trees, append-only evidence, executable completion checks, and declared workflows. Its design
aims to replace the approval, progress monitoring, and completion decisions
that a person supplies in an interactive coding session.

The Terminal-Bench campaign established a strong result for one frozen Foe
release paired with `openai-codex/gpt-5.6-sol`. The release passed 40 of 44
scored protected attempts across 36 distinct tasks. Confirmation passed 15 of
16 attempts, calibration passed 18 of 20 tasks, and a fresh holdout passed 7
of 8 valid tasks. These results are a staged development qualification. They
are not an official Terminal-Bench score.

The campaign also demonstrated why Foe's workflow and verifier abstractions
matter. A repair episode ran in 17 of the 44 protected attempts. Fifteen of
those attempts passed the task-owned grader. Several accounts show an
independent assessment finding a concrete defect, a repair changing the
artifact, and a later assessment accepting the corrected state.

The same evidence identifies important limits. Independent model stages can
share one incorrect interpretation. Four externally rejected artifacts were
reported as completed. One externally accepted artifact ended with a blocked
Foe outcome. Assessment, repair, and confirmation consumed 68 percent of the
model calls and at least 79 percent of recorded input tokens. The evaluated
workflow favored quality at high cost.

Foe's self-improvement mechanism produced two qualified transferable
improvements. One changed workflow configuration. One changed Foe source,
added a Rust regression test, and updated affected specifications. The
campaign also produced expensive invalid candidates and failed to diagnose
the semantic mechanism behind the hardest repeated failure. General
cross-trajectory self-improvement therefore remains a demonstrated research
path rather than a reliable product capability.

The campaign provides evidence for three Foe advantages:

- Complete, content-addressed accounts make failures attributable after an
  unattended run.
- Declared workflows can correct artifacts through fresh assessment and
  bounded repair.
- A task-owned verifier can turn a failed completion claim into useful
  feedback before the episode ends.

The campaign does not establish a token, latency, cost, or accuracy advantage
over another harness. It did not run a competitor baseline or the complete
Terminal-Bench submission protocol.

## Scope and evidence

The unchanged task-owned grader supplied the final score. It came from
[Terminal-Bench 2.1](https://hub.harborframework.com/datasets/terminal-bench/terminal-bench-2-1/6),
executed by Harbor after Foe exited. The benchmark release contains 89 tasks.
The official protocol requires repeated trials across the complete set. The
[release description](https://www.tbench.ai/news/terminal-bench-2-1) explains
the validation changes made for Terminal-Bench 2.1.

This report uses three evidence collections.

1. The early cross-trajectory corpus contains 113 trial records and 103 Foe
   root logs retained through 2026-08-24. Its aggregate and mechanism analysis
   is in [cross-trajectory-analysis.md](cross-trajectory-analysis.md).
2. The later campaign record follows candidate selection, rejection,
   correction, self-improvement, and release qualification. The
   [complete record](campaign.md) is retained in this directory.
3. The frozen-release analysis reads every retained diagnostic report from
   the 16 confirmation attempts, 20 calibration attempts, and 8 valid fresh
   holdout attempts. These 44 attempts contain 215 root and child episode
   accounts and 2,150 model calls.

The raw task workspaces and episode trees remain outside Git because they
contain large benchmark artifacts, complete model traffic, and credential
material used inside task containers. Each retained campaign manifest records
the task, source tree, portable binary, program identity, grader result,
resource use, and Foe conformance result. The full historical record gives the
local evidence paths and manifest digests.

The report treats task-owned grades as quality evidence. Foe outcomes,
workflow events, tool results, and model usage explain how the result arose.
They do not replace the external grade.

## What Foe is

### An episode runtime

One Foe invocation runs one bounded unit of work, called an episode. The
episode receives a fixed task, a program, explicit grants, tools, a model, and
resource limits. It ends with one typed outcome: completed, blocked,
exhausted, or failed. [design.md](../../docs/design.md) specifies the runtime
and [config.md](../../docs/config.md) specifies the program document.

An episode can start child episodes. The root holds shared call, token, time,
episode-count, and concurrency allowances. Child reservations and releases
are recorded. This structure supports unattended execution while preserving
a finite resource envelope.

### Declared permissions and effect controls

A program names the directories, executables, child programs, and network
access it can use. Built-in tools enforce path grants. On supported Linux
hosts, the sandbox can compile grants into Landlock restrictions. Tool
effects and the kinds and counts of grants participate in program identity.
[Program lineage and transition
evidence](../../docs/lineage-identity.md#definitions) defines Foe's broader
term, authority, as the complete set of effects a program may exercise.

The Terminal-Bench adapter ran Foe with `sandbox.mode` set to `off`. Docker
was the isolation boundary. The campaign therefore tested behavior inside a
container and the built-in capability checks. It did not test Landlock as part
of task quality.

### An append-only account

Every request, response, tool call, tool result, workflow firing, budget
operation, verification result, and outcome is written to an append-only
JSONL account. The account stores the complete canonical tool result and the
bounded rendering shown to the model. [log-format.md](../../docs/log-format.md)
defines reconstruction and obligation-pairing rules.

Program identity covers stable model-visible behavior, tool definitions,
workflow structure, tool effects, grant kinds and counts, and runtime-owned
instructions. Campaign manifests add source-tree and binary digests. This separation allowed the
campaign to distinguish model variance, workflow behavior, implementation
changes, and deployment faults.

### First-class workflows

A workflow is a declared graph. Data moves through `follows` edges. Branches
select control successors. A model node starts a fresh child episode whose
initial context contains only its declared inputs. Tool nodes perform
deterministic work. Bounded cycles permit assessment and repair. The runtime
records each firing and enforces the graph described in
[workflow.md](../../docs/workflow.md).

The evaluated release used a five-role coding graph:

```text
implementation
     |
     v
assessment ---> repair ---> reassessment
     |
     v
final confirmation ---> final repair ---> final confirmation
```

Every role shared the task workspace. Each model child had a fresh
conversation. Implementation used Sol at low reasoning effort. Assessment,
repair, and confirmation used Sol at `xhigh` reasoning effort. Every
qualifying request used the priority service tier.

The repository state reviewed for this report,
`89dd279b01f9e7b98082afa9debb7836f5eff271`, has consolidated the built-in
program into implementation followed by one independent audit that can
repair. The frozen-release results apply to the five-role graph and portable
binary identified below. They do not measure the consolidated program.

### Verifier-governed completion

A program can declare an executable under `done_when.verify`. Foe runs the
verifier when the model attempts completion. Findings return to the same model
episode as an observation. An empty successful result completes the episode.
The verifier executable remains outside the model's write grants.

This mechanism gives the completion decision to an external verifier when a
task or repository provides one. Without a verifier, typed returns and model
judgment govern completion. The campaign shows a material difference between
those two conditions.

### Bounded self-improvement

The supported repository example evaluates a disposable Foe source copy,
passes deterministic findings to a model node, changes implementation, test,
and specification, and verifies the result outside the writable tree.
[self-improvement.md](../../docs/self-improvement.md) documents three repeated
successes on that bounded change.

The Terminal-Bench campaign extended the pattern with cross-trajectory
evidence matched to its source revision and runtime binary. Unchanged
external evaluation assessed each candidate. That machinery
was campaign infrastructure. The reviewed repository state documents
automatic improvement selection and general reliability as unsupported.

## Foe's intended advantages

| Advantage | Design mechanism | Campaign evidence | Evidence boundary |
| --- | --- | --- | --- |
| Unattended execution | Fixed task, predeclared grants, loop detection, budgets, and typed outcomes | Every scored protected task ran without a human approval step | Five artifact and outcome mismatches show that autonomous termination still needs stronger semantic validation |
| Reconstructable evidence | Append-only root and child accounts, program identity, source and binary digests | Every scored protected account passed trace conformance; failures could be attributed to exact stages and tool results | Conformance establishes runtime-account consistency rather than task correctness |
| Corrective workflows | Fresh assessment, bounded repair, reassessment, and final confirmation | Repair activated in 17 protected attempts; 15 received a passing external grade | No paired no-repair control isolates the average causal effect |
| Governed completion | External verifier findings can reject completion and return corrective feedback | A public checker converted repeated DNA and release-layout failures in modified evaluation cases | Modified cases measure convergence and are outside the standard score |
| Transferable self-improvement | Evidence matched to the evaluated source revision and runtime binary; typed diagnosis; isolated source candidate; external activation and transfer | Two generated improvements passed an activation case and an unrelated transfer case | The harder final semantic correction required direct engineering |
| Inspectable runtime | Strict source budgets keep the kernel and supporting components small | At report publication, `scripts/loc.sh` counted 5,248 production Rust lines in the kernel | Source size improves reviewability; it does not prove safety or benchmark efficacy |

## Campaign design

### Frozen release

The final protected cohorts used one immutable source and binary pair:

| Property | Value |
| --- | --- |
| source commit | `eecfce6ef89c37ee3bdba7f9f8ac81e48f9a24dd` |
| source tree | `git-tree-sha1:4d926a3c9d3107308cba2d34922598d9a101ba5b` |
| portable binary | `sha256:986b6f0c7c52f3a72f787e537f52f632a32562f6c501481d55a7a01438b4f6ee` |
| implementation model | `openai-codex/gpt-5.6-sol`, low reasoning |
| independent-stage model | `openai-codex/gpt-5.6-sol`, `xhigh` reasoning |
| service tier | `priority` |
| token policy | measured without hard task-quality ceilings |
| execution isolation | Harbor task container, Foe sandbox off |

Model-call and wall-time limits remained generous loop backstops. Input,
output, cache-read tokens, estimated cost, and latency were measurements. A
quality improvement could proceed when its cost increased.

### Standard and verifier-governed cases

Standard Terminal-Bench cases were closed-book. The task-owned grader ran
after Foe exited and was absent from the Foe program. Assessment and
confirmation could inspect the task statement and workspace. They could not
read grader results.

Modified verifier-governed cases exposed a public checker through
`done_when.verify`. Each checker had an untouched-workspace negative control
and an oracle control. Harbor still ran the unchanged task-owned grader after
the episode. Results from these cases measured corrective convergence and did
not enter standard Terminal-Bench totals.

### Staged task sets

The campaign used development tasks for trajectory inspection and candidate
construction. It then used confirmation, calibration, and holdout cohorts for
stronger evidence. Weak candidates stopped before larger cohorts. Task-owned
quality was the promotion criterion.

The final development result combines a complete 12-task qualification of an
earlier ancestor with focused requalification of every mechanism affected by
later model-visible changes. The final source did not rerun the identical
12-task development set. Confirmation, calibration, and fresh holdout all ran
the exact final source and binary pair.

## Quality results

### Protected cohorts

| Cohort | Scored attempts | External successes | Distinct tasks | Result |
| --- | ---: | ---: | ---: | --- |
| confirmation | 16 | 15 | 8 | Every task succeeded at least once |
| calibration | 20 | 18 | 20 | Passed the predeclared threshold of 17 successes |
| fresh holdout | 8 | 7 | 8 | Passed the predeclared threshold of 7 successes |
| aggregate | 44 | 40 | 36 | 90.9 percent of scored protected attempts passed |

The confirmation failure remains in the denominator. A provider refusal
before the first response on one fresh-holdout task was retained as deployment
evidence and excluded from task quality. A replacement task was selected
before its instruction or trajectory was opened.

An earlier sealed holdout stopped after host swap growth. It recorded two
successes and two valid failures before four unstarted tasks were withheld.
The fresh cohort was authorized after the release had strong confirmation and
calibration evidence. The earlier result remains evidence against treating the
fresh cohort as a pristine prespecified holdout.

### Development improvements

One assessed development release passed 9 of 12 tasks. The three failures
identified separate harness mechanisms:

- assessment destroyed task-required live service state;
- repair expanded a current-content change into Git history and an encrypted
  archive;
- black-box validation tested only the supplied model instance.

The resulting Foe changes required preservation of task-required live state,
narrow mutation scope, and varied public-interface fixtures. The corrected
lineage converted all three tasks. A combined descendant later passed all 12
development tasks under one binary.

Further trajectories produced rules for authoritative acceptance paths,
assessment after repair, independent final confirmation, release-component
preservation, and overlapping artifact decompositions. The final rule fixed a
repeated primer-design failure that persisted under higher reasoning effort
and added diagnosis stages.

### Cost and usage

The 44 scored protected attempts used 2,150 model calls. Provider usage was
present for 2,149 calls. Those usage-bearing calls recorded 59,169,620 input
tokens, 38,218,240 cached-input tokens, and 1,576,802 output tokens. One
confirmation call omitted usage, so complete totals are unavailable.

The campaign's conservative complete-attempt accounting reports estimated
cost of at least $127.18 across confirmation, calibration, and fresh holdout.
Among externally successful attempts with a complete price, median estimated
cost was $1.84 and the 90th-percentile value was $8.71. Median model calls per
success were 43.5, and the 90th-percentile value was 95.

These measurements do not support a token-efficiency claim. The campaign
optimized quality and used a large independent-verification stack. No matched
competitor result exists.

### Concurrent execution

A four-task serial execution passed every task in 1,629.481 task seconds. A
matched two-worker execution passed the same four tasks in 984.389 task
seconds, a 39.6 percent makespan reduction. The controller reserved container
memory and issued a private credential lease to each worker.

This result validates campaign-level parallel task execution and credential
isolation. It does not measure parallel model nodes inside one Foe workflow.

## What the trajectories show

The model-call and input distribution shows where the evaluated workflow spent
its resources. Input counts include every response with provider usage. One
assessment response lacks usage, so the input total is a lower bound.

| Role | Child episodes | Model calls | Share of calls | Recorded input tokens | Share of recorded input |
| --- | ---: | ---: | ---: | ---: | ---: |
| implementation | 44 | 687 | 32.0 percent | 12,279,129 | 20.8 percent |
| assessment and reassessment | 62 | 643 | 29.9 percent | 17,322,256 | 29.3 percent |
| assessment repair | 18 | 324 | 15.1 percent | 16,398,228 | 27.7 percent |
| final confirmation | 45 | 463 | 21.5 percent | 12,723,849 | 21.5 percent |
| final-confirmation repair | 2 | 33 | 1.5 percent | 446,158 | 0.8 percent |
| total | 171 | 2,150 | 100.0 percent | 59,169,620 | 100.0 percent |

### Independent repair often changed the result

The final corpus contains 17 attempts with at least one repair episode. Fifteen
passed the external grader. The accounts contain several direct correction
chains:

- assessment found an omitted chess move and repair wrote the complete set;
- assessment used optical character recognition (OCR) to recover a digest
  that implementation could not read;
- assessment corrected video transcription boundaries and missing commands;
- final confirmation replaced a noise-sensitive eigenvalue optimization;
- assessment redirected a MIPS build to the supplied virtual-machine syscall
  contract;
- two repair cycles corrected floating-point ordering and error behavior in a
  compiled portfolio extension;
- assessment restored missing release components and exact paths for POV-Ray;
- assessment and confirmation corrected primers across overlapping insertion
  boundaries.

Fresh context was useful when the later stage applied a materially different
test. The quality benefit came from method diversity and permission to correct
the artifact, rather than the mere presence of another model call.

### Independent model stages can share one blind spot

Fresh conversations did not guarantee independent interpretation. The
`gcode-to-text` implementation, assessment, and confirmation all rendered the
same geometry as terminal text and inferred the same incorrect phrase. None
used image rendering and OCR even though package installation was available.

Earlier DNA failures had the same structure. Assessment and confirmation
selected one decomposition of the serialized sequence and varied measurements
inside that choice. The task grader used coupled overlapping decompositions.
Higher reasoning effort and an added diagnosis stage repeated the same
mistake. The successful correction changed the validation contract so every
task-compatible overlapping decomposition had to pass.

Multiple model stages therefore provide useful execution independence and
limited semantic independence. An authoritative verifier or a materially
different validation method provides stronger independence.

### Repair can discard previously satisfied requirements

The failed `fix-code-vulnerability` attempt initially wrote the grader-required
`CWE-93` report entry. Final confirmation found a separate parser issue. The
repair fixed that issue and replaced the report entry with `CWE-20`. The
external grader passed five of six tests and rejected the changed report.

The repair received the original task and current findings. Its contract did
not make previously satisfied requirements explicit state that had to survive.
This failure supports requirement-coverage evidence that persists across
workflow edges and must be rechecked after every mutation.

### Validation can destroy evidence or task state

The failed `db-wal-recovery` attempt began by letting SQLite remove the invalid
task-supplied write-ahead log (WAL) before it was inspected. Assessment
correctly rejected the result. Repair then created a structurally valid WAL
with plausible rows. Both later model stages accepted it. The external grader
showed that the recreated WAL omitted an update from 100 to 150.

The first `crack-7z-hash` confirmation produced the correct solution file.
Assessment and confirmation passed `/dev/null` as a John the Ripper cache
path. The program changed the special device's mode. The external grader later
failed during dependency setup because `/dev/null` denied writes.

These failures show that read-like probes can mutate shared state. A strong
unattended harness needs disposable validation copies, explicit task-state
preservation, and post-validation checks for protected artifacts and live
state.

### Typed outcomes and artifact quality still diverge

All four externally failed protected attempts ended with a completed Foe
outcome. One externally successful adaptive-rejection-sampler artifact ended
blocked because another repair would exceed the node's `max_fires` value.
The diagnostic field `artifact_outcome_mismatch` was therefore true in 5 of
44 scored attempts.

Typed outcomes made each divergence visible. Their semantic accuracy depended
on who or what decided completion. A model-only final confirmation could still
accept a wrong artifact. A fixed workflow limit could still reject a correct
artifact. The evidence supports wider loop backstops during quality evaluation
and verifier-governed completion when an authoritative check exists.

### Tool friction consumed recoverable calls

The 44 protected attempts contain 249 recorded tool failures. Many nonzero
shell commands were intentional probes. Three recurring tool-interface costs
were avoidable:

- 51 edit calls failed their file-version precondition;
- 35 read calls targeted paths outside the read tool's grants;
- 6 shell calls attempted a missing executable.

The version guard prevented stale edits, but its exact input format was costly
for the model. The read failures also show a mismatch between Bash-visible
temporary files and the narrower built-in read surface. The reviewed
repository state makes edit versions optional. Guarded edits remain available
when the caller supplies a version.

### Tool-result replay dominated context

The diagnostic collector retains each attempt's largest replayed results. The
sum of this bounded sample exceeded 56 million rendered characters. One read
of `vm.js` contributed 2.9 million replayed characters across later requests.
Large package-install logs, compiler output, repository searches, and source
reads also persisted through long child conversations.

Fresh workflow children bounded replay across roles. Replay still grew inside
each long assessment or repair episode. This pattern supports bounded command
output, queryable archived results, and narrower deterministic preprocessing.
Any reduction must preserve access to evidence that later validation needs.

## Self-improvement results

### Qualified workflow improvement

Three closed-book `dna-insert` attempts had failed after Foe reported
completion. One verifier-governed attempt passed. A diagnosis from evidence
matched to the evaluated source revision and runtime binary selected a
verifier-governed assessment-and-repair workflow in one model call.
Candidate generation used 12,218 input tokens, 1,394 output tokens, and an
estimated $0.076752.

Two fresh activation attempts applied the generated workflow and passed the
unchanged external grader. One unrelated `git-multibranch` transfer attempt
also passed. The workflow improvement therefore met its declared activation
and transfer rule.

The improvement applies where a public task-owned verifier exists. It does
not establish a closed-book correction for DNA primer interpretation.

### Qualified source improvement

Another corpus matched to one source revision and runtime binary contained two
rejected and two successful DNA episodes. Foe generated a source change that
required assessment to use a task-declared or repository-declared acceptance
path. When no authoritative
path existed, the rule required two independently derived equivalent methods.

The generation workflow used 41 model calls, 1,723,894 input tokens,
1,141,248 cached-input tokens, 19,977 output tokens, and an estimated
$3.186623. The candidate changed the built-in program configuration, added a
Rust regression test, and updated three affected specifications. The unchanged
`dna-insert` activation and the unrelated `gpt2-codegolf` transfer both passed.

This result proves that Foe can produce a source, test, and specification
change from trajectory evidence matched to its source revision and runtime
binary. The resulting behavior transferred once. This result does not
estimate repeatability across source changes.

### Limits of the improvement loop

The self-improvement path also produced four cautionary results.

- One source candidate used 172 model calls and an estimated $15.68 before an
  independent causal review found that its mechanism was absent from every
  failed configuration.
- A release-completeness source candidate used 106 model calls and passed two
  activation attempts. Its selected transfer task did not exercise release
  acquisition, so transfer remained unproven.
- Cross-trajectory diagnosis returned insufficient evidence for two DNA
  failures because their external assertion names differed. Both failures
  arose from the same coupled-decomposition decision.
- The final overlapping-decomposition correction was implemented directly
  after focused experiments. Foe did not generate the decisive final change.

The improvement loop was useful when the evidence exposed one explicit
configuration contrast or one source-owned validation rule. It was weak when
causality required joining different external failures through internal
semantic decisions. A reliable loop needs machine-checked candidate
activation, bounded cross-trajectory summaries, requirement-level failure
grouping, and unchanged external promotion tests.

## Assessment of Foe's advantages

### Advantages supported by the campaign

Foe's strongest demonstrated advantage is evidence quality. The campaign
could attribute failure to a particular model stage, tool result, mutation,
workflow decision, source tree, and binary. The same account distinguished a
correct artifact with a blocked outcome from an incorrect artifact with a
completed outcome.

Declared workflows also showed practical value. Independent assessment and
repair converted concrete artifact defects on tasks involving compilers,
services, images, video, numerical software, cryptography, data formats, and
virtual machines. Typed handoffs and fresh contexts made those corrections
inspectable.

Verifier-governed completion is the clearest mechanism-level quality result.
It supplied external findings before termination and converted repeated
failure cases in the modified lane. The external grader supplied the final
quality decision.

### Advantages that remain hypotheses

The campaign supplies no evidence that Foe is more accurate than Codex CLI,
Claude Code, or another harness on the same model. It also supplies no
matched evidence for lower cost, fewer tokens, or lower latency.

The evaluated five-role program used large context and many model calls.
Independent verification improved several artifacts and also repeated shared
semantic errors. The resulting quality and cost point may be useful, but its
position on a cross-harness Pareto frontier is unknown.

Declared permissions and effect controls remain an architectural advantage
with partial campaign coverage. Built-in path grants operated during the trials. Landlock was
disabled inside Docker, so the campaign does not support a kernel-sandbox
quality or security claim.

### Human-out-of-the-loop assessment

Foe ran every scored protected task without interactive approvals. It handled
long builds, package installation, live services, virtual machines, repair
cycles, and externally scored artifacts. This establishes broad unattended
execution capability for the selected tasks.

Semantic completion remains the principal qualification. Model-only
completion disagreed with the task grader in four failures. A fixed workflow
limit produced one false blocked outcome. Human-out-of-the-loop execution is
most credible when a verifier owns completion and validation cannot damage the
state it judges.

## Engineering implications

The trajectory corpus supports five priorities.

1. Use task- or repository-owned verification to make the completion decision
   whenever it is available. Keep standard closed-book scoring
   separate from verifier-governed convergence measurement.
2. Carry explicit requirement coverage across repairs. Revalidate every
   previously satisfied requirement after any model stage changes the
   workspace.
3. Run destructive validation on disposable copies. Preserve supplied
   evidence, special devices, services, and requested final state through
   every workflow stage.
4. Require material method diversity when uncertainty remains. Environment
   discovery should expose available OCR, image, debugger, compiler, and
   package-installation paths without requiring repeated shell probes.
5. Make cross-trajectory self-improvement compare semantic decisions and
   requirement coverage. Candidate activation and external transfer must be
   machine-checked before promotion.

Cost optimization should follow the quality mechanisms. The first targets are
workflow-stage consolidation when a stage adds no independent finding,
predecessor evidence references that avoid workspace rediscovery, bounded
tool rendering, and queryable archived results. Every change needs a matched
quality evaluation on tasks where the mechanism activates.

## Limitations

The campaign used selected subsets and adaptive development. It did not run
the 89-task, repeated-trial submission required for an official result. The
40-of-44 protected aggregate is a campaign result rather than a population
estimate.

Confirmation used two attempts per task. Calibration and fresh holdout used
one valid attempt per task. Stochastic uncertainty remains large. The fresh
holdout followed an earlier partial holdout and replaced a provider-refused
task under a predeclared unopened-task rule.

The model, reasoning policy, workflow, provider route, and Foe runtime changed
together across many development comparisons. Mechanism-specific activation
cases support individual changes. The complete campaign cannot isolate Foe
from Sol model capability.

One confirmation response omitted provider usage. Cost and token totals are
lower bounds. Priority service was used throughout the final qualification,
and no standard-service latency comparison was performed.

The task containers ran with Foe's kernel sandbox disabled. Docker isolated
the benchmark. Security and Landlock efficacy require separate tests.

The evaluated release is an ancestor of the repository state reviewed for
this report. That state has a simpler two-role built-in workflow and later
tool and completion changes. Another protected evaluation is required before
assigning the frozen release's quality result to that implementation.

## Conclusion

Foe completed a demanding staged campaign with strong selected-task quality.
The frozen release passed 40 of 44 scored protected attempts, including 18 of
20 calibration tasks and 7 of 8 fresh holdout tasks. It also preserved a
conformant account matched to the source revision and runtime binary for every
valid attempt.

The campaign validates Foe's central integration: declared workflows,
external verification, bounded correction, typed outcomes, and reconstructable
evidence can support unattended coding across varied environments. The
accounts made both successes and failures unusually diagnosable.

The campaign also establishes the remaining boundary. Model-only verification can
share semantic errors, validation can damage the state it judges, and a deep
verification graph can consume most of the run's calls and context. Foe's
path toward a Pareto-leading unattended harness is stronger completion
checks, safer state handling, materially independent validation, and
lower-cost evidence reuse.

Self-improvement is proven at bounded scale. Two generated changes transferred
once. The decisive final semantic correction still required direct
engineering. Future claims should require repeated self-generated source
changes that improve untouched external tasks and survive a sealed holdout.
