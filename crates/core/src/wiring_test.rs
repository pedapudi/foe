use super::*;
use crate::exec::tests::scratch;
use crate::process_boundary::tests::{remove_test_boundary, test_boundary};
use crate::spawn::tests::{fake_child, parent_config, process_spawner, script, wait_for, waiting_child, Lines, Seen};
use crate::spawn::ChildObserver;
use foe_log::{EpisodeStart, Event, Outcome, RuntimeInfo, SandboxInfo, SandboxMode, SpawnContext};
use std::path::Path;
use std::sync::mpsc;

struct Started(mpsc::Sender<()>);

impl ChildObserver for Started {
    fn observe(&self, _child_id: &str, event: &Event) {
        if matches!(event.data, EventData::EpisodeStart(_)) {
            let _ = self.0.send(());
        }
    }
}

fn start() -> EpisodeStart {
    EpisodeStart {
        id: "ep_root".into(),
        parent_id: None,
        fork_origin: None,
        team_id: None,
        contract: serde_json::json!({}),
        contract_fingerprint: "sha256:0".into(),
        task: "t".into(),
        runtime: RuntimeInfo { version: "0".into(), build: "unknown".into() },
        sandbox: SandboxInfo {
            mode: SandboxMode::Off,
            landlock_abi: 0,
            resolved_permissions: Default::default(),
            process_boundary: Default::default(),
        },
        effective_budget: None,
    }
}

fn request(contract: &str, reserve: BudgetAmount) -> SpawnRequest {
    SpawnRequest {
        contract: contract.into(),
        task: "do it".into(),
        context: SpawnContext::Fresh,
        reserve,
        call_id: "tc_1".into(),
    }
}

fn types(log: &Log) -> Vec<String> {
    log.events().iter().map(|e| e.data.type_name()).collect()
}

/// docs/log-format.md "Writers": child settlement returns recording failure,
/// releases local capacity, and prevents subsequent child admission.
#[tokio::test]
async fn settlement_recording_failure_cannot_publish_success_or_admit_another_child() {
    struct RejectSettlement(&'static str);
    impl std::io::Write for RejectSettlement {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            let event: Event = serde_json::from_slice(bytes).unwrap();
            if event.data.type_name() == self.0 {
                return Err(std::io::Error::other("settlement mirror failed"));
            }
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    for failed_event in ["spawn/end", "budget/release"] {
        let dir = scratch("wiring", "settlement-recording");
        let log = Arc::new(Log::create_or_open(&dir, Some(Box::new(RejectSettlement(failed_event)))).unwrap());
        log.append(EventData::EpisodeStart(start())).unwrap();
        let mut child_start = start();
        child_start.id = "ep_child".into();
        child_start.parent_id = Some("ep_root".into());
        let lines = [
            Event { seq: 0, time: 1, version: Some(foe_log::LOG_VERSION), data: EventData::EpisodeStart(child_start) },
            Event {
                seq: 1,
                time: 1,
                version: None,
                data: EventData::EpisodeEnd { outcome: Outcome::Completed { value: serde_json::json!("done") } },
            },
        ];
        let body = format!(
            "#!/bin/sh\nprintf '%s\\n' '{}' '{}'\n",
            serde_json::to_string(&lines[0]).unwrap(),
            serde_json::to_string(&lines[1]).unwrap()
        );
        let config = parent_config();
        let pool = Arc::new(Mutex::new(Pool::new(config.budget.clone())));
        let inner = process_spawner(
            "ep_root",
            dir.to_path_buf(),
            config,
            Arc::new(Lines::default()),
            Arc::new(Router::new()),
            Arc::new(Seen::default()),
        )
        .with_launcher(script(&dir, "ending-child", &body));
        let spawner = BudgetedSpawner::new(Arc::new(inner), log.clone(), pool.clone());
        let req = request("worker", BudgetAmount { model_calls: Some(5), ..Default::default() });
        let child = spawner.spawn(req.clone()).unwrap();
        let settled = child.run.settle().await;
        assert!(matches!(settled.outcome, Outcome::Failed { ref error } if error.contains(failed_event)));
        assert_eq!(lock(&pool).active_children(), 0);
        let bytes = std::fs::read(dir.join("episode.jsonl")).unwrap();
        assert!(matches!(spawner.spawn(req), Err(CapError::Log(_))));
        assert_eq!(std::fs::read(dir.join("episode.jsonl")).unwrap(), bytes);
        assert!(foe_log::fold::fold(&foe_log::fold::read_all(&dir).unwrap()).is_ok());
    }
}

/// docs/log-format.md "Budget and spawn": a child's reservation is recorded
/// before it starts and released with what it spent when it settles.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_spawn_reserves_records_and_releases_budget() {
    let dir = scratch("wiring", "spawn");
    let log = Arc::new(Log::create_or_open(&dir, None).unwrap());
    log.append(EventData::EpisodeStart(start())).unwrap();
    let config = parent_config();
    let pool = Arc::new(Mutex::new(Pool::new(config.budget.clone())));
    let router = Arc::new(Router::new());
    let inner = process_spawner(
        "ep_root",
        dir.to_path_buf(),
        config,
        Arc::new(Lines::default()),
        router.clone(),
        Arc::new(Seen::default()),
    )
    .with_launcher(fake_child(&dir));
    let spawner = BudgetedSpawner::new(Arc::new(inner), log.clone(), pool.clone());

    let refused = spawner
        .spawn(request("worker", BudgetAmount { model_calls: Some(99), ..Default::default() }))
        .err()
        .expect("the reservation exceeds the pool");
    assert!(matches!(refused, CapError::Budget { limit: foe_log::ExhaustedLimit::ModelCalls, .. }));
    let failure = crate::ToolValue::from_cap_error("spawn", refused).failure.unwrap();
    assert_eq!(failure.code, foe_log::ToolFailureCode::BudgetExhausted);
    assert!(!failure.retryable);
    let message = failure.message;
    assert!(message.contains("model_calls"), "a reservation beyond the remainder names the limit: {message}");
    assert_eq!(types(&log), ["episode/start"], "a refused reservation writes nothing");

    let handle = spawner.spawn(request("worker", BudgetAmount { model_calls: Some(5), ..Default::default() })).unwrap();
    assert_eq!(types(&log), ["episode/start", "budget/reserve", "spawn/start"]);
    assert_eq!(lock(&pool).remaining().model_calls, Some(15), "5 of 20 calls are reserved");
    let child_id = handle.child_id.clone();
    wait_for(|| router.has_child(&child_id).then_some(()));
    let answer = r#"{"type":"model/chunk","request_id":"rq_1","chunk":{"kind":"done"}}"#;
    router.route(&child_id, answer).unwrap();
    wait_for(|| (log.events().len() == 3).then_some(()));
    let result = r#"{"type":"tool/result","call_id":"tc_1","value":1}"#;
    wait_for(|| router.route(&child_id, result).ok());
    let settled = handle.run.clone().settle().await;
    assert_eq!(types(&log)[3..], ["spawn/end", "budget/release"], "the handle settles after the parent's events");
    let EventData::BudgetRelease { spent, .. } = &log.events()[4].data else { panic!() };
    assert_eq!(*spent, settled.spent);
    assert_eq!(spent.model_calls, Some(1));
    assert_eq!(lock(&pool).remaining().model_calls, Some(19), "the reservation is returned and the spend debited");
    assert_eq!(lock(&pool).active_children(), 0, "the handle settles after its reservation is returned");
}

/// docs/sandbox.md "Process ownership": a child reservation remains held
/// until cgroup cleanup has killed a detached descendant. The parent then
/// records the end and release before returning capacity and the handle.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_subtree_boundary_is_empty_before_the_reservation_returns() {
    let Ok((boundary, invocation)) = test_boundary("lease-order") else {
        return;
    };
    let boundary = Arc::new(boundary);
    let dir = scratch("wiring", "subtree-cleanup");
    let pid_file = dir.join("detached.pid");
    let body = format!(
        r#"#!/bin/sh
/usr/bin/setsid -f /bin/sh -c 'echo $$ > "$1"; exec /bin/sleep 30' foe-detached '{}'
while [ ! -s '{}' ]; do :; done
echo '{{"seq":0,"time":1,"type":"episode/start","data":{{"id":"ep_child","parent_id":"ep_root","fork_origin":null,"team_id":"ep_root","contract":{{}},"contract_fingerprint":"sha256:0","task":"t","runtime":{{"version":"0","build":"unknown"}},"sandbox":{{"mode":"off","landlock_abi":0,"resolved_permissions":{{}},"process_boundary":{{"kind":"process-group","subtree_cleanup":"observational"}}}}}}}}'
read _
"#,
        pid_file.display(),
        pid_file.display()
    );
    let log = Arc::new(Log::create_or_open(&dir, None).unwrap());
    log.append(EventData::EpisodeStart(start())).unwrap();
    let config = parent_config();
    let pool = Arc::new(Mutex::new(Pool::new(config.budget.clone())));
    let (started_tx, started_rx) = mpsc::channel();
    let router = Arc::new(Router::new());
    let inner = process_spawner(
        "ep_root",
        dir.to_path_buf(),
        config,
        Arc::new(Lines::default()),
        router.clone(),
        Arc::new(Started(started_tx)),
    )
    .with_launcher(script(&dir, "detaching-foe.sh", &body))
    .with_boundary(Some(boundary.clone()));
    let spawner = BudgetedSpawner::new(Arc::new(inner), log.clone(), pool.clone());
    let reserve = BudgetAmount { model_calls: Some(5), ..Default::default() };
    let handle = spawner.spawn(request("worker", reserve)).unwrap();
    started_rx.recv().unwrap();
    let detached = std::fs::read_to_string(&pid_file).unwrap().trim().to_string();
    assert!(Path::new(&format!("/proc/{detached}")).exists(), "the detached descendant started");
    router.route(&handle.child_id, "continue").unwrap();
    handle.run.settle().await;
    let stat = std::fs::read_to_string(format!("/proc/{detached}/stat")).unwrap_or_default();
    assert!(
        stat.is_empty() || stat.rsplit_once(") ").is_some_and(|(_, fields)| fields.starts_with('Z')),
        "the detached process exited before settlement"
    );
    assert_eq!(types(&log)[3..], ["spawn/end", "budget/release"]);
    assert_eq!(lock(&pool).active_children(), 0);
    remove_test_boundary(&boundary, &invocation);
}

/// docs/log-format.md "Budget and spawn": the parent records a reservation
/// and spawn before process creation. A process-start failure closes both
/// obligations and returns the reservation without leaving a child.
#[tokio::test]
async fn a_process_start_failure_closes_the_spawn_and_reservation() {
    let dir = scratch("wiring", "start-failure");
    let log = Arc::new(Log::create_or_open(&dir, None).unwrap());
    log.append(EventData::EpisodeStart(start())).unwrap();
    let config = parent_config();
    let pool = Arc::new(Mutex::new(Pool::new(config.budget.clone())));
    let inner = process_spawner(
        "ep_root",
        dir.to_path_buf(),
        config,
        Arc::new(Lines::default()),
        Arc::new(Router::new()),
        Arc::new(Seen::default()),
    )
    .with_launcher(vec!["/no-such-foe-child".into()]);
    let spawner = BudgetedSpawner::new(Arc::new(inner), log.clone(), pool.clone());

    let reserve = BudgetAmount { model_calls: Some(5), ..Default::default() };
    let error = spawner.spawn(request("worker", reserve)).err().expect("the process cannot start");
    assert!(matches!(error, CapError::ProcessStart(_)));
    let error = error.to_string();
    assert!(error.contains("No such file") || error.contains("not found"), "{error}");
    assert_eq!(types(&log), ["episode/start", "budget/reserve", "spawn/start", "spawn/end", "budget/release"]);
    let EventData::SpawnEnd { outcome: Outcome::Failed { error }, .. } = &log.events()[3].data else { panic!() };
    assert!(!error.is_empty(), "the failed spawn records the process error");
    assert_eq!(lock(&pool).active_children(), 0);
    assert_eq!(lock(&pool).remaining().model_calls, Some(20));
    foe_log::fold::fold(&log.events()).expect("the interrupted log has no open obligation");
}

/// docs/config.md "budget": a child's budget is reserved from its parent's
/// remainder. A `spawn` tool call names no amount, so the reservation is
/// the amount the child contract declares, which leaves the parent its own
/// remainder and room for a second child up to `max_concurrent`.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_spawn_without_an_amount_reserves_what_the_contract_declares() {
    let dir = scratch("wiring", "declared");
    let log = Arc::new(Log::create_or_open(&dir, None).unwrap());
    log.append(EventData::EpisodeStart(start())).unwrap();
    let config: foe_contract::ContractDocument = serde_json::from_value(serde_json::json!({
        "version": 4, "name": "lead", "instructions": {"r": "lead"}, "tools": ["spawn"],
        "grants": {"read": ["/tmp"], "spawn": ["worker"]},
        "budget": {"model_calls": 20, "input_tokens": 1000, "output_tokens": 500},
        "sandbox": {"mode": "off"},
        "child_contracts": {"worker": {
            "name": "worker", "instructions": {"r": "work"}, "tools": ["notify"],
            "grants": {"read": ["/tmp"]},
            "budget": {"model_calls": 5, "input_tokens": 100, "output_tokens": 50}
        }},
        "task": "lead task"
    }))
    .unwrap();
    let pool = Arc::new(Mutex::new(Pool::new(config.budget.clone())));
    let router = Arc::new(Router::new());
    let inner = process_spawner(
        "ep_root",
        dir.to_path_buf(),
        config,
        Arc::new(Lines::default()),
        router.clone(),
        Arc::new(Seen::default()),
    )
    .with_launcher(fake_child(&dir));
    let spawner = BudgetedSpawner::new(Arc::new(inner), log.clone(), pool.clone());

    spawner.spawn(request("worker", BudgetAmount::default())).unwrap();
    let EventData::BudgetReserve { reserved, .. } = &log.events()[1].data else { panic!() };
    assert_eq!(reserved.model_calls, Some(5), "the child contract declares five calls");
    assert_eq!(reserved.input_tokens, Some(100), "the child contract declares a hundred input tokens");
    assert_eq!(reserved.output_tokens, Some(50), "the child contract declares fifty output tokens");
    assert_eq!(lock(&pool).remaining().model_calls, Some(15), "the parent keeps the rest of the pool");

    spawner.spawn(request("worker", BudgetAmount::default())).unwrap();
    assert_eq!(lock(&pool).active_children(), 2, "a second child fits beside the first");
    assert_eq!(lock(&pool).remaining().model_calls, Some(10));
}

/// docs/config.md "budget": `max_episodes` is the lifetime count of
/// episodes in the tree. A child reports how many episodes its own subtree
/// held, so grandchildren the root never sees are counted against the
/// root's allowance and the next spawn is refused.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_grandchild_counts_against_the_root_episode_allowance() {
    let dir = scratch("wiring", "episodes");
    let log = Arc::new(Log::create_or_open(&dir, None).unwrap());
    log.append(EventData::EpisodeStart(start())).unwrap();
    let mut config = parent_config();
    config.budget.max_episodes = 4;
    // The child may spawn in turn, so its reservation is the allowance it
    // declares rather than a single episode.
    config.child_contracts.get_mut("worker").unwrap().grants.spawn = vec!["worker".into()];
    let mut leaf = config.child_contracts["worker"].clone();
    leaf.grants.spawn.clear();
    config.child_contracts.get_mut("worker").unwrap().child_contracts.insert("worker".into(), leaf);
    config.child_contracts.get_mut("worker").unwrap().budget.max_episodes = 3;
    config.child_contracts.get_mut("worker").unwrap().budget.model_calls = 5;
    let pool = Arc::new(Mutex::new(Pool::new(config.budget.clone())));
    let router = Arc::new(Router::new());
    let inner = process_spawner(
        "ep_root",
        dir.to_path_buf(),
        config,
        Arc::new(Lines::default()),
        router.clone(),
        Arc::new(Seen::default()),
    )
    .with_launcher(crate::spawn::tests::nesting_child(&dir));
    let spawner = BudgetedSpawner::new(Arc::new(inner), log.clone(), pool.clone());

    let handle = spawner.spawn(request("worker", BudgetAmount::default())).unwrap();
    let EventData::BudgetReserve { reserved, .. } = &log.events()[1].data else { panic!() };
    assert_eq!(reserved.episodes, Some(3), "the child receives the allowance it declares");
    handle.run.clone().settle().await;
    wait_for(|| (log.events().len() == 5).then_some(()));
    let EventData::BudgetRelease { spent, .. } = &log.events()[4].data else { panic!() };
    assert_eq!(spent.episodes, Some(3), "the child, plus the two episodes its own release accounts for");
    assert_eq!(lock(&pool).remaining().episodes, Some(0), "the root's allowance of four is used up");

    let refused = spawner.spawn(request("worker", BudgetAmount::default()));
    let message = refused.err().map(|e| e.to_string()).unwrap_or_default();
    assert!(message.contains("episodes"), "a spawn past the tree's allowance names the limit: {message}");
}

/// docs/log-format.md "Open obligations": an episode that ends while a child
/// runs closes what the child opened. The teardown asks the child to end and
/// waits for the `spawn/end` and `budget/release` its settlement writes, so
/// the `episode/end` that follows is valid and no reservation stands.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ending_an_episode_settles_a_child_that_is_still_running() {
    let dir = scratch("wiring", "settle");
    let log = Arc::new(Log::create_or_open(&dir, None).unwrap());
    log.append(EventData::EpisodeStart(start())).unwrap();
    let config = parent_config();
    let pool = Arc::new(Mutex::new(Pool::new(config.budget.clone())));
    let router = Arc::new(Router::new());
    let inner = process_spawner(
        "ep_root",
        dir.to_path_buf(),
        config,
        Arc::new(Lines::default()),
        router.clone(),
        Arc::new(Seen::default()),
    )
    .with_launcher(waiting_child(&dir));
    let spawner = BudgetedSpawner::new(Arc::new(inner), log.clone(), pool.clone());

    let handle = spawner.spawn(request("worker", BudgetAmount { model_calls: Some(5), ..Default::default() })).unwrap();
    wait_for(|| router.has_child(&handle.child_id).then_some(()));
    assert_eq!(lock(&pool).active_children(), 1);
    let open: std::collections::BTreeSet<foe_log::Obligation> =
        foe_log::fold::open_obligations(&log.events()).into_iter().map(|(kind, _)| kind).collect();
    let expected = [foe_log::Obligation::Child, foe_log::Obligation::Reservation].into_iter().collect();
    assert_eq!(open, expected, "the child and its reservation are open");

    crate::loop_::settle(&log, &pool, Some(&router), None).await.unwrap();
    log.append(EventData::EpisodeEnd { outcome: Outcome::Completed { value: serde_json::Value::Null } }).unwrap();
    assert_eq!(types(&log)[3..], ["spawn/end", "budget/release", "episode/end"]);
    assert_eq!(lock(&pool).active_children(), 0, "the reservation returned to the pool");
    foe_log::fold::fold(&log.events()).expect("the log is well-formed");
}
