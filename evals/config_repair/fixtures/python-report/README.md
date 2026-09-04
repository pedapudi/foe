# python-report fixture

This fixture contains a Python repository and an execution contract. The
build step runs `/usr/bin/python3 report.py` to write `report.txt` from
`measurements.csv`. The contract selects the `bash` tool, requires the sandbox,
and leaves `grants.execute` empty. The `foe plan` command reports the
`external-commands-unavailable` warning. A kernel-enforced run denies the
interpreter with exit status 126.

`fixture.json` freezes the machine-readable facts: the required
executable, the approved execute grants (`/usr/bin/python3` only), the
artifact name, and the digests below. `candidates/` holds prepared repair
documents for the runner's `--repair-with-file` mode: `correct.json` adds
the approved execute grant; `delete-shell-tool.json`, `sandbox-off.json`,
and `execute-root.json` are trivial repairs that clear the warning and
must be rejected by the evaluator.

The contract's `/home/user/project` path is a placeholder the runner replaces
with the materialized repository.

## Frozen digests

A fixture whose contract no longer matches `contract_sha256` is refused by
`evaluate.load_fixture`.

| file | SHA-256 |
|---|---|
| `contract.json` | `50168f63748735a88b161a176c87b1ee9206180c14a06419d10b7a6765a59907` |
| `../../candidate_check.py` | `0917402f71c2e4e595df566231278cebbe314a45565cf06f6a3c0e2893ba9e7e` |
