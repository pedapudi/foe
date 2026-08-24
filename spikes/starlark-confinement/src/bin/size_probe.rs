//! Minimal binary that links the confined evaluator. Its release size,
//! compared against the `size-baseline` crate's binary built with the
//! same profile, measures what the evaluator dependency adds to a
//! shipping binary.

use std::sync::atomic::AtomicBool;
use std::sync::Arc;

use starlark_confinement_spike::fixed_dispatcher;
use starlark_confinement_spike::run_program;
use starlark_confinement_spike::Limits;

fn main() {
    let result = run_program(
        "def main():\n    r = call_tool('probe', {'n': 1})\n    return {'ok': not r.is_error}\n",
        &Limits::generous(),
        fixed_dispatcher(serde_json::json!({"n": 1})),
        Arc::new(AtomicBool::new(false)),
    );
    match result {
        Ok(success) => println!("{}", success.value),
        Err(failure) => println!("{failure:?}"),
    }
}
