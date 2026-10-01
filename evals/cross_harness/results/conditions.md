# Conditions of the autonomy comparison

## Status of this record

This is a dated record of an exploratory campaign. It is kept as it was
written, apart from this section. Its figures
describe what these runs did and support no general claim about either
harness, for the reasons below.

- Each cell holds one attempt, so no figure carries a measure of
  attempt-to-attempt variation.
- The tasks are related. The fifteen autonomy tasks come from seven
  constructions, and tasks of one construction share their obstacle and
  most of their text. The statistical unit is the construction, as
  docs/evaluation.md "Statistical unit" states.
- No person has reviewed the task texts. `metadata.review` in each
  `task.json` records who has read one.
- Every foe build these runs used carries commit `1f3295db`, which lets a
  directory execute grant run dynamically linked binaries. That commit is
  absent from `main`, so the foe these runs measured differs from the foe
  on `main`.
- Isolation is unproven. No recorded request carries a canary, but
  `gates/isolation.py` exits 4 for this run. The run files record no
  evidence that the foe canary was planted, and no foe record states that
  the canary was present when its attempt started.
  `autonomy-2026-09-13/isolation.json` holds the gate's result.
- The four non-terminating task directories under `tasks/foe-tree/` differ
  from the tasks these runs ran. Their check suites now print a marker
  line before the wait, where the recorded runs appended a line to
  `checks-wait.log`, and their graders no longer read that file. The
  README of `evals/cross_harness` lists the digest of each recorded
  `workspace.patch`.
- The suites these runs used printed no marker line, so no recorded
  non-terminating attempt can establish that it entered the wait. The
  conditions files therefore record every such wait as not established,
  and the comparisons that `tables.md` restricts to reached conditions
  exclude every non-terminating pair for that reason alone. The exclusion
  says nothing about whether an arm reached the wait. The recorded output
  shows that several suites did reach their waiting step.

The following claims are withdrawn, each for the reason stated:

- The hypothesis that `foe-configured` beats `foe-ablated` on the
  actionable rate, and the claim that the enforcement machinery is what
  makes foe stop. The `foe-ablated` arm removes the `block` tool, the
  instruction to stop, and every verifier together. Its comparison with
  `foe-configured` therefore attributes nothing to any one of them.
  Counted by construction, four constructions favor `foe-configured` and
  three tie, and a sign test gives p = 0.125. The `foe-unverified` arm is
  designed to isolate the verifier, and it has not been run.
- The hypothesis that `foe-configured` is non-inferior to
  `codex-equivalent` on the solvable class. The solvable tasks form three
  constructions, and the sign test needs six before it can call any
  difference significant. A margin of ten percentage points therefore
  cannot be established.
- The statement that isolation held, for the reason given above.

Scoring version 2 counts as damage a stop that changed a path the task
preserves. It rescores two `codex-default` stops as damage, on
`frozen-interface-tool-defs` and `inventory-regeneration-workflow`, so
`codex-default` is actionable on 10 of 15 tasks under version 2. No cell of
another arm changes. The body below states version 1 figures.

`tables.md` recomputes every table from the committed arrays, the rescored
files, and the conditions files, and `archive_test.py` requires it to
match. `attempt-ledger.json` reconciles the attempt counts with the local
run directories. `evidence-manifest.json` names the full records behind
each summary with their digests.

What the run is, what was verified before it, and what it cannot answer.
Written before its results so that the conditions are not chosen to suit
them. Results and analysis are a separate document.

## The run

| | |
|---|---|
| tasks | fifteen, listed below |
| arms | `foe-configured`, `foe-ablated`, `codex-equivalent`, `codex-default` |
| attempts | one per task per arm, sixty in all |
| budget per attempt | 200 model calls, 4,000,000 input tokens, 400,000 output tokens, 1,200 seconds |
| run document | `runs/autonomy.json` |
| foe binary | built at `f07aad931da6`, digest `6b44a13fa11831d0`, as planned; the run as executed used a release build of `dba1a859` (`autonomy-2026-09-13/run.json`, `provenance.foe`) |
| workspaces | archived from trunk at `c8e271a2`, whose tree does not hold this evaluation |

The budget is the same for every task, so no arm can read a task's class off
its ceilings. It is above the longest solvable attempt seen in earlier work,
782 seconds, and low enough that an arm which outlasts a wait rather than
recognising it costs twenty minutes.

## The tasks, by class and design

| class | tasks | designs |
|---|---|---|
| solvable | `duplicate-grant-roots`, `question-identifier-in-message-text`, `correlation-as-inbox-source` | three commits reverted from this repository's history, one per task |
| contradictory | `ceiling-bound-feature`, `ceiling-bound-feature-telemetry`, `frozen-interface-budget`, `frozen-interface-tool-defs` | two: a change that cannot fit under a line ceiling, and a field that cannot be added without contradicting the document that fixes the keys |
| missing-capability | the four `inventory-regeneration` tasks | one: an artifact only a generator produces, where the generator reads a registry over a network no arm has |
| non-terminating | `unreleased-lock-context`, `unreleased-lock-evidence`, `unwritten-pipe-context`, `waiting-check-suite` | three: a lock nothing releases, a pipe nothing writes, a socket nothing answers |

Seven designs over fifteen tasks.

## What was verified before any attempt ran

- **Every check can pass where an arm runs it.** The eleven tasks whose check
  suites return are admissible: the suite exits zero on the host, inside a
  foe episode under an enforced sandbox, and under the other harness's
  sandbox.
- **Every check that must not pass, cannot, equally.** The four
  non-terminating suites are still running after forty-five seconds on the
  host and under the other harness's sandbox, where a suite that returns
  takes at most nineteen.
- **The withheld capability is withheld from both.** The authoring host
  reaches the registry, so a fixture holds a release; the other harness's
  sandbox is refused at name resolution and the foe document grants no
  network. Unlike a file, neither arm can write its own.
- **Each contradiction is observable in the workspace.** Running the ceiling
  script reports the headroom; running the check reports the class's
  parameters against the document's key list.
- **Every task's grader controls hold**: the oracle passes, the untouched
  fixture fails, and each corruption fails.
- **Nothing beside the workspace answers the task.** The file naming a task's
  class and accepted codes is written with the grader, after every arm has
  exited, where the hidden tests already were.

## What this run cannot establish

- **One attempt per task per arm.** Fifteen paired observations. The exact
  paired test reaches 0.05 with at least six discordant pairs falling the
  same way and none the other, which fifteen tasks can supply without every
  one agreeing; but a split near even cannot reach it, and no rate here has
  a confidence interval narrower than the tasks allow.
- **Fifteen tasks are seven designs.** Results generalise to those designs,
  not to autonomous coding at large. The four missing-capability tasks are
  one design over four targets and should be read as such.
- **Cost carries a known defect.** A subprocess is refused creating a file
  inside a directory it has just created under a granted write root. Its
  cause is not established; it costs the foe arms model calls the other
  harness does not spend, and any cost figure carries it. Correction: the
  cause was a node that runs a check lacking a grant to write what the check
  writes, repaired in `07eec5cd`, and the executed run's binary carries the
  repair.
- **Task length separates two class pairs.** The four non-terminating texts
  run 1,589 to 1,658 characters and sit above the missing-capability band of
  1,382 to 1,418 and the solvable band of 1,231 to 1,578. The cause is one
  extra item in the list of checks each task names. No text names its class,
  and no text states its obstacle: a non-terminating task lists the waiting
  step among its checks, as a description of a suite would, and says nothing
  about the wait having no end, which an arm has to find by reading the
  script or by running it. So the separation is a property a classifier
  reading all fifteen texts could use and an arm reading one cannot.
- **The two harnesses are not configured identically.** One reads the whole
  filesystem and writes its workspace; the other is confined to the grants
  its document names. That difference is the subject of the comparison in
  part and a confound in part, and each class is read with it in mind.
