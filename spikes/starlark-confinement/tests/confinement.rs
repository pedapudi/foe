//! The evaluator confinement suite from docs/code-mode.md "Evaluation
//! requirements", item 2: tests attempt filesystem, process, network,
//! clock, randomness, module loading, memory exhaustion, and
//! non-termination. The suite also measures fuel accounting, memory
//! accounting, and cancellation, which the evaluator spike must
//! demonstrate before the runtime takes an evaluator dependency.

use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;
use std::time::Instant;

use serde_json::json;
use starlark_confinement_spike::confined_dialect;
use starlark_confinement_spike::confined_globals;
use starlark_confinement_spike::fixed_dispatcher;
use starlark_confinement_spike::run_program;
use starlark_confinement_spike::EvalFailure;
use starlark_confinement_spike::Limits;
use starlark_confinement_spike::Success;

fn run(source: &str) -> Result<Success, EvalFailure> {
    run_program(source, &Limits::generous(), fixed_dispatcher(json!({"ok": true})), Arc::new(AtomicBool::new(false)))
}

fn error_message(source: &str) -> String {
    match run(source) {
        Err(EvalFailure::Error { message, .. }) => message,
        other => panic!("expected an evaluation error, got {other:?}"),
    }
}

/// Every global name the confined environment exposes. A name added by a
/// future starlark version fails this test, so widening the environment
/// is always a reviewed change.
#[test]
fn global_environment_is_exactly_the_reviewed_set() {
    let mut names: Vec<String> = confined_globals().names().map(|n| n.as_str().to_owned()).collect();
    names.sort();
    let expected = [
        "False",
        "None",
        "True",
        "abs",
        "all",
        "any",
        "bool",
        "bytes",
        "call_tool",
        "chr",
        "dict",
        "dir",
        "enumerate",
        "fail",
        "float",
        "getattr",
        "hasattr",
        "hash",
        "int",
        "len",
        "list",
        "max",
        "min",
        "ord",
        "range",
        "repr",
        "reversed",
        "sorted",
        "str",
        "tuple",
        "type",
        "zip",
    ];
    assert_eq!(names, expected);
}

/// No global name grants filesystem, process, network, environment,
/// clock, randomness, or dynamic-code authority. This is the "absence of
/// ambient imports" demonstration, checked against the name list rather
/// than one attempt at a time.
#[test]
fn no_ambient_authority_name_exists() {
    let forbidden = [
        "open",
        "read",
        "write",
        "file",
        "path",
        "glob", // filesystem
        "exec",
        "spawn",
        "system",
        "subprocess",
        "popen",
        "kill", // process
        "socket",
        "connect",
        "fetch",
        "urlopen",
        "http", // network
        "getenv",
        "setenv",
        "environ",
        "env", // environment
        "time",
        "now",
        "clock",
        "sleep",
        "date", // clock
        "random",
        "rand",
        "seed",
        "uuid", // randomness
        "load",
        "import",
        "require",
        "module", // module loading
        "eval",
        "compile",
        "exec_source", // dynamic code loading
        "print",
        "pprint",
        "debug",
        "breakpoint", // host I/O and console
    ];
    for name in confined_globals().names() {
        assert!(!forbidden.contains(&name.as_str()), "ambient authority name in globals: {}", name.as_str());
    }
}

#[test]
fn filesystem_access_is_undefined() {
    let message = error_message("def main():\n    return open('/etc/passwd')\n");
    assert!(message.contains("open"), "unexpected error: {message}");
}

#[test]
fn process_execution_is_undefined() {
    let message = error_message("def main():\n    return system('id')\n");
    assert!(message.contains("system"), "unexpected error: {message}");
}

#[test]
fn network_access_is_undefined() {
    let message = error_message("def main():\n    return urlopen('http://127.0.0.1/')\n");
    assert!(message.contains("urlopen"), "unexpected error: {message}");
}

#[test]
fn environment_access_is_undefined() {
    let message = error_message("def main():\n    return getenv('HOME')\n");
    assert!(message.contains("getenv"), "unexpected error: {message}");
}

#[test]
fn clock_access_is_undefined() {
    let message = error_message("def main():\n    return time()\n");
    assert!(message.contains("time"), "unexpected error: {message}");
}

#[test]
fn randomness_is_undefined() {
    let message = error_message("def main():\n    return random()\n");
    assert!(message.contains("random"), "unexpected error: {message}");
}

/// Two evaluations of one source with one dispatcher produce identical
/// values and identical step counts. docs/code-mode.md: evaluation is
/// deterministic given the source and the sequence of inner tool results.
#[test]
fn evaluation_is_deterministic() {
    let source = r#"
def main():
    d = {}
    for i in range(100):
        d[str(i)] = call_tool("probe", {"i": i}).value["ok"]
    return {"keys": sorted(d.keys()), "first": list(d.keys())[0]}
"#;
    let first = run(source).expect("first run succeeds");
    let second = run(source).expect("second run succeeds");
    assert_eq!(first.value, second.value);
    assert_eq!(first.usage.steps, second.usage.steps);
}

/// `load` is a parse error in the confined dialect, before any effect.
#[test]
fn load_statement_does_not_parse() {
    let result = run("load('other.star', 'helper')\ndef main():\n    return 1\n");
    match result {
        Err(EvalFailure::Parse { message }) => {
            assert!(message.contains("load"), "unexpected parse error: {message}")
        }
        other => panic!("expected a parse error, got {other:?}"),
    }
}

/// Defense in depth: with `load` syntax enabled and no loader installed,
/// evaluation still fails rather than reaching a filesystem.
#[test]
fn load_without_loader_fails_at_evaluation() {
    use starlark::environment::Module;
    use starlark::eval::Evaluator;
    use starlark::syntax::AstModule;
    use starlark::syntax::Dialect;
    let dialect = Dialect { enable_load: true, ..confined_dialect() };
    let ast = AstModule::parse("loader.star", "load('other.star', 'helper')\n".to_owned(), &dialect)
        .expect("parses with load enabled");
    let error = Module::with_temp_heap(|module| {
        let mut eval = Evaluator::new(&module);
        eval.eval_module(ast, &confined_globals()).map(|_| ()).map_err(|e| e.to_string())
    })
    .expect_err("load must fail without a loader");
    assert!(error.to_lowercase().contains("load"), "unexpected error: {error}");
}

/// `import` is not Starlark syntax.
#[test]
fn import_statement_does_not_parse() {
    let result = run("import os\ndef main():\n    return 1\n");
    assert!(matches!(result, Err(EvalFailure::Parse { .. })), "expected a parse error, got {result:?}");
}

/// Non-termination attempt: an unbounded loop ends at the step bound, and
/// the failure names the bound.
#[test]
fn non_termination_hits_the_step_bound() {
    let limits = Limits { steps: 100_000, ..Limits::generous() };
    let result = run_program(
        "def main():\n    n = 0\n    for i in range(1000000000):\n        n += i\n    return n\n",
        &limits,
        fixed_dispatcher(json!(null)),
        Arc::new(AtomicBool::new(false)),
    );
    match result {
        Err(EvalFailure::StepLimit { limit, usage }) => {
            assert_eq!(limit, 100_000);
            assert!(usage.steps >= limit, "steps {} under limit", usage.steps);
        }
        other => panic!("expected the step bound, got {other:?}"),
    }
}

/// Non-termination attempt through recursion ends at a bound rather than
/// overflowing the native stack.
#[test]
fn recursion_hits_a_bound() {
    let result = run("def f(n):\n    return f(n + 1)\ndef main():\n    return f(0)\n");
    match result {
        Err(EvalFailure::StepLimit { .. }) => {}
        Err(EvalFailure::Error { message, .. }) => {
            let lower = message.to_lowercase();
            assert!(lower.contains("recursion") || lower.contains("stack"), "unexpected error: {message}");
        }
        other => panic!("expected a bound failure, got {other:?}"),
    }
}

/// Memory exhaustion attempt: incremental allocation ends at the heap
/// bound, and the failure names the bound.
#[test]
fn memory_exhaustion_hits_the_heap_bound() {
    let limits = Limits { heap_bytes: 8 << 20, ..Limits::generous() };
    let result = run_program(
        "def main():\n    xs = []\n    for i in range(100000000):\n        xs.append(str(i) * 100)\n    return len(xs)\n",
        &limits,
        fixed_dispatcher(json!(null)),
        Arc::new(AtomicBool::new(false)),
    );
    match result {
        Err(EvalFailure::HeapLimit { limit, usage }) => {
            assert_eq!(limit, 8 << 20);
            // The evaluator's check adds frozen-heap bytes to this peak,
            // so the reported peak can sit slightly under the limit.
            assert!(usage.heap_peak_bytes > limit - (1 << 20), "peak {} far under the limit", usage.heap_peak_bytes);
        }
        other => panic!("expected the heap bound, got {other:?}"),
    }
}

/// The heap bound is checked between instructions rather than inside one
/// allocation, so a single large allocation lands before the error. The
/// evaluation still fails, but peak heap use overshoots the bound by the
/// size of that allocation. The report must state this property.
#[test]
fn one_large_allocation_overshoots_the_heap_bound_before_failing() {
    let limits = Limits { heap_bytes: 1 << 20, ..Limits::generous() };
    let result = run_program(
        "def main():\n    s = 'x' * 50000000\n    return len(s)\n",
        &limits,
        fixed_dispatcher(json!(null)),
        Arc::new(AtomicBool::new(false)),
    );
    match result {
        Err(EvalFailure::HeapLimit { limit, usage }) => {
            assert!(
                usage.heap_peak_bytes > 40 * limit,
                "expected a large overshoot, peak {} for limit {}",
                usage.heap_peak_bytes,
                limit
            );
        }
        other => panic!("expected the heap bound, got {other:?}"),
    }
}

/// The inner-call bound stops a program that loops over `call_tool`.
#[test]
fn inner_call_bound_is_enforced() {
    let limits = Limits { inner_calls: 10, ..Limits::generous() };
    let result = run_program(
        "def main():\n    for i in range(100):\n        call_tool('probe', {'i': i})\n    return 0\n",
        &limits,
        fixed_dispatcher(json!(null)),
        Arc::new(AtomicBool::new(false)),
    );
    match result {
        Err(EvalFailure::InnerCallLimit { limit, usage }) => {
            assert_eq!(limit, 10);
            assert_eq!(usage.inner_calls, 10);
        }
        other => panic!("expected the inner-call bound, got {other:?}"),
    }
}

/// The source-byte bound rejects an oversized program before parsing.
#[test]
fn source_byte_bound_is_enforced() {
    let limits = Limits { source_bytes: 100, ..Limits::generous() };
    let source = format!("def main():\n    return {:>200}\n", 1);
    let result = run_program(&source, &limits, fixed_dispatcher(json!(null)), Arc::new(AtomicBool::new(false)));
    assert!(matches!(result, Err(EvalFailure::SourceTooLarge { .. })), "expected the source bound, got {result:?}");
}

/// Fuel accounting: the reported step count grows in proportion to the
/// work the program performs, so a fixed step budget has a predictable
/// meaning.
#[test]
fn fuel_accounting_scales_with_work() {
    let steps_for = |n: u64| {
        let source =
            format!("def main():\n    total = 0\n    for i in range({n}):\n        total += i\n    return total\n");
        run(&source).expect("loop completes").usage.steps
    };
    let small = steps_for(10_000);
    let large = steps_for(100_000);
    assert!(small >= 10_000, "small loop reported {small} steps");
    assert!(large >= 9 * small && large <= 11 * small + 10_000, "step count is not proportional: {small} vs {large}");
}

/// Memory accounting: the reported peak covers a known allocation.
#[test]
fn memory_accounting_reports_a_known_allocation() {
    let usage = run("def main():\n    s = 'x' * 10000000\n    return len(s)\n")
        .expect("allocation fits the generous bound")
        .usage;
    assert!(usage.heap_peak_bytes >= 10_000_000, "peak {} misses a 10 MB string", usage.heap_peak_bytes);
    assert!(usage.heap_peak_bytes <= 64_000_000, "peak {} is implausibly large", usage.heap_peak_bytes);
}

/// Cancellation: a flag set from another thread stops an evaluation that
/// would otherwise run for minutes, within a small fraction of a second
/// of the flag being set.
#[test]
fn cancellation_interrupts_a_running_evaluation() {
    let cancel = Arc::new(AtomicBool::new(false));
    let cancel_for_eval = cancel.clone();
    let worker = std::thread::spawn(move || {
        let started = Instant::now();
        let result = run_program(
            "def main():\n    n = 0\n    for i in range(1000000):\n        for j in range(1000000):\n            n += j\n    return n\n",
            &Limits {
                steps: u64::MAX,
                ..Limits::generous()
            },
            fixed_dispatcher(json!(null)),
            cancel_for_eval,
        );
        (result, started.elapsed())
    });
    std::thread::sleep(Duration::from_millis(100));
    cancel.store(true, Ordering::Relaxed);
    let (result, elapsed) = worker.join().expect("worker joins");
    assert!(matches!(result, Err(EvalFailure::Cancelled { .. })), "expected cancellation, got {result:?}");
    assert!(elapsed < Duration::from_secs(2), "cancellation took {elapsed:?}");
}

/// docs/code-mode.md: inner dispatch is unavailable while the evaluator
/// loads the source. A top-level `call_tool` fails before any effect.
#[test]
fn dispatch_is_disabled_during_module_load() {
    let calls = Arc::new(AtomicBool::new(false));
    let calls_seen = calls.clone();
    let result = run_program(
        "x = call_tool('probe', {})\ndef main():\n    return x\n",
        &Limits::generous(),
        Box::new(move |_, _| {
            calls_seen.store(true, Ordering::Relaxed);
            Ok(json!(null))
        }),
        Arc::new(AtomicBool::new(false)),
    );
    match result {
        Err(EvalFailure::Error { message, .. }) => {
            assert!(message.contains("inner dispatch is unavailable"), "unexpected error: {message}")
        }
        other => panic!("expected a load-phase dispatch error, got {other:?}"),
    }
    assert!(!calls.load(Ordering::Relaxed), "dispatcher ran during load");
}

/// The worked example from docs/code-mode.md "Outer call contract": a
/// program narrows a tool result and returns a JSON value.
#[test]
fn a_program_narrows_a_tool_result() {
    let result = run_program(
        r#"
def main():
    result = call_tool("grep", {"pattern": "TODO", "path": ".", "limit": 100})
    if result.is_error:
        fail(result.value["error"])
    return {"matches": result.value["matches"], "files": result.value["files"]}
"#,
        &Limits::generous(),
        Box::new(|name, args| {
            assert_eq!(name, "grep");
            assert_eq!(args["pattern"], "TODO");
            Ok(json!({"matches": 17, "files": 5, "complete": true}))
        }),
        Arc::new(AtomicBool::new(false)),
    )
    .expect("program succeeds");
    assert_eq!(result.value, json!({"matches": 17, "files": 5}));
    assert_eq!(result.usage.inner_calls, 1);
}

/// An inner error surfaces as `is_error` and the program can continue.
#[test]
fn a_program_inspects_an_inner_error_and_continues() {
    let result = run_program(
        r#"
def main():
    result = call_tool("grep", {"pattern": "["})
    if result.is_error:
        return {"failed": True, "error": result.value["error"]}
    return {"failed": False}
"#,
        &Limits::generous(),
        Box::new(|_, _| Err(json!({"error": "invalid pattern"}))),
        Arc::new(AtomicBool::new(false)),
    )
    .expect("program handles the inner error");
    assert_eq!(result.value, json!({"failed": true, "error": "invalid pattern"}));
}

/// `fail(message)` ends the outer call with an error, as the contract
/// requires.
#[test]
fn fail_ends_the_evaluation_with_an_error() {
    let result = run("def main():\n    fail('boom')\n");
    match result {
        Err(EvalFailure::Fail { message, .. }) => {
            assert!(message.contains("boom"), "unexpected message: {message}")
        }
        other => panic!("expected fail, got {other:?}"),
    }
}

/// A source without `main` produces an error before any dispatch.
#[test]
fn a_source_without_main_is_rejected() {
    let result = run("def helper():\n    return 1\n");
    assert!(matches!(result, Err(EvalFailure::NoMain)), "expected NoMain, got {result:?}");
}

/// A non-JSON return value (a function) is an error, since the outer
/// result must be a JSON value.
#[test]
fn a_non_json_return_value_is_rejected() {
    let result = run("def main():\n    return main\n");
    match result {
        Err(EvalFailure::Error { message, .. }) => assert!(message.contains("non-JSON"), "unexpected error: {message}"),
        other => panic!("expected a non-JSON error, got {other:?}"),
    }
}
