//! Child episodes: process start, lineage, budget reservation, outcome collection.
//!
//! A child is a further `foe` process. The parent is the child's host: it
//! writes the child's configuration under `children/<child_id>/`, starts
//! the child with standard input and output piped, reads every event the
//! child writes, forwards the ones that need a host answer upward tagged
//! with the child's id, routes tagged answers back down, and collects the
//! child's `episode/end`. See docs/protocol.md "Children".
//!
//! Two files are written beside the child's log before it starts.
//! `config.json` is the configuration the child is launched with, derived
//! from the parent's `programs` entry with `version`, `model`, and
//! `sandbox` inherited. `lineage.json` names the child's own id, its parent,
//! and its team lead, for the child's `episode/start`; a child that reads it
//! sends its `notify`, `send`, and `team` calls to this process, which
//! answers them through the [`ChildObserver`].
//!
//! The parent never writes the child's log, and this module never writes
//! the parent's. `budget/reserve`, `spawn/start`, `spawn/end`, and
//! `budget/release` are the loop's to write around a call to
//! [`Spawner::spawn`] and a wait on [`ChildRun`].

use crate::{CapError, SpawnHandle, SpawnRequest, Spawner, ToolValue};
use foe_config::{Budget, ChildProgram, Config};
use foe_log::seed::SeedHeader;
use foe_log::{BudgetAmount, Event, EventData, InboxItem, Outcome, SpawnContext, Usage};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::ffi::OsString;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use tokio::sync::watch;

/// The link from this episode to the process hosting it.
pub trait Uplink: Send + Sync {
    /// Writes one line to the host. The line is a descendant's event that
    /// needs a host answer; it carries `episode_id` and has no line feed.
    fn forward(&self, line: &str);

    /// Whether a process above this one can answer what is forwarded. A
    /// process with no host answers nothing, so a `host/tool-call` it would
    /// forward is refused where it stands rather than left waiting.
    fn answers(&self) -> bool;
}

/// The parent's side of a child's log. The team coordinator implements this.
pub trait ChildObserver: Send + Sync {
    /// Sees every event a direct child writes, after forwarding, answering,
    /// and settlement have been handled.
    fn observe(&self, child_id: &str, event: &Event);

    /// Answers a child's `host/tool-call` here rather than forwarding it.
    /// `None` forwards the call to the host above.
    fn host_call(&self, child_id: &str, name: &str, args: &serde_json::Value) -> Option<ToolValue> {
        let _ = (child_id, name, args);
        None
    }

    /// Sees the child's outcome once its output has ended: the one in its
    /// `episode/end`, or a failure when the process ended without one.
    fn ended(&self, child_id: &str, outcome: &Outcome) {
        let _ = (child_id, outcome);
    }
}

/// Standard input of every running child, and the direct child under which
/// each known descendant runs. Shared by the spawner, which registers
/// children, and the host protocol reader, which routes answers.
#[derive(Default)]
pub struct Router {
    inner: Mutex<RouterState>,
}

#[derive(Default)]
struct RouterState {
    children: HashMap<String, ChildStdin>,
    below: HashMap<String, String>,
}

impl Router {
    pub fn new() -> Self {
        Router::default()
    }

    /// Delivers a host answer tagged `episode_id` to the direct child whose
    /// subtree contains that episode. The tag stays on the line.
    pub fn route(&self, episode_id: &str, line: &str) -> Result<(), CapError> {
        let child =
            {
                let inner = self.inner.lock().unwrap();
                if inner.children.contains_key(episode_id) {
                    episode_id.to_string()
                } else {
                    inner.below.get(episode_id).cloned().ok_or_else(|| {
                        CapError::Invalid(format!("episode {episode_id}: no running child leads to it"))
                    })?
                }
            };
        self.write(&child, line)
    }

    /// Writes an inbox item to a direct child.
    pub fn send_inbox(&self, child_id: &str, item: &InboxItem) -> Result<(), CapError> {
        let mut value = serde_json::to_value(item).map_err(|e| CapError::Invalid(e.to_string()))?;
        value["type"] = "inbox/item".into();
        self.write(child_id, &value.to_string())
    }

    /// Sends `cancel` to every running child.
    pub fn cancel_all(&self) {
        let ids: Vec<String> = self.inner.lock().unwrap().children.keys().cloned().collect();
        for id in ids {
            let _ = self.write(&id, r#"{"type":"cancel"}"#);
        }
    }

    pub fn has_child(&self, child_id: &str) -> bool {
        self.inner.lock().unwrap().children.contains_key(child_id)
    }

    fn write(&self, child_id: &str, line: &str) -> Result<(), CapError> {
        let mut inner = self.inner.lock().unwrap();
        let stdin = inner
            .children
            .get_mut(child_id)
            .ok_or_else(|| CapError::Invalid(format!("child {child_id}: not running")))?;
        stdin.write_all(line.as_bytes())?;
        stdin.write_all(b"\n")?;
        stdin.flush()?;
        Ok(())
    }

    fn learn(&self, descendant: &str, child_id: &str) {
        self.inner.lock().unwrap().below.insert(descendant.to_string(), child_id.to_string());
    }

    fn remove(&self, child_id: &str) {
        let mut inner = self.inner.lock().unwrap();
        inner.children.remove(child_id);
        inner.below.retain(|_, via| via != child_id);
    }
}

/// A child that has ended: its outcome and what its whole subtree spent.
#[derive(Debug, Clone, PartialEq)]
pub struct Settled {
    pub outcome: Outcome,
    /// Token usage of the child's own requests.
    pub usage: Usage,
    /// Model calls and tokens of the child and every episode below it, and
    /// the child's wall-clock seconds. This is what `budget/release` records.
    pub spent: BudgetAmount,
}

/// A running child. Cloneable so that the loop and a tool can both wait.
#[derive(Clone)]
pub struct ChildRun {
    rx: watch::Receiver<Option<Settled>>,
}

impl ChildRun {
    /// A handle whose settlement its holder publishes, for a layer that
    /// owes work after the child process ends and before its caller may
    /// observe the child as settled.
    pub(crate) fn pending() -> (watch::Sender<Option<Settled>>, Self) {
        let (tx, rx) = watch::channel(None);
        (tx, Self { rx })
    }

    pub async fn wait(self) -> (Outcome, Usage) {
        let settled = self.settle().await;
        (settled.outcome, settled.usage)
    }

    pub async fn settle(mut self) -> Settled {
        loop {
            if let Some(settled) = self.rx.borrow_and_update().clone() {
                return settled;
            }
            if self.rx.changed().await.is_err() {
                let error = "the reader of the child's output ended before episode/end".to_string();
                let (usage, spent) = (Usage::default(), BudgetAmount::default());
                return Settled { outcome: Outcome::Failed { error }, usage, spent };
            }
        }
    }
}

pub struct ProcessSpawner {
    episode_id: String,
    log_dir: PathBuf,
    config: Config,
    /// The child's argument vector prefix; the running `foe` binary.
    launcher: Vec<OsString>,
    uplink: Arc<dyn Uplink>,
    router: Arc<Router>,
    observer: Arc<dyn ChildObserver>,
    next: AtomicU64,
}

impl ProcessSpawner {
    pub fn new(
        episode_id: String,
        log_dir: PathBuf,
        config: Config,
        uplink: Arc<dyn Uplink>,
        router: Arc<Router>,
        observer: Arc<dyn ChildObserver>,
    ) -> Result<Self, CapError> {
        let exe = std::env::current_exe()?;
        let launcher = vec![exe.into_os_string()];
        Ok(ProcessSpawner { episode_id, log_dir, config, launcher, uplink, router, observer, next: AtomicU64::new(0) })
    }

    /// Replaces the binary that runs children. Tests use a script.
    pub fn with_launcher(mut self, argv: Vec<OsString>) -> Self {
        self.launcher = argv;
        self
    }

    /// What to reserve for a request: the amount the caller named, and the
    /// budget the child program declares when the caller named none. A
    /// dimension the program leaves unlimited stays unset, and the pool
    /// grants the parent's whole remainder for it. Reserving the remainder
    /// for every dimension would exhaust the parent while one child runs.
    pub fn reserve_for(&self, req: &SpawnRequest) -> BudgetAmount {
        let all = |b: &Budget| BudgetAmount {
            model_calls: Some(b.model_calls),
            input_tokens: b.input_tokens,
            output_tokens: b.output_tokens,
            seconds: b.seconds,
            episodes: None,
        };
        let program = self.config.programs.get(&req.program);
        let declared = program.filter(|_| req.reserve.model_calls.is_none());
        let mut amount = declared.map_or(req.reserve, |p| all(&p.budget));
        // A child that can start no children of its own holds exactly one
        // episode, whatever allowance its program declares. Asking for the
        // declared allowance would hold the parent's whole remainder
        // against a leaf and starve the leaf's siblings.
        amount.episodes = program.map(|p| match self.spawns_below(p) {
            true => u64::from(p.budget.max_episodes),
            false => 1,
        });
        amount
    }

    /// Whether a child running `program` could start children in turn. A
    /// spawn grant and a workflow model node are the two sources of
    /// descendants, and the model node may sit at any workflow depth.
    fn spawns_below(&self, program: &ChildProgram) -> bool {
        let workflow_spawns = program.workflow.as_ref().is_some_and(|wf| wf.contains_model_node());
        self.config.budget.max_depth > 1
            && program.budget.max_depth > 0
            && (!program.grants.spawn.is_empty() || workflow_spawns)
    }

    /// A fresh child id. A caller that reserves budget under the id before
    /// the child starts passes it to [`ProcessSpawner::spawn_as`].
    pub fn child_id(&self) -> String {
        let n = self.next.fetch_add(1, Ordering::SeqCst);
        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
        let digest = Sha256::digest(format!("{}:{n}:{now}:{}", self.episode_id, std::process::id()));
        format!("ep_{}", hex::encode(&digest[..4]))
    }
}

/// The configuration a child is launched with. Budget dimensions the parent
/// reserved replace the program's own when they are tighter, the episode
/// allowance is the share the parent granted, and the depth below the child
/// is one less than below the parent.
pub fn child_config(parent: &Config, program: &ChildProgram, task: String, reserve: BudgetAmount) -> Config {
    let mut budget = program.budget.clone();
    let tighter = |own: Option<u64>, reserved: Option<u64>| reserved.map_or(own, |n| Some(own.map_or(n, |t| t.min(n))));
    budget.model_calls = tighter(Some(budget.model_calls), reserve.model_calls).unwrap_or(budget.model_calls);
    budget.input_tokens = tighter(budget.input_tokens, reserve.input_tokens);
    budget.output_tokens = tighter(budget.output_tokens, reserve.output_tokens);
    budget.seconds = tighter(budget.seconds, reserve.seconds);
    budget.max_depth = budget.max_depth.min(parent.budget.max_depth.saturating_sub(1));
    if let Some(episodes) = reserve.episodes {
        budget.max_episodes = budget.max_episodes.min(episodes.try_into().unwrap_or(u32::MAX));
    }
    Config {
        version: parent.version,
        name: program.name.clone(),
        instructions: program.instructions.clone(),
        tools: program.tools.clone(),
        tool_defs: program.tool_defs.clone(),
        host_tools: program.host_tools.clone(),
        grants: program.grants.clone(),
        budget,
        done_when: program.done_when.clone(),
        context: program.context.clone(),
        model: parent.model.clone(),
        sandbox: parent.sandbox.clone(),
        programs: program.programs.clone(),
        workflow: program.workflow.clone(),
        task,
    }
}

impl Spawner for ProcessSpawner {
    fn spawn(&self, req: SpawnRequest) -> Result<SpawnHandle, CapError> {
        self.spawn_as(self.child_id(), req)
    }
}

impl ProcessSpawner {
    /// Starts a child under a given id.
    pub fn spawn_as(&self, child_id: String, req: SpawnRequest) -> Result<SpawnHandle, CapError> {
        if !self.config.grants.spawn.contains(&req.program) {
            return Err(CapError::Invalid(format!("grants.spawn does not list program {}", req.program)));
        }
        let program = self
            .config
            .programs
            .get(&req.program)
            .ok_or_else(|| CapError::Invalid(format!("programs has no entry named {}", req.program)))?;
        let dir = self.log_dir.join("children").join(&child_id);
        std::fs::create_dir_all(&dir)?;
        let invalid = |e: serde_json::Error| CapError::Invalid(e.to_string());
        let config = child_config(&self.config, program, req.task, req.reserve);
        let config_path = dir.join("config.json");
        std::fs::write(&config_path, serde_json::to_vec_pretty(&config).map_err(invalid)?)?;
        let lineage = serde_json::json!({
            "episode_id": child_id, "parent_id": self.episode_id, "team_id": self.episode_id,
        });
        std::fs::write(dir.join("lineage.json"), serde_json::to_vec_pretty(&lineage).map_err(invalid)?)?;
        if req.context == SpawnContext::Fork {
            let log_error =
                |e: foe_log::LogError| CapError::Invalid(format!("seed from {}: {e}", self.log_dir.display()));
            let until = foe_log::fold::read_all(&self.log_dir).map_err(log_error)?.len() as u64;
            let lead = Some(self.episode_id.clone());
            let header = SeedHeader { new_id: child_id.clone(), parent_id: lead.clone(), team_id: lead };
            foe_log::seed::seed(&self.log_dir, until, &dir, header).map_err(log_error)?;
        }
        let mut cmd = Command::new(&self.launcher[0]);
        cmd.args(&self.launcher[1..])
            .arg("--config")
            .arg(&config_path)
            .arg("--host")
            .arg("--log-dir")
            .arg(&dir)
            .env_clear()
            .current_dir(&dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = cmd.spawn()?;
        let stdin = child.stdin.take().ok_or_else(|| CapError::Invalid("child has no stdin".into()))?;
        let stdout = child.stdout.take().ok_or_else(|| CapError::Invalid("child has no stdout".into()))?;
        let stderr = child.stderr.take().ok_or_else(|| CapError::Invalid("child has no stderr".into()))?;
        self.router.inner.lock().unwrap().children.insert(child_id.clone(), stdin);
        relay_stderr(child_id.clone(), stderr);
        let (tx, run) = ChildRun::pending();
        let reader = Reader {
            child_id: child_id.clone(),
            uplink: self.uplink.clone(),
            router: self.router.clone(),
            observer: self.observer.clone(),
        };
        std::thread::spawn(move || {
            let settled = reader.run(stdout);
            reader.router.remove(&reader.child_id);
            let _ = child.wait();
            let _ = tx.send(Some(settled));
        });
        Ok(SpawnHandle { child_id, dir, run })
    }
}

/// The child's diagnostics go to the parent's standard error, one line at a
/// time, prefixed with the child's id. Standard error is never parsed.
fn relay_stderr(child_id: String, stderr: impl std::io::Read + Send + 'static) {
    std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            eprintln!("[{child_id}] {line}");
        }
    });
}

/// The call id and the tool name of a `host/tool-call` line, when the line
/// is one. Other event lines pass through unread.
fn host_call_of(value: &serde_json::Map<String, serde_json::Value>) -> Option<(String, String)> {
    let data = value.get("data").filter(|_| value.get("type").is_some_and(|t| t == "host/tool-call"))?;
    let field = |name| data.get(name)?.as_str().map(str::to_string);
    Some((field("call_id")?, field("name")?))
}

/// The result a `host/tool-call` receives when no process above the one
/// reading it can answer. The episode that called learns at once rather
/// than waiting for an answer that cannot come; see docs/protocol.md
/// "Children".
fn no_host(episode_id: &str, name: &str) -> ToolValue {
    ToolValue::error(format!("`{name}`: no host above episode {episode_id} can answer a host tool call"))
}

/// Reads one child's standard output to its end.
struct Reader {
    child_id: String,
    uplink: Arc<dyn Uplink>,
    router: Arc<Router>,
    observer: Arc<dyn ChildObserver>,
}

impl Reader {
    /// Sends the child's own request upward, tagged with the child's id.
    fn forward(&self, mut value: serde_json::Map<String, serde_json::Value>) {
        value.insert("episode_id".into(), self.child_id.clone().into());
        self.uplink.forward(&serde_json::Value::Object(value).to_string());
    }

    /// Answers a host tool call that this process settled: one the observer
    /// handled, or one no host above can answer. `episode_id` names the
    /// descendant that made the call when it was not the direct child.
    fn answer(&self, episode_id: Option<&str>, call_id: &str, result: ToolValue) {
        let mut line = serde_json::json!({
            "type": "tool/result", "call_id": call_id, "value": result.value,
            "rendered": result.rendered, "is_error": result.is_error,
        });
        if let Some(id) = episode_id {
            line["episode_id"] = id.into();
        }
        let _ = self.router.route(episode_id.unwrap_or(&self.child_id), &line.to_string());
    }

    fn run(&self, stdout: impl std::io::Read) -> Settled {
        let start = std::time::Instant::now();
        let mut usage = Usage::default();
        let mut calls = 0u64;
        let mut below = BudgetAmount::default();
        let mut outcome = None;
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let parsed: Result<serde_json::Map<String, serde_json::Value>, _> = serde_json::from_str(&line);
            let Ok(value) = parsed else {
                outcome = Some(Outcome::Failed {
                    error: format!("child {} wrote a line that is not a JSON object", self.child_id),
                });
                break;
            };
            if let Some(id) = value.get("episode_id").and_then(|v| v.as_str()) {
                // Forwarded by the child on behalf of one of its own descendants.
                self.router.learn(id, &self.child_id);
                match host_call_of(&value).filter(|_| !self.uplink.answers()) {
                    Some((call_id, name)) => self.answer(Some(id), &call_id, no_host(id, &name)),
                    None => self.uplink.forward(&line),
                }
                continue;
            }
            let Ok(event) = serde_json::from_value::<Event>(serde_json::Value::Object(value.clone())) else {
                outcome = Some(Outcome::Failed {
                    error: format!("child {} wrote an event that does not parse", self.child_id),
                });
                break;
            };
            match &event.data {
                EventData::HostToolCall { call_id, name, args, .. } => {
                    match self.observer.host_call(&self.child_id, name, args) {
                        Some(result) => self.answer(None, call_id, result),
                        None if self.uplink.answers() => self.forward(value),
                        None => self.answer(None, call_id, no_host(&self.child_id, name)),
                    }
                }
                EventData::ModelRequest(_) => {
                    calls += 1;
                    self.forward(value);
                }
                EventData::AssistantMessage(m) => {
                    usage.input += m.usage.input;
                    usage.output += m.usage.output;
                    usage.cache_read += m.usage.cache_read;
                }
                EventData::BudgetRelease { spent, .. } => {
                    below.model_calls = Some(below.model_calls.unwrap_or(0) + spent.model_calls.unwrap_or(0));
                    below.input_tokens = Some(below.input_tokens.unwrap_or(0) + spent.input_tokens.unwrap_or(0));
                    below.output_tokens = Some(below.output_tokens.unwrap_or(0) + spent.output_tokens.unwrap_or(0));
                    below.episodes = Some(below.episodes.unwrap_or(0) + spent.episodes.unwrap_or(0));
                }
                EventData::EpisodeEnd { outcome: o } => outcome = Some(o.clone()),
                _ => {}
            }
            self.observer.observe(&self.child_id, &event);
            // The log ends here; the caller closes the child's standard
            // input so that a child waiting on its host can exit.
            if matches!(event.data, EventData::EpisodeEnd { .. }) {
                break;
            }
        }
        let outcome = outcome.unwrap_or_else(|| Outcome::Failed {
            error: format!("child {} exited without episode/end", self.child_id),
        });
        self.observer.ended(&self.child_id, &outcome);
        let spent = BudgetAmount {
            model_calls: Some(calls + below.model_calls.unwrap_or(0)),
            input_tokens: Some(usage.input + below.input_tokens.unwrap_or(0)),
            output_tokens: Some(usage.output + below.output_tokens.unwrap_or(0)),
            seconds: Some(start.elapsed().as_secs()),
            // The child itself, plus every episode its own releases account
            // for. A process that started counts even when it wrote no log.
            episodes: Some(1 + below.episodes.unwrap_or(0)),
        };
        Settled { outcome, usage, spent }
    }
}

#[cfg(test)]
#[path = "spawn_test.rs"]
pub(crate) mod tests;
