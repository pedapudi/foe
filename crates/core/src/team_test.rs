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

#[test]
fn duplicate_peer_messages_are_recognized_by_id() {
    let item = InboxItem {
        source: InboxSource::Peer,
        content: vec![],
        from: Some("ep_a".into()),
        message_id: Some("tm_07".into()),
    };
    let events = [event(1, EventData::InboxItem(item))];
    assert!(is_duplicate(&events, "tm_07"));
    assert!(!is_duplicate(&events, "tm_08"));
}

fn team() -> (Arc<Team>, Arc<MemLog>, Arc<MemInbox>, Arc<Router>) {
    let log = Arc::new(MemLog::default());
    let inbox = Arc::new(MemInbox::default());
    let router = Arc::new(Router::new());
    let team = Arc::new(Team::new("ep_lead".into(), log.clone(), inbox.clone(), router.clone()));
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

#[test]
fn built_ins_and_host_tool_defs_partition_the_five_tools() {
    let (team, _, _, _) = team();
    let names: Vec<String> = tools(team).iter().map(|t| t.spec().name.clone()).collect();
    assert_eq!(names, ["spawn", "steer"]);
    let defs = host_tool_defs();
    let hosted: Vec<&String> = defs.keys().collect();
    assert_eq!(hosted, ["notify", "send", "team"]);
    assert_eq!(defs["send"].effect, Effect::Pure);
}

fn ctx(spawner: Option<Arc<dyn Spawner>>) -> CallCtx {
    CallCtx {
        call_id: "tc".into(),
        step: 1,
        reader: None,
        writer: None,
        executor: None,
        spawner,
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
    let tools = tools(team.clone());
    let spawn = tools.iter().find(|t| t.spec().name == "spawn").unwrap();
    let args = serde_json::json!({ "program": "worker", "task": "do it", "name": "w1" });
    let value = spawn.call(args.clone(), &ctx(Some(spawner.clone()))).await;
    assert!(!value.is_error, "{:?}", value.rendered);
    let child_id = value.value["child_id"].as_str().unwrap().to_string();
    assert!(spawn.call(args, &ctx(Some(spawner.clone()))).await.is_error, "roster names are unique");
    let config: crate::Config =
        serde_json::from_slice(&std::fs::read(dir.join("children").join(&child_id).join("config.json")).unwrap())
            .unwrap();
    assert!(config.host_tools.contains_key("notify"), "the child resolves notify as a host tool");

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
