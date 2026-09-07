//! The agent loop: assemble, stream, execute, settle; the three step rules; request-failure recovery; looping detection.
//!
//! Implements docs/design.md (The episode). One call to [`run`] drives one
//! episode from its first request to its `episode/end`. The loop is the
//! only writer of its log; the protocol layer and the spawner append
//! through the same shared [`Log`]. Before each step's request the loop
//! consults the context policy, which may replace the oldest part of the
//! projection with a summary; docs/compaction.md specifies that.

use crate::budget::Pool;
use crate::context::{Answer, ContextPolicy, ContextState, Summarized, SummaryCall};
use crate::inbox::Inbox;
use crate::registry::{Handles, Registry};
use crate::spawn::Router;
use crate::{result_budget, ChunkSink, ModelRequestBody, RuntimeError, ToolValue, Transport};
use foe_contract::document::{completion_evidence_required, ResolvedContract};
use foe_contract::fingerprint::{canonical, sha256_hex};
use foe_contract::harness_text as text;
use foe_log::{
    fold, seed, AssistantMessage, BlockedCode, Chunk, CompactionStart, CompactionTrigger, ContentBlock, EpisodeStart,
    Event, EventData, ExhaustedLimit, HeaderReason, InboxItem, InboxSource, LogError, Message, ModelRequest, Outcome,
    RequestHeader, RetryCause, StopReason, ThinkingBlock, ToolCall, ToolResult, ToolSchema, Usage, VerificationResult,
    VerificationStatus, SUMMARY_REQUEST_PREFIX,
};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};
use tokio::sync::watch;
use tokio::task::JoinSet;

/// A canonical value serializing longer than this is written to `spill/`
/// and replaced by a locator. The rendering beside it needs no such rule:
/// the turn budget has already bounded it.
pub const SPILL_LIMIT: usize = 64 * 1024;
/// Non-outage failed attempts per step before the episode is blocked as
/// `recovery-exhausted`. A provider-reported outage is bounded by the
/// budget instead: waiting costs only time and model calls, which the
/// budget already meters.
pub const MAX_ATTEMPTS: u32 = 5;
const BACKOFF_BASE_MS: u64 = 500;
const BACKOFF_CAP_MS: u64 = 8_000;
const OUTAGE_BACKOFF_CAP_MS: u64 = 60_000;
/// How long the teardown waits for children it asked to end before it
/// records their settlement itself.
const SETTLE_GRACE: Duration = Duration::from_secs(10);
/// How often a wait on children or on arrivals rereads its evidence.
pub const SETTLE_POLL: Duration = Duration::from_millis(20);

pub fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

/// The episode's log, shared between the loop, the protocol layer, and the
/// spawner. Keeps every event in memory beside the file so that message
/// derivation and loop detection never reread the file.
pub struct Log {
    dir: PathBuf,
    inner: Mutex<(foe_log::append::Writer, Vec<Event>)>,
    failure: watch::Sender<Option<String>>,
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
        Ok(Self { dir: dir.to_path_buf(), inner: Mutex::new((writer, events)), failure: watch::channel(None).0 })
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    pub fn append(&self, data: EventData) -> Result<Event, LogError> {
        self.record(&mut lock(&self.inner), data)
    }

    fn record(&self, inner: &mut (foe_log::append::Writer, Vec<Event>), data: EventData) -> Result<Event, LogError> {
        self.check()?;
        let event = self.capture(inner.0.append(data))?;
        inner.1.push(event.clone());
        Ok(event)
    }

    pub fn sync(&self) -> Result<(), LogError> {
        self.capture(lock(&self.inner).0.sync())
    }

    fn capture<T>(&self, result: Result<T, LogError>) -> Result<T, LogError> {
        result.inspect_err(|error| {
            if self.failure.borrow().is_none() {
                self.failure.send_replace(Some(error.to_string()));
            }
        })
    }

    pub fn check(&self) -> Result<(), LogError> {
        self.failure.borrow().clone().map_or(Ok(()), |error| Err(LogError::Recording(error)))
    }

    pub async fn failed(&self) -> LogError {
        LogError::Recording(wait_stop(self.failure.subscribe()).await)
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

/// Writes the immutable prefix of a fresh episode before any external input
/// can reach its log. A resumed or seeded episode already has its prefix.
pub fn initialize(log: &Log, start: &EpisodeStart) -> Result<(), LogError> {
    if log.next_seq() == 0 {
        log.append(EventData::EpisodeStart(start.clone()))?;
    }
    if log.next_seq() == 1 {
        let task = lock(&log.inner).0.state().start.as_ref().expect("episode/start was recorded").task.clone();
        log.append(EventData::InboxItem(item(InboxSource::Task, &task)))?;
        log.sync()?;
    }
    Ok(())
}

/// Everything one episode needs. `start` is written when the log is empty;
/// a seeded log keeps its own. `pool` is shared with the spawner; `run`
/// restores its recorded consumption once. Call `Pool::restore` before admitting children. `context`
/// compacts the conversation when it outgrows the window; `None` never
/// compacts.
pub struct Params {
    pub log: Arc<Log>,
    pub start: EpisodeStart,
    pub contract: ResolvedContract,
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
    /// The episode's process sessions. Teardown stops episode-lifetime
    /// sessions and releases explicitly authorized task-lifetime sessions.
    pub sessions: Option<Arc<dyn crate::Sessions>>,
    pub context: Option<Arc<dyn ContextPolicy>>,
}

/// Appends an `inbox/item` the moment it arrives, dropping a peer message
/// whose `message_id` the log already holds. The protocol layer and the
/// spawner deliver items through this.
pub fn append_inbox_item(log: &Log, item: InboxItem) -> Result<Option<Event>, LogError> {
    let mut inner = lock(&log.inner);
    if crate::inbox::is_duplicate(&inner.1, &item) {
        return Ok(None);
    }
    log.record(&mut inner, EventData::InboxItem(item)).map(Some)
}

/// Appends one `session`-source item per session exit not yet reported:
/// the session subject line, with `from` naming the session id. The loop
/// posts before each request, while a turn's calls run so that a `wait`
/// sees the arrival, and at settlement.
fn post_session_exits(log: &Log, sessions: Option<&Arc<dyn crate::Sessions>>) -> Result<(), LogError> {
    for status in sessions.iter().flat_map(|s| s.take_exited()) {
        let content = vec![ContentBlock::Text { text: crate::session::subject(&status) }];
        let item =
            InboxItem { source: InboxSource::Session, content, from: Some(status.id.to_string()), message_id: None };
        log.append(EventData::InboxItem(item))?;
    }
    Ok(())
}

/// Runs the episode to its end, closes what its log left open through
/// [`settle`], and writes `episode/end`.
pub async fn run(params: Params) -> Result<Outcome, RuntimeError> {
    let (log, pool) = (params.log.clone(), params.pool.clone());
    let (children, sessions) = (params.children.clone(), params.sessions.clone());
    let driven = async { Episode::new(params)?.drive().await };
    finish(&log, &pool, children.as_deref(), sessions, driven).await
}

/// Stops work on recording failure and cleans up before returning any error.
pub async fn finish(
    log: &Log,
    pool: &Mutex<Pool>,
    children: Option<&Router>,
    sessions: Option<Arc<dyn crate::Sessions>>,
    work: impl std::future::Future<Output = Result<Outcome, RuntimeError>>,
) -> Result<Outcome, RuntimeError> {
    let driven = tokio::select! {
        biased;
        error = log.failed() => Err(error.into()),
        result = work => result,
    };
    let settled = settle(log, pool, children, sessions).await;
    log.check()?;
    let outcome = match driven {
        Ok(outcome) => outcome,
        Err(RuntimeError::Log(e)) => return Err(e.into()),
        Err(e) => Outcome::Failed { error: e.to_string() },
    };
    settled?;
    log.append(EventData::EpisodeEnd { outcome: outcome.clone() })?;
    log.sync()?;
    Ok(outcome)
}

/// Closes every obligation the log opened, so that `episode/end` is valid.
/// See docs/log-format.md "Open obligations".
///
/// Settlement records one synthetic `tool/result` for every process session
/// still alive. It stops an episode-lifetime session. It records an observed-
/// alive task-lifetime session as released to the enclosing task environment,
/// with the process and process-group ids needed for cleanup. Every session
/// exit not yet reported is then posted as a
/// `session`-source inbox item, so the one-item-per-lifetime rule holds to
/// the end of the log. A child still running is asked to end, and the `spawn/end` and
/// `budget/release` its reservation owes are awaited for [`SETTLE_GRACE`].
/// Whatever is still open after that, including every tool call left
/// without a result, is closed by the synthetic events the log crate
/// produces. The workflow executor ends its episodes through this too.
pub async fn settle(
    log: &Log,
    pool: &Mutex<Pool>,
    children: Option<&Router>,
    sessions: Option<Arc<dyn crate::Sessions>>,
) -> Result<(), RuntimeError> {
    if lock(pool).active_children() > 0 {
        if let Some(children) = children {
            children.cancel_all();
        }
        settled_children(pool, Some(Instant::now() + SETTLE_GRACE)).await;
    }
    if let Some(sessions) = sessions {
        let settler = sessions.clone();
        let settled = tokio::task::spawn_blocking(move || settler.settle())
            .await
            .map_err(|e| RuntimeError::Protocol(format!("session settlement task failed: {e}")))?;
        let step = log.with_events(latest_step);
        let mut recording = Ok(());
        for settlement in settled {
            let (value, subject) = settlement.result();
            let result = log.append(EventData::ToolResult(ToolResult {
                step,
                call_id: format!("session-{}-settle", settlement.status.id),
                name: crate::session::SESSION_TOOL.into(),
                value,
                rendered: subject.clone(),
                is_error: false,
                failure: None,
                spill: None,
                subject: Some(subject),
                duration_ms: 0,
                synthetic: true,
            }));
            if result.is_err() && settlement.released_to_task {
                let stopper = sessions.clone();
                let _ = tokio::task::spawn_blocking(move || stopper.stop(settlement.status.id)).await;
            }
            recording = recording.and(result.map(|_| ()));
        }
        recording?;
        post_session_exits(log, Some(&sessions))?;
    }
    for data in log.with_events(seed::closing_events) {
        log.append(data)?;
    }
    log.sync()?;
    Ok(())
}

/// The step of the most recent `model/request`, or zero before the first.
fn latest_step(events: &[Event]) -> u32 {
    events
        .iter()
        .rev()
        .find_map(|e| match &e.data {
            EventData::ModelRequest(r) => Some(r.step),
            _ => None,
        })
        .unwrap_or(0)
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

/// One authoritative verifier invocation, recorded. Runs the verifier
/// through the registry and appends the one `verification/result` event
/// the invocation owes: `accepted` for an empty finding list, `findings`
/// otherwise, and `failed` when the verifier could not judge. The agent
/// loop and the workflow executor both verify through this, so no
/// authoritative invocation goes unrecorded, and every recorded event
/// attests the canonical-JSON digest of the exact candidate it judged.
/// Returns the verifier's judgment and the appended event's `seq`.
#[allow(clippy::too_many_arguments)]
pub async fn verify_recorded(
    log: &Log,
    registry: &Registry,
    handles: &Handles,
    runtime_build: &str,
    verifier: &str,
    step: u32,
    candidate: &Value,
    spill_dir: PathBuf,
    deadline: Option<Instant>,
) -> Result<(Result<Vec<String>, String>, u64), RuntimeError> {
    let started = Instant::now();
    let judged = registry.verify_with(verifier, handles, candidate, step, spill_dir, deadline).await;
    let (status, findings, error) = match &judged {
        Ok(findings) if findings.is_empty() => (VerificationStatus::Accepted, Vec::new(), None),
        Ok(findings) => (VerificationStatus::Findings, findings.clone(), None),
        Err(error) => (VerificationStatus::Failed, Vec::new(), Some(error.clone())),
    };
    let event = log.append(EventData::VerificationResult(VerificationResult {
        step,
        tool: verifier.into(),
        verifier_fingerprint: registry.verifier_fingerprint(verifier, runtime_build),
        status,
        findings,
        error,
        candidate_sha256: Some(format!("sha256:{}", sha256_hex(canonical(candidate).as_bytes()))),
        duration_ms: started.elapsed().as_millis() as u64,
    }))?;
    Ok((judged, event.seq))
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
        initialize(&p.log, &p.start)?;
        let events = p.log.events();
        let state = fold::fold(&events)?;
        lock(&p.pool).restore(&events, foe_log::append::now_millis());
        Ok(Self {
            inbox: Inbox::from_state(&state),
            header: state.header_seq.zip(state.header),
            step: latest_step(&events),
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
            let final_request = {
                let pool = lock(&self.p.pool);
                pool.exhausted().is_none() && pool.remaining().model_calls == Some(1)
            };
            if final_request {
                self.append_inbox(InboxSource::System, text::FINAL_REQUEST)?;
            }
            post_session_exits(&self.p.log, self.p.sessions.as_ref())?;
            self.write_header(self.p.registry.system_prompt(&self.p.contract.instructions), self.p.registry.schemas())?;
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
        Ok((!ok && cut.exceeds_window).then_some(Outcome::Exhausted { limit: ExhaustedLimit::ContextWindow }))
    }

    /// One model request with bounded retries. See docs/design.md "Failure
    /// of a model request". A summarization request is attempted once.
    async fn request(&mut self, kind: Request) -> Result<Answer, RuntimeError> {
        let summary = matches!(kind, Request::Summary(_));
        let mut attempt = 0;
        let mut faults = 0u32;
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
            let configured = self.p.contract.model.as_ref().and_then(|m| m.max_output_tokens);
            let max_output_tokens = match lock(&self.p.pool).request_max_output(configured) {
                Ok(cap) => cap,
                Err(limit) => return Ok(Answer::Ended(Outcome::Exhausted { limit })),
            };
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
                max_output_tokens,
            }))?;
            self.request_seq = event.seq;
            self.inbox.consume(&consumed);
            lock(&self.p.pool).note_request();
            let body = ModelRequestBody {
                request_id: request_id.clone(),
                system: header.system,
                tools: header.tools,
                messages,
                max_output_tokens,
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
                        self.append_result(call, ToolValue::error(text::INTERRUPTED_RESULT), None, 0, true)?;
                    }
                    return Ok(Answer::Interrupted);
                }
                _ => (RetryCause::Transport, "the stream ended without a terminal chunk".to_string()),
            };
            if summary {
                return Ok(Answer::Failed(error));
            }
            // A provider-reported outage is explicitly retryable and
            // repeating fixes it; its bound is the budget. Every other
            // cause keeps the attempt ceiling, tested before the delay is
            // computed so the episode never waits out a delay for an
            // attempt it will not make.
            let outage = matches!(cause, RetryCause::Provider | RetryCause::RateLimit);
            if !outage {
                faults += 1;
                if faults >= MAX_ATTEMPTS {
                    let message = format!("{MAX_ATTEMPTS} attempts at step {} failed", self.step);
                    return Ok(Answer::Ended(Outcome::Blocked { code: BlockedCode::RecoveryExhausted, message }));
                }
            }
            let cap = if outage { OUTAGE_BACKOFF_CAP_MS } else { BACKOFF_CAP_MS };
            let delay_ms = (BACKOFF_BASE_MS << (attempt - 1).min(7)).min(cap);
            let funds = |d: &Instant| {
                tokio::time::Instant::now() + Duration::from_millis(delay_ms) < tokio::time::Instant::from_std(*d)
            };
            if outage && !self.deadline().as_ref().is_none_or(funds) {
                let message = format!(
                    "provider unavailable through {attempt} attempts at step {}; the remaining seconds budget cannot fund another",
                    self.step
                );
                return Ok(Answer::Ended(Outcome::Blocked { code: BlockedCode::RecoveryExhausted, message }));
            }
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
                results.push(self.append_result(call, ToolValue::error(text::LENGTH_LIMIT_ERROR), None, 0, false)?);
            }
            return Ok(Ok(results));
        }
        if message.tool_calls.is_empty() {
            return Ok(Ok(results));
        }
        let deadline = self.deadline();
        let run = run_calls(
            self.p.log.clone(),
            self.p.registry.clone(),
            self.p.handles.clone(),
            message.tool_calls.clone(),
            self.step,
            self.spill_dir.clone(),
            deadline,
        );
        // Session exits are posted while the calls run, so a blocking call
        // such as `wait` observes the arrival rather than outwaiting it.
        let posting = async {
            loop {
                tokio::time::sleep(SETTLE_POLL).await;
                if let Err(e) = post_session_exits(&self.p.log, self.p.sessions.as_ref()) {
                    break e;
                }
            }
        };
        let values = tokio::select! {
            values = run => values,
            e = posting => return Err(e.into()),
            reason = wait_stop(self.p.stop.clone()) => return Ok(Err(Outcome::Failed { error: reason })),
            _ = until(deadline) => return Ok(Err(Outcome::Exhausted { limit: ExhaustedLimit::Seconds })),
        };
        // The turn's results are bounded together, before any is appended,
        // so that the log and every later request carry the same text.
        let (mut values, durations): (Vec<ToolValue>, Vec<u64>) = values.into_iter().unzip();
        let archives =
            result_budget::bound(&mut values, &message.tool_calls, self.step, self.p.registry.has_retrieve());
        for (((call, value), archive), duration_ms) in
            message.tool_calls.iter().zip(values).zip(archives).zip(durations)
        {
            results.push(self.append_result(call, value, archive, duration_ms, false)?);
            if self.p.registry.effect(&call.name).is_some_and(|e| !e.concurrent()) {
                self.p.log.sync()?;
            }
        }
        Ok(Ok(results))
    }

    fn append_result(
        &self,
        call: &ToolCall,
        value: ToolValue,
        archive: Option<crate::retrieval::ArchivedRendering>,
        duration_ms: u64,
        synthetic: bool,
    ) -> Result<ToolResult, RuntimeError> {
        let cite = completion_evidence_required(self.p.contract.done_when.as_ref());
        append_result(&self.p.log, &self.spill_dir, self.step, call, value, archive, duration_ms, synthetic, cite)
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
            let code = serde_json::from_value(blocked.value["code"].clone()).expect("block schema validates the code");
            let message = blocked.value["message"].as_str().expect("block schema validates the message").to_string();
            return Ok(Some(Outcome::Blocked { code, message }));
        }
        let threshold = self.p.contract.budget.loop_threshold as usize;
        if let Some(outcome) = self.p.log.with_events(|events| looping(events, threshold)) {
            return Ok(Some(outcome));
        }
        let finished = message.tool_calls.is_empty() && !message.interrupted && message.stop != StopReason::Length;
        let verifier_called = self
            .p
            .contract
            .done_when
            .as_ref()
            .and_then(|done| done.verify.as_deref())
            .is_some_and(|name| succeeded(name).is_some());
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
            (finished || verifier_called).then(|| Value::String(message.text.clone()))
        };
        let Some(candidate) = candidate else { return Ok(None) };
        if completion_evidence_required(self.p.contract.done_when.as_ref()) {
            let findings = learned_findings(&self.p.log, &candidate);
            if !findings.is_empty() {
                let message = text::fill(text::INVALID_ARGS, &[("name", text::RETURN_NAME), ("reason", &findings)]);
                self.append_inbox(InboxSource::System, &message)?;
                return Ok(None);
            }
        }
        let Some(done) = self.p.contract.done_when.clone().filter(|d| d.verify.is_some()) else {
            return Ok(Some(Outcome::Completed { value: candidate }));
        };
        let verifier = done.verify.clone().unwrap_or_default();
        let (judged, _) = verify_recorded(
            &self.p.log,
            &self.p.registry,
            &self.p.handles,
            &self.p.start.runtime.build,
            &verifier,
            self.step,
            &candidate,
            self.spill_dir.clone(),
            self.deadline(),
        )
        .await?;
        let findings = judged.map_err(RuntimeError::Protocol)?;
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

fn learned_findings(log: &Log, candidate: &Value) -> String {
    let Some(items) = candidate.get("learned").and_then(Value::as_array).filter(|items| !items.is_empty()) else {
        return "`value.learned` is a non-empty array".into();
    };
    log.with_events(|events| {
        for (index, item) in items.iter().enumerate() {
            let seq = item.get("seq").and_then(Value::as_u64).unwrap_or(u64::MAX);
            let result =
                usize::try_from(seq).ok().and_then(|seq| events.get(seq)).and_then(|event| match &event.data {
                    EventData::ToolResult(result) if !result.is_error && !result.synthetic => Some(result),
                    _ => None,
                });
            let Some(result) = result else {
                return format!("`learned[{index}].seq` {seq} does not name a successful tool/result");
            };
            if foe_log::artifact::read_canonical(&log.dir().join("spill"), seq, result).is_err() {
                return format!("`learned[{index}].seq` {seq} does not reconstruct");
            }
        }
        String::new()
    })
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

/// Appends one `tool/result`, spilling a large canonical value first. The
/// loop writes every model-issued result through this, and [`InnerCalls`]
/// writes every inner result through it.
#[allow(clippy::too_many_arguments)]
fn append_result(
    log: &Log,
    spill_dir: &Path,
    step: u32,
    call: &ToolCall,
    value: ToolValue,
    archive: Option<crate::retrieval::ArchivedRendering>,
    duration_ms: u64,
    synthetic: bool,
    cite_seq: bool,
) -> Result<ToolResult, RuntimeError> {
    let (subject, is_error, failure) = (value.subject.clone(), value.is_error, value.failure.as_deref().cloned());
    if let Some(archive) = archive {
        let archive = crate::retrieval::retain(spill_dir, step, &call.id, &archive)?;
        log.append(EventData::ToolRenderingArchive(archive))?;
    }
    let (value, mut rendered, spill) = spill(spill_dir, value)?;
    let mut inner = lock(&log.inner);
    if cite_seq {
        rendered.insert_str(0, &format!("[seq {}]\n", inner.0.next_seq()))
    }
    let result = ToolResult {
        step,
        call_id: call.id.clone(),
        name: call.name.clone(),
        value,
        rendered,
        is_error,
        failure,
        spill,
        subject,
        duration_ms,
        synthetic,
    };
    log.record(&mut inner, EventData::ToolResult(result.clone()))?;
    Ok(result)
}

/// The composer the loop hands the composing tool for one outer call: each
/// inner call appends a `tool/inner-call` event, dispatches through the
/// ordinary registry with the episode's handles, and appends the inner
/// `tool/result`, which derived messages exclude. The composing tool
/// itself, `block`, and the synthesized `return` are refused before any
/// event is written: the first would recurse, and the other two have
/// meaning only in a model-issued top-level call.
struct InnerCalls {
    log: Arc<Log>,
    registry: Arc<Registry>,
    handles: Handles,
    outer_call_id: String,
    step: u32,
    spill_dir: PathBuf,
    deadline: Option<Instant>,
    index: std::sync::atomic::AtomicU32,
}

#[async_trait::async_trait]
impl crate::Composer for InnerCalls {
    async fn call(&self, name: &str, args: Value) -> Result<(Value, bool), RuntimeError> {
        if [crate::COMPOSING_TOOL, text::BLOCK_NAME, text::RETURN_NAME].contains(&name) {
            let message = format!("`{name}` is not callable from a `{}` contract", crate::COMPOSING_TOOL);
            return Ok((serde_json::json!({ "error": message }), true));
        }
        let index = self.index.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let call = ToolCall { id: format!("{}_{index}", self.outer_call_id), name: name.into(), args };
        self.log.append(EventData::ToolInnerCall(foe_log::ToolInnerCall {
            outer_call_id: self.outer_call_id.clone(),
            call_id: call.id.clone(),
            index,
            name: call.name.clone(),
            args: call.args.clone(),
        }))?;
        let started = Instant::now();
        let value =
            self.registry.dispatch(&self.handles, &call, self.step, self.spill_dir.clone(), self.deadline, None).await;
        // The contract receives the canonical value even when the log holds
        // a spill locator in its place.
        let canonical = value.value.clone();
        let ms = started.elapsed().as_millis() as u64;
        Ok((
            canonical,
            append_result(&self.log, &self.spill_dir, self.step, &call, value, None, ms, false, false)?.is_error,
        ))
    }
}

/// Pure and read-only calls run concurrently; every other call waits for
/// the calls before it and runs alone. Results come back in issue order.
async fn run_calls(
    log: Arc<Log>,
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
        let composer: Option<Arc<dyn crate::Composer>> = (call.name == crate::COMPOSING_TOOL).then(|| {
            Arc::new(InnerCalls {
                log: log.clone(),
                registry: registry.clone(),
                handles: handles.clone(),
                outer_call_id: call.id.clone(),
                step,
                spill_dir: spill_dir.clone(),
                deadline,
                index: 0.into(),
            }) as Arc<dyn crate::Composer>
        });
        let log = log.clone();
        let task = async move {
            let started = Instant::now();
            let value = match log.check() {
                Ok(()) => registry.dispatch(&handles, &call, step, spill_dir, deadline, composer).await,
                Err(error) => ToolValue::from_cap_error(&call.name, crate::CapError::Log(error)),
            };
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

/// Writes a canonical value to a content-derived file under `spill/` when it exceeds
/// [`SPILL_LIMIT`]. Returns the inlined value, which is then a locator, the
/// rendered text, and the spill file name.
fn spill(spill_dir: &Path, value: ToolValue) -> Result<(Value, String, Option<String>), RuntimeError> {
    let canonical =
        serde_json::to_vec(&value.value).map_err(|e| RuntimeError::Protocol(format!("tool value serializes: {e}")))?;
    let rendered = value.rendered.unwrap_or_else(|| String::from_utf8_lossy(&canonical).into_owned());
    if canonical.len() <= SPILL_LIMIT {
        return Ok((value.value, rendered, None));
    }
    let digest = crate::retrieval::digest(&canonical);
    let file = format!("result-{}.json", digest.trim_start_matches("sha256:"));
    foe_log::artifact::retain(&spill_dir.join(&file), &canonical).map_err(foe_log::LogError::Io)?;
    let framed = text::fill(
        text::SPILL_FRAME,
        &[("bytes", &canonical.len().to_string()), ("path", &format!("spill/{file}")), ("head", &rendered)],
    );
    let locator =
        serde_json::json!({ "spill": file, "bytes": canonical.len(), "is_error": value.is_error, "digest": digest });
    Ok((locator, framed, Some(file)))
}

/// Records streamed chunks as `assistant/chunk` events and assembles the
/// message. The shared log retains a recording failure and wakes the
/// executor, which cancels the stream. Workflow recovery uses the same recorder.
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
        Ok(self.log.check()?)
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
        if self.log.check().is_err() {
            return;
        }
        let event =
            EventData::AssistantChunk { step: self.step, request_id: self.request_id.clone(), chunk: chunk.clone() };
        if self.log.append(event).is_err() {
            return;
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
    format!("{name} {} -> {}", foe_contract::fingerprint::canonical(args), foe_contract::fingerprint::canonical(result))
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
