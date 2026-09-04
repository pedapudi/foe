# jq-totals fixture

This fixture contains a jq repository and an execution contract. The build
step runs `/usr/bin/jq -r -f totals.jq inventory.json > totals.txt` to write
one category-total line per inventory category. The contract selects the
`bash` tool, requires the sandbox, and leaves `grants.execute` empty. The `foe
plan` command reports the `external-commands-unavailable` warning. A
kernel-enforced run denies the jq binary with exit status 126.

`fixture.json` freezes the machine-readable facts: the required
executable, the approved execute grants (`/usr/bin/jq` only), the artifact
name, and the digests below. `candidates/` holds prepared repair documents
for the runner's `--repair-with-file` mode: `correct.json` adds the
approved execute grant; `delete-shell-tool.json`, `sandbox-off.json`, and
`execute-root.json` are trivial repairs that clear the warning and must be
rejected by the evaluator.

The contract's `/home/user/project` path is a placeholder the runner replaces
with the materialized repository.

## Frozen digests

A fixture whose contract no longer matches `contract_sha256` is refused by
`evaluate.load_fixture`.

| file | SHA-256 |
|---|---|
| `contract.json` | `3e88b01b5d0f1e973f705aa87ff7acdfe5e3a457df64366917a5b197d5876411` |
| `../../candidate_check.py` | `0917402f71c2e4e595df566231278cebbe314a45565cf06f6a3c0e2893ba9e7e` |
