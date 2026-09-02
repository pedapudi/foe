use crate::fold::{derive_messages, fold, read_all, read_from, render_continuation, validate_next};
use crate::*;
use std::ops::Deref;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT_SCRATCH: AtomicU64 = AtomicU64::new(0);

pub struct ScratchDir(PathBuf);

impl Deref for ScratchDir {
    type Target = Path;

    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl AsRef<Path> for ScratchDir {
    fn as_ref(&self) -> &Path {
        &self.0
    }
}

impl Drop for ScratchDir {
    fn drop(&mut self) {
        if std::thread::panicking() {
            eprintln!("retained failed test directory: {}", self.0.display());
            return;
        }
        match std::fs::symlink_metadata(&self.0) {
            Ok(metadata) if metadata.file_type().is_dir() => std::fs::remove_dir_all(&self.0).unwrap(),
            Ok(_) => std::fs::remove_file(&self.0).unwrap(),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => panic!("failed to inspect {} for cleanup: {error}", self.0.display()),
        }
    }
}

pub fn tmp(name: &str) -> ScratchDir {
    assert_eq!(Path::new(name).file_name(), Some(name.as_ref()), "scratch name must be one path component");
    let parent = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../target/test-scratch");
    std::fs::create_dir_all(&parent).unwrap();
    loop {
        let ordinal = NEXT_SCRATCH.fetch_add(1, Ordering::Relaxed);
        let path = parent.join(format!("foe-log-{}-{ordinal}-{name}", std::process::id()));
        match std::fs::create_dir(&path) {
            Ok(()) => return ScratchDir(path),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => panic!("failed to create {}: {error}", path.display()),
        }
    }
}

pub fn start(id: &str) -> EpisodeStart {
    EpisodeStart {
        id: id.into(),
        parent_id: None,
        fork_origin: None,
        team_id: None,
        contract: serde_json::json!({ "name": "p" }),
        contract_fingerprint: "sha256:0".into(),
        task: "do it".into(),
        runtime: RuntimeInfo { version: "0.1.0".into(), build: "unknown".into() },
        sandbox: SandboxInfo {
            mode: SandboxMode::Off,
            landlock_abi: 0,
            resolved_permissions: Default::default(),
            process_boundary: Default::default(),
        },
        effective_budget: None,
    }
}

#[test]
fn episode_start_reads_logs_written_before_effective_budget_evidence() {
    let expected = start("ep");
    let mut value = serde_json::to_value(&expected).unwrap();
    value.as_object_mut().unwrap().remove("effective_budget");
    let decoded: EpisodeStart = serde_json::from_value(value).unwrap();
    assert_eq!(decoded, expected);
}

/// docs/log-format.md "Verification": `candidate_sha256` is an optional
/// additive field. An event carrying it round-trips; a log written before
/// the field existed parses with no association claim.
#[test]
fn verification_result_reads_logs_written_before_candidate_sha256() {
    let mut expected = VerificationResult {
        step: 1,
        tool: "check".into(),
        verifier_fingerprint: format!("sha256:{}", "a".repeat(64)),
        status: VerificationStatus::Accepted,
        findings: Vec::new(),
        error: None,
        candidate_sha256: Some(format!("sha256:{}", "b".repeat(64))),
        duration_ms: 1,
    };
    let line = serde_json::to_string(&expected).unwrap();
    assert!(line.contains("candidate_sha256"), "{line}");
    assert_eq!(serde_json::from_str::<VerificationResult>(&line).unwrap(), expected);
    let mut value = serde_json::to_value(&expected).unwrap();
    value.as_object_mut().unwrap().remove("candidate_sha256");
    let decoded: VerificationResult = serde_json::from_value(value).unwrap();
    expected.candidate_sha256 = None;
    assert_eq!(decoded, expected);
    let absent = serde_json::to_string(&decoded).unwrap();
    assert!(!absent.contains("candidate_sha256"), "absence serializes as absence: {absent}");
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
        max_output_tokens: None,
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
        failure: None,
        spill: None,
        subject: None,
        duration_ms: 1,
        synthetic: false,
    })
}

pub fn call(id: &str) -> ToolCall {
    ToolCall { id: id.into(), name: "read".into(), args: serde_json::json!({ "path": "a" }) }
}

pub fn inner_call(outer: &str, index: u32) -> EventData {
    EventData::ToolInnerCall(ToolInnerCall {
        outer_call_id: outer.into(),
        call_id: format!("{outer}_{index}"),
        index,
        name: "read".into(),
        args: serde_json::json!({ "path": "a" }),
    })
}

pub fn number(datas: Vec<EventData>) -> Vec<Event> {
    datas
        .into_iter()
        .enumerate()
        .map(|(i, data)| Event { seq: i as u64, time: 1000 + i as i64, version: None, data })
        .collect()
}

/// A summarization request and its response, as the loop records them.
fn summary_request(step: u32, header_seq: u64) -> EventData {
    EventData::ModelRequest(ModelRequest {
        step,
        attempt: 1,
        request_id: format!("{SUMMARY_REQUEST_PREFIX}{step}"),
        header_seq,
        consumed: vec![],
        messages: vec![Message::User { content: text("transcript") }],
        max_output_tokens: None,
    })
}

fn summary_response(step: u32, summary: &str) -> EventData {
    let EventData::AssistantMessage(message) = assistant(step, summary, vec![], false) else { unreachable!() };
    EventData::AssistantMessage(AssistantMessage {
        request_id: format!("{SUMMARY_REQUEST_PREFIX}{step}"),
        stop: StopReason::End,
        ..message
    })
}

fn compaction_summary(
    step: u32,
    first_kept_seq: u64,
    summary_request_seq: u64,
    summary: &str,
    read: &[&str],
) -> EventData {
    let files = CompactedFiles { read: read.iter().map(|s| s.to_string()).collect(), written: vec![], edited: vec![] };
    EventData::CompactionSummary(CompactionSummary {
        step,
        summary: summary.into(),
        state: ContinuationState {
            task: "fix it".into(),
            done_when: "a turn with no tool calls".into(),
            outstanding_findings: vec![],
            files,
            children: vec![],
            covered: Covered { first_seq: 1, last_seq: first_kept_seq - 1 },
            budget_remaining: BudgetAmount { model_calls: Some(4), ..Default::default() },
        },
        first_kept_seq,
        summary_request_seq,
    })
}

/// The base fixture continued through two compactions: one at step 3
/// that keeps from the second request (seq 8), one at step 4 that keeps
/// from the third request (seq 18). Each compaction is a start event, a
/// header for the summarization request, the request and its response,
/// the summary, the end, and the header restored for the next step.
fn compacted_fixture() -> Vec<Event> {
    let mut datas: Vec<EventData> = fixture().into_iter().map(|e| e.data).collect();
    let start = |step| {
        EventData::CompactionStart(CompactionStart {
            step,
            covered: Covered { first_seq: 1, last_seq: 7 },
            trigger: CompactionTrigger::Threshold,
            projected_tokens: 190_000,
            reserved: BudgetAmount::default(),
        })
    };
    let end =
        |step| EventData::CompactionEnd { step, ok: true, usage: Usage::default(), active_estimate: 50, error: None };
    datas.extend(vec![
        start(3),                                                     // 11
        header(),                                                     // 12
        summary_request(3, 12),                                       // 13
        summary_response(3, "first summary"),                         // 14
        compaction_summary(3, 8, 13, "first summary", &["a"]),        // 15
        end(3),                                                       // 16
        header(),                                                     // 17
        request(3, 17, vec![], vec![]),                               // 18
        assistant(3, "done", vec![], false),                          // 19
        start(4),                                                     // 20
        header(),                                                     // 21
        summary_request(4, 21),                                       // 22
        summary_response(4, "second summary"),                        // 23
        compaction_summary(4, 18, 22, "second summary", &["a", "b"]), // 24
        end(4),                                                       // 25
        header(),                                                     // 26
        request(4, 26, vec![], vec![]),                               // 27
    ]);
    number(datas)
}

/// docs/log-format.md "Derived messages" after a compaction: the task
/// verbatim, the continuation message, then the events from
/// `first_kept_seq` on, with the items a kept request consumed included
/// although they lie before the cut.
#[test]
fn derivation_after_one_compaction_opens_with_task_and_continuation() {
    let events = compacted_fixture();
    fold(&events).expect("the compacted fixture is a well-formed log");
    let messages = derive_messages(&events, 18, &[]);
    let EventData::CompactionSummary(summary) = &events[15].data else { panic!() };
    let continuation = render_continuation(summary);
    assert!(continuation
        .starts_with("## Continuation state\n\ncovered: seq 1 to 7\ndone_when: a turn with no tool calls\n"));
    assert!(continuation.contains("\noutstanding_findings: (none)\nfiles_read:\n- a\nfiles_written: (none)\n"));
    assert!(continuation
        .contains(
            "\nchildren: (none)\nbudget_remaining: model_calls 4, input_tokens unlimited, output_tokens unlimited, seconds unlimited"
        ));
    assert!(continuation.ends_with("\n\n## Summary\n\nfirst summary"));
    assert_eq!(messages[0], Message::User { content: text("fix it") });
    assert_eq!(messages[1], Message::User { content: text(&continuation) });
    assert_eq!(
        messages[2],
        Message::User {
            content: vec![ContentBlock::Text { text: "hurry".into() }, ContentBlock::Text { text: "also this".into() }]
        },
        "the items the kept request at seq 8 consumed enter at its position"
    );
    assert_eq!(
        messages[3],
        Message::Assistant { text: "partial".into(), tool_calls: vec![call("tc_2")], thinking: vec![] }
    );
    assert_eq!(messages.len(), 5, "the task, the continuation, and the three kept messages");
}

#[test]
fn derivation_after_two_compactions_uses_the_latest_summary() {
    let events = compacted_fixture();
    let messages = derive_messages(&events, 27, &[]);
    let EventData::CompactionSummary(summary) = &events[24].data else { panic!() };
    assert_eq!(messages[0], Message::User { content: text("fix it") });
    assert_eq!(messages[1], Message::User { content: text(&render_continuation(summary)) });
    assert_eq!(messages[2], Message::Assistant { text: "done".into(), tool_calls: vec![], thinking: vec![] });
    assert_eq!(messages.len(), 3);
    assert!(render_continuation(summary).contains("files_read:\n- a\n- b\n"));
}

/// A summarization request and its response contribute nothing, whether
/// the derivation runs before or after the summary they produced.
#[test]
fn the_summarization_request_and_its_response_are_excluded() {
    let events = compacted_fixture();
    assert_eq!(derive_messages(&events, 15, &[]), derive_messages(&events, 11, &[]));
    let later = derive_messages(&events, 20, &[]);
    assert!(!later.iter().any(|m| matches!(m, Message::Assistant { text, .. } if text.contains("summary"))));
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
    let forward = Event { seq: 5, time: 0, version: None, data: request(2, 2, vec![5], vec![]) };
    assert!(validate_next(&state, &forward).is_err());
    let fresh = Event { seq: 5, time: 0, version: None, data: request(2, 2, vec![4], vec![]) };
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
    events.push(Event { seq: 11, time: 0, version: None, data: end.clone() });
    fold(&events).unwrap();
    events.push(Event { seq: 12, time: 0, version: None, data: inbox(InboxSource::Parent, "late") });
    assert!(matches!(fold(&events), Err(LogError::Invalid { seq: 12, rule }) if rule.contains("episode/end")));
    let state = fold(&events[..11]).unwrap();
    assert!(validate_next(&state, &events[11]).is_ok());
}

/// Every pairing docs/log-format.md defines, as the opening event and the
/// closing event that the log owes it.
fn pairings() -> Vec<(EventData, EventData)> {
    let reserved = BudgetAmount { model_calls: Some(2), ..Default::default() };
    vec![
        (assistant(3, "", vec![call("tc_x")], false), result(3, "tc_x", "ok")),
        (
            EventData::RequestRetry { step: 3, attempt: 1, cause: RetryCause::Provider, delay_ms: 500 },
            EventData::ModelRequest(ModelRequest {
                step: 3,
                attempt: 2,
                request_id: "rq_3".into(),
                header_seq: 2,
                consumed: vec![],
                messages: vec![],
                max_output_tokens: None,
            }),
        ),
        (
            EventData::CompactionStart(CompactionStart {
                step: 3,
                covered: Covered { first_seq: 1, last_seq: 5 },
                trigger: CompactionTrigger::Threshold,
                projected_tokens: 100,
                reserved,
            }),
            EventData::CompactionEnd { step: 3, ok: true, usage: Usage::default(), active_estimate: 10, error: None },
        ),
        (
            EventData::SpawnStart {
                child_id: "ep_c".into(),
                contract: "survey".into(),
                context: SpawnContext::Fresh,
                call_id: "tc_s".into(),
            },
            EventData::SpawnEnd {
                child_id: "ep_c".into(),
                outcome: Outcome::Completed { value: serde_json::Value::Null },
            },
        ),
        (
            EventData::BudgetReserve { child_id: "ep_c".into(), reserved },
            EventData::BudgetRelease { child_id: "ep_c".into(), spent: reserved },
        ),
    ]
}

fn ended(seq: u64) -> Event {
    let outcome = Outcome::Failed { error: "x".into() };
    Event { seq, time: 0, version: None, data: EventData::EpisodeEnd { outcome } }
}

#[test]
fn every_obligation_the_log_opened_is_closed_before_episode_end() {
    for (opening, closing) in pairings() {
        let mut events = fixture();
        events.push(Event { seq: 11, time: 0, version: None, data: opening.clone() });
        events.push(ended(12));
        let opened = opening.type_name();
        assert!(
            matches!(fold(&events), Err(LogError::Invalid { rule, .. }) if rule.contains("closed before episode/end")),
            "an episode/end after {opened} with nothing closing it is invalid"
        );
        events[12] = Event { seq: 12, time: 0, version: None, data: closing };
        events.push(ended(13));
        fold(&events).unwrap_or_else(|e| panic!("{opened} closed before episode/end is valid: {e}"));
    }
}

#[test]
fn an_obligation_is_closed_once_and_only_after_it_was_opened() {
    for (opening, closing) in pairings() {
        let mut events = fixture();
        events.push(Event { seq: 11, time: 0, version: None, data: closing.clone() });
        let opened = opening.type_name();
        assert!(
            matches!(fold(&events), Err(LogError::Invalid { rule, .. }) if rule.contains("an earlier event opened")),
            "closing what no {opened} opened is invalid"
        );
        events[11] = Event { seq: 11, time: 0, version: None, data: opening };
        events.push(Event { seq: 12, time: 0, version: None, data: closing.clone() });
        events.push(Event { seq: 13, time: 0, version: None, data: closing });
        assert!(
            matches!(fold(&events), Err(LogError::Invalid { rule, .. }) if rule.contains("closed once")),
            "closing what {opened} opened twice is invalid"
        );
    }
}

/// docs/log-format.md "Open obligations": a `tool/result` with `synthetic`
/// true whose call id names no call is the runtime's account of work it
/// settled itself, such as the implicit stop of a surviving process
/// session. It closes nothing. The same result without `synthetic` stays
/// invalid.
#[test]
fn a_synthetic_result_for_no_call_is_the_runtimes_own_account() {
    let settle = |synthetic| {
        let EventData::ToolResult(mut r) = result(2, "session-1-settle", "session 1: killed after 3s") else {
            unreachable!()
        };
        r.synthetic = synthetic;
        EventData::ToolResult(r)
    };
    let mut events = fixture();
    events.push(Event { seq: 11, time: 0, version: None, data: settle(false) });
    assert!(
        matches!(fold(&events), Err(LogError::Invalid { rule, .. }) if rule.contains("an earlier event opened")),
        "a result closing no call is invalid unless it is synthetic"
    );
    events[11] = Event { seq: 11, time: 0, version: None, data: settle(true) };
    events.push(ended(12));
    fold(&events).expect("a synthetic result closing no call is valid");
}

/// The closed-once rule binds synthetic results too: a call already closed
/// cannot be closed again by the runtime's own account.
#[test]
fn a_synthetic_result_cannot_close_a_call_twice() {
    let EventData::ToolResult(mut r) = result(1, "tc_1", "again") else { unreachable!() };
    r.synthetic = true;
    let mut events = fixture();
    events.push(Event { seq: 11, time: 0, version: None, data: EventData::ToolResult(r) });
    assert!(
        matches!(fold(&events), Err(LogError::Invalid { rule, .. }) if rule.contains("closed once")),
        "a synthetic result naming a closed call is invalid"
    );
}

#[test]
fn a_team_message_may_stand_undelivered_at_episode_end() {
    let mut events = fixture();
    let message =
        EventData::TeamMessage { message_id: "tm_01".into(), from: "ep_a".into(), to: "ep_b".into(), content: vec![] };
    events.push(Event { seq: 11, time: 0, version: None, data: message });
    events.push(ended(12));
    fold(&events).expect("the lead requeues an undelivered message when the target restarts");
}

#[test]
fn tool_result_needs_a_call_and_only_one() {
    let mut events = fixture();
    events.push(Event { seq: 11, time: 0, version: None, data: result(2, "tc_2", "again") });
    assert!(matches!(fold(&events), Err(LogError::Invalid { seq: 11, .. })));
    events[11].data = result(2, "tc_9", "orphan");
    assert!(matches!(fold(&events), Err(LogError::Invalid { seq: 11, .. })));
}

#[test]
fn request_must_name_current_header_and_fresh_inbox() {
    let state = fold(&fixture()[..4]).unwrap();
    let stale = Event { seq: 4, time: 0, version: None, data: request(2, 0, vec![], vec![]) };
    assert!(validate_next(&state, &stale).is_err());
    let reused = Event { seq: 4, time: 0, version: None, data: request(2, 2, vec![1], vec![]) };
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

/// docs/log-format.md "Open obligations": a `tool/inner-call` opens the
/// tool-call obligation and its ordinary `tool/result` closes it.
/// "Derived messages": that result contributes nothing; the outer result
/// alone reaches the model.
#[test]
fn an_inner_call_opens_the_tool_call_obligation_and_its_result_is_excluded() {
    let datas = vec![
        EventData::EpisodeStart(start("ep")),
        inbox(InboxSource::Task, "do it"),
        header(),
        request(1, 2, vec![1], vec![]),
        assistant(1, "", vec![ToolCall { name: "python".into(), ..call("tc_p") }], false),
        inner_call("tc_p", 0),
        result(1, "tc_p_0", "inner rendered"),
        result(1, "tc_p", "outer rendered"),
        EventData::EpisodeEnd { outcome: Outcome::Completed { value: serde_json::json!("v") } },
    ];
    let events = number(datas);
    fold(&events).unwrap();
    let rendered: Vec<String> = derive_messages(&events, u64::MAX, &[])
        .into_iter()
        .filter_map(|m| match m {
            Message::Tool { rendered, .. } => Some(rendered),
            _ => None,
        })
        .collect();
    assert_eq!(rendered, ["outer rendered"], "the outer result alone enters derived messages");
}

/// docs/log-format.md "Open obligations": an inner call left open stands
/// like any open tool call, so a log may not end while one is open.
#[test]
fn an_open_inner_call_blocks_episode_end() {
    let datas = vec![
        EventData::EpisodeStart(start("ep")),
        inbox(InboxSource::Task, "do it"),
        header(),
        request(1, 2, vec![1], vec![]),
        assistant(1, "", vec![call("tc_p")], false),
        inner_call("tc_p", 0),
        result(1, "tc_p", "outer rendered"),
        EventData::EpisodeEnd { outcome: Outcome::Completed { value: serde_json::json!("v") } },
    ];
    let err = fold(&number(datas)).unwrap_err();
    assert!(err.to_string().contains("closed before episode/end"), "{err}");
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
        EventData::ToolRenderingArchive(RenderingArchive {
            step: 1,
            call_id: "c".into(),
            file: format!("renderings/{}.txt", "a".repeat(64)),
            digest: format!("sha256:{}", "a".repeat(64)),
            bytes: 1,
        }),
        EventData::HostToolCall { step: 1, call_id: "c".into(), name: "h".into(), args: reserved.clone() },
        EventData::ToolInnerCall(ToolInnerCall {
            outer_call_id: "c".into(),
            call_id: "c_0".into(),
            index: 0,
            name: "read".into(),
            args: reserved.clone(),
        }),
        inbox(InboxSource::Request, "reserved source"),
        EventData::BudgetReserve {
            child_id: "k".into(),
            reserved: BudgetAmount { model_calls: Some(1), seconds: Some(3), ..Default::default() },
        },
        EventData::BudgetRelease { child_id: "k".into(), spent: BudgetAmount::default() },
        EventData::SpawnStart {
            child_id: "k".into(),
            contract: "p".into(),
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
        EventData::CompactionStart(CompactionStart {
            step: 3,
            covered: Covered { first_seq: 1, last_seq: 7 },
            trigger: CompactionTrigger::Threshold,
            projected_tokens: 190_000,
            reserved: BudgetAmount { model_calls: Some(3), ..Default::default() },
        }),
        compaction_summary(3, 8, 13, "first summary", &["a"]),
        EventData::CompactionEnd {
            step: 3,
            ok: false,
            usage: Usage::default(),
            active_estimate: 190_000,
            error: Some("the summary was empty".into()),
        },
        EventData::WorkflowNodeStart(WorkflowNodeStart {
            node: "survey".into(),
            fire: 1,
            inputs: vec![4],
            child_id: Some("ep_1".into()),
        }),
        EventData::WorkflowNodeEnd(WorkflowNodeEnd {
            node: "survey".into(),
            fire: 1,
            value: reserved,
            rendered: "r".into(),
            error: None,
            failure: None,
            duration_ms: 2,
        }),
        EventData::WorkflowBranch(WorkflowBranch {
            node: "propose".into(),
            fire: 1,
            label: "accept".into(),
            successors: vec!["derive".into()],
        }),
        EventData::WorkflowRecovery(WorkflowRecovery {
            node: "derive".into(),
            fire: 1,
            cause: "operation-failed".into(),
            action: "retry".into(),
            target: Some("survey".into()),
            note: None,
            failure: None,
            intervention: 1,
        }),
        EventData::VerificationResult(VerificationResult {
            step: 3,
            tool: "check".into(),
            verifier_fingerprint: "sha256:aa".into(),
            status: VerificationStatus::Findings,
            findings: vec!["missing test".into()],
            error: None,
            candidate_sha256: Some(format!("sha256:{}", "b".repeat(64))),
            duration_ms: 12,
        }),
    ];
    let mut seen = std::collections::BTreeSet::new();
    for data in variants {
        let name = data.type_name();
        seen.insert(name.clone());
        let event = Event { seq: 0, time: 1, version: None, data };
        let line = serde_json::to_string(&event).unwrap();
        assert!(line.contains(&format!("\"type\":\"{name}\"")), "{line}");
        let back: Event = serde_json::from_str(&line).unwrap();
        assert_eq!(back, event);
        assert_eq!(serde_json::to_string(&back).unwrap(), line);
    }
    // The expected set is read from the declaration of `EventData` rather
    // than written here, so a variant added to the enum and not to the list
    // above fails this test instead of passing unnoticed.
    let declared: std::collections::BTreeSet<String> = include_str!("lib.rs")
        .lines()
        .skip_while(|line| !line.starts_with("pub enum EventData"))
        .take_while(|line| *line != "}")
        .filter_map(|line| line.split_once("rename = \"").and_then(|(_, rest)| rest.split_once('"')))
        .map(|(name, _)| name.to_string())
        .collect();
    assert!(declared.len() > 20, "the declaration of EventData was not found");
    assert_eq!(seen, declared, "one of each event type, reserved ones included");
}

/// docs/log-format.md "Tool calls": logs written before typed failures
/// remain readable, and a typed failure survives serialization exactly.
#[test]
fn tool_failure_is_additive_and_round_trips() {
    let old = serde_json::json!({
        "seq": 0, "time": 1, "type": "tool/result", "data": {
            "step": 1, "call_id": "tc_1", "name": "read",
            "value": { "error": "denied" }, "rendered": "denied", "is_error": true,
            "spill": null, "subject": "denied", "duration_ms": 1, "synthetic": false
        }
    });
    let parsed: Event = serde_json::from_value(old.clone()).unwrap();
    assert_eq!(serde_json::to_value(&parsed).unwrap(), old);
    let EventData::ToolResult(result) = parsed.data else { panic!() };
    assert!(result.failure.is_none());

    let failure = ToolFailure {
        code: ToolFailureCode::CapabilityDenied,
        message: "access refused".into(),
        retryable: false,
        details: serde_json::json!({ "path": "/private" }),
    };
    let event = Event {
        seq: 0,
        time: 1,
        version: None,
        data: EventData::ToolResult(ToolResult { failure: Some(failure.clone()), ..result }),
    };
    let back: Event = serde_json::from_str(&serde_json::to_string(&event).unwrap()).unwrap();
    let EventData::ToolResult(result) = back.data else { panic!() };
    assert_eq!(result.failure, Some(failure));
}

#[test]
fn a_failure_marks_the_tool_result_as_an_error() {
    let mut events = fixture();
    let EventData::ToolResult(result) = &mut events[6].data else { panic!() };
    result.failure = Some(ToolFailure {
        code: ToolFailureCode::OperationFailed,
        message: "failed".into(),
        retryable: true,
        details: serde_json::json!({}),
    });
    assert!(matches!(fold(&events), Err(LogError::Invalid { seq: 6, rule }) if rule.contains("is_error")));
}

/// docs/log-format.md `episode/start`: the sandbox record includes resolved
/// permissions and the process cleanup mechanism.
#[test]
fn sandbox_process_boundary_and_permissions_round_trip() {
    let info = SandboxInfo {
        mode: SandboxMode::BestEffort,
        landlock_abi: 7,
        resolved_permissions: Default::default(),
        process_boundary: ProcessBoundaryInfo {
            kind: ProcessBoundaryKind::CgroupV2,
            subtree_cleanup: SubtreeCleanup::Enforced,
            reason: None,
        },
    };
    let json = serde_json::to_value(&info).unwrap();
    assert_eq!(json["process_boundary"]["kind"], "cgroup-v2");
    assert_eq!(serde_json::from_value::<SandboxInfo>(json).unwrap(), info);
}

/// docs/log-format.md "Envelope": a log whose first event states no
/// version is read as version 3, the last version whose writers stated
/// none.
#[test]
fn a_log_stating_no_version_reads_as_version_3() {
    let dir = tmp("unversioned");
    let lines: String = number(vec![EventData::EpisodeStart(start("ep")), inbox(InboxSource::Task, "t")])
        .iter()
        .map(|event| serde_json::to_string(event).unwrap() + "\n")
        .collect();
    std::fs::write(dir.join("episode.jsonl"), lines).unwrap();
    let events = read_all(&dir).unwrap();
    assert!(events.iter().all(|event| event.version.is_none()));
    fold(&events).unwrap();
}

/// docs/log-format.md "Envelope": a stated version this reader does not
/// read is refused with both versions named, before event parsing, so the
/// refusal does not depend on the reader knowing the log's event shapes.
#[test]
fn an_unsupported_stated_version_is_refused_naming_both_versions() {
    let dir = tmp("unsupported-version");
    let line = r#"{"seq":0,"time":0,"version":4,"type":"future/event","data":{}}"#;
    std::fs::write(dir.join("episode.jsonl"), format!("{line}\n")).unwrap();
    let error = read_all(&dir).unwrap_err();
    assert!(matches!(error, LogError::UnsupportedVersion { found: 4, supported: LOG_VERSION }), "{error}");
    assert_eq!(error.to_string(), "log states format version 4; this reader reads version 3");
}
