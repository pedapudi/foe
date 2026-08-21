//! The episode runtime.
//!
//! This crate owns configuration, identity, grants, budget, the tool
//! registry, the agent loop, the inbox, spawning, teams, the executable
//! runner, the Landlock sandbox, and the host protocol. `docs/design.md`
//! states what each part guarantees.
//!
//! This file holds the contract types shared across crates: how a tool is
//! declared, what a tool receives when called, how a model transport is
//! driven, and how a configuration document is typed. Behavior lives in the
//! modules. Tool packs such as `foe-code` depend on this file alone.

#![forbid(unsafe_code)]

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub use foe_log as log;
pub use foe_log::{BlockedCode, Chunk, ContentBlock, Message, Outcome, StopReason, ToolCall, ToolSchema, Usage};

pub mod budget;
pub mod config;
pub mod exec;
pub mod grants;
pub mod harness_text;
pub mod identity;
pub mod inbox;
pub mod loop_;
pub mod protocol;
pub mod registry;
pub mod sandbox;
pub mod spawn;
pub mod team;
#[cfg(test)]
#[path = "fixtures_test.rs"]
mod test_util;
pub mod wiring;

// ---- tools --------------------------------------------------------------------

/// A tool's declared interaction with the world. The registry refuses a tool
/// whose effect the grants do not cover. At dispatch, the effect decides
/// which capability handles the tool receives and whether it may run
/// concurrently with its siblings.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Effect {
    /// Touches nothing outside its arguments. May run concurrently.
    Pure,
    /// Reads within the read roots. May run concurrently.
    Reads,
    /// Writes within the write roots. Runs alone, in issue order.
    Writes,
    /// Starts a process. Runs alone, in issue order.
    Execs,
    /// Starts a child episode. Runs alone, in issue order.
    Spawns,
}

impl Effect {
    pub fn concurrent(self) -> bool {
        matches!(self, Effect::Pure | Effect::Reads)
    }
}

/// What identity hashes and what the model sees. See docs/design.md "Tools".
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolSpec {
    pub name: String,
    pub description: String,
    /// Appended to the system prompt after the instructions, in `tools` order.
    pub instruction: Option<String>,
    /// JSON Schema for the arguments.
    pub params: serde_json::Value,
    pub effect: Effect,
}

impl ToolSpec {
    pub fn schema(&self) -> ToolSchema {
        ToolSchema { name: self.name.clone(), description: self.description.clone(), parameters: self.params.clone() }
    }
}

/// What a tool returns. The log stores `value`; the model sees `rendered`
/// when present and a compact rendering of `value` otherwise.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ToolValue {
    pub value: serde_json::Value,
    pub rendered: Option<String>,
    pub is_error: bool,
}

impl ToolValue {
    pub fn ok(value: serde_json::Value, rendered: impl Into<String>) -> Self {
        Self { value, rendered: Some(rendered.into()), is_error: false }
    }
    pub fn error(message: impl Into<String>) -> Self {
        let message = message.into();
        Self { value: serde_json::json!({ "error": message }), rendered: Some(message), is_error: true }
    }
}

/// Capability handles a tool may receive. Each is `Some` only when the
/// tool's declared effect entitles it and the grants cover it. A tool has
/// no other route to the filesystem, to processes, or to child episodes.
pub struct CallCtx {
    pub call_id: String,
    pub step: u32,
    pub reader: Option<Arc<dyn Reader>>,
    pub writer: Option<Arc<dyn Writer>>,
    pub executor: Option<Arc<dyn Executor>>,
    pub spawner: Option<Arc<dyn Spawner>>,
    /// Directory for output too large to inline; always present.
    pub spill_dir: PathBuf,
    /// Remaining wall-clock budget, when the episode has one.
    pub deadline: Option<std::time::Instant>,
}

/// Filesystem reads bounded to the read roots. Every path is canonicalized
/// and checked before use; a path outside the roots is an error.
pub trait Reader: Send + Sync {
    fn read(&self, path: &Path) -> Result<Vec<u8>, CapError>;
    fn metadata(&self, path: &Path) -> Result<std::fs::Metadata, CapError>;
    /// Walks `root` honoring ignore files. Yields canonical paths.
    fn walk(&self, root: &Path) -> Result<Box<dyn Iterator<Item = PathBuf> + Send>, CapError>;
    fn roots(&self) -> &[PathBuf];
}

/// Filesystem writes bounded to the write roots. `write` replaces the file
/// atomically: it stages beside the target and renames.
pub trait Writer: Send + Sync {
    fn write(&self, path: &Path, bytes: &[u8]) -> Result<(), CapError>;
    fn create_dir_all(&self, path: &Path) -> Result<(), CapError>;
    fn roots(&self) -> &[PathBuf];
}

/// Starts a process from a fixed argument vector, with a constructed
/// environment, standard input at `/dev/null`, and the sandbox narrowed.
/// Never invokes a shell on the caller's behalf.
pub trait Executor: Send + Sync {
    fn run(&self, req: ExecRequest) -> Result<ExecResult, CapError>;
}

#[derive(Debug, Clone)]
pub struct ExecRequest {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub cwd: PathBuf,
    pub env: BTreeMap<String, String>,
    pub timeout: std::time::Duration,
    pub network: bool,
    /// Bytes for standard input; `None` means `/dev/null`.
    pub stdin: Option<Vec<u8>>,
}

#[derive(Debug, Clone)]
pub struct ExecResult {
    /// `None` when the process was killed.
    pub exit_code: Option<i32>,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
    pub timed_out: bool,
    pub duration: std::time::Duration,
}

/// Starts child episodes bounded to the declared child programs.
pub trait Spawner: Send + Sync {
    fn spawn(&self, req: SpawnRequest) -> Result<SpawnHandle, CapError>;
}

#[derive(Debug, Clone)]
pub struct SpawnRequest {
    /// A key of `programs` in the configuration.
    pub program: String,
    pub task: String,
    pub context: foe_log::SpawnContext,
    /// What to reserve from the parent's remainder; an unset dimension
    /// takes the whole remainder. The spawner records what it granted.
    pub reserve: foe_log::BudgetAmount,
    /// The tool call that starts the child, recorded in `spawn/start`.
    pub call_id: String,
}

pub struct SpawnHandle {
    pub child_id: String,
    pub dir: PathBuf,
    /// Settles when the child's `episode/end` arrives. Clone it to wait in
    /// more than one place.
    pub run: spawn::ChildRun,
}

#[derive(Debug, thiserror::Error)]
pub enum CapError {
    #[error("{path}: outside every granted root")]
    Denied { path: PathBuf },
    #[error("{0}")]
    Io(#[from] std::io::Error),
    #[error("{0}")]
    Invalid(String),
}

/// A tool. Built-in tools, configured executables, and host tools all
/// implement this; the registry does not distinguish them after
/// construction.
#[async_trait::async_trait]
pub trait Tool: Send + Sync {
    fn spec(&self) -> &ToolSpec;
    async fn call(&self, args: serde_json::Value, ctx: &CallCtx) -> ToolValue;
}

// ---- transport ---------------------------------------------------------------

/// One model request as the transport sees it.
#[derive(Debug, Clone)]
pub struct ModelRequestBody {
    pub request_id: String,
    pub system: String,
    pub tools: Vec<ToolSchema>,
    pub messages: Vec<Message>,
    pub max_output_tokens: Option<u32>,
}

/// Drives a model. The built-in clients in `foe-transport` and the host
/// protocol both implement this. The runtime records every chunk it
/// receives; a transport never writes the log.
#[async_trait::async_trait]
pub trait Transport: Send + Sync {
    fn route(&self) -> foe_log::ModelRoute;
    /// Sends one request and delivers chunks in order to `sink`. Ends with a
    /// `Chunk::Done` or `Chunk::Error`. Returns when the last chunk is sent.
    async fn stream(&self, req: ModelRequestBody, sink: &mut (dyn ChunkSink + Send));
}

pub trait ChunkSink {
    fn push(&mut self, chunk: Chunk);
}

impl ChunkSink for Vec<Chunk> {
    fn push(&mut self, chunk: Chunk) {
        Vec::push(self, chunk)
    }
}

// ---- configuration -----------------------------------------------------------

/// The configuration document. Field for field, docs/config.md.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub version: u32,
    pub name: String,
    pub instructions: BTreeMap<String, String>,
    pub tools: Vec<String>,
    #[serde(default)]
    pub tool_defs: BTreeMap<String, ToolDef>,
    /// Tools the host implements over the protocol. The specification lives
    /// here so that identity is computable from the document alone.
    #[serde(default)]
    pub host_tools: BTreeMap<String, HostToolDef>,
    pub grants: Grants,
    pub budget: Budget,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub done_when: Option<DoneWhen>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<ModelConfig>,
    #[serde(default)]
    pub sandbox: SandboxConfig,
    #[serde(default)]
    pub programs: BTreeMap<String, ChildProgram>,
    pub task: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ToolDef {
    pub exec: PathBuf,
    pub description: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub instruction: Option<String>,
    #[serde(default)]
    pub network: bool,
    #[serde(default = "default_tool_timeout")]
    pub timeout_seconds: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cwd: Option<PathBuf>,
}

fn default_tool_timeout() -> u64 {
    120
}

/// A host-implemented tool as declared in the document. See docs/config.md
/// `host_tools`. The host supplies only the implementation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HostToolDef {
    pub description: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub instruction: Option<String>,
    pub params: serde_json::Value,
    pub effect: Effect,
}

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Grants {
    pub read: Vec<PathBuf>,
    #[serde(default)]
    pub write: Vec<PathBuf>,
    #[serde(default)]
    pub spawn: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Budget {
    pub model_calls: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tokens: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seconds: Option<u64>,
    #[serde(default = "default_max_depth")]
    pub max_depth: u32,
    #[serde(default = "default_max_episodes")]
    pub max_episodes: u32,
    #[serde(default = "default_max_concurrent")]
    pub max_concurrent: u32,
    #[serde(default = "default_loop_threshold")]
    pub loop_threshold: u32,
}

fn default_max_depth() -> u32 {
    1
}
fn default_max_episodes() -> u32 {
    8
}
fn default_max_concurrent() -> u32 {
    4
}
fn default_loop_threshold() -> u32 {
    3
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DoneWhen {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub verify: Option<String>,
    #[serde(default = "default_retries")]
    pub retries: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub returns: Option<serde_json::Value>,
}

fn default_retries() -> u32 {
    2
}

/// The `model` block. The provider name is opaque to this crate: which
/// names a build knows is decided where the transport is composed, and
/// `foe plan` reports the resolution. Every key other than the three named
/// fields is a provider-specific option, flat and string-valued, such as
/// `api_key_file`, `base_url`, `project`, or `exec`. The block does not
/// participate in identity.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelConfig {
    pub provider: String,
    pub model: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_output_tokens: Option<u32>,
    #[serde(flatten)]
    pub options: BTreeMap<String, String>,
}

impl ModelConfig {
    pub fn new(provider: impl Into<String>, model: impl Into<String>) -> Self {
        ModelConfig {
            provider: provider.into(),
            model: model.into(),
            max_output_tokens: None,
            options: BTreeMap::new(),
        }
    }

    /// One provider-specific option, when present.
    pub fn option(&self, key: &str) -> Option<&str> {
        self.options.get(key).map(String::as_str)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SandboxConfig {
    #[serde(default = "default_sandbox_mode")]
    pub mode: foe_log::SandboxMode,
}

impl Default for SandboxConfig {
    fn default() -> Self {
        Self { mode: foe_log::SandboxMode::BestEffort }
    }
}

fn default_sandbox_mode() -> foe_log::SandboxMode {
    foe_log::SandboxMode::BestEffort
}

/// A child program: a configuration without `version`, `task`, `model`, or
/// `sandbox`, which are inherited.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChildProgram {
    pub name: String,
    pub instructions: BTreeMap<String, String>,
    pub tools: Vec<String>,
    #[serde(default)]
    pub tool_defs: BTreeMap<String, ToolDef>,
    #[serde(default)]
    pub host_tools: BTreeMap<String, HostToolDef>,
    pub grants: Grants,
    pub budget: Budget,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub done_when: Option<DoneWhen>,
    #[serde(default)]
    pub programs: BTreeMap<String, ChildProgram>,
}

// ---- errors --------------------------------------------------------------------

/// Every construction error names the key and the rule. Construction fails
/// before any process starts and before any log is written.
#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("{key}: {rule}")]
    Invalid { key: String, rule: String },
    #[error("{0}")]
    Parse(#[from] serde_json::Error),
    #[error("{0}")]
    Io(#[from] std::io::Error),
}

#[derive(Debug, thiserror::Error)]
pub enum RuntimeError {
    #[error(transparent)]
    Config(#[from] ConfigError),
    #[error(transparent)]
    Log(#[from] foe_log::LogError),
    #[error(transparent)]
    Cap(#[from] CapError),
    #[error("protocol: {0}")]
    Protocol(String),
    #[error("sandbox: {0}")]
    Sandbox(String),
}
