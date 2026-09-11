# Cross-harness evaluation

Evaluations that compare foe with Codex CLI on the properties foe claims as
its own: bounded, truthful termination; coordinated teams under disjoint
write grants; containment by grants; enforced budgets; verifier-gated
completion; and typed state across compaction. [docs/evaluation.md](../../docs/evaluation.md)
specifies the families, the arms, and the validity gates. This directory
holds the runners.

## Containment matrix

`containment_matrix.py` runs one fixed set of probe commands under every
configuration of both harnesses without a model, and reports which accesses
each sandbox allowed and which it denied. A probe prints a marker when its
access happened, so a cell is `allowed` when the marker appears, `denied`
when the diagnostic is the kernel's or the sandbox's, and `error` otherwise.
Every cell is compared with what the harness's documents state, and a cell
that differs makes the run exit 1.

foe runs the probes as `bash` calls of one scripted episode per
configuration, so each foe cell has an episode log behind it. Codex runs each
probe through `codex sandbox -P PROFILE`, which applies one permission
profile to a command without a model.

```sh
cargo build -p foe
python3 evals/cross_harness/containment_matrix.py --foe target/debug/foe
bazel run //evals/cross_harness:containment-matrix
```

`--skip-codex` runs the foe configurations alone. The fixture, the episode
logs, `matrix.json`, and `matrix.md` are written under `--out`, by default
`~/.local/state/foe/cross-harness/containment`. The fixture lives under the
home directory rather than `/tmp`, which Codex's workspace policy leaves
writable, so that a write outside the workspace is one.

The unit tests need no binary:

```sh
sh evals/cross_harness/run_unit_tests.sh
bazel test //evals/cross_harness:cross_harness_unit_test
```
