//! The agent loop: assemble, stream, execute, settle; the three step rules; request-failure recovery; looping detection.
//!
//! Implements docs/design.md (The episode). One call to [`run`] drives one
//! episode from its first request to its `episode/end`. The loop is the
//! only writer of its log; the protocol layer and the spawner append
//! through the same shared [`Log`]. Before each step's request the loop
//! consults the context policy, which may replace the oldest part of the
//! projection with a summary; docs/compaction.md specifies that.

use crate::budget::Pool;
use crate::config::Program;
use crate::context::{Answer, ContextPolicy, ContextState, Summarized, SummaryCall};
use crate::harness_text as text;
use crate::inbox::Inbox;
use crate::registry::{Handles, Registry};
use crate::spawn::Router;
use crate::{ChunkSink, ModelRequestBody, RuntimeError, ToolValue, Transport};
use foe_log::{
    fold, seed, AssistantMessage, BlockedCode, Chunk, CompactionStart, CompactionTrigger, ContentBlock, EpisodeStart,
    Event, EventData, ExhaustedLimit, HeaderReason, InboxItem, InboxSource, LogError, Message, ModelRequest, Outcome,
    RequestHeader, RetryCause, StopReason, ThinkingBlock, ToolCall, ToolResult, ToolSchema, Usage,
    SUMMARY_REQUEST_PREFIX,
};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};
use tokio::sync::watch;
use tokio::task::JoinSet;

/// A rendered result longer than this, or a canonical value serializing
/// longer than this, is written to `spill/` and replaced by a locator.
pub const SPILL_LIMIT: usize = 64 * 1024;
/// Bytes of the rendered text kept inline ahead of the locator.
pub const SPILL_HEAD: usize = 16 * 1024;
/// Attempts per step before the episode is blocked as `recovery-exhausted`.
pub const MAX_ATTEMPTS: u32 = 5;
const BACKOFF_BASE_MS: u64 = 500;
const BACKOFF_CAP_MS: u64 = 8_000;
/// How long the teardown waits for children it asked to end before it
/// records their settlement itself.
const SETTLE_GRACE: Duration = Duration::from_secs(10);
/// How often a wait on children rereads the pool.
const SETTLE_POLL: Duration = Duration::from_millis(20);

pub fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

/// The episode's log, shared between the loop, the protocol layer, and the
/// spawner. Keeps every event in memory beside the file so that message
/// derivation and loop detection never reread the file.
pub struct Log {
    dir: PathBuf,
    inner: Mutex<(foe_log::append::Writer, Vec<Event>)>,
}

impl Log {
    /// Creates the log, or continues one that already has events, for
    /// example one that seeding wrote. The mirror first receives an
    /// existing file as it stands, so a host reading standard output sees
    /// the seeded prefix as well.
    pub fn create_or_open(dir: &Path, mut mirror: Option<Box<dyn std::io::Write + Send>>) -> Result<Self, LogError> {
        let file = dir.join(fold::LOG_FILE);
        let events = if file.exists() { fold::read_all(dir)? } else { Vec::new() };
        if let (Some(mirror), false) = (&mut mirror, events.is_empty()) {
            std::io::copy(&mut std::fs::File::open(&file)?, mirror)?;
            mirror.flush()?;
        }
        let writer = match events.is_empty() {
            true => foe_log::append::Writer::create(dir, mirror)?,
            false => foe_log::append::Writer::open(dir, mirror)?,
        };
        Ok(Self { dir: dir.to_path_buf(), inner: Mutex::new((writer, events)) })
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    pub fn append(&self, data: EventData) -> Result<Event, LogError> {
        let mut inner = lock(&self.inner);
        let event = inner.0.append(data)?;
        inner.1.push(event.clone());
        Ok(event)
    }

    pub fn sync(&self) -> Result<(), LogError> {
        lock(&self.inner).0.sync()
    }

    pub fn next_seq(&self) -> u64 {
        lock(&self.inner).0.next_seq()
    }

    pub fn events(&self) -> Vec<Event> {
        lock(&self.inner).1.clone()
    }

    pub fn with_events<R>(&self, f: impl FnOnce(&[Event]) -> R) -> R {
        f(&lock(&self.inner).1)
    }

    /// True when a `model/request` with this id was written. Scans from the
    /// end, where the request in flight is.
    pub fn has_request(&self, request_id: &str) -> bool {
        self.with_events(|events| {
            events.iter().rev().any(|e| matches!(&e.data, EventData::ModelRequest(r) if r.request_id == request_id))
        })
    }
}

/// Everything one episode needs. `start` is written when the log is empty;
/// a seeded log keeps its own. `pool` is shared with the spawner; `run`
/// folds the existing events into it before the first step. `context`
/// compacts the conversation when it outgrows the window; `None` never
/// compacts.
pub struct Params {
    pub log: Arc<Log>,
    pub start: EpisodeStart,
    pub program: Program,
    pub registry: Arc<Registry>,
    pub handles: Handles,
    pub transport: Arc<dyn Transport>,
    pub pool: Arc<Mutex<Pool>>,
    /// `Some(reason)` ends the episode as `failed` with that reason.
    pub stop: watch::Receiver<Option<String>>,
    /// The running children, when this episode may start any. The teardown
    /// ends the ones still running, so that no child outlives the episode
    /// that started it and no reservation stands unreturned.
    pub children: Option<Arc<Router>>,
    pub context: Option<Arc<dyn ContextPolicy>>,
}

/// Appends an `inbox/item` the moment it arrives, dropping a peer message
/// whose `message_id` the log already holds. The protocol layer and the
/// spawner deliver items through this.
pub fn append_inbox_item(log: &Log, item: InboxItem) -> Result<Option<Event>, LogError> {
    if log.with_events(|events| crate::inbox::is_duplicate(events, &item)) {
        return Ok(None);
    }
    log.append(EventData::InboxItem(item)).map(Some)
}

/// Runs the episode to its end, closes what its log left open through
/// [`settle`], and writes `episode/end`.
pub async fn run(params: Params) -> Result<Outcome, RuntimeError> {
    let (log, pool, children) = (params.log.clone(), params.pool.clone(), params.children.clone());
    let driven = match Episode::new(params) {
        Ok(mut episode) => episode.drive().await,
        Err(e) => Err(e),
    };
    let outcome = match driven {
        Ok(outcome) => outcome,
        Err(RuntimeError::Log(e)) => return Err(e.into()),
        Err(e) => Outcome::Failed { error: e.to_string() },
    };
    settle(&log, &pool, children.as_deref()).await?;
    log.append(EventData::EpisodeEnd { outcome: outcome.clone() })?;
    log.sync()?;
    Ok(outcome)
}

/// Closes every obligation the log opened, so that `episode/end` is valid.
/// See docs/log-format.md "Open obligations".
///
/// A child still running when the episode ends is asked to end, and the
/// `spawn/end` and `budget/release` its reservation owes are awaited for
/// [`SETTLE_GRACE`]. Whatever is still open after that, including every
/// tool call left without a result, is closed by the synthetic events the
/// log crate produces. The workflow executor ends its episodes through this
/// too.
pub async fn settle(log: &Log, pool: &Mutex<Pool>, children: Option<&Router>) -> Result<(), RuntimeError> {
    if lock(pool).active_children() > 0 {
        if let Some(children) = children {
            children.cancel_all();
        }
        settled_children(pool, Some(Instant::now() + SETTLE_GRACE)).await;
    }
    for data in log.with_events(seed::closing_events) {
        log.append(data)?;
    }
    log.sync()?;
    Ok(())
}

/// Waits until every child has settled, which is when the `spawn/end` and
/// `budget/release` each reservation owes are in the log, or until
/// `deadline`. Returns the number of children still running. Without a
/// deadline the wait has no bound other than the children's own budgets.
pub async fn settled_children(pool: &Mutex<Pool>, deadline: Option<Instant>) -> usize {
    while lock(pool).active_children() > 0 && deadline.is_none_or(|d| Instant::now() < d) {
        tokio::time::sleep(SETTLE_POLL).await;
    }
    lock(pool).active_children()
}

/// Which request a step issues.
enum Request {
    /// The step's own request, assembled from the log and the inbox.
    Step,
    /// A summarization request carrying these messages; attempted once.
    Summary(Vec<Message>),
}

struct Episode {
    p: Params,
    inbox: Inbox,
    header: Option<(u64, RequestHeader)>,
    step: u32,
    requests: u64,
    /// `seq` of the most recent `model/request`.
    request_seq: u64,
    verify_attempts: u32,
    spill_dir: PathBuf,
}

impl Episode {
    fn new(p: Params) -> Result<Self, RuntimeError> {
        if p.log.next_seq() == 0 {
            p.log.append(EventData::EpisodeStart(p.start.clone()))?;
            p.log.append(EventData::InboxItem(item(InboxSource::Task, &p.start.task)))?;
            p.log.sync()?;
        }
        let events = p.log.events();
        let state = fold::fold(&events)?;
        events.iter().for_each(|e| lock(&p.pool).apply(&e.data));
        let step = events.iter().rev().find_map(|e| match &e.data {
            EventData::ModelRequest(r) => Some(r.step),
            _ => None,
        });
        Ok(Self {
            inbox: Inbox::from_state(&state),
            header: state.header_seq.zip(state.header),
            step: step.unwrap_or(0),
            requests: state.model_calls,
            request_seq: 0,
            verify_attempts: 0,
            spill_dir: p.log.dir().join("spill"),
            p,
        })
    }

    async fn drive(&mut self) -> Result<Outcome, RuntimeError> {
        loop {
            if let Some(reason) = self.p.stop.borrow().clone() {
                return Ok(Outcome::Failed { error: reason });
            }
            self.step += 1;
            if let Some(limit) = self.exhausted() {
                return Ok(Outcome::Exhausted { limit });
            }
            if let Some(outcome) = self.compact().await? {
                return Ok(outcome);
            }
            self.write_header(self.p.registry.system_prompt(&self.p.program.instructions), self.p.registry.schemas())?;
            let message = match self.request(Request::Step).await? {
                Answer::Message { message, .. } => message,
                Answer::Interrupted => continue,
                Answer::Failed(error) => {
                    return Ok(Outcome::Failed { error: format!("model request failed: {error}") })
                }
                Answer::Ended(outcome) => return Ok(outcome),
            };
            let results = match self.execute(&message).await? {
                Ok(results) => results,
                Err(outcome) => return Ok(outcome),
            };
            if let Some(outcome) = self.settle(&message, &results).await? {
                return Ok(outcome);
            }
        }
    }

    fn exhausted(&self) -> Option<ExhaustedLimit> {
        lock(&self.p.pool).exhausted()
    }

    fn deadline(&self) -> Option<Instant> {
        lock(&self.p.pool).deadline()
    }

    fn append_inbox(&mut self, source: InboxSource, text: &str) -> Result<(), RuntimeError> {
        append_inbox_item(&self.p.log, item(source, text))?;
        Ok(())
    }

    /// Writes `request/header` when `system`, `tools`, or the route differ
    /// from the header in effect.
    fn write_header(&mut self, system: String, tools: Vec<ToolSchema>) -> Result<(), RuntimeError> {
        let model = self.p.transport.route();
        if self.header.as_ref().is_some_and(|(_, h)| h.system == system && h.tools == tools && h.model == model) {
            return Ok(());
        }
        let reason = if self.header.is_some() { HeaderReason::Change } else { HeaderReason::Initial };
        let header = RequestHeader { reason, system, tools, model };
        let event = self.p.log.append(EventData::RequestHeader(header.clone()))?;
        self.header = Some((event.seq, header));
        Ok(())
    }

    /// Consults the context policy before a step's request and records the
    /// compaction it makes. A failed summarization leaves the projection
    /// as it was; when that projection passes the window itself, the
    /// episode ends as exhausted.
    async fn compact(&mut self) -> Result<Option<Outcome>, RuntimeError> {
        let Some(policy) = self.p.context.clone() else { return Ok(None) };
        let remaining = lock(&self.p.pool).remaining();
        let Some(cut) = self.p.log.with_events(|events| policy.plan(&ContextState { events, remaining })) else {
            return Ok(None);
        };
        let step = self.step;
        self.p.log.append(EventData::CompactionStart(CompactionStart {
            step,
            covered: cut.covered,
            trigger: CompactionTrigger::Threshold,
            projected_tokens: cut.projected_tokens,
            reserved: remaining,
        }))?;
        let events = self.p.log.events();
        let state = ContextState { events: &events, remaining };
        let (ok, usage, active_estimate, error) = match policy.summarize(&state, &cut, self).await? {
            Summarized::Ended(outcome) => return Ok(Some(outcome)),
            Summarized::Summary { summary, usage, active_estimate } => {
                self.p.log.append(EventData::CompactionSummary(*summary))?;
                (true, usage, active_estimate, None)
            }
            Summarized::Failed { error, usage } => (false, usage, cut.projected_tokens, Some(error)),
        };
        self.p.log.append(EventData::CompactionEnd { step, ok, usage, active_estimate, error })?;
        Ok((!ok && cut.exceeds_window).then_some(Outcome::Exhausted { limit: ExhaustedLimit::Tokens }))
    }

    /// One model request with bounded retries. See docs/design.md "Failure
    /// of a model request". A summarization request is attempted once.
    async fn request(&mut self, kind: Request) -> Result<Answer, RuntimeError> {
        let summary = matches!(kind, Request::Summary(_));
        let mut attempt = 0;
        // The cause and the delay of the failure that the next attempt
        // retries. The `request/retry` is written from it immediately
        // before that attempt, because the event states that a request is
        // being retried and nothing else may end the step between the two.
        let mut retried: Option<(RetryCause, u64)> = None;
        loop {
            attempt += 1;
            if let Some(limit) = self.exhausted() {
                return Ok(Answer::Ended(Outcome::Exhausted { limit }));
            }
            self.requests += 1;
            let request_id = format!("{}{:04}", if summary { SUMMARY_REQUEST_PREFIX } else { "rq_" }, self.requests);
            let (consumed, messages) = match &kind {
                Request::Summary(messages) => (Vec::new(), messages.clone()),
                Request::Step => {
                    self.p.log.with_events(|events| self.inbox.absorb(events));
                    let consumed = self.inbox.pending();
                    let messages = self.p.log.with_events(|e| fold::derive_messages(e, u64::MAX, &consumed));
                    (consumed, messages)
                }
            };
            let (header_seq, header) = self.header.clone().expect("header written before the request");
            if let Some((cause, delay_ms)) = retried.take() {
                let (step, failed) = (self.step, attempt - 1);
                self.p.log.append(EventData::RequestRetry { step, attempt: failed, cause, delay_ms })?;
            }
            let event = self.p.log.append(EventData::ModelRequest(ModelRequest {
                step: self.step,
                attempt,
                request_id: request_id.clone(),
                header_seq,
                consumed: consumed.clone(),
                messages: messages.clone(),
            }))?;
            self.request_seq = event.seq;
            self.inbox.consume(&consumed);
            lock(&self.p.pool).note_request();
            let body = ModelRequestBody {
                request_id: request_id.clone(),
                system: header.system,
                tools: header.tools,
                messages,
                max_output_tokens: self.p.program.model.as_ref().and_then(|m| m.max_output_tokens),
            };
            let mut recorder = Recorder::new(self.p.log.clone(), self.step, request_id);
            let transport = self.p.transport.clone();
            let streamed = tokio::select! {
                _ = transport.stream(body, &mut recorder) => None,
                reason = wait_stop(self.p.stop.clone()) => Some(Outcome::Failed { error: reason }),
                _ = until(self.deadline()) => Some(Outcome::Exhausted { limit: ExhaustedLimit::Seconds }),
            };
            recorder.check()?;
            if let Some(outcome) = streamed {
                if !recorder.calls.is_empty() {
                    let message = recorder.message(StopReason::Interrupted, Usage::default(), true);
                    self.p.log.append(EventData::AssistantMessage(message))?;
                }
                return Ok(Answer::Ended(outcome));
            }
            let (cause, error) = match recorder.terminal.take() {
                Some(Chunk::Done { stop, usage }) => {
                    let message = recorder.message(stop, usage, false);
                    self.p.log.append(EventData::AssistantMessage(message.clone()))?;
                    lock(&self.p.pool).note_usage(usage);
                    return Ok(Answer::Message { message, request_seq: self.request_seq });
                }
                Some(Chunk::Error { message, retryable }) if recorder.calls.is_empty() => {
                    let lower = message.to_ascii_lowercase();
                    let cause = if !recorder.text.is_empty() {
                        RetryCause::Interrupted
                    } else if lower.contains("rate") || lower.contains("429") {
                        RetryCause::RateLimit
                    } else {
                        RetryCause::Provider
                    };
                    if !retryable {
                        return Ok(Answer::Failed(message));
                    }
                    (cause, message)
                }
                Some(Chunk::Error { .. }) => {
                    let message = recorder.message(StopReason::Interrupted, Usage::default(), true);
                    self.p.log.append(EventData::AssistantMessage(message.clone()))?;
                    for call in &message.tool_calls {
                        self.append_result(call, ToolValue::error(text::INTERRUPTED_RESULT), 0, true)?;
                    }
                    return Ok(Answer::Interrupted);
                }
                _ => (RetryCause::Transport, "the stream ended without a terminal chunk".to_string()),
            };
            if summary {
                return Ok(Answer::Failed(error));
            }
            // The ceiling is tested before the delay is computed, so the
            // episode never waits out a delay for an attempt it will not
            // make. The message counts the attempts that were made.
            if attempt >= MAX_ATTEMPTS {
                let message = format!("{MAX_ATTEMPTS} attempts at step {} failed", self.step);
                return Ok(Answer::Ended(Outcome::Blocked { code: BlockedCode::RecoveryExhausted, message }));
            }
            let delay_ms = (BACKOFF_BASE_MS << (attempt - 1).min(4)).min(BACKOFF_CAP_MS);
            retried = Some((cause, delay_ms));
            tokio::select! {
                _ = tokio::time::sleep(Duration::from_millis(delay_ms)) => {}
                reason = wait_stop(self.p.stop.clone()) => return Ok(Answer::Ended(Outcome::Failed { error: reason })),
            }
        }
    }

    /// Runs the calls of one message and appends their results in issue
    /// order. `Err` carries the outcome when cancellation or the deadline
    /// cut execution short; the results already produced are then lost and
    /// [`run`] writes synthetic ones.
    async fn execute(&mut self, message: &AssistantMessage) -> Result<Result<Vec<ToolResult>, Outcome>, RuntimeError> {
        let mut results = Vec::new();
        if message.stop == StopReason::Length {
            for call in &message.tool_calls {
                results.push(self.append_result(call, ToolValue::error(text::LENGTH_LIMIT_ERROR), 0, false)?);
            }
            return Ok(Ok(results));
        }
        if message.tool_calls.is_empty() {
            return Ok(Ok(results));
        }
        let deadline = self.deadline();
        let run = run_calls(
            self.p.registry.clone(),
            self.p.handles.clone(),
            message.tool_calls.clone(),
            self.step,
            self.spill_dir.clone(),
            deadline,
        );
        let values = tokio::select! {
            values = run => values,
            reason = wait_stop(self.p.stop.clone()) => return Ok(Err(Outcome::Failed { error: reason })),
            _ = until(deadline) => return Ok(Err(Outcome::Exhausted { limit: ExhaustedLimit::Seconds })),
        };
        for (call, (value, duration_ms)) in message.tool_calls.iter().zip(values) {
            results.push(self.append_result(call, value, duration_ms, false)?);
            if self.p.registry.effect(&call.name).is_some_and(|e| !e.concurrent()) {
                self.p.log.sync()?;
            }
        }
        Ok(Ok(results))
    }

    /// Appends one `tool/result`, spilling a large value first.
    fn append_result(
        &self,
        call: &ToolCall,
        value: ToolValue,
        duration_ms: u64,
        synthetic: bool,
    ) -> Result<ToolResult, RuntimeError> {
        let (value, rendered, spill) = spill(&self.spill_dir, &call.id, value)?;
        let is_error = value.get("error").is_some() && value.as_object().is_some_and(|o| o.len() == 1);
        let result = ToolResult {
            step: self.step,
            call_id: call.id.clone(),
            name: call.name.clone(),
            value,
            rendered,
            is_error,
            spill,
            duration_ms,
            synthetic,
        };
        self.p.log.append(EventData::ToolResult(result.clone()))?;
        Ok(result)
    }

    /// Block, looping, `done_when`, then budget. A turn that completes the
    /// task on the last permitted call completes the episode; the budget
    /// ends it only when work remains.
    async fn settle(
        &mut self,
        message: &AssistantMessage,
        results: &[ToolResult],
    ) -> Result<Option<Outcome>, RuntimeError> {
        match self.settle_outcome(message, results).await? {
            None => Ok(self.exhausted().map(|limit| Outcome::Exhausted { limit })),
            end => Ok(end),
        }
    }

    async fn settle_outcome(
        &mut self,
        message: &AssistantMessage,
        results: &[ToolResult],
    ) -> Result<Option<Outcome>, RuntimeError> {
        let succeeded = |name: &str| results.iter().find(|r| r.name == name && !r.is_error && !r.synthetic);
        if let Some(blocked) = succeeded(text::BLOCK_NAME) {
            let code = serde_json::from_value(blocked.value["code"].clone()).unwrap_or(BlockedCode::GoalUnreachable);
            let message = blocked.value["message"].as_str().unwrap_or_default().to_string();
            return Ok(Some(Outcome::Blocked { code, message }));
        }
        let threshold = self.p.program.budget.loop_threshold as usize;
        if let Some(outcome) = self.p.log.with_events(|events| looping(events, threshold)) {
            return Ok(Some(outcome));
        }
        let finished = message.tool_calls.is_empty() && !message.interrupted && message.stop != StopReason::Length;
        let candidate = if self.p.registry.has_return() {
            match succeeded(text::RETURN_NAME) {
                Some(returned) => Some(returned.value["value"].clone()),
                None if finished => {
                    self.append_inbox(InboxSource::System, text::RETURN_REQUIRED)?;
                    None
                }
                None => None,
            }
        } else {
            finished.then(|| Value::String(message.text.clone()))
        };
        let Some(candidate) = candidate else { return Ok(None) };
        let Some(done) = self.p.program.done_when.clone().filter(|d| d.verify.is_some()) else {
            return Ok(Some(Outcome::Completed { value: candidate }));
        };
        let verifier = done.verify.clone().unwrap_or_default();
        let findings = self
            .p
            .registry
            .verify_with(&verifier, &self.p.handles, &candidate, self.step, self.spill_dir.clone(), self.deadline())
            .await
            .map_err(RuntimeError::Protocol)?;
        if findings.is_empty() {
            return Ok(Some(Outcome::Completed { value: candidate }));
        }
        if self.verify_attempts >= done.retries {
            let message =
                format!("`{verifier}` still reports {} finding(s) after {} retries", findings.len(), done.retries);
            return Ok(Some(Outcome::Blocked { code: BlockedCode::VerificationUnsatisfiable, message }));
        }
        self.verify_attempts += 1;
        let framed = text::fill(text::VERIFY_FINDINGS, &[("tool", &verifier), ("findings", &findings.join("\n"))]);
        self.append_inbox(InboxSource::Verify, &framed)?;
        Ok(None)
    }
}

#[async_trait::async_trait]
impl SummaryCall for Episode {
    async fn call(&mut self, system: &str, user: String) -> Result<Answer, RuntimeError> {
        self.write_header(system.to_string(), Vec::new())?;
        let content = vec![ContentBlock::Text { text: user }];
        self.request(Request::Summary(vec![Message::User { content }])).await
    }
}

fn item(source: InboxSource, text: &str) -> InboxItem {
    InboxItem { source, content: vec![ContentBlock::Text { text: text.into() }], from: None, message_id: None }
}

/// Resolves with the reason once the stop signal carries one. Never
/// resolves when the sender is gone, which is the case without a host.
pub async fn wait_stop(mut stop: watch::Receiver<Option<String>>) -> String {
    loop {
        if let Some(reason) = stop.borrow_and_update().clone() {
            return reason;
        }
        if stop.changed().await.is_err() {
            std::future::pending::<()>().await;
        }
    }
}

/// Resolves at `deadline`; never resolves without one.
pub async fn until(deadline: Option<Instant>) {
    match deadline {
        Some(d) => tokio::time::sleep_until(tokio::time::Instant::from_std(d)).await,
        None => std::future::pending().await,
    }
}

/// Pure and read-only calls run concurrently; every other call waits for
/// the calls before it and runs alone. Results come back in issue order.
async fn run_calls(
    registry: Arc<Registry>,
    handles: Handles,
    calls: Vec<ToolCall>,
    step: u32,
    spill_dir: PathBuf,
    deadline: Option<Instant>,
) -> Vec<(ToolValue, u64)> {
    let mut results: Vec<Option<(ToolValue, u64)>> = calls.iter().map(|_| None).collect();
    let mut batch: JoinSet<(usize, ToolValue, u64)> = JoinSet::new();
    async fn drain(batch: &mut JoinSet<(usize, ToolValue, u64)>, results: &mut [Option<(ToolValue, u64)>]) {
        while let Some(joined) = batch.join_next().await {
            if let Ok((i, value, ms)) = joined {
                results[i] = Some((value, ms));
            }
        }
    }
    for (i, call) in calls.into_iter().enumerate() {
        let concurrent = registry.effect(&call.name).is_none_or(|e| e.concurrent());
        let (registry, handles, spill_dir) = (registry.clone(), handles.clone(), spill_dir.clone());
        let task = async move {
            let started = Instant::now();
            let value = registry.dispatch(&handles, &call, step, spill_dir, deadline).await;
            (i, value, started.elapsed().as_millis() as u64)
        };
        if concurrent {
            batch.spawn(task);
        } else {
            drain(&mut batch, &mut results).await;
            let (i, value, ms) = task.await;
            results[i] = Some((value, ms));
        }
    }
    drain(&mut batch, &mut results).await;
    results
        .into_iter()
        .map(|r| r.unwrap_or_else(|| (ToolValue::error("the tool task ended without a result"), 0)))
        .collect()
}

/// Renders a value for the model and writes it to `spill/<call_id>.json`
/// when it exceeds [`SPILL_LIMIT`]. Returns the inlined value, the rendered
/// text, and the spill file name.
fn spill(spill_dir: &Path, call_id: &str, value: ToolValue) -> Result<(Value, String, Option<String>), RuntimeError> {
    let canonical =
        serde_json::to_vec(&value.value).map_err(|e| RuntimeError::Protocol(format!("tool value serializes: {e}")))?;
    let rendered = value.rendered.unwrap_or_else(|| String::from_utf8_lossy(&canonical).into_owned());
    if rendered.len() <= SPILL_LIMIT && canonical.len() <= SPILL_LIMIT {
        return Ok((value.value, rendered, None));
    }
    std::fs::create_dir_all(spill_dir).map_err(foe_log::LogError::Io)?;
    let safe: String =
        call_id.chars().map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '_' }).collect();
    let file = format!("{safe}.json");
    std::fs::write(spill_dir.join(&file), &canonical).map_err(foe_log::LogError::Io)?;
    let mut head_end = SPILL_HEAD.min(rendered.len());
    while !rendered.is_char_boundary(head_end) {
        head_end -= 1;
    }
    let framed = text::fill(
        text::SPILL_FRAME,
        &[
            ("bytes", &canonical.len().to_string()),
            ("path", &format!("spill/{file}")),
            ("head_bytes", &head_end.to_string()),
            ("head", &rendered[..head_end]),
        ],
    );
    let locator = serde_json::json!({ "spill": file, "bytes": canonical.len(), "is_error": value.is_error });
    Ok((locator, framed, Some(file)))
}

/// Records streamed chunks as `assistant/chunk` events and assembles the
/// message. A log write failure is kept and reported after the stream. The
/// workflow executor records its recovery requests through it as well.
pub struct Recorder {
    log: Arc<Log>,
    step: u32,
    request_id: String,
    text: String,
    /// Calls in the order they started, with their argument text so far.
    calls: Vec<(String, String, String)>,
    /// Closed reasoning blocks, then the block still open, if any.
    thinking: Vec<ThinkingBlock>,
    open_thinking: Option<String>,
    terminal: Option<Chunk>,
    failure: Option<LogError>,
}

impl Recorder {
    pub fn new(log: Arc<Log>, step: u32, request_id: String) -> Self {
        Self {
            log,
            step,
            request_id,
            text: String::new(),
            calls: Vec::new(),
            thinking: Vec::new(),
            open_thinking: None,
            terminal: None,
            failure: None,
        }
    }

    /// A signature closes the open block, or forms a block of its own when
    /// no text preceded it: a redacted block, or a provider that withholds
    /// reasoning text, still issues the signature the replay needs.
    fn close_thinking(&mut self, signature: Option<String>) {
        match (self.open_thinking.take(), signature) {
            (Some(text), signature) => self.thinking.push(ThinkingBlock { text, signature }),
            (None, Some(signature)) => {
                self.thinking.push(ThinkingBlock { text: String::new(), signature: Some(signature) })
            }
            (None, None) => {}
        }
    }

    pub fn check(&mut self) -> Result<(), RuntimeError> {
        self.failure.take().map_or(Ok(()), |e| Err(e.into()))
    }

    /// The `Done` or `Error` chunk that ended the stream, taken once.
    pub fn take_terminal(&mut self) -> Option<Chunk> {
        self.terminal.take()
    }

    pub fn message(&self, stop: StopReason, usage: Usage, interrupted: bool) -> AssistantMessage {
        let open = self.open_thinking.clone().map(|text| ThinkingBlock { text, signature: None });
        AssistantMessage {
            step: self.step,
            request_id: self.request_id.clone(),
            text: self.text.clone(),
            tool_calls: self
                .calls
                .iter()
                .map(|(id, name, args)| ToolCall { id: id.clone(), name: name.clone(), args: parse_tolerant(args) })
                .collect(),
            stop,
            usage,
            interrupted,
            thinking: self.thinking.iter().cloned().chain(open).collect(),
        }
    }
}

impl ChunkSink for Recorder {
    fn push(&mut self, chunk: Chunk) {
        let event =
            EventData::AssistantChunk { step: self.step, request_id: self.request_id.clone(), chunk: chunk.clone() };
        if let Err(e) = self.log.append(event) {
            self.failure.get_or_insert(e);
        }
        match chunk {
            Chunk::Text { delta } => {
                self.close_thinking(None);
                self.text.push_str(&delta);
            }
            Chunk::Thinking { delta } => self.open_thinking.get_or_insert_with(String::new).push_str(&delta),
            Chunk::ThinkingSignature { signature } => self.close_thinking(Some(signature)),
            Chunk::ToolCallEnd { .. } => {}
            Chunk::ToolCallStart { id, name } => {
                self.close_thinking(None);
                self.calls.push((id, name, String::new()));
            }
            Chunk::ToolCallDelta { id, delta } => {
                if let Some(call) = self.calls.iter_mut().find(|c| c.0 == id) {
                    call.2.push_str(&delta);
                }
            }
            terminal @ (Chunk::Done { .. } | Chunk::Error { .. }) => self.terminal = Some(terminal),
        }
    }
}

/// Parses streamed tool-call arguments, closing strings, arrays, and
/// objects that a truncated stream left open. A text that cannot be
/// repaired becomes an empty object, which then fails validation.
pub fn parse_tolerant(args: &str) -> Value {
    if args.trim().is_empty() {
        return Value::Object(Default::default());
    }
    if let Ok(value) = serde_json::from_str(args) {
        return value;
    }
    let mut candidates = vec![args.to_string()];
    let trimmed = args.trim_end().trim_end_matches([',', ':']).trim_end();
    candidates.push(trimmed.to_string());
    if let Some(cut) = trimmed.rfind([',', '{', '[']) {
        candidates.push(trimmed[..=cut].trim_end_matches(',').to_string());
    }
    for candidate in candidates {
        if let Ok(value) = serde_json::from_str::<Value>(&close_open(&candidate)) {
            return value;
        }
    }
    Value::Object(Default::default())
}

fn close_open(text: &str) -> String {
    let (mut in_string, mut escaped, mut stack) = (false, false, Vec::new());
    for c in text.chars() {
        match (in_string, escaped, c) {
            (true, true, _) => escaped = false,
            (true, false, '\\') => escaped = true,
            (true, false, '"') => in_string = false,
            (true, _, _) => {}
            (false, _, '"') => in_string = true,
            (false, _, '{') => stack.push('}'),
            (false, _, '[') => stack.push(']'),
            (false, _, '}' | ']') => {
                stack.pop();
            }
            _ => {}
        }
    }
    let mut out = text.to_string();
    if in_string {
        out.push('"');
    }
    out.extend(stack.iter().rev());
    out
}

fn signature(name: &str, args: &Value, result: &Value) -> String {
    format!("{name} {} -> {}", crate::identity::canonical(args), crate::identity::canonical(result))
}

/// The two forms of lack of progress in docs/design.md "Blocking conditions
/// the runtime detects", over the last `threshold` steps: a call signature
/// present in every one of them, or the same non-empty assistant text in
/// every one of them. Summarization requests are not steps.
fn looping(events: &[Event], threshold: usize) -> Option<Outcome> {
    let mut steps: BTreeMap<u32, (String, BTreeSet<String>)> = BTreeMap::new();
    let mut args: BTreeMap<&str, (&str, &Value)> = BTreeMap::new();
    for event in events {
        match &event.data {
            EventData::AssistantMessage(m) if !m.request_id.starts_with(SUMMARY_REQUEST_PREFIX) => {
                steps.entry(m.step).or_default().0 = m.text.clone();
                args.extend(m.tool_calls.iter().map(|c| (c.id.as_str(), (c.name.as_str(), &c.args))));
            }
            EventData::ToolResult(r) => {
                if let Some((name, a)) = args.get(r.call_id.as_str()) {
                    steps.entry(r.step).or_default().1.insert(signature(name, a, &r.value));
                }
            }
            _ => {}
        }
    }
    let recent: Vec<_> = steps.into_values().rev().take(threshold).collect();
    let (text, calls) = recent.get(threshold - 1).and(recent.first())?;
    if let Some(repeated) = calls.iter().find(|s| recent.iter().all(|(_, set)| set.contains(*s))) {
        let message = format!("the call {repeated} returned an identical result in {threshold} consecutive steps");
        return Some(Outcome::Blocked { code: BlockedCode::LoopingToolCall, message });
    }
    if !text.is_empty() && recent.iter().all(|(t, _)| t == text) {
        let message = format!("the assistant produced identical text in {threshold} consecutive steps");
        return Some(Outcome::Blocked { code: BlockedCode::LoopingReasoning, message });
    }
    None
}

#[cfg(test)]
#[path = "loop_test.rs"]
mod tests;
