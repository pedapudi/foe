//! Prototype evaluator for the selection gate in docs/tool-composition.md.
//!
//! The design requires evidence of five properties before the runtime may
//! take an evaluator dependency: fuel accounting, memory accounting,
//! cancellation, a disabled module loader, and the absence of ambient
//! imports. This crate wraps the `starlark` crate with the confinement
//! configuration the design describes and exposes the measurements the
//! test suite in `tests/confinement.rs` asserts on.
//!
//! The crate is a spike. It holds no registry, no capability handles, and
//! no log integration. Inner dispatch goes to a caller-supplied closure
//! that stands in for the ordinary tool registry.

use std::cell::Cell;
use std::cell::RefCell;
use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;
use std::sync::Arc;

use starlark::any::ProvidesStaticType;
use starlark::environment::Globals;
use starlark::environment::GlobalsBuilder;
use starlark::environment::Module;
use starlark::eval::Evaluator;
use starlark::syntax::AstModule;
use starlark::syntax::Dialect;
use starlark::values::dict::AllocDict;
use starlark::values::list::AllocList;
use starlark::values::structs::AllocStruct;
use starlark::values::Heap;
use starlark::values::Value;

/// Stand-in for the tool registry. Receives the inner tool name and the
/// JSON argument object. `Ok` is the tool's canonical result value; `Err`
/// is the tool's canonical error value.
pub type ToolDispatcher = Box<dyn FnMut(&str, &serde_json::Value) -> Result<serde_json::Value, serde_json::Value>>;

/// The four evaluator bounds from docs/tool-composition.md "Evaluation
/// environment", plus the call-stack depth bound the evaluator offers.
#[derive(Clone, Copy, Debug)]
pub struct Limits {
    pub source_bytes: usize,
    pub steps: u64,
    pub heap_bytes: usize,
    pub inner_calls: u64,
    pub call_stack_depth: usize,
}

impl Limits {
    /// Generous defaults for tests that must not hit any bound.
    pub fn generous() -> Self {
        Limits {
            source_bytes: 1 << 20,
            steps: 100_000_000,
            heap_bytes: 256 << 20,
            inner_calls: 1_000,
            call_stack_depth: 50,
        }
    }
}

/// What one evaluation consumed. `steps` is the evaluator's tick count,
/// where one tick is one function call or one loop back-edge.
/// `heap_peak_bytes` is the peak of the Starlark value heap.
#[derive(Clone, Copy, Debug)]
pub struct Usage {
    pub steps: u64,
    pub heap_peak_bytes: usize,
    pub inner_calls: u64,
}

/// A completed evaluation: the JSON value `main` returned and what the
/// evaluation consumed.
#[derive(Debug)]
pub struct Success {
    pub value: serde_json::Value,
    pub usage: Usage,
}

/// Every way an evaluation ends without a returned value. Each bound
/// failure names its bound, which docs/tool-composition.md requires of the outer
/// error result.
#[derive(Debug)]
pub enum EvalFailure {
    SourceTooLarge {
        bytes: usize,
        limit: usize,
    },
    Parse {
        message: String,
    },
    /// The source defines no zero-argument `main` function.
    NoMain,
    StepLimit {
        limit: u64,
        usage: Usage,
    },
    HeapLimit {
        limit: usize,
        usage: Usage,
    },
    Cancelled {
        usage: Usage,
    },
    InnerCallLimit {
        limit: u64,
        usage: Usage,
    },
    /// The contract called `fail(message)`.
    Fail {
        message: String,
        usage: Usage,
    },
    /// Any other evaluation error: an undefined name, a type error, a
    /// dispatch attempt during module load, or a non-JSON return value.
    Error {
        message: String,
        usage: Usage,
    },
}

/// State shared with the `call_tool` native function through
/// `Evaluator::extra`.
#[derive(ProvidesStaticType)]
struct ToolHost {
    dispatcher: RefCell<ToolDispatcher>,
    calls: Cell<u64>,
    max_calls: u64,
    /// docs/tool-composition.md: inner dispatch is unavailable while the
    /// evaluator loads the source and enabled only while invoking `main`.
    dispatch_enabled: Cell<bool>,
}

fn json_to_starlark<'v>(heap: Heap<'v>, v: &serde_json::Value) -> anyhow::Result<Value<'v>> {
    Ok(match v {
        serde_json::Value::Null => Value::new_none(),
        serde_json::Value::Bool(b) => Value::new_bool(*b),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                heap.alloc(i)
            } else if let Some(f) = n.as_f64() {
                heap.alloc(f)
            } else {
                anyhow::bail!("unrepresentable JSON number: {n}");
            }
        }
        serde_json::Value::String(s) => heap.alloc(s.as_str()),
        serde_json::Value::Array(items) => {
            let values: Vec<Value> =
                items.iter().map(|item| json_to_starlark(heap, item)).collect::<anyhow::Result<_>>()?;
            heap.alloc(AllocList(values))
        }
        serde_json::Value::Object(fields) => {
            let pairs: Vec<(Value, Value)> = fields
                .iter()
                .map(|(k, item)| Ok((heap.alloc(k.as_str()), json_to_starlark(heap, item)?)))
                .collect::<anyhow::Result<_>>()?;
            heap.alloc(AllocDict(pairs))
        }
    })
}

#[starlark::starlark_module]
fn code_mode_globals(builder: &mut GlobalsBuilder) {
    /// The one inner-dispatch function from docs/tool-composition.md "Outer call
    /// contract": `call_tool(name, args)` returns
    /// `struct(value = <canonical JSON value>, is_error = <boolean>)`.
    fn call_tool<'v>(name: &str, args: Value<'v>, eval: &mut Evaluator<'v, '_, '_>) -> anyhow::Result<Value<'v>> {
        let host = eval
            .extra
            .ok_or_else(|| anyhow::anyhow!("call_tool: no tool host installed"))?
            .downcast_ref::<ToolHost>()
            .ok_or_else(|| anyhow::anyhow!("call_tool: extra is not a ToolHost"))?;
        if !host.dispatch_enabled.get() {
            anyhow::bail!("call_tool: inner dispatch is unavailable while the evaluator loads the source");
        }
        if host.calls.get() >= host.max_calls {
            anyhow::bail!("call_tool: inner tool call bound of {} exceeded", host.max_calls);
        }
        host.calls.set(host.calls.get() + 1);
        let args_json =
            args.to_json_value().map_err(|e| anyhow::anyhow!("call_tool: args is not a JSON value: {e}"))?;
        if !args_json.is_object() {
            anyhow::bail!("call_tool: args must be a JSON object");
        }
        let outcome = (host.dispatcher.borrow_mut())(name, &args_json);
        let heap = eval.heap();
        let (value, is_error) = match outcome {
            Ok(v) => (json_to_starlark(heap, &v)?, false),
            Err(v) => (json_to_starlark(heap, &v)?, true),
        };
        Ok(heap.alloc(AllocStruct([("value", value), ("is_error", Value::new_bool(is_error))])))
    }
}

/// The exact global environment the prototype exposes. Tests snapshot its
/// name set to prove the absence of ambient authority.
pub fn confined_globals() -> Globals {
    GlobalsBuilder::standard().with(code_mode_globals).build()
}

/// The exact dialect the prototype accepts. `load` statements do not
/// parse; every other setting matches the Starlark standard dialect.
pub fn confined_dialect() -> Dialect {
    Dialect { enable_load: false, enable_load_reexport: false, ..Dialect::Standard }
}

fn classify(message: String, limits: &Limits, usage: Usage) -> EvalFailure {
    if message.contains("Evaluation cancelled") {
        EvalFailure::Cancelled { usage }
    } else if message.contains("Heap memory limit") {
        EvalFailure::HeapLimit { limit: limits.heap_bytes, usage }
    } else if message.contains("ticks has been exceeded") {
        EvalFailure::StepLimit { limit: limits.steps, usage }
    } else if message.contains("inner tool call bound") {
        EvalFailure::InnerCallLimit { limit: limits.inner_calls, usage }
    } else if message.contains("fail:") {
        EvalFailure::Fail { message, usage }
    } else {
        EvalFailure::Error { message, usage }
    }
}

/// Parse `source`, load it with inner dispatch disabled, then invoke its
/// zero-argument `main` with dispatch enabled, under every bound in
/// `limits`. `cancel` may be set from another thread at any time;
/// evaluation then stops at the next forward-progress check.
pub fn run_contract(
    source: &str,
    limits: &Limits,
    dispatcher: ToolDispatcher,
    cancel: Arc<AtomicBool>,
) -> Result<Success, EvalFailure> {
    if source.len() > limits.source_bytes {
        return Err(EvalFailure::SourceTooLarge { bytes: source.len(), limit: limits.source_bytes });
    }
    let ast = AstModule::parse("contract.star", source.to_owned(), &confined_dialect())
        .map_err(|e| EvalFailure::Parse { message: e.to_string() })?;
    let globals = confined_globals();
    let host = ToolHost {
        dispatcher: RefCell::new(dispatcher),
        calls: Cell::new(0),
        max_calls: limits.inner_calls,
        dispatch_enabled: Cell::new(false),
    };
    Module::with_temp_heap(|module| {
        let mut eval = Evaluator::new(&module);
        eval.extra = Some(&host);
        eval.set_max_tick_count(limits.steps).expect("tick limit set once");
        eval.set_max_heap_size(limits.heap_bytes).expect("heap limit set once");
        eval.set_max_callstack_size(limits.call_stack_depth).expect("call stack limit set once");
        eval.set_check_cancelled(Box::new(move || cancel.load(Ordering::Relaxed)));

        let usage_of = |eval: &Evaluator, calls: u64| Usage {
            steps: eval.get_total_tick_count(),
            heap_peak_bytes: eval.heap().peak_allocated_bytes(),
            inner_calls: calls,
        };

        if let Err(e) = eval.eval_module(ast, &globals) {
            let usage = usage_of(&eval, host.calls.get());
            return Err(classify(e.to_string(), limits, usage));
        }
        let Some(main_fn) = module.get("main") else {
            return Err(EvalFailure::NoMain);
        };
        host.dispatch_enabled.set(true);
        let result = eval.eval_function(main_fn, &[], &[]);
        host.dispatch_enabled.set(false);
        let usage = usage_of(&eval, host.calls.get());
        match result {
            Ok(v) => {
                let value = v.to_json_value().map_err(|e| EvalFailure::Error {
                    message: format!("main returned a non-JSON value: {e}"),
                    usage,
                })?;
                Ok(Success { value, usage })
            }
            Err(e) => Err(classify(e.to_string(), limits, usage)),
        }
    })
}

/// A dispatcher for tests and the size probe: answers every call with a
/// fixed JSON value.
pub fn fixed_dispatcher(result: serde_json::Value) -> ToolDispatcher {
    Box::new(move |_name, _args| Ok(result.clone()))
}
