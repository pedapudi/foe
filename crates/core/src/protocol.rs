//! The host protocol: echoing the log to stdout, reading answers from stdin, forwarding child requests.
//!
//! Implements docs/protocol.md. Standard output is the log mirror. Standard
//! input carries four line types, parsed here and routed to whoever waits:
//! `model/chunk` to the transport, `tool/result` to a host tool call,
//! `inbox/item` to the log, `cancel` to the stop signal. A line tagged with
//! a descendant's `episode_id` is handed to the [`Downlink`] unchanged.

use crate::loop_::{append_inbox_item, lock, until, wait_stop, Log};
use crate::{CallCtx, ChunkSink, ModelRequestBody, Tool, ToolValue, Transport};
use foe_contract::document::ResolvedContract;
use foe_contract::tools::host_spec;
use foe_contract::ToolSpec;
use foe_log::{Chunk, EventData, ExhaustedLimit, InboxItem, InboxSource, ModelRoute, ToolFailure, ToolFailureCode};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncBufReadExt, AsyncRead, BufReader};
use tokio::sync::{mpsc, oneshot, watch};

/// Appends to this episode's inbox. The protocol layer implements it; the
/// spawner and the team tools deliver child results and peer messages
/// through it.
pub trait InboxSink: Send + Sync {
    fn append(&self, item: InboxItem);
}

/// Routes lines tagged for descendant episodes down to the child process
/// that hosts them. The spawner implements it.
pub trait Downlink: Send + Sync {
    fn route(&self, episode_id: &str, line: &str);
    fn cancel_all(&self);
}

/// The log mirror for `--host` mode. `Stdout` locks per call, so a whole
/// line written with one call never interleaves with [`forward_line`].
pub fn stdout_mirror() -> Box<dyn Write + Send> {
    Box::new(std::io::stdout())
}

/// Writes a descendant's tagged event line to standard output. Such a line
/// belongs to the descendant's log; it passes through this process so the
/// root host sees every request in the tree.
pub fn forward_line(line: &str) -> std::io::Result<()> {
    let mut out = std::io::stdout().lock();
    out.write_all(line.as_bytes())?;
    if !line.ends_with('\n') {
        out.write_all(b"\n")?;
    }
    out.flush()
}

/// The provider and model named in `request/header` when the host supplies
/// the transport. The host knows the real route; foe does not.
pub const HOST_ROUTE: &str = "host";

enum RequestSlot {
    /// Chunks that arrived before the transport claimed the request.
    Buffered(Vec<Chunk>),
    Live(mpsc::UnboundedSender<Chunk>),
}

struct Inner {
    self_id: String,
    log: Arc<Log>,
    stop: watch::Sender<Option<String>>,
    downlink: Option<Arc<dyn Downlink>>,
    requests: Mutex<HashMap<String, RequestSlot>>,
    settled: Mutex<HashSet<String>>,
    calls: Mutex<HashMap<String, oneshot::Sender<ToolValue>>>,
    /// Set when standard input reached its end or a protocol error ended
    /// the exchange. Every wait on the host then fails at once.
    closed: AtomicBool,
}

/// This process's side of the protocol.
#[derive(Clone)]
pub struct Host {
    inner: Arc<Inner>,
}

impl Host {
    /// `self_id` is this episode's id; a line tagged with it, or untagged,
    /// is this episode's own answer. Returns the stop signal the loop
    /// watches: `Some(reason)` after `cancel` or a protocol error.
    pub fn new(
        self_id: String,
        log: Arc<Log>,
        downlink: Option<Arc<dyn Downlink>>,
    ) -> (Self, watch::Receiver<Option<String>>) {
        let (stop, stop_rx) = watch::channel(None);
        let inner = Inner {
            self_id,
            log,
            stop,
            downlink,
            requests: Mutex::new(HashMap::new()),
            settled: Mutex::new(HashSet::new()),
            calls: Mutex::new(HashMap::new()),
            closed: AtomicBool::new(false),
        };
        (Self { inner: Arc::new(inner) }, stop_rx)
    }

    /// Reads host lines until end of input or a protocol error.
    pub fn spawn_reader<R: AsyncRead + Unpin + Send + 'static>(&self, stdin: R) -> tokio::task::JoinHandle<()> {
        let host = self.clone();
        tokio::spawn(async move { host.read_lines(stdin).await })
    }

    pub async fn read_lines<R: AsyncRead + Unpin>(&self, stdin: R) {
        let mut lines = BufReader::new(stdin).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            if line.trim().is_empty() {
                continue;
            }
            if let Err(reason) = self.handle_line(&line) {
                self.stop(&format!("protocol: {reason}"));
                break;
            }
        }
        self.close();
    }

    /// Ends the episode as `failed` with `reason`.
    pub fn stop(&self, reason: &str) {
        let _ = self.inner.stop.send(Some(reason.to_string()));
        self.close();
    }

    fn close(&self) {
        self.inner.closed.store(true, Ordering::SeqCst);
        lock(&self.inner.requests).retain(|_, slot| matches!(slot, RequestSlot::Buffered(_)));
        lock(&self.inner.calls).clear();
    }

    fn handle_line(&self, line: &str) -> Result<(), String> {
        let value: Value = serde_json::from_str(line).map_err(|e| format!("line is not a JSON object: {e}"))?;
        if let Some(id) = value.get("episode_id").and_then(Value::as_str).filter(|id| *id != self.inner.self_id) {
            return match &self.inner.downlink {
                Some(downlink) => {
                    downlink.route(id, line);
                    Ok(())
                }
                None => Err(format!("line tagged for episode `{id}`, which this process does not host")),
            };
        }
        let field = |name: &str| value.get(name).and_then(Value::as_str).ok_or_else(|| format!("line lacks `{name}`"));
        match field("type")? {
            "model/chunk" => {
                let request_id = field("request_id")?;
                let chunk: Chunk = serde_json::from_value(value.get("chunk").cloned().unwrap_or(Value::Null))
                    .map_err(|e| format!("model/chunk for `{request_id}`: chunk: {e}"))?;
                self.deliver_chunk(request_id, chunk)
            }
            "tool/result" => {
                let call_id = field("call_id")?;
                let rendered = value.get("rendered").and_then(Value::as_str).map(str::to_string);
                let is_error = value.get("is_error").and_then(Value::as_bool).unwrap_or(false);
                let mut failure = value
                    .get("failure")
                    .filter(|v| !v.is_null())
                    .cloned()
                    .map(serde_json::from_value::<ToolFailure>)
                    .transpose()
                    .map_err(|e| format!("tool/result for `{call_id}` has an invalid `failure`: {e}"))?;
                if failure.is_some() && !is_error {
                    return Err(format!("tool/result for `{call_id}` has `failure` but `is_error` is false"));
                }
                if is_error && failure.is_none() {
                    let message = rendered
                        .clone()
                        .or_else(|| value.get("value")?.get("error")?.as_str().map(str::to_string))
                        .unwrap_or_else(|| "the host tool reported an error".into());
                    failure = Some(ToolFailure {
                        code: ToolFailureCode::OperationFailed,
                        message,
                        retryable: true,
                        details: serde_json::json!({ "source": "host-compatibility" }),
                    });
                }
                let result = ToolValue {
                    value: value
                        .get("value")
                        .cloned()
                        .ok_or_else(|| format!("tool/result for `{call_id}` lacks `value`"))?,
                    rendered,
                    is_error,
                    failure: failure.map(Box::new),
                    subject: None,
                };
                let sender = lock(&self.inner.calls).remove(call_id);
                sender
                    .ok_or_else(|| format!("tool/result names call `{call_id}`, which is unknown or already settled"))?
                    .send(result)
                    .map_err(|_| format!("call `{call_id}` is no longer awaited"))
            }
            "inbox/item" => {
                let item: InboxItem = serde_json::from_value(value.clone()).map_err(|e| format!("inbox/item: {e}"))?;
                if !matches!(item.source, InboxSource::Parent | InboxSource::Child | InboxSource::Peer) {
                    return Err(format!("inbox/item source {:?} is not one a host may send", item.source));
                }
                InboxSink::append(self, item);
                Ok(())
            }
            "cancel" => {
                if let Some(downlink) = &self.inner.downlink {
                    downlink.cancel_all();
                }
                self.stop("cancelled");
                Ok(())
            }
            other => Err(format!("unknown line type `{other}`")),
        }
    }

    fn deliver_chunk(&self, request_id: &str, chunk: Chunk) -> Result<(), String> {
        let terminal = matches!(chunk, Chunk::Done { .. } | Chunk::Error { .. });
        let mut requests = lock(&self.inner.requests);
        match requests.get_mut(request_id) {
            Some(RequestSlot::Live(tx)) => {
                let _ = tx.send(chunk);
                if terminal {
                    requests.remove(request_id);
                    lock(&self.inner.settled).insert(request_id.to_string());
                }
            }
            Some(RequestSlot::Buffered(chunks)) => chunks.push(chunk),
            None if lock(&self.inner.settled).contains(request_id) => {
                return Err(format!("model/chunk names request `{request_id}`, which is already settled"));
            }
            None if self.inner.log.has_request(request_id) => {
                requests.insert(request_id.to_string(), RequestSlot::Buffered(vec![chunk]));
            }
            None => return Err(format!("model/chunk names request `{request_id}`, which was never issued")),
        }
        Ok(())
    }

    /// The transport that asks the host for every model response.
    pub fn transport(&self) -> Arc<dyn Transport> {
        Arc::new(HostTransport { host: self.clone() })
    }

    /// One implementation per `host_tools` entry of `contract`, with the
    /// specification taken from the document.
    pub fn tools(&self, contract: &ResolvedContract) -> Vec<Box<dyn Tool>> {
        contract.host_tools.iter().map(|(name, def)| self.tool(host_spec(name, def))).collect()
    }

    /// A tool that emits `host/tool-call` and waits for the host's
    /// `tool/result`. The spawner uses this for the tools a parent answers.
    pub fn tool(&self, spec: ToolSpec) -> Box<dyn Tool> {
        Box::new(HostTool { host: self.clone(), spec })
    }
}

impl InboxSink for Host {
    fn append(&self, item: InboxItem) {
        if let Err(e) = append_inbox_item(&self.inner.log, item) {
            self.stop(&format!("log: {e}"));
        }
    }
}

struct HostTransport {
    host: Host,
}

fn closed_error(what: &str) -> Chunk {
    Chunk::Error { message: format!("the host closed standard input before answering {what}"), retryable: false }
}

#[async_trait::async_trait]
impl Transport for HostTransport {
    fn route(&self) -> ModelRoute {
        ModelRoute { provider: HOST_ROUTE.into(), model: HOST_ROUTE.into() }
    }

    async fn stream(&self, req: ModelRequestBody, sink: &mut (dyn ChunkSink + Send)) {
        let inner = &self.host.inner;
        let (tx, mut rx) = mpsc::unbounded_channel();
        let buffered = {
            let mut requests = lock(&inner.requests);
            let buffered = match requests.remove(&req.request_id) {
                Some(RequestSlot::Buffered(chunks)) => chunks,
                _ => Vec::new(),
            };
            requests.insert(req.request_id.clone(), RequestSlot::Live(tx));
            buffered
        };
        let settle = || {
            lock(&inner.requests).remove(&req.request_id);
            lock(&inner.settled).insert(req.request_id.clone());
        };
        // Chunks that arrived before the transport claimed the request come
        // first; the rest arrive over the channel. A host that closed
        // standard input answers nothing further, and waiting on the
        // channel would never end, so the closed flag ends the stream in
        // place of a terminal chunk.
        let mut buffered = buffered.into_iter();
        loop {
            let chunk = match buffered.next() {
                Some(chunk) => Some(chunk),
                None if inner.closed.load(Ordering::SeqCst) => None,
                None => rx.recv().await,
            };
            let Some(chunk) = chunk else {
                settle();
                sink.push(closed_error(&format!("request `{}`", req.request_id)));
                return;
            };
            let terminal = matches!(chunk, Chunk::Done { .. } | Chunk::Error { .. });
            sink.push(chunk);
            if terminal {
                settle();
                return;
            }
        }
    }
}

struct HostTool {
    host: Host,
    spec: ToolSpec,
}

#[async_trait::async_trait]
impl Tool for HostTool {
    fn spec(&self) -> &ToolSpec {
        &self.spec
    }

    async fn call(&self, args: Value, ctx: &CallCtx) -> ToolValue {
        let inner = &self.host.inner;
        let (tx, rx) = oneshot::channel();
        lock(&inner.calls).insert(ctx.call_id.clone(), tx);
        if inner.closed.load(Ordering::SeqCst) {
            lock(&inner.calls).remove(&ctx.call_id);
            return ToolValue::unavailable(format!(
                "the host closed standard input before `{}` was called",
                self.spec.name
            ));
        }
        let event = EventData::HostToolCall {
            step: ctx.step,
            call_id: ctx.call_id.clone(),
            name: self.spec.name.clone(),
            args,
        };
        if let Err(e) = inner.log.append(event) {
            lock(&inner.calls).remove(&ctx.call_id);
            return ToolValue::unavailable(format!("`{}` could not be recorded: {e}", self.spec.name));
        }
        // The wait ends three ways besides the answer: standard input
        // closed, the stop signal, and the `seconds` budget. A wait that
        // ends without an answer forgets the call, so a `tool/result` that
        // arrives afterwards is the protocol error it is.
        let name = &self.spec.name;
        let unanswered = |why: String| {
            lock(&inner.calls).remove(&ctx.call_id);
            ToolValue::unavailable(format!("`{name}` went unanswered: {why}"))
        };
        let stopped = |reason| format!("the episode stopped: {reason}");
        tokio::select! {
            answer = rx => match answer {
                Ok(value) => value,
                // The sender is dropped both when standard input ends and
                // when the episode stops; the stop signal says which.
                Err(_) => match inner.stop.borrow().clone() {
                    Some(reason) => unanswered(stopped(reason)),
                    None => unanswered("the host closed standard input".into()),
                },
            },
            reason = wait_stop(inner.stop.subscribe()) => {
                lock(&inner.calls).remove(&ctx.call_id);
                ToolValue::failed(
                    ToolFailureCode::Interrupted,
                    format!("`{name}` went unanswered: {}", stopped(reason)),
                    false,
                    serde_json::json!({}),
                )
            },
            _ = until(ctx.deadline) => {
                lock(&inner.calls).remove(&ctx.call_id);
                ToolValue::failed(
                    ToolFailureCode::BudgetExhausted,
                    format!("`{name}` went unanswered: the budget's seconds elapsed"),
                    false,
                    serde_json::json!({ "limit": ExhaustedLimit::Seconds }),
                )
            },
        }
    }
}

#[cfg(test)]
#[path = "protocol_test.rs"]
mod tests;
