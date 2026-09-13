# Conditions of the autonomy comparison

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
| foe binary | built at `f07aad931da6`, digest `6b44a13fa11831d0` |
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
  harness does not spend, and any cost figure carries it.
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
