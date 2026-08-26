# Self-improvement in foe

This report evaluates whether foe can improve its own source through a
declared workflow. It describes the implemented mechanism, results, failure
modes, and operating guidance. The evidence covers one bounded coding
task with one model route. It does not establish general autonomous
self-improvement.

## Scope and status

Self-improvement means that foe performs five operations in one assessed run:

1. Evaluate a disposable copy of its source with a deterministic checker.
2. Give the checker findings to a model episode.
3. Change the implementation, regression test, and specification together.
4. Verify the changed source with an executable outside the writable tree.
5. Preserve the episode tree as evidence of authority, resource use, and
   information flow.

The implemented example extends the built-in `read` tool with a canonical
`total_bytes` field. The model must preserve rendered output, add a regression
assertion, and specify the field in `docs/tools.md`. The
[self-extension example](../examples/self-extension/) runs against a fresh
source copy and never grants the episode write access to the repository
checkout.

The two-node workflow is implemented. The three-run result supports
repeatability for the stated task. The Terminal-Bench evaluation runner also
implements an identity-bound workflow that proposes one improvement from
repeated failed and successful trajectory contrasts. Automatic promotion into
the checkout and general reliability across unrelated tasks remain
unsupported.

## Workflow structure

The [workflow configuration](../examples/self-extension/workflow-config.json)
contains two nodes:

1. A tool node named `evaluate_read_tool` runs the checker against the fresh
   source copy. Its standard output becomes evidence for downstream work.
2. A terminal model node named `improve_read_tool` receives the task and the
   evaluator output. It reads and edits the three affected files, then calls
   the checker.

The terminal node owns its completion condition through `done_when.verify`.
A checker call with findings returns the findings to the same child episode.
A clean checker call completes that child after all tool effects in the turn
have settled. The workflow then completes with the terminal node's value.

This structure keeps the interactive surface small. The author supplies a
task, a checker, and a two-node workflow. The runtime reuses the ordinary
program, tool, budget, episode, and workflow concepts.

## Cross-trajectory evidence collection

The Terminal-Bench runner retains a diagnostic report for every assessed
attempt. The deterministic collector in
[`collect_diagnostics.py`](../evals/terminal_bench/collect_diagnostics.py)
combines reports produced by one source tree and runtime binary. Every input
manifest must identify which workflow implementation owned the attempt.

The [Terminal-Bench task registry](../evals/terminal_bench/cases.json) defines
which tasks may supply self-improvement evidence. The eligible set contains
development tasks, capability-search tasks, and opened confirmation tasks.
Calibration tasks and the sealed holdout remain outside that set.

Evidence schema 6 groups failures by task, typed outcome, artifact mismatch,
and named failed verifier checks. This coarse profile permits two failed
attempts with different assertions to enter one same-task contrast. The
contrast also requires one successful episode for the same task. Trial
infrastructure failures cannot enter a contrast.

The collector reloads the retained task-owned verifier artifact after Foe
exits. It requires regular files confined to the retained trial directory.
The task name, task checksum, reward, error, and verifier digest must match
the diagnosis written after that trial.

Each failed attempt carries the verifier digest and bounded failure loci. A
locus contains a normalized source location, assertion expression, and concise
message when pytest or the Common Test Report Format (CTRF) supplies them. Its
digest covers those fields and the normalized check name. Host paths, memory
addresses, timestamps, terminal formatting, parameter values, and the
remaining traceback are excluded.

This collection step runs outside the task episode. The model that performs
the task receives no task-owned grader output through this path. The later
self-improvement diagnosis receives only the compact derived evidence.

The collector retains bounded outcomes, input-growth landmarks, resource
usage, and execution-configuration summaries. A failed completed attempt may
retain model-authored completion details under `untrusted_completion_claim`.
The compact JSON document contains at most 12 diagnoses and 48 KiB so one
workflow result can carry the complete evidence document.

Every verifier report records total, retained, omitted, unlocated, and
ambiguous failure counts. An attempt enters a repeated contrast only when each
failed test has one unique bounded locus. Missing, malformed, partial, and
ambiguous reports cannot support a contrast. A malformed artifact stops
evidence collection.

## Identity-bound candidate workflow

The [Terminal-Bench evaluation runner](../evals/terminal_bench/) accepts a
bounded trajectory report produced outside the candidate's authority. The
report identifies the evaluated source tree and runtime binary. Each eligible
contrast names one task, at least two failed attempts, at least one successful
episode identifier, and the common failure profile. Each failed attempt carries
its verifier provenance, completeness counts, and failure loci. A digest
identifies the complete contrast. Failed and successful identifiers must be
disjoint.

The runner copies the report into the retained run directory before creating
the program. The resulting version 3 program contains four model nodes:

1. A diagnosis node receives only the task and bounded trajectory report. In
   automatic mode, it chooses a source or workflow-configuration candidate.
   It may instead report insufficient evidence.
2. A source implementation node receives the typed diagnosis and task. It
   returns a typed summary of changed paths, validation, and unresolved risks.
3. A fresh review node receives the task, diagnosis, and implementation
   handoff. It uses `xhigh` reasoning and can repair the candidate. Its
   44-request allowance cannot consume the finalization allowance. When it
   exhausts, a declared empty handoff lets the workflow continue.
4. A fresh finalization node receives every typed handoff. It has 16 reserved
   requests, runs the candidate checker before review, repairs remaining
   findings, and owns terminal completion through that checker.

The diagnosis verifier enforces a requested candidate kind before an
implementation episode can start. It accepts an insufficient-evidence result
for every requested kind. The source checker runs from outside the candidate's
writable directories and remains the authority for source acceptance. If the
finalization node ends after the source files have been produced, the runner can
recover the typed diagnosis from its child episode and apply the source checker
to the artifact.

A candidate-producing diagnosis selects one contrast by its digest. It cites
every failed episode, verifier-report digest, and locus digest from that
contrast. Its success list copies the selected contrast's successful root
episode identifiers as bare strings. It explains each local failure and states
one shared mechanism. The runtime invokes the diagnosis verifier after the
node returns its typed value. The diagnosis verifier rejects a missing or
substituted citation. The diagnosis returns insufficient evidence when the
loci do not support one shared mechanism.

A source diagnosis proposes one general, source-owned, falsifiable mechanism
from repeated retained failures. Candidate generation does not require prior
evidence of transfer or task-quality improvement. Repository validation can
admit the proposal for evaluation. Unchanged external task quality remains the
sole promotion authority.

An external source-candidate assessment can guide one later generation. The
evaluator constructs the private assessment from the retained source bundle,
a completed parent campaign, and a completed candidate campaign. The
assessment binds the parent and candidate evaluations, source trees, source
bundle, source candidate, and proposal program. Canonical SHA-256 identities
cover each evaluator-owned object. The assessment also
retains the prior typed diagnosis, exact candidate source entries and bytes,
an identity-bound line-oriented patch in unified-diff format, the private
campaign and trial records, and
each exact raw verifier report. For changed and deleted files, the evaluator
reads base blobs from a supplied parent repository and requires each blob to
match the source manifest and recorded base tree.
It reads the private task from the retained plan. For a campaign that has no
plan artifact, it reads the task from the root `episode/start` only when that
event's episode, program, and runtime identities match the trial diagnostics.

The evaluator derives one bounded candidate-assessment diagnostics projection
from the private assessment. The projection contains the prior typed
diagnosis, the verified candidate patch, complete candidate failure loci, final
validation windows, and qualified parent and candidate success references.
The projected patch carries ordered source entries, a unified diff with five
context lines, and the digest of the private full-content patch. Complete file
contents remain in the private assessment.
It contains no raw task text, task name, task checksum, numeric reward,
campaign label, absolute artifact path, or unstructured grader prose. A
normalized failure locus may carry its bounded location, assertion, and
concise message.

The projection is limited to 48 KiB. Construction rejects symbolic links,
escaped paths, incomplete campaigns, errored or nonconformant trials, Boolean
or nonfinite rewards, conflicting identities, and an incomplete source
adoption set. A candidate failure is eligible only when every failed test has
one unique bounded locus. Each validation window contains at most eight
untruncated results. A longer sequence retains the last edit and the latest
seven results, or the latest eight results when no edit occurred. The window
records the number of intervening results that it omitted.

Supplying a private assessment does not add a workflow node. The existing
trajectory tool node returns a document that contains both trajectory
diagnostics and the validated assessment projection. Only the fresh diagnosis
node follows that tool result. The implementation node still receives the task
and typed diagnosis. The review receives the task, diagnosis, and
implementation handoff. Finalization also receives the review handoff or its
declared empty value. No coding node has a read grant for the assessment files.

A revised diagnosis cites the assessment contrast, rejected source-candidate
identity, prior diagnosis digest, every failed attempt, every failed verifier,
and every failure locus. It also cites every qualified parent and candidate
success. Its disposition is `retain`, `narrow`, `replace`, or
`insufficient-evidence`. The diagnosis verifier enforces those citations
before source implementation can start.

The diagnosis verifier also confines task-specific assessment details to the
diagnosis episode. It rejects a typed handoff that copies a retained failure
location, assertion, or message into its explanatory fields. Source acceptance
then scans every changed file for assessment identities, verifier identities,
failure-locus identities, and retained failure text. A match rejects the
candidate before source evidence capture. The implementation child receives
the generalized diagnosis and its opaque citations.

Source evidence for a later generation contains the bounded diagnostics and
a canonical generation-context record. The context binds the revised
diagnosis, disposition, assessment contrast, rejected candidate, trajectory
evidence, and generation parent. These auxiliary files change the source
bundle identity. The source-candidate identity remains a hash of the base
tree and exact source entries. After source capture, the runner rejects a
candidate whose source-candidate identity equals the rejected identity.

An explicit request can produce an instruction-revision or tool-definition
proposal. The Terminal-Bench runner cannot apply either kind to an evaluation
program. Automatic selection excludes them until an application path can bind
the applied program to the proposal.

A workflow candidate preserves the controls that determine its activation.
The binding covers the primary model, reasoning effort, service tier, token
policy, workflow owner, and completion-governance mode. The workflow owner
distinguishes Foe's built-in workflow from a graph created by the evaluation
runner. Completion governance distinguishes a declared verifier from a model
completion report. The binding omits task identity so a candidate can be
evaluated on transfer tasks under the same controls.

The evidence that creates a workflow candidate is task-specific. A successful
audit must reverse a baseline failure on the same activation task. The model,
primary effort, service tier, token policy, workflow owner, and completion
governance must remain equal. Success on a transfer task supplies later
evidence and cannot establish the initial causal contrast.

The default OpenAI service tier is `priority` for all three model nodes. A
preview constructs and validates the complete program without creating the
requested retained directory or sending a model request. It removes any empty
candidate validation directories that it created for grant resolution. A
confirmed run retains those private directories for execution.

The candidate checker establishes repository conformance. It does not assess
Terminal-Bench task quality. The unchanged task-owned Terminal-Bench grader
remains the sole authority for promotion based on quality. Tokens, estimated
cost, cache use, and latency are recorded as diagnostics.

Source and workflow candidates have external Terminal-Bench application
paths. The runner accepts source-candidate evidence rather than a proposed
child program identity. Before provider spend, a trusted checker verifies the
retained source bytes, Git blobs, modes, deletions, clean candidate tree, and
rebuilt binary digest.

Source evaluation runs from a separate controller checkout. The candidate
source tree is an explicit input. The controller records its source root and
committed tree identity. It separately records the trusted build root that
contains the checker, along with runner and checker paths and digests. It
validates proposal-tree provenance and verifier authorization before provider
spend. Source evidence retains the generated candidate checker as a regular
file. Its digest must equal the accepted result recorded by the finalization
child, whose program must declare that checker. A confirmed campaign freezes
the validated source bundle under its run directory and uses that copy for
every later adoption check.

The proposal tree contains one root episode. Every retained child keeps its
complete parent chain to that root. Each `spawn/start` program resolves to the
recorded child identity through the parent identity document. Workflow paths
resolve through nested workflow nodes. The source proposal format carries only
the root identity document, so a retained child cannot start another retained
episode.

Automatic source candidates cannot change Cargo, Bazel, module, toolchain,
package, or build-script metadata. The candidate checker compares that
metadata with its trusted baseline before validation. It rescans repository
status, source bytes, and build metadata after all validation commands.

A no-spend preview may pair the source tree with a caller-supplied binary for
diagnosis. A confirmed campaign builds `//:foe-portable` from the clean,
accepted tree with the controller's Bazel executable and the protected build
graph. The candidate cannot change Cargo, Bazel, module, toolchain, package,
or build-script metadata. The campaign retains the command, Bazel path,
version, and digest, protected build-graph digest, complete build-log digest,
source-tree identity, and output digest. Evaluation and adoption use only the
retained output.

The adapter retains the rebuilt binary's actual `foe plan --json` report. For
a configured program, it creates the report before the episode starts. For
the built-in coding workflow, it reconstructs a program document from the
root `episode/start` and plans that document after the episode. The verifier
and credential paths remain present until reconstruction ends. The adapter
requires the plan to equal the recorded program, task, and identity. The
trusted checker also requires the root log to carry the rebuilt binary digest.
It then constructs and verifies lineage from the actual program identity. The
portable adoption record retains the runtime-effective workflow child
identities and configured verifier that source capture authenticated.
`campaign.json` records the source candidate, completed adoptions, checker
digests, and evaluated source and binary pair. Adoption failure sets
`direct_implementation_required`, invalidates the task record, and makes the
campaign exit unsuccessfully.

The source-improvement controller writes Foe's conventional credential path
into every model block before planning or execution. Transport construction
therefore adds no credential option between the retained plan and the root or
child episode starts.

A source bundle may retain an `execution_credential` beside the parent plan
when its authenticated runtime omitted that resolved option from plan output.
The checker accepts this field only for runtime build
`sha256:ff7d062a57acf865e22d7781fb7e9c05ac95863e5a255fc3145d4479e0eebb59`
and an absolute OpenAI Codex `token_file`. Every episode start must carry the
retained path and the runtime named by the parent identity document. The
checker also verifies the workflow node, its runtime-effective leaf budget,
and the recomputed child identity. The content-addressed episode logs retain
the resulting root and child programs.

## Acceptance conditions

A successful example attempt satisfies six independent conditions:

1. The fresh source produces at least one evaluator finding.
2. The final source produces no evaluator findings.
3. The episode tree contains successful `read`, `edit`, and `check` results.
4. Both declared workflow nodes finish.
5. The root and child episodes record completed outcomes.
6. The trace evaluator accepts authority, budget, reconstruction, outcome,
   and workflow-provenance evidence.

The runner checks the first five conditions directly and invokes
[`evals/trace_quality.py`](../evals/trace_quality.py) for the sixth. The
artifact checker and the trace evaluator answer different questions. The
checker assesses the resulting source. The trace evaluator assesses how foe
produced that source.

The routine example uses a fast structural checker because it copies only the
three affected files. Candidate promotion needs a complete build and test
run. The measured candidates were therefore overlaid onto complete source
archives and compiled after the model-backed attempts.

The Terminal-Bench self-improvement runner has one additional acceptance
condition for a workflow candidate. It must complete a valid lineage adoption
after the artifact check. Adoption failure rejects the candidate, sets
`direct_implementation_required`, and makes the process exit unsuccessfully.

A source candidate first earns artifact acceptance. Its content-addressed
manifest retains regular bytes, Git object types, file modes, blob identities,
and deletions. Evidence capture failure rejects the candidate and makes the
self-improvement process exit unsuccessfully. Successful capture authorizes
external evaluation. Promotion requires the external evaluation to complete
lineage from the rebuilt binary's actual program identity. The required
implementation, regression test, and specification are regular files that
exist after the change. Each is added or content-modified relative to the base
tree. A deletion, symbolic link, type change, unchanged file, or rename-away
does not satisfy the requirement.

## Measured result

The measurements below were collected on 2026-08-22 from three fresh runs of
`openai-codex/gpt-5.6-sol`. Every run used the same program identity and
runtime binary. The program identity hashes stable behavior, authority
shape, budgets, child and workflow definitions, and runtime contributions.
The runtime build hash identifies the binary that enforced the contract.

| property | recorded value |
|---|---|
| program identity | `sha256:de9298226d3c8e633b18070e5bfa22bdb7278ecbf5b191e4588431ab774875bf` |
| runtime build | `sha256:96a0e63f07e540a575b1c183fd00cc939a9dc0c85fe16d004ee7a3ff2d707824` |
| root budget | 72,000 input tokens, 6,000 output tokens, 12 model calls, 240 seconds, 2 episodes |
| model-child budget | 72,000 input tokens, 5,000 output tokens, 10 model calls, 180 seconds |
| sandbox observation | best-effort mode with Landlock ABI 7 |

All three attempts batched the implementation, test, and documentation edits
into one model turn. Every trajectory called the checker and completed
without a final text-only model request.

| attempt | model calls | input tokens | output tokens | workflow-node duration | artifact | trace |
|---:|---:|---:|---:|---:|---|---|
| 1 | 3 | 14,824 | 722 | 16.359 seconds | accepted | conformant |
| 2 | 3 | 15,241 | 1,024 | 22.353 seconds | accepted | conformant |
| 3 | 3 | 14,862 | 780 | 17.744 seconds | accepted | conformant |
| total | 9 | 44,927 | 2,526 | 56.456 seconds | three accepted | three conformant |

Each trace passed 18 declared-authority assertions, 20 hierarchical-budget
assertions, 194 reconstructable-evidence assertions, 6 typed-outcome
assertions, and 11 workflow-provenance assertions. Every candidate compiled
in a complete source archive. All seven `read` tests passed for each
candidate, including the exact rendered-output assertion.

The candidates were independently generated. The implementation and test
files each had two distinct content hashes. The documentation files had three
distinct content hashes. The external checker and compiled tests accepted
these equivalent implementations rather than requiring one recorded patch.

Three successful attempts on one task measure repeatability for that task.
They do not estimate reliability across other kinds of changes.

## What works well

### Deterministic evaluation before model judgment

The evaluator is a tool node because its behavior is fully specified. It
produces the same findings for the same source and uses no model tokens. The
model receives evaluator findings rather than spending a request
rediscovering them.

Model nodes remain useful where the work requires judgment. The terminal node
chooses an implementation that keeps code, tests, and documentation
consistent. Separating deterministic evaluation from model judgment spends
model calls on the part that benefits from them.

### A terminal improvement node

The model node is terminal because the accepted artifact is the workflow's
result. Its `follows` list names only the task and evaluator output. The child
therefore receives the information it needs without inheriting an unrelated
root conversation.

The completion verifier belongs inside the child program. Findings return to
the child that can act on them. One child can revise its work several times
without consuming another episode slot.

### Verifier calls as completion signals

A model commonly edits files, calls a checker, receives a clean result, and
uses another model request to report completion. That last request repeats
the full conversation and changes no artifact.

foe now treats a non-error call to the declared verifier as a completion
signal. The runtime invokes the verifier in its authoritative mode after the
turn settles. A clean result completes the episode before budget exhaustion
is applied.

Three successful traces collected before this rule contained one redundant
final request each. Those requests consumed 23,551 input tokens and 197
output tokens in total. Verifier-call completion removes one request from each
such trajectory. Differences in edit batching prevent attribution of the
entire before-and-after token difference to this rule.

### Evidence outside the model's authority

The model can execute the checker through its declared tool, but it cannot
write the checker or the repository checkout. The runner invokes the checker
again after foe exits. A model response cannot convert a failing artifact
into an accepted artifact by claiming success in prose.

The append-only episode tree records a second evidence layer. It establishes
which files the model read and edited, which checker it called, which budgets
the child consumed, and which workflow inputs reached the child.

### Repeated stochastic and deterministic tests

Provider-backed attempts measure whether the model can perform the task under
real sampling and service conditions. The deterministic transport exercises
a forced correction path without provider cost. It first leaves the
documentation incomplete, receives verifier findings, repairs the
documentation, and completes through a clean checker call.

The two forms cover different risks. Provider repetition detects behavior
variance. The deterministic route protects the recovery and provenance
mechanics from regression.

### Budgets with observed headroom

Budgets protect unattended runs from unbounded spend and execution. They also
need enough headroom for ordinary variation. The model child permits ten
calls even though the measured successful attempts used three calls. Input
and output tokens have separate limits because long context and long answers
create different risks.

The runtime charges provider-reported input usage after each response. Local
token estimates guide context policy and planning. They do not reject a
request as if an estimate were an exact provider measurement.

## Observed failure modes

### Workflow-level retry consumed an additional episode

An earlier configuration placed verification around the workflow model node.
A finding asked the scheduler to fire that node again. The root allowance of
two episodes covered the root and the first child, so the retry could not
start another child.

Moving `done_when.verify` into the child keeps correction within one episode.
This placement also keeps verifier feedback in the context of the model that
can repair the artifact.

### A line-oriented checker rejected valid wrapped prose

The first documentation check searched one line at a time for words that
explained the complete byte count. A model wrote the required phrase across
two Markdown lines. The content was correct, but the checker reported a
finding and caused three additional model turns.

The checker now normalizes Markdown line breaks before applying the semantic
phrase check. Its regression fixture wraps the phrase across two lines. An
evaluator should reject incorrect meaning while accepting formatting that
does not change meaning.

### A completion request exhausted a correct episode

One model-backed attempt produced source that passed the external checker on
its eighth call. The former completion rule still required a separate turn
with no tool calls. The call budget had no remaining request, so the episode
reported `exhausted` even though the artifact was accepted.

The verifier-call completion rule resolves this specific false-negative
outcome. Findings on the last allowed call still produce `exhausted`. The
budget therefore continues to stop unfinished work.

### A structural checker does not establish compilation

The fast checker confirms the requested field, regression assertion, and
specification text. It cannot detect every syntax error or unrelated edit.
The complete-source validation caught this coverage boundary by running the
real `foe-code` tests against each generated candidate.

A cheap checker is useful during an episode. Promotion requires compilation,
tests, and any repository-wide policy checks that the source tree declares.

## Practices to avoid

### Requiring prose after a clean verifier result

A clean verifier result already supplies the program's declared acceptance
evidence. Requiring another model turn increases input tokens and latency. It
also creates a false exhaustion point after the useful work is complete.

### Setting bounds from one best-case trajectory

Edit batching varies across attempts. A model may use one edit turn or one
turn per file while producing equivalent artifacts. Set safety ceilings with
headroom above observed variation. Report actual spend separately from the
declared maximum.

### Treating estimates as exact usage

An approximate tokenizer can help predict context growth. It cannot replace
provider-reported accounting. Charging an estimate as exact usage can stop an
episode whose provider-measured usage remains within the allowance.

### Letting recovery create a fresh model child unnecessarily

A verifier finding usually concerns the state held by the child that made
the change. Feed that finding into the same child through `done_when`.
Workflow-level retries are appropriate only when the program intends a fresh
episode with fresh context and has budgeted another episode.

### Giving the model authority over its evaluator

An evaluator that the candidate can rewrite cannot establish acceptance.
Keep the evaluator executable outside writable roots. Run an external grader
after the episode when the result will influence promotion or comparison.

### Using the repository checkout as the candidate workspace

Self-improvement can corrupt its own evidence when it edits the runtime,
configuration, checker, or logs that govern the active run. Use a disposable
copy with explicit grants. Promote an accepted patch through a separate,
reviewable operation.

### Equating artifact success with runtime success

An external grader may accept the files left by an exhausted or failed
episode. Preserve the artifact result and episode outcome as separate fields.
An explicitly declared verifier may authorize completion at the runtime
boundary. An undeclared post hoc grader must not rewrite episode history.

### Promoting from a structural checker alone

Pattern checks are inexpensive and provide focused feedback. They leave
syntax, type, integration, and policy failures uncovered. Compile and test a
candidate in a complete source tree before promotion.

### Generalizing from one task

Three accepted attempts establish repeatability for the `total_bytes` task
under one model route. They provide no estimate for behavioral fixes, tool
creation, large refactors, long-context work, or another provider. Report the
tested scope with every reliability claim.

### Enabling compaction without an accuracy evaluation

Compaction can lower repeated context by replacing prior evidence with a
summary. The summary can omit details needed for a correct change. Measure
artifact accuracy, token use, and latency together before enabling compaction
for self-improvement tasks.

## Evidence to collect

Every self-improvement evaluation should retain the following evidence:

- the initial and final evaluator findings;
- the candidate diff and the immutable source revision it started from;
- the source-bundle and source-candidate identities;
- each completed adoption, evidence, program, state, and parent identity;
- the evaluation checker digest and controller-build record;
- the retained Git object, mode, bytes, or deletion for every changed file;
- root and child outcomes;
- provider-reported input, output, and cache-read tokens per response;
- model requests, tool calls, rendered tool-result sizes, and tool errors;
- workflow node inputs, outputs, branches, and durations;
- budget reservations, releases, and final spend;
- the complete trace-conformance report;
- external compilation, test, and policy-check results;
- the model route, program identity, runtime build, sandbox observation, and
  attempt count.

Per-response accounting reveals context amplification that aggregate totals
hide. Tool-result sizes identify outputs that every later request carries
again. Separate artifact and outcome fields expose false-negative completion
behavior. Workflow provenance establishes whether the evaluator findings
actually reached the model that changed the source.

## Recommended operating pattern

Use the following sequence for a bounded self-improvement task:

1. Define an evaluator that prints one actionable finding per line and exits
   successfully when it can judge the candidate.
2. Confirm that the untouched source fails the evaluator.
3. Run the evaluator as a tool node.
4. Pass the task and findings to one terminal model node with narrow read and
   write grants.
5. Put the evaluator in the child program's `done_when.verify` field.
6. Keep model-call and token ceilings above observed successful variance.
7. Re-run an external grader after the episode.
8. Compile and test the candidate in a complete disposable source tree.
9. Capture the accepted source candidate as content-addressed evidence.
10. Inspect trace conformance and resource use.
11. Evaluate the rebuilt candidate against unchanged external tasks.
12. Complete lineage from each actual evaluation program and episode.
13. Promote the patch through a separate review step.

Improvements may add capability, simplify implementation, or remove behavior
that harms task quality. A bound, retry rule, or completion condition is a
valid improvement target when measured evidence shows that it rejects correct
artifacts or consumes resources without increasing acceptance quality.

## Reproduction

Run the deterministic workflow and its forced-correction test:

```sh
bazel run //examples/self-extension:self-improvement-workflow
bazel test //examples/self-extension:self_improvement_workflow_model_runner_test
```

Print the provider-backed spending plan, then run three fresh attempts:

```sh
bazel run //examples/self-extension:self-improvement-workflow-model -- \
  --attempts 3
bazel run //examples/self-extension:self-improvement-workflow-model -- \
  --attempts 3 \
  --confirm-spend
```

For any retained candidate, overlay its three files onto a complete source
copy and run the affected test module:

```sh
cargo test -p foe-code read::tests
```

The model-backed runner records the candidate source and episode tree under
`target/`. Its final output prints the exact directory and the command that
opens the episode in the viewer.

## Conclusion

foe can express bounded self-improvement as a two-node workflow. Deterministic
evaluation supplies evidence, and one terminal model episode changes the
source under narrow authority. A declared verifier closes the loop without a
redundant completion request.

The measured task succeeded in three fresh provider-backed attempts. Every
candidate passed the external checker, compiled, and passed the affected
tests. Every trajectory was conformant. This result supports the workflow
structure for small assessed changes. Broader claims require more tasks,
models, providers, long-context conditions, behavioral removals, and tool-
creation evaluations.
