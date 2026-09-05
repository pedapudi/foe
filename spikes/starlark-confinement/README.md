# Starlark confinement spike

docs/tool-composition.md records the earlier `code` tool whose child contracts ran in a confined
Starlark evaluator. The design forbids the runtime from taking an
evaluator dependency before a spike demonstrates five properties: fuel
accounting, memory accounting, cancellation, a disabled module loader,
and the absence of ambient imports. This crate is that spike. It wraps the Rust Starlark
implementation (the `starlark` crate, version 0.14.2, from
github.com/facebook/starlark-rust, Apache-2.0) and carries the confinement
test suite in `tests/confinement.rs`. The empty `[workspace]` table in
`Cargo.toml` detaches the crate from the repository workspace, so no
product crate depends on anything here.

## Verdict

Starlark through the `starlark` crate meets every property the selection
gate requires. No requirement fails. Three qualifications, listed under
"Qualifications", bound what the evaluator's own limits guarantee; each
has a compensating control that the code-mode design already requires.

## What the prototype does

`src/lib.rs` exposes one function, `run_contract`. It parses a source
string in a dialect with `load` disabled, evaluates the module with inner
dispatch disabled, then invokes the module's zero-argument `main` with
dispatch enabled. Inner dispatch is the single native function
`call_tool(name, args)` from the design's outer call contract; it forwards
to a caller-supplied closure that stands in for the tool registry and
returns `struct(value = ..., is_error = ...)`. The caller supplies five
bounds: source bytes, evaluator steps, evaluator heap bytes, inner tool
calls, and call-stack depth. Every evaluation reports what it consumed:
steps, peak heap bytes, and inner calls.

## Evidence for each required property

Each item names the tests that demonstrate it.

- **Fuel accounting.** The evaluator counts steps, where one step is one
  function call or one loop back-edge. The count grows in proportion to
  the work a contract performs: a loop of 100,000 iterations reports ten
  times the steps of a loop of 10,000
  (`fuel_accounting_scales_with_work`). An unbounded loop under a bound of
  100,000 steps fails with an error naming the bound after 101,000 steps,
  because the evaluator checks its limits every 1,000 steps
  (`non_termination_hits_the_step_bound`).
- **Memory accounting.** The evaluator reports peak heap bytes; the
  report covers a known 10 MB string allocation
  (`memory_accounting_reports_a_known_allocation`). A contract that
  allocates without bound under an 8 MiB heap limit fails with an error
  naming the limit (`memory_exhaustion_hits_the_heap_bound`).
- **Cancellation.** A flag set from another thread stops an evaluation
  that would otherwise run for minutes; the evaluation returns a
  cancellation error at the next limit check, well inside the two-second
  bound the test asserts (`cancellation_interrupts_a_running_evaluation`).
- **Disabled module loader.** With `enable_load: false` in the dialect, a
  `load` statement is a parse error, so rejection happens before any
  effect (`load_statement_does_not_parse`). As defense in depth, even a
  dialect with `load` enabled fails at evaluation when no loader is
  installed, because the loader is an explicit constructor argument rather
  than an ambient default (`load_without_loader_fails_at_evaluation`).
  `import` is not Starlark syntax (`import_statement_does_not_parse`).
- **Absence of ambient imports.** The global environment is the Starlark
  standard library plus `call_tool`: 32 names, snapshot-asserted as an
  exact list, so any name a future crate version adds fails the suite
  (`global_environment_is_exactly_the_reviewed_set`). No name grants
  filesystem, process, network, environment, clock, randomness, module,
  or dynamic-code authority (`no_ambient_authority_name_exists`), and one
  attempt test per category confirms the call fails
  (`filesystem_access_is_undefined` through `randomness_is_undefined`).
  Two evaluations of one source with one dispatcher produce identical
  values and identical step counts, including dictionary iteration order
  (`evaluation_is_deterministic`).

## Evidence for the remaining design requirements

- **Bounds beyond fuel and memory.** The source-byte bound rejects an
  oversized contract before parsing (`source_byte_bound_is_enforced`). The
  inner-call bound stops a contract that loops over `call_tool`
  (`inner_call_bound_is_enforced`). Unbounded recursion fails with a
  Starlark call-stack overflow at the configured depth, before the native
  stack is at risk (`recursion_hits_a_bound`).
- **Dispatch gating.** A `call_tool` in top-level module code fails with
  an error and the dispatcher never runs, which implements the contract's
  rule that inner dispatch is unavailable while the evaluator loads the
  source (`dispatch_is_disabled_during_module_load`).
- **Outer call contract.** The worked contract from docs/tool-composition.md runs
  unchanged: struct field access on the result, `is_error` inspection,
  and a narrowed JSON return value (`a_contract_narrows_a_tool_result`,
  `a_contract_inspects_an_inner_error_and_continues`). `fail(message)`
  ends the evaluation with an error carrying the message
  (`fail_ends_the_evaluation_with_an_error`). A missing `main` and a
  non-JSON return value each produce an error before or instead of a
  result (`a_source_without_main_is_rejected`,
  `a_non_json_return_value_is_rejected`).

## Qualifications

- **The heap bound is checked between instructions.** A single large
  allocation completes before the check runs. A 50 MB string under a
  1 MiB bound fails only after the allocation, so peak memory overshoots
  the bound by the size of that one value
  (`one_large_allocation_overshoots_the_heap_bound_before_failing`). The
  crate's own documentation also states that values backed by the native
  Rust heap, such as the backing storage of a large dictionary, are not
  counted. The episode's process-level sandbox therefore remains the
  authority for hard memory containment; the evaluator bound is the
  budget that produces a clean, attributable error.
- **Enforcement granularity is 1,000 steps.** Fuel, heap, and
  cancellation checks all run at that interval, so each can overrun by up
  to 1,000 steps plus the duration of the instruction in progress.
- **A blocking native call is not interrupted.** The cancellation flag is
  read between evaluator steps. While `call_tool` waits on a tool, the
  evaluator cannot act on the flag; the runtime must cancel the inner
  tool itself, which docs/tool-composition.md already requires of ordinary tool
  teardown. The upstream crate states that its limits are protection
  against accidental blowups rather than a security boundary against
  malicious code, which matches this design: the registry and the sandbox
  hold the authority, and the evaluator holds none to leak.

## Cost of the dependency

- **Binary size.** A minimal binary linking the confined evaluator
  (`src/bin/size_probe.rs`), built with the workspace's release profile
  (`opt-level = "z"`, fat LTO, `panic = "abort"`, stripped), is 4,274,376
  bytes. The same binary without the `starlark` dependency
  (`../size-baseline`) is 302,208 bytes. The evaluator therefore adds
  about 3.97 MB. The repository's release binary measures 5,417,656
  bytes, so adoption would grow it by roughly 73 percent.
- **Dependency tree.** The spike's lockfile resolves 173 crates; 119 of
  their names are absent from the repository's lockfile. The additions
  include the four Starlark crates themselves (`starlark`,
  `starlark_syntax`, `starlark_map`, `starlark_derive`) and transitive
  dependencies such as `num-bigint`, `logos`, `lsp-types`, `rustyline`,
  and `allocative`; a portion are build-time or proc-macro crates that do
  not enter the binary.
- **Build time.** A cold release build of the spike, its dependencies,
  and the fat-LTO link takes about 80 seconds on a development machine.
- **License.** Apache-2.0, the same license as this repository.

## Running the spike

```
cd spikes/starlark-confinement
cargo test                # the confinement suite, 27 tests
cargo build --release     # builds target/release/size_probe
cd ../size-baseline
cargo build --release     # builds the size comparison baseline
```
