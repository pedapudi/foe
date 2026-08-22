# Self-extension demo

This example runs foe against a disposable copy of its own `read` tool. The
task adds a `total_bytes` field to the tool's canonical JSON result. The
episode also adds a regression assertion and updates the tool specification.

The copied files come from the current checkout:

- `crates/code/src/read.rs` implements the built-in tool.
- `crates/code/src/read_test.rs` tests the canonical result.
- `docs/tools.md` specifies the result for users and integrators.

The runner grants read and write access only to the disposable source tree.
The checkout remains outside the episode's grants. The built-in `edit` tool
also enforces the write root on systems without Landlock.

## Run

From the repository root:

```sh
bazel run //examples/self-extension
```

The target builds `//:foe`, copies the source files under
`target/foe-self-extension-demo.XXXXXX/foe/`, and runs one complete episode.
The resulting directory also contains the materialized configuration and the
episode log.

A binary at another path can run the example directly:

```sh
examples/self-extension/run.sh /absolute/path/to/foe
```

The same runner is a Bazel test target:

```sh
bazel test //examples/self-extension:self_extension_test
```

## Episode behavior

The deterministic local model transport makes no provider request and needs
no credential. It follows the ordinary coding loop:

1. The model reads the implementation, test, and specification.
2. The model calls `edit` once for each file.
3. The model reports completion.
4. The configured verifier checks the three source artifacts.

The verifier checks the source contract in less than a second. It does not
compile the disposable source slice because the slice omits the rest of the
workspace. The repository's Rust test job remains the compilation gate for
changes made to the real source tree.

## Expected result

The implementation gains this canonical field while its rendered line output
stays unchanged:

```rust
"total_bytes": bytes.len(),
```

The regression test asserts the byte count of its fixture. The tool table and
the detailed `read` specification describe the new field. `run.sh` checks the
changed files and confirms that the episode log contains both `read` and
`edit` tool results.
