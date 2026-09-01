# Sandbox demo

This example demonstrates kernel-enforced file access. The runner creates two
sibling directories. The configuration grants read access to one directory
and gives no grant for the other directory.

The agent calls the repository-owned `read-file.sh` contract for one file in
each directory. The granted file is returned. Landlock rejects the second
file open with a permission error, which foe records as the tool result.

## Requirements

The demo requires Linux with Landlock support and `/usr/bin/python3`.
`sandbox.mode` is `required`, so foe exits before the episode starts when
Landlock is unavailable. The process-boundary record states whether the host
also delegates cgroup v2.

The deterministic local model transport requires no model credential and
makes no provider request.

## Run

From the repository root:

```sh
bazel run //examples/sandbox
```

The target builds `//:foe` and runs `run.sh`. A binary at another path can run
the example directly:

```sh
examples/sandbox/run.sh /absolute/path/to/foe
```

Each run creates `target/foe-sandbox-demo.XXXXXX/`. The directory contains
the materialized configuration, both fixture directories, and the episode
log. The runner prints a command that serves the viewer for the log.

The same runner is a Bazel test target:

```sh
bazel test //examples/sandbox:sandbox_test
```

## Access policy

The materialized paths differ on every run. Their relationship stays fixed:

```text
target/foe-sandbox-demo.XXXXXX/
├── project/allowed.txt          read grant covers this file
├── outside-grant/denied.txt     no grant covers this file
└── episode/episode.jsonl
```

The configured `cat` tool grants execute access to `read-file.sh`. The child
process inherits the episode read roots. It can open `allowed.txt` and cannot
open `denied.txt`. The contract fingerprint hashes the repository-owned script,
so the example has the same fingerprint when system utility binaries differ.

## Expected result

The command exits zero because the agent completes its reporting task. The
denied `cat` process exits nonzero. A nonzero exit from a configured
executable is a tool result, so the model receives the permission error and
reports it in the next step.

`run.sh` checks three facts:

- The log contains the contents of `allowed.txt`.
- The log contains the permission error from `denied.txt`.
- `episode/start.sandbox.landlock_abi` is greater than zero.
- `episode/start.sandbox.process_boundary` records enforced cgroup v2
  cleanup or the observational process-group fallback.

The viewer states the Landlock ABI and process cleanup mechanism in the
details region. It shows both `cat` calls in the conversation tab. The denied
call contains its exit code and standard error.
