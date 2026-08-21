use super::*;
use crate::exec::tests::scratch;
use crate::spawn::tests::{fake_child, parent_config, wait_for, Lines, Seen};
use foe_log::{EpisodeStart, RuntimeInfo, SandboxInfo, SandboxMode, SpawnContext};

fn start() -> EpisodeStart {
    EpisodeStart {
        id: "ep_root".into(),
        parent_id: None,
        fork_origin: None,
        team_id: None,
        program: serde_json::json!({}),
        identity: "sha256:0".into(),
        task: "t".into(),
        runtime: RuntimeInfo { version: "0".into(), build: "unknown".into() },
        sandbox: SandboxInfo { mode: SandboxMode::Off, landlock_abi: 0 },
    }
}

fn request(program: &str, reserve: BudgetAmount) -> SpawnRequest {
    SpawnRequest {
        program: program.into(),
        task: "do it".into(),
        context: SpawnContext::Fresh,
        reserve,
        call_id: "tc_1".into(),
    }
}

fn types(log: &Log) -> Vec<String> {
    log.events().iter().map(|e| e.data.type_name()).collect()
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
    let inner = ProcessSpawner::new(
        "ep_root".into(),
        dir.clone(),
        config,
        Arc::new(Lines::default()),
        router.clone(),
        Arc::new(Seen::default()),
    )
    .unwrap()
    .with_launcher(fake_child(&dir));
    let spawner = BudgetedSpawner::new(Arc::new(inner), log.clone(), pool.clone());

    let refused = spawner.spawn(request("worker", BudgetAmount { model_calls: Some(99), ..Default::default() }));
    let message = refused.err().map(|e| e.to_string()).unwrap_or_default();
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
    wait_for(|| (log.events().len() == 5).then_some(()));
    assert_eq!(types(&log)[3..], ["spawn/end", "budget/release"]);
    let EventData::BudgetRelease { spent, .. } = &log.events()[4].data else { panic!() };
    assert_eq!(*spent, settled.spent);
    assert_eq!(spent.model_calls, Some(1));
    assert_eq!(lock(&pool).remaining().model_calls, Some(19), "the reservation is returned and the spend debited");
    assert_eq!(lock(&pool).active_children(), 0);
}
