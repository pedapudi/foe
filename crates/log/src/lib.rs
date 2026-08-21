//! The append-only episode log.
//!
//! Every episode writes one log. The log is the source of truth for the
//! model's request history, the viewer, replay, forking, budget accounting,
//! and team state. `docs/log-format.md` specifies the format; this crate
//! implements it and nothing else. It depends on serde alone so that any
//! program can read a log without taking on the runtime.
//!
//! The types in this file are the contract between crates. Their shapes
//! follow the specification field for field. Behavior lives in the sibling
//! modules: [`append`] writes, [`fold`] reads, [`seed`] copies a prefix.

#![forbid(unsafe_code)]

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub mod append;
pub mod fold;
pub mod seed;

/// The log format version this crate writes and reads.
pub const LOG_VERSION: u32 = 1;

/// One line of the log.
///
/// `seq` starts at 0 and is contiguous. `time` is milliseconds since the
/// Unix epoch. `data` carries the payload for `kind`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Event {
    pub seq: u64,
    pub time: i64,
    #[serde(flatten)]
    pub data: EventData,
}

/// Every event type, implemented or reserved.
///
/// The `type` field on the wire is the variant's `serde(rename)`. Reserved
/// variants exist so that a version 1 reader can parse logs written by a
/// later version; nothing in version 1 emits them.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", content = "data")]
pub enum EventData {
    // ---- lifecycle -------------------------------------------------------
    #[serde(rename = "episode/start")]
    EpisodeStart(EpisodeStart),
    #[serde(rename = "episode/end")]
    EpisodeEnd { outcome: Outcome },
    /// Marks the end of events copied from another log. Data is empty.
    #[serde(rename = "seed/end")]
    SeedEnd {},

    // ---- requests --------------------------------------------------------
    #[serde(rename = "request/header")]
    RequestHeader(RequestHeader),
    #[serde(rename = "model/request")]
    ModelRequest(ModelRequest),
    #[serde(rename = "request/retry")]
    RequestRetry { step: u32, attempt: u32, cause: RetryCause, delay_ms: u64 },

    // ---- assistant output -----------------------------------------------
    #[serde(rename = "assistant/chunk")]
    AssistantChunk { step: u32, request_id: String, chunk: Chunk },
    #[serde(rename = "assistant/message")]
    AssistantMessage(AssistantMessage),

    // ---- tools -----------------------------------------------------------
    #[serde(rename = "tool/result")]
    ToolResult(ToolResult),
    /// A tool call that resolves to a host tool. The host answers with a
    /// `tool/result` line over the protocol.
    #[serde(rename = "host/tool-call")]
    HostToolCall { step: u32, call_id: String, name: String, args: serde_json::Value },

    // ---- inbox -----------------------------------------------------------
    #[serde(rename = "inbox/item")]
    InboxItem(InboxItem),

    // ---- budget and spawn -----------------------------------------------
    #[serde(rename = "budget/reserve")]
    BudgetReserve { child_id: String, reserved: BudgetAmount },
    #[serde(rename = "budget/release")]
    BudgetRelease { child_id: String, spent: BudgetAmount },
    #[serde(rename = "spawn/start")]
    SpawnStart { child_id: String, program: String, context: SpawnContext, call_id: String },
    #[serde(rename = "spawn/end")]
    SpawnEnd { child_id: String, outcome: Outcome },

    // ---- teams (lead log only) ------------------------------------------
    #[serde(rename = "team/roster")]
    TeamRoster { member_id: String, name: String, description: String, phase: MemberPhase },
    #[serde(rename = "team/message")]
    TeamMessage { message_id: String, from: String, to: String, content: Vec<ContentBlock> },
    #[serde(rename = "team/delivered")]
    TeamDelivered { message_id: String, to: String },
    /// Reserved for a shared task board.
    #[serde(rename = "team/task")]
    TeamTask(serde_json::Value),

    // ---- sandbox ---------------------------------------------------------
    #[serde(rename = "sandbox/denied")]
    SandboxDenied { pid: u32, comm: String, path: String, access: String },

    // ---- reserved --------------------------------------------------------
    #[serde(rename = "compaction/start")]
    CompactionStart(serde_json::Value),
    #[serde(rename = "compaction/summary")]
    CompactionSummary(serde_json::Value),
    #[serde(rename = "compaction/end")]
    CompactionEnd(serde_json::Value),
    #[serde(rename = "workflow/node-start")]
    WorkflowNodeStart(serde_json::Value),
    #[serde(rename = "workflow/node-end")]
    WorkflowNodeEnd(serde_json::Value),
    #[serde(rename = "workflow/recovery")]
    WorkflowRecovery(serde_json::Value),
}

impl EventData {
    /// The wire name of this event's type, as it appears in `"type"`.
    pub fn type_name(&self) -> &'static str {
        match self {
            EventData::EpisodeStart(_) => "episode/start",
            EventData::EpisodeEnd { .. } => "episode/end",
            EventData::SeedEnd {} => "seed/end",
            EventData::RequestHeader(_) => "request/header",
            EventData::ModelRequest(_) => "model/request",
            EventData::RequestRetry { .. } => "request/retry",
            EventData::AssistantChunk { .. } => "assistant/chunk",
            EventData::AssistantMessage(_) => "assistant/message",
            EventData::ToolResult(_) => "tool/result",
            EventData::HostToolCall { .. } => "host/tool-call",
            EventData::InboxItem(_) => "inbox/item",
            EventData::BudgetReserve { .. } => "budget/reserve",
            EventData::BudgetRelease { .. } => "budget/release",
            EventData::SpawnStart { .. } => "spawn/start",
            EventData::SpawnEnd { .. } => "spawn/end",
            EventData::TeamRoster { .. } => "team/roster",
            EventData::TeamMessage { .. } => "team/message",
            EventData::TeamDelivered { .. } => "team/delivered",
            EventData::TeamTask(_) => "team/task",
            EventData::SandboxDenied { .. } => "sandbox/denied",
            EventData::CompactionStart(_) => "compaction/start",
            EventData::CompactionSummary(_) => "compaction/summary",
            EventData::CompactionEnd(_) => "compaction/end",
            EventData::WorkflowNodeStart(_) => "workflow/node-start",
            EventData::WorkflowNodeEnd(_) => "workflow/node-end",
            EventData::WorkflowRecovery(_) => "workflow/recovery",
        }
    }
}

// ---- lifecycle payloads -----------------------------------------------------

/// Payload of `episode/start`. Always `seq` 0, exactly one per log.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EpisodeStart {
    pub id: String,
    pub parent_id: Option<String>,
    pub fork_origin: Option<ForkOrigin>,
    pub team_id: Option<String>,
    /// The resolved configuration with `task` removed.
    pub program: serde_json::Value,
    /// `sha256:<hex>` over the program; see docs/design.md "Programs and identity".
    pub identity: String,
    pub task: String,
    pub runtime: RuntimeInfo,
    pub sandbox: SandboxInfo,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ForkOrigin {
    pub episode_id: String,
    pub seq: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RuntimeInfo {
    pub version: String,
    /// `sha256:<hex>` of the running binary, or `unknown` when unavailable.
    pub build: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SandboxInfo {
    pub mode: SandboxMode,
    /// 0 when Landlock was unavailable.
    pub landlock_abi: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum SandboxMode {
    BestEffort,
    Required,
    Off,
}

/// The one outcome of an episode. See docs/design.md "The episode".
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Outcome {
    Completed { value: serde_json::Value },
    Blocked { code: BlockedCode, message: String },
    Exhausted { limit: ExhaustedLimit },
    Failed { error: String },
}

/// Closed vocabulary of blocking conditions. A supervising episode routes on
/// these. See docs/log-format.md "Blocked codes".
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum BlockedCode {
    LoopingToolCall,
    LoopingReasoning,
    GoalUnreachable,
    AmbiguousTask,
    MissingCapability,
    VerificationUnsatisfiable,
    ChildBlocked,
    RecoveryExhausted,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExhaustedLimit {
    ModelCalls,
    Tokens,
    Seconds,
    Depth,
    Episodes,
    Concurrency,
}

// ---- request payloads --------------------------------------------------------

/// The slowly-changing part of a request. Written with reason `initial`
/// before the first request and with reason `change` before any request
/// whose header differs from the previous one.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RequestHeader {
    pub reason: HeaderReason,
    pub system: String,
    pub tools: Vec<ToolSchema>,
    pub model: ModelRoute,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum HeaderReason {
    Initial,
    Change,
}

/// What the model sees for one tool.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolSchema {
    pub name: String,
    pub description: String,
    pub parameters: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelRoute {
    pub provider: String,
    pub model: String,
}

/// One model call. `messages` is the full derived list; `consumed` names the
/// inbox items that entered this request for the first time.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelRequest {
    pub step: u32,
    pub attempt: u32,
    pub request_id: String,
    pub header_seq: u64,
    pub consumed: Vec<u64>,
    pub messages: Vec<Message>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum RetryCause {
    Transport,
    RateLimit,
    Provider,
    Interrupted,
}

// ---- messages ----------------------------------------------------------------

/// A message as the model receives it. Produced by [`fold::derive_messages`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "role", rename_all = "lowercase")]
pub enum Message {
    User { content: Vec<ContentBlock> },
    Assistant { text: String, tool_calls: Vec<ToolCall> },
    Tool { call_id: String, name: String, rendered: String, is_error: bool },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum ContentBlock {
    Text { text: String },
    /// Base64 data with its media type. Only text is sent to a model that
    /// does not accept images; the block is then replaced by a placeholder.
    Image { data: String, media_type: String },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub args: serde_json::Value,
}

// ---- assistant payloads -----------------------------------------------------

/// One streamed fragment. See docs/protocol.md "model/chunk".
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Chunk {
    Text { delta: String },
    Thinking { delta: String },
    ToolCallStart { id: String, name: String },
    ToolCallDelta { id: String, delta: String },
    ToolCallEnd { id: String },
    Done { stop: StopReason, usage: Usage },
    Error { message: String, retryable: bool },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum StopReason {
    End,
    Tool,
    Length,
    Interrupted,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct Usage {
    pub input: u64,
    pub output: u64,
    pub cache_read: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AssistantMessage {
    pub step: u32,
    pub request_id: String,
    pub text: String,
    pub tool_calls: Vec<ToolCall>,
    pub stop: StopReason,
    pub usage: Usage,
    /// True when the request failed after a tool call started; `text` is
    /// then the prefix that arrived.
    pub interrupted: bool,
}

// ---- tool payloads -----------------------------------------------------------

/// Exactly one per tool call, matched by `call_id`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolResult {
    pub step: u32,
    pub call_id: String,
    pub name: String,
    /// The canonical value. When `spill` is set, this is a locator object.
    pub value: serde_json::Value,
    /// What the model received.
    pub rendered: String,
    pub is_error: bool,
    /// File name under `spill/` when the canonical value was too large to inline.
    pub spill: Option<String>,
    pub duration_ms: u64,
    /// True when written by seeding or by request-failure recovery rather
    /// than by running the tool.
    pub synthetic: bool,
}

// ---- inbox payloads ----------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InboxItem {
    pub source: InboxSource,
    pub content: Vec<ContentBlock>,
    pub from: Option<String>,
    pub message_id: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum InboxSource {
    Task,
    Parent,
    Child,
    Peer,
    Verify,
    System,
    /// Reserved for correlated exchanges.
    Request,
    /// Reserved for correlated exchanges.
    Response,
}

// ---- budget, spawn, team payloads ------------------------------------------

/// An amount of budget. Absent fields mean unlimited for that dimension.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct BudgetAmount {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_calls: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tokens: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seconds: Option<u64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SpawnContext {
    Fresh,
    Fork,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MemberPhase {
    Provisioning,
    Active,
    Failed,
}

// ---- errors ---------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
pub enum LogError {
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("line {line}: {source}")]
    Parse { line: u64, source: serde_json::Error },
    #[error("event {seq}: {rule}")]
    Invalid { seq: u64, rule: &'static str },
    #[error("log is empty")]
    Empty,
}

/// A fold of the log into the state a reader needs. See [`fold`].
#[derive(Debug, Clone, Default, PartialEq)]
pub struct State {
    pub start: Option<EpisodeStart>,
    pub outcome: Option<Outcome>,
    /// `seq` of the most recent `request/header`.
    pub header_seq: Option<u64>,
    pub header: Option<RequestHeader>,
    /// Inbox items by `seq`, with whether a request has consumed them.
    pub inbox: BTreeMap<u64, (InboxItem, bool)>,
    pub usage: Usage,
    pub model_calls: u64,
    /// Children by id, with their last known outcome.
    pub children: BTreeMap<String, Option<Outcome>>,
    pub seeded_through: Option<u64>,
}
