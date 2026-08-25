use super::*;
use crate::exec::tests::scratch;
use crate::spawn::tests::{fake_child, parent_config, wait_for, Lines};
use crate::spawn::ProcessSpawner;

#[derive(Default)]
struct MemLog(Mutex<Vec<Event>>);

impl LeadLog for MemLog {
    fn append(&self, data: EventData) {
        let mut events = self.0.lock().unwrap();
        let seq = events.len() as u64;
        events.push(Event { seq, time: 0, data });
    }
    fn events(&self) -> Vec<Event> {
        self.0.lock().unwrap().clone()
    }
}

#[derive(Default)]
struct MemInbox(Mutex<Vec<InboxItem>>);

impl InboxSink for MemInbox {
    fn append(&self, item: InboxItem) {
        self.0.lock().unwrap().push(item);
    }
}

fn event(seq: u64, data: EventData) -> Event {
    Event { seq, time: 0, data }
}

fn roster(seq: u64, id: &str, name: &str, phase: MemberPhase) -> Event {
    event(seq, EventData::TeamRoster { member_id: id.into(), name: name.into(), description: String::new(), phase })
}

fn message(seq: u64, id: &str, to: &str) -> Event {
    event(seq, EventData::TeamMessage { message_id: id.into(), from: "ep_a".into(), to: to.into(), content: vec![] })
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
        event(3, EventData::SeedEnd {}),
        roster(4, "ep_c", "writer", MemberPhase::Active),
    ];
    let state = fold(&events);
    assert_eq!(state.roster.len(), 1);
    assert_eq!(state.roster[0].name, "writer");
    assert!(state.queue.is_empty());
}

fn team() -> (Arc<Team>, Arc<MemLog>, Arc<MemInbox>, Arc<Router>) {
    let log = Arc::new(MemLog::default());
    let inbox = Arc::new(MemInbox::default());
    let router = Arc::new(Router::new());
    let pool = Arc::new(Mutex::new(Pool::new(crate::budget::tests::budget())));
    let team = Arc::new(Team::new("ep_lead".into(), log.clone(), inbox.clone(), router.clone(), pool));
    (team, log, inbox, router)
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
    assert_eq!(listed.rendered.as_deref(), Some("tester\tep_b\tactive"));
}

/// docs/config.md `tools`: the six team tools are built in. A root answers
/// `send` and `team` from its own roster and has no parent to notify.
#[tokio::test]
async fn a_root_serves_send_and_team_from_its_own_roster() {
    let (team, log, _, _) = team();
    let names: Vec<String> = tools(team.clone(), None).iter().map(|t| t.spec().name.clone()).collect();
    assert_eq!(names, ["spawn", "wait", "steer", "notify", "send", "team"]);
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
    assert_eq!(roster.rendered.as_deref(), Some("tester\tep_b\tactive"));
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

#[tokio::test]
async fn spawn_tool_runs_a_child_whose_notify_and_end_reach_the_lead() {
    let dir = scratch("team", "spawn");
    let (team, log, inbox, router) = team();
    let uplink = Arc::new(Lines::default());
    let spawner: Arc<dyn Spawner> = Arc::new(
        ProcessSpawner::new(
            "ep_lead".into(),
            dir.clone(),
            parent_config(),
            uplink.clone(),
            router.clone(),
            team.clone(),
        )
        .unwrap()
        .with_launcher(fake_child(&dir)),
    );
    let tools = tools(team.clone(), None);
    let spawn = tools.iter().find(|t| t.spec().name == "spawn").unwrap();
    let args = serde_json::json!({ "program": "worker", "task": "do it", "name": "w1" });
    let value = spawn.call(args.clone(), &ctx(Some(spawner.clone()))).await;
    assert!(!value.is_error, "{:?}", value.rendered);
    let child_id = value.value["child_id"].as_str().unwrap().to_string();
    assert!(spawn.call(args, &ctx(Some(spawner.clone()))).await.is_error, "roster names are unique");
    let config: foe_config::Config =
        serde_json::from_slice(&std::fs::read(dir.join("children").join(&child_id).join("config.json")).unwrap())
            .unwrap();
    assert_eq!(config.tools, ["notify"], "the child's tools are the program's; notify is built in");

    wait_for(|| (uplink.0.lock().unwrap().len() == 2).then_some(()));
    let steer = tools.iter().find(|t| t.spec().name == "steer").unwrap();
    let steered = steer.call(serde_json::json!({ "to": "w1", "content": "\"go\"" }), &ctx(None)).await;
    assert!(!steered.is_error, "{:?}", steered.rendered);
    let items = wait_for(|| {
        let items = inbox.0.lock().unwrap();
        (items.len() == 2).then(|| items.clone())
    });
    assert_eq!(items[0].content, text_content("progress"), "notify was answered by the lead");
    assert_eq!(items[0].from.as_deref(), Some(&*child_id));
    let ended = format!("w1 ({child_id}) ended: completed with ");
    let ContentBlock::Text { text } = &items[1].content[0] else { panic!() };
    assert!(text.starts_with(&ended), "{text}");
    assert!(text.contains(r#""source":"parent""#), "the steer reached the child: {text}");
    assert!(text.contains(r#""type":"tool/result""#), "the notify result reached the child: {text}");
    let phases: Vec<MemberPhase> = log
        .events()
        .iter()
        .filter_map(|e| match &e.data {
            EventData::TeamRoster { phase, .. } => Some(*phase),
            _ => None,
        })
        .collect();
    assert_eq!(phases, [MemberPhase::Provisioning, MemberPhase::Active]);
    assert_eq!(team.state().member("w1").unwrap().member_id, child_id);
    assert!(uplink.0.lock().unwrap().len() == 2, "notify was never forwarded upward");
}

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

/// docs/tools.md "wait": the bare form blocks until every child has ended
/// and returns at once when no child is running; when the seconds budget
/// runs out first, the result is an error naming the children still running.
#[tokio::test]
async fn bare_wait_keeps_its_all_children_meaning() {
    let (team, _, _, _) = team();
    let value = wait_tool(team.clone()).call(serde_json::json!({}), &ctx(None)).await;
    assert_eq!((value.is_error, value.rendered.as_deref()), (false, Some("every child has ended")));
    assert_eq!(value.value, serde_json::json!({ "running": 0 }));
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
    log.append(EventData::ModelRequest(foe_log::ModelRequest {
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
        foe_config::schema::check(format!("tools.{}.params", spec.name), &spec.params).unwrap();
    }
}
