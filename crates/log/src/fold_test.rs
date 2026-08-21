use crate::fold::{derive_messages, fold, read_all, read_from, validate_next};
use crate::*;
use std::path::PathBuf;

fn tmp(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("foe-log-fold-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

pub fn start(id: &str) -> EpisodeStart {
    EpisodeStart {
        id: id.into(),
        parent_id: None,
        fork_origin: None,
        team_id: None,
        program: serde_json::json!({ "name": "p" }),
        identity: "sha256:0".into(),
        task: "do it".into(),
        runtime: RuntimeInfo { version: "0.1.0".into(), build: "unknown".into() },
        sandbox: SandboxInfo { mode: SandboxMode::Off, landlock_abi: 0 },
    }
}

pub fn text(s: &str) -> Vec<ContentBlock> {
    vec![ContentBlock::Text { text: s.into() }]
}

pub fn inbox(source: InboxSource, s: &str) -> EventData {
    EventData::InboxItem(InboxItem { source, content: text(s), from: None, message_id: None })
}

pub fn header() -> EventData {
    EventData::RequestHeader(RequestHeader {
        reason: HeaderReason::Initial,
        system: "sys".into(),
        tools: vec![],
        model: ModelRoute { provider: "p".into(), model: "m".into() },
    })
}

pub fn request(step: u32, header_seq: u64, consumed: Vec<u64>, messages: Vec<Message>) -> EventData {
    EventData::ModelRequest(ModelRequest {
        step,
        attempt: 1,
        request_id: format!("rq_{step}"),
        header_seq,
        consumed,
        messages,
    })
}

pub fn assistant(step: u32, text: &str, calls: Vec<ToolCall>, interrupted: bool) -> EventData {
    EventData::AssistantMessage(AssistantMessage {
        step,
        request_id: format!("rq_{step}"),
        text: text.into(),
        tool_calls: calls,
        stop: if interrupted { StopReason::Interrupted } else { StopReason::Tool },
        usage: Usage { input: 10, output: 5, cache_read: 0 },
        interrupted,
        thinking: vec![],
    })
}

pub fn result(step: u32, call_id: &str, rendered: &str) -> EventData {
    EventData::ToolResult(ToolResult {
        step,
        call_id: call_id.into(),
        name: "read".into(),
        value: serde_json::json!({ "ok": true }),
        rendered: rendered.into(),
        is_error: false,
        spill: None,
        duration_ms: 1,
        synthetic: false,
    })
}

pub fn call(id: &str) -> ToolCall {
    ToolCall { id: id.into(), name: "read".into(), args: serde_json::json!({ "path": "a" }) }
}

pub fn number(datas: Vec<EventData>) -> Vec<Event> {
    datas.into_iter().enumerate().map(|(i, data)| Event { seq: i as u64, time: 1000 + i as i64, data }).collect()
}

/// A hand-written fixture: task, a step with a tool call, a steer that
/// arrives while that step's request is in flight, a second steer after
/// it, and an interrupted second step. Covers every derivation rule.
fn fixture() -> Vec<Event> {
    number(vec![
        EventData::EpisodeStart(start("ep_1")),
        inbox(InboxSource::Task, "fix it"),
        header(),
        request(1, 2, vec![1], vec![]),
        inbox(InboxSource::Parent, "hurry"),
        assistant(1, "reading", vec![call("tc_1")], false),
        result(1, "tc_1", "file body"),
        inbox(InboxSource::Peer, "also this"),
        request(2, 2, vec![4, 7], vec![]),
        assistant(2, "partial", vec![call("tc_2")], true),
        result(2, "tc_2", "not recorded"),
    ])
}

#[test]
fn derived_messages_follow_the_rule_against_the_fixture() {
    let events = fixture();
    // Rule 3 and 7: the request at seq 3 sees only the task, as a user message.
    let first = derive_messages(&events, 3, &[1]);
    assert_eq!(first, vec![Message::User { content: text("fix it") }]);
    // Rules 3-6 for the second request: the steer that arrived during the
    // first request (seq 4) is placed where it was consumed, after the
    // assistant message and the tool result, merged with the later steer.
    let second = derive_messages(&events, 8, &[4, 7]);
    assert_eq!(
        second,
        vec![
            Message::User { content: text("fix it") },
            Message::Assistant { text: "reading".into(), tool_calls: vec![call("tc_1")], thinking: vec![] },
            Message::Tool {
                call_id: "tc_1".into(),
                name: "read".into(),
                rendered: "file body".into(),
                is_error: false
            },
            Message::User {
                content: vec![
                    ContentBlock::Text { text: "hurry".into() },
                    ContentBlock::Text { text: "also this".into() }
                ]
            },
        ]
    );
    // Rule 5: an interrupted message keeps its text and recorded calls. The
    // recorded request at seq 8 contributes the same user message.
    let third = derive_messages(&events, 11, &[]);
    assert_eq!(third[..4], second[..4]);
    assert_eq!(
        third[4],
        Message::Assistant { text: "partial".into(), tool_calls: vec![call("tc_2")], thinking: vec![] }
    );
    assert_eq!(third.len(), 6);
}

#[test]
fn unconsumed_inbox_items_are_excluded() {
    let events = fixture();
    let messages = derive_messages(&events, 8, &[]);
    assert_eq!(messages.len(), 3);
    assert!(!messages.iter().any(|m| matches!(m, Message::User { content } if content == &text("hurry"))));
}

#[test]
fn consumed_names_earlier_unconsumed_items_only() {
    let state = fold(&fixture()[..5]).unwrap();
    let forward = Event { seq: 5, time: 0, data: request(2, 2, vec![5], vec![]) };
    assert!(validate_next(&state, &forward).is_err());
    let fresh = Event { seq: 5, time: 0, data: request(2, 2, vec![4], vec![]) };
    assert!(validate_next(&state, &fresh).is_ok());
}

#[test]
fn fold_requires_seq_contiguous_from_zero() {
    let mut events = fixture();
    events[4].seq = 9;
    let err = fold(&events).unwrap_err();
    assert!(matches!(err, LogError::Invalid { seq: 9, rule } if rule.contains("contiguous")));
}

#[test]
fn fold_fills_state() {
    let state = fold(&fixture()).unwrap();
    assert_eq!(state.start.unwrap().id, "ep_1");
    assert_eq!(state.header_seq, Some(2));
    assert_eq!(state.model_calls, 2);
    assert_eq!(state.usage, Usage { input: 20, output: 10, cache_read: 0 });
    assert!(state.inbox.values().all(|(_, consumed)| *consumed));
    assert!(state.outcome.is_none());
}

#[test]
fn episode_start_is_first_and_only_at_seq_zero() {
    let events = number(vec![inbox(InboxSource::Task, "t")]);
    assert!(matches!(fold(&events), Err(LogError::Invalid { seq: 0, .. })));
    let events = number(vec![EventData::EpisodeStart(start("a")), EventData::EpisodeStart(start("b"))]);
    assert!(matches!(fold(&events), Err(LogError::Invalid { seq: 1, .. })));
}

#[test]
fn episode_end_is_last() {
    let end = EventData::EpisodeEnd { outcome: Outcome::Completed { value: serde_json::Value::Null } };
    let mut events = fixture();
    events.push(Event { seq: 11, time: 0, data: end.clone() });
    fold(&events).unwrap();
    events.push(Event { seq: 12, time: 0, data: inbox(InboxSource::Parent, "late") });
    assert!(matches!(fold(&events), Err(LogError::Invalid { seq: 12, rule }) if rule.contains("episode/end")));
    let state = fold(&events[..11]).unwrap();
    assert!(validate_next(&state, &events[11]).is_ok());
}

#[test]
fn ended_log_gives_every_call_a_result() {
    let mut events = fixture();
    events.truncate(10);
    events.push(Event {
        seq: 10,
        time: 0,
        data: EventData::EpisodeEnd { outcome: Outcome::Failed { error: "x".into() } },
    });
    assert!(matches!(fold(&events), Err(LogError::Invalid { rule, .. }) if rule.contains("every tool call")));
}

#[test]
fn tool_result_needs_a_call_and_only_one() {
    let mut events = fixture();
    events.push(Event { seq: 11, time: 0, data: result(2, "tc_2", "again") });
    assert!(matches!(fold(&events), Err(LogError::Invalid { seq: 11, .. })));
    events[11].data = result(2, "tc_9", "orphan");
    assert!(matches!(fold(&events), Err(LogError::Invalid { seq: 11, .. })));
}

#[test]
fn request_must_name_current_header_and_fresh_inbox() {
    let state = fold(&fixture()[..4]).unwrap();
    let stale = Event { seq: 4, time: 0, data: request(2, 0, vec![], vec![]) };
    assert!(validate_next(&state, &stale).is_err());
    let reused = Event { seq: 4, time: 0, data: request(2, 2, vec![1], vec![]) };
    assert!(validate_next(&state, &reused).is_err());
}

#[test]
fn read_from_tails_complete_lines_only() {
    let dir = tmp("tail");
    let path = dir.join("episode.jsonl");
    let events = fixture();
    let line = |e: &Event| serde_json::to_string(e).unwrap() + "\n";
    std::fs::write(&path, line(&events[0]) + &line(&events[1])).unwrap();
    let (first, offset) = read_from(&dir, 0).unwrap();
    assert_eq!(first.len(), 2);
    let partial = line(&events[2]);
    let (head, tail) = partial.split_at(partial.len() / 2);
    std::fs::write(&path, line(&events[0]) + &line(&events[1]) + head).unwrap();
    let (none, same) = read_from(&dir, offset).unwrap();
    assert!(none.is_empty());
    assert_eq!(same, offset);
    std::fs::write(&path, line(&events[0]) + &line(&events[1]) + head + tail).unwrap();
    let (rest, end) = read_from(&dir, same).unwrap();
    assert_eq!(rest, vec![events[2].clone()]);
    assert_eq!(end as usize, (line(&events[0]) + &line(&events[1]) + &partial).len());
    assert_eq!(read_all(&dir).unwrap().len(), 3);
}

#[test]
fn missing_log_is_not_found() {
    let dir = tmp("missing");
    match read_from(&dir, 0) {
        Err(LogError::Io(e)) => assert_eq!(e.kind(), std::io::ErrorKind::NotFound),
        other => panic!("{other:?}"),
    }
}

#[test]
fn every_event_variant_round_trips() {
    let outcome = Outcome::Blocked { code: BlockedCode::LoopingToolCall, message: "m".into() };
    let reserved = serde_json::json!({ "any": [1, "two", null] });
    let variants = vec![
        EventData::EpisodeStart(start("ep")),
        EventData::EpisodeEnd { outcome: outcome.clone() },
        EventData::SeedEnd {},
        header(),
        request(1, 0, vec![1], vec![Message::User { content: text("x") }]),
        EventData::RequestRetry { step: 1, attempt: 2, cause: RetryCause::RateLimit, delay_ms: 5 },
        EventData::AssistantChunk {
            step: 1,
            request_id: "rq".into(),
            chunk: Chunk::ToolCallDelta { id: "t".into(), delta: "{".into() },
        },
        EventData::AssistantChunk {
            step: 1,
            request_id: "rq".into(),
            chunk: Chunk::Done { stop: StopReason::Length, usage: Usage::default() },
        },
        EventData::AssistantChunk {
            step: 1,
            request_id: "rq".into(),
            chunk: Chunk::Error { message: "e".into(), retryable: true },
        },
        EventData::AssistantChunk {
            step: 1,
            request_id: "rq".into(),
            chunk: Chunk::ThinkingSignature { signature: "sig".into() },
        },
        {
            let EventData::AssistantMessage(mut m) = assistant(1, "t", vec![call("c")], false) else { unreachable!() };
            m.thinking = vec![
                ThinkingBlock { text: "hmm".into(), signature: Some("sig".into()) },
                ThinkingBlock { text: "more".into(), signature: None },
            ];
            EventData::AssistantMessage(m)
        },
        result(1, "c", "r"),
        EventData::HostToolCall { step: 1, call_id: "c".into(), name: "h".into(), args: reserved.clone() },
        inbox(InboxSource::Request, "reserved source"),
        EventData::BudgetReserve {
            child_id: "k".into(),
            reserved: BudgetAmount { model_calls: Some(1), tokens: None, seconds: Some(3) },
        },
        EventData::BudgetRelease { child_id: "k".into(), spent: BudgetAmount::default() },
        EventData::SpawnStart {
            child_id: "k".into(),
            program: "p".into(),
            context: SpawnContext::Fork,
            call_id: "c".into(),
        },
        EventData::SpawnEnd {
            child_id: "k".into(),
            outcome: Outcome::Exhausted { limit: ExhaustedLimit::Concurrency },
        },
        EventData::TeamRoster {
            member_id: "m".into(),
            name: "n".into(),
            description: "d".into(),
            phase: MemberPhase::Provisioning,
        },
        EventData::TeamMessage { message_id: "tm".into(), from: "a".into(), to: "b".into(), content: text("hi") },
        EventData::TeamDelivered { message_id: "tm".into(), to: "b".into() },
        EventData::TeamTask(reserved.clone()),
        EventData::SandboxDenied { pid: 1, comm: "c".into(), path: "/p".into(), access: "read".into() },
        EventData::CompactionStart(reserved.clone()),
        EventData::CompactionSummary(reserved.clone()),
        EventData::CompactionEnd(reserved.clone()),
        EventData::WorkflowNodeStart(reserved.clone()),
        EventData::WorkflowNodeEnd(reserved.clone()),
        EventData::WorkflowRecovery(reserved),
    ];
    let mut seen = std::collections::BTreeSet::new();
    for data in variants {
        let name = data.type_name();
        seen.insert(name);
        let event = Event { seq: 0, time: 1, data };
        let line = serde_json::to_string(&event).unwrap();
        assert!(line.contains(&format!("\"type\":\"{name}\"")), "{line}");
        let back: Event = serde_json::from_str(&line).unwrap();
        assert_eq!(back, event);
        assert_eq!(serde_json::to_string(&back).unwrap(), line);
    }
    assert_eq!(seen.len(), 26, "one of each event type, reserved ones included");
}
