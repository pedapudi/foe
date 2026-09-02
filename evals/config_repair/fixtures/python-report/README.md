# python-report fixture

A Python repository whose build step runs `/usr/bin/python3 report.py` to
write `report.txt` from `measurements.csv`, paired with a deliberately
broken execution contract: `contract.json` selects the `bash` tool, sets
`sandbox.mode` to `required`, and leaves `grants.execute` empty, so
`foe plan` reports the `external-commands-unavailable` warning and a
kernel-enforced run denies the interpreter with exit status 126.

`fixture.json` freezes the machine-readable facts: the required
executable, the approved execute grants (`/usr/bin/python3` only), the
artifact name, and the digests below. `candidates/` holds prepared repair
documents for the runner's `--repair-with-file` mode: `correct.json` adds
the approved execute grant; `delete-shell-tool.json`, `sandbox-off.json`,
and `execute-root.json` are trivial repairs that clear the warning and
must be rejected by the evaluator.

The contract's `/home/user/project` and `/home/user/task-transport.py`
paths are placeholders the runner replaces with the materialized
repository and task transport; the contract fingerprint excludes resolved
permission paths, so materialization does not change it.

## Frozen digests

Recorded 2026-09-01, before any repair ran. A fixture whose contract no
longer matches `contract_sha256` is refused by `evaluate.load_fixture`.

| file | SHA-256 |
|---|---|
| `contract.json` | `e37041791dd1cd319c1cd188c362114a73618df70eab3577e704ba363aa0ec3f` |
| `../../task_transport.py` | `8d2c80aaf152928058be8fba5c88a189ca47d493dc76b5441d1fdf48fa3f5936` |
| `../../candidate_check.py` | `018e2288ab17a262f5cba59af213654341d071869819959c4ec20a9bfbb03cc8` |

The resolved contract fingerprint at freeze time is
`sha256:b9192849704ff7ebcaebab62abbf2bd92e5bf7ff3ba9d6b6e7c59ff46bdef468`
under runtime build
`sha256:3f2c0a6e98606ba5bd5867e209d8b40d039910afe119cf5830f449bfa071df6d`;
the fingerprint covers the captured transport bytes and
runtime-contributed text, so a different runtime build reports a different
fingerprint over the same frozen files.
