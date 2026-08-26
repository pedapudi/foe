use super::*;
use crate::exec::tests::scratch;
use crate::spawn::tests::{fake_child, parent_config, wait_for, waiting_child, Lines, Seen};
use foe_log::{EpisodeStart, Outcome, RuntimeInfo, SandboxInfo, SandboxMode, SpawnContext};

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
    assert_eq!(types(&log)[3..], ["spawn/end", "budget/release"], "the handle settles after the parent's events");
    let EventData::BudgetRelease { spent, .. } = &log.events()[4].data else { panic!() };
    assert_eq!(*spent, settled.spent);
    assert_eq!(spent.model_calls, Some(1));
    assert_eq!(lock(&pool).remaining().model_calls, Some(19), "the reservation is returned and the spend debited");
    assert_eq!(lock(&pool).active_children(), 0, "the handle settles after its reservation is returned");
}

/// docs/config.md "budget": a child's budget is reserved from its parent's
/// remainder. A `spawn` tool call names no amount, so the reservation is
/// the amount the child program declares, which leaves the parent its own
/// remainder and room for a second child up to `max_concurrent`.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_spawn_without_an_amount_reserves_what_the_program_declares() {
    let dir = scratch("wiring", "declared");
    let log = Arc::new(Log::create_or_open(&dir, None).unwrap());
    log.append(EventData::EpisodeStart(start())).unwrap();
    let config: foe_config::Config = serde_json::from_value(serde_json::json!({
        "version": 3, "name": "lead", "instructions": {"r": "lead"}, "tools": ["spawn"],
        "grants": {"read": ["/src"], "spawn": ["worker"]},
        "budget": {"model_calls": 20, "input_tokens": 1000, "output_tokens": 500},
        "sandbox": {"mode": "off"},
        "programs": {"worker": {
            "name": "worker", "instructions": {"r": "work"}, "tools": ["notify"],
            "grants": {"read": ["/src"]},
            "budget": {"model_calls": 5, "input_tokens": 100, "output_tokens": 50}
        }},
        "task": "lead task"
    }))
    .unwrap();
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

    spawner.spawn(request("worker", BudgetAmount::default())).unwrap();
    let EventData::BudgetReserve { reserved, .. } = &log.events()[1].data else { panic!() };
    assert_eq!(reserved.model_calls, Some(5), "the child program declares five calls");
    assert_eq!(reserved.input_tokens, Some(100), "the child program declares a hundred input tokens");
    assert_eq!(reserved.output_tokens, Some(50), "the child program declares fifty output tokens");
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
    config.programs.get_mut("worker").unwrap().grants.spawn = vec!["worker".into()];
    let worker = config.programs["worker"].clone();
    config.programs.get_mut("worker").unwrap().programs.insert("worker".into(), worker);
    config.programs.get_mut("worker").unwrap().budget.max_episodes = 3;
    config.programs.get_mut("worker").unwrap().budget.model_calls = 5;
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
    let inner = ProcessSpawner::new(
        "ep_root".into(),
        dir.clone(),
        config,
        Arc::new(Lines::default()),
        router.clone(),
        Arc::new(Seen::default()),
    )
    .unwrap()
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
