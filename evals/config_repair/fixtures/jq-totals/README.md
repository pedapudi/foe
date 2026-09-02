# jq-totals fixture

A jq repository whose build step runs
`/usr/bin/jq -r -f totals.jq inventory.json > totals.txt` to write one
category-total line per inventory category, paired with a deliberately
broken execution contract: `contract.json` selects the `bash` tool, sets
`sandbox.mode` to `required`, and leaves `grants.execute` empty, so
`foe plan` reports the `external-commands-unavailable` warning and a
kernel-enforced run denies the jq binary with exit status 126.

`fixture.json` freezes the machine-readable facts: the required
executable, the approved execute grants (`/usr/bin/jq` only), the artifact
name, and the digests below. `candidates/` holds prepared repair documents
for the runner's `--repair-with-file` mode: `correct.json` adds the
approved execute grant; `delete-shell-tool.json`, `sandbox-off.json`, and
`execute-root.json` are trivial repairs that clear the warning and must be
rejected by the evaluator.

The contract's `/home/user/project` and `/home/user/task-transport.py`
paths are placeholders the runner replaces with the materialized
repository and task transport; the contract fingerprint excludes resolved
permission paths, so materialization does not change it.

## Frozen digests

Recorded 2026-09-01, before any repair ran, so a repair of the
python-report fixture transfers here only through the unchanged workflow.
A fixture whose contract no longer matches `contract_sha256` is refused by
`evaluate.load_fixture`.

| file | SHA-256 |
|---|---|
| `contract.json` | `c84ce16c40b40d704a0ac63424f2949ac0ebc4e734491880548b8aa0f8696689` |
| `../../task_transport.py` | `8d2c80aaf152928058be8fba5c88a189ca47d493dc76b5441d1fdf48fa3f5936` |
| `../../candidate_check.py` | `018e2288ab17a262f5cba59af213654341d071869819959c4ec20a9bfbb03cc8` |

The resolved contract fingerprint at freeze time is
`sha256:0da24c0f8a6749343792c1bc71addcdb10add0c8c47317b79bde5d617826da4e`
under runtime build
`sha256:4c0dce854a5e9ce17e431e1310047b06034db6620561f4ebe81c37197db4bb52`;
the fingerprint covers the captured transport bytes and
runtime-contributed text, so a different runtime build reports a different
fingerprint over the same frozen files.
