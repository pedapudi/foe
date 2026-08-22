use super::*;
use crate::exec::tests::scratch;
use foe_log::ExhaustedLimit;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
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

/// A stand-in child that runs until the parent writes it a line. The
/// parent's teardown writes `cancel`, which is what ends it.
pub(crate) const WAITING_CHILD: &str = r#"#!/bin/sh
echo '{"seq":0,"time":1,"type":"episode/start","data":{"id":"ep_child","parent_id":"ep_root","fork_origin":null,"team_id":"ep_root","program":{},"identity":"sha256:0","task":"t","runtime":{"version":"0","build":"unknown"},"sandbox":{"mode":"off","landlock_abi":0}}}'
read -r line
echo '{"seq":1,"time":1,"type":"episode/end","data":{"outcome":{"kind":"failed","error":"cancelled"}}}'
"#;

pub(crate) fn waiting_child(dir: &Path) -> Vec<OsString> {
    script(dir, "waiting-foe.sh", WAITING_CHILD)
}

pub(crate) fn fake_child(dir: &Path) -> Vec<OsString> {
    script(dir, "fake-foe.sh", FAKE_CHILD)
}

/// A stand-in child that settles one child of its own and then ends, so
/// that what it reports covers a subtree rather than itself alone.
pub(crate) const NESTING_CHILD: &str = r#"#!/bin/sh
echo '{"seq":0,"time":1,"type":"episode/start","data":{"id":"ep_child","parent_id":"ep_root","fork_origin":null,"team_id":"ep_root","program":{},"identity":"sha256:0","task":"t","runtime":{"version":"0","build":"unknown"},"sandbox":{"mode":"off","landlock_abi":0}}}'
echo '{"seq":1,"time":1,"type":"budget/release","data":{"child_id":"ep_grand","spent":{"model_calls":3,"tokens":50,"episodes":2}}}'
echo '{"seq":2,"time":1,"type":"episode/end","data":{"outcome":{"kind":"completed","value":"done"}}}'
"#;

pub(crate) fn nesting_child(dir: &Path) -> Vec<OsString> {
    script(dir, "nesting-foe.sh", NESTING_CHILD)
}

fn script(dir: &Path, name: &str, body: &str) -> Vec<OsString> {
    let script = dir.join(name);
    std::fs::write(&script, body).unwrap();
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

/// docs/config.md `budget`: `max_episodes` bounds the whole tree. A child
/// that can start no children of its own asks for one episode, so a leaf
/// does not hold its parent's whole allowance against its siblings, and
/// the share the parent grants caps what the child's own document declares.
#[test]
fn a_child_asks_for_the_episodes_its_subtree_can_hold() {
    let dir = scratch("spawn", "episode-share");
    let mut config = parent_config();
    let worker = config.programs["worker"].clone();
    let mut nested = worker.clone();
    nested.name = "nested".into();
    nested.grants.spawn = vec!["worker".into()];
    nested.programs.insert("worker".into(), worker);
    nested.budget.max_episodes = 5;
    config.programs.insert("nested".into(), nested);
    config.grants.spawn.push("nested".into());
    let spawner = ProcessSpawner::new(
        "ep_root".into(),
        dir,
        config.clone(),
        Arc::new(Lines::default()),
        Arc::new(Router::new()),
        Arc::new(Seen::default()),
    )
    .unwrap();
    let ask = |program: &str| {
        let req = SpawnRequest {
            program: program.into(),
            task: "t".into(),
            context: SpawnContext::Fresh,
            reserve: BudgetAmount::default(),
            call_id: "tc".into(),
        };
        spawner.reserve_for(&req).episodes
    };
    assert_eq!(ask("worker"), Some(1), "a child with no spawn grant holds one episode");
    assert_eq!(ask("nested"), Some(5), "a child that may spawn asks for the allowance it declares");

    let granted = BudgetAmount { model_calls: Some(5), episodes: Some(2), ..Default::default() };
    let child = child_config(&config, &config.programs["nested"], "t".into(), granted);
    assert_eq!(child.budget.max_episodes, 2, "the granted share caps the child's own allowance");
}

/// docs/config.md `budget` and docs/workflow.md "Model nodes": a model node
/// inside a child program's nested workflow is descendant work even when
/// that child declares no explicit spawn grant.
#[test]
fn a_workflow_bearing_child_asks_for_its_subtree_capacity() {
    let dir = scratch("spawn", "workflow-share");
    let mut config = parent_config();
    let child = config.programs.get_mut("worker").unwrap();
    child.budget.max_episodes = 5;
    child.budget.max_concurrent = 3;
    child.workflow = Some(
        serde_json::from_value(serde_json::json!({ "nodes": {
            "outer": { "workflow": { "nodes": {
                "model": { "model": {
                    "name": "model", "instructions": { "r": "work" }, "tools": ["notify"],
                    "grants": { "read": ["/src"] }, "budget": { "model_calls": 1 }
                }, "terminal": true }
            } } }
        } }))
        .unwrap(),
    );
    assert!(child.grants.spawn.is_empty(), "the workflow is the child's only source of descendants");
    let spawner = ProcessSpawner::new(
        "ep_root".into(),
        dir,
        config,
        Arc::new(Lines::default()),
        Arc::new(Router::new()),
        Arc::new(Seen::default()),
    )
    .unwrap();
    let req = SpawnRequest {
        program: "worker".into(),
        task: "t".into(),
        context: SpawnContext::Fresh,
        reserve: BudgetAmount::default(),
        call_id: "tc".into(),
    };
    let reserved = spawner.reserve_for(&req);
    assert_eq!(reserved.episodes, Some(5));
    assert_eq!(reserved.concurrent, Some(4), "the child occupies one slot beside its three-child allowance");
}

/// docs/config.md `budget`: `max_concurrent` bounds child episodes running
/// anywhere below the root. A child's subtree lease includes that child.
#[test]
fn a_root_concurrency_lease_caps_grandchildren() {
    let dir = scratch("spawn", "concurrent-share");
    let mut config = parent_config();
    config.budget.max_concurrent = 2;
    let worker = config.programs["worker"].clone();
    let nested = config.programs.get_mut("worker").unwrap();
    nested.grants.spawn = vec!["worker".into()];
    nested.programs.insert("worker".into(), worker);
    nested.budget.model_calls = 5;
    nested.budget.max_concurrent = 4;
    let spawner = ProcessSpawner::new(
        "ep_root".into(),
        dir,
        config.clone(),
        Arc::new(Lines::default()),
        Arc::new(Router::new()),
        Arc::new(Seen::default()),
    )
    .unwrap();
    let req = SpawnRequest {
        program: "worker".into(),
        task: "t".into(),
        context: SpawnContext::Fresh,
        reserve: BudgetAmount::default(),
        call_id: "tc".into(),
    };
    let mut root_pool = crate::budget::Pool::new(config.budget.clone());
    let granted = root_pool.reserve("child", spawner.reserve_for(&req)).unwrap();
    assert_eq!(granted.concurrent, Some(2), "the root grants only its two remaining slots");
    let child = child_config(&config, &config.programs["worker"], "t".into(), granted);
    assert_eq!(child.budget.max_concurrent, 1, "the running child occupies one root slot");
    let mut child_pool = crate::budget::Pool::new(child.budget);
    let leaf = || BudgetAmount { model_calls: Some(1), episodes: Some(1), concurrent: Some(1), ..Default::default() };
    child_pool.reserve("grandchild", leaf()).unwrap();
    assert_eq!(child_pool.reserve("second", leaf()).unwrap_err(), ExhaustedLimit::Concurrency);
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
        reserve: BudgetAmount { model_calls: Some(5), ..Default::default() },
        call_id: "tc_spawn".into(),
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
    let result = format!(r#"{{"type":"tool/result","call_id":"tc_1","episode_id":"{}","value":1}}"#, handle.child_id);
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
    let kinds: Vec<String> = seen.0.lock().unwrap().iter().map(|(_, e)| e.data.type_name()).collect();
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
        call_id: "tc_spawn".into(),
    };
    let err = spawner.spawn(req).err().unwrap().to_string();
    assert!(err.contains("grants.spawn"), "{err}");
}

#[test]
fn routing_to_an_unknown_episode_is_an_error() {
    let router = Router::new();
    assert!(router.route("ep_none", "{}").is_err());
}
