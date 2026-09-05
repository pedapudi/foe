//! The append-only episode log.
//!
//! Every episode writes one log. The log is the source of truth for the
//! model's request history, the viewer, replay, forking, budget accounting,
//! and team state. `docs/log-format.md` specifies the format; this crate
//! implements it and nothing else. It depends only on serde, serde_json, and
//! thiserror, so a log reader does not take on the runtime.
//!
//! The types in this file are the contract between crates. Their shapes
//! follow the specification field for field. Behavior lives in the sibling
//! modules: [`append`] writes, [`fold`] reads, [`seed`] copies a prefix.

#![forbid(unsafe_code)]

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

pub mod append;
pub mod artifact;
pub mod digest;
pub mod fold;
pub mod seed;

/// The log format version this crate writes and reads.
pub const LOG_VERSION: u32 = 3;

/// One line of the log.
///
/// `seq` starts at 0 and is contiguous. `time` is milliseconds since the
/// Unix epoch. `data` carries the payload for `kind`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Event {
    pub seq: u64,
    pub time: i64,
    /// The format version of the log, stated on the first event and absent
    /// after. A log whose first event states none is version 3, the last
    /// version whose writers stated none.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub version: Option<u32>,
    #[serde(flatten)]
    pub data: EventData,
}

/// Every event type, implemented or reserved.
///
/// The `type` field on the wire is the variant's `serde(rename)`. Reserved
/// variants exist so that a version 3 reader can parse logs written by a
/// later version; nothing in version 3 emits them.
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
    /// The complete rendering retained before the turn budget shortened it.
    #[serde(rename = "tool/rendering-archive")]
    ToolRenderingArchive(RenderingArchive),
    /// A tool call that resolves to a host tool. The host answers with a
    /// `tool/result` line over the protocol.
    #[serde(rename = "host/tool-call")]
    HostToolCall { step: u32, call_id: String, name: String, args: serde_json::Value },
    /// One inner dispatch a composing tool performed through the registry.
    /// Opens the ordinary tool-call obligation; the inner `tool/result`
    /// closes it and is excluded from derived messages.
    #[serde(rename = "tool/inner-call")]
    ToolInnerCall(ToolInnerCall),

    // ---- verification ----------------------------------------------------
    #[serde(rename = "verification/result")]
    VerificationResult(VerificationResult),

    // ---- inbox -----------------------------------------------------------
    #[serde(rename = "inbox/item")]
    InboxItem(InboxItem),

    // ---- budget and spawn -----------------------------------------------
    #[serde(rename = "budget/reserve")]
    BudgetReserve { child_id: String, reserved: BudgetAmount },
    #[serde(rename = "budget/release")]
    BudgetRelease { child_id: String, spent: BudgetAmount },
    #[serde(rename = "spawn/start")]
    SpawnStart { child_id: String, contract: String, context: SpawnContext, call_id: String },
    #[serde(rename = "spawn/end")]
    SpawnEnd { child_id: String, outcome: Outcome },

    // ---- teams (lead log only) ------------------------------------------
    #[serde(rename = "team/roster")]
    TeamRoster { member_id: String, name: String, description: String, phase: MemberPhase },
    #[serde(rename = "team/message")]
    TeamMessage { message_id: String, from: String, to: String, content: Vec<ContentBlock> },
    #[serde(rename = "team/delivered")]
    TeamDelivered { message_id: String, to: String },
    #[serde(rename = "team/task")]
    TeamTask(TeamTask),

    // ---- sandbox ---------------------------------------------------------
    #[serde(rename = "sandbox/denied")]
    SandboxDenied { pid: u32, comm: String, path: String, access: String },

    // ---- compaction ------------------------------------------------------
    #[serde(rename = "compaction/start")]
    CompactionStart(CompactionStart),
    #[serde(rename = "compaction/summary")]
    CompactionSummary(CompactionSummary),
    /// The summarization ended. `usage` is what the summarization response
    /// reported, zero when none arrived. `active_estimate` is the estimated
    /// token count of the next request. `error` states why `ok` is false.
    #[serde(rename = "compaction/end")]
    CompactionEnd {
        step: u32,
        ok: bool,
        usage: Usage,
        active_estimate: u64,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },

    // ---- workflows -------------------------------------------------------
    #[serde(rename = "workflow/node-start")]
    WorkflowNodeStart(WorkflowNodeStart),
    #[serde(rename = "workflow/node-end")]
    WorkflowNodeEnd(WorkflowNodeEnd),
    #[serde(rename = "workflow/recovery")]
    WorkflowRecovery(WorkflowRecovery),
    #[serde(rename = "workflow/branch")]
    WorkflowBranch(WorkflowBranch),
}

impl EventData {
    /// The wire name of this event's type, as it appears in `"type"`: the
    /// variant's `serde(rename)`, read back from the serialized form.
    pub fn type_name(&self) -> String {
        serde_json::to_value(self).ok().and_then(|v| v["type"].as_str().map(str::to_string)).unwrap_or_default()
    }
}

/// A pairing of events the format defines: one event opens an obligation
/// and a later event closes it, the two matched by a key. See
/// docs/log-format.md "Open obligations".
///
/// This list and [`obligations`] are the only places that name a pairing.
/// A new paired event type is added here, and the check in
/// [`fold::validate_next`] and the repair in [`seed::closing_events`] then
/// cover it without further change.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Obligation {
    /// A tool call in an `assistant/message`, closed by the `tool/result`
    /// naming the call id. The key is the call id.
    ToolCall,
    /// A `request/retry`, closed by the `model/request` of the attempt it
    /// announces. The key is the step and that attempt's number.
    Retry,
    /// A `compaction/start`, closed by the `compaction/end` of the same
    /// step. The key is the step.
    Compaction,
    /// A `spawn/start`, closed by the `spawn/end` naming the child. The key
    /// is the child id.
    Child,
    /// A `budget/reserve`, closed by the `budget/release` naming the child.
    /// The key is the child id.
    Reservation,
    /// A `team/message`, closed by the `team/delivered` naming it. The key
    /// is the message id.
    Delivery,
}

impl Obligation {
    /// Whether the format binds the log to this pairing. Three rules follow
    /// from being bound: a closing event names an obligation an earlier
    /// event opened, it closes that obligation once, and no obligation
    /// stands open at `episode/end`.
    ///
    /// Every pairing is bound except `Delivery`. A `team/message` that no
    /// `team/delivered` follows is a message the lead queued and the target
    /// never recorded. The format has the lead offer such a message again
    /// when the target restarts, which produces a second delivery record
    /// for one message, and it defines no event for a message given up on.
    /// An undelivered message is therefore a state the log records rather
    /// than a defect a writer may not produce.
    pub fn is_binding(self) -> bool {
        self != Obligation::Delivery
    }
}

/// The key of the `Retry` an attempt at `step` closes.
fn attempt_key(step: u32, attempt: u32) -> String {
    format!("{step}/{attempt}")
}

/// What one event does to the open obligations: for each, the pairing, the
/// key matching an opening to its closing, and whether the event opens it.
/// An event that neither opens nor closes anything yields nothing.
pub fn obligations(data: &EventData) -> Vec<(Obligation, String, bool)> {
    let one = |kind, key: String, opens| vec![(kind, key, opens)];
    match data {
        EventData::AssistantMessage(m) => {
            m.tool_calls.iter().map(|c| (Obligation::ToolCall, c.id.clone(), true)).collect()
        }
        EventData::ToolResult(r) => one(Obligation::ToolCall, r.call_id.clone(), false),
        EventData::ToolInnerCall(c) => one(Obligation::ToolCall, c.call_id.clone(), true),
        // A retry announces the attempt after the one that failed.
        EventData::RequestRetry { step, attempt, .. } => one(Obligation::Retry, attempt_key(*step, attempt + 1), true),
        EventData::ModelRequest(r) if r.attempt > 1 => one(Obligation::Retry, attempt_key(r.step, r.attempt), false),
        EventData::CompactionStart(s) => one(Obligation::Compaction, s.step.to_string(), true),
        EventData::CompactionEnd { step, .. } => one(Obligation::Compaction, step.to_string(), false),
        EventData::SpawnStart { child_id, .. } => one(Obligation::Child, child_id.clone(), true),
        EventData::SpawnEnd { child_id, .. } => one(Obligation::Child, child_id.clone(), false),
        EventData::BudgetReserve { child_id, .. } => one(Obligation::Reservation, child_id.clone(), true),
        EventData::BudgetRelease { child_id, .. } => one(Obligation::Reservation, child_id.clone(), false),
        EventData::TeamMessage { message_id, .. } => one(Obligation::Delivery, message_id.clone(), true),
        EventData::TeamDelivered { message_id, .. } => one(Obligation::Delivery, message_id.clone(), false),
        _ => Vec::new(),
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
    pub contract: serde_json::Value,
    /// `sha256:<hex>` over the execution contract.
    pub contract_fingerprint: String,
    pub task: String,
    pub runtime: RuntimeInfo,
    pub sandbox: SandboxInfo,
    /// The allowance enforced for this episode.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub effective_budget: Option<Budget>,
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
    /// The filesystem and network permissions compiled for this episode.
    pub resolved_permissions: ResolvedPermissions,
    /// How the runtime owns descendant processes.
    pub process_boundary: ProcessBoundaryInfo,
}

/// The permissions resolved for an episode and the reason for every entry.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResolvedPermissions {
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub read: Vec<ResolvedPathPermission>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub write: Vec<ResolvedPathPermission>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub execute: Vec<ResolvedPathPermission>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub bind_tcp: Vec<u16>,
    /// Each entry explains one reason outbound TCP remains available.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub connect_tcp: Vec<String>,
}

/// One filesystem path in a resolved permission set.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResolvedPathPermission {
    pub path: String,
    pub reason: String,
    /// Present when the rule names one captured configured executable.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sha256: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProcessBoundaryInfo {
    pub kind: ProcessBoundaryKind,
    pub subtree_cleanup: SubtreeCleanup,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ProcessBoundaryKind {
    CgroupV2,
    #[default]
    ProcessGroup,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SubtreeCleanup {
    Enforced,
    #[default]
    Observational,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum SandboxMode {
    #[default]
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
    RecoveryFailed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExhaustedLimit {
    ModelCalls,
    InputTokens,
    OutputTokens,
    ContextWindow,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_output_tokens: Option<u32>,
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
    User {
        content: Vec<ContentBlock>,
    },
    Assistant {
        text: String,
        tool_calls: Vec<ToolCall>,
        /// Reasoning blocks in the order produced. A transport replays them
        /// to the same model route and omits them for any other route.
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        thinking: Vec<ThinkingBlock>,
    },
    Tool {
        call_id: String,
        name: String,
        rendered: String,
        is_error: bool,
    },
}

/// One reasoning block. `signature` is an opaque provider token that must
/// accompany the block when it is replayed; it is absent when the provider
/// issues none.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ThinkingBlock {
    pub text: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub signature: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum ContentBlock {
    Text {
        text: String,
    },
    /// Base64 data with its media type. Only text is sent to a model that
    /// does not accept images; the block is then replaced by a placeholder.
    Image {
        data: String,
        media_type: String,
    },
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
    Text {
        delta: String,
    },
    Thinking {
        delta: String,
    },
    /// Closes the current thinking block with the provider's replay token.
    /// Sent at most once per block; absent for providers that issue none.
    ThinkingSignature {
        signature: String,
    },
    ToolCallStart {
        id: String,
        name: String,
    },
    ToolCallDelta {
        id: String,
        delta: String,
    },
    ToolCallEnd {
        id: String,
    },
    Done {
        stop: StopReason,
        usage: Usage,
    },
    Error {
        message: String,
        retryable: bool,
    },
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
    /// Reasoning blocks assembled from `thinking` and `thinking_signature`
    /// chunks, in order. Empty when the model produced none.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub thinking: Vec<ThinkingBlock>,
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
    /// Machine-readable failure classification. Absent for successful
    /// results and for error results written before this field existed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub failure: Option<ToolFailure>,
    /// File name under `spill/` when the canonical value was too large to inline.
    pub spill: Option<String>,
    /// One line the tool wrote naming what it acted on and what came of
    /// it, for a reader scanning a list. Never sent to the model.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subject: Option<String>,
    pub duration_ms: u64,
    /// True when written by seeding or by request-failure recovery rather
    /// than by running the tool.
    pub synthetic: bool,
}

/// Why a tool call failed. `message` is for the model and a person;
/// scheduling reads `code`, `retryable`, and `details` instead.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolFailure {
    pub code: ToolFailureCode,
    pub message: String,
    pub retryable: bool,
    pub details: serde_json::Value,
}

/// Stable failure classes shared by every tool source.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ToolFailureCode {
    InvalidCall,
    CapabilityDenied,
    Unavailable,
    ProcessStartFailed,
    ProcessExit,
    TimedOut,
    BudgetExhausted,
    LimitExceeded,
    OperationFailed,
    Interrupted,
}

/// Payload of `tool/inner-call`: one tool call a composing tool, such as
/// the built-in `compose_tools` tool, dispatched through the registry while its
/// own model-issued call ran. `outer_call_id` names that model-issued call;
/// `call_id` is the inner call's own id, which its `tool/result` names;
/// `index` counts the outer call's inner dispatches from 0.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolInnerCall {
    pub outer_call_id: String,
    pub call_id: String,
    pub index: u32,
    pub name: String,
    pub args: serde_json::Value,
}

// ---- verification payloads ---------------------------------------------------

/// One authoritative verifier invocation, per `done_when.verify` or a
/// workflow node's `verify`: the accepted run that completes the work, a
/// findings run that re-fires it, or a failed run. The event never enters
/// derived messages; the model sees findings only through the `verify`
/// inbox item.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct VerificationResult {
    /// The step whose completion attempt was verified.
    pub step: u32,
    /// The verifier tool's name.
    pub tool: String,
    /// For a configured executable, `sha256:<hex>` over its file content at
    /// invocation; for a built-in or host tool, the runtime build hash.
    pub verifier_fingerprint: String,
    pub status: VerificationStatus,
    /// The finding strings the `verify` inbox item carries; empty for
    /// `accepted` and for `failed`.
    pub findings: Vec<String>,
    /// Present only for `failed`: why the verifier could not judge.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    /// `sha256:<hex>` over the canonical JSON of the exact candidate value
    /// handed to the verifier. Logs written before the field existed omit
    /// it; absence makes no claim about what was judged.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub candidate_sha256: Option<String>,
    pub duration_ms: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum VerificationStatus {
    Accepted,
    Findings,
    Failed,
}

// ---- archived rendering payloads --------------------------------------------

/// Immutable evidence for a rendering shortened before `tool/result`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RenderingArchive {
    pub step: u32,
    pub call_id: String,
    /// Path below `spill/`, derived from `digest`.
    pub file: String,
    /// `sha256:<lowercase hex>` of the complete UTF-8 rendering.
    pub digest: String,
    pub bytes: u64,
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
    /// A process session of this episode ended; one item per session
    /// lifetime, written when the runtime observes the exit.
    Session,
    /// Reserved for correlated exchanges.
    Request,
    /// Reserved for correlated exchanges.
    Response,
}

// ---- budget, spawn, team payloads ------------------------------------------

fn u32_default<const N: u32>() -> u32 {
    N
}

/// The limits enforced for one episode and its descendant work.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Budget {
    pub model_calls: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub input_tokens: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_tokens: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seconds: Option<u64>,
    #[serde(default = "u32_default::<1>")]
    pub max_depth: u32,
    #[serde(default = "u32_default::<8>")]
    pub max_episodes: u32,
    #[serde(default = "u32_default::<4>")]
    pub max_concurrent: u32,
    #[serde(default = "u32_default::<8>")]
    pub loop_threshold: u32,
}

/// An amount of budget. Absent fields mean unlimited for that dimension.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct BudgetAmount {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_calls: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub input_tokens: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_tokens: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seconds: Option<u64>,
    /// Episodes, counting the one the amount is granted to. In a
    /// `budget/reserve` this is the child's whole subtree allowance; in a
    /// `budget/release` it is how many episodes the subtree held.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub episodes: Option<u64>,
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

/// One complete revision of a task on a team's board. The lead log is the
/// only writer. A queued task has no owner. A running or settled task names
/// the child episode assigned to it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TeamTask {
    pub task_id: String,
    pub revision: u64,
    pub name: String,
    pub contract: String,
    pub description: String,
    pub context: SpawnContext,
    pub status: TaskStatus,
    pub owner: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub blocked_by: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub scope: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub outcome: Option<Outcome>,
    /// The model-issued call that posted the task. A later capacity release
    /// may start the child, so the call and the launch can be separated.
    pub call_id: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TaskStatus {
    Queued,
    Running,
    Completed,
    Blocked,
    Exhausted,
    Failed,
}

// ---- compaction payloads -------------------------------------------------------

/// Request ids of summarization requests start with this. The request and
/// its response are recorded like any other and contribute nothing to
/// derived messages. See docs/compaction.md.
pub const SUMMARY_REQUEST_PREFIX: &str = "cmp_";

/// A contiguous range of events, both ends included.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct Covered {
    pub first_seq: u64,
    pub last_seq: u64,
}

/// Payload of `compaction/start`. `covered` is the span this compaction
/// summarizes directly; `projected_tokens` is the projection of the next
/// request that crossed the threshold; `reserved` is the budget remaining
/// to the episode, which the summarization call draws on.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CompactionStart {
    pub step: u32,
    pub covered: Covered,
    pub trigger: CompactionTrigger,
    pub projected_tokens: u64,
    pub reserved: BudgetAmount,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CompactionTrigger {
    Threshold,
}

/// Payload of `compaction/summary`. `summary` is the model's narrative;
/// `state` is built by the runtime from typed events. Events before
/// `first_kept_seq` leave the derived message list once this is written.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CompactionSummary {
    pub step: u32,
    pub summary: String,
    pub state: ContinuationState,
    pub first_kept_seq: u64,
    pub summary_request_seq: u64,
}

/// What the model must still honor and what the runtime knows, carried
/// across a compaction as data rather than as model output. `task` is the
/// task text verbatim; `done_when` renders the completion condition in one
/// line; `outstanding_findings` holds the latest verifier report; the file
/// lists and `children` accumulate across every compaction of the episode.
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub struct ContinuationState {
    pub task: String,
    pub done_when: String,
    pub outstanding_findings: Vec<String>,
    pub files: CompactedFiles,
    pub children: Vec<ChildSummary>,
    pub covered: Covered,
    pub budget_remaining: BudgetAmount,
}

/// Paths named by `read`, `write`, and `edit` calls whose results were not
/// errors, sorted and without duplicates.
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub struct CompactedFiles {
    pub read: Vec<String>,
    pub written: Vec<String>,
    pub edited: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChildSummary {
    pub id: String,
    pub contract: String,
    pub outcome: Outcome,
}

// ---- workflow payloads --------------------------------------------------------

/// One firing of a workflow node begins. `fire` counts this node's firings
/// from 1. `inputs` lists the `seq` of the events that produced the values
/// the node receives. `child_id` names the child episode of a model node.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorkflowNodeStart {
    pub node: String,
    pub fire: u32,
    pub inputs: Vec<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub child_id: Option<String>,
}

/// A firing ended. `value` is the node's canonical output and `rendered`
/// the text its successors receive. A failed firing carries null and an
/// empty rendering. An optional model node carries its declared `empty`
/// output together with the blocked or exhausted child error.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorkflowNodeEnd {
    pub node: String,
    pub fire: u32,
    pub value: serde_json::Value,
    pub rendered: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub failure: Option<ToolFailure>,
    pub duration_ms: u64,
}

/// A node with `branches` chose a label; only `successors` fire.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorkflowBranch {
    pub node: String,
    pub fire: u32,
    pub label: String,
    pub successors: Vec<String>,
}

/// A recovery decision was made and applied. `action` is `retry`, `amend`,
/// `skip`, or `abort`; `target` names the node a retry or amend re-fires;
/// `intervention` counts decisions in this episode from 1.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorkflowRecovery {
    pub node: String,
    pub fire: u32,
    pub cause: String,
    pub action: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub failure: Option<ToolFailure>,
    pub intervention: u32,
}

// ---- errors ---------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
pub enum LogError {
    #[error("{0}")]
    Recording(String),
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("line {line}: {source}")]
    Parse { line: u64, source: serde_json::Error },
    #[error("event {seq}: {rule}")]
    Invalid { seq: u64, rule: &'static str },
    #[error("log is empty")]
    Empty,
    #[error("log states format version {found}; this reader reads version {supported}")]
    UnsupportedVersion { found: u64, supported: u32 },
    #[error("rendering archive event {seq} at {path}: {rule}")]
    Archive { seq: u64, path: String, rule: String },
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
    /// An archive event must be followed immediately by its result.
    pub pending_rendering_archive: Option<RenderingArchive>,
    /// Obligations opened and not yet closed, by pairing and key.
    pub open: BTreeSet<(Obligation, String)>,
    /// Obligations whose most recent opening has been closed.
    pub closed: BTreeSet<(Obligation, String)>,
}
