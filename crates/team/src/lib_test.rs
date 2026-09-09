use super::*;
use foe_contract::Budget;
use std::sync::atomic::{AtomicUsize, Ordering};

fn budget() -> Budget {
    Budget {
        model_calls: Some(10),
        input_tokens: Some(1000),
        output_tokens: Some(400),
        seconds: None,
        max_depth: 1,
        max_episodes: 3,
        max_concurrent: 1,
        loop_threshold: 3,
    }
}

#[derive(Default)]
struct MemLog(Mutex<Vec<Event>>);

impl LeadLog for MemLog {
    fn append(&self, data: EventData) -> Result<(), CapError> {
        let mut events = self.0.lock().unwrap();
        let seq = events.len() as u64;
        events.push(Event { seq, time: 0, version: None, data });
        Ok(())
    }
    fn check(&self) -> Result<(), CapError> {
        Ok(())
    }
    fn events(&self) -> Vec<Event> {
        self.0.lock().unwrap().clone()
    }
    fn with_events(&self, read: &mut dyn FnMut(&[Event])) {
        read(&self.0.lock().unwrap());
    }
}

impl MemLog {
    fn append(&self, data: EventData) {
        LeadLog::append(self, data).unwrap();
    }
}

/// docs/log-format.md "Writers": a failed team append cannot deliver a
/// message, and a scheduler cannot admit work after recording has failed.
#[test]
fn recording_failure_prevents_message_delivery_and_scheduling() {
    struct FailedLog;
    impl LeadLog for FailedLog {
        fn append(&self, _: EventData) -> Result<(), CapError> {
            self.check()
        }
        fn check(&self) -> Result<(), CapError> {
            Err(CapError::Log(foe_log::LogError::Recording("team/message recording failed".into())))
        }
        fn events(&self) -> Vec<Event> {
            vec![event(0, start())]
        }
        fn with_events(&self, read: &mut dyn FnMut(&[Event])) {
            read(&[event(0, start())]);
        }
    }
    struct NoLaunch;
    impl Spawner for NoLaunch {
        fn allocate_id(&self) -> String {
            panic!("recording failure must precede allocation")
        }
        fn launch(&self, _: String, _: SpawnRequest) -> Result<foe_core::SpawnHandle, CapError> {
            panic!("recording failure must precede child launch")
        }
    }
    let inbox = Arc::new(MemInbox::default());
    let team = Arc::new(Team::new(
        "ep_lead".into(),
        Arc::new(FailedLog),
        inbox.clone(),
        Arc::new(Router::new()),
        Arc::new(Mutex::new(Pool::new(budget()))),
    ));
    assert!(team.send("ep_lead", "lead", text_content("message"), Correlate::Statement).is_err());
    assert!(inbox.0.lock().unwrap().is_empty());
    assert!(team.schedule(Arc::new(NoLaunch)).is_err());
}

#[derive(Default)]
struct MemInbox(Mutex<Vec<InboxItem>>);

impl InboxSink for MemInbox {
    fn append(&self, item: InboxItem) {
        self.0.lock().unwrap().push(item);
    }
}

fn event(seq: u64, data: EventData) -> Event {
    Event { seq, time: 0, version: None, data }
}

fn roster(seq: u64, id: &str, name: &str, phase: MemberPhase) -> Event {
    event(seq, EventData::TeamRoster { member_id: id.into(), name: name.into(), description: String::new(), phase })
}

fn message(seq: u64, id: &str, to: &str) -> Event {
    event(seq, EventData::TeamMessage { message_id: id.into(), from: "ep_a".into(), to: to.into(), content: vec![] })
}

fn start() -> EventData {
    EventData::EpisodeStart(foe_log::EpisodeStart {
        id: "ep_lead".into(),
        parent_id: None,
        fork_origin: None,
        team_id: None,
        contract: serde_json::json!({ "name": "lead" }),
        contract_fingerprint: "sha256:0".into(),
        task: "deliver the feature".into(),
        runtime: foe_log::RuntimeInfo { version: "0".into(), build: "unknown".into() },
        sandbox: foe_log::SandboxInfo {
            mode: foe_log::SandboxMode::Off,
            landlock_abi: 0,
            resolved_permissions: Default::default(),
            process_boundary: Default::default(),
        },
        effective_budget: None,
    })
}

/// docs/design.md "Agent teams": lifecycle evidence projects a one-agent
/// team and its root task without a team event.
#[test]
fn episode_lifecycle_is_the_singleton_team() {
    let mut events = vec![event(0, start())];
    let state = fold(&events);
    assert_eq!((state.lead_id.as_str(), state.roster.len(), state.tasks.len()), ("ep_lead", 1, 1));
    assert_eq!(state.tasks[0].task_id, "task_root");
    assert_eq!((state.tasks[0].status, state.roster[0].phase), (TaskStatus::Running, MemberPhase::Active));
    assert!(!events.iter().any(|event| matches!(event.data, EventData::TeamTask(_) | EventData::TeamRoster { .. })));

    let outcome = Outcome::Completed { value: serde_json::json!({ "done": true }) };
    events.push(event(1, EventData::EpisodeEnd { outcome: outcome.clone() }));
    let settled = fold(&events);
    assert_eq!(settled.tasks[0].status, TaskStatus::Completed);
    assert_eq!(settled.tasks[0].outcome.as_ref(), Some(&outcome));
    assert_eq!(settled.roster[0].phase, MemberPhase::Active);
    assert_eq!(settled.roster[0].task_status, Some(TaskStatus::Completed));
}

fn task(id: &str, revision: u64, status: TaskStatus) -> TeamTask {
    TeamTask {
        task_id: id.into(),
        revision,
        name: "worker".into(),
        contract: "worker".into(),
        description: "work".into(),
        context: SpawnContext::Fresh,
        status,
        owner: None,
        blocked_by: vec![],
        write: vec!["src".into()],
        outcome: None,
        call_id: "tc".into(),
    }
}

/// docs/log-format.md `team/task`: folding keeps the highest recorded
/// revision and preserves the lead log's task order.
#[test]
fn fold_keeps_latest_task_revisions_in_creation_order() {
    let events = [
        event(0, start()),
        event(1, EventData::TeamTask(task("task_01", 0, TaskStatus::Queued))),
        event(2, EventData::TeamTask(task("task_02", 0, TaskStatus::Queued))),
        event(3, EventData::TeamTask(task("task_01", 1, TaskStatus::Running))),
    ];
    let state = fold(&events);
    assert_eq!(
        state.tasks.iter().map(|task| task.task_id.as_str()).collect::<Vec<_>>(),
        ["task_root", "task_01", "task_02"]
    );
    assert_eq!((state.tasks[1].revision, state.tasks[1].status), (1, TaskStatus::Running));
}

#[test]
fn fold_tracks_phases_queue_and_deliveries() {
    let events = [
        roster(1, "ep_a", "reviewer", MemberPhase::Provisioning),
        roster(2, "ep_b", "tester", MemberPhase::Provisioning),
        roster(3, "ep_a", "reviewer", MemberPhase::Active),
        message(4, "tm_01", "ep_b"),
        message(5, "tm_02", "ep_b"),
        event(6, EventData::TeamDelivered { message_id: "tm_01".into(), to: "ep_b".into() }),
    ];
    let state = fold(&events);
    assert_eq!(state.roster.len(), 2);
    assert_eq!(state.member("reviewer").unwrap().phase, MemberPhase::Active);
    assert_eq!(state.member("tester").unwrap().phase, MemberPhase::Provisioning);
    assert_eq!(state.queue.len(), 2);
    let pending: Vec<&str> = state.undelivered().map(|m| m.message_id.as_str()).collect();
    assert_eq!(pending, ["tm_02"]);
}

#[test]
fn fold_skips_team_events_copied_by_seeding() {
    let events = [
        roster(1, "ep_a", "reviewer", MemberPhase::Active),
        message(2, "tm_01", "ep_a"),
        event(3, EventData::TeamTask(task("task_01", 0, TaskStatus::Queued))),
        event(4, EventData::SeedEnd {}),
        roster(5, "ep_c", "writer", MemberPhase::Active),
        event(6, EventData::TeamTask(task("task_01", 0, TaskStatus::Queued))),
    ];
    let state = fold(&events);
    assert_eq!(state.roster.len(), 1);
    assert_eq!(state.roster[0].name, "writer");
    assert_eq!(state.tasks.len(), 1);
    assert!(state.queue.is_empty());
}

fn team() -> (Arc<Team>, Arc<MemLog>, Arc<MemInbox>, Arc<Router>) {
    let log = Arc::new(MemLog::default());
    let inbox = Arc::new(MemInbox::default());
    let router = Arc::new(Router::new());
    let pool = Arc::new(Mutex::new(Pool::new(budget())));
    let team = Arc::new(Team::new("ep_lead".into(), log.clone(), inbox.clone(), router.clone(), pool));
    (team, log, inbox, router)
}

/// docs/log-format.md "Teams": message identity survives resume and remains
/// distinct from a copied source queue after seeding.
#[test]
fn message_identity_comes_from_the_recorded_queue_and_lead_episode() {
    let (first, log, inbox, router) = team();
    log.append(start());
    let first_id = first.send("ep_lead", "lead", text_content("first"), Correlate::Statement).unwrap();
    let resumed = Team::new(
        "ep_lead".into(),
        log.clone(),
        inbox.clone(),
        router.clone(),
        Arc::new(Mutex::new(Pool::new(budget()))),
    );
    let second_id = resumed.send("ep_lead", "lead", text_content("second"), Correlate::Statement).unwrap();
    assert_ne!(first_id, second_id);
    let mut copied = log.events();
    let EventData::EpisodeStart(start) = &mut copied[0].data else { panic!() };
    start.id = "ep_fork".into();
    let fork_log = Arc::new(MemLog(Mutex::new(copied)));
    fork_log.append(EventData::SeedEnd {});
    let forked =
        Team::new("ep_fork".into(), fork_log, inbox.clone(), router, Arc::new(Mutex::new(Pool::new(budget()))));
    let fork_id = forked.send("ep_fork", "lead", text_content("fork"), Correlate::Statement).unwrap();
    assert_ne!(fork_id, first_id);
    assert_ne!(fork_id, second_id);
    assert_eq!(inbox.0.lock().unwrap().len(), 3);
}

/// docs/log-format.md "Teams": concurrent senders allocate distinct identities
/// under the same lock that records their queue entries.
#[test]
fn concurrent_senders_record_distinct_messages() {
    let (team, log, inbox, _) = team();
    log.append(start());
    std::thread::scope(|scope| {
        for index in 0..16 {
            let team = team.clone();
            scope.spawn(move || {
                team.send("ep_lead", "lead", text_content(&index.to_string()), Correlate::Statement).unwrap();
            });
        }
    });
    let state = team.state();
    assert_eq!(state.queue.len(), 16);
    assert_eq!(state.queue.iter().map(|message| &message.message_id).collect::<BTreeSet<_>>().len(), 16);
    assert_eq!(inbox.0.lock().unwrap().len(), 16);
}

#[test]
fn notify_from_a_member_becomes_an_inbox_item() {
    let (team, _, inbox, _) = team();
    assert!(team.host_call("ep_a", "notify", &serde_json::json!({})).unwrap().is_error, "content is required");
    let value = team.host_call("ep_a", "notify", &serde_json::json!({ "content": "hi" })).unwrap();
    assert!(!value.is_error);
    let items = inbox.0.lock().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0].source, InboxSource::Child);
    assert_eq!(items[0].from.as_deref(), Some("ep_a"));
    assert_eq!(items[0].content, text_content("hi"));
    assert!(team.host_call("ep_a", "other", &serde_json::json!({})).is_none(), "other calls are forwarded");
}

#[test]
fn send_queues_a_message_and_peer_receipt_records_delivery() {
    let (team, log, _, _) = team();
    let missing = team.host_call("ep_a", "send", &serde_json::json!({ "to": "nobody", "content": "x" })).unwrap();
    assert!(missing.is_error);
    log.append(EventData::TeamRoster {
        member_id: "ep_b".into(),
        name: "tester".into(),
        description: String::new(),
        phase: MemberPhase::Active,
    });
    let sent = team.host_call("ep_a", "send", &serde_json::json!({ "to": "tester", "content": "run it" })).unwrap();
    assert!(!sent.is_error, "{:?}", sent.rendered);
    let state = team.state();
    assert_eq!(state.queue.len(), 1);
    assert_eq!(state.queue[0].to, "ep_b");
    assert_eq!(state.queue[0].from, "ep_a");
    assert_eq!(state.queue[0].content, text_content("run it"));
    assert_eq!(state.undelivered().count(), 1, "the target is not running, so the message stays queued");
    let receipt = InboxItem {
        source: InboxSource::Peer,
        content: vec![],
        from: Some("ep_a".into()),
        message_id: Some(state.queue[0].message_id.clone()),
    };
    team.observe("ep_b", &event(5, EventData::InboxItem(receipt)));
    assert_eq!(team.state().undelivered().count(), 0);
    let listed = team.host_call("ep_a", "team", &serde_json::json!({})).unwrap();
    assert_eq!(listed.rendered.as_deref(), Some("members:\ntester\tep_b\tactive\ntasks:\n"));
}

/// docs/tools.md `send`: an episode in the middle of a tree belongs to one
/// team and leads another, so `send` and `ask` take the same `scope` `team`
/// takes. A root's two teams are one, so both scopes reach its own roster
/// here; the forwarding a member does at `member` scope is the arm `team`
/// already had.
#[tokio::test]
async fn send_and_ask_take_the_scope_that_selects_which_team_they_address() {
    let (team, log, _, _) = team();
    log.append(start());
    log.append(EventData::TeamRoster {
        member_id: "ep_b".into(),
        name: "tester".into(),
        description: String::new(),
        phase: MemberPhase::Active,
    });
    let by_name = |name: &str| tools(team.clone(), None).into_iter().find(|t| t.spec().name == name).unwrap();
    for name in ["send", "ask"] {
        let params = by_name(name).spec().params.clone();
        let scope = &params["properties"]["scope"];
        assert_eq!(scope["enum"], serde_json::json!(["member", "led"]), "{name} takes a scope");
        let led = serde_json::json!({ "to": "tester", "content": "x", "scope": "led" });
        assert!(!by_name(name).call(led, &ctx(None)).await.is_error, "led reaches the team this episode leads");
        let bare = serde_json::json!({ "to": "tester", "content": "x" });
        assert!(!by_name(name).call(bare, &ctx(None)).await.is_error, "a root's two teams are one");
        let wrong = serde_json::json!({ "to": "tester", "content": "x", "scope": "sideways" });
        let refused = by_name(name).call(wrong, &ctx(None)).await;
        assert!(refused.is_error, "{:?}", refused.rendered);
        assert!(refused.rendered.unwrap_or_default().contains("neither member nor led"));
    }
    assert_eq!(team.state().queue.len(), 4, "each accepted call queued one message");
}

/// docs/tools.md "ask": a question carries its own identity, the answer
/// carries the question's, and `wait` with `{reply: id}` holds for that
/// answer alone.
#[tokio::test]
async fn a_question_is_answered_by_identity_and_wakes_only_its_asker() {
    let (team, log, inbox, _) = team();
    log.append(start());
    log.append(EventData::TeamRoster {
        member_id: "ep_b".into(),
        name: "tester".into(),
        description: String::new(),
        phase: MemberPhase::Active,
    });
    let ask = tools(team.clone(), None).into_iter().find(|t| t.spec().name == "ask").unwrap();
    let asked = ask.call(serde_json::json!({ "to": "tester", "content": "which crate?" }), &ctx(None)).await;
    assert!(!asked.is_error, "{:?}", asked.rendered);
    let question = asked.value["message_id"].as_str().expect("`ask` returns the question's identity").to_string();
    let wait = |c: serde_json::Value, deadline: Option<std::time::Instant>| {
        let team = team.clone();
        async move {
            let ctx = deadline.map_or_else(|| ctx(None), ctx_deadline);
            wait_tool(team).call(serde_json::json!({ "until": [c] }), &ctx).await
        }
    };
    // A statement from the same teammate is not the answer.
    log.append(EventData::InboxItem(InboxItem {
        source: InboxSource::Peer,
        content: text_content("still reading"),
        from: Some("ep_b".into()),
        message_id: Some("other".into()),
    }));
    let early = wait(serde_json::json!({ "reply": question.clone() }), Some(soon())).await;
    assert_eq!(early.value["matched"], serde_json::json!("timeout"), "only the answer ends the wait");
    // The teammate answers, naming the question it answers.
    let answer = serde_json::json!({ "to": "lead", "content": "foe-team", "reply_to": question.clone() });
    let sent = team.host_call("ep_b", "send", &answer).unwrap();
    assert!(!sent.is_error, "{:?}", sent.rendered);
    let items = inbox.0.lock().unwrap().clone();
    let reply = items.last().expect("the answer reaches the lead");
    assert_eq!(reply.source, InboxSource::Response);
    assert_eq!(reply.message_id.as_deref(), Some(question.as_str()), "the answer carries the question's identity");
    log.append(EventData::InboxItem(reply.clone()));
    let met = wait(serde_json::json!({ "reply": question.clone() }), None).await;
    assert_eq!(met.value["matched"], serde_json::json!({ "reply": question.clone() }));
    // Question and answer share an identity and are two queue entries with
    // two targets, so confirming the question leaves the answer outstanding.
    let receipt = InboxItem {
        source: InboxSource::Request,
        content: vec![],
        from: Some("ep_lead".into()),
        message_id: Some(question),
    };
    team.observe("ep_b", &event(9, EventData::InboxItem(receipt)));
    let state = team.state();
    assert_eq!(state.queue.len(), 2);
    assert_eq!(state.undelivered().map(|m| m.to.as_str()).collect::<Vec<_>>(), ["ep_lead"]);
}

/// docs/config.md `tools`: the eight team tools are built in. A root answers
/// `send` and `team` from its own roster and has no parent to notify.
#[tokio::test]
async fn a_root_serves_send_and_team_from_its_own_roster() {
    let (team, log, _, _) = team();
    let names: Vec<String> = tools(team.clone(), None).iter().map(|t| t.spec().name.clone()).collect();
    assert_eq!(names, ["spawn", "wait", "steer", "cancel", "notify", "send", "ask", "team"]);
    assert_eq!(builtin_specs().iter().map(|s| s.name.as_str()).collect::<Vec<_>>(), names);
    let send = builtin_specs().into_iter().find(|s| s.name == "send").expect("`send` is built in");
    assert_eq!(send.effect, Effect::Pure, "`send` needs no grant");
    let tools = tools(team, None);
    let by_name = |name: &str| tools.iter().find(|t| t.spec().name == name).unwrap();
    assert!(by_name("notify").call(serde_json::json!({ "content": "x" }), &ctx(None)).await.is_error);
    log.append(EventData::TeamRoster {
        member_id: "ep_b".into(),
        name: "tester".into(),
        description: String::new(),
        phase: MemberPhase::Active,
    });
    let sent = by_name("send").call(serde_json::json!({ "to": "tester", "content": "go" }), &ctx(None)).await;
    assert!(!sent.is_error, "{:?}", sent.rendered);
    let EventData::TeamMessage { from, to, .. } = &log.events()[1].data else { panic!() };
    assert_eq!((from.as_str(), to.as_str()), ("ep_lead", "ep_b"));
    let roster = by_name("team").call(serde_json::json!({}), &ctx(None)).await;
    assert_eq!(roster.rendered.as_deref(), Some("members:\ntester\tep_b\tactive\ntasks:\n"));
}

fn ctx(spawner: Option<Arc<dyn Spawner>>) -> CallCtx {
    CallCtx {
        call_id: "tc".into(),
        step: 1,
        reader: None,
        writer: None,
        executor: None,
        spawner,
        sessions: None,
        composer: None,
        spill_dir: PathBuf::new(),
        deadline: None,
    }
}

use std::path::PathBuf;

fn wait_tool(team: Arc<Team>) -> Box<dyn Tool> {
    tools(team, None).into_iter().find(|t| t.spec().name == "wait").unwrap()
}

fn ctx_deadline(deadline: std::time::Instant) -> CallCtx {
    CallCtx { deadline: Some(deadline), ..ctx(None) }
}

fn soon() -> std::time::Instant {
    std::time::Instant::now() + Duration::from_millis(60)
}

fn inbox_event(source: InboxSource, from: Option<&str>) -> EventData {
    EventData::InboxItem(InboxItem { source, content: vec![], from: from.map(str::to_string), message_id: None })
}

/// docs/tools.md "wait": the bare form blocks until every team task and
/// child has settled. It returns at once when there is no delegated work.
#[tokio::test]
async fn bare_wait_keeps_its_all_children_meaning() {
    let (team, _, _, _) = team();
    let value = wait_tool(team.clone()).call(serde_json::json!({}), &ctx(None)).await;
    assert_eq!((value.is_error, value.rendered.as_deref()), (false, Some("every team task has settled")));
    assert_eq!(value.value, serde_json::json!({ "pending": 0 }));
    team.pool.lock().unwrap().reserve("ep_a", BudgetAmount::default()).unwrap();
    let out = wait_tool(team.clone()).call(serde_json::json!({}), &ctx_deadline(soon())).await;
    assert!(out.is_error);
    assert!(out.rendered.unwrap_or_default().contains("seconds budget"), "the budget bound keeps its error");
    let timed = wait_tool(team).call(serde_json::json!({ "timeout_seconds": 1 }), &ctx(None)).await;
    assert_eq!((timed.is_error, timed.value), (false, serde_json::json!({ "matched": "timeout" })));
}

/// docs/tools.md "wait": an `until` wait returns when an unconsumed inbox
/// item matches a condition, naming the condition met; an item an earlier
/// request consumed is not news, and nothing matching is a timeout.
#[tokio::test]
async fn wait_until_matches_unconsumed_arrivals_by_source_child_and_session() {
    let (team, log, _, _) = team();
    let wait = |args: serde_json::Value, deadline: Option<std::time::Instant>| {
        let team = team.clone();
        async move {
            let ctx = deadline.map_or_else(|| ctx(None), ctx_deadline);
            wait_tool(team).call(args, &ctx).await
        }
    };
    let until = |c: serde_json::Value| serde_json::json!({ "until": [c] });
    // An arrival by source, landing while the wait blocks.
    let appender = log.clone();
    let landed = tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(60)).await;
        appender.append(inbox_event(InboxSource::Child, Some("ep_a")));
    });
    let value = wait(until(serde_json::json!({ "inbox": "child" })), None).await;
    landed.await.unwrap();
    assert_eq!(value.value, serde_json::json!({ "matched": { "inbox": "child" } }), "{:?}", value.rendered);
    // A child reaching an outcome: the ended report plus its spawn/end.
    assert_eq!(
        wait(until(serde_json::json!({ "child": "ep_a" })), Some(soon())).await.value["matched"],
        serde_json::json!("timeout"),
        "a child item without a recorded outcome is not an outcome"
    );
    log.append(EventData::SpawnEnd {
        child_id: "ep_a".into(),
        outcome: Outcome::Completed { value: serde_json::Value::Null },
    });
    let by_id = wait(until(serde_json::json!({ "child": "ep_a" })), None).await;
    assert_eq!(by_id.value["matched"], serde_json::json!({ "child": "ep_a" }));
    let by_kind = wait(until(serde_json::json!({ "child": "any", "outcome": "completed" })), None).await;
    assert_eq!(by_kind.value["matched"], serde_json::json!({ "child": "any", "outcome": "completed" }));
    let wrong_kind = wait(until(serde_json::json!({ "child": "ep_a", "outcome": "failed" })), Some(soon())).await;
    assert_eq!(wrong_kind.value["matched"], serde_json::json!("timeout"));
    // A session exit, matched by id or by `any` through the session item.
    log.append(inbox_event(InboxSource::Session, Some("3")));
    assert_eq!(
        wait(until(serde_json::json!({ "session": 3 })), None).await.value["matched"],
        serde_json::json!({ "session": 3 })
    );
    assert_eq!(
        wait(until(serde_json::json!({ "session": "any" })), None).await.value["matched"],
        serde_json::json!({ "session": "any" })
    );
    assert_eq!(
        wait(until(serde_json::json!({ "session": 7 })), Some(soon())).await.value["matched"],
        serde_json::json!("timeout")
    );
    let invalid = wait(until(serde_json::json!({ "session": "x" })), None).await;
    assert!(invalid.is_error, "{:?}", invalid.rendered);
    // Consumption ends an arrival's news: the same conditions time out once
    // a request lists every item in `consumed`.
    let consumed: Vec<u64> =
        log.events().iter().filter(|e| matches!(e.data, EventData::InboxItem(_))).map(|e| e.seq).collect();
    log.append(EventData::ModelRequest(foe_core::log::ModelRequest {
        step: 1,
        attempt: 1,
        request_id: "rq_01".into(),
        header_seq: 0,
        consumed,
        messages: vec![],
        max_output_tokens: None,
    }));
    let stale = wait(until(serde_json::json!({ "child": "ep_a" })), Some(soon())).await;
    assert_eq!(stale.value["matched"], serde_json::json!("timeout"), "a consumed arrival is not news");
}

/// docs/config.md "JSON Schema subset": dispatch checks a call against the
/// tool's parameter schema, so a schema the runtime writes stays inside the
/// subset the runtime evaluates.
#[test]
fn every_team_tool_schema_stays_inside_the_implemented_subset() {
    for spec in super::builtin_specs() {
        foe_contract::schema::check(format!("tools.{}.params", spec.name), &spec.params).unwrap();
    }
}

/// docs/design.md "Agent teams": a lead stops one running member by roster
/// name. A member no longer running is not an error: the lead asked for a
/// state that already holds.
#[test]
fn cancel_names_a_member_and_is_quiet_about_one_no_longer_running() {
    let (team, log, _inbox, _router) = team();
    log.append(start());
    log.append(EventData::TeamRoster {
        member_id: "ep_worker".into(),
        name: "worker".into(),
        description: "audit one unit".into(),
        phase: MemberPhase::Active,
    });

    // The router holds no child, which is a member that has already settled.
    assert!(team.cancel("worker", "the answer is already in hand").is_ok());
    let missing = team.cancel("absent", "no such member").unwrap_err();
    assert!(missing.to_string().contains("no member named absent"), "{missing}");
}

/// docs/design.md "Agent teams": a lead partitions by granting each worker a
/// write root of its own, and the board refuses a root that overlaps one a
/// live task still holds. Containment either way is an overlap.
#[test]
fn a_write_root_may_not_overlap_one_a_live_task_holds() {
    /// Every launch is refused for capacity, which leaves each task queued
    /// and therefore live: the overlap this test is about is between tasks
    /// that have not settled, running or not.
    struct AtCapacity;
    impl Spawner for AtCapacity {
        fn allocate_id(&self) -> String {
            "ep_child".into()
        }
        fn launch(&self, _: String, _: SpawnRequest) -> Result<foe_core::SpawnHandle, CapError> {
            Err(CapError::Budget { limit: foe_log::ExhaustedLimit::Concurrency, name: "max_concurrent".into() })
        }
    }
    let (team, log, _inbox, _router) = team();
    log.append(start());
    let spawner: Arc<dyn Spawner> = Arc::new(AtCapacity);
    let roots = |paths: &[&str]| paths.iter().map(|p| (*p).to_string()).collect::<Vec<_>>();
    let add = |name: &str, write: Vec<String>| {
        let req = SpawnRequest {
            contract: "worker".into(),
            task: "audit".into(),
            context: SpawnContext::Fresh,
            reserve: BudgetAmount::default(),
            write: None,
            call_id: "tc".into(),
        };
        team.delegate(spawner.clone(), req, Some(name), Vec::new(), write)
    };

    add("a", roots(&["/p/crates/log"])).unwrap();
    for wanted in [vec!["/p/crates/log"], vec!["/p/crates"], vec!["/p/crates/log/src"]] {
        let refused = add("b", roots(&wanted)).unwrap_err();
        assert!(refused.to_string().contains("still writing"), "{wanted:?}: {refused}");
    }

    // A neighbour is not an overlap, and neither is a name that only shares
    // a prefix of characters.
    add("c", roots(&["/p/crates/core"])).unwrap();
    add("d", roots(&["/p/crates/log-other"])).unwrap();
}

/// The reading a wait does must not copy the log. `wait` folds the events
/// fifty times a second for as long as it waits, and the fold keeps nothing
/// of the copy; a run's log grows, so a copy per tick grows with it.
#[tokio::test]
async fn waiting_reads_the_log_in_place_and_never_copies_it() {
    #[derive(Default)]
    struct Counted {
        events: Mutex<Vec<Event>>,
        copies: AtomicUsize,
        reads: AtomicUsize,
    }
    impl LeadLog for Counted {
        fn append(&self, data: EventData) -> Result<(), CapError> {
            let mut events = self.events.lock().unwrap();
            let seq = events.len() as u64;
            events.push(Event { seq, time: 0, version: None, data });
            Ok(())
        }
        fn check(&self) -> Result<(), CapError> {
            Ok(())
        }
        fn events(&self) -> Vec<Event> {
            self.copies.fetch_add(1, Ordering::SeqCst);
            self.events.lock().unwrap().clone()
        }
        fn with_events(&self, read: &mut dyn FnMut(&[Event])) {
            self.reads.fetch_add(1, Ordering::SeqCst);
            read(&self.events.lock().unwrap());
        }
    }

    let log = Arc::new(Counted::default());
    LeadLog::append(&*log, start()).unwrap();
    let team = Arc::new(Team::new(
        "ep_lead".into(),
        log.clone(),
        Arc::new(MemInbox::default()),
        Arc::new(Router::new()),
        Arc::new(Mutex::new(Pool::new(budget()))),
    ));
    let _ = team.state();
    // Both wait forms: the bare one folds the board, the `until` one reads
    // the arrivals. Each spins until its deadline, so each ticks many times.
    let bare = wait_tool(team.clone()).call(serde_json::json!({}), &ctx_deadline(soon())).await;
    assert!(!bare.is_error || bare.rendered.is_some());
    let until = serde_json::json!({ "until": [{ "inbox": "child" }] });
    wait_tool(team.clone()).call(until, &ctx_deadline(soon())).await;

    assert_eq!(log.copies.load(Ordering::SeqCst), 0, "nothing copied the log");
    assert!(log.reads.load(Ordering::SeqCst) > 1, "and the reads happened");
}
