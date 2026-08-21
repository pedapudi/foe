//! Fixtures shared by the tests of this crate: temporary directories, a
//! minimal resolved program, a scripted transport, probe tools that report
//! which handles they received, and a fake executor.

use crate::config::{resolve, Program};
use crate::{
    CallCtx, ChunkSink, Config, Effect, ExecRequest, ExecResult, Executor, ModelRequestBody, Tool, ToolSpec, ToolValue,
    Transport,
};
use foe_log::{Chunk, ModelRoute, StopReason, Usage};
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

pub fn tmp(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("foe-core-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// A valid document granting read and write on `root`, with `block` as its
/// only tool and the host transport.
pub fn config_value(root: &Path) -> Value {
    json!({
        "version": 1,
        "name": "fixture",
        "instructions": { "10-role": "You are a test agent.", "05-first": "Be brief." },
        "tools": ["block"],
        "grants": { "read": [root], "write": [root] },
        "budget": { "model_calls": 10 },
        "task": "do the thing"
    })
}

pub fn config(root: &Path) -> Config {
    serde_json::from_value(config_value(root)).unwrap()
}

pub fn program(root: &Path) -> Program {
    resolve(&config(root)).unwrap()
}

pub fn program_with(root: &Path, edit: impl FnOnce(&mut Value)) -> Result<Program, crate::ConfigError> {
    let mut value = config_value(root);
    edit(&mut value);
    let config: Config = serde_json::from_value(value)?;
    resolve(&config)
}

// ---- chunks ------------------------------------------------------------------

pub fn text(s: &str) -> Chunk {
    Chunk::Text { delta: s.into() }
}

pub fn done(stop: StopReason) -> Chunk {
    Chunk::Done { stop, usage: Usage { input: 100, output: 10, cache_read: 0 } }
}

pub fn call(id: &str, name: &str, args: &str) -> Vec<Chunk> {
    vec![
        Chunk::ToolCallStart { id: id.into(), name: name.into() },
        Chunk::ToolCallDelta { id: id.into(), delta: args.into() },
        Chunk::ToolCallEnd { id: id.into() },
    ]
}

/// A turn of text followed by tool calls and a `tool` stop.
pub fn turn(text_: &str, calls: Vec<Vec<Chunk>>) -> Vec<Chunk> {
    let mut chunks = vec![text(text_)];
    let stop = if calls.is_empty() { StopReason::End } else { StopReason::Tool };
    chunks.extend(calls.into_iter().flatten());
    chunks.push(done(stop));
    chunks
}

/// Replays scripted responses in order. When the script runs out, every
/// further request receives an empty final turn.
pub struct ScriptedTransport {
    pub responses: Mutex<VecDeque<Vec<Chunk>>>,
    pub requests: Mutex<Vec<ModelRequestBody>>,
}

impl ScriptedTransport {
    pub fn new(responses: Vec<Vec<Chunk>>) -> Self {
        Self { responses: Mutex::new(responses.into()), requests: Mutex::new(Vec::new()) }
    }
}

#[async_trait::async_trait]
impl Transport for ScriptedTransport {
    fn route(&self) -> ModelRoute {
        ModelRoute { provider: "test".into(), model: "scripted".into() }
    }

    async fn stream(&self, req: ModelRequestBody, sink: &mut (dyn ChunkSink + Send)) {
        self.requests.lock().unwrap().push(req);
        let chunks = self.responses.lock().unwrap().pop_front().unwrap_or_else(|| turn("", vec![]));
        for chunk in chunks {
            sink.push(chunk);
        }
    }
}

// ---- tools -------------------------------------------------------------------

pub fn spec(name: &str, effect: Effect) -> ToolSpec {
    ToolSpec {
        name: name.into(),
        description: format!("probe {name}"),
        instruction: None,
        params: json!({ "type": "object" }),
        effect,
    }
}

/// Reports which handles it received and when it ran, and sleeps for
/// `delay` so that concurrency is observable.
pub struct Probe {
    pub spec: ToolSpec,
    pub delay: Duration,
    pub runs: Mutex<Vec<(String, std::time::Instant, std::time::Instant)>>,
}

impl Probe {
    pub fn new(name: &str, effect: Effect) -> Self {
        Self { spec: spec(name, effect), delay: Duration::from_millis(0), runs: Mutex::new(Vec::new()) }
    }

    pub fn slow(name: &str, effect: Effect, ms: u64) -> Self {
        Self { delay: Duration::from_millis(ms), ..Self::new(name, effect) }
    }
}

#[async_trait::async_trait]
impl Tool for Probe {
    fn spec(&self) -> &ToolSpec {
        &self.spec
    }

    async fn call(&self, args: Value, ctx: &CallCtx) -> ToolValue {
        let started = std::time::Instant::now();
        tokio::time::sleep(self.delay).await;
        self.runs.lock().unwrap().push((ctx.call_id.clone(), started, std::time::Instant::now()));
        let value = json!({
            "reader": ctx.reader.is_some(), "writer": ctx.writer.is_some(),
            "executor": ctx.executor.is_some(), "spawner": ctx.spawner.is_some(),
            "args": args,
        });
        if let Some(big) = args.get("big").and_then(Value::as_u64) {
            return ToolValue::ok(value, "x".repeat(big as usize));
        }
        ToolValue::ok(value, format!("{} ran", self.spec.name))
    }
}

/// Returns a list of findings taken from its argument's `findings` field,
/// for verifier tests.
pub struct Verifier {
    pub spec: ToolSpec,
    pub findings: Mutex<VecDeque<Vec<String>>>,
}

#[async_trait::async_trait]
impl Tool for Verifier {
    fn spec(&self) -> &ToolSpec {
        &self.spec
    }

    async fn call(&self, _args: Value, _ctx: &CallCtx) -> ToolValue {
        let findings = self.findings.lock().unwrap().pop_front().unwrap_or_default();
        ToolValue::ok(json!(findings), "verified")
    }
}

/// Records every request and answers with its standard input echoed to
/// standard output, the arguments joined on standard error, and
/// `exit_code`, which is 0 by default.
#[derive(Default)]
pub struct FakeExecutor {
    pub requests: Mutex<Vec<ExecRequest>>,
    pub exit_code: i32,
}

impl Executor for FakeExecutor {
    fn run(&self, req: ExecRequest) -> Result<ExecResult, crate::CapError> {
        let stdout = req.stdin.clone().unwrap_or_default();
        let stderr = req.args.join(" ").into_bytes();
        self.requests.lock().unwrap().push(req);
        let exit_code = Some(self.exit_code);
        Ok(ExecResult { exit_code, stdout, stderr, timed_out: false, duration: Duration::from_millis(3) })
    }
}
