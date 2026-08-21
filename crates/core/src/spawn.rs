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
//! `sandbox` inherited and with `notify`, `send`, and `team` declared under
//! `host_tools`, because the parent is the host that answers them.
//! `lineage.json` names the child's own id, its parent, and its team lead,
//! for the child's `episode/start`.
//!
//! The parent never writes the child's log, and this module never writes
//! the parent's. `budget/reserve`, `spawn/start`, `spawn/end`, and
//! `budget/release` are the loop's to write around a call to
//! [`Spawner::spawn`] and a wait on [`ChildRun`].

use crate::{CapError, ChildProgram, Config, SpawnHandle, SpawnRequest, Spawner, ToolValue};
use foe_log::seed::SeedHeader;
use foe_log::{BudgetAmount, Event, EventData, InboxItem, Outcome, SpawnContext, Usage};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::ffi::OsString;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use tokio::sync::watch;

/// The link from this episode to the process hosting it.
pub trait Uplink: Send + Sync {
    /// Writes one line to the host. The line is a descendant's event that
    /// needs a host answer; it carries `episode_id` and has no line feed.
    fn forward(&self, line: &str);
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
                return Settled {
                    outcome: Outcome::Failed { error },
                    usage: Usage::default(),
                    spent: BudgetAmount::default(),
                };
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

    fn child_id(&self) -> String {
        let n = self.next.fetch_add(1, Ordering::SeqCst);
        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
        let digest = Sha256::digest(format!("{}:{n}:{now}:{}", self.episode_id, std::process::id()));
        format!("ep_{}", hex::encode(&digest[..4]))
    }
}

/// The configuration a child is launched with. Budget dimensions the parent
/// reserved replace the program's own when they are tighter, the depth
/// below the child is one less than below the parent, and the team tools the
/// parent answers are declared as host tools.
pub fn child_config(parent: &Config, program: &ChildProgram, task: String, reserve: BudgetAmount) -> Config {
    let mut budget = program.budget.clone();
    if let Some(n) = reserve.model_calls {
        budget.model_calls = budget.model_calls.min(n);
    }
    if let Some(n) = reserve.tokens {
        budget.tokens = Some(budget.tokens.map_or(n, |t| t.min(n)));
    }
    if let Some(n) = reserve.seconds {
        budget.seconds = Some(budget.seconds.map_or(n, |t| t.min(n)));
    }
    budget.max_depth = budget.max_depth.min(parent.budget.max_depth.saturating_sub(1));
    let mut host_tools = program.host_tools.clone();
    host_tools.extend(crate::team::host_tool_defs());
    Config {
        version: parent.version,
        name: program.name.clone(),
        instructions: program.instructions.clone(),
        tools: program.tools.clone(),
        tool_defs: program.tool_defs.clone(),
        host_tools,
        grants: program.grants.clone(),
        budget,
        done_when: program.done_when.clone(),
        model: parent.model.clone(),
        sandbox: parent.sandbox.clone(),
        programs: program.programs.clone(),
        task,
    }
}

impl Spawner for ProcessSpawner {
    fn spawn(&self, req: SpawnRequest) -> Result<SpawnHandle, CapError> {
        if !self.config.grants.spawn.contains(&req.program) {
            return Err(CapError::Invalid(format!("grants.spawn does not list program {}", req.program)));
        }
        let program = self
            .config
            .programs
            .get(&req.program)
            .ok_or_else(|| CapError::Invalid(format!("programs has no entry named {}", req.program)))?;
        let child_id = self.child_id();
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
            let header = SeedHeader {
                new_id: child_id.clone(),
                parent_id: Some(self.episode_id.clone()),
                team_id: Some(self.episode_id.clone()),
            };
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
        let (tx, rx) = watch::channel(None);
        let reader = Reader {
            child_id: child_id.clone(),
            uplink: self.uplink.clone(),
            router: self.router.clone(),
            observer: self.observer.clone(),
        };
        std::thread::spawn(move || {
            let settled = reader.run(stdout);
            let _ = child.wait();
            reader.router.remove(&reader.child_id);
            let _ = tx.send(Some(settled));
        });
        Ok(SpawnHandle { child_id, dir, run: ChildRun { rx } })
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

    /// Answers a host tool call the parent handled itself.
    fn answer(&self, call_id: &str, result: ToolValue) {
        let line = serde_json::json!({
            "type": "tool/result", "call_id": call_id, "value": result.value,
            "rendered": result.rendered, "is_error": result.is_error,
        });
        let _ = self.router.write(&self.child_id, &line.to_string());
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
                self.uplink.forward(&line);
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
                        Some(result) => self.answer(call_id, result),
                        None => self.forward(value),
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
                    below.tokens = Some(below.tokens.unwrap_or(0) + spent.tokens.unwrap_or(0));
                }
                EventData::EpisodeEnd { outcome: o } => outcome = Some(o.clone()),
                _ => {}
            }
            self.observer.observe(&self.child_id, &event);
        }
        let outcome = outcome.unwrap_or_else(|| Outcome::Failed {
            error: format!("child {} exited without episode/end", self.child_id),
        });
        let spent = BudgetAmount {
            model_calls: Some(calls + below.model_calls.unwrap_or(0)),
            tokens: Some(usage.input + usage.output + below.tokens.unwrap_or(0)),
            seconds: Some(start.elapsed().as_secs()),
        };
        Settled { outcome, usage, spent }
    }
}

/// The directory of a child's log, for callers that know only the id.
pub fn child_dir(parent_log_dir: &Path, child_id: &str) -> PathBuf {
    parent_log_dir.join("children").join(child_id)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::exec::tests::scratch;
    use std::os::unix::fs::PermissionsExt;
    use std::time::Duration;

    #[derive(Default)]
    pub(crate) struct Lines(pub Mutex<Vec<String>>);

    impl Uplink for Lines {
        fn forward(&self, line: &str) {
            self.0.lock().unwrap().push(line.to_string());
        }
    }

    #[derive(Default)]
    pub(crate) struct Seen(pub Mutex<Vec<(String, Event)>>);

    impl ChildObserver for Seen {
        fn observe(&self, child_id: &str, event: &Event) {
            self.0.lock().unwrap().push((child_id.to_string(), event.clone()));
        }
    }

    pub(crate) fn parent_config() -> Config {
        serde_json::from_value(serde_json::json!({
            "version": 1, "name": "lead", "instructions": {"r": "lead"}, "tools": ["spawn"],
            "grants": {"read": ["/src"], "spawn": ["worker"]},
            "budget": {"model_calls": 20, "max_depth": 2},
            "sandbox": {"mode": "off"},
            "programs": {"worker": {
                "name": "worker", "instructions": {"r": "work"}, "tools": ["notify"],
                "grants": {"read": ["/src"]}, "budget": {"model_calls": 50, "max_depth": 3}
            }},
            "task": "lead task"
        }))
        .unwrap()
    }

    /// A stand-in child: writes a start event, a request, waits for one
    /// routed answer, calls the host tool `notify`, waits for its result,
    /// then ends with both answers as its value. A first pre-tagged request
    /// stands for one forwarded from a grandchild.
    pub(crate) const FAKE_CHILD: &str = r#"#!/bin/sh
echo '{"seq":0,"time":1,"type":"episode/start","data":{"id":"ep_child","parent_id":"ep_root","fork_origin":null,"team_id":"ep_root","program":{},"identity":"sha256:0","task":"t","runtime":{"version":"0","build":"unknown"},"sandbox":{"mode":"off","landlock_abi":0}}}'
echo '{"seq":9,"time":1,"type":"model/request","episode_id":"ep_grand","data":{"step":1,"attempt":1,"request_id":"rq_g","header_seq":0,"consumed":[],"messages":[]}}'
echo '{"seq":1,"time":1,"type":"model/request","data":{"step":1,"attempt":1,"request_id":"rq_1","header_seq":0,"consumed":[1],"messages":[]}}'
read -r answer
echo '{"seq":2,"time":1,"type":"assistant/message","data":{"step":1,"request_id":"rq_1","text":"","tool_calls":[{"id":"tc_1","name":"notify","args":{"content":"progress"}}],"stop":"tool","usage":{"input":10,"output":5,"cache_read":0},"interrupted":false}}'
echo '{"seq":3,"time":1,"type":"host/tool-call","data":{"step":1,"call_id":"tc_1","name":"notify","args":{"content":"progress"}}}'
read -r result
echo "{\"seq\":4,\"time\":1,\"type\":\"episode/end\",\"data\":{\"outcome\":{\"kind\":\"completed\",\"value\":[$answer,$result]}}}"
"#;

    pub(crate) fn fake_child(dir: &Path) -> Vec<OsString> {
        let script = dir.join("fake-foe.sh");
        std::fs::write(&script, FAKE_CHILD).unwrap();
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
        vec!["/bin/sh".into(), script.into_os_string()]
    }

    pub(crate) fn wait_for<T>(mut probe: impl FnMut() -> Option<T>) -> T {
        for _ in 0..500 {
            if let Some(v) = probe() {
                return v;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        panic!("condition not met within 5 seconds");
    }

    #[tokio::test]
    async fn child_requests_are_forwarded_and_answers_routed() {
        let dir = scratch("spawn", "roundtrip");
        let uplink = Arc::new(Lines::default());
        let router = Arc::new(Router::new());
        let seen = Arc::new(Seen::default());
        let spawner = ProcessSpawner::new(
            "ep_root".into(),
            dir.clone(),
            parent_config(),
            uplink.clone(),
            router.clone(),
            seen.clone(),
        )
        .unwrap()
        .with_launcher(fake_child(&dir));
        let req = SpawnRequest {
            program: "worker".into(),
            task: "do it".into(),
            context: SpawnContext::Fresh,
            reserve: BudgetAmount { model_calls: Some(5), tokens: None, seconds: None },
        };
        let handle = spawner.spawn(req).unwrap();
        let child_dir = dir.join("children").join(&handle.child_id);
        let written: Config = serde_json::from_slice(&std::fs::read(child_dir.join("config.json")).unwrap()).unwrap();
        assert_eq!(written.name, "worker");
        assert_eq!(written.task, "do it");
        assert_eq!(written.budget.model_calls, 5, "the reservation caps the program's budget");
        assert_eq!(written.budget.max_depth, 1, "depth below the child is one less than below the parent");
        assert_eq!(written.sandbox.mode, foe_log::SandboxMode::Off, "sandbox is inherited");
        let lineage: serde_json::Value =
            serde_json::from_slice(&std::fs::read(child_dir.join("lineage.json")).unwrap()).unwrap();
        assert_eq!(lineage["parent_id"], "ep_root");
        assert_eq!(lineage["episode_id"], handle.child_id.as_str());

        let forwarded = wait_for(|| {
            let lines = uplink.0.lock().unwrap();
            (lines.len() == 2).then(|| lines.clone())
        });
        let grand: serde_json::Value = serde_json::from_str(&forwarded[0]).unwrap();
        assert_eq!(grand["episode_id"], "ep_grand", "a pre-tagged line is forwarded unchanged");
        let own: serde_json::Value = serde_json::from_str(&forwarded[1]).unwrap();
        assert_eq!(own["episode_id"], handle.child_id.as_str(), "the child's own request is tagged with its id");
        assert_eq!(own["type"], "model/request");

        let answer = r#"{"type":"model/chunk","request_id":"rq_1","episode_id":"ep_grand","chunk":{"kind":"done"}}"#;
        router.route("ep_grand", answer).unwrap();
        let forwarded = wait_for(|| {
            let lines = uplink.0.lock().unwrap();
            (lines.len() == 3).then(|| lines.clone())
        });
        let call: serde_json::Value = serde_json::from_str(&forwarded[2]).unwrap();
        assert_eq!(call["type"], "host/tool-call", "a host call the observer does not answer is forwarded");
        assert_eq!(call["episode_id"], handle.child_id.as_str());
        let result =
            format!(r#"{{"type":"tool/result","call_id":"tc_1","episode_id":"{}","value":1}}"#, handle.child_id);
        router.route(&handle.child_id, &result).unwrap();
        let settled = handle.run.clone().settle().await;
        let Outcome::Completed { value } = &settled.outcome else { panic!("{:?}", settled.outcome) };
        assert_eq!(value[0], serde_json::from_str::<serde_json::Value>(answer).unwrap(), "routed by descendant id");
        assert_eq!(value[1], serde_json::from_str::<serde_json::Value>(&result).unwrap(), "routed by child id");
        assert_eq!(settled.usage, Usage { input: 10, output: 5, cache_read: 0 });
        assert_eq!(settled.spent.model_calls, Some(1));
        assert_eq!(settled.spent.tokens, Some(15));
        let (outcome, _) = handle.run.wait().await;
        assert!(matches!(outcome, Outcome::Completed { .. }));
        assert!(!router.has_child(&handle.child_id));
        let kinds: Vec<&str> = seen.0.lock().unwrap().iter().map(|(_, e)| e.data.type_name()).collect::<Vec<_>>();
        assert_eq!(kinds, ["episode/start", "model/request", "assistant/message", "host/tool-call", "episode/end"]);
    }

    #[test]
    fn spawn_refuses_programs_outside_the_grant() {
        let dir = scratch("spawn", "refuse");
        let spawner = ProcessSpawner::new(
            "ep_root".into(),
            dir.clone(),
            parent_config(),
            Arc::new(Lines::default()),
            Arc::new(Router::new()),
            Arc::new(Seen::default()),
        )
        .unwrap()
        .with_launcher(fake_child(&dir));
        let req = SpawnRequest {
            program: "other".into(),
            task: "x".into(),
            context: SpawnContext::Fresh,
            reserve: BudgetAmount::default(),
        };
        let err = spawner.spawn(req).err().unwrap().to_string();
        assert!(err.contains("grants.spawn"), "{err}");
    }

    #[test]
    fn routing_to_an_unknown_episode_is_an_error() {
        let router = Router::new();
        assert!(router.route("ep_none", "{}").is_err());
    }
}
