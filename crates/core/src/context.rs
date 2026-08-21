//! The seam between the loop and context compaction.
//!
//! The loop consults a [`ContextPolicy`] before each step's request. The
//! policy decides from the log alone whether the projected next request
//! outgrows the model's window, where the kept suffix begins, and what the
//! summarization request says; the loop lends it one recorded, budgeted
//! model call through [`SummaryCall`] and writes the compaction events.
//! `foe-context` implements the policy; docs/compaction.md specifies it.

use crate::RuntimeError;
use foe_log::{AssistantMessage, BudgetAmount, CompactionSummary, Covered, Event, Outcome, Usage};

/// What a policy reads: every event so far and the budget remaining.
pub struct ContextState<'a> {
    pub events: &'a [Event],
    pub remaining: BudgetAmount,
}

/// Where the projection is cut. `first_kept_seq` is the `seq` of the
/// `model/request` that opens the kept suffix; `covered` is the span the
/// summarization call reads directly; `exceeds_window` is true when the
/// projection passes the window itself rather than the reserve, so that a
/// failed summarization ends the episode.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cut {
    pub first_kept_seq: u64,
    pub covered: Covered,
    pub projected_tokens: u64,
    pub exceeds_window: bool,
}

/// How one model request ended.
pub enum Answer {
    /// A response arrived; `request_seq` is the `seq` of its `model/request`.
    Message { message: AssistantMessage, request_seq: u64 },
    /// The request failed after a tool call started; the interrupted
    /// message and synthetic results are written.
    Interrupted,
    /// The request failed and is not retried.
    Failed(String),
    /// The episode ended while the request was in flight.
    Ended(Outcome),
}

/// What a summarization produced.
pub enum Summarized {
    /// The summary event's payload, the summarization response's usage,
    /// and the estimated token count of the next request.
    Summary {
        summary: Box<CompactionSummary>,
        usage: Usage,
        active_estimate: u64,
    },
    /// No usable summary; the projection is left as it was.
    Failed {
        error: String,
        usage: Usage,
    },
    Ended(Outcome),
}

/// The loop's side of a summarization: one request with `system` as its
/// system prompt, no tools, and `user` as its only message, recorded with
/// a `cmp_` request id, counted against the budget, and attempted once.
#[async_trait::async_trait]
pub trait SummaryCall: Send {
    async fn call(&mut self, system: &str, user: String) -> Result<Answer, RuntimeError>;
}

#[async_trait::async_trait]
pub trait ContextPolicy: Send + Sync {
    /// The cut to make before the next request, or `None` while the
    /// projected request fits under the window less the reserve.
    fn plan(&self, state: &ContextState) -> Option<Cut>;
    /// Summarizes `cut.covered` with one call through `call` and builds the
    /// continuation state from the events.
    async fn summarize(
        &self,
        state: &ContextState<'_>,
        cut: &Cut,
        call: &mut dyn SummaryCall,
    ) -> Result<Summarized, RuntimeError>;
}
