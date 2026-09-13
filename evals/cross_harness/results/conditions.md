# Conditions of the cross-harness autonomy run

What the run was, what was verified before it, and what it cannot answer.
Written before its results so that the conditions are not chosen to suit
them. The results and the analysis are a separate document.

## The run

| | |
|---|---|
| tasks | `duplicate-grant-roots`, `correlation-as-inbox-source` (solvable); `ceiling-bound-feature-telemetry`, `frozen-interface-budget` (contradictory); `unreleased-lock-context`, `unreleased-lock-evidence` (non-terminating) |
| arms | `foe-configured`, `foe-ablated`, `codex-equivalent`, `codex-default` |
| attempts | one per task per arm, 24 in all |
| budget per attempt | 200 model calls, 4,000,000 input tokens, 400,000 output tokens, 1,800 seconds |
| run document | `runs/rerun-after-fixes.json` |

The foe binary is built from this branch merged with trunk at
`43ed4d1ae66d`. Trunk alone cannot run the evaluation: six runtime
commits made during this work are not on it, and one of them gives an
episode the scratch directory the check tool writes into. The workspaces are
archived from trunk at `c8e271a2`, which carries the repaired line counter
and does not carry the evaluation, so no workspace shows an arm the
instruments that grade it.

## What was verified before any attempt ran

- **The check can pass where an arm runs it.** The four tasks whose check
  suites return are admissible: the suite exits zero on the host, inside a
  foe episode under an enforced sandbox, and under the other harness's
  sandbox.
- **The check cannot pass, equally, where it is meant not to.** The two
  non-terminating tasks are gated differently, because their suites are
  built never to return and the ordinary gate would wait out its own limit
  and call them inadmissible. Their suites are still running after
  forty-five seconds on the host and under the other harness's sandbox,
  where a suite that returns takes at most nineteen seconds on this tree.
- **A task presuming a capability absent is refused when the capability is
  on the host.** Both inventory tasks are refused on this host and are not
  in the run.
- **A granted toolchain is reachable by name.** Confirmed in the run itself:
  twenty-one shell commands name the Rust toolchain under the foe arms and
  none exits with the status a missing command produces.

## What this run cannot establish

- **One attempt per task per arm.** Six tasks over three classes. The exact
  paired test reaches a two-sided probability at or below 0.05 only with at
  least six discordant pairs, which six paired attempts cannot produce. Every
  rate here is descriptive.
- **Three designs, not six tasks.** The two contradictory tasks are one
  construction over two targets and so are the two non-terminating ones. The
  result generalises to those designs and not to autonomous coding at large.
- **The two contradictory tasks do not measure the same thing.** One
  conflict is settled by running a command and reading a number; the other
  by weighing a repository rule against a direct instruction. They are
  reported separately and not averaged.
- **Cost is the cost of foe as it stands.** The repair that named a prompt
  cache after the conversation did not change the measured hit rate, and the
  reason for the gap is not established.
- **The missing-capability class is absent.** Its two tasks were refused, so
  this run says nothing about that class.
- **Task length still separates two of the three classes.** In the six texts
  of this run the two non-terminating tasks run 1,589 and 1,619 characters
  while the two solvable ones run 1,231 and 1,524, so the bands do not
  overlap. Across every autonomy task in the tree the only separated pair is
  missing-capability against non-terminating, and neither band is wide: each
  is one construction over two or five targets, and a construction writes
  texts of nearly the same length. The separation is a symptom of two tasks
  per class from one design rather than a property of the classes, and at two
  samples it cannot be told from chance. An arm sees one text at a time and
  has nothing to compare it against, so this bounds what a classifier reading
  the texts alone could be said to prove, not what an arm could exploit.
