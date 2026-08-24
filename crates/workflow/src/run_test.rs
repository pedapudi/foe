use super::{run, WorkflowParams};
use foe_config::config::resolve;
use foe_config::{Config, Effect, ToolSpec};
use foe_core::budget::Pool;
use foe_core::loop_::{Log, Params};
use foe_core::registry::{Handles, Registry};
use foe_core::{
    CallCtx, CapError, ChunkSink, ModelRequestBody, SpawnHandle, SpawnRequest, Spawner, Tool, ToolValue, Transport,
};
use foe_log::{
    BlockedCode, Chunk, EpisodeStart, Event, EventData, ModelRoute, Outcome, RuntimeInfo, SandboxInfo, SandboxMode,
    StopReason, Usage,
};
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Every call of one tool: its arguments and when it ran.
type Calls = Arc<Mutex<Vec<(Value, Instant, Instant)>>>;

/// A tool that answers each call with the next scripted result, or with
/// its arguments when the script is spent, and records every call.
struct Scripted {
    spec: ToolSpec,
    results: Mutex<VecDeque<ToolValue>>,
    calls: Calls,
    delay: Duration,
}

#[async_trait::async_trait]
impl Tool for Scripted {
    fn spec(&self) -> &ToolSpec {
        &self.spec
    }

    async fn call(&self, args: Value, _ctx: &CallCtx) -> ToolValue {
        let started = Instant::now();
        tokio::time::sleep(self.delay).await;
        self.calls.lock().unwrap().push((args.clone(), started, Instant::now()));
        let next = self.results.lock().unwrap().pop_front();
        next.unwrap_or_else(|| ToolValue::ok(args.clone(), args.to_string()))
    }
}

/// Records the task of every spawn request and starts no child.
#[derive(Default)]
struct NoSpawner(Mutex<Vec<String>>);

impl Spawner for NoSpawner {
    fn spawn(&self, req: SpawnRequest) -> Result<SpawnHandle, CapError> {
        self.0.lock().unwrap().push(req.task);
        Err(CapError::Invalid("this test spawns nothing".into()))
    }
}

/// Answers each model request with the next scripted response.
struct Responses(Mutex<VecDeque<Vec<Chunk>>>, Mutex<Vec<ModelRequestBody>>);

#[async_trait::async_trait]
impl Transport for Responses {
    fn route(&self) -> ModelRoute {
        ModelRoute { provider: "test".into(), model: "scripted".into() }
    }

    async fn stream(&self, req: ModelRequestBody, sink: &mut (dyn ChunkSink + Send)) {
        self.1.lock().unwrap().push(req);
        let chunks = self
            .0
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or_else(|| vec![Chunk::Error { message: "the script is spent".into(), retryable: false }]);
        for chunk in chunks {
            sink.push(chunk);
        }
    }
}

/// A response that calls `recover` with `args`.
fn recover(args: Value) -> Vec<Chunk> {
    vec![
        Chunk::ToolCallStart { id: "tc_r".into(), name: "recover".into() },
        Chunk::ToolCallDelta { id: "tc_r".into(), delta: args.to_string() },
        Chunk::ToolCallEnd { id: "tc_r".into() },
        Chunk::Done { stop: StopReason::Tool, usage: Usage { input: 10, output: 5, cache_read: 0 } },
    ]
}

struct Fixture {
    dir: std::path::PathBuf,
    config: Value,
    tools: Vec<Box<dyn Tool>>,
    calls: std::collections::BTreeMap<String, Calls>,
    responses: Vec<Vec<Chunk>>,
    spawner: Arc<NoSpawner>,
    /// When set, the stop signal is raised this long after the run starts.
    stop_after: Option<Duration>,
}

impl Fixture {
    fn new(name: &str, tools: &[&str], workflow: Value) -> Self {
        let dir = std::env::temp_dir().join(format!("foe-workflow-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let mut names: Vec<&str> = vec!["block"];
        names.extend(tools);
        let config = json!({
            "version": 2, "name": "wf", "instructions": { "r": "test" }, "tools": names,
            "grants": { "read": [dir] }, "budget": { "model_calls": 10 }, "task": "run the graph",
            "workflow": workflow
        });
        Self {
            dir,
            config,
            tools: Vec::new(),
            calls: Default::default(),
            responses: Vec::new(),
            spawner: Default::default(),
            stop_after: None,
        }
    }

    /// Registers a scripted tool under `name` with the given results.
    fn tool(mut self, name: &str, results: Vec<ToolValue>, delay_ms: u64) -> Self {
        self.add_tool(name, results, delay_ms, Effect::Pure);
        self
    }

    /// Registers a scripted tool whose effect is not concurrent, and grants
    /// the write root such an effect needs.
    fn effectful_tool(mut self, name: &str, delay_ms: u64, effect: Effect) -> Self {
        self.config["grants"]["write"] = json!([self.dir]);
        self.add_tool(name, Vec::new(), delay_ms, effect);
        self
    }

    fn add_tool(&mut self, name: &str, results: Vec<ToolValue>, delay_ms: u64, effect: Effect) {
        let calls = Arc::new(Mutex::new(Vec::new()));
        self.calls.insert(name.to_string(), calls.clone());
        let spec = ToolSpec {
            name: name.into(),
            description: format!("scripted {name}"),
            instruction: None,
            params: json!({ "type": "object" }),
            effect,
        };
        self.tools.push(Box::new(Scripted {
            spec,
            results: Mutex::new(results.into()),
            calls,
            delay: Duration::from_millis(delay_ms),
        }));
    }

    fn respond(mut self, chunks: Vec<Chunk>) -> Self {
        self.responses.push(chunks);
        self
    }

    fn calls(&self, name: &str) -> Vec<(Value, Instant, Instant)> {
        self.calls[name].lock().unwrap().clone()
    }

    async fn run(&mut self) -> (Outcome, Vec<Event>) {
        let config: Config = serde_json::from_value(self.config.clone()).unwrap();
        let program = resolve(&config).unwrap();
        let log_dir = self.dir.join("episode");
        std::fs::create_dir_all(&log_dir).unwrap();
        let log = Arc::new(Log::create_or_open(&log_dir, None).unwrap());
        let registry = Registry::new(&program, vec![], std::mem::take(&mut self.tools)).unwrap();
        let (stop, stop_rx) = tokio::sync::watch::channel(None);
        let stop_after = self.stop_after;
        // The sender lives for the whole run: dropping it would make every
        // wait on the stop signal resolve at once.
        let stopper = tokio::spawn(async move {
            match stop_after {
                Some(after) => {
                    tokio::time::sleep(after).await;
                    let _ = stop.send(Some("cancelled".into()));
                }
                None => std::future::pending().await,
            }
        });
        let responses = Responses(Mutex::new(std::mem::take(&mut self.responses).into()), Mutex::new(Vec::new()));
        let episode = Params {
            log: log.clone(),
            start: EpisodeStart {
                id: "ep_wf".into(),
                parent_id: None,
                fork_origin: None,
                team_id: None,
                program: program.to_value(),
                identity: "sha256:test".into(),
                task: "run the graph".into(),
                runtime: RuntimeInfo { version: "0".into(), build: "unknown".into() },
                sandbox: SandboxInfo { mode: SandboxMode::Off, landlock_abi: 0 },
            },
            pool: Arc::new(Mutex::new(Pool::new(program.budget.clone()))),
            registry: Arc::new(registry),
            handles: Handles::default(),
            transport: Arc::new(responses),
            stop: stop_rx,
            children: None,
            sessions: None,
            program: program.clone(),
            context: None,
        };
        let params = WorkflowParams { episode, workflow: program.workflow.unwrap(), spawner: self.spawner.clone() };
        let outcome = run(params).await.unwrap();
        stopper.abort();
        let events = foe_log::fold::read_all(&log_dir).unwrap();
        foe_log::fold::fold(&events).expect("the log is well-formed");
        (outcome, events)
    }
}

fn starts(events: &[Event]) -> Vec<(String, u32)> {
    events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::WorkflowNodeStart(s) => Some((s.node.clone(), s.fire)),
            _ => None,
        })
        .collect()
}

fn recoveries(events: &[Event]) -> Vec<&foe_log::WorkflowRecovery> {
    events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::WorkflowRecovery(r) => Some(r),
            _ => None,
        })
        .collect()
}

fn count(events: &[Event], kind: &str) -> usize {
    events.iter().filter(|e| e.data.type_name() == kind).count()
}

/// docs/workflow.md "Firing" and "Choice points": a cycle re-fires through
/// the chosen label only, bindings carry values forward, and the terminal
/// node completes the workflow with its value.
#[tokio::test]
async fn a_branching_cycle_fires_listed_successors_and_completes_at_the_terminal() {
    let mut fx = Fixture::new(
        "branch",
        &["list", "grep", "decide", "derive"],
        json!({ "nodes": {
            "manifest": { "tool": "list" },
            "survey": { "tool": "grep", "args": { "pattern": { "$node": "manifest", "pointer": "/top" } },
                        "follows": ["manifest"], "max_fires": 3 },
            "propose": { "tool": "decide", "args": { "hits": { "$node": "survey" } }, "follows": ["manifest", "survey"],
                         "branches": { "accept": ["derive"], "widen": ["survey"] }, "max_fires": 3 },
            "derive": { "tool": "derive", "args": { "experiment": { "$node": "propose" } }, "follows": ["propose"],
                        "terminal": true }
        } }),
    )
    .tool("list", vec![ToolValue::ok(json!({ "top": "parse" }), "parse")], 0)
    .tool("grep", vec![], 0)
    .tool(
        "decide",
        vec![
            ToolValue::ok(json!({ "branch": "widen" }), "widen"),
            ToolValue::ok(json!({ "branch": "accept" }), "accept"),
        ],
        0,
    )
    .tool("derive", vec![], 0);
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!({ "experiment": { "branch": "accept" } }) });
    let fired = starts(&events);
    let names: Vec<String> = fired.iter().map(|(n, f)| format!("{n}#{f}")).collect();
    assert_eq!(names, ["manifest#1", "survey#1", "propose#1", "survey#2", "propose#2", "derive#1"]);
    assert_eq!(fx.calls("grep")[0].0, json!({ "pattern": "parse" }), "the pointer binding resolved");
    let branches: Vec<&str> = events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::WorkflowBranch(b) => Some(b.label.as_str()),
            _ => None,
        })
        .collect();
    assert_eq!(branches, ["widen", "accept"]);
    let EventData::WorkflowNodeStart(derive) =
        &events.iter().find(|e| matches!(&e.data, EventData::WorkflowNodeStart(s) if s.node == "derive")).unwrap().data
    else {
        panic!()
    };
    let EventData::WorkflowNodeEnd(end) = &events[derive.inputs[0] as usize].data else {
        panic!("inputs name node-end events")
    };
    assert_eq!((end.node.as_str(), end.fire), ("propose", 2));
    assert_eq!(count(&events, "model/request"), 0, "no recovery was needed");
    assert_eq!(events.last().unwrap().data.type_name(), "episode/end");
}

/// docs/workflow.md "The graph": a node that follows `task` receives the
/// invocation task as its first section whatever the order of `follows`,
/// a tool node binds it as a string, and a recovery decision for such a
/// node sees the same section.
#[tokio::test]
async fn a_node_that_follows_task_receives_it_first() {
    let manifest = json!({ "tool": "echo", "args": { "text": { "$node": "task" } }, "follows": ["task"] });
    let mut fx = Fixture::new(
        "task",
        &["echo"],
        json!({ "nodes": {
            "manifest": manifest,
            "propose": { "model": { "name": "propose", "instructions": { "r": "p" }, "tools": ["block"],
                                    "grants": { "read": [fx_root("task")] }, "budget": { "model_calls": 1 } },
                         "follows": ["manifest", "task"], "terminal": true }
        } }),
    )
    .tool("echo", vec![], 0);
    let (outcome, events) = fx.run().await;
    assert!(matches!(outcome, Outcome::Failed { .. }), "the spawner starts nothing: {outcome:?}");
    assert_eq!(fx.calls("echo")[0].0, json!({ "text": "run the graph" }), "the binding yields the task text");
    let spawned = fx.spawner.0.lock().unwrap().clone();
    assert!(spawned[0].starts_with("## task\n\nrun the graph\n\n## manifest\n\n"), "{}", spawned[0]);
    let EventData::WorkflowNodeStart(start) = &events
        .iter()
        .find(|e| matches!(&e.data, EventData::WorkflowNodeStart(s) if s.node == "manifest"))
        .unwrap()
        .data
    else {
        panic!()
    };
    assert_eq!(start.inputs, vec![1], "the task item at seq 1 is the input");

    let mut fx = Fixture::new(
        "task-recovery",
        &["echo", "bad"],
        json!({ "nodes": {
            "manifest": manifest,
            "check": { "tool": "bad", "follows": ["manifest", "task"], "terminal": true }
        } }),
    )
    .tool("echo", vec![], 0)
    .tool("bad", vec![ToolValue::error("no")], 0)
    .respond(recover(json!({ "action": "abort", "code": "goal-unreachable", "message": "stop" })));
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Blocked { code: BlockedCode::GoalUnreachable, message: "stop".into() });
    let EventData::InboxItem(item) = &events
        .iter()
        .find(|e| matches!(&e.data, EventData::InboxItem(i) if i.source == foe_log::InboxSource::System))
        .unwrap()
        .data
    else {
        panic!()
    };
    let foe_log::ContentBlock::Text { text } = &item.content[0] else { panic!() };
    assert!(text.starts_with("## task\n\nrun the graph\n\n## manifest"), "{text}");
}

/// docs/workflow.md "Choice points" and "Model nodes": a chosen branch
/// controls whether a model successor fires, while `follows` carries the
/// choosing node's rendered value into the child task.
#[tokio::test]
async fn a_chosen_branch_value_reaches_a_model_successor_that_follows_it() {
    let mut fx = Fixture::new(
        "branch-model-input",
        &["diagnose"],
        json!({ "nodes": {
            "diagnose": { "tool": "diagnose", "branches": { "implement-source": ["implement"] } },
            "implement": { "model": { "name": "implement", "instructions": { "r": "implement" },
                                      "tools": ["block"], "grants": { "read": [fx_root("branch-model-input")] },
                                      "budget": { "model_calls": 1 } },
                           "follows": ["task", "diagnose"], "terminal": true }
        } }),
    )
    .tool("diagnose", vec![ToolValue::ok(json!({ "branch": "implement-source" }), "typed diagnosis")], 0);
    let (outcome, _) = fx.run().await;
    assert!(matches!(outcome, Outcome::Failed { .. }), "the spawner starts nothing: {outcome:?}");
    let spawned = fx.spawner.0.lock().unwrap().clone();
    assert_eq!(spawned.len(), 1);
    assert_eq!(spawned[0], "## task\n\nrun the graph\n\n## diagnose\n\ntyped diagnosis");
}

fn fx_root(name: &str) -> std::path::PathBuf {
    std::env::temp_dir().join(format!("foe-workflow-{}-{name}", std::process::id()))
}

/// docs/workflow.md "Firing": nodes with no pending dependency between
/// them fire concurrently.
#[tokio::test]
async fn independent_nodes_fire_concurrently() {
    let mut fx = Fixture::new(
        "concurrent",
        &["slow", "join"],
        json!({ "nodes": {
            "a": { "tool": "slow" },
            "b": { "tool": "slow" },
            "c": { "tool": "join", "follows": ["a", "b"], "terminal": true }
        } }),
    )
    .tool("slow", vec![], 80)
    .tool("join", vec![], 0);
    let (outcome, events) = fx.run().await;
    assert!(matches!(outcome, Outcome::Completed { .. }));
    let slow = fx.calls("slow");
    assert_eq!(slow.len(), 2);
    assert!(slow[0].1 < slow[1].2 && slow[1].1 < slow[0].2, "a and b overlap");
    assert_eq!(starts(&events).last().unwrap().0, "c");
}

/// docs/workflow.md "Firing": tool nodes whose effect is not concurrent run
/// one at a time, in node-start order, across nested workflows as well.
#[tokio::test]
async fn effectful_tool_nodes_run_one_at_a_time() {
    let mut fx = Fixture::new(
        "effectful",
        &["write", "join"],
        json!({ "nodes": {
            "outer": { "tool": "write", "args": { "node": "outer" } },
            "nested": { "workflow": { "nodes": {
                "inner": { "tool": "write", "args": { "node": "nested/inner" }, "terminal": true }
            } } },
            "done": { "tool": "join", "follows": ["outer", "nested"], "terminal": true }
        } }),
    )
    .effectful_tool("write", 40, Effect::Writes)
    .tool("join", vec![], 0);
    let (outcome, events) = fx.run().await;
    assert!(matches!(outcome, Outcome::Completed { .. }));
    let calls = fx.calls("write");
    assert_eq!(calls.len(), 2);
    assert!(calls[0].2 <= calls[1].1 || calls[1].2 <= calls[0].1, "effectful calls overlapped: {calls:?}");
    let call_order: Vec<&str> = calls.iter().filter_map(|call| call.0["node"].as_str()).collect();
    let started = starts(&events);
    let start_order: Vec<&str> = started
        .iter()
        .filter_map(|(name, _)| matches!(name.as_str(), "outer" | "nested/inner").then_some(name.as_str()))
        .collect();
    assert_eq!(call_order, start_order, "effectful calls follow node-start order");
}

/// docs/workflow.md "Recovery": a tool error goes to one recovery decision;
/// `retry` re-fires the node; the episode completes once it succeeds.
#[tokio::test]
async fn a_tool_error_is_recovered_by_retry() {
    let mut fx = Fixture::new(
        "retry",
        &["flaky", "after"],
        json!({ "nodes": {
            "first": { "tool": "flaky", "max_fires": 2 },
            "second": { "tool": "after", "follows": ["first"], "terminal": true }
        } }),
    )
    .tool("flaky", vec![ToolValue::error("the service timed out"), ToolValue::ok(json!("ok"), "ok")], 0)
    .tool("after", vec![], 0)
    .respond(recover(json!({ "action": "retry", "node": "first" })));
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!({}) });
    let recovery = recoveries(&events);
    assert_eq!(recovery.len(), 1);
    assert_eq!((recovery[0].action.as_str(), recovery[0].target.as_deref()), ("retry", Some("first")));
    assert_eq!((recovery[0].cause.as_str(), recovery[0].intervention), ("tool-error", 1));
    assert_eq!(starts(&events), [("first".into(), 1), ("first".into(), 2), ("second".into(), 1)]);
    let kinds: Vec<String> = events.iter().map(|e| e.data.type_name()).collect();
    assert!(kinds.iter().any(|k| k == "request/header") && kinds.iter().any(|k| k == "model/request"));
    let request = events.iter().find(|e| e.data.type_name() == "model/request").unwrap();
    let EventData::ModelRequest(request) = &request.data else { panic!() };
    assert_eq!(request.messages.len(), 1, "a recovery request carries its context alone");
    let EventData::ToolResult(result) = &events.iter().find(|e| e.data.type_name() == "tool/result").unwrap().data
    else {
        panic!()
    };
    assert_eq!((result.name.as_str(), result.is_error), ("recover", false));
}

/// docs/workflow.md "What it may do": retry and amend reach the failed
/// node and its ancestors only; skip needs `empty`; a decision outside the
/// offer ends the episode as `recovery-failed`.
#[tokio::test]
async fn recovery_reaches_ancestors_only_and_skip_needs_empty() {
    let graph = json!({ "nodes": {
        "a": { "tool": "t", "max_fires": 2 },
        "other": { "tool": "t" },
        "b": { "tool": "bad", "follows": ["a"], "max_fires": 2 },
        "c": { "tool": "t", "follows": ["b", "other"], "terminal": true }
    } });
    let mut fx = Fixture::new("eligibility", &["t", "bad"], graph.clone())
        .tool("t", vec![], 0)
        .tool("bad", vec![ToolValue::error("no")], 0)
        .respond(recover(json!({ "action": "retry", "node": "other" })));
    let (outcome, events) = fx.run().await;
    assert!(
        matches!(&outcome, Outcome::Blocked { code: BlockedCode::RecoveryFailed, message } if message.contains("`other`"))
    );
    assert!(recoveries(&events).is_empty(), "a refused decision is not a recovery");
    let EventData::ToolResult(result) = &events.iter().find(|e| e.data.type_name() == "tool/result").unwrap().data
    else {
        panic!()
    };
    assert!(result.is_error, "the refused call has an error result");
    let EventData::RequestHeader(header) =
        &events.iter().find(|e| e.data.type_name() == "request/header").unwrap().data
    else {
        panic!()
    };
    assert_eq!(
        header.tools[0].parameters["properties"]["node"]["enum"],
        json!(["a", "b"]),
        "the offer lists ancestors"
    );
    assert_eq!(header.tools[0].parameters["properties"]["action"]["enum"], json!(["retry", "amend", "abort"]));

    let mut fx = Fixture::new("skip-refused", &["t", "bad"], graph.clone())
        .tool("t", vec![], 0)
        .tool("bad", vec![ToolValue::error("no")], 0)
        .respond(recover(json!({ "action": "skip" })));
    let (outcome, _) = fx.run().await;
    assert!(matches!(outcome, Outcome::Blocked { code: BlockedCode::RecoveryFailed, .. }));

    let mut with_empty = graph.clone();
    with_empty["nodes"]["b"]["empty"] = json!({ "hits": [] });
    let mut fx = Fixture::new("skip", &["t", "bad"], with_empty)
        .tool("t", vec![], 0)
        .tool("bad", vec![ToolValue::error("no")], 0)
        .respond(recover(json!({ "action": "skip" })));
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!({}) });
    assert_eq!(recoveries(&events)[0].action, "skip");
    let EventData::WorkflowNodeStart(c) =
        &events.iter().find(|e| matches!(&e.data, EventData::WorkflowNodeStart(s) if s.node == "c")).unwrap().data
    else {
        panic!()
    };
    assert!(
        c.inputs.iter().any(|seq| events[*seq as usize].data.type_name() == "workflow/recovery"),
        "a skipped input points at the recovery"
    );

    let mut fx = Fixture::new("amend", &["t", "bad"], graph)
        .tool("t", vec![], 0)
        .tool("bad", vec![ToolValue::error("no")], 0)
        .respond(recover(json!({ "action": "amend", "node": "a", "note": "try harder" })));
    let (outcome, events) = fx.run().await;
    assert_eq!(recoveries(&events)[0].note.as_deref(), Some("try harder"));
    assert_eq!(starts(&events).iter().filter(|(n, _)| n == "a").count(), 2, "the amended ancestor re-fired");
    assert!(matches!(outcome, Outcome::Completed { .. }), "the second firing of `bad` echoes its arguments");
}

/// docs/workflow.md "When it fires": a denied path and a spent budget are
/// settled; the episode ends with the matching outcome and no model call.
#[tokio::test]
async fn settled_failures_end_the_episode_without_a_decision() {
    let mut fx =
        Fixture::new("settled", &["denied"], json!({ "nodes": { "only": { "tool": "denied", "terminal": true } } }))
            .tool("denied", vec![ToolValue::error("/etc/shadow: outside every granted root")], 0);
    let (outcome, events) = fx.run().await;
    assert!(matches!(outcome, Outcome::Failed { error } if error.contains("outside every granted root")));
    assert_eq!(count(&events, "model/request"), 0);
    let mut fx = Fixture::new(
        "spent",
        &["spent"],
        json!({ "nodes": { "only": { "tool": "spent", "terminal": true } } }),
    )
    .tool("spent", vec![ToolValue::error("budget: the episodes limit leaves no room for a child")], 0);
    let (outcome, _) = fx.run().await;
    assert_eq!(outcome, Outcome::Exhausted { limit: foe_log::ExhaustedLimit::Episodes });
}

/// docs/workflow.md "What bounds it": the intervention cap and `max_fires`
/// end the episode as `recovery-exhausted`; disabled recovery ends it with
/// the node's outcome.
#[tokio::test]
async fn bounds_end_the_episode_as_recovery_exhausted() {
    let mut fx = Fixture::new(
        "interventions",
        &["bad"],
        json!({
            "nodes": { "only": { "tool": "bad", "max_fires": 5, "terminal": true } },
            "recovery": { "max_interventions": 1 }
        }),
    )
    .tool("bad", vec![ToolValue::error("one"), ToolValue::error("two")], 0)
    .respond(recover(json!({ "action": "retry", "node": "only" })));
    let (outcome, events) = fx.run().await;
    assert!(
        matches!(&outcome, Outcome::Blocked { code: BlockedCode::RecoveryExhausted, message } if message.contains("max_interventions"))
    );
    assert_eq!(recoveries(&events).len(), 1);
    assert_eq!(count(&events, "model/request"), 1, "the cap is checked before a second decision");

    let mut fx = Fixture::new(
        "max-fires",
        &["again"],
        json!({ "nodes": {
            "start": { "tool": "again" },
            "a": { "tool": "again", "follows": ["start"], "max_fires": 2 },
            "b": { "tool": "again", "args": { "branch": "loop" }, "follows": ["a"],
                   "branches": { "loop": ["a"], "stop": [] }, "max_fires": 2 }
        } }),
    )
    .tool("again", vec![], 0);
    let (outcome, events) = fx.run().await;
    assert!(
        matches!(&outcome, Outcome::Blocked { code: BlockedCode::RecoveryExhausted, message } if message.contains("max_fires")),
        "{outcome:?}"
    );
    assert_eq!(starts(&events).len(), 5, "a and b fired twice each after start, then the bound held");

    let mut fx = Fixture::new(
        "disabled",
        &["bad"],
        json!({
            "nodes": { "only": { "tool": "bad", "terminal": true } },
            "recovery": { "enabled": false }
        }),
    )
    .tool("bad", vec![ToolValue::error("one")], 0);
    let (outcome, events) = fx.run().await;
    assert!(matches!(outcome, Outcome::Failed { error } if error == "one"));
    assert_eq!(count(&events, "model/request"), 0);
}

/// docs/workflow.md "Nodes": `verify` findings re-fire the node up to
/// `retries` times, then go to recovery; a nested workflow's terminal
/// value is its node's value; an aborted recovery carries the code.
#[tokio::test]
async fn verify_retries_then_recovers_and_nested_workflows_produce_their_terminal_value() {
    let mut fx = Fixture::new(
        "verify",
        &["t", "check"],
        json!({ "nodes": {
            "inner": { "workflow": { "nodes": {
                "x": { "tool": "t" },
                "y": { "tool": "t", "args": { "from": { "$node": "x" } }, "follows": ["x"], "terminal": true }
            } }, "verify": "check", "retries": 1, "max_fires": 2 },
            "out": { "tool": "t", "args": { "got": { "$node": "inner" } }, "follows": ["inner"], "terminal": true }
        } }),
    )
    .tool("t", vec![], 0)
    .tool("check", vec![ToolValue::ok(json!(["too short"]), "1"), ToolValue::ok(json!(["still short"]), "1")], 0)
    .respond(recover(json!({ "action": "abort", "code": "goal-unreachable", "message": "cannot satisfy check" })));
    let (outcome, events) = fx.run().await;
    assert_eq!(
        outcome,
        Outcome::Blocked { code: BlockedCode::GoalUnreachable, message: "cannot satisfy check".into() }
    );
    let fired: Vec<String> = starts(&events).iter().map(|(n, f)| format!("{n}#{f}")).collect();
    assert_eq!(fired, ["inner#1", "inner/x#1", "inner/y#1", "inner#2", "inner/x#1", "inner/y#1"]);
    let recorded: Vec<_> = events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::VerificationResult(v) => Some(v),
            _ => None,
        })
        .collect();
    assert_eq!(recorded.len(), 2, "one event per node-level invocation");
    assert!(recorded.iter().all(|v| v.tool == "check" && v.status == foe_log::VerificationStatus::Findings));
    assert_eq!(recorded[1].findings, vec!["still short".to_string()]);
    assert_eq!(recoveries(&events)[0].cause, "verify-findings");
    assert_eq!(recoveries(&events)[0].action, "abort");
    let EventData::InboxItem(item) = &events
        .iter()
        .find(|e| matches!(&e.data, EventData::InboxItem(i) if i.source == foe_log::InboxSource::System))
        .unwrap()
        .data
    else {
        panic!()
    };
    let foe_log::ContentBlock::Text { text } = &item.content[0] else { panic!() };
    assert!(text.contains("## findings") && text.contains("still short"), "{text}");
    assert!(text.contains("retry and amend may name: (no node)."), "a node at its bound is not offered: {text}");

    let mut fx = Fixture::new(
        "nested",
        &["t"],
        json!({ "nodes": {
            "inner": { "workflow": { "nodes": {
                "x": { "tool": "t", "args": { "v": 1 } },
                "y": { "tool": "t", "args": { "from": { "$node": "x" } }, "follows": ["x"], "terminal": true }
            } } },
            "out": { "tool": "t", "args": { "got": { "$node": "inner" } }, "follows": ["inner"], "terminal": true }
        } }),
    )
    .tool("t", vec![], 0);
    let (outcome, _) = fx.run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!({ "got": { "from": { "v": 1 } } }) });
}

fn skips(events: &[Event]) -> Vec<&foe_log::WorkflowNodeSkipped> {
    events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::WorkflowNodeSkipped(s) => Some(s),
            _ => None,
        })
        .collect()
}

/// docs/workflow.md "The conditional audit guard": a node whose guard
/// names a verifier-accepted node does not fire; it contributes the named
/// node's value to its successors, which name the skip event among their
/// inputs.
#[tokio::test]
async fn a_verified_result_skips_the_guarded_node() {
    let mut fx = Fixture::new(
        "guard-skip",
        &["t", "check", "audit", "report"],
        json!({ "nodes": {
            "work": { "tool": "t", "verify": "check" },
            "audit": { "tool": "audit", "follows": ["work"], "skip_when_verified": "work" },
            "report": { "tool": "report", "args": { "got": { "$node": "audit" } }, "follows": ["audit"],
                        "terminal": true }
        } }),
    )
    .tool("t", vec![ToolValue::ok(json!({ "made": 1 }), "made")], 0)
    .tool("check", vec![ToolValue::ok(json!([]), "clean")], 0)
    .tool("audit", vec![], 0)
    .tool("report", vec![], 0);
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!({ "got": { "made": 1 } }) });
    assert!(fx.calls("audit").is_empty(), "the guarded node did not fire");
    assert_eq!(starts(&events).iter().map(|(n, _)| n.as_str()).collect::<Vec<_>>(), ["work", "report"]);
    let skipped = skips(&events);
    assert_eq!(skipped.len(), 1);
    assert_eq!((skipped[0].node.as_str(), skipped[0].verified_by.as_str()), ("audit", "work"));
    let EventData::VerificationResult(evidence) = &events[skipped[0].verification_seq as usize].data else {
        panic!("verification_seq names the accepted verification")
    };
    assert_eq!(evidence.status, foe_log::VerificationStatus::Accepted);
    let EventData::WorkflowNodeStart(report) =
        &events.iter().find(|e| matches!(&e.data, EventData::WorkflowNodeStart(s) if s.node == "report")).unwrap().data
    else {
        panic!()
    };
    let skip_seq = events.iter().find(|e| matches!(e.data, EventData::WorkflowNodeSkipped(_))).unwrap().seq;
    assert!(report.inputs.contains(&skip_seq), "the successor names the skip event as its input");
}

/// docs/workflow.md "The conditional audit guard": a skipped terminal node
/// completes the workflow with the named node's value.
#[tokio::test]
async fn a_skipped_terminal_node_completes_with_the_named_nodes_value() {
    let mut fx = Fixture::new(
        "guard-terminal",
        &["t", "check", "audit"],
        json!({ "nodes": {
            "work": { "tool": "t", "verify": "check" },
            "audit": { "tool": "audit", "follows": ["work"], "skip_when_verified": "work", "terminal": true }
        } }),
    )
    .tool("t", vec![ToolValue::ok(json!({ "made": 1 }), "made")], 0)
    .tool("check", vec![ToolValue::ok(json!([]), "clean")], 0)
    .tool("audit", vec![], 0);
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!({ "made": 1 }) });
    assert!(fx.calls("audit").is_empty());
    assert_eq!(skips(&events).len(), 1);
}

/// docs/workflow.md "The conditional audit guard": a run the verifier
/// never accepted fires the guarded node exactly as it would without the
/// guard. Here the named node fails and recovery skips it, so its empty
/// value was never verified.
#[tokio::test]
async fn an_unverified_run_fires_the_guarded_node_unchanged() {
    let mut fx = Fixture::new(
        "guard-unverified",
        &["t", "check", "audit"],
        json!({ "nodes": {
            "work": { "tool": "t", "verify": "check", "empty": { "made": 0 } },
            "audit": { "tool": "audit", "follows": ["work"], "skip_when_verified": "work", "terminal": true }
        } }),
    )
    .tool("t", vec![ToolValue::error("the service timed out")], 0)
    .tool("check", vec![], 0)
    .tool("audit", vec![ToolValue::ok(json!({ "audited": true }), "audited")], 0)
    .respond(recover(json!({ "action": "skip" })));
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!({ "audited": true }) });
    assert_eq!(fx.calls("audit").len(), 1, "the guarded node fired");
    assert!(skips(&events).is_empty(), "nothing was skipped by the guard");
    assert_eq!(count(&events, "verification/result"), 0, "the named node's verifier never ran");
}

/// The two nodes of a drain test: one that answers at once and completes
/// the workflow, and one that never answers.
fn draining(name: &str) -> Fixture {
    Fixture::new(
        name,
        &["finish", "linger"],
        json!({ "nodes": {
            "finish": { "tool": "finish", "terminal": true },
            "linger": { "tool": "linger" }
        } }),
    )
    .tool("finish", vec![ToolValue::ok(json!({ "done": true }), "done")], 0)
    .tool("linger", vec![], 60_000)
}

/// The `workflow/node-end` of the node named, with its error.
fn node_error(events: &[Event], node: &str) -> Option<String> {
    events.iter().find_map(|e| match &e.data {
        EventData::WorkflowNodeEnd(end) if end.node == node => Some(end.error.clone().unwrap_or_default()),
        _ => None,
    })
}

/// docs/workflow.md "Completion": a firing still running when the workflow
/// completes is awaited only as far as the episode's `seconds` budget. The
/// budget elapsing abandons the firing, records its end with the bound that
/// abandoned it, and leaves the outcome the graph reached standing.
/// The clock is virtual: the lingering tool sleeps for a minute and nothing
/// in the fixture touches the world, so tokio advances to the budget's
/// deadline as soon as the episode is idle and the wait is measured rather
/// than served.
#[tokio::test(start_paused = true)]
async fn a_firing_still_running_when_the_seconds_budget_elapses_is_abandoned() {
    let mut fx = draining("drain-seconds");
    fx.config["budget"]["seconds"] = json!(1);
    let started = tokio::time::Instant::now();
    let (outcome, events) = fx.run().await;
    let elapsed = started.elapsed();
    assert!(
        (Duration::from_secs(1)..Duration::from_secs(2)).contains(&elapsed),
        "the drain ended at the budget rather than with the firing, which sleeps for a minute: {elapsed:?}"
    );
    assert_eq!(outcome, Outcome::Completed { value: json!({ "done": true }) });
    assert_eq!(node_error(&events, "finish"), Some(String::new()), "the terminal node ended with no error");
    let error = node_error(&events, "linger").expect("the abandoned firing was given an end");
    assert!(error.contains("seconds"), "{error}");
}

/// docs/workflow.md "Completion": the stop signal ends the wait for the
/// firings still running, whether or not the program declares `seconds`.
/// The clock is virtual; see
/// `a_firing_still_running_when_the_seconds_budget_elapses_is_abandoned`.
#[tokio::test(start_paused = true)]
async fn a_firing_still_running_when_the_episode_stops_is_abandoned() {
    let mut fx = draining("drain-stop");
    fx.stop_after = Some(Duration::from_millis(200));
    let started = tokio::time::Instant::now();
    let (outcome, events) = fx.run().await;
    assert_eq!(started.elapsed(), Duration::from_millis(200), "the drain ended at the stop signal");
    assert_eq!(outcome, Outcome::Completed { value: json!({ "done": true }) });
    let error = node_error(&events, "linger").expect("the abandoned firing was given an end");
    assert!(error.contains("stopped") && error.contains("cancelled"), "{error}");
}
