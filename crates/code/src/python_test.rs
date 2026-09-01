use super::*;
use crate::testing::{ctx_with_executor, Fixture};
use foe_core::exec::LocalExecutor;
use foe_core::log::SandboxMode;
use foe_core::sandbox::Sandbox;
use foe_core::RuntimeError;
use std::collections::VecDeque;
use std::sync::atomic::AtomicBool;
use std::sync::Mutex;

/// Records every inner call and answers from a canned queue; an ordinary
/// success once the queue is empty.
#[derive(Default)]
struct FakeComposer {
    calls: Mutex<Vec<(String, Value)>>,
    responses: Mutex<VecDeque<(Value, bool)>>,
}

#[async_trait::async_trait]
impl Composer for FakeComposer {
    async fn call(&self, name: &str, args: Value) -> Result<(Value, bool), RuntimeError> {
        self.calls.lock().unwrap().push((name.into(), args));
        Ok(self.responses.lock().unwrap().pop_front().unwrap_or((json!({ "ok": true }), false)))
    }
}

fn interpreter_present() -> bool {
    let present = std::path::Path::new(PYTHON_BIN).is_file();
    if !present {
        eprintln!("skipped: no interpreter at {PYTHON_BIN}");
    }
    present
}

async fn run_with(fx: &Fixture, sandbox: Sandbox, composer: Arc<FakeComposer>, args: Value) -> ToolValue {
    let cancel = Arc::new(AtomicBool::new(false));
    let executor = Arc::new(LocalExecutor::new(Arc::new(sandbox), Policy::default(), fx.root().join("spill"), cancel));
    let mut ctx = ctx_with_executor(fx, executor);
    ctx.composer = Some(composer);
    Python::new().call(args, &ctx).await
}

async fn run_source(source: &str, composer: Arc<FakeComposer>) -> ToolValue {
    let fx = Fixture::new();
    let sandbox = Sandbox::new(SandboxMode::Off).unwrap();
    run_with(&fx, sandbox, composer, json!({ "source": source })).await
}

#[tokio::test]
async fn source_composes_inner_calls_and_returns_a_derived_value() {
    if !interpreter_present() {
        return;
    }
    let composer = Arc::new(FakeComposer::default());
    composer.responses.lock().unwrap().extend([(json!({ "matches": 17 }), false), (json!({ "error": "no" }), true)]);
    let source = "def main():\n\
                  \x20   first = call_tool(\"grep\", {\"pattern\": \"x\"})\n\
                  \x20   second = call_tool(\"read\", {\"path\": \"gone\"})\n\
                  \x20   print(\"looked\")\n\
                  \x20   return {\"matches\": first[\"value\"][\"matches\"], \"read_failed\": second[\"is_error\"]}\n";
    let value = run_source(source, composer.clone()).await;
    assert!(!value.is_error, "{:?}", value.rendered);
    assert_eq!(value.value["returned"], json!({ "matches": 17, "read_failed": true }));
    let derivation = &value.value["derivation"];
    assert_eq!(derivation["complete"], json!(true));
    assert_eq!(derivation["inner_calls"], json!(2));
    assert_eq!(derivation["errors"], json!(1));
    assert_eq!(derivation["by_tool"], json!({ "grep": 1, "read": 1 }));
    assert_eq!(value.value["stdout"], json!("looked\n"), "the source's own output is a diagnostic");
    let recorded = composer.calls.lock().unwrap();
    assert_eq!(recorded[0].0, "grep");
    assert_eq!(recorded[1].1, json!({ "path": "gone" }));
    let subject = value.subject.unwrap();
    assert!(subject.contains("2 call(s), 1 error(s)"), "{subject}");
}

/// The process receives an empty environment. The interpreter's own locale
/// coercion may then set `LC_CTYPE` for itself; nothing else may appear,
/// and in particular nothing of the episode process's environment.
#[tokio::test]
async fn the_environment_is_empty() {
    if !interpreter_present() {
        return;
    }
    let source = "import os\ndef main():\n    return dict(os.environ)\n";
    let value = run_source(source, Arc::new(FakeComposer::default())).await;
    assert!(!value.is_error, "{:?}", value.rendered);
    let env = value.value["returned"].as_object().unwrap();
    assert!(env.keys().all(|key| key == "LC_CTYPE"), "{env:?}");
}

/// docs/code-mode.md "Confinement": the interpreter reads `/usr` alone, so
/// source that opens a workspace file fails where Landlock enforces.
#[tokio::test]
async fn a_workspace_open_is_denied_under_enforcement() {
    if !interpreter_present() {
        return;
    }
    let sandbox = Sandbox::new(SandboxMode::BestEffort).unwrap();
    if sandbox.abi() == 0 {
        eprintln!("skipped: the kernel offers no Landlock");
        return;
    }
    let fx = Fixture::new();
    fx.write("secret.txt", "s");
    let target = fx.root().join("secret.txt");
    let source = format!(
        "def main():\n    try:\n        open({:?})\n        return \"opened\"\n    except OSError:\n        return \"denied\"\n",
        target.to_str().unwrap()
    );
    let value = run_with(&fx, sandbox, Arc::new(FakeComposer::default()), json!({ "source": source })).await;
    assert!(!value.is_error, "{:?}", value.rendered);
    assert_eq!(value.value["returned"], json!("denied"));
}

#[tokio::test]
async fn fail_ends_the_call_as_an_error() {
    if !interpreter_present() {
        return;
    }
    let value = run_source("def main():\n    fail(\"not enough evidence\")\n", Arc::new(FakeComposer::default())).await;
    assert!(value.is_error);
    assert_eq!(value.value["error"]["message"], json!("not enough evidence"));
    assert_eq!(value.value["error"]["derivation"]["complete"], json!(false));
    assert_eq!(value.failure.unwrap().code, foe_core::ToolFailureCode::OperationFailed);
}

#[tokio::test]
async fn an_uncaught_exception_is_an_error_carrying_the_traceback() {
    if !interpreter_present() {
        return;
    }
    let value = run_source("def main():\n    return 1 // 0\n", Arc::new(FakeComposer::default())).await;
    assert!(value.is_error);
    let message = value.value["error"]["message"].as_str().unwrap();
    assert!(message.contains("ZeroDivisionError"), "{message}");
}

#[tokio::test]
async fn the_inner_call_bound_ends_the_source() {
    if !interpreter_present() {
        return;
    }
    let source = "def main():\n    for _ in range(150):\n        call_tool(\"t\", {})\n    return 0\n";
    let composer = Arc::new(FakeComposer::default());
    let value = run_source(source, composer.clone()).await;
    assert!(value.is_error);
    let message = value.value["error"]["message"].as_str().unwrap();
    assert!(message.contains(&PYTHON_INNER_CALL_MAX.to_string()), "{message}");
    assert_eq!(
        composer.calls.lock().unwrap().len() as u32,
        PYTHON_INNER_CALL_MAX,
        "no call past the bound is dispatched"
    );
}

#[tokio::test]
async fn the_memory_cap_ends_an_allocation() {
    if !interpreter_present() {
        return;
    }
    let source = "def main():\n    return len(\"a\" * (600 * 1024 * 1024))\n";
    let value = run_source(source, Arc::new(FakeComposer::default())).await;
    assert!(value.is_error);
    let message = value.value["error"]["message"].as_str().unwrap();
    assert!(message.contains("MemoryError"), "{message}");
}

#[tokio::test]
async fn the_timeout_kills_the_interpreter() {
    if !interpreter_present() {
        return;
    }
    let fx = Fixture::new();
    let sandbox = Sandbox::new(SandboxMode::Off).unwrap();
    let args = json!({ "source": "def main():\n    while True:\n        pass\n", "timeout_seconds": 1 });
    let value = run_with(&fx, sandbox, Arc::new(FakeComposer::default()), args).await;
    assert!(value.is_error);
    let message = value.value["error"]["message"].as_str().unwrap();
    assert!(message.contains("timed out"), "{message}");
    assert_eq!(value.failure.unwrap().code, foe_core::ToolFailureCode::TimedOut);
}

#[tokio::test]
async fn a_missing_interpreter_is_an_error_naming_the_path() {
    let fx = Fixture::new();
    let cancel = Arc::new(AtomicBool::new(false));
    let sandbox = Arc::new(Sandbox::new(SandboxMode::Off).unwrap());
    let executor = Arc::new(LocalExecutor::new(sandbox, Policy::default(), fx.root().join("spill"), cancel));
    let mut ctx = ctx_with_executor(&fx, executor);
    ctx.composer = Some(Arc::new(FakeComposer::default()));
    let mut tool = Python::new();
    tool.bin = "/nonexistent/python3".into();
    let value = tool.call(json!({ "source": "def main():\n    return 0\n" }), &ctx).await;
    assert!(value.is_error);
    assert!(value.rendered.unwrap().contains("/nonexistent/python3"));
}

#[tokio::test]
async fn the_source_bound_is_checked_before_the_interpreter_starts() {
    let source = format!("# {}\ndef main():\n    return 0\n", "x".repeat(PYTHON_SOURCE_MAX_BYTES));
    let value = run_source(&source, Arc::new(FakeComposer::default())).await;
    assert!(value.is_error);
    assert!(value.rendered.unwrap().contains(&PYTHON_SOURCE_MAX_BYTES.to_string()));
}

#[tokio::test]
async fn a_dispatch_without_a_composer_is_an_error() {
    let fx = Fixture::new();
    let cancel = Arc::new(AtomicBool::new(false));
    let sandbox = Arc::new(Sandbox::new(SandboxMode::Off).unwrap());
    let executor = Arc::new(LocalExecutor::new(sandbox, Policy::default(), fx.root().join("spill"), cancel));
    let ctx = ctx_with_executor(&fx, executor);
    let value = Python::new().call(json!({ "source": "def main():\n    return 0\n" }), &ctx).await;
    assert!(value.is_error);
    assert!(value.rendered.unwrap().contains("composer"));
}

/// docs/code-mode.md: one scripted episode whose model turn submits a
/// Python source composing several inner calls. The episode completes, the log
/// records each inner call and its result, derived messages carry the
/// outer result alone, and the folded log balances.
mod episode {
    use super::*;
    use foe_core::budget::Pool;
    use foe_core::grants::RootReader;
    use foe_core::log::{fold, EpisodeStart, EventData, ModelRoute, RuntimeInfo, SandboxInfo};
    use foe_core::loop_::{run, Log, Params};
    use foe_core::registry::{Handles, Registry};
    use foe_core::{Chunk, ChunkSink, ModelRequestBody, StopReason, Transport, Usage};

    struct Scripted(Mutex<VecDeque<Vec<Chunk>>>);

    #[async_trait::async_trait]
    impl Transport for Scripted {
        fn route(&self) -> ModelRoute {
            ModelRoute { provider: "test".into(), model: "scripted".into() }
        }

        async fn stream(&self, _req: ModelRequestBody, sink: &mut (dyn ChunkSink + Send)) {
            let done = Chunk::Done { stop: StopReason::End, usage: Usage::default() };
            for chunk in self.0.lock().unwrap().pop_front().unwrap_or_else(|| vec![done]) {
                sink.push(chunk);
            }
        }
    }

    #[tokio::test]
    async fn a_scripted_episode_composes_inner_calls_end_to_end() {
        if !super::interpreter_present() {
            return;
        }
        let fx = Fixture::new();
        fx.write("data.txt", "one\ntwo\nthree\n");
        let root = fx.root();
        let source = "def main():\n\
                       \x20   data = call_tool(\"read\", {\"path\": \"data.txt\"})\n\
                       \x20   if data[\"is_error\"]:\n\
                       \x20       fail(data[\"value\"][\"error\"])\n\
                       \x20   missing = call_tool(\"read\", {\"path\": \"missing.txt\"})\n\
                       \x20   return {\"lines\": len(data[\"value\"][\"content\"].splitlines()), \"missing\": missing[\"is_error\"]}\n";
        let config: foe_contract::ContractDocument = serde_json::from_value(json!({
            "version": 4, "name": "compose", "instructions": { "role": "compose" },
            "tools": ["python", "read"],
            "grants": { "read": [root], "write": [root] },
            "budget": { "model_calls": 4 }, "task": "count the lines"
        }))
        .unwrap();
        let resolved = foe_contract::document::resolve(&config).unwrap();
        let registry = Arc::new(Registry::new(&resolved, vec![], crate::all()).unwrap());
        let args = json!({ "source": source }).to_string();
        let turn = vec![
            Chunk::ToolCallStart { id: "tc_py".into(), name: "python".into() },
            Chunk::ToolCallDelta { id: "tc_py".into(), delta: args },
            Chunk::ToolCallEnd { id: "tc_py".into() },
            Chunk::Done { stop: StopReason::Tool, usage: Usage::default() },
        ];
        let dir = root.join("episode");
        std::fs::create_dir_all(&dir).unwrap();
        let log = Arc::new(Log::create_or_open(&dir, None).unwrap());
        let cancel = Arc::new(AtomicBool::new(false));
        let sandbox = Arc::new(Sandbox::new(SandboxMode::Off).unwrap());
        let executor = LocalExecutor::new(sandbox, Policy::default(), dir.join("spill"), cancel);
        let handles = Handles {
            reader: Some(Arc::new(RootReader::new(vec![root.clone()]).unwrap())),
            executor: Some(Arc::new(executor)),
            ..Handles::default()
        };
        let (_stop, stop_rx) = tokio::sync::watch::channel(None);
        let start = EpisodeStart {
            id: "ep_compose".into(),
            parent_id: None,
            fork_origin: None,
            team_id: None,
            contract: resolved.to_value(),
            contract_fingerprint: "sha256:test".into(),
            task: "count the lines".into(),
            runtime: RuntimeInfo { version: "0".into(), build: "unknown".into() },
            sandbox: SandboxInfo {
                mode: SandboxMode::Off,
                landlock_abi: 0,
                resolved_permissions: Default::default(),
                process_boundary: Default::default(),
            },
            effective_budget: None,
        };
        let outcome = run(Params {
            log: log.clone(),
            start,
            contract: resolved.clone(),
            registry,
            handles,
            transport: Arc::new(Scripted(Mutex::new(VecDeque::from([turn])))),
            pool: Arc::new(std::sync::Mutex::new(Pool::new(resolved.budget.clone()))),
            stop: stop_rx,
            children: None,
            sessions: None,
            context: None,
        })
        .await
        .unwrap();
        assert!(matches!(outcome, foe_core::Outcome::Completed { .. }), "{outcome:?}");
        let events = fold::read_all(&dir).unwrap();
        fold::fold(&events).expect("the log balances");
        let inner: Vec<&str> = events
            .iter()
            .filter_map(|e| match &e.data {
                EventData::ToolInnerCall(c) => Some(c.call_id.as_str()),
                _ => None,
            })
            .collect();
        assert_eq!(inner, ["tc_py_0", "tc_py_1"]);
        let outer = events
            .iter()
            .find_map(|e| match &e.data {
                EventData::ToolResult(r) if r.call_id == "tc_py" => Some(r.value.clone()),
                _ => None,
            })
            .unwrap();
        assert_eq!(outer["returned"], json!({ "lines": 3, "missing": true }), "{outer}");
        assert_eq!(outer["derivation"]["inner_calls"], json!(2));
        let EventData::ModelRequest(last) =
            &events.iter().rev().find(|e| matches!(e.data, EventData::ModelRequest(_))).unwrap().data
        else {
            panic!()
        };
        let tool_messages: Vec<&str> = last
            .messages
            .iter()
            .filter_map(|m| match m {
                foe_core::Message::Tool { call_id, .. } => Some(call_id.as_str()),
                _ => None,
            })
            .collect();
        assert_eq!(tool_messages, ["tc_py"], "the outer result alone reaches the model");
    }
}
