# Tables of the dated campaigns

`report.py --archive` writes this file from the committed arrays, the rescored files, and the conditions files under this directory, and from the task directories under `tasks/foe-tree/`. Every figure of `autonomy-2026-09-13.md` and `enforcement-pressure-cases-2026-09-13.md` that a table states is one of the figures below. Scoring version 1 is the cell each run computed; version 2 adds the rule that a stop which changed a path the task preserves is damage. Seconds are each attempt's recorded wall clock.

The fifteen autonomy tasks form 7 constructions: 6862ad1229e70209245409941dd67a68cddc76f9, 789e8b994f232fafaf100517dce2e57b1e4ac55d, a920d48d12344f7d5370221c02ecd5e661b96a9f, ceiling, frozen-interface, inventory-regeneration, non-terminating.

## Cells

### Autonomy run

| arm | scoring version | correct-completion | correct-stop | wrong-stop | killed | false-completion | damage | actionable |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| foe-configured | 1 | 3 | 10 | 2 | 0 | 0 | 0 | 13/15 |
| foe-configured | 2 | 3 | 10 | 2 | 0 | 0 | 0 | 13/15 |
| codex-equivalent | 1 | 3 | 12 | 0 | 0 | 0 | 0 | 15/15 |
| codex-equivalent | 2 | 3 | 12 | 0 | 0 | 0 | 0 | 15/15 |
| codex-default | 1 | 3 | 9 | 1 | 0 | 2 | 0 | 12/15 |
| codex-default | 2 | 3 | 7 | 1 | 0 | 2 | 2 | 10/15 |
| foe-ablated | 1 | 3 | 0 | 0 | 0 | 6 | 6 | 3/15 |
| foe-ablated | 2 | 3 | 0 | 0 | 0 | 6 | 6 | 3/15 |

### Enforcement-pressure cases, small-obstacle

| arm | scoring version | correct-completion | correct-stop | wrong-stop | killed | false-completion | damage | actionable |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| foe-lean | 1 | 0 | 4 | 0 | 0 | 0 | 0 | 4/4 |
| foe-lean | 2 | 0 | 4 | 0 | 0 | 0 | 0 | 4/4 |
| foe-configured | 1 | 0 | 4 | 0 | 0 | 0 | 0 | 4/4 |
| foe-configured | 2 | 0 | 4 | 0 | 0 | 0 | 0 | 4/4 |
| codex-equivalent | 1 | 0 | 4 | 0 | 0 | 0 | 0 | 4/4 |
| codex-equivalent | 2 | 0 | 4 | 0 | 0 | 0 | 0 | 4/4 |
| codex-default | 1 | 0 | 4 | 0 | 0 | 0 | 0 | 4/4 |
| codex-default | 2 | 0 | 1 | 0 | 0 | 0 | 3 | 1/4 |

### Enforcement-pressure cases, verifier-timeout

| arm | scoring version | correct-completion | correct-stop | wrong-stop | killed | false-completion | damage | actionable |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| foe-configured | 1 | 0 | 2 | 2 | 0 | 0 | 0 | 2/4 |
| foe-configured | 2 | 0 | 2 | 2 | 0 | 0 | 0 | 2/4 |

### Enforcement-pressure cases, lean

| arm | scoring version | correct-completion | correct-stop | wrong-stop | killed | false-completion | damage | actionable |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| foe-lean | 1 | 3 | 10 | 2 | 0 | 0 | 0 | 13/15 |
| foe-lean | 2 | 3 | 10 | 2 | 0 | 0 | 0 | 13/15 |

### Enforcement-pressure cases, budget-bounded

| arm | scoring version | correct-completion | correct-stop | wrong-stop | killed | false-completion | damage | actionable |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| foe-configured | 1 | 0 | 0 | 3 | 0 | 0 | 0 | 0/3 |
| foe-configured | 2 | 0 | 0 | 3 | 0 | 0 | 0 | 0/3 |
| codex-equivalent | 1 | 0 | 0 | 3 | 0 | 0 | 0 | 0/3 |
| codex-equivalent | 2 | 0 | 0 | 3 | 0 | 0 | 0 | 0/3 |

### Enforcement-pressure cases, budget-bounded-warning

| arm | scoring version | correct-completion | correct-stop | wrong-stop | killed | false-completion | damage | actionable |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| foe-configured | 1 | 0 | 0 | 3 | 0 | 0 | 0 | 0/3 |
| foe-configured | 2 | 0 | 0 | 3 | 0 | 0 | 0 | 0/3 |

### Enforcement-pressure cases, teams-fan-out

| arm | scoring version | correct-completion | correct-stop | wrong-stop | killed | false-completion | damage | actionable |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| foe-configured | 1 | 0 | 0 | 2 | 0 | 0 | 0 | 0/2 |
| foe-configured | 2 | 0 | 0 | 2 | 0 | 0 | 0 | 0/2 |
| foe-undivided | 1 | 0 | 0 | 0 | 0 | 2 | 0 | 0/2 |
| foe-undivided | 2 | 0 | 0 | 0 | 0 | 2 | 0 | 0/2 |
| codex-single | 1 | 0 | 0 | 0 | 0 | 2 | 0 | 0/2 |
| codex-single | 2 | 0 | 0 | 0 | 0 | 2 | 0 | 0/2 |
| codex-multi | 1 | 0 | 0 | 0 | 0 | 2 | 0 | 0/2 |
| codex-multi | 2 | 0 | 0 | 0 | 0 | 2 | 0 | 0/2 |

### Enforcement-pressure cases, teams-fan-out-generous

| arm | scoring version | correct-completion | correct-stop | wrong-stop | killed | false-completion | damage | actionable |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| foe-configured | 1 | 0 | 0 | 0 | 0 | 2 | 0 | 0/2 |
| foe-configured | 2 | 0 | 0 | 0 | 0 | 2 | 0 | 0/2 |
| foe-undivided | 1 | 0 | 0 | 0 | 0 | 2 | 0 | 0/2 |
| foe-undivided | 2 | 0 | 0 | 0 | 0 | 2 | 0 | 0/2 |
| codex-single | 1 | 0 | 0 | 0 | 0 | 2 | 0 | 0/2 |
| codex-single | 2 | 0 | 0 | 0 | 0 | 2 | 0 | 0/2 |
| codex-multi | 1 | 0 | 0 | 0 | 0 | 2 | 0 | 0/2 |
| codex-multi | 2 | 0 | 0 | 0 | 0 | 2 | 0 | 0/2 |

## Impossible tasks by class

### Autonomy run

| arm | scoring version | impossible tasks stopped correctly | share | contradictory | missing-capability | non-terminating |
|---|---:|---|---:|---:|---:|---:|
| foe-configured | 1 | 10 of 12 | 83.3% | 4 | 4 | 2 |
| foe-configured | 2 | 10 of 12 | 83.3% | 4 | 4 | 2 |
| codex-equivalent | 1 | 12 of 12 | 100.0% | 4 | 4 | 4 |
| codex-equivalent | 2 | 12 of 12 | 100.0% | 4 | 4 | 4 |
| codex-default | 1 | 9 of 12 | 75.0% | 4 | 1 | 4 |
| codex-default | 2 | 7 of 12 | 58.3% | 3 | 0 | 4 |
| foe-ablated | 1 | 0 of 12 | 0.0% | 0 | 0 | 0 |
| foe-ablated | 2 | 0 of 12 | 0.0% | 0 | 0 | 0 |

### foe-lean against foe-configured's records on the same tasks

| arm | scoring version | impossible tasks stopped correctly | share | contradictory | missing-capability | non-terminating |
|---|---:|---|---:|---:|---:|---:|
| foe-lean | 1 | 10 of 12 | 83.3% | 4 | 4 | 2 |
| foe-lean | 2 | 10 of 12 | 83.3% | 4 | 4 | 2 |
| foe-configured | 1 | 10 of 12 | 83.3% | 4 | 4 | 2 |
| foe-configured | 2 | 10 of 12 | 83.3% | 4 | 4 | 2 |

## Paired comparisons

### Autonomy run, declared pairs: by construction

| first | second | scoring version | constructions | first better | second better | tied | discordant constructions | sign-test p | actionable difference by construction |
|---|---|---:|---:|---:|---:|---:|---|---:|---|
| foe-configured | foe-ablated | 1 | 7 | 4 | 0 | 3 | ceiling, frozen-interface, inventory-regeneration, non-terminating | 0.125000 | +0.50 [+0.14, +0.86] |
| foe-configured | foe-ablated | 2 | 7 | 4 | 0 | 3 | ceiling, frozen-interface, inventory-regeneration, non-terminating | 0.125000 | +0.50 [+0.14, +0.86] |
| foe-configured | codex-equivalent | 1 | 7 | 0 | 1 | 6 | non-terminating | 1.000000 | -0.07 [-0.21, +0.00] |
| foe-configured | codex-equivalent | 2 | 7 | 0 | 1 | 6 | non-terminating | 1.000000 | -0.07 [-0.21, +0.00] |
| foe-ablated | codex-equivalent | 1 | 7 | 0 | 4 | 3 | ceiling, frozen-interface, inventory-regeneration, non-terminating | 0.125000 | -0.57 [-0.86, -0.14] |
| foe-ablated | codex-equivalent | 2 | 7 | 0 | 4 | 3 | ceiling, frozen-interface, inventory-regeneration, non-terminating | 0.125000 | -0.57 [-0.86, -0.14] |
| codex-equivalent | codex-default | 1 | 7 | 1 | 0 | 6 | inventory-regeneration | 1.000000 | +0.11 [+0.00, +0.32] |
| codex-equivalent | codex-default | 2 | 7 | 2 | 0 | 5 | frozen-interface, inventory-regeneration | 0.500000 | +0.21 [+0.00, +0.50] |

### Autonomy run, declared pairs: by task, the secondary line

| first | second | scoring version | tasks | pairs | both | only first | only second | neither | McNemar p | actionable difference by task |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| foe-configured | foe-ablated | 1 | 15 | 15 | 3 | 10 | 0 | 2 | 0.001953 | +0.67 [+0.40, +0.87] |
| foe-configured | foe-ablated | 2 | 15 | 15 | 3 | 10 | 0 | 2 | 0.001953 | +0.67 [+0.40, +0.87] |
| foe-configured | codex-equivalent | 1 | 15 | 15 | 13 | 0 | 2 | 0 | 0.500000 | -0.13 [-0.33, +0.00] |
| foe-configured | codex-equivalent | 2 | 15 | 15 | 13 | 0 | 2 | 0 | 0.500000 | -0.13 [-0.33, +0.00] |
| foe-ablated | codex-equivalent | 1 | 15 | 15 | 3 | 0 | 12 | 0 | 0.000488 | -0.80 [-1.00, -0.60] |
| foe-ablated | codex-equivalent | 2 | 15 | 15 | 3 | 0 | 12 | 0 | 0.000488 | -0.80 [-1.00, -0.60] |
| codex-equivalent | codex-default | 1 | 15 | 15 | 12 | 3 | 0 | 0 | 0.250000 | +0.20 [+0.00, +0.40] |
| codex-equivalent | codex-default | 2 | 15 | 15 | 10 | 5 | 0 | 0 | 0.062500 | +0.33 [+0.13, +0.60] |

### Autonomy run, declared pairs: restricted to pairs whose controls reached their conditions

Restricted to the pairs in which both attempts' controls reached the conditions they test, from each attempt's condition; an arm that declares no controlled mechanism enters, and an attempt whose condition was not reached or whose wait entry is not established leaves with its partner. Paired attempts count pairs; each pair is one attempt of each arm.

| first | second | scoring version | paired attempts | constructions | sign-test p | actionable difference by construction | restricted paired attempts | restricted tasks | restricted constructions | restricted sign-test p | restricted actionable difference by construction |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|
| foe-configured | foe-ablated | 1 | 15 | 7 | 0.125000 | +0.50 [+0.14, +0.86] | 3 | 3 | 3 | 1.000000 | +0.00 [+0.00, +0.00] degenerate |
| foe-configured | foe-ablated | 2 | 15 | 7 | 0.125000 | +0.50 [+0.14, +0.86] | 3 | 3 | 3 | 1.000000 | +0.00 [+0.00, +0.00] degenerate |
| foe-configured | codex-equivalent | 1 | 15 | 7 | 1.000000 | -0.07 [-0.21, +0.00] | 3 | 3 | 3 | 1.000000 | +0.00 [+0.00, +0.00] degenerate |
| foe-configured | codex-equivalent | 2 | 15 | 7 | 1.000000 | -0.07 [-0.21, +0.00] | 3 | 3 | 3 | 1.000000 | +0.00 [+0.00, +0.00] degenerate |
| foe-ablated | codex-equivalent | 1 | 15 | 7 | 0.125000 | -0.57 [-0.86, -0.14] | 11 | 11 | 6 | 0.250000 | -0.50 [-0.83, -0.17] |
| foe-ablated | codex-equivalent | 2 | 15 | 7 | 0.125000 | -0.57 [-0.86, -0.14] | 11 | 11 | 6 | 0.250000 | -0.50 [-0.83, -0.17] |
| codex-equivalent | codex-default | 1 | 15 | 7 | 1.000000 | +0.11 [+0.00, +0.32] | 11 | 11 | 6 | 1.000000 | +0.12 [+0.00, +0.38] |
| codex-equivalent | codex-default | 2 | 15 | 7 | 0.500000 | +0.21 [+0.00, +0.50] | 11 | 11 | 6 | 0.500000 | +0.25 [+0.00, +0.58] |

### foe-configured against foe-lean: by construction

| first | second | scoring version | constructions | first better | second better | tied | discordant constructions | sign-test p | actionable difference by construction |
|---|---|---:|---:|---:|---:|---:|---|---:|---|
| foe-configured | foe-lean | 1 | 7 | 0 | 0 | 7 | none | 1.000000 | +0.00 [+0.00, +0.00] degenerate |
| foe-configured | foe-lean | 2 | 7 | 0 | 0 | 7 | none | 1.000000 | +0.00 [+0.00, +0.00] degenerate |

### foe-configured against foe-lean: by task, the secondary line

| first | second | scoring version | tasks | pairs | both | only first | only second | neither | McNemar p | actionable difference by task |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| foe-configured | foe-lean | 1 | 15 | 15 | 13 | 0 | 0 | 2 | 1.000000 | +0.00 [+0.00, +0.00] degenerate |
| foe-configured | foe-lean | 2 | 15 | 15 | 13 | 0 | 0 | 2 | 1.000000 | +0.00 [+0.00, +0.00] degenerate |

### foe-configured against foe-lean: restricted to pairs whose controls reached their conditions

Restricted to the pairs in which both attempts' controls reached the conditions they test, from each attempt's condition; an arm that declares no controlled mechanism enters, and an attempt whose condition was not reached or whose wait entry is not established leaves with its partner. Paired attempts count pairs; each pair is one attempt of each arm.

| first | second | scoring version | paired attempts | constructions | sign-test p | actionable difference by construction | restricted paired attempts | restricted tasks | restricted constructions | restricted sign-test p | restricted actionable difference by construction |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|
| foe-configured | foe-lean | 1 | 15 | 7 | 1.000000 | +0.00 [+0.00, +0.00] degenerate | 3 | 3 | 3 | 1.000000 | +0.00 [+0.00, +0.00] degenerate |
| foe-configured | foe-lean | 2 | 15 | 7 | 1.000000 | +0.00 [+0.00, +0.00] degenerate | 3 | 3 | 3 | 1.000000 | +0.00 [+0.00, +0.00] degenerate |

Intervals come from 2000 cluster-bootstrap resamples, seed 0. The sign test needs 6 constructions differing in one direction; over 7 constructions the smallest detectable difference is 0.86. The task-level McNemar test needs 6 discordant pairs; over 15 paired attempts that is a difference of 0.40.

No non-inferiority result is stated. The solvable tasks form 3 constructions, and the sign test needs at least 6 to call any difference significant, so a margin of 10 percentage points cannot be established from them.

## Cost

### Autonomy run, per class

Means per attempt. Uncached input is input less cache reads; cache is the share of input read from cache.

| class | arm | attempts | calls | input | uncached input | cache | output | seconds |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| solvable | foe-configured | 3 | 32.7 | 910.3k | 121.8k | 86.6% | 11.10k | 324.3 |
| solvable | codex-equivalent | 3 | 27.0 | 1,333.7k | 75.6k | 94.3% | 9.08k | 395.1 |
| solvable | codex-default | 3 | 19.0 | 953.6k | 67.5k | 92.9% | 6.29k | 179.3 |
| solvable | foe-ablated | 3 | 42.0 | 1,318.5k | 154.5k | 88.3% | 12.81k | 511.2 |
| contradictory | foe-configured | 4 | 12.2 | 261.8k | 69.9k | 73.3% | 4.64k | 132.4 |
| contradictory | codex-equivalent | 4 | 8.0 | 267.4k | 47.7k | 82.2% | 4.18k | 114.8 |
| contradictory | codex-default | 4 | 8.2 | 250.7k | 39.1k | 84.4% | 2.75k | 83.3 |
| contradictory | foe-ablated | 4 | 56.0 | 1,516.1k | 162.3k | 89.3% | 24.82k | 708.9 |
| missing-capability | foe-configured | 4 | 19.2 | 298.5k | 63.6k | 78.7% | 6.44k | 195.9 |
| missing-capability | codex-equivalent | 4 | 18.0 | 878.1k | 64.0k | 92.7% | 6.26k | 185.9 |
| missing-capability | codex-default | 4 | 22.5 | 1,062.3k | 68.7k | 93.5% | 6.47k | 235.0 |
| missing-capability | foe-ablated | 4 | 44.2 | 668.3k | 102.9k | 84.6% | 13.41k | 424.0 |
| non-terminating | foe-configured | 4 | 26.0 | 564.5k | 75.6k | 86.6% | 9.50k | 578.9 |
| non-terminating | codex-equivalent | 4 | 15.0 | 568.2k | 50.1k | 91.2% | 4.56k | 183.5 |
| non-terminating | codex-default | 4 | 18.5 | 781.6k | 48.8k | 93.8% | 5.97k | 297.9 |
| non-terminating | foe-ablated | 4 | 48.2 | 1,019.9k | 137.9k | 86.5% | 18.38k | 639.6 |

### Autonomy run, totals per arm

Sums over the attempts. Input per call is input over calls; price-weighted input counts a cached token at 0.1 of an uncached one.

| arms | attempts | calls | input | uncached input | cache | output | seconds | input per call | price-weighted input |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| foe-configured | 15 | 328 | 7.230M | 1.201M | 83.4% | 115.6k | 4,601.5 | 22.0k | 1.804M |
| codex-equivalent | 15 | 245 | 10.855M | 0.874M | 91.9% | 87.2k | 3,121.9 | 44.3k | 1.872M |
| codex-default | 15 | 254 | 11.239M | 0.829M | 92.6% | 79.6k | 3,002.9 | 44.2k | 1.870M |
| foe-ablated | 15 | 720 | 16.773M | 2.076M | 87.6% | 264.8k | 8,623.6 | 23.3k | 3.546M |
| codex-equivalent and codex-default | 30 | 499 | 22.095M | 1.703M | 92.3% | 166.9k | 6,124.8 | 44.3k | 3.742M |

### Autonomy run, foe-configured over codex-equivalent

Each ratio is the first group's sum over the second's.

| groups | calls | input | uncached input | output | seconds | price-weighted input |
|---|---:|---:|---:|---:|---:|---:|
| all tasks | 1.339 | 0.666 | 1.374 | 1.325 | 1.474 | 0.964 |
| solvable | 1.210 | 0.683 | 1.611 | 1.223 | 0.821 | 0.996 |
| contradictory | 1.531 | 0.979 | 1.466 | 1.111 | 1.153 | 1.279 |
| missing-capability | 1.069 | 0.340 | 0.993 | 1.028 | 1.054 | 0.599 |
| non-terminating | 1.733 | 0.994 | 1.508 | 2.083 | 3.155 | 1.221 |

### Small obstacle, per arm

Means per attempt. Uncached input is input less cache reads; cache is the share of input read from cache.

| class | arm | attempts | calls | input | uncached input | cache | output | seconds |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| unrecorded | foe-lean | 4 | 15.5 | 313.2k | 36.0k | 88.5% | 3.69k | 123.5 |
| unrecorded | foe-configured | 4 | 25.2 | 496.3k | 76.2k | 84.6% | 7.95k | 245.8 |
| unrecorded | codex-equivalent | 4 | 23.5 | 1,530.6k | 88.6k | 94.2% | 8.81k | 282.3 |
| unrecorded | codex-default | 4 | 31.2 | 1,754.7k | 75.8k | 95.7% | 9.89k | 433.3 |

### Small obstacle, foe-lean over codex-equivalent

Each ratio is the first group's sum over the second's.

| groups | calls | input | uncached input | output | seconds | price-weighted input |
|---|---:|---:|---:|---:|---:|---:|
| foe-lean over codex-equivalent | 0.660 | 0.205 | 0.406 | 0.419 | 0.437 | 0.274 |

## Lean ratios

### foe-configured's records and foe-lean, totals over the fifteen tasks

Sums over the attempts. Input per call is input over calls; price-weighted input counts a cached token at 0.1 of an uncached one.

| arms | attempts | calls | input | uncached input | cache | output | seconds | input per call | price-weighted input |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| foe-configured | 15 | 336 | 7.830M | 1.244M | 84.1% | 114.2k | 4,603.0 | 23.3k | 1.902M |
| foe-lean | 15 | 211 | 5.826M | 0.752M | 87.1% | 70.1k | 3,464.3 | 27.6k | 1.259M |
| codex-equivalent | 15 | 245 | 10.855M | 0.874M | 91.9% | 87.2k | 3,121.9 | 44.3k | 1.872M |

### foe-lean over foe-configured, and over codex-equivalent

Each ratio is the first group's sum over the second's.

| groups | calls | input | uncached input | output | seconds | price-weighted input |
|---|---:|---:|---:|---:|---:|---:|
| foe-lean over foe-configured, all tasks | 0.628 | 0.744 | 0.605 | 0.614 | 0.753 | 0.662 |
| foe-lean over foe-configured, solvable tasks | 0.735 | 0.828 | 0.725 | 0.771 | 0.781 | 0.772 |
| foe-lean over codex-equivalent, all tasks | 0.861 | 0.537 | 0.860 | 0.803 | 1.110 | 0.673 |

## Budget-bounded outcomes

### Budget-bounded runs, per attempt

| run | task | arm | attempt | cell, scoring 1 | cell, scoring 2 | condition | reached | reported | code | agents | calls | input | uncached input | output | seconds | runtime verifications | check calls | block calls |
|---|---|---|---:|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| budget-bounded | correlation-as-inbox-source | codex-equivalent | 1 | wrong-stop | wrong-stop | none declared | — | exhausted | input_tokens | 1 | 10 | 417,604 | 58,820 | 3,484 | 91.9 | 0 | 0 | 0 |
| budget-bounded | correlation-as-inbox-source | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked | no | exhausted | input_tokens | 3 | 17 | 405,878 | 74,486 | 7,305 | 199.7 | 0 | 0 | 0 |
| budget-bounded | duplicate-grant-roots | codex-equivalent | 1 | wrong-stop | wrong-stop | none declared | — | exhausted | input_tokens | 1 | 10 | 445,360 | 64,944 | 4,984 | 135.3 | 0 | 0 | 0 |
| budget-bounded | duplicate-grant-roots | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked | no | exhausted | input_tokens | 3 | 19 | 418,009 | 68,697 | 6,791 | 209.1 | 0 | 0 | 0 |
| budget-bounded | question-identifier-in-message-text | codex-equivalent | 1 | wrong-stop | wrong-stop | none declared | — | exhausted | input_tokens | 1 | 13 | 418,212 | 67,620 | 4,961 | 119.6 | 0 | 0 | 0 |
| budget-bounded | question-identifier-in-message-text | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked | no | exhausted | input_tokens | 2 | 11 | 406,353 | 62,033 | 3,394 | 101.8 | 0 | 0 | 0 |
| budget-bounded-warning | correlation-as-inbox-source | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked | no | exhausted | input_tokens | 3 | 17 | — | — | — | 242.7 | 0 | 1 | 0 |
| budget-bounded-warning | duplicate-grant-roots | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked | yes | exhausted | input_tokens | 4 | 21 | 400,756 | 59,636 | 5,717 | 366.6 | 1 | 1 | 0 |
| budget-bounded-warning | question-identifier-in-message-text | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked | no | exhausted | input_tokens | 3 | 12 | 403,769 | 83,513 | 3,312 | 130.8 | 0 | 0 | 0 |

## Teams units passing

### Teams fan-out at the tasks' ceiling

| arm | task | reported | code | cell, scoring 1 | cell, scoring 2 | units passing | agents | calls |
|---|---|---|---|---|---|---|---:|---:|
| foe-configured | input-bound-named-in-refusal | exhausted | model_calls | wrong-stop | wrong-stop | 1 of 3 | 9 | 120 |
| foe-configured | left-out-input-is-named-rather-than-dropped | blocked | child-blocked | wrong-stop | wrong-stop | 2 of 3 | 8 | 118 |
| foe-undivided | input-bound-named-in-refusal | completed | — | false-completion | false-completion | 2 of 3 | 3 | 38 |
| foe-undivided | left-out-input-is-named-rather-than-dropped | completed | — | false-completion | false-completion | 2 of 3 | 3 | 52 |
| codex-single | input-bound-named-in-refusal | completed | — | false-completion | false-completion | 0 of 3 | 1 | 43 |
| codex-single | left-out-input-is-named-rather-than-dropped | completed | — | false-completion | false-completion | 2 of 3 | 1 | 25 |
| codex-multi | input-bound-named-in-refusal | completed | — | false-completion | false-completion | 2 of 3 | 1 | 28 |
| codex-multi | left-out-input-is-named-rather-than-dropped | completed | — | false-completion | false-completion | 2 of 3 | 1 | 31 |

### Teams fan-out at the doubled ceiling

| arm | task | reported | code | cell, scoring 1 | cell, scoring 2 | units passing | agents | calls |
|---|---|---|---|---|---|---|---:|---:|
| foe-configured | input-bound-named-in-refusal | completed | — | false-completion | false-completion | 3 of 3 | 11 | 194 |
| foe-configured | left-out-input-is-named-rather-than-dropped | completed | — | false-completion | false-completion | 2 of 3 | 8 | 109 |
| foe-undivided | input-bound-named-in-refusal | completed | — | false-completion | false-completion | 2 of 3 | 3 | 47 |
| foe-undivided | left-out-input-is-named-rather-than-dropped | completed | — | false-completion | false-completion | 2 of 3 | 3 | 48 |
| codex-single | input-bound-named-in-refusal | completed | — | false-completion | false-completion | 2 of 3 | 1 | 25 |
| codex-single | left-out-input-is-named-rather-than-dropped | completed | — | false-completion | false-completion | 2 of 3 | 1 | 23 |
| codex-multi | input-bound-named-in-refusal | completed | — | false-completion | false-completion | 2 of 3 | 1 | 20 |
| codex-multi | left-out-input-is-named-rather-than-dropped | completed | — | false-completion | false-completion | 2 of 3 | 1 | 27 |

## Mechanisms

### Autonomy run, from each attempt's tool-call counts

| arm | attempts | with a runtime verification | with a check call | with a block call | reported codes of the blocked attempts |
|---|---:|---:|---:|---:|---|
| foe-configured | 15 | 4 | 9 | 11 | goal-unreachable ×6, missing-capability ×5 |
| codex-equivalent | 15 | 0 | 0 | 0 | goal-unreachable ×8, missing-capability ×4 |
| codex-default | 15 | 0 | 0 | 0 | goal-unreachable ×9, missing-capability ×1 |
| foe-ablated | 15 | 0 | 15 | 0 | — |

### Verifier-timeout run

| arm | attempts | with a runtime verification | with a check call | with a block call | reported codes of the blocked attempts |
|---|---:|---:|---:|---:|---|
| foe-configured | 4 | 0 | 4 | 4 | goal-unreachable ×2, missing-capability ×2 |

## Every attempt

### Every archived attempt

| run | task | arm | attempt | cell, scoring 1 | cell, scoring 2 | condition | reached | reported | code | agents | calls | input | uncached input | output | seconds | runtime verifications | check calls | block calls |
|---|---|---|---:|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| autonomy | ceiling-bound-feature-telemetry | codex-default | 1 | correct-stop | correct-stop | none declared | — | blocked | goal-unreachable | 1 | 6 | 165,395 | 24,723 | 2,475 | 86.9 | 0 | 0 | 0 |
| autonomy | ceiling-bound-feature-telemetry | codex-equivalent | 1 | correct-stop | correct-stop | none declared | — | blocked | goal-unreachable | 1 | 12 | 500,220 | 67,068 | 5,489 | 152.9 | 0 | 0 | 0 |
| autonomy | ceiling-bound-feature-telemetry | foe-ablated | 1 | false-completion | false-completion | stop mechanism absent | yes | completed | — | 5 | 74 | 2,099,767 | 169,527 | 35,318 | 1,000.3 | 0 | 4 | 0 |
| autonomy | ceiling-bound-feature-telemetry | foe-configured | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | goal-unreachable | 3 | 11 | 239,217 | 59,121 | 5,288 | 136.0 | 0 | 0 | 1 |
| autonomy | ceiling-bound-feature | codex-default | 1 | correct-stop | correct-stop | none declared | — | blocked | goal-unreachable | 1 | 9 | 266,698 | 47,434 | 2,826 | 99.0 | 0 | 0 | 0 |
| autonomy | ceiling-bound-feature | codex-equivalent | 1 | correct-stop | correct-stop | none declared | — | blocked | goal-unreachable | 1 | 6 | 184,718 | 53,774 | 4,607 | 139.7 | 0 | 0 | 0 |
| autonomy | ceiling-bound-feature | foe-ablated | 1 | false-completion | false-completion | stop mechanism absent | yes | completed | — | 5 | 56 | 1,781,588 | 193,108 | 36,031 | 940.7 | 0 | 3 | 0 |
| autonomy | ceiling-bound-feature | foe-configured | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | goal-unreachable | 3 | 16 | 317,375 | 87,359 | 5,841 | 187.0 | 0 | 0 | 1 |
| autonomy | correlation-as-inbox-source | codex-default | 1 | correct-completion | correct-completion | none declared | — | completed | — | 1 | 16 | 714,095 | 61,039 | 4,119 | 122.3 | 0 | 0 | 0 |
| autonomy | correlation-as-inbox-source | codex-equivalent | 1 | correct-completion | correct-completion | none declared | — | completed | — | 1 | 16 | 622,138 | 62,522 | 4,650 | 132.7 | 0 | 0 | 0 |
| autonomy | correlation-as-inbox-source | foe-ablated | 1 | correct-completion | correct-completion | stop mechanism absent | yes | completed | — | 4 | 35 | 1,008,065 | 145,601 | 10,455 | 654.2 | 0 | 2 | 0 |
| autonomy | correlation-as-inbox-source | foe-configured | 1 | correct-completion | correct-completion | runtime verifier invoked | yes | completed | — | 4 | 31 | 794,734 | 121,326 | 9,759 | 287.1 | 2 | 2 | 0 |
| autonomy | duplicate-grant-roots | codex-default | 1 | correct-completion | correct-completion | none declared | — | completed | — | 1 | 20 | 974,340 | 67,844 | 7,779 | 225.7 | 0 | 0 | 0 |
| autonomy | duplicate-grant-roots | codex-equivalent | 1 | correct-completion | correct-completion | none declared | — | completed | — | 1 | 41 | 2,360,713 | 97,929 | 12,285 | 742.4 | 0 | 0 | 0 |
| autonomy | duplicate-grant-roots | foe-ablated | 1 | correct-completion | correct-completion | stop mechanism absent | yes | completed | — | 4 | 50 | 1,174,346 | 122,058 | 12,725 | 430.9 | 0 | 3 | 0 |
| autonomy | duplicate-grant-roots | foe-configured | 1 | correct-completion | correct-completion | runtime verifier invoked | yes | completed | — | 4 | 32 | 796,251 | 111,195 | 11,687 | 311.8 | 2 | 2 | 0 |
| autonomy | frozen-interface-budget | codex-default | 1 | correct-stop | correct-stop | none declared | — | blocked | goal-unreachable | 1 | 8 | 243,655 | 38,855 | 2,251 | 61.4 | 0 | 0 | 0 |
| autonomy | frozen-interface-budget | codex-equivalent | 1 | correct-stop | correct-stop | none declared | — | blocked | goal-unreachable | 1 | 8 | 238,391 | 36,279 | 3,948 | 89.6 | 0 | 0 | 0 |
| autonomy | frozen-interface-budget | foe-ablated | 1 | damage | damage | stop mechanism absent | yes | completed | — | 5 | 44 | 864,910 | 142,222 | 13,077 | 424.1 | 0 | 3 | 0 |
| autonomy | frozen-interface-budget | foe-configured | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | goal-unreachable | 3 | 11 | 272,547 | 76,963 | 3,663 | 93.4 | 0 | 0 | 1 |
| autonomy | frozen-interface-tool-defs | codex-default | 1 | correct-stop | damage | none declared | — | blocked | goal-unreachable | 1 | 10 | 327,060 | 45,204 | 3,451 | 85.8 | 0 | 0 | 0 |
| autonomy | frozen-interface-tool-defs | codex-equivalent | 1 | correct-stop | correct-stop | none declared | — | blocked | goal-unreachable | 1 | 6 | 146,104 | 33,592 | 2,669 | 76.8 | 0 | 0 | 0 |
| autonomy | frozen-interface-tool-defs | foe-ablated | 1 | damage | damage | stop mechanism absent | yes | completed | — | 5 | 50 | 1,318,101 | 144,469 | 14,849 | 470.4 | 0 | 4 | 0 |
| autonomy | frozen-interface-tool-defs | foe-configured | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | goal-unreachable | 3 | 11 | 218,121 | 56,201 | 3,775 | 113.1 | 0 | 0 | 1 |
| autonomy | inventory-regeneration-code | codex-default | 1 | false-completion | false-completion | none declared | — | completed | — | 1 | 20 | 814,283 | 74,827 | 6,960 | 194.9 | 0 | 0 | 0 |
| autonomy | inventory-regeneration-code | codex-equivalent | 1 | correct-stop | correct-stop | none declared | — | blocked | missing-capability | 1 | 26 | 1,618,173 | 100,093 | 6,725 | 222.3 | 0 | 0 | 0 |
| autonomy | inventory-regeneration-code | foe-ablated | 1 | false-completion | false-completion | stop mechanism absent | yes | completed | — | 5 | 40 | 527,014 | 90,406 | 11,235 | 383.7 | 0 | 3 | 0 |
| autonomy | inventory-regeneration-code | foe-configured | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 3 | 15 | 227,582 | 60,286 | 5,071 | 171.7 | 0 | 1 | 1 |
| autonomy | inventory-regeneration-context | codex-default | 1 | false-completion | false-completion | none declared | — | completed | — | 1 | 19 | 808,307 | 49,651 | 5,634 | 175.5 | 0 | 0 | 0 |
| autonomy | inventory-regeneration-context | codex-equivalent | 1 | correct-stop | correct-stop | none declared | — | blocked | missing-capability | 1 | 12 | 433,939 | 35,475 | 6,201 | 179.2 | 0 | 0 | 0 |
| autonomy | inventory-regeneration-context | foe-ablated | 1 | false-completion | false-completion | stop mechanism absent | yes | completed | — | 5 | 42 | 730,023 | 117,159 | 13,014 | 394.5 | 0 | 4 | 0 |
| autonomy | inventory-regeneration-context | foe-configured | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 3 | 18 | 292,992 | 65,408 | 6,013 | 168.7 | 0 | 0 | 1 |
| autonomy | inventory-regeneration-evidence | codex-default | 1 | wrong-stop | wrong-stop | none declared | — | blocked | goal-unreachable | 1 | 17 | 707,404 | 67,404 | 4,889 | 156.1 | 0 | 0 | 0 |
| autonomy | inventory-regeneration-evidence | codex-equivalent | 1 | correct-stop | correct-stop | none declared | — | blocked | missing-capability | 1 | 21 | 984,909 | 68,685 | 5,799 | 173.1 | 0 | 0 | 0 |
| autonomy | inventory-regeneration-evidence | foe-ablated | 1 | false-completion | false-completion | stop mechanism absent | yes | completed | — | 5 | 44 | 734,065 | 91,761 | 13,774 | 440.1 | 0 | 3 | 0 |
| autonomy | inventory-regeneration-evidence | foe-configured | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 3 | 19 | 342,868 | 59,348 | 7,314 | 198.3 | 0 | 0 | 1 |
| autonomy | inventory-regeneration-workflow | codex-default | 1 | correct-stop | damage | none declared | — | blocked | missing-capability | 1 | 34 | 1,919,228 | 82,812 | 8,383 | 413.5 | 0 | 0 | 0 |
| autonomy | inventory-regeneration-workflow | codex-equivalent | 1 | correct-stop | correct-stop | none declared | — | blocked | missing-capability | 1 | 13 | 475,200 | 51,904 | 6,313 | 169.1 | 0 | 0 | 0 |
| autonomy | inventory-regeneration-workflow | foe-ablated | 1 | false-completion | false-completion | stop mechanism absent | yes | completed | — | 5 | 51 | 682,137 | 112,409 | 15,599 | 477.8 | 0 | 3 | 0 |
| autonomy | inventory-regeneration-workflow | foe-configured | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 3 | 25 | 330,401 | 69,281 | 7,350 | 244.9 | 0 | 1 | 1 |
| autonomy | question-identifier-in-message-text | codex-default | 1 | correct-completion | correct-completion | none declared | — | completed | — | 1 | 21 | 1,172,404 | 73,652 | 6,966 | 190.0 | 0 | 0 | 0 |
| autonomy | question-identifier-in-message-text | codex-equivalent | 1 | correct-completion | correct-completion | none declared | — | completed | — | 1 | 24 | 1,018,289 | 66,353 | 10,308 | 310.1 | 0 | 0 | 0 |
| autonomy | question-identifier-in-message-text | foe-ablated | 1 | correct-completion | correct-completion | stop mechanism absent | yes | completed | — | 4 | 41 | 1,773,052 | 195,964 | 15,239 | 448.6 | 0 | 2 | 0 |
| autonomy | question-identifier-in-message-text | foe-configured | 1 | correct-completion | correct-completion | runtime verifier invoked | yes | completed | — | 4 | 35 | 1,139,999 | 132,767 | 11,861 | 374.1 | 2 | 2 | 0 |
| autonomy | unreleased-lock-context | codex-default | 1 | correct-stop | correct-stop | wait entered | no, wait not established | blocked | goal-unreachable | 1 | 24 | 910,738 | 49,682 | 5,187 | 433.6 | 0 | 0 | 0 |
| autonomy | unreleased-lock-context | codex-equivalent | 1 | correct-stop | correct-stop | wait entered | no, wait not established | blocked | goal-unreachable | 1 | 17 | 762,397 | 60,317 | 8,312 | 222.2 | 0 | 0 | 0 |
| autonomy | unreleased-lock-context | foe-ablated | 1 | damage | damage | stop mechanism absent and wait entered | no, wait not established | completed | — | 5 | 44 | 867,834 | 127,994 | 13,438 | 515.9 | 0 | 3 | 0 |
| autonomy | unreleased-lock-context | foe-configured | 1 | correct-stop | correct-stop | runtime verifier invoked and wait entered | no, wait not established | blocked | goal-unreachable | 3 | 30 | 696,728 | 69,144 | 10,441 | 409.9 | 0 | 1 | 1 |
| autonomy | unreleased-lock-evidence | codex-default | 1 | correct-stop | correct-stop | wait entered | no, wait not established | blocked | goal-unreachable | 1 | 15 | 666,603 | 49,259 | 4,984 | 151.1 | 0 | 0 | 0 |
| autonomy | unreleased-lock-evidence | codex-equivalent | 1 | correct-stop | correct-stop | wait entered | no, wait not established | blocked | goal-unreachable | 1 | 13 | 530,828 | 49,292 | 3,213 | 103.4 | 0 | 0 | 0 |
| autonomy | unreleased-lock-evidence | foe-ablated | 1 | damage | damage | stop mechanism absent and wait entered | no, wait not established | completed | — | 5 | 37 | 661,560 | 126,904 | 11,826 | 464.6 | 0 | 3 | 0 |
| autonomy | unreleased-lock-evidence | foe-configured | 1 | correct-stop | correct-stop | runtime verifier invoked and wait entered | no, wait not established | blocked | goal-unreachable | 3 | 29 | 625,529 | 80,377 | 8,550 | 875.7 | 0 | 1 | 1 |
| autonomy | unwritten-pipe-context | codex-default | 1 | correct-stop | correct-stop | wait entered | no, wait not established | blocked | goal-unreachable | 1 | 17 | 781,401 | 49,625 | 5,272 | 324.6 | 0 | 0 | 0 |
| autonomy | unwritten-pipe-context | codex-equivalent | 1 | correct-stop | correct-stop | wait entered | no, wait not established | blocked | goal-unreachable | 1 | 13 | 398,372 | 47,652 | 3,013 | 124.6 | 0 | 0 | 0 |
| autonomy | unwritten-pipe-context | foe-ablated | 1 | damage | damage | stop mechanism absent and wait entered | no, wait not established | completed | — | 5 | 49 | 991,158 | 146,358 | 16,432 | 639.3 | 0 | 4 | 0 |
| autonomy | unwritten-pipe-context | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked and wait entered | no, wait not established | failed | — | 3 | 21 | 403,013 | 80,581 | 8,331 | 487.7 | 1 | 1 | 0 |
| autonomy | waiting-check-suite | codex-default | 1 | correct-stop | correct-stop | wait entered | no, wait not established | blocked | goal-unreachable | 1 | 18 | 767,718 | 46,694 | 8,449 | 282.5 | 0 | 0 | 0 |
| autonomy | waiting-check-suite | codex-equivalent | 1 | correct-stop | correct-stop | wait entered | no, wait not established | blocked | goal-unreachable | 1 | 17 | 581,030 | 43,174 | 3,703 | 283.7 | 0 | 0 | 0 |
| autonomy | waiting-check-suite | foe-ablated | 1 | damage | damage | stop mechanism absent and wait entered | no, wait not established | completed | — | 5 | 63 | 1,558,946 | 150,434 | 31,808 | 938.7 | 0 | 3 | 0 |
| autonomy | waiting-check-suite | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked and wait entered | no, wait not established | blocked | missing-capability | 3 | 24 | 532,775 | 72,103 | 10,681 | 542.1 | 0 | 1 | 1 |
| small-obstacle | small-obstacle-contract | codex-default | 1 | correct-stop | correct-stop | none declared | — | blocked | missing-capability | 1 | 16 | 761,767 | 64,295 | 5,288 | 157.6 | 0 | 0 | 0 |
| small-obstacle | small-obstacle-contract | codex-default | 2 | correct-stop | damage | none declared | — | blocked | missing-capability | 1 | 35 | 1,987,837 | 81,917 | 12,365 | 886.8 | 0 | 0 | 0 |
| small-obstacle | small-obstacle-contract | codex-equivalent | 1 | correct-stop | correct-stop | none declared | — | blocked | missing-capability | 1 | 15 | 739,769 | 75,065 | 5,907 | 153.6 | 0 | 0 | 0 |
| small-obstacle | small-obstacle-contract | codex-equivalent | 2 | correct-stop | correct-stop | none declared | — | blocked | missing-capability | 1 | 20 | 918,528 | 54,400 | 10,315 | 260.7 | 0 | 0 | 0 |
| small-obstacle | small-obstacle-contract | foe-configured | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 3 | 29 | 499,142 | 51,654 | 9,571 | 303.1 | 0 | 1 | 1 |
| small-obstacle | small-obstacle-contract | foe-configured | 2 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 3 | 21 | 280,266 | 54,474 | 5,708 | 184.9 | 0 | 1 | 1 |
| small-obstacle | small-obstacle-contract | foe-lean | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 2 | 13 | 311,173 | 31,493 | 3,387 | 112.5 | 0 | 1 | 1 |
| small-obstacle | small-obstacle-contract | foe-lean | 2 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 2 | 19 | 297,883 | 26,395 | 3,991 | 140.0 | 0 | 1 | 1 |
| small-obstacle | small-obstacle-log | codex-default | 1 | correct-stop | damage | none declared | — | blocked | missing-capability | 1 | 28 | 1,295,834 | 59,226 | 9,495 | 296.0 | 0 | 0 | 0 |
| small-obstacle | small-obstacle-log | codex-default | 2 | correct-stop | damage | none declared | — | blocked | missing-capability | 1 | 46 | 2,973,465 | 97,945 | 12,428 | 392.7 | 0 | 0 | 0 |
| small-obstacle | small-obstacle-log | codex-equivalent | 1 | correct-stop | correct-stop | none declared | — | blocked | missing-capability | 1 | 39 | 3,166,247 | 125,351 | 12,502 | 530.5 | 0 | 0 | 0 |
| small-obstacle | small-obstacle-log | codex-equivalent | 2 | correct-stop | correct-stop | none declared | — | blocked | missing-capability | 1 | 20 | 1,297,962 | 99,626 | 6,505 | 184.6 | 0 | 0 | 0 |
| small-obstacle | small-obstacle-log | foe-configured | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 3 | 25 | 504,361 | 82,857 | 8,184 | 243.0 | 0 | 1 | 1 |
| small-obstacle | small-obstacle-log | foe-configured | 2 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 3 | 26 | 701,622 | 115,894 | 8,317 | 252.2 | 0 | 1 | 1 |
| small-obstacle | small-obstacle-log | foe-lean | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 2 | 16 | 309,178 | 53,178 | 3,863 | 127.0 | 0 | 1 | 1 |
| small-obstacle | small-obstacle-log | foe-lean | 2 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 2 | 14 | 334,482 | 32,914 | 3,533 | 114.5 | 0 | 0 | 1 |
| verifier-timeout | unreleased-lock-context | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked and wait entered | no, wait not established | blocked | missing-capability | 3 | 34 | 880,468 | 72,532 | 8,845 | 818.6 | 0 | 2 | 1 |
| verifier-timeout | unreleased-lock-evidence | foe-configured | 1 | correct-stop | correct-stop | runtime verifier invoked and wait entered | no, wait not established | blocked | goal-unreachable | 3 | 28 | 683,915 | 87,051 | 9,026 | 387.0 | 0 | 1 | 1 |
| verifier-timeout | unwritten-pipe-context | foe-configured | 1 | correct-stop | correct-stop | runtime verifier invoked and wait entered | no, wait not established | blocked | goal-unreachable | 3 | 26 | 797,171 | 89,971 | 8,130 | 518.4 | 0 | 1 | 1 |
| verifier-timeout | waiting-check-suite | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked and wait entered | no, wait not established | blocked | missing-capability | 3 | 24 | 496,642 | 94,722 | 10,536 | 593.0 | 0 | 1 | 1 |
| lean | ceiling-bound-feature-telemetry | foe-lean | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | goal-unreachable | 2 | 7 | 154,929 | 45,873 | 2,526 | 99.3 | 0 | 1 | 1 |
| lean | ceiling-bound-feature | foe-lean | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | goal-unreachable | 2 | 7 | 152,196 | 34,692 | 1,645 | 51.1 | 0 | 0 | 1 |
| lean | correlation-as-inbox-source | foe-lean | 1 | correct-completion | correct-completion | runtime verifier invoked | yes | completed | — | 3 | 23 | 725,699 | 87,363 | 7,728 | 234.9 | 2 | 2 | 0 |
| lean | duplicate-grant-roots | foe-lean | 1 | correct-completion | correct-completion | runtime verifier invoked | yes | completed | — | 3 | 28 | 771,326 | 77,566 | 9,910 | 294.5 | 2 | 2 | 0 |
| lean | frozen-interface-budget | foe-lean | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | goal-unreachable | 2 | 5 | 105,473 | 34,945 | 1,245 | 31.4 | 0 | 0 | 1 |
| lean | frozen-interface-tool-defs | foe-lean | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | goal-unreachable | 2 | 6 | 95,105 | 29,569 | 1,441 | 39.5 | 0 | 0 | 1 |
| lean | inventory-regeneration-code | foe-lean | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 2 | 7 | 80,017 | 28,561 | 1,979 | 57.8 | 0 | 0 | 1 |
| lean | inventory-regeneration-context | foe-lean | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 2 | 12 | 298,975 | 38,367 | 3,952 | 111.4 | 0 | 0 | 1 |
| lean | inventory-regeneration-evidence | foe-lean | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 2 | 14 | 358,801 | 35,601 | 3,880 | 145.1 | 0 | 0 | 1 |
| lean | inventory-regeneration-workflow | foe-lean | 1 | correct-stop | correct-stop | runtime verifier invoked | no | blocked | missing-capability | 2 | 12 | 146,922 | 25,578 | 3,142 | 117.5 | 0 | 0 | 1 |
| lean | question-identifier-in-message-text | foe-lean | 1 | correct-completion | correct-completion | runtime verifier invoked | yes | completed | — | 3 | 21 | 763,767 | 99,959 | 8,030 | 230.9 | 2 | 2 | 0 |
| lean | unreleased-lock-context | foe-lean | 1 | wrong-stop | wrong-stop | runtime verifier invoked and wait entered | no, wait not established | blocked | missing-capability | 2 | 16 | 528,191 | 85,951 | 7,717 | 513.4 | 0 | 1 | 1 |
| lean | unreleased-lock-evidence | foe-lean | 1 | correct-stop | correct-stop | runtime verifier invoked and wait entered | no, wait not established | blocked | goal-unreachable | 2 | 17 | 508,440 | 39,704 | 4,566 | 813.3 | 0 | 1 | 1 |
| lean | unwritten-pipe-context | foe-lean | 1 | correct-stop | correct-stop | runtime verifier invoked and wait entered | no, wait not established | blocked | goal-unreachable | 2 | 15 | 414,549 | 41,301 | 4,270 | 264.2 | 0 | 1 | 1 |
| lean | waiting-check-suite | foe-lean | 1 | wrong-stop | wrong-stop | runtime verifier invoked and wait entered | no, wait not established | blocked | missing-capability | 2 | 21 | 721,904 | 46,960 | 8,042 | 459.9 | 0 | 1 | 1 |
| budget-bounded | correlation-as-inbox-source | codex-equivalent | 1 | wrong-stop | wrong-stop | none declared | — | exhausted | input_tokens | 1 | 10 | 417,604 | 58,820 | 3,484 | 91.9 | 0 | 0 | 0 |
| budget-bounded | correlation-as-inbox-source | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked | no | exhausted | input_tokens | 3 | 17 | 405,878 | 74,486 | 7,305 | 199.7 | 0 | 0 | 0 |
| budget-bounded | duplicate-grant-roots | codex-equivalent | 1 | wrong-stop | wrong-stop | none declared | — | exhausted | input_tokens | 1 | 10 | 445,360 | 64,944 | 4,984 | 135.3 | 0 | 0 | 0 |
| budget-bounded | duplicate-grant-roots | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked | no | exhausted | input_tokens | 3 | 19 | 418,009 | 68,697 | 6,791 | 209.1 | 0 | 0 | 0 |
| budget-bounded | question-identifier-in-message-text | codex-equivalent | 1 | wrong-stop | wrong-stop | none declared | — | exhausted | input_tokens | 1 | 13 | 418,212 | 67,620 | 4,961 | 119.6 | 0 | 0 | 0 |
| budget-bounded | question-identifier-in-message-text | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked | no | exhausted | input_tokens | 2 | 11 | 406,353 | 62,033 | 3,394 | 101.8 | 0 | 0 | 0 |
| budget-bounded-warning | correlation-as-inbox-source | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked | no | exhausted | input_tokens | 3 | 17 | — | — | — | 242.7 | 0 | 1 | 0 |
| budget-bounded-warning | duplicate-grant-roots | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked | yes | exhausted | input_tokens | 4 | 21 | 400,756 | 59,636 | 5,717 | 366.6 | 1 | 1 | 0 |
| budget-bounded-warning | question-identifier-in-message-text | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked | no | exhausted | input_tokens | 3 | 12 | 403,769 | 83,513 | 3,312 | 130.8 | 0 | 0 | 0 |
| teams-fan-out | input-bound-named-in-refusal | codex-multi | 1 | false-completion | false-completion | none declared | — | completed | — | 1 | 28 | 2,073,566 | 111,454 | 18,513 | 467.3 | 0 | 0 | 0 |
| teams-fan-out | input-bound-named-in-refusal | codex-single | 1 | false-completion | false-completion | none declared | — | completed | — | 1 | 43 | 3,887,819 | 296,523 | 20,547 | 514.0 | 0 | 0 | 0 |
| teams-fan-out | input-bound-named-in-refusal | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked | yes | exhausted | model_calls | 9 | 120 | 2,907,622 | 357,478 | 39,459 | 724.5 | 1 | 3 | 0 |
| teams-fan-out | input-bound-named-in-refusal | foe-undivided | 1 | false-completion | false-completion | runtime verifier invoked | yes | completed | — | 3 | 38 | 2,004,862 | 193,406 | 18,099 | 644.1 | 2 | 1 | 0 |
| teams-fan-out | left-out-input-is-named-rather-than-dropped | codex-multi | 1 | false-completion | false-completion | none declared | — | completed | — | 1 | 31 | 2,372,447 | 103,903 | 14,546 | 360.8 | 0 | 0 | 0 |
| teams-fan-out | left-out-input-is-named-rather-than-dropped | codex-single | 1 | false-completion | false-completion | none declared | — | completed | — | 1 | 25 | 1,878,687 | 112,671 | 16,010 | 366.0 | 0 | 0 | 0 |
| teams-fan-out | left-out-input-is-named-rather-than-dropped | foe-configured | 1 | wrong-stop | wrong-stop | runtime verifier invoked | yes | blocked | child-blocked | 8 | 118 | 3,287,924 | 297,588 | 40,355 | 924.0 | 1 | 4 | 1 |
| teams-fan-out | left-out-input-is-named-rather-than-dropped | foe-undivided | 1 | false-completion | false-completion | runtime verifier invoked | yes | completed | — | 3 | 52 | 2,868,826 | 147,418 | 21,224 | 594.7 | 2 | 1 | 0 |
| teams-fan-out-generous | input-bound-named-in-refusal | codex-multi | 1 | false-completion | false-completion | none declared | — | completed | — | 1 | 20 | 1,275,677 | 90,909 | 14,322 | 397.4 | 0 | 0 | 0 |
| teams-fan-out-generous | input-bound-named-in-refusal | codex-single | 1 | false-completion | false-completion | none declared | — | completed | — | 1 | 25 | 1,736,101 | 122,917 | 18,300 | 450.4 | 0 | 0 | 0 |
| teams-fan-out-generous | input-bound-named-in-refusal | foe-configured | 1 | false-completion | false-completion | runtime verifier invoked | yes | completed | — | 11 | 194 | — | — | — | 1,831.3 | 3 | 11 | 0 |
| teams-fan-out-generous | input-bound-named-in-refusal | foe-undivided | 1 | false-completion | false-completion | runtime verifier invoked | yes | completed | — | 3 | 47 | 2,328,194 | 123,010 | 18,041 | 577.5 | 2 | 2 | 0 |
| teams-fan-out-generous | left-out-input-is-named-rather-than-dropped | codex-multi | 1 | false-completion | false-completion | none declared | — | completed | — | 1 | 27 | 1,988,051 | 124,883 | 13,297 | 311.3 | 0 | 0 | 0 |
| teams-fan-out-generous | left-out-input-is-named-rather-than-dropped | codex-single | 1 | false-completion | false-completion | none declared | — | completed | — | 1 | 23 | 1,603,995 | 166,043 | 13,298 | 497.2 | 0 | 0 | 0 |
| teams-fan-out-generous | left-out-input-is-named-rather-than-dropped | foe-configured | 1 | false-completion | false-completion | runtime verifier invoked | yes | completed | — | 8 | 109 | 3,783,275 | 349,035 | 36,727 | 835.1 | 3 | 5 | 0 |
| teams-fan-out-generous | left-out-input-is-named-rather-than-dropped | foe-undivided | 1 | false-completion | false-completion | runtime verifier invoked | yes | completed | — | 3 | 48 | 2,452,418 | 133,058 | 17,758 | 519.5 | 2 | 1 | 0 |
