# Configuration repair

This directory measures and closes one self-improvement loop over a
configuration defect. The fixture contract selects a shell tool while leaving
`grants.execute` empty. The `foe plan` command reports this defect as the
`external-commands-unavailable` warning. A baseline run fails, and an
operational-failure digest cites the warning and denial evidence. A repair
child returns a corrected contract document. An unchanged external evaluator
reruns the task and judges the candidate against the recorded fixture.

The result establishes externally verified configuration self-repair: the
diagnosis, candidate, verification, and transfer machinery work end to
end. It establishes nothing about improving the runtime, its tools, or its
workflow implementation. The digest reports only mechanically attributable
signals — a denial names its path, an invalid call names its field —
and grouping failures by shared semantic cause remains separate work.

## Attempt directories

One directory per attempt holds everything a later reader needs to
reconstruct it:

```text
contract.json     the execution contract the attempt ran
plan.json         the `foe plan --json` output for that contract
episode/          the episode log tree (episode.jsonl and children/)
evaluation.json   the external evaluator's verdict for the attempt
candidate.json    the returned candidate in canonical JSON, when the
                  attempt produced one
```

Configuration warnings exist only in plan output. Episode logs do not contain
them, so `plan.json` is retained beside the log. Consumers receive an explicit
list of attempt directories. No consumer searches a tree for attempts.

## Operational-failure digest

`operational_digest.py ATTEMPT_DIR [ATTEMPT_DIR ...]` prints one JSON
report with a block per attempt and a cross-attempt aggregation. Every row
cites the reconstructable event by episode id and log sequence, or names
`plan.json` as its source. The per-attempt rows are:

- `configuration_warnings` — the static warnings from the retained
  `plan.json`.
- `enforced_permission_denials` — tool results whose typed failure code is
  `capability-denied`; the runtime itself refused the call.
- `possible_permission_denials` — process results with exit status 126 and
  `Permission denied` on standard error, matching the runtime's own
  `permission_denial: "possible"` marking. The block carries its basis
  string: this is a heuristic signal and never an established cause.
- `typed_failure_counts` — typed tool failures counted by
  {tool, failure code, field}, where the field is the argument a failure
  names in its details or message. The `invalid-call` rows are the
  invalid-call counts; the same key shape covers every other failure code.
- `repeated_failed_commands` — shell commands that failed more than once,
  with every citation.
- `calls_before_first_productive` — per episode, the number of tool
  results before the first successful write (a non-error `edit` result),
  successful execution (a shell result with exit status 0), or accepted
  verification.
- `completed_artifacts_with_noncompleted_outcomes` — attempts whose
  retained evaluation passed the task-artifact check while the episode
  outcome is not `completed`.

The cross-attempt block aggregates the failure counts, repeated commands,
warnings, and denial totals over the declared directories.

## Fixtures

`fixtures/` holds two recorded fixtures in different languages. Each fixture
contains a small repository and a contract whose `grants.execute` is empty
beside the selected `bash` tool:

| fixture | language | required executable | artifact |
|---|---|---|---|
| `python-report` | Python | `/usr/bin/python3` | `report.txt` |
| `jq-totals` | jq | `/usr/bin/jq` | `totals.txt` |

Each fixture's `fixture.json` records the required executable, the approved
execute grants, and the SHA-256 digests of the contract and proposal
verifier. `evaluate.load_fixture` refuses a fixture whose contract differs
from its recorded digest.

The host supplies each task episode with deterministic responses. It issues
the fixture's build command through the `bash` tool, reports completion when
the command exits zero, and blocks on `missing-capability` otherwise. The
responses never inspect the contract's grants, so the baseline and candidate
runs differ only in what the runtime permits.

## External evaluator

`evaluate.py` judges a candidate contract against the frozen fixture. The
repair machinery never edits the evaluator and the evaluator never edits a
candidate. A candidate passes only when every check passes:

- `required-command-ran` — the rerun log records the fixture's required
  executable exiting zero through a shell tool.
- `sandbox-not-weakened` — the candidate's sandbox mode is at least the
  fixture contract's mode, on the order off < best-effort < required.
- `shell-tool-available` — every shell tool the fixture contract selects
  remains selected.
- `execute-grants-approved` — every candidate execute grant is an approved
  executable or lies under an approved directory from the fixture's frozen
  list.
- `no-unrelated-widening` — the read, write, spawn, and bind grant sets,
  the tool list, and the tool definitions may only shrink or stay equal.
- `task-artifact` — the artifact matches an expectation computed here from
  the frozen fixture data, independently of anything the episode wrote or
  reported.
- `bundle-verified` — `verify-evidence-bundle` accepts the retained
  evidence bundle, the verifier fingerprint is the frozen proposal
  verifier's digest, and the attested `candidate.json` is this candidate's
  canonical JSON.

The disappearance of the configuration warning passes nothing by itself: a
candidate that deletes `bash`, sets the sandbox off, or grants execute on
`/` clears the warning and fails here, and the unit tests hold each of
those rejection paths.

## Repair-loop runner

`run_repair_loop.py FIXTURE_DIR --output DIR` runs the whole loop and
writes `attempt-baseline/`, `attempt-repair/`, `attempt-rerun/`, the
digest, the evidence bundle, and `pipeline-report.json` under the output
directory. It exits 0 when the evaluator passes the candidate, 1 when the
evaluator rejects it, and 2 when a pipeline step fails.

The proposal episode uses one of these response sources:

- `--repair-with-file CANDIDATE.json` — the host returns the prepared
  candidate document. Fixture placeholder paths in the prepared file are
  materialized like the fixture contract. `fixtures/<name>/candidates/`
  holds a correct candidate and the three trivial repairs above. The trivial
  repairs must exit 1.
- `--repair-with-model PROVIDER/MODEL` — the same proposal contract with a
  runtime-owned model route (`--repair-api-key-file`,
  `--repair-reasoning-effort`, and `--repair-model-calls` adjust it).

In both modes the repair child receives the broken contract, plan warnings,
and digest as files under its read root. It returns the corrected document
through the synthesized `return` tool. A recorded structural verifier
(`candidate_check.py`) accepts the document shape in the proposal episode and
attests the judged value's digest. The runner checks the candidate with `foe
plan`, records the resolved execute permissions, builds and verifies the
evidence bundle, reruns the task, and gives the evidence to the evaluator.

The `foe`, `build-evidence-bundle`, and `verify-evidence-bundle` binaries
default to `target/release/` in this repository; `cargo build --release`
produces them.
