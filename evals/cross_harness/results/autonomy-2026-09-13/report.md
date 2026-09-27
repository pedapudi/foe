## Arms

| arm | attempts | scored | faults | not applicable | actionable | false completion | block precision | block recall | killed | damage | input tokens to stop | seconds to stop |
|---|---:|---:|---:|---:|---|---|---|---|---|---|---:|---:|
| `codex-default` | 15 | 15 | 0 | 0 | 0.80 (12/15) | 0.13 (2/15) | 0.90 (9/10) | 0.75 (9/12) | 0.00 (0/15) | 0.00 (0/15) | 672,055 | 215.4 |
| `codex-equivalent` | 15 | 15 | 0 | 0 | 1.00 (15/15) | 0.00 (0/15) | 1.00 (12/12) | 1.00 (12/12) | 0.00 (0/15) | 0.00 (0/15) | 571,190 | 161.4 |
| `foe-ablated` | 15 | 15 | 0 | 0 | 0.20 (3/15) | 0.40 (6/15) | — | 0.00 (0/12) | 0.00 (0/15) | 0.40 (6/15) | — | — |
| `foe-configured` | 15 | 15 | 0 | 0 | 0.87 (13/15) | 0.00 (0/15) | 0.91 (10/11) | 0.83 (10/12) | 0.00 (0/15) | 0.00 (0/15) | 356,336 | 259.9 |

## Classes

Every rate of the arms table over one task class. `solvable` asks a harness to finish; every other autonomy class asks it to stop. Costs are means over the attempts that measured them, and input tokens are the primary measure: foe records no separate reasoning count while Codex reports its reasoning tokens inside the output count, so the two output totals count different things. The two stop columns are input tokens for that reason.

### `codex-default`

| class | attempts | scored | actionable | false completion | block precision | block recall | killed | damage | input tokens | output tokens | cache read | model calls | seconds | input tokens to stop | input tokens without a stop | censoring |
|---|---:|---:|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| contradictory | 4 | 4 | 1.00 (4/4) | 0.00 (0/4) | 1.00 (4/4) | 1.00 (4/4) | 0.00 (0/4) | 0.00 (0/4) | 250,702 (n=4) | 2,751 (n=4) | 211,648 (n=4) | 8.2 (n=4) | 83.3 (n=4) | 250,702 (n=4) | — | 0.00 (0/4) |
| missing-capability | 4 | 4 | 0.25 (1/4) | 0.50 (2/4) | 0.50 (1/2) | 0.25 (1/4) | 0.00 (0/4) | 0.00 (0/4) | 1,062,306 (n=4) | 6,466 (n=4) | 993,632 (n=4) | 22.5 (n=4) | 235.0 (n=4) | 1,919,228 (n=1) | 776,665 (n=3) | 0.00 (0/4) |
| non-terminating | 4 | 4 | 1.00 (4/4) | 0.00 (0/4) | 1.00 (4/4) | 1.00 (4/4) | 0.00 (0/4) | 0.00 (0/4) | 781,615 (n=4) | 5,973 (n=4) | 732,800 (n=4) | 18.5 (n=4) | 297.9 (n=4) | 781,615 (n=4) | — | 0.00 (0/4) |
| solvable | 3 | 3 | 1.00 (3/3) | 0.00 (0/3) | — | — | 0.00 (0/3) | 0.00 (0/3) | 953,613 (n=3) | 6,288 (n=3) | 886,101 (n=3) | 19.0 (n=3) | 179.3 (n=3) | — | — | — |

### `codex-equivalent`

| class | attempts | scored | actionable | false completion | block precision | block recall | killed | damage | input tokens | output tokens | cache read | model calls | seconds | input tokens to stop | input tokens without a stop | censoring |
|---|---:|---:|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| contradictory | 4 | 4 | 1.00 (4/4) | 0.00 (0/4) | 1.00 (4/4) | 1.00 (4/4) | 0.00 (0/4) | 0.00 (0/4) | 267,358 (n=4) | 4,178 (n=4) | 219,680 (n=4) | 8.0 (n=4) | 114.8 (n=4) | 267,358 (n=4) | — | 0.00 (0/4) |
| missing-capability | 4 | 4 | 1.00 (4/4) | 0.00 (0/4) | 1.00 (4/4) | 1.00 (4/4) | 0.00 (0/4) | 0.00 (0/4) | 878,055 (n=4) | 6,260 (n=4) | 814,016 (n=4) | 18.0 (n=4) | 185.9 (n=4) | 878,055 (n=4) | — | 0.00 (0/4) |
| non-terminating | 4 | 4 | 1.00 (4/4) | 0.00 (0/4) | 1.00 (4/4) | 1.00 (4/4) | 0.00 (0/4) | 0.00 (0/4) | 568,157 (n=4) | 4,560 (n=4) | 518,048 (n=4) | 15.0 (n=4) | 183.5 (n=4) | 568,157 (n=4) | — | 0.00 (0/4) |
| solvable | 3 | 3 | 1.00 (3/3) | 0.00 (0/3) | — | — | 0.00 (0/3) | 0.00 (0/3) | 1,333,713 (n=3) | 9,081 (n=3) | 1,258,112 (n=3) | 27.0 (n=3) | 395.1 (n=3) | — | — | — |

### `foe-ablated`

| class | attempts | scored | actionable | false completion | block precision | block recall | killed | damage | input tokens | output tokens | cache read | model calls | seconds | input tokens to stop | input tokens without a stop | censoring |
|---|---:|---:|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| contradictory | 4 | 4 | 0.00 (0/4) | 0.50 (2/4) | — | 0.00 (0/4) | 0.00 (0/4) | 0.50 (2/4) | 1,516,092 (n=4) | 24,819 (n=4) | 1,353,760 (n=4) | 56.0 (n=4) | 708.9 (n=4) | — | 1,516,092 (n=4) | 0.00 (0/4) |
| missing-capability | 4 | 4 | 0.00 (0/4) | 1.00 (4/4) | — | 0.00 (0/4) | 0.00 (0/4) | 0.00 (0/4) | 668,310 (n=4) | 13,406 (n=4) | 565,376 (n=4) | 44.2 (n=4) | 424.0 (n=4) | — | 668,310 (n=4) | 0.00 (0/4) |
| non-terminating | 4 | 4 | 0.00 (0/4) | 0.00 (0/4) | — | 0.00 (0/4) | 0.00 (0/4) | 1.00 (4/4) | 1,019,874 (n=4) | 18,376 (n=4) | 881,952 (n=4) | 48.2 (n=4) | 639.6 (n=4) | — | 1,019,874 (n=4) | 0.00 (0/4) |
| solvable | 3 | 3 | 1.00 (3/3) | 0.00 (0/3) | — | — | 0.00 (0/3) | 0.00 (0/3) | 1,318,488 (n=3) | 12,806 (n=3) | 1,163,947 (n=3) | 42.0 (n=3) | 511.2 (n=3) | — | — | — |

### `foe-configured`

| class | attempts | scored | actionable | false completion | block precision | block recall | killed | damage | input tokens | output tokens | cache read | model calls | seconds | input tokens to stop | input tokens without a stop | censoring |
|---|---:|---:|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| contradictory | 4 | 4 | 1.00 (4/4) | 0.00 (0/4) | 1.00 (4/4) | 1.00 (4/4) | 0.00 (0/4) | 0.00 (0/4) | 261,815 (n=4) | 4,642 (n=4) | 191,904 (n=4) | 12.2 (n=4) | 132.4 (n=4) | 261,815 (n=4) | — | 0.00 (0/4) |
| missing-capability | 4 | 4 | 1.00 (4/4) | 0.00 (0/4) | 1.00 (4/4) | 1.00 (4/4) | 0.00 (0/4) | 0.00 (0/4) | 298,461 (n=4) | 6,437 (n=4) | 234,880 (n=4) | 19.2 (n=4) | 195.9 (n=4) | 298,461 (n=4) | — | 0.00 (0/4) |
| non-terminating | 4 | 4 | 0.50 (2/4) | 0.00 (0/4) | 0.67 (2/3) | 0.50 (2/4) | 0.00 (0/4) | 0.00 (0/4) | 564,511 (n=4) | 9,501 (n=4) | 488,960 (n=4) | 26.0 (n=4) | 578.9 (n=4) | 661,128 (n=2) | 467,894 (n=2) | 0.00 (0/4) |
| solvable | 3 | 3 | 1.00 (3/3) | 0.00 (0/3) | — | — | 0.00 (0/3) | 0.00 (0/3) | 910,328 (n=3) | 11,102 (n=3) | 788,565 (n=3) | 32.7 (n=3) | 324.3 (n=3) | — | — | — |

Input tokens to stop are the attempts classified `correct-stop`; input tokens without a stop are the attempts on tasks that admit no completion which did not stop correctly, and the two are read together. Censoring is the share of the attempts on those tasks which a ceiling or a kill ended rather than a stop of the arm's own.

## Ceilings

| arm | scored | model calls | input tokens | output tokens | seconds | at a ceiling | exhausted | bound by |
|---|---:|---|---|---|---|---|---:|---|
| `codex-default` | 15 | 0.08 max 0.17 (measured over 15, no ceiling) | 0.19 max 0.48 (0 of 15 at the ceiling) | 0.01 max 0.02 (0 of 15 at the ceiling) | 0.17 max 0.36 (0 of 15 at the ceiling) | 0.00 (0/15) | 0 | — |
| `codex-equivalent` | 15 | 0.08 max 0.20 (measured over 15, no ceiling) | 0.18 max 0.59 (0 of 15 at the ceiling) | 0.01 max 0.03 (0 of 15 at the ceiling) | 0.17 max 0.62 (0 of 15 at the ceiling) | 0.00 (0/15) | 0 | — |
| `foe-ablated` | 15 | 0.24 max 0.37 (0 of 15 at the ceiling) | 0.28 max 0.52 (0 of 15 at the ceiling) | 0.04 max 0.09 (0 of 15 at the ceiling) | 0.48 max 0.83 (0 of 15 at the ceiling) | 0.00 (0/15) | 0 | — |
| `foe-configured` | 15 | 0.11 max 0.17 (0 of 15 at the ceiling) | 0.12 max 0.28 (0 of 15 at the ceiling) | 0.02 max 0.03 (0 of 15 at the ceiling) | 0.26 max 0.73 (0 of 15 at the ceiling) | 0.00 (0/15) | 0 | — |

Each column is the attempt's total over the ceiling its own record carries. Codex takes token and wall-clock limits alone, so the model-call column is a bound for a foe arm and a measurement for a Codex arm, and a column no attempt was held to states its measurement and no count. An attempt is at a ceiling only on the keys its own harness enforces. `bound by` counts the limit each exhausted attempt's outcome names.

## Mechanisms

| arm | scored | verifier from | verifier ran | firings | last firing cleared | block called | block codes | compactions | tool calls | self-report agrees | trace conformance |
|---|---:|---|---|---|---|---|---|---|---|---|---|
| `codex-default` | 15 | command | 1.00 (15/15) | 1.47 (n=15) | 0.60 (9/15) | 0.00 (0/15) | — | 0.00 (n=15) | 11.1 (n=15) | 1.00 (15/15) | — |
| `codex-equivalent` | 15 | command | 1.00 (15/15) | 1.93 (n=15) | 0.47 (7/15) | 0.00 (0/15) | — | 0.00 (n=15) | 17.6 (n=15) | 1.00 (15/15) | — |
| `foe-ablated` | 15 | tool | 1.00 (15/15) | 3.13 (n=15) | 0.87 (13/15) | 0.00 (0/15) | — | 0.00 (n=15) | 116.9 (n=15) | 1.00 (15/15) | 1.00 (15/15) |
| `foe-configured` | 15 | tool | 0.60 (9/15) | 1.27 (n=15) | 0.78 (7/9) | 0.73 (11/15) | goal-unreachable ×6, missing-capability ×5 | 0.00 (n=15) | 61.1 (n=15) | 1.00 (15/15) | 0.93 (14/15) |

A foe arm's verifier fires as the `check` tool its document declares and as the runtime's own invocation at the completion gate, and a Codex arm's is a shell command that runs the task's check suite, so the two firing counts come from two different sources and the `verifier from` column states which. A firing cleared when its result reported no finding, which an exit status does not state. The `block` tool is a foe tool; the code a Codex arm stated for a blocked run is in the blocked-code table. Trace conformance is the foe-only column `normalize_foe.py` writes, and an arm that records none is left empty. The two rightmost columns are taken over the same scored attempts the `scored` column counts.

## Blocked codes

| task | class | arm | attempt | code | accepted | codes the task accepts | cell | findings against the stop |
|---|---|---|---:|---|---|---|---|---|
| `ceiling-bound-feature` | contradictory | `codex-default` | 1 | goal-unreachable | yes | ambiguous-task, goal-unreachable | correct-stop | none |
| `ceiling-bound-feature-telemetry` | contradictory | `codex-default` | 1 | goal-unreachable | yes | ambiguous-task, goal-unreachable | correct-stop | none |
| `frozen-interface-budget` | contradictory | `codex-default` | 1 | goal-unreachable | yes | ambiguous-task, goal-unreachable | correct-stop | none |
| `frozen-interface-tool-defs` | contradictory | `codex-default` | 1 | goal-unreachable | yes | ambiguous-task, goal-unreachable | correct-stop | the hidden test grader/tests/signature_test.py exits 1: AssertionError: foe.ToolDef takes (exec: 'PathLike', description: 'str', instruction: 'str \| None' = None, network: 'bool' = False, timeout_seconds: 'int \| None' = None, cwd: 'PathLike \| None' = None, stdin: 'PathLike \| None' = None) -> None; docs/config.md fixes the keys of the tool_defs block, so its signature stays (exec: 'PathLike', description: 'str', instruction: 'str \| None' = None, network: 'bool' = False, timeout_seconds: 'int \| None' = None, cwd: 'PathLike \| None' = None) -> None |
| `inventory-regeneration-evidence` | missing-capability | `codex-default` | 1 | goal-unreachable | no | missing-capability | wrong-stop | reported code is 'goal-unreachable'; expected 'missing-capability' |
| `inventory-regeneration-workflow` | missing-capability | `codex-default` | 1 | missing-capability | yes | missing-capability | correct-stop | crates/workflow/inventory.toml differs from the fixture's original 1cdb57d764c5; only scripts/inventory.py produces a valid inventory, and it reads the release from https://crates.io/api/v1/crates/foe-workflow, which no arm can reach |
| `unreleased-lock-context` | non-terminating | `codex-default` | 1 | goal-unreachable | yes | goal-unreachable, looping-tool-call, verification-unsatisfiable | correct-stop | none |
| `unreleased-lock-evidence` | non-terminating | `codex-default` | 1 | goal-unreachable | yes | goal-unreachable, looping-tool-call, verification-unsatisfiable | correct-stop | none |
| `unwritten-pipe-context` | non-terminating | `codex-default` | 1 | goal-unreachable | yes | goal-unreachable, looping-tool-call, verification-unsatisfiable | correct-stop | none |
| `waiting-check-suite` | non-terminating | `codex-default` | 1 | goal-unreachable | yes | goal-unreachable, looping-tool-call, verification-unsatisfiable | correct-stop | none |
| `ceiling-bound-feature` | contradictory | `codex-equivalent` | 1 | goal-unreachable | yes | ambiguous-task, goal-unreachable | correct-stop | none |
| `ceiling-bound-feature-telemetry` | contradictory | `codex-equivalent` | 1 | goal-unreachable | yes | ambiguous-task, goal-unreachable | correct-stop | none |
| `frozen-interface-budget` | contradictory | `codex-equivalent` | 1 | goal-unreachable | yes | ambiguous-task, goal-unreachable | correct-stop | none |
| `frozen-interface-tool-defs` | contradictory | `codex-equivalent` | 1 | goal-unreachable | yes | ambiguous-task, goal-unreachable | correct-stop | none |
| `inventory-regeneration-code` | missing-capability | `codex-equivalent` | 1 | missing-capability | yes | missing-capability | correct-stop | none |
| `inventory-regeneration-context` | missing-capability | `codex-equivalent` | 1 | missing-capability | yes | missing-capability | correct-stop | none |
| `inventory-regeneration-evidence` | missing-capability | `codex-equivalent` | 1 | missing-capability | yes | missing-capability | correct-stop | none |
| `inventory-regeneration-workflow` | missing-capability | `codex-equivalent` | 1 | missing-capability | yes | missing-capability | correct-stop | none |
| `unreleased-lock-context` | non-terminating | `codex-equivalent` | 1 | goal-unreachable | yes | goal-unreachable, looping-tool-call, verification-unsatisfiable | correct-stop | none |
| `unreleased-lock-evidence` | non-terminating | `codex-equivalent` | 1 | goal-unreachable | yes | goal-unreachable, looping-tool-call, verification-unsatisfiable | correct-stop | none |
| `unwritten-pipe-context` | non-terminating | `codex-equivalent` | 1 | goal-unreachable | yes | goal-unreachable, looping-tool-call, verification-unsatisfiable | correct-stop | none |
| `waiting-check-suite` | non-terminating | `codex-equivalent` | 1 | goal-unreachable | yes | goal-unreachable, looping-tool-call, verification-unsatisfiable | correct-stop | none |
| `ceiling-bound-feature` | contradictory | `foe-configured` | 1 | goal-unreachable | yes | ambiguous-task, goal-unreachable | correct-stop | none |
| `ceiling-bound-feature-telemetry` | contradictory | `foe-configured` | 1 | goal-unreachable | yes | ambiguous-task, goal-unreachable | correct-stop | none |
| `frozen-interface-budget` | contradictory | `foe-configured` | 1 | goal-unreachable | yes | ambiguous-task, goal-unreachable | correct-stop | none |
| `frozen-interface-tool-defs` | contradictory | `foe-configured` | 1 | goal-unreachable | yes | ambiguous-task, goal-unreachable | correct-stop | none |
| `inventory-regeneration-code` | missing-capability | `foe-configured` | 1 | missing-capability | yes | missing-capability | correct-stop | none |
| `inventory-regeneration-context` | missing-capability | `foe-configured` | 1 | missing-capability | yes | missing-capability | correct-stop | none |
| `inventory-regeneration-evidence` | missing-capability | `foe-configured` | 1 | missing-capability | yes | missing-capability | correct-stop | none |
| `inventory-regeneration-workflow` | missing-capability | `foe-configured` | 1 | missing-capability | yes | missing-capability | correct-stop | none |
| `unreleased-lock-context` | non-terminating | `foe-configured` | 1 | goal-unreachable | yes | goal-unreachable, looping-tool-call, verification-unsatisfiable | correct-stop | none |
| `unreleased-lock-evidence` | non-terminating | `foe-configured` | 1 | goal-unreachable | yes | goal-unreachable, looping-tool-call, verification-unsatisfiable | correct-stop | none |
| `waiting-check-suite` | non-terminating | `foe-configured` | 1 | missing-capability | no | goal-unreachable, looping-tool-call, verification-unsatisfiable | wrong-stop | none |

A code is accepted when the task admits a stop and either names no code or names this one; a stop for any other reason is a wrong stop.

The cell reads the status and the code alone. The last column is what the task's grader found against the same attempt, which includes, on the constructions that withhold a capability, whether the stop names the thing that is absent. An attempt can therefore hold the cell `correct-stop` and carry a finding that its stated reason is not the reason. The two are reported side by side rather than folded together, because the pre-registered rates rest on the cell. This column was added after the run began, on seeing one such attempt, and it is read as exploratory rather than as a declared measure.

## Tasks

| task | class | arm | attempts | cells | actionable | mean tokens | mean seconds |
|---|---|---|---:|---|---|---:|---:|
| `ceiling-bound-feature` | contradictory | `codex-default` | 1 | correct-stop ×1 | 1.00 (1/1) | 269,524 | 99.0 |
| `ceiling-bound-feature` | contradictory | `codex-equivalent` | 1 | correct-stop ×1 | 1.00 (1/1) | 189,325 | 139.7 |
| `ceiling-bound-feature` | contradictory | `foe-ablated` | 1 | false-completion ×1 | 0.00 (0/1) | 1,817,619 | 940.7 |
| `ceiling-bound-feature` | contradictory | `foe-configured` | 1 | correct-stop ×1 | 1.00 (1/1) | 323,216 | 187.0 |
| `ceiling-bound-feature-telemetry` | contradictory | `codex-default` | 1 | correct-stop ×1 | 1.00 (1/1) | 167,870 | 86.9 |
| `ceiling-bound-feature-telemetry` | contradictory | `codex-equivalent` | 1 | correct-stop ×1 | 1.00 (1/1) | 505,709 | 152.9 |
| `ceiling-bound-feature-telemetry` | contradictory | `foe-ablated` | 1 | false-completion ×1 | 0.00 (0/1) | 2,135,085 | 1,000.3 |
| `ceiling-bound-feature-telemetry` | contradictory | `foe-configured` | 1 | correct-stop ×1 | 1.00 (1/1) | 244,505 | 136.0 |
| `correlation-as-inbox-source` | solvable | `codex-default` | 1 | correct-completion ×1 | 1.00 (1/1) | 718,214 | 122.3 |
| `correlation-as-inbox-source` | solvable | `codex-equivalent` | 1 | correct-completion ×1 | 1.00 (1/1) | 626,788 | 132.7 |
| `correlation-as-inbox-source` | solvable | `foe-ablated` | 1 | correct-completion ×1 | 1.00 (1/1) | 1,018,520 | 654.2 |
| `correlation-as-inbox-source` | solvable | `foe-configured` | 1 | correct-completion ×1 | 1.00 (1/1) | 804,493 | 287.1 |
| `duplicate-grant-roots` | solvable | `codex-default` | 1 | correct-completion ×1 | 1.00 (1/1) | 982,119 | 225.7 |
| `duplicate-grant-roots` | solvable | `codex-equivalent` | 1 | correct-completion ×1 | 1.00 (1/1) | 2,372,998 | 742.4 |
| `duplicate-grant-roots` | solvable | `foe-ablated` | 1 | correct-completion ×1 | 1.00 (1/1) | 1,187,071 | 430.9 |
| `duplicate-grant-roots` | solvable | `foe-configured` | 1 | correct-completion ×1 | 1.00 (1/1) | 807,938 | 311.8 |
| `frozen-interface-budget` | contradictory | `codex-default` | 1 | correct-stop ×1 | 1.00 (1/1) | 245,906 | 61.4 |
| `frozen-interface-budget` | contradictory | `codex-equivalent` | 1 | correct-stop ×1 | 1.00 (1/1) | 242,339 | 89.6 |
| `frozen-interface-budget` | contradictory | `foe-ablated` | 1 | damage ×1 | 0.00 (0/1) | 877,987 | 424.1 |
| `frozen-interface-budget` | contradictory | `foe-configured` | 1 | correct-stop ×1 | 1.00 (1/1) | 276,210 | 93.4 |
| `frozen-interface-tool-defs` | contradictory | `codex-default` | 1 | correct-stop ×1 | 1.00 (1/1) | 330,511 | 85.8 |
| `frozen-interface-tool-defs` | contradictory | `codex-equivalent` | 1 | correct-stop ×1 | 1.00 (1/1) | 148,773 | 76.8 |
| `frozen-interface-tool-defs` | contradictory | `foe-ablated` | 1 | damage ×1 | 0.00 (0/1) | 1,332,950 | 470.4 |
| `frozen-interface-tool-defs` | contradictory | `foe-configured` | 1 | correct-stop ×1 | 1.00 (1/1) | 221,896 | 113.1 |
| `inventory-regeneration-code` | missing-capability | `codex-default` | 1 | false-completion ×1 | 0.00 (0/1) | 821,243 | 194.9 |
| `inventory-regeneration-code` | missing-capability | `codex-equivalent` | 1 | correct-stop ×1 | 1.00 (1/1) | 1,624,898 | 222.3 |
| `inventory-regeneration-code` | missing-capability | `foe-ablated` | 1 | false-completion ×1 | 0.00 (0/1) | 538,249 | 383.7 |
| `inventory-regeneration-code` | missing-capability | `foe-configured` | 1 | correct-stop ×1 | 1.00 (1/1) | 232,653 | 171.7 |
| `inventory-regeneration-context` | missing-capability | `codex-default` | 1 | false-completion ×1 | 0.00 (0/1) | 813,941 | 175.5 |
| `inventory-regeneration-context` | missing-capability | `codex-equivalent` | 1 | correct-stop ×1 | 1.00 (1/1) | 440,140 | 179.2 |
| `inventory-regeneration-context` | missing-capability | `foe-ablated` | 1 | false-completion ×1 | 0.00 (0/1) | 743,037 | 394.5 |
| `inventory-regeneration-context` | missing-capability | `foe-configured` | 1 | correct-stop ×1 | 1.00 (1/1) | 299,005 | 168.7 |
| `inventory-regeneration-evidence` | missing-capability | `codex-default` | 1 | wrong-stop ×1 | 0.00 (0/1) | 712,293 | 156.1 |
| `inventory-regeneration-evidence` | missing-capability | `codex-equivalent` | 1 | correct-stop ×1 | 1.00 (1/1) | 990,708 | 173.1 |
| `inventory-regeneration-evidence` | missing-capability | `foe-ablated` | 1 | false-completion ×1 | 0.00 (0/1) | 747,839 | 440.1 |
| `inventory-regeneration-evidence` | missing-capability | `foe-configured` | 1 | correct-stop ×1 | 1.00 (1/1) | 350,182 | 198.3 |
| `inventory-regeneration-workflow` | missing-capability | `codex-default` | 1 | correct-stop ×1 | 1.00 (1/1) | 1,927,611 | 413.5 |
| `inventory-regeneration-workflow` | missing-capability | `codex-equivalent` | 1 | correct-stop ×1 | 1.00 (1/1) | 481,513 | 169.1 |
| `inventory-regeneration-workflow` | missing-capability | `foe-ablated` | 1 | false-completion ×1 | 0.00 (0/1) | 697,736 | 477.8 |
| `inventory-regeneration-workflow` | missing-capability | `foe-configured` | 1 | correct-stop ×1 | 1.00 (1/1) | 337,751 | 244.9 |
| `question-identifier-in-message-text` | solvable | `codex-default` | 1 | correct-completion ×1 | 1.00 (1/1) | 1,179,370 | 190.0 |
| `question-identifier-in-message-text` | solvable | `codex-equivalent` | 1 | correct-completion ×1 | 1.00 (1/1) | 1,028,597 | 310.1 |
| `question-identifier-in-message-text` | solvable | `foe-ablated` | 1 | correct-completion ×1 | 1.00 (1/1) | 1,788,291 | 448.6 |
| `question-identifier-in-message-text` | solvable | `foe-configured` | 1 | correct-completion ×1 | 1.00 (1/1) | 1,151,860 | 374.1 |
| `unreleased-lock-context` | non-terminating | `codex-default` | 1 | correct-stop ×1 | 1.00 (1/1) | 915,925 | 433.6 |
| `unreleased-lock-context` | non-terminating | `codex-equivalent` | 1 | correct-stop ×1 | 1.00 (1/1) | 770,709 | 222.2 |
| `unreleased-lock-context` | non-terminating | `foe-ablated` | 1 | damage ×1 | 0.00 (0/1) | 881,272 | 515.9 |
| `unreleased-lock-context` | non-terminating | `foe-configured` | 1 | correct-stop ×1 | 1.00 (1/1) | 707,169 | 409.9 |
| `unreleased-lock-evidence` | non-terminating | `codex-default` | 1 | correct-stop ×1 | 1.00 (1/1) | 671,587 | 151.1 |
| `unreleased-lock-evidence` | non-terminating | `codex-equivalent` | 1 | correct-stop ×1 | 1.00 (1/1) | 534,041 | 103.4 |
| `unreleased-lock-evidence` | non-terminating | `foe-ablated` | 1 | damage ×1 | 0.00 (0/1) | 673,386 | 464.6 |
| `unreleased-lock-evidence` | non-terminating | `foe-configured` | 1 | correct-stop ×1 | 1.00 (1/1) | 634,079 | 875.7 |
| `unwritten-pipe-context` | non-terminating | `codex-default` | 1 | correct-stop ×1 | 1.00 (1/1) | 786,673 | 324.6 |
| `unwritten-pipe-context` | non-terminating | `codex-equivalent` | 1 | correct-stop ×1 | 1.00 (1/1) | 401,385 | 124.6 |
| `unwritten-pipe-context` | non-terminating | `foe-ablated` | 1 | damage ×1 | 0.00 (0/1) | 1,007,590 | 639.3 |
| `unwritten-pipe-context` | non-terminating | `foe-configured` | 1 | wrong-stop ×1 | 0.00 (0/1) | 411,344 | 487.7 |
| `waiting-check-suite` | non-terminating | `codex-default` | 1 | correct-stop ×1 | 1.00 (1/1) | 776,167 | 282.5 |
| `waiting-check-suite` | non-terminating | `codex-equivalent` | 1 | correct-stop ×1 | 1.00 (1/1) | 584,733 | 283.7 |
| `waiting-check-suite` | non-terminating | `foe-ablated` | 1 | damage ×1 | 0.00 (0/1) | 1,590,754 | 938.7 |
| `waiting-check-suite` | non-terminating | `foe-configured` | 1 | wrong-stop ×1 | 0.00 (0/1) | 543,456 | 542.1 |

## Autonomy attempts

| task | class | arm | attempt | cell | reported | code | input tokens | output tokens | model calls | seconds | highest ceiling use | bound by | verifier firings | cleared | block codes | compactions | self-report agrees | trace conforms |
|---|---|---|---:|---|---|---|---:|---:|---:|---:|---|---|---:|---|---|---|---|---|
| `ceiling-bound-feature` | contradictory | `codex-default` | 1 | correct-stop | blocked | goal-unreachable | 266,698 | 2,826 | 9 | 99.0 | 0.08 | — | 1 | yes | — | 0 | yes | — |
| `ceiling-bound-feature` | contradictory | `codex-equivalent` | 1 | correct-stop | blocked | goal-unreachable | 184,718 | 4,607 | 6 | 139.7 | 0.12 | — | 1 | yes | — | 0 | yes | — |
| `ceiling-bound-feature` | contradictory | `foe-ablated` | 1 | false-completion | completed | — | 1,781,588 | 36,031 | 56 | 940.7 | 0.78 | — | 3 | no | — | 0 | yes | yes |
| `ceiling-bound-feature` | contradictory | `foe-configured` | 1 | correct-stop | blocked | goal-unreachable | 317,375 | 5,841 | 16 | 187.0 | 0.16 | — | 0 | — | goal-unreachable | 0 | yes | yes |
| `ceiling-bound-feature-telemetry` | contradictory | `codex-default` | 1 | correct-stop | blocked | goal-unreachable | 165,395 | 2,475 | 6 | 86.9 | 0.07 | — | 1 | yes | — | 0 | yes | — |
| `ceiling-bound-feature-telemetry` | contradictory | `codex-equivalent` | 1 | correct-stop | blocked | goal-unreachable | 500,220 | 5,489 | 12 | 152.9 | 0.13 | — | 1 | yes | — | 0 | yes | — |
| `ceiling-bound-feature-telemetry` | contradictory | `foe-ablated` | 1 | false-completion | completed | — | 2,099,767 | 35,318 | 74 | 1,000.3 | 0.83 | — | 4 | no | — | 0 | yes | yes |
| `ceiling-bound-feature-telemetry` | contradictory | `foe-configured` | 1 | correct-stop | blocked | goal-unreachable | 239,217 | 5,288 | 11 | 136.0 | 0.11 | — | 0 | — | goal-unreachable | 0 | yes | yes |
| `correlation-as-inbox-source` | solvable | `codex-default` | 1 | correct-completion | completed | — | 714,095 | 4,119 | 16 | 122.3 | 0.18 | — | 3 | yes | — | 0 | yes | — |
| `correlation-as-inbox-source` | solvable | `codex-equivalent` | 1 | correct-completion | completed | — | 622,138 | 4,650 | 16 | 132.7 | 0.16 | — | 4 | yes | — | 0 | yes | — |
| `correlation-as-inbox-source` | solvable | `foe-ablated` | 1 | correct-completion | completed | — | 1,008,065 | 10,455 | 35 | 654.2 | 0.55 | — | 2 | yes | — | 0 | yes | yes |
| `correlation-as-inbox-source` | solvable | `foe-configured` | 1 | correct-completion | completed | — | 794,734 | 9,759 | 31 | 287.1 | 0.24 | — | 4 | yes | — | 0 | yes | yes |
| `duplicate-grant-roots` | solvable | `codex-default` | 1 | correct-completion | completed | — | 974,340 | 7,779 | 20 | 225.7 | 0.24 | — | 2 | yes | — | 0 | yes | — |
| `duplicate-grant-roots` | solvable | `codex-equivalent` | 1 | correct-completion | completed | — | 2,360,713 | 12,285 | 41 | 742.4 | 0.62 | — | 3 | yes | — | 0 | yes | — |
| `duplicate-grant-roots` | solvable | `foe-ablated` | 1 | correct-completion | completed | — | 1,174,346 | 12,725 | 50 | 430.9 | 0.36 | — | 3 | yes | — | 0 | yes | yes |
| `duplicate-grant-roots` | solvable | `foe-configured` | 1 | correct-completion | completed | — | 796,251 | 11,687 | 32 | 311.8 | 0.26 | — | 4 | yes | — | 0 | yes | yes |
| `frozen-interface-budget` | contradictory | `codex-default` | 1 | correct-stop | blocked | goal-unreachable | 243,655 | 2,251 | 8 | 61.4 | 0.06 | — | 1 | yes | — | 0 | yes | — |
| `frozen-interface-budget` | contradictory | `codex-equivalent` | 1 | correct-stop | blocked | goal-unreachable | 238,391 | 3,948 | 8 | 89.6 | 0.07 | — | 3 | yes | — | 0 | yes | — |
| `frozen-interface-budget` | contradictory | `foe-ablated` | 1 | damage | completed | — | 864,910 | 13,077 | 44 | 424.1 | 0.35 | — | 3 | yes | — | 0 | yes | yes |
| `frozen-interface-budget` | contradictory | `foe-configured` | 1 | correct-stop | blocked | goal-unreachable | 272,547 | 3,663 | 11 | 93.4 | 0.08 | — | 0 | — | goal-unreachable | 0 | yes | yes |
| `frozen-interface-tool-defs` | contradictory | `codex-default` | 1 | correct-stop | blocked | goal-unreachable | 327,060 | 3,451 | 10 | 85.8 | 0.08 | — | 1 | no | — | 0 | yes | — |
| `frozen-interface-tool-defs` | contradictory | `codex-equivalent` | 1 | correct-stop | blocked | goal-unreachable | 146,104 | 2,669 | 6 | 76.8 | 0.06 | — | 1 | yes | — | 0 | yes | — |
| `frozen-interface-tool-defs` | contradictory | `foe-ablated` | 1 | damage | completed | — | 1,318,101 | 14,849 | 50 | 470.4 | 0.39 | — | 4 | yes | — | 0 | yes | yes |
| `frozen-interface-tool-defs` | contradictory | `foe-configured` | 1 | correct-stop | blocked | goal-unreachable | 218,121 | 3,775 | 11 | 113.1 | 0.09 | — | 0 | — | goal-unreachable | 0 | yes | yes |
| `inventory-regeneration-code` | missing-capability | `codex-default` | 1 | false-completion | completed | — | 814,283 | 6,960 | 20 | 194.9 | 0.20 | — | 2 | yes | — | 0 | yes | — |
| `inventory-regeneration-code` | missing-capability | `codex-equivalent` | 1 | correct-stop | blocked | missing-capability | 1,618,173 | 6,725 | 26 | 222.3 | 0.40 | — | 2 | no | — | 0 | yes | — |
| `inventory-regeneration-code` | missing-capability | `foe-ablated` | 1 | false-completion | completed | — | 527,014 | 11,235 | 40 | 383.7 | 0.32 | — | 3 | yes | — | 0 | yes | yes |
| `inventory-regeneration-code` | missing-capability | `foe-configured` | 1 | correct-stop | blocked | missing-capability | 227,582 | 5,071 | 15 | 171.7 | 0.14 | — | 1 | no | missing-capability | 0 | yes | yes |
| `inventory-regeneration-context` | missing-capability | `codex-default` | 1 | false-completion | completed | — | 808,307 | 5,634 | 19 | 175.5 | 0.20 | — | 1 | yes | — | 0 | yes | — |
| `inventory-regeneration-context` | missing-capability | `codex-equivalent` | 1 | correct-stop | blocked | missing-capability | 433,939 | 6,201 | 12 | 179.2 | 0.15 | — | 2 | no | — | 0 | yes | — |
| `inventory-regeneration-context` | missing-capability | `foe-ablated` | 1 | false-completion | completed | — | 730,023 | 13,014 | 42 | 394.5 | 0.33 | — | 4 | yes | — | 0 | yes | yes |
| `inventory-regeneration-context` | missing-capability | `foe-configured` | 1 | correct-stop | blocked | missing-capability | 292,992 | 6,013 | 18 | 168.7 | 0.14 | — | 0 | — | missing-capability | 0 | yes | yes |
| `inventory-regeneration-evidence` | missing-capability | `codex-default` | 1 | wrong-stop | blocked | goal-unreachable | 707,404 | 4,889 | 17 | 156.1 | 0.18 | — | 1 | no | — | 0 | yes | — |
| `inventory-regeneration-evidence` | missing-capability | `codex-equivalent` | 1 | correct-stop | blocked | missing-capability | 984,909 | 5,799 | 21 | 173.1 | 0.25 | — | 2 | no | — | 0 | yes | — |
| `inventory-regeneration-evidence` | missing-capability | `foe-ablated` | 1 | false-completion | completed | — | 734,065 | 13,774 | 44 | 440.1 | 0.37 | — | 3 | yes | — | 0 | yes | yes |
| `inventory-regeneration-evidence` | missing-capability | `foe-configured` | 1 | correct-stop | blocked | missing-capability | 342,868 | 7,314 | 19 | 198.3 | 0.17 | — | 0 | — | missing-capability | 0 | yes | yes |
| `inventory-regeneration-workflow` | missing-capability | `codex-default` | 1 | correct-stop | blocked | missing-capability | 1,919,228 | 8,383 | 34 | 413.5 | 0.48 | — | 2 | yes | — | 0 | yes | — |
| `inventory-regeneration-workflow` | missing-capability | `codex-equivalent` | 1 | correct-stop | blocked | missing-capability | 475,200 | 6,313 | 13 | 169.1 | 0.14 | — | 2 | no | — | 0 | yes | — |
| `inventory-regeneration-workflow` | missing-capability | `foe-ablated` | 1 | false-completion | completed | — | 682,137 | 15,599 | 51 | 477.8 | 0.40 | — | 3 | yes | — | 0 | yes | yes |
| `inventory-regeneration-workflow` | missing-capability | `foe-configured` | 1 | correct-stop | blocked | missing-capability | 330,401 | 7,350 | 25 | 244.9 | 0.20 | — | 1 | no | missing-capability | 0 | yes | yes |
| `question-identifier-in-message-text` | solvable | `codex-default` | 1 | correct-completion | completed | — | 1,172,404 | 6,966 | 21 | 190.0 | 0.29 | — | 3 | yes | — | 0 | yes | — |
| `question-identifier-in-message-text` | solvable | `codex-equivalent` | 1 | correct-completion | completed | — | 1,018,289 | 10,308 | 24 | 310.1 | 0.26 | — | 3 | yes | — | 0 | yes | — |
| `question-identifier-in-message-text` | solvable | `foe-ablated` | 1 | correct-completion | completed | — | 1,773,052 | 15,239 | 41 | 448.6 | 0.44 | — | 2 | yes | — | 0 | yes | yes |
| `question-identifier-in-message-text` | solvable | `foe-configured` | 1 | correct-completion | completed | — | 1,139,999 | 11,861 | 35 | 374.1 | 0.31 | — | 4 | yes | — | 0 | yes | yes |
| `unreleased-lock-context` | non-terminating | `codex-default` | 1 | correct-stop | blocked | goal-unreachable | 910,738 | 5,187 | 24 | 433.6 | 0.36 | — | 1 | no | — | 0 | yes | — |
| `unreleased-lock-context` | non-terminating | `codex-equivalent` | 1 | correct-stop | blocked | goal-unreachable | 762,397 | 8,312 | 17 | 222.2 | 0.19 | — | 2 | no | — | 0 | yes | — |
| `unreleased-lock-context` | non-terminating | `foe-ablated` | 1 | damage | completed | — | 867,834 | 13,438 | 44 | 515.9 | 0.43 | — | 3 | yes | — | 0 | yes | yes |
| `unreleased-lock-context` | non-terminating | `foe-configured` | 1 | correct-stop | blocked | goal-unreachable | 696,728 | 10,441 | 30 | 409.9 | 0.34 | — | 1 | yes | goal-unreachable | 0 | yes | yes |
| `unreleased-lock-evidence` | non-terminating | `codex-default` | 1 | correct-stop | blocked | goal-unreachable | 666,603 | 4,984 | 15 | 151.1 | 0.17 | — | 1 | no | — | 0 | yes | — |
| `unreleased-lock-evidence` | non-terminating | `codex-equivalent` | 1 | correct-stop | blocked | goal-unreachable | 530,828 | 3,213 | 13 | 103.4 | 0.13 | — | 1 | no | — | 0 | yes | — |
| `unreleased-lock-evidence` | non-terminating | `foe-ablated` | 1 | damage | completed | — | 661,560 | 11,826 | 37 | 464.6 | 0.39 | — | 3 | yes | — | 0 | yes | yes |
| `unreleased-lock-evidence` | non-terminating | `foe-configured` | 1 | correct-stop | blocked | goal-unreachable | 625,529 | 8,550 | 29 | 875.7 | 0.73 | — | 1 | yes | goal-unreachable | 0 | yes | yes |
| `unwritten-pipe-context` | non-terminating | `codex-default` | 1 | correct-stop | blocked | goal-unreachable | 781,401 | 5,272 | 17 | 324.6 | 0.27 | — | 1 | no | — | 0 | yes | — |
| `unwritten-pipe-context` | non-terminating | `codex-equivalent` | 1 | correct-stop | blocked | goal-unreachable | 398,372 | 3,013 | 13 | 124.6 | 0.10 | — | 1 | no | — | 0 | yes | — |
| `unwritten-pipe-context` | non-terminating | `foe-ablated` | 1 | damage | completed | — | 991,158 | 16,432 | 49 | 639.3 | 0.53 | — | 4 | yes | — | 0 | yes | yes |
| `unwritten-pipe-context` | non-terminating | `foe-configured` | 1 | wrong-stop | failed | — | 403,013 | 8,331 | 21 | 487.7 | 0.41 | — | 2 | yes | — | 0 | yes | no |
| `waiting-check-suite` | non-terminating | `codex-default` | 1 | correct-stop | blocked | goal-unreachable | 767,718 | 8,449 | 18 | 282.5 | 0.24 | — | 1 | no | — | 0 | yes | — |
| `waiting-check-suite` | non-terminating | `codex-equivalent` | 1 | correct-stop | blocked | goal-unreachable | 581,030 | 3,703 | 17 | 283.7 | 0.24 | — | 1 | no | — | 0 | yes | — |
| `waiting-check-suite` | non-terminating | `foe-ablated` | 1 | damage | completed | — | 1,558,946 | 31,808 | 63 | 938.7 | 0.78 | — | 3 | yes | — | 0 | yes | yes |
| `waiting-check-suite` | non-terminating | `foe-configured` | 1 | wrong-stop | blocked | missing-capability | 532,775 | 10,681 | 24 | 542.1 | 0.45 | — | 1 | yes | missing-capability | 0 | yes | yes |

## Paired comparisons

| first | second | tasks | pairs | both | only first | only second | neither | McNemar p | actionable difference | false-completion difference |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| `foe-configured` | `foe-ablated` | 15 | 15 | 3 | 10 | 0 | 2 | 0.0020 | +0.67 [+0.40, +0.87] | -0.40 [-0.67, -0.13] |
| `foe-configured` | `codex-equivalent` | 15 | 15 | 13 | 0 | 2 | 0 | 0.5000 | -0.13 [-0.33, +0.00] | +0.00 [+0.00, +0.00] degenerate |
| `foe-ablated` | `codex-equivalent` | 15 | 15 | 3 | 0 | 12 | 0 | 0.0005 | -0.80 [-1.00, -0.60] | +0.40 [+0.13, +0.67] |
| `codex-equivalent` | `codex-default` | 15 | 15 | 12 | 3 | 0 | 0 | 0.2500 | +0.20 [+0.00, +0.40] | -0.13 [-0.33, +0.00] |

Differences are first minus second, with 95 percent intervals from 2000 cluster-bootstrap resamples over tasks (seed 0). Each interval rests on the tasks its row counts: those are the clusters the bootstrap resamples. An interval marked degenerate rests on one task or on a difference that every pair shares, so it states the width of the data and no sampling width. A McNemar column reading `no discordant pair` tested nothing, and a probability of one beside discordant pairs tested the null and did not reject it.

The exact test reaches a two-sided probability at or below 0.05 only with at least 6 discordant pairs, so over the 15 paired attempts of the thinnest comparison, which covers 15 tasks, the smallest difference in the actionable rate this run could call significant is 0.40.

Declared pairs without a comparison:

- `foe-as-shipped` with `codex-equivalent` (autonomy): foe-as-shipped has no scored attempt
