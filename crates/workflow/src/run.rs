//! The executor: fires nodes as the scheduler makes them ready, routes
//! every failure through recovery, and ends the episode with one outcome.
//! Implements docs/workflow.md "Firing", "Completion", and "Recovery".
//!
//! Tool nodes dispatch through the episode's registry. Model nodes are
//! child episodes started through the spawner the binary wired, which
//! reserves their budget and records the spawn events. A nested workflow
//! node runs a further executor over the same log, with its node names
//! prefixed by the path to it. A recovery decision is one model request
//! recorded in this log like any other, with its context as a system inbox
//! item and its answer as an assistant message and a `recover` tool result.

use crate::bind;
use crate::graph::{Produced, Scheduler};
use foe_core::budget::Pool;
use foe_core::config::Program;
use foe_core::harness_text as text;
use foe_core::loop_::{lock, until, wait_stop, Log, Params, Recorder};
use foe_core::registry::{conforms, Handles, Registry, Source};
use foe_core::workflow::{ancestors, Node, WorkflowConfig};
use foe_core::{Effect, ModelRequestBody, RuntimeError, SpawnRequest, Spawner, ToolSpec, ToolValue, Transport};
use foe_log::{
    seed, BlockedCode, BudgetAmount, Chunk, ContentBlock, Event, EventData, ExhaustedLimit, HeaderReason, InboxItem,
    InboxSource, Message, ModelRequest, Outcome, RequestHeader, SpawnContext, ToolCall, ToolResult, WorkflowBranch,
    WorkflowNodeEnd, WorkflowNodeStart, WorkflowRecovery,
};
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;
use tokio::sync::watch;
use tokio::task::JoinSet;

/// Everything one workflow episode needs: what the agent loop takes, the
/// spawner that starts model nodes, and the graph.
pub struct WorkflowParams {
    pub episode: Params,
    pub spawner: Arc<dyn Spawner>,
    pub workflow: WorkflowConfig,
}

/// Runs the workflow to its end and writes `episode/end`.
pub async fn run(params: WorkflowParams) -> Result<Outcome, RuntimeError> {
    let p = params.episode;
    let log = p.log.clone();
    if log.next_seq() == 0 {
        let task = p.start.task.clone();
        log.append(EventData::EpisodeStart(p.start))?;
        let content = vec![ContentBlock::Text { text: task }];
        let item = InboxItem { source: InboxSource::Task, content, from: None, message_id: None };
        log.append(EventData::InboxItem(item))?;
        log.sync()?;
    }
    let shared = Arc::new(Shared {
        log: log.clone(),
        registry: p.registry,
        handles: p.handles,
        transport: p.transport,
        pool: p.pool,
        stop: p.stop,
        spawner: params.spawner,
        program: p.program,
        step: AtomicU32::new(0),
        header: Mutex::new(None),
    });
    let mut executor = Executor::new(shared, String::new(), &params.workflow);
    let outcome = match executor.drive().await {
        Ok(outcome) => outcome,
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

/// What every executor over one log shares, nested ones included.
struct Shared {
    log: Arc<Log>,
    registry: Arc<Registry>,
    handles: Handles,
    transport: Arc<dyn Transport>,
    pool: Arc<Mutex<Pool>>,
    stop: watch::Receiver<Option<String>>,
    spawner: Arc<dyn Spawner>,
    program: Program,
    /// Counts firings, verifications, and recovery requests; a recovery
    /// request's id is drawn from it.
    step: AtomicU32,
    header: Mutex<Option<(u64, RequestHeader)>>,
}

/// Why a firing produced no value. `settled` marks a failure a second
/// attempt cannot change, which ends the episode with `outcome`; otherwise
/// `outcome` is what the episode ends with when recovery is disabled.
struct Trouble {
    cause: String,
    detail: String,
    findings: Vec<String>,
    outcome: Outcome,
    settled: bool,
}

impl Trouble {
    fn recoverable(cause: &str, detail: impl Into<String>, outcome: Outcome) -> Self {
        Self { cause: cause.into(), detail: detail.into(), findings: Vec::new(), outcome, settled: false }
    }

    fn findings(cause: &str, findings: Vec<String>, outcome: Outcome) -> Self {
        Self { detail: findings.join("\n"), findings, ..Self::recoverable(cause, "", outcome) }
    }

    /// A failure a second attempt cannot change.
    fn settled(outcome: Outcome) -> Self {
        let detail = match &outcome {
            Outcome::Failed { error } => error.clone(),
            other => serde_json::to_value(other).map(|v| v.to_string()).unwrap_or_default(),
        };
        Self { cause: "settled".into(), detail, findings: Vec::new(), outcome, settled: true }
    }
}

/// A firing's value and the text its successors receive.
type Output = Result<(Value, String), Trouble>;
type Firing = Pin<Box<dyn Future<Output = Output> + Send>>;

/// An episode's outcome as a node's output: a child's blocked, failed, or
/// exhausted end is recoverable; a nested workflow that exhausted the
/// shared budget is settled.
fn output_of(outcome: Outcome, nested: bool) -> Output {
    match outcome {
        Outcome::Completed { value } => Ok((value.clone(), render(&value))),
        Outcome::Exhausted { limit } if nested => Err(Trouble::settled(Outcome::Exhausted { limit })),
        Outcome::Blocked { code, message } => {
            let cause = format!("blocked: {}", limit_or_code(code));
            Err(Trouble::recoverable(&cause, message.clone(), Outcome::Blocked { code, message }))
        }
        Outcome::Failed { error } => Err(Trouble::recoverable("failed", error.clone(), Outcome::Failed { error })),
        Outcome::Exhausted { limit } => {
            let (cause, detail) = (format!("exhausted: {}", limit_or_code(limit)), "the child spent its budget");
            Err(Trouble::recoverable(&cause, detail, Outcome::Exhausted { limit }))
        }
    }
}

/// The wire name of a limit or a blocked code.
fn limit_or_code(value: impl serde::Serialize) -> String {
    serde_json::to_value(value).ok().and_then(|v| v.as_str().map(str::to_string)).unwrap_or_default()
}

/// A failure message's settled outcome, when it names one: a denied path,
/// an executable that could not start, or a budget limit the pool refused.
fn settled_in(message: &str) -> Option<Outcome> {
    if message.contains("outside every granted root") || message.contains("could not start") {
        return Some(Outcome::Failed { error: message.to_string() });
    }
    let named = message.strip_prefix("budget: the ")?.split(' ').next()?;
    let limit: ExhaustedLimit = serde_json::from_value(Value::String(named.into())).ok()?;
    Some(Outcome::Exhausted { limit })
}

/// A tool result as a node value, or the trouble it represents. For a
/// `tool_defs` executable, `configured` is set and an exit code other than
/// zero or a timeout is a failure, because the node has no judgment to
/// read the code the way the loop's model does.
fn classify_tool(value: ToolValue, configured: bool) -> Output {
    let rendered = value.rendered.unwrap_or_else(|| render(&value.value));
    let exited_badly = value.value["exit_code"] != json!(0) || value.value["timed_out"] == json!(true);
    if !(value.is_error || configured && exited_badly) {
        return Ok((value.value, rendered));
    }
    match settled_in(&rendered) {
        Some(outcome) => Err(Trouble::settled(outcome)),
        None => Err(Trouble::recoverable("tool-error", rendered.clone(), Outcome::Failed { error: rendered })),
    }
}

/// The text successors of a node receive: a string as itself, anything
/// else as compact JSON.
pub fn render(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

fn section(name: &str, body: &str) -> String {
    text::fill(text::WORKFLOW_SECTION, &[("name", name), ("body", body)])
}

struct Fired {
    node: String,
    fire: u32,
    started: Instant,
    result: Output,
}

enum Action {
    Retry(String),
    Amend(String, String),
    Skip,
    Abort(BlockedCode, String),
}

enum Decision {
    Action(Action),
    Failed(String),
    End(Outcome),
}

/// The `recover` tool: the closed action set, with the nodes a retry or
/// amend may name and `skip` present only when the node declares `empty`.
fn recover_spec(targets: &[String], skip: bool) -> ToolSpec {
    let actions: Vec<&str> = ["retry", "amend", "abort"].into_iter().chain(skip.then_some("skip")).collect();
    let codes =
        ["goal-unreachable", "ambiguous-task", "missing-capability", "verification-unsatisfiable", "child-blocked"];
    ToolSpec {
        name: text::RECOVER_NAME.into(),
        description: text::RECOVER_DESCRIPTION.into(),
        instruction: None,
        params: json!({
            "type": "object",
            "properties": {
                "action": { "type": "string", "enum": actions },
                "node": { "type": "string", "enum": targets },
                "note": { "type": "string" },
                "code": { "type": "string", "enum": codes },
                "message": { "type": "string" }
            },
            "required": ["action"],
            "additionalProperties": false
        }),
        effect: Effect::Pure,
    }
}

/// Reads the chosen action and checks it against what was offered.
fn parse_action(args: &Value, targets: &[String], skip: bool) -> Result<Action, String> {
    let field = |name: &str| args.get(name).and_then(Value::as_str).map(str::to_string);
    let target = || {
        let node = field("node").ok_or("`node` is required for retry and amend")?;
        match targets.contains(&node) {
            true => Ok(node),
            false => Err(format!("`{node}` is not the failed node or an ancestor of it that may fire again")),
        }
    };
    match field("action").as_deref() {
        Some("retry") => Ok(Action::Retry(target()?)),
        Some("amend") => Ok(Action::Amend(target()?, field("note").ok_or("`note` is required for amend")?)),
        Some("skip") if skip => Ok(Action::Skip),
        Some("skip") => Err("skip is not offered: the node declares no `empty` value".into()),
        Some("abort") => {
            let code = serde_json::from_value(args.get("code").cloned().unwrap_or(Value::Null))
                .map_err(|_| "`code` is required for abort and is one of the offered codes".to_string())?;
            Ok(Action::Abort(code, field("message").unwrap_or_default()))
        }
        _ => Err("`action` is one of retry, amend, skip, and abort".into()),
    }
}

struct Executor {
    shared: Arc<Shared>,
    /// Empty at the top; the path to a nested workflow node plus `/` inside it.
    prefix: String,
    wf: WorkflowConfig,
    sched: Scheduler,
    tasks: JoinSet<Fired>,
    interventions: u32,
    done_attempts: u32,
}

impl Executor {
    fn new(shared: Arc<Shared>, prefix: String, wf: &WorkflowConfig) -> Self {
        let sched = Scheduler::new(wf);
        Self { shared, prefix, wf: wf.clone(), sched, tasks: JoinSet::new(), interventions: 0, done_attempts: 0 }
    }

    fn full(&self, name: &str) -> String {
        format!("{}{name}", self.prefix)
    }

    fn log(&self, data: EventData) -> Result<Event, RuntimeError> {
        Ok(self.shared.log.append(data)?)
    }

    fn deadline(&self) -> Option<Instant> {
        lock(&self.shared.pool).deadline()
    }

    fn next_step(&self) -> u32 {
        self.shared.step.fetch_add(1, Ordering::SeqCst) + 1
    }

    /// Drives the graph to an outcome, then waits for every firing still
    /// running so that its events precede `episode/end`.
    fn drive(&mut self) -> Pin<Box<dyn Future<Output = Result<Outcome, RuntimeError>> + Send + '_>> {
        Box::pin(async move {
            let outcome = self.schedule().await;
            while let Some(joined) = self.tasks.join_next().await {
                if let Ok(f) = joined {
                    self.sched.finish(&f.node);
                    self.node_end(&f.node, f.fire, f.started, &f.result, None)?;
                }
            }
            outcome
        })
    }

    async fn schedule(&mut self) -> Result<Outcome, RuntimeError> {
        loop {
            if let Some(reason) = self.shared.stop.borrow().clone() {
                return Ok(Outcome::Failed { error: reason });
            }
            if let Some(limit) = lock(&self.shared.pool).exhausted() {
                return Ok(Outcome::Exhausted { limit });
            }
            let mut deferred = false;
            for name in self.sched.ready() {
                let node = &self.sched.nodes[&name];
                let bound = node.max_fires.unwrap_or(1);
                if self.sched.state[&name].fires >= bound {
                    let message =
                        format!("node `{}` would fire again beyond its max_fires of {bound}", self.full(&name));
                    return Ok(Outcome::Blocked { code: BlockedCode::RecoveryExhausted, message });
                }
                let cap = self.shared.program.budget.max_concurrent as usize;
                if node.model.is_some() && lock(&self.shared.pool).active_children() >= cap {
                    deferred = true;
                    continue;
                }
                self.launch(&name)?;
            }
            if self.tasks.is_empty() {
                if deferred {
                    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
                    continue;
                }
                let error = "the workflow stalled: no node is ready and no terminal node completed".to_string();
                return Ok(Outcome::Failed { error });
            }
            let (stop, deadline) = (self.shared.stop.clone(), self.deadline());
            let joined = tokio::select! {
                joined = self.tasks.join_next() => joined,
                reason = wait_stop(stop) => return Ok(Outcome::Failed { error: reason }),
                _ = until(deadline) => return Ok(Outcome::Exhausted { limit: ExhaustedLimit::Seconds }),
            };
            let fired = joined
                .expect("a task is running")
                .map_err(|e| RuntimeError::Protocol(format!("a node task ended abnormally: {e}")))?;
            if let Some(outcome) = self.settle(fired).await? {
                return Ok(outcome);
            }
        }
    }

    /// Starts one firing of a ready node and records `workflow/node-start`.
    fn launch(&mut self, name: &str) -> Result<(), RuntimeError> {
        let sh = self.shared.clone();
        let node: Node = self.sched.nodes[name].clone();
        let full = self.full(name);
        let (fire, findings, note) = self.sched.begin(name);
        let inputs: Vec<(String, Produced)> = self.sched.inputs[name]
            .iter()
            .filter_map(|i| self.sched.state[i].value.as_ref().map(|p| (i.clone(), p.clone())))
            .collect();
        let input_seqs: Vec<u64> = inputs.iter().map(|(_, p)| p.seq).collect();
        let call_id = format!("{full}#{fire}");
        let step = self.next_step();
        let started = Instant::now();
        let mut sections: Vec<String> = inputs.iter().map(|(n, p)| section(n, &p.rendered)).collect();
        if !findings.is_empty() {
            sections.push(section("findings", &findings.join("\n")));
        }
        sections.extend(note.as_deref().map(|n| section("recovery", n)));
        let mut child_id = None;
        let lookup = |n: &str| inputs.iter().find(|(i, _)| i == n).map(|(_, p)| p.value.clone());
        let task: Firing = if let Some(tool) = &node.tool {
            match bind::resolve(node.args.as_ref().unwrap_or(&serde_json::Map::new()), &lookup) {
                Err(reason) => {
                    let outcome = Outcome::Failed { error: format!("node `{full}` {reason}") };
                    Box::pin(async move { Err(Trouble::recoverable("binding-missing", reason, outcome)) })
                }
                Ok(args) => {
                    let call = ToolCall { id: call_id, name: tool.clone(), args };
                    let (deadline, spill) = (self.deadline(), sh.log.dir().join("spill"));
                    let configured = sh.registry.source(tool) == Some(Source::Configured);
                    Box::pin(async move {
                        let value = sh.registry.dispatch(&sh.handles, &call, step, spill, deadline).await;
                        classify_tool(value, configured)
                    })
                }
            }
        } else if let Some(program) = &node.model {
            let reserve = BudgetAmount {
                model_calls: Some(program.budget.model_calls),
                tokens: program.budget.tokens,
                seconds: program.budget.seconds,
            };
            let task = sections.join("\n\n");
            let req = SpawnRequest { program: full.clone(), task, context: SpawnContext::Fresh, reserve, call_id };
            match sh.spawner.spawn(req) {
                Ok(handle) => {
                    child_id = Some(handle.child_id.clone());
                    Box::pin(async move { output_of(handle.run.wait().await.0, false) })
                }
                Err(e) => {
                    let message = e.to_string();
                    let outcome = settled_in(&message).unwrap_or(Outcome::Failed { error: message });
                    Box::pin(async move { Err(Trouble::settled(outcome)) })
                }
            }
        } else {
            let inner = node.workflow.clone().expect("a node has one kind");
            let mut nested = Executor::new(sh.clone(), format!("{full}/"), &inner);
            Box::pin(async move {
                match nested.drive().await {
                    Ok(outcome) => output_of(outcome, true),
                    Err(e) => Err(Trouble::settled(Outcome::Failed { error: e.to_string() })),
                }
            })
        };
        self.log(EventData::WorkflowNodeStart(WorkflowNodeStart { node: full, fire, inputs: input_seqs, child_id }))?;
        let name = name.to_string();
        self.tasks.spawn(async move { Fired { node: name, fire, started, result: task.await } });
        Ok(())
    }

    /// Records `workflow/node-end`. `error` overrides the result's own
    /// error text when given.
    fn node_end(
        &self,
        name: &str,
        fire: u32,
        started: Instant,
        result: &Output,
        error: Option<String>,
    ) -> Result<Event, RuntimeError> {
        let (value, rendered, error) = match result {
            Ok((value, rendered)) => (value.clone(), rendered.clone(), error),
            Err(trouble) => (Value::Null, String::new(), Some(trouble.detail.clone())),
        };
        let duration_ms = started.elapsed().as_millis() as u64;
        let end = WorkflowNodeEnd { node: self.full(name), fire, value, rendered, error, duration_ms };
        self.log(EventData::WorkflowNodeEnd(end))
    }

    async fn verify_with(&self, verifier: &str, value: &Value) -> Result<Vec<String>, RuntimeError> {
        let sh = &self.shared;
        sh.registry
            .verify_with(verifier, &sh.handles, value, self.next_step(), sh.log.dir().join("spill"), self.deadline())
            .await
            .map_err(RuntimeError::Protocol)
    }

    /// Verifies, records, and propagates a finished firing. `Some` ends the episode.
    async fn settle(&mut self, fired: Fired) -> Result<Option<Outcome>, RuntimeError> {
        let (name, fire, started) = (fired.node.clone(), fired.fire, fired.started);
        self.sched.finish(&name);
        let node = self.sched.nodes[&name].clone();
        if fired.result.is_err() {
            self.node_end(&name, fire, started, &fired.result, None)?;
        }
        let (value, rendered) = match fired.result {
            Ok(produced) => produced,
            Err(trouble) => return self.trouble(&name, fire, trouble).await,
        };
        let result = Ok((value.clone(), rendered.clone()));
        if let Some(verifier) = &node.verify {
            let findings = self.verify_with(verifier, &value).await?;
            if !findings.is_empty() {
                let error = format!("`{verifier}` reported {} finding(s)", findings.len());
                self.node_end(&name, fire, started, &result, Some(error))?;
                let state = self.sched.state.get_mut(&name).expect("a known node");
                if state.verify_attempts < node.retries {
                    state.verify_attempts += 1;
                    state.findings = findings;
                    self.sched.force(&name);
                    return Ok(None);
                }
                let message = format!("`{verifier}` still reports findings after {} retries", node.retries);
                let outcome = Outcome::Blocked { code: BlockedCode::VerificationUnsatisfiable, message };
                return self.trouble(&name, fire, Trouble::findings("verify-findings", findings, outcome)).await;
            }
        }
        let label = value.get("branch").and_then(Value::as_str).filter(|l| node.branches.contains_key(*l));
        if !node.branches.is_empty() && label.is_none() {
            let labels: Vec<&String> = node.branches.keys().collect();
            let detail = format!("the value names no branch label among {labels:?}");
            self.node_end(&name, fire, started, &result, Some(detail.clone()))?;
            let outcome = Outcome::Failed { error: format!("node `{}`: {detail}", self.full(&name)) };
            return self.trouble(&name, fire, Trouble::recoverable("branch-missing", detail, outcome)).await;
        }
        let label = label.map(str::to_string);
        let event = self.node_end(&name, fire, started, &result, None)?;
        self.produce(&name, fire, Produced { value, rendered, seq: event.seq }, label).await
    }

    /// Propagates a value along the admitted edges and completes the
    /// workflow when the node is terminal or the label has no successors.
    async fn produce(
        &mut self,
        name: &str,
        fire: u32,
        produced: Produced,
        label: Option<String>,
    ) -> Result<Option<Outcome>, RuntimeError> {
        let value = produced.value.clone();
        let node = self.sched.nodes[name].clone();
        self.sched.produced(name, produced, label.as_deref());
        let mut ends = node.terminal;
        if let Some(label) = label {
            let successors = node.branches[&label].clone();
            ends |= successors.is_empty();
            let branch = WorkflowBranch { node: self.full(name), fire, label, successors };
            self.log(EventData::WorkflowBranch(branch))?;
        }
        match ends {
            true => self.complete(name, fire, value).await,
            false => Ok(None),
        }
    }

    /// Applies the episode's `done_when` to a completing value. Findings
    /// re-fire the nearest model ancestor up to `retries` times, then go
    /// to recovery at the completing node. A nested workflow completes
    /// with its value as it stands.
    async fn complete(&mut self, name: &str, fire: u32, value: Value) -> Result<Option<Outcome>, RuntimeError> {
        let done = self.shared.program.done_when.clone().filter(|_| self.prefix.is_empty());
        let Some(done) = done else {
            return Ok(Some(Outcome::Completed { value }));
        };
        let mut findings = Vec::new();
        if let Some(Err(reason)) = done.returns.as_ref().map(|schema| conforms(schema, &value)) {
            findings.push(format!("the terminal value does not conform to done_when.returns: {reason}"));
        }
        if let (true, Some(verifier)) = (findings.is_empty(), &done.verify) {
            findings = self.verify_with(verifier, &value).await?;
        }
        if findings.is_empty() {
            return Ok(Some(Outcome::Completed { value }));
        }
        if let (true, Some(target)) = (self.done_attempts < done.retries, self.sched.nearest_model(name)) {
            self.done_attempts += 1;
            self.sched.state.get_mut(&target).expect("a known node").findings = findings;
            self.sched.force(&target);
            return Ok(None);
        }
        let message = format!("done_when still reports {} finding(s) after {} retries", findings.len(), done.retries);
        let outcome = Outcome::Blocked { code: BlockedCode::VerificationUnsatisfiable, message };
        self.trouble(name, fire, Trouble::findings("done-when-findings", findings, outcome)).await
    }

    /// Routes a failure: settled failures and disabled recovery end the
    /// episode; otherwise one recovery decision is made and applied. Boxed
    /// because a skip produces a value, which may complete, which may fail.
    fn trouble<'a>(
        &'a mut self,
        name: &'a str,
        fire: u32,
        trouble: Trouble,
    ) -> Pin<Box<dyn Future<Output = Result<Option<Outcome>, RuntimeError>> + Send + 'a>> {
        Box::pin(self.recover(name, fire, trouble))
    }

    async fn recover(&mut self, name: &str, fire: u32, trouble: Trouble) -> Result<Option<Outcome>, RuntimeError> {
        if trouble.settled || !self.wf.recovery.enabled {
            return Ok(Some(trouble.outcome));
        }
        let full = self.full(name);
        if self.interventions >= self.wf.recovery.max_interventions {
            let message = format!(
                "recovery.max_interventions ({}) is reached; node `{full}` failed: {}: {}",
                self.wf.recovery.max_interventions, trouble.cause, trouble.detail
            );
            return Ok(Some(Outcome::Blocked { code: BlockedCode::RecoveryExhausted, message }));
        }
        let node = self.sched.nodes[name].clone();
        let mut eligible: BTreeSet<String> = ancestors(&self.sched.preds, name);
        eligible.insert(name.to_string());
        let targets: Vec<String> = eligible
            .into_iter()
            .filter(|n| self.sched.state[n].fires < self.sched.nodes[n].max_fires.unwrap_or(1))
            .collect();
        let skip = node.empty.is_some();
        let message = self.recovery_message(name, fire, &trouble, &targets, skip);
        let action = match self.decide(recover_spec(&targets, skip), message, &targets, skip).await? {
            Decision::Action(action) => action,
            Decision::Failed(reason) => {
                let message = format!("the recovery decision for node `{full}` failed: {reason}");
                return Ok(Some(Outcome::Blocked { code: BlockedCode::RecoveryFailed, message }));
            }
            Decision::End(outcome) => return Ok(Some(outcome)),
        };
        self.interventions += 1;
        let (action_name, target, note) = match &action {
            Action::Retry(t) => ("retry", Some(t.clone()), None),
            Action::Amend(t, n) => ("amend", Some(t.clone()), Some(n.clone())),
            Action::Skip => ("skip", None, None),
            Action::Abort(..) => ("abort", None, None),
        };
        let event = self.log(EventData::WorkflowRecovery(WorkflowRecovery {
            node: full,
            fire,
            cause: trouble.cause,
            action: action_name.into(),
            target,
            note,
            intervention: self.interventions,
        }))?;
        match action {
            Action::Retry(target) => self.sched.force(&target),
            Action::Amend(target, note) => {
                self.sched.state.get_mut(&target).expect("a known node").note = Some(note);
                self.sched.force(&target);
            }
            Action::Skip => {
                let empty = node.empty.clone().expect("skip was offered");
                let label = empty.get("branch").and_then(Value::as_str).filter(|l| node.branches.contains_key(*l));
                let produced = Produced { value: empty.clone(), rendered: render(&empty), seq: event.seq };
                return self.produce(name, fire, produced, label.map(str::to_string)).await;
            }
            Action::Abort(code, message) => return Ok(Some(Outcome::Blocked { code, message })),
        }
        Ok(None)
    }

    /// The context of a recovery decision: the failed node's inputs and
    /// any `recovery.follows` widening, then the failure and the offer.
    fn recovery_message(&self, name: &str, fire: u32, trouble: &Trouble, targets: &[String], skip: bool) -> String {
        let node = &self.sched.nodes[name];
        let mut seen = BTreeSet::new();
        let widened = node.recovery.iter().flat_map(|r| &r.follows);
        let mut sections = Vec::new();
        for input in self.sched.inputs[name].iter().chain(widened).filter(|i| seen.insert((*i).clone())) {
            let body = self.sched.state[input].value.as_ref().map_or("(no value yet)", |p| p.rendered.as_str());
            sections.push(section(input, body));
        }
        let detail = match trouble.findings.is_empty() {
            true => trouble.detail.clone(),
            false => section("findings", &trouble.findings.join("\n")),
        };
        let offered = if targets.is_empty() { "(no node)".to_string() } else { targets.join(", ") };
        let skip = if skip { "available" } else { "not available; the node declares no empty value" };
        let failure = text::fill(
            text::WORKFLOW_RECOVERY_FAILURE,
            &[
                ("node", &self.full(name)),
                ("fire", &fire.to_string()),
                ("cause", &trouble.cause),
                ("detail", &detail),
                ("targets", &offered),
                ("skip", skip),
            ],
        );
        sections.push(section("failure", &failure));
        sections.join("\n\n")
    }

    /// One model request for a recovery decision, recorded like any other
    /// request: header, system inbox item, request, chunks, message, and
    /// the `recover` tool result.
    async fn decide(
        &mut self,
        spec: ToolSpec,
        message: String,
        targets: &[String],
        skip: bool,
    ) -> Result<Decision, RuntimeError> {
        let sh = self.shared.clone();
        if let Some(limit) = lock(&sh.pool).exhausted() {
            return Ok(Decision::End(Outcome::Exhausted { limit }));
        }
        let system = text::WORKFLOW_RECOVERY_INSTRUCTION.to_string();
        let header_seq = {
            let mut current = lock(&sh.header);
            let reason = if current.is_some() { HeaderReason::Change } else { HeaderReason::Initial };
            let header = RequestHeader {
                reason,
                system: system.clone(),
                tools: vec![spec.schema()],
                model: sh.transport.route(),
            };
            let same =
                |h: &RequestHeader| h.system == header.system && h.tools == header.tools && h.model == header.model;
            if !current.as_ref().is_some_and(|(_, h)| same(h)) {
                let event = sh.log.append(EventData::RequestHeader(header.clone()))?;
                *current = Some((event.seq, header));
            }
            current.as_ref().map(|(seq, _)| *seq).expect("a header was written")
        };
        let content = vec![ContentBlock::Text { text: message }];
        let item = InboxItem { source: InboxSource::System, content: content.clone(), from: None, message_id: None };
        let item = sh.log.append(EventData::InboxItem(item))?;
        let step = self.next_step();
        let request_id = format!("rq_{step:04}");
        let messages = vec![Message::User { content }];
        sh.log.append(EventData::ModelRequest(ModelRequest {
            step,
            attempt: 1,
            request_id: request_id.clone(),
            header_seq,
            consumed: vec![item.seq],
            messages: messages.clone(),
        }))?;
        lock(&sh.pool).note_request();
        let body = ModelRequestBody {
            request_id: request_id.clone(),
            system,
            tools: vec![spec.schema()],
            messages,
            max_output_tokens: sh.program.model.as_ref().and_then(|m| m.max_output_tokens),
        };
        let mut recorder = Recorder::new(sh.log.clone(), step, request_id);
        let ended = tokio::select! {
            _ = sh.transport.stream(body, &mut recorder) => None,
            reason = wait_stop(sh.stop.clone()) => Some(Outcome::Failed { error: reason }),
            _ = until(self.deadline()) => Some(Outcome::Exhausted { limit: ExhaustedLimit::Seconds }),
        };
        recorder.check()?;
        if let Some(outcome) = ended {
            return Ok(Decision::End(outcome));
        }
        let (stop, usage) = match recorder.take_terminal() {
            Some(Chunk::Done { stop, usage }) => (stop, usage),
            Some(Chunk::Error { message, .. }) => {
                return Ok(Decision::Failed(format!("the model request failed: {message}")));
            }
            _ => return Ok(Decision::Failed("the model request ended without a terminal chunk".into())),
        };
        let message = recorder.message(stop, usage, false);
        sh.log.append(EventData::AssistantMessage(message.clone()))?;
        lock(&sh.pool).note_usage(usage);
        let mut decision = Decision::Failed("the response made no call to `recover`".into());
        for call in &message.tool_calls {
            let decides = call.name == text::RECOVER_NAME && matches!(decision, Decision::Failed(_));
            let result = match decides.then(|| parse_action(&call.args, targets, skip)) {
                Some(Ok(action)) => {
                    decision = Decision::Action(action);
                    ToolValue::ok(call.args.clone(), "Applied.")
                }
                Some(Err(reason)) => {
                    decision = Decision::Failed(reason.clone());
                    ToolValue::error(text::fill(
                        text::INVALID_ARGS,
                        &[("name", text::RECOVER_NAME), ("reason", &reason)],
                    ))
                }
                None => ToolValue::error(format!("`{}` was not applied: one call to `recover` decides", call.name)),
            };
            sh.log.append(EventData::ToolResult(ToolResult {
                step,
                call_id: call.id.clone(),
                name: call.name.clone(),
                value: result.value,
                rendered: result.rendered.unwrap_or_default(),
                is_error: result.is_error,
                spill: None,
                duration_ms: 0,
                synthetic: false,
            }))?;
        }
        Ok(decision)
    }
}

#[cfg(test)]
#[path = "run_test.rs"]
mod tests;
