//! Fixtures shared by the tests of this crate: temporary directories, a
//! minimal resolved contract, a scripted transport, probe tools that report
//! which handles they received, and a fake executor.

use crate::{CallCtx, ChunkSink, ExecRequest, ExecResult, Executor, ModelRequestBody, Tool, ToolValue, Transport};
use foe_contract::document::{resolve, ResolvedContract};
use foe_contract::{ContractDocument, Effect, ToolSpec};
use foe_log::{Chunk, ModelRoute, StopReason, Usage};
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::ops::Deref;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

pub struct ScratchDir {
    dir: Option<tempfile::TempDir>,
}

impl ScratchDir {
    fn new(prefix: &str, name: &str) -> Self {
        assert_eq!(Path::new(name).file_name(), Some(name.as_ref()), "scratch name must be one path component");
        let dir = tempfile::Builder::new().prefix(&format!("{prefix}-{name}-")).tempdir().unwrap();
        Self { dir: Some(dir) }
    }

    fn keep(mut self) -> PathBuf {
        let mut dir = self.dir.take().unwrap();
        dir.disable_cleanup(true);
        dir.path().to_path_buf()
    }

    fn path(&self) -> &Path {
        self.dir.as_ref().unwrap().path()
    }
}

impl Deref for ScratchDir {
    type Target = Path;

    fn deref(&self) -> &Self::Target {
        self.path()
    }
}

impl AsRef<Path> for ScratchDir {
    fn as_ref(&self) -> &Path {
        self.path()
    }
}

impl serde::Serialize for ScratchDir {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serde::Serialize::serialize(self.path(), serializer)
    }
}

impl Drop for ScratchDir {
    fn drop(&mut self) {
        let Some(mut dir) = self.dir.take() else { return };
        if std::thread::panicking() {
            eprintln!("retained failed test directory: {}", dir.path().display());
            dir.disable_cleanup(true);
            return;
        }
        let path = dir.path().to_path_buf();
        dir.close().unwrap_or_else(|error| panic!("failed to remove test directory {}: {error}", path.display()));
    }
}

pub fn tmp(name: &str) -> ScratchDir {
    ScratchDir::new("foe-core", name)
}

#[test]
fn scratch_directories_are_unique_and_removed_after_success() {
    let first = tmp("scratch-cleanup");
    let first_path = first.to_path_buf();
    let second = tmp("scratch-cleanup");
    assert_ne!(first.as_ref(), second.as_ref());
    drop(first);
    assert!(!first_path.exists());
}

#[test]
fn scratch_directories_are_retained_during_unwinding_and_when_requested() {
    let (send, receive) = std::sync::mpsc::channel();
    assert!(std::thread::spawn(move || {
        let dir = tmp("scratch-failure");
        send.send(dir.to_path_buf()).unwrap();
        panic!("injected failure");
    })
    .join()
    .is_err());
    let failed = receive.recv().unwrap();
    assert!(failed.is_dir());
    std::fs::remove_dir_all(failed).unwrap();

    let kept = tmp("scratch-kept").keep();
    assert!(kept.is_dir());
    std::fs::remove_dir_all(kept).unwrap();
}

#[cfg(unix)]
#[test]
fn scratch_cleanup_does_not_follow_a_replacement_symlink() {
    use std::os::unix::fs::symlink;

    let target = tmp("scratch-symlink-target");
    std::fs::write(target.join("marker"), "present").unwrap();
    let owned = tmp("scratch-symlink-owned");
    std::fs::remove_dir_all(&*owned).unwrap();
    symlink(&*target, &*owned).unwrap();
    drop(owned);
    assert_eq!(std::fs::read_to_string(target.join("marker")).unwrap(), "present");
}

/// A valid document granting read and write on `root`, with `block` as its
/// only tool and the host transport.
pub fn config_value(root: &Path) -> Value {
    json!({
        "version": 4,
        "name": "fixture",
        "instructions": { "10-role": "You are a test agent.", "05-first": "Be brief." },
        "tools": ["block"],
        "grants": { "read": [root], "write": [root] },
        "budget": { "model_calls": 10 },
        "task": "do the thing"
    })
}

pub fn contract_with(
    root: &Path,
    edit: impl FnOnce(&mut Value),
) -> Result<ResolvedContract, foe_contract::ContractError> {
    let mut value = config_value(root);
    edit(&mut value);
    let config: ContractDocument = serde_json::from_value(value)?;
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
            let text = "x".repeat(big as usize);
            return ToolValue::ok(json!({ "big": text }), text);
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

/// Records every request and answers with `stdout`, or with its standard
/// input echoed back when `stdout` is unset, the arguments joined on
/// standard error, and `exit_code`, which is 0 by default.
#[derive(Default)]
pub struct FakeExecutor {
    pub requests: Mutex<Vec<ExecRequest>>,
    pub exit_code: i32,
    /// Written to standard output in place of the echoed input, for a
    /// verifier whose findings are the subject.
    pub stdout: Option<String>,
}

impl Executor for FakeExecutor {
    fn run(&self, req: ExecRequest) -> Result<ExecResult, crate::CapError> {
        let stdout = match &self.stdout {
            Some(text) => text.clone().into_bytes(),
            None => req.stdin.clone().unwrap_or_default(),
        };
        let stderr = req.args.join(" ").into_bytes();
        self.requests.lock().unwrap().push(req);
        let exit_code = Some(self.exit_code);
        Ok(ExecResult { exit_code, stdout, stderr, timed_out: false, duration: Duration::from_millis(3) })
    }
}
