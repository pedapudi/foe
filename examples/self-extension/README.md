# Self-extension demo

This example runs foe against a disposable copy of its own `read` tool. One
form gives the extension task directly to an episode. A second form evaluates
the source first and gives the findings to a terminal workflow node that
improves the source.

Both forms add a `total_bytes` field to the tool's canonical JSON result. The
source change also adds a regression assertion and updates the tool
specification.

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

## Run evaluation before improvement

The self-improvement workflow contains two nodes:

1. `evaluate_read_tool` runs the external checker against the unmodified
   source. Its findings are the measured evidence for the change.
2. `improve_read_tool` receives the task and findings. This terminal model
   node edits the source, test, and specification. The checker verifies its
   output before the workflow can complete. A clean checker call completes
   the child without a separate model request.

The runner requires the fresh source to fail the checker. It then requires
the final source to pass. It also requires recorded `read`, `edit`, and
`check` tool results and a conformant workflow trace.

Run the deterministic form without a model credential:

```sh
bazel run //examples/self-extension:self-improvement-workflow
bazel test //examples/self-extension:self_improvement_workflow_test
```

## Run with a model provider

The model-backed runner replaces the scripted transport with
`openai-codex/gpt-5.6-sol`. It keeps the same disposable source, grants,
task, and verifier. The episode has declared limits of 24,000 input tokens,
4,000 output tokens, and eight model calls.

The runner prints the limits and starts no episode until the command includes
`--confirm-spend`:

```sh
bazel run //examples/self-extension:self-extension-model
bazel run //examples/self-extension:self-extension-model -- --confirm-spend
```

Select another configured provider with `--model PROVIDER/MODEL`:

```sh
bazel run //examples/self-extension:self-extension-model -- \
  --model openai/gpt-5.6-sol \
  --confirm-spend
```

The runner checks the candidate source and episode trace after foe exits. It
prints the artifact directory and a viewer command. The source checkout stays
outside the episode's read and write grants.

The model-backed runner has a deterministic test route that spends no model
credit:

```sh
bazel test //examples/self-extension:self_extension_model_runner_test
```

## Run the workflow with a model provider

The model-backed workflow declares 72,000 input tokens, 6,000 output tokens,
and twelve model calls per fresh attempt. The first command prints the spending
plan. The second command runs three independent attempts:

```sh
bazel run //examples/self-extension:self-improvement-workflow-model -- \
  --attempts 3
bazel run //examples/self-extension:self-improvement-workflow-model -- \
  --attempts 3 \
  --confirm-spend
```

Each attempt starts from a fresh source copy. The command succeeds only when
every attempt passes the artifact checker and trace evaluator. Select another
provider with `--model PROVIDER/MODEL` as in the direct model-backed form.

The build tests two fresh workflow attempts with the deterministic transport:

```sh
bazel test //examples/self-extension:self_improvement_workflow_model_runner_test
```

## Episode behavior

The deterministic local model transport makes no provider request and needs
no credential. The direct form follows the ordinary coding loop:

1. The model reads the implementation, test, and specification.
2. The model calls `edit` once for each file.
3. The model reports completion.
4. The configured verifier checks the three source artifacts.

The verifier checks the source artifacts in less than a second. It does not
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
the detailed `read` specification describe the new field. Every runner checks
the changed files and requires recorded `read` and `edit` results. The
workflow runners also require a `check` result and both workflow node results.
