//! The agent loop: assemble, stream, execute, settle; the three step rules; request-failure recovery; looping detection.
//!
//! Implements docs/design.md (The episode). One call to [`run`] drives one
//! episode from its first request to its `episode/end`. The loop is the
//! only writer of its log; the protocol layer and the spawner append
//! through the same shared [`Log`].

use crate::budget::Pool;
use crate::config::Program;
use crate::harness_text as text;
use crate::inbox::Inbox;
use crate::registry::{Handles, Registry};
use crate::{ChunkSink, ModelRequestBody, RuntimeError, ToolValue, Transport};
use foe_log::{
    fold, seed, AssistantMessage, BlockedCode, Chunk, ContentBlock, EpisodeStart, Event, EventData, ExhaustedLimit,
    HeaderReason, InboxItem, InboxSource, LogError, ModelRequest, Outcome, RequestHeader, RetryCause, StopReason,
    ThinkingBlock, ToolCall, ToolResult, Usage,
};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet, VecDeque};
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
    pub fn create(dir: &Path, mirror: Option<Box<dyn std::io::Write + Send>>) -> Result<Self, LogError> {
        let writer = foe_log::append::Writer::create(dir, mirror)?;
        Ok(Self { dir: dir.to_path_buf(), inner: Mutex::new((writer, Vec::new())) })
    }

    /// Opens a log that already has events, for example one that seeding
    /// wrote. The mirror first receives the file as it stands, so a host
    /// reading standard output sees the seeded prefix as well.
    pub fn open(dir: &Path, mut mirror: Option<Box<dyn std::io::Write + Send>>) -> Result<Self, LogError> {
        let events = fold::read_all(dir)?;
        if let Some(mirror) = &mut mirror {
            std::io::copy(&mut std::fs::File::open(dir.join(fold::LOG_FILE))?, mirror)?;
            mirror.flush()?;
        }
        let writer = foe_log::append::Writer::open(dir, mirror)?;
        Ok(Self { dir: dir.to_path_buf(), inner: Mutex::new((writer, events)) })
    }

    pub fn create_or_open(dir: &Path, mirror: Option<Box<dyn std::io::Write + Send>>) -> Result<Self, LogError> {
        if dir.join(fold::LOG_FILE).exists() {
            Self::open(dir, mirror)
        } else {
            Self::create(dir, mirror)
        }
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
/// folds the existing events into it before the first step.
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

/// Runs the episode to its end and writes `episode/end`. Every tool call
/// left without a result by the way the episode ended receives a synthetic
/// result first, so the log is well-formed.
pub async fn run(params: Params) -> Result<Outcome, RuntimeError> {
    let log = params.log.clone();
    let outcome = match Episode::new(params) {
        Ok(mut episode) => match episode.drive().await {
            Ok(outcome) => outcome,
            Err(RuntimeError::Log(e)) => return Err(e.into()),
            Err(e) => Outcome::Failed { error: e.to_string() },
        },
        Err(RuntimeError::Log(e)) => return Err(e.into()),
        Err(e) => Outcome::Failed { error: e.to_string() },
    };
    for result in log.with_events(seed::orphan_results) {
        log.append(EventData::ToolResult(ToolResult { rendered: text::INTERRUPTED_RESULT.into(), ..result }))?;
    }
    log.sync()?;
    log.append(EventData::EpisodeEnd { outcome: outcome.clone() })?;
    log.sync()?;
    Ok(outcome)
}

enum Flow {
    Continue,
    End(Outcome),
}

struct Episode {
    log: Arc<Log>,
    program: Program,
    registry: Arc<Registry>,
    handles: Handles,
    transport: Arc<dyn Transport>,
    pool: Arc<Mutex<Pool>>,
    stop: watch::Receiver<Option<String>>,
    inbox: Inbox,
    header: Option<(u64, RequestHeader)>,
    step: u32,
    requests: u64,
    history: History,
    verify_attempts: u32,
    spill_dir: PathBuf,
}

impl Episode {
    fn new(p: Params) -> Result<Self, RuntimeError> {
        if p.log.next_seq() == 0 {
            let task = p.start.task.clone();
            p.log.append(EventData::EpisodeStart(p.start))?;
            p.log.append(EventData::InboxItem(InboxItem {
                source: InboxSource::Task,
                content: vec![ContentBlock::Text { text: task }],
                from: None,
                message_id: None,
            }))?;
            p.log.sync()?;
        }
        let events = p.log.events();
        let state = fold::fold(&events)?;
        {
            let mut pool = lock(&p.pool);
            events.iter().for_each(|e| pool.apply(&e.data));
        }
        let step = events
            .iter()
            .filter_map(|e| match &e.data {
                EventData::ModelRequest(r) => Some(r.step),
                _ => None,
            })
            .max()
            .unwrap_or(0);
        Ok(Self {
            inbox: Inbox::from_state(&state),
            header: state.header_seq.zip(state.header),
            step,
            requests: state.model_calls,
            history: History::from_events(&events, p.program.budget.loop_threshold as usize),
            verify_attempts: 0,
            spill_dir: p.log.dir().join("spill"),
            log: p.log,
            program: p.program,
            registry: p.registry,
            handles: p.handles,
            transport: p.transport,
            pool: p.pool,
            stop: p.stop,
        })
    }

    async fn drive(&mut self) -> Result<Outcome, RuntimeError> {
        loop {
            if let Some(reason) = self.stop.borrow().clone() {
                return Ok(Outcome::Failed { error: reason });
            }
            self.step += 1;
            if let Some(limit) = self.exhausted() {
                return Ok(Outcome::Exhausted { limit });
            }
            self.write_header_if_changed()?;
            let message = match self.request().await? {
                Requested::Message(message) => message,
                Requested::Interrupted => continue,
                Requested::End(outcome) => return Ok(outcome),
            };
            let results = match self.execute(&message).await? {
                Ok(results) => results,
                Err(outcome) => return Ok(outcome),
            };
            if let Flow::End(outcome) = self.settle(&message, &results).await? {
                return Ok(outcome);
            }
        }
    }

    fn exhausted(&self) -> Option<ExhaustedLimit> {
        lock(&self.pool).exhausted()
    }

    fn deadline(&self) -> Option<Instant> {
        lock(&self.pool).deadline()
    }

    fn append_inbox(&mut self, item: InboxItem) -> Result<(), RuntimeError> {
        append_inbox_item(&self.log, item)?;
        Ok(())
    }

    fn write_header_if_changed(&mut self) -> Result<(), RuntimeError> {
        let system = self.registry.system_prompt(&self.program.instructions);
        let tools = self.registry.schemas();
        let model = self.transport.route();
        let unchanged =
            self.header.as_ref().is_some_and(|(_, h)| h.system == system && h.tools == tools && h.model == model);
        if unchanged {
            return Ok(());
        }
        let reason = if self.header.is_some() { HeaderReason::Change } else { HeaderReason::Initial };
        let header = RequestHeader { reason, system, tools, model };
        let event = self.log.append(EventData::RequestHeader(header.clone()))?;
        self.header = Some((event.seq, header));
        Ok(())
    }

    /// One model request with bounded retries. See docs/design.md "Failure
    /// of a model request".
    async fn request(&mut self) -> Result<Requested, RuntimeError> {
        let mut attempt = 0;
        loop {
            attempt += 1;
            if let Some(limit) = self.exhausted() {
                return Ok(Requested::End(Outcome::Exhausted { limit }));
            }
            if attempt > MAX_ATTEMPTS {
                let message = format!("{MAX_ATTEMPTS} attempts at step {} failed", self.step);
                return Ok(Requested::End(Outcome::Blocked { code: BlockedCode::RecoveryExhausted, message }));
            }
            self.requests += 1;
            let request_id = format!("rq_{:04}", self.requests);
            self.log.with_events(|events| self.inbox.absorb(events));
            let consumed = self.inbox.pending();
            let (header_seq, header) = self.header.clone().expect("header written before the request");
            let messages = self.log.with_events(|events| fold::derive_messages(events, u64::MAX, &consumed));
            self.log.append(EventData::ModelRequest(ModelRequest {
                step: self.step,
                attempt,
                request_id: request_id.clone(),
                header_seq,
                consumed: consumed.clone(),
                messages: messages.clone(),
            }))?;
            self.inbox.consume(&consumed);
            lock(&self.pool).note_request();
            let body = ModelRequestBody {
                request_id: request_id.clone(),
                system: header.system,
                tools: header.tools,
                messages,
                max_output_tokens: self.program.model.as_ref().and_then(|m| m.max_output_tokens),
            };
            let mut recorder = Recorder::new(self.log.clone(), self.step, request_id);
            let transport = self.transport.clone();
            let streamed = tokio::select! {
                _ = transport.stream(body, &mut recorder) => None,
                reason = wait_stop(self.stop.clone()) => Some(Outcome::Failed { error: reason }),
                _ = until(self.deadline()) => Some(Outcome::Exhausted { limit: ExhaustedLimit::Seconds }),
            };
            recorder.check()?;
            if let Some(outcome) = streamed {
                if !recorder.calls.is_empty() {
                    self.log.append(EventData::AssistantMessage(recorder.message(
                        StopReason::Interrupted,
                        Usage::default(),
                        true,
                    )))?;
                }
                return Ok(Requested::End(outcome));
            }
            let (cause, retryable) = match recorder.terminal.take() {
                Some(Chunk::Done { stop, usage }) => {
                    let message = recorder.message(stop, usage, false);
                    self.log.append(EventData::AssistantMessage(message.clone()))?;
                    lock(&self.pool).note_usage(usage);
                    return Ok(Requested::Message(message));
                }
                Some(Chunk::Error { message, retryable }) if recorder.calls.is_empty() && recorder.text.is_empty() => {
                    let lower = message.to_ascii_lowercase();
                    let cause = if lower.contains("rate") || lower.contains("429") {
                        RetryCause::RateLimit
                    } else {
                        RetryCause::Provider
                    };
                    if !retryable {
                        return Ok(Requested::End(Outcome::Failed {
                            error: format!("model request failed: {message}"),
                        }));
                    }
                    (cause, true)
                }
                Some(Chunk::Error { message, retryable }) if recorder.calls.is_empty() => {
                    if !retryable {
                        return Ok(Requested::End(Outcome::Failed {
                            error: format!("model request failed: {message}"),
                        }));
                    }
                    (RetryCause::Interrupted, true)
                }
                Some(Chunk::Error { .. }) => {
                    let message = recorder.message(StopReason::Interrupted, Usage::default(), true);
                    self.log.append(EventData::AssistantMessage(message.clone()))?;
                    for call in &message.tool_calls {
                        self.append_result(call, ToolValue::error(text::INTERRUPTED_RESULT), 0, true)?;
                    }
                    return Ok(Requested::Interrupted);
                }
                _ => (RetryCause::Transport, true),
            };
            debug_assert!(retryable);
            let delay_ms = (BACKOFF_BASE_MS << (attempt - 1).min(4)).min(BACKOFF_CAP_MS);
            self.log.append(EventData::RequestRetry { step: self.step, attempt, cause, delay_ms })?;
            tokio::select! {
                _ = tokio::time::sleep(Duration::from_millis(delay_ms)) => {}
                reason = wait_stop(self.stop.clone()) => return Ok(Requested::End(Outcome::Failed { error: reason })),
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
            self.registry.clone(),
            self.handles.clone(),
            message.tool_calls.clone(),
            self.step,
            self.spill_dir.clone(),
            deadline,
        );
        let values = tokio::select! {
            values = run => values,
            reason = wait_stop(self.stop.clone()) => return Ok(Err(Outcome::Failed { error: reason })),
            _ = until(deadline) => return Ok(Err(Outcome::Exhausted { limit: ExhaustedLimit::Seconds })),
        };
        for (call, (value, duration_ms)) in message.tool_calls.iter().zip(values) {
            results.push(self.append_result(call, value, duration_ms, false)?);
            if self.registry.effect(&call.name).is_some_and(|e| !e.concurrent()) {
                self.log.sync()?;
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
        let result = ToolResult {
            step: self.step,
            call_id: call.id.clone(),
            name: call.name.clone(),
            value,
            rendered,
            is_error: false,
            spill,
            duration_ms,
            synthetic,
        };
        let result = ToolResult { is_error: spill_is_error(&result), ..result };
        self.log.append(EventData::ToolResult(result.clone()))?;
        Ok(result)
    }

    /// Block, looping, `done_when`, then budget. A turn that completes the
    /// task on the last permitted call completes the episode; the budget
    /// ends it only when work remains.
    async fn settle(&mut self, message: &AssistantMessage, results: &[ToolResult]) -> Result<Flow, RuntimeError> {
        match self.settle_outcome(message, results).await? {
            Flow::Continue => Ok(match self.exhausted() {
                Some(limit) => Flow::End(Outcome::Exhausted { limit }),
                None => Flow::Continue,
            }),
            end => Ok(end),
        }
    }

    async fn settle_outcome(
        &mut self,
        message: &AssistantMessage,
        results: &[ToolResult],
    ) -> Result<Flow, RuntimeError> {
        let succeeded = |name: &str| results.iter().find(|r| r.name == name && !r.is_error && !r.synthetic);
        if let Some(blocked) = succeeded(text::BLOCK_NAME) {
            let code = serde_json::from_value(blocked.value["code"].clone()).unwrap_or(BlockedCode::GoalUnreachable);
            let message = blocked.value["message"].as_str().unwrap_or_default().to_string();
            return Ok(Flow::End(Outcome::Blocked { code, message }));
        }
        let signatures = results
            .iter()
            .filter_map(|r| {
                message.tool_calls.iter().find(|c| c.id == r.call_id).map(|c| signature(&c.name, &c.args, &r.value))
            })
            .collect();
        self.history.push(signatures, message.text.clone());
        let n = self.history.threshold;
        if let Some(repeated) = self.history.looping_call() {
            let message = format!("the call {repeated} returned an identical result in {n} consecutive steps");
            return Ok(Flow::End(Outcome::Blocked { code: BlockedCode::LoopingToolCall, message }));
        }
        if self.history.looping_text() {
            let message = format!("the assistant produced identical text in {n} consecutive steps");
            return Ok(Flow::End(Outcome::Blocked { code: BlockedCode::LoopingReasoning, message }));
        }
        let finished = message.tool_calls.is_empty() && !message.interrupted && message.stop != StopReason::Length;
        let candidate = if self.registry.has_return() {
            match succeeded(text::RETURN_NAME) {
                Some(returned) => Some(returned.value["value"].clone()),
                None if finished => {
                    self.append_inbox(system_item(text::RETURN_REQUIRED))?;
                    None
                }
                None => None,
            }
        } else {
            finished.then(|| Value::String(message.text.clone()))
        };
        let Some(candidate) = candidate else { return Ok(Flow::Continue) };
        let Some(done) = self.program.done_when.clone().filter(|d| d.verify.is_some()) else {
            return Ok(Flow::End(Outcome::Completed { value: candidate }));
        };
        let verifier = done.verify.clone().unwrap_or_default();
        let findings = self
            .registry
            .verify(&self.handles, &candidate, self.step, self.spill_dir.clone(), self.deadline())
            .await
            .map_err(RuntimeError::Protocol)?;
        if findings.is_empty() {
            return Ok(Flow::End(Outcome::Completed { value: candidate }));
        }
        if self.verify_attempts >= done.retries {
            let message =
                format!("`{verifier}` still reports {} finding(s) after {} retries", findings.len(), done.retries);
            return Ok(Flow::End(Outcome::Blocked { code: BlockedCode::VerificationUnsatisfiable, message }));
        }
        self.verify_attempts += 1;
        let framed = text::fill(text::VERIFY_FINDINGS, &[("tool", &verifier), ("findings", &findings.join("\n"))]);
        self.append_inbox(InboxItem {
            source: InboxSource::Verify,
            content: vec![ContentBlock::Text { text: framed }],
            from: None,
            message_id: None,
        })?;
        Ok(Flow::Continue)
    }
}

enum Requested {
    Message(AssistantMessage),
    /// An interrupted message and its synthetic results were written.
    Interrupted,
    End(Outcome),
}

fn system_item(text: &str) -> InboxItem {
    InboxItem {
        source: InboxSource::System,
        content: vec![ContentBlock::Text { text: text.into() }],
        from: None,
        message_id: None,
    }
}

/// Resolves with the reason once the stop signal carries one. Never
/// resolves when the sender is gone, which is the case without a host.
async fn wait_stop(mut stop: watch::Receiver<Option<String>>) -> String {
    loop {
        if let Some(reason) = stop.borrow_and_update().clone() {
            return reason;
        }
        if stop.changed().await.is_err() {
            std::future::pending::<()>().await;
        }
    }
}

async fn until(deadline: Option<Instant>) {
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

fn spill_is_error(result: &ToolResult) -> bool {
    result.value.get("error").is_some() && result.value.as_object().is_some_and(|o| o.len() == 1)
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
/// message. A log write failure is kept and reported after the stream.
struct Recorder {
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
    fn new(log: Arc<Log>, step: u32, request_id: String) -> Self {
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

    fn thinking_blocks(&self) -> Vec<ThinkingBlock> {
        let mut blocks = self.thinking.clone();
        blocks.extend(self.open_thinking.clone().map(|text| ThinkingBlock { text, signature: None }));
        blocks
    }

    fn check(&mut self) -> Result<(), RuntimeError> {
        self.failure.take().map_or(Ok(()), |e| Err(e.into()))
    }

    fn message(&self, stop: StopReason, usage: Usage, interrupted: bool) -> AssistantMessage {
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
            thinking: self.thinking_blocks(),
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

/// The last `threshold` steps, for detecting the two forms of lack of
/// progress in docs/design.md "Blocking conditions the runtime detects".
struct History {
    threshold: usize,
    calls: VecDeque<BTreeSet<String>>,
    texts: VecDeque<String>,
}

impl History {
    fn from_events(events: &[Event], threshold: usize) -> Self {
        let mut history = Self { threshold, calls: VecDeque::new(), texts: VecDeque::new() };
        let mut steps: BTreeMap<u32, (String, BTreeSet<String>)> = BTreeMap::new();
        let mut args: BTreeMap<String, (String, Value)> = BTreeMap::new();
        for event in events {
            match &event.data {
                EventData::AssistantMessage(m) => {
                    steps.entry(m.step).or_default().0 = m.text.clone();
                    for c in &m.tool_calls {
                        args.insert(c.id.clone(), (c.name.clone(), c.args.clone()));
                    }
                }
                EventData::ToolResult(r) => {
                    if let Some((name, a)) = args.get(&r.call_id) {
                        steps.entry(r.step).or_default().1.insert(signature(name, a, &r.value));
                    }
                }
                _ => {}
            }
        }
        for (_, (text, calls)) in steps {
            history.push(calls, text);
        }
        history
    }

    fn push(&mut self, calls: BTreeSet<String>, text: String) {
        self.calls.push_back(calls);
        self.texts.push_back(text);
        while self.calls.len() > self.threshold {
            self.calls.pop_front();
            self.texts.pop_front();
        }
    }

    /// A call signature present in every one of the last `threshold` steps.
    fn looping_call(&self) -> Option<&String> {
        if self.calls.len() < self.threshold {
            return None;
        }
        self.calls[0].iter().find(|s| self.calls.iter().all(|set| set.contains(*s)))
    }

    fn looping_text(&self) -> bool {
        self.texts.len() >= self.threshold
            && !self.texts[0].is_empty()
            && self.texts.iter().all(|t| *t == self.texts[0])
    }
}

#[cfg(test)]
#[path = "loop_test.rs"]
mod tests;
