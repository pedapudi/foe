//! The episode runtime.
//!
//! This crate owns grants, budget, the tool registry, the agent loop, the
//! inbox, spawning, the executable runner, the Landlock sandbox, and
//! the host protocol. What a contract is — the contract document, its
//! resolution, and fingerprint — is `foe-contract`, which this crate reads.
//! `docs/design.md` states what each part guarantees.
//!
//! This file holds the runtime contract types shared across crates: what a
//! tool receives when it is called, what it returns, and how a model
//! transport is driven. Behavior lives in the modules. Tool packs such as
//! `foe-code` depend on this file and on `foe-contract`.

#![forbid(unsafe_code)]

use foe_contract::{ContractError, ToolSpec};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub use foe_log as log;
pub use foe_log::{
    BlockedCode, Chunk, ContentBlock, Message, Outcome, StopReason, ToolCall, ToolFailure, ToolFailureCode, ToolSchema,
    Usage,
};

pub mod budget;
pub mod captured_executable;
pub mod confine;
pub mod context;
pub mod exec;
pub mod executable_support;
pub mod fingerprint;
pub mod grants;
pub mod inbox;
pub mod loop_;
pub mod process_boundary;
pub mod protocol;
pub mod registry;
pub mod result_budget;
pub mod retrieval;
pub mod sandbox;
pub mod session;
pub mod spawn;
#[cfg(test)]
#[path = "fixtures_test.rs"]
mod test_util;
pub mod wiring;

// ---- tools --------------------------------------------------------------------

/// How many lines from `lines`, in the order given, fit within `max_lines`
/// lines and `max_chars` characters, and how many characters they take.
/// Each line counts the newline that follows it, and is taken whole or not
/// at all, so a cut on this boundary never splits a character. Every bound
/// on how much of a result the model sees is measured this way.
pub fn fitting<'a>(lines: impl Iterator<Item = &'a &'a str>, max_lines: usize, max_chars: usize) -> (usize, usize) {
    let (mut kept, mut used) = (0, 0);
    for line in lines.take(max_lines) {
        let width = line.chars().count() + 1;
        if used + width > max_chars {
            break;
        }
        used += width;
        kept += 1;
    }
    (kept, used)
}

/// Longest a `subject` may be. Anything past it is cut where it is written.
pub const SUBJECT_MAX: usize = 120;

/// What a tool returns. The log stores `value`; the model sees `rendered`
/// when present and a compact rendering of `value` otherwise. A failed
/// call carries one machine-readable classification in `failure`.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ToolValue {
    pub value: serde_json::Value,
    pub rendered: Option<String>,
    pub is_error: bool,
    pub failure: Option<Box<ToolFailure>>,
    /// One line naming what this call acted on and what came of it, which
    /// the tool writes from what it did. `rendered` is what the model
    /// received; this is what a person reads in a list, and it never
    /// reaches the model. See docs/tools.md.
    pub subject: Option<String>,
}

impl ToolValue {
    pub fn ok(value: serde_json::Value, rendered: impl Into<String>) -> Self {
        Self { value, rendered: Some(rendered.into()), is_error: false, failure: None, subject: None }
    }
    /// The message is the subject too: it already names what failed, which
    /// is the one line a reader scanning for the failure wants.
    pub fn error(message: impl Into<String>) -> Self {
        Self::failed(ToolFailureCode::OperationFailed, message, true, serde_json::json!({}))
    }
    pub fn failed(
        code: ToolFailureCode,
        message: impl Into<String>,
        retryable: bool,
        details: serde_json::Value,
    ) -> Self {
        let message = message.into();
        let failure = ToolFailure { code, message: message.clone(), retryable, details };
        let value = serde_json::json!({ "error": &message });
        Self { value, rendered: Some(message.clone()), is_error: true, failure: Some(Box::new(failure)), subject: None }
            .subject(message)
    }
    pub fn invalid(message: impl Into<String>) -> Self {
        Self::failed(ToolFailureCode::InvalidCall, message, true, serde_json::json!({}))
    }
    pub fn unavailable(message: impl Into<String>) -> Self {
        Self::failed(ToolFailureCode::Unavailable, message, false, serde_json::json!({}))
    }
    pub fn from_cap_error(context: &str, error: CapError) -> Self {
        let message = match &error {
            CapError::Denied { path } => format!(
                "{context}: {} is outside this tool's filesystem permissions; review grants.read and grants.write",
                path.display()
            ),
            _ => format!("{context}: {error}"),
        };
        match error {
            CapError::Denied { path } => {
                Self::failed(ToolFailureCode::CapabilityDenied, message, false, serde_json::json!({ "path": path }))
            }
            CapError::CapabilityDenied(capability) => Self::failed(
                ToolFailureCode::CapabilityDenied,
                message,
                false,
                serde_json::json!({ "capability": capability }),
            ),
            CapError::Budget { limit, .. } => {
                Self::failed(ToolFailureCode::BudgetExhausted, message, false, serde_json::json!({ "limit": limit }))
            }
            CapError::ProcessStart(reason) => Self::failed(
                ToolFailureCode::ProcessStartFailed,
                message,
                false,
                serde_json::json!({ "reason": reason }),
            ),
            CapError::Log(error) => Self::failed(
                ToolFailureCode::Unavailable,
                message,
                false,
                serde_json::json!({ "log": error.to_string() }),
            ),
            CapError::Io(error) => Self::failed(
                ToolFailureCode::OperationFailed,
                message,
                true,
                serde_json::json!({ "io_kind": format!("{:?}", error.kind()) }),
            ),
            CapError::Invalid(reason) => {
                Self::failed(ToolFailureCode::OperationFailed, message, true, serde_json::json!({ "reason": reason }))
            }
        }
    }
    /// Records what the call acted on, held to one line of [`SUBJECT_MAX`].
    /// A line past the limit ends in an ellipsis, so a cut is never silent.
    pub fn subject(mut self, subject: impl Into<String>) -> Self {
        let line = subject.into().replace(['\n', '\t'], " ");
        let cut: String = line.chars().take(SUBJECT_MAX - 1).collect();
        self.subject = Some(if line.chars().count() > SUBJECT_MAX { cut + "…" } else { line });
        self
    }
}

/// Name of the one composing tool, implemented in `foe-code`. The agent
/// loop builds a [`Composer`] for a call with this name and for no other,
/// so no further tool can reach inner dispatch.
pub const COMPOSING_TOOL: &str = "compose_tools";

/// Dispatches inner tool calls on behalf of the composing tool, recording
/// each as a `tool/inner-call` event and its ordinary `tool/result`. The
/// registry remains the only path from an inner call to an effect.
#[async_trait::async_trait]
pub trait Composer: Send + Sync {
    /// One inner dispatch. Returns the canonical value and whether it is
    /// an error. `Err` means the log refused an append; the outer call
    /// then ends as an error.
    async fn call(&self, name: &str, args: serde_json::Value) -> Result<(serde_json::Value, bool), RuntimeError>;
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
    pub sessions: Option<Arc<dyn Sessions>>,
    /// Present only for the call the agent loop recognizes as the
    /// [`COMPOSING_TOOL`].
    pub composer: Option<Arc<dyn Composer>>,
    /// Directory for output too large to inline; always present.
    pub spill_dir: PathBuf,
    /// Remaining wall-clock budget, when the episode has one.
    pub deadline: Option<std::time::Instant>,
}

/// Filesystem reads bounded to descriptor-held read roots. A path outside
/// those roots is an error.
pub trait Reader: Send + Sync {
    /// Opens a file through the descriptor-bound root. Streaming consumers
    /// use this operation so their memory does not grow with the file size.
    fn open(&self, path: &Path) -> Result<Box<dyn std::io::Read + Send>, CapError>;
    fn metadata(&self, path: &Path) -> Result<std::fs::Metadata, CapError>;
    /// Enumerates one directory through the same descriptor-bound root.
    fn read_dir(&self, path: &Path) -> Result<Vec<ReadEntry>, CapError>;
    fn roots(&self) -> &[PathBuf];
}

/// One entry observed through a [`Reader`]. Other file types, including
/// symbolic links, have both type fields false.
pub struct ReadEntry {
    pub path: PathBuf,
    pub is_file: bool,
    pub is_dir: bool,
}

/// Filesystem writes bounded to the write roots. `write` replaces the file
/// atomically: it stages beside the target and renames.
pub trait Writer: Send + Sync {
    fn write(&self, path: &Path, bytes: &[u8]) -> Result<(), CapError>;
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
    pub command: PathBuf,
    /// Construction-committed bytes for configured tools and executable
    /// transports. Ordinary subprocess calls leave this unset.
    pub captured_executable: Option<Arc<captured_executable::CapturedExecutable>>,
    pub args: Vec<String>,
    pub cwd: PathBuf,
    pub env: BTreeMap<String, String>,
    pub timeout: std::time::Duration,
    pub network: bool,
    /// Bytes for standard input; `None` means `/dev/null`.
    pub stdin: Option<Vec<u8>>,
    /// Replaces the narrowing the executor would derive from `command`.
    /// The `compose_tools` tool confines its interpreter with a policy of its
    /// own; every other request leaves this unset.
    pub policy: Option<sandbox::Policy>,
    /// File descriptors the child receives, each at the number given. The
    /// executor duplicates each descriptor for the child; the caller's
    /// copy closes when the request is dropped, at the end of the run.
    pub pass_fds: Vec<(i32, Arc<std::os::fd::OwnedFd>)>,
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

/// Supervises process sessions: processes that outlive the call that
/// started them. The `session` tool reaches such processes only through
/// this handle, as `bash` reaches processes only through [`Executor`].
pub trait Sessions: Send + Sync {
    /// Starts a session in its own process group. An implementation bounds
    /// how many sessions may be alive at once.
    fn start(&self, req: SessionRequest) -> Result<SessionStatus, CapError>;
    /// The session's state, and every output byte captured since the last
    /// take, both streams drained.
    fn take_output(&self, id: u64) -> Result<(SessionStatus, SessionOutput), CapError>;
    /// Writes bytes to the session's standard input.
    fn write_stdin(&self, id: u64, bytes: &[u8]) -> Result<SessionStatus, CapError>;
    /// Sends a signal named in the form `SIGINT` to the process group.
    fn signal(&self, id: u64, signal: &str) -> Result<SessionStatus, CapError>;
    /// Ends the session: SIGTERM to the group, a grace wait, then SIGKILL.
    /// Returns the final status; stopping an ended session returns its
    /// status again.
    fn stop(&self, id: u64) -> Result<SessionStatus, CapError>;
    /// Settles every alive session. Episode-lifetime sessions stop. A task-
    /// lifetime session is released to the enclosing task environment.
    fn settle(&self) -> Vec<SessionSettlement>;
    /// The final status of every session whose end no earlier call
    /// reported, each session's once per lifetime. The agent loop turns
    /// these into `session`-source inbox items. Default: no exit reports.
    fn take_exited(&self) -> Vec<SessionStatus> {
        Vec::new()
    }
}

/// What starts a session: the command, arguments, environment, and working
/// directory it runs with, and the short name results call it by. The
/// network is closed, as for every executable.
#[derive(Debug, Clone)]
pub struct SessionRequest {
    /// One word naming the process in subjects, such as `postgres`.
    pub name: String,
    pub command: PathBuf,
    pub args: Vec<String>,
    pub env: BTreeMap<String, String>,
    pub cwd: PathBuf,
    pub lifetime: SessionLifetime,
}

/// How long the runtime owns a process session. Episode is the default.
/// Task requires explicit contract permission and transfers ownership at
/// settlement to the environment that owns the foe invocation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SessionLifetime {
    Episode,
    Task,
}

/// What settlement did with a process group that had a live member. The
/// leader and group ids identify a released task session for external cleanup.
#[derive(Debug, Clone, PartialEq)]
pub struct SessionSettlement {
    pub status: SessionStatus,
    pub pid: u32,
    pub process_group: i32,
    pub released_to_task: bool,
}

/// A session's state: whether the process group is alive, the exit code
/// once it is not — `None` when a signal ended it — and the whole seconds
/// from the start to now or to the end.
#[derive(Debug, Clone, PartialEq)]
pub struct SessionStatus {
    pub id: u64,
    pub name: String,
    pub alive: bool,
    pub exit_code: Option<i32>,
    pub seconds: u64,
}

/// Output taken from a session: each stream's bytes since the last take.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct SessionOutput {
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
}

/// This episode's own log, as the lead of its team: appends team events and
/// reads everything written so far. The team coordinator writes through it.
pub trait LeadLog: Send + Sync {
    fn append(&self, event: foe_log::EventData);
    fn events(&self) -> Vec<foe_log::Event>;
}

/// Starts child episodes limited to the declared child contracts.
pub trait Spawner: Send + Sync {
    /// Allocates an identifier without reserving budget or starting work.
    fn allocate_id(&self) -> String;
    fn launch(&self, child_id: String, req: SpawnRequest) -> Result<SpawnHandle, CapError>;
    fn spawn(&self, req: SpawnRequest) -> Result<SpawnHandle, CapError> {
        self.launch(self.allocate_id(), req)
    }
}

#[derive(Debug, Clone)]
pub struct SpawnRequest {
    /// A key of `child_contracts` in the configuration.
    pub contract: String,
    pub task: String,
    pub context: foe_log::SpawnContext,
    /// What to reserve from the parent's remainder. An unset dimension
    /// takes the amount the child contract declares, and the whole
    /// remainder when the contract declares none. The spawner records what
    /// it granted.
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
    #[error("capability denied: {0}")]
    CapabilityDenied(String),
    #[error("{0}")]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Log(#[from] foe_log::LogError),
    #[error("the {name} budget limit leaves no room for a child")]
    Budget { limit: foe_log::ExhaustedLimit, name: String },
    #[error("{0}")]
    ProcessStart(String),
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

// ---- errors --------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
pub enum RuntimeError {
    #[error(transparent)]
    Contract(#[from] ContractError),
    #[error(transparent)]
    Log(#[from] foe_log::LogError),
    #[error(transparent)]
    Cap(#[from] CapError),
    #[error("protocol: {0}")]
    Protocol(String),
    #[error("sandbox: {0}")]
    Sandbox(String),
}
