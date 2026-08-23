use super::{done_when_line, files, first_kept, projected, prompt, steps, tokens, Policy};
use foe_core::context::{Answer, ContextPolicy, ContextState, Cut, Summarized, SummaryCall};
use foe_core::{ContextConfig, DoneWhen, RuntimeError};
use foe_log::{
    AssistantMessage, BudgetAmount, CompactedFiles, CompactionSummary, ContentBlock, ContinuationState, Covered,
    EpisodeStart, Event, EventData, InboxItem, InboxSource, Message, ModelRequest, Outcome, RuntimeInfo, SandboxInfo,
    SandboxMode, StopReason, ToolCall, ToolResult, Usage,
};
use serde_json::json;

fn text(s: &str) -> Vec<ContentBlock> {
    vec![ContentBlock::Text { text: s.into() }]
}

fn inbox(source: InboxSource, s: &str) -> EventData {
    EventData::InboxItem(InboxItem { source, content: text(s), from: None, message_id: None })
}

fn request(step: u32, id: &str, consumed: Vec<u64>) -> EventData {
    let request_id = id.to_string();
    EventData::ModelRequest(ModelRequest {
        step,
        attempt: 1,
        request_id,
        header_seq: 2,
        consumed,
        messages: vec![],
        max_output_tokens: None,
    })
}

fn assistant(step: u32, id: &str, body: &str, calls: Vec<ToolCall>, input: u64) -> EventData {
    EventData::AssistantMessage(AssistantMessage {
        step,
        request_id: id.into(),
        text: body.into(),
        tool_calls: calls,
        stop: StopReason::Tool,
        usage: Usage { input, output: 20, cache_read: 0 },
        interrupted: false,
        thinking: vec![],
    })
}

fn call(id: &str, name: &str, path: &str) -> ToolCall {
    ToolCall { id: id.into(), name: name.into(), args: json!({ "path": path }) }
}

fn result(step: u32, call_id: &str, rendered: &str, is_error: bool) -> EventData {
    EventData::ToolResult(ToolResult {
        step,
        call_id: call_id.into(),
        name: "read".into(),
        value: json!({}),
        rendered: rendered.into(),
        is_error,
        spill: None,
        subject: None,
        duration_ms: 1,
        synthetic: false,
    })
}

fn number(datas: Vec<EventData>) -> Vec<Event> {
    datas.into_iter().enumerate().map(|(i, data)| Event { seq: i as u64, time: i as i64, data }).collect()
}

/// Three steps: a read of `a` (48 bytes rendered), a failed read of `b`
/// and an edit of `c` (8 bytes), and a final read of `d` (120 bytes).
fn episode() -> Vec<Event> {
    let start = EpisodeStart {
        id: "ep".into(),
        parent_id: None,
        fork_origin: None,
        team_id: None,
        program: json!({}),
        identity: "sha256:0".into(),
        task: "fix the parser".into(),
        runtime: RuntimeInfo { version: "0".into(), build: "unknown".into() },
        sandbox: SandboxInfo { mode: SandboxMode::Off, landlock_abi: 0 },
    };
    number(vec![
        EventData::EpisodeStart(start),                                          // 0
        inbox(InboxSource::Task, "fix the parser"),                              // 1
        request(1, "rq_0001", vec![1]),                                          // 2
        assistant(1, "rq_0001", "reading", vec![call("c1", "read", "a")], 1000), // 3
        result(1, "c1", &"x".repeat(48), false),                                 // 4
        request(2, "rq_0002", vec![]),                                           // 5
        assistant(2, "rq_0002", "editing", vec![call("c2", "read", "b"), call("c3", "edit", "c")], 2000), // 6
        result(2, "c2", "gone", true),                                           // 7
        result(2, "c3", "ok", false),                                            // 8
        request(3, "rq_0003", vec![]),                                           // 9
        assistant(3, "rq_0003", "", vec![call("c4", "read", "d")], 3000),        // 10
        result(3, "c4", &"y".repeat(120), false),                                // 11
        inbox(InboxSource::Parent, "hurry up"),                                  // 12
    ])
}

fn config(window: Option<u64>, reserve: u64, keep: u64) -> ContextConfig {
    ContextConfig {
        compact: true,
        window_tokens: window,
        reserve_tokens: reserve,
        keep_recent_tokens: keep,
        margin_tokens: 100,
    }
}

fn state(events: &[Event]) -> ContextState<'_> {
    ContextState { events, remaining: BudgetAmount { model_calls: Some(7), ..Default::default() } }
}

/// docs/compaction.md "When it triggers": the projection is the last
/// response's input and output, what arrived after it, the output limit,
/// and the margin; a successful compaction resets it until a response
/// follows; summarization responses never project.
#[test]
fn the_projection_follows_the_last_ordinary_response() {
    let events = episode();
    assert_eq!(projected(&events, 500, 100), Some(3000 + 20 + tokens(120 + 8) + 500 + 100));
    let mut events = events;
    let seq = events.len() as u64;
    events.push(Event { seq, time: 0, data: assistant(4, "cmp_0004", "a summary", vec![], 9_999) });
    assert_eq!(projected(&events, 0, 0), Some(3000 + 20 + tokens(128)), "a cmp_ response is skipped");
    events.push(Event {
        seq: seq + 1,
        time: 0,
        data: EventData::CompactionEnd { step: 4, ok: true, usage: Usage::default(), active_estimate: 1, error: None },
    });
    assert_eq!(projected(&events, 0, 0), None, "nothing projects until the compacted request is answered");
    events.push(Event { seq: seq + 2, time: 0, data: assistant(4, "rq_0005", "next", vec![], 400) });
    assert_eq!(projected(&events, 0, 0), Some(420));
}

/// docs/compaction.md "Where it cuts": steps are sized by their assistant
/// text and rendered results; the kept suffix is the longest that fits
/// `keep_recent_tokens`, with at least one step kept and one summarized.
#[test]
fn the_cut_falls_on_a_step_boundary_and_always_makes_progress() {
    let events = episode();
    let steps = steps(&events, 1);
    assert_eq!(steps, vec![(2, tokens(7 + 48)), (5, tokens(7 + 4 + 2)), (9, tokens(120))]);
    assert_eq!(first_kept(&steps, 1_000), Some(1), "everything fits, so the oldest step alone is summarized");
    assert_eq!(first_kept(&steps, 30), Some(2), "the newest step alone fits");
    assert_eq!(first_kept(&steps, 1), Some(2), "the newest step is kept even when it does not fit");
    assert_eq!(first_kept(&steps, 34), Some(1), "the two newest steps fit: 30 + 4");
    assert_eq!(first_kept(&steps[..1], 1_000), None, "one step cannot be cut");
    let policy = Policy::new(config(Some(4_000), 800, 34), 4_000, 100, None);
    let cut = policy.plan(&state(&events)).expect("3000 + 20 + 32 + 100 + 100 exceeds 4000 - 800");
    assert_eq!(cut.first_kept_seq, 5);
    assert_eq!(cut.covered, Covered { first_seq: 1, last_seq: 4 });
    assert_eq!(cut.projected_tokens, 3252);
    assert!(!cut.exceeds_window);
    let roomy = Policy::new(config(Some(4_000), 100, 34), 4_000, 100, None);
    assert!(roomy.plan(&state(&events)).is_none(), "3252 fits under 3900");
    let tight = Policy::new(config(Some(3_000), 100, 34), 3_000, 100, None);
    assert!(tight.plan(&state(&events)).unwrap().exceeds_window);
}

/// A second compaction starts where the first kept from, and carries the
/// earlier summary's file lists forward.
#[test]
fn file_lists_are_extracted_structurally_and_accumulate_across_summaries() {
    let events = episode();
    let first = files(&events, Covered { first_seq: 1, last_seq: 4 }, &CompactedFiles::default());
    assert_eq!(first.read, vec!["a"]);
    let carried = CompactedFiles { read: vec!["z".into()], written: vec!["w".into()], edited: vec![] };
    let second = files(&events, Covered { first_seq: 5, last_seq: 8 }, &carried);
    assert_eq!(second.read, vec!["z"], "the failed read of b is not listed");
    assert_eq!(second.written, vec!["w"]);
    assert_eq!(second.edited, vec!["c"]);
    let again = files(&events, Covered { first_seq: 1, last_seq: 11 }, &first);
    assert_eq!(again.read, vec!["a", "d"], "sorted, without duplicates");
}

/// docs/compaction.md "What the summary receives": the earlier summary
/// under its heading, then the span as labeled plain text.
#[test]
fn the_prompt_is_labeled_plain_text_with_the_earlier_summary_first() {
    let span = vec![
        Message::User { content: text("fix the parser") },
        Message::Assistant { text: "reading".into(), tool_calls: vec![call("c1", "read", "a")], thinking: vec![] },
        Message::Tool { call_id: "c1".into(), name: "read".into(), rendered: "body".into(), is_error: false },
        Message::Tool { call_id: "c2".into(), name: "read".into(), rendered: "gone".into(), is_error: true },
    ];
    let fresh = prompt(None, &span);
    assert_eq!(
        fresh,
        "# Transcript\n\n[user]\nfix the parser\n\n[assistant]\nreading\n[call read {\"path\":\"a\"}]\n\n\
         [result read]\nbody\n\n[result read error]\ngone"
    );
    let iterated = prompt(Some("earlier"), &span);
    assert!(iterated.starts_with("# Earlier summary\n\nearlier\n\n# Transcript\n\n[user]"));
}

#[test]
fn the_completion_condition_renders_in_one_line() {
    assert_eq!(done_when_line(None), "a turn with no tool calls");
    let verified = DoneWhen { verify: Some("check".into()), retries: 2, returns: None };
    assert_eq!(
        done_when_line(Some(&verified)),
        "a turn with no tool calls or a non-error `check` call, then `check` reports no findings"
    );
    let returned = DoneWhen { verify: None, retries: 2, returns: Some(json!({ "type": "object" })) };
    assert_eq!(done_when_line(Some(&returned)), "a call to `return` with a value conforming to its schema");
}

/// Answers the summarization call with a scripted response and records
/// what it was asked.
struct Scripted {
    answer: Option<Answer>,
    asked: Option<(String, String)>,
}

#[async_trait::async_trait]
impl SummaryCall for Scripted {
    async fn call(&mut self, system: &str, user: String) -> Result<Answer, RuntimeError> {
        self.asked = Some((system.to_string(), user));
        Ok(self.answer.take().expect("one call"))
    }
}

fn response(body: &str) -> Answer {
    let EventData::AssistantMessage(message) = assistant(4, "cmp_0004", body, vec![], 800) else { unreachable!() };
    Answer::Message { message, request_seq: 14 }
}

/// docs/compaction.md "What the summary contains": the narrative is the
/// model's; the continuation state is built from the events and the budget.
#[tokio::test]
async fn summarize_builds_the_state_from_events_and_the_narrative_from_the_model() {
    let events = episode();
    let verified = DoneWhen { verify: Some("check".into()), retries: 2, returns: None };
    let policy = Policy::new(config(Some(4_000), 500, 34), 4_000, 100, Some(&verified));
    let cut = Cut {
        first_kept_seq: 9,
        covered: Covered { first_seq: 1, last_seq: 8 },
        projected_tokens: 3252,
        exceeds_window: false,
    };
    let mut call = Scripted { answer: Some(response("## Goal\nfix")), asked: None };
    let Summarized::Summary { summary, usage, active_estimate } =
        policy.summarize(&state(&events), &cut, &mut call).await.unwrap()
    else {
        panic!("a summary")
    };
    let (system, user) = call.asked.unwrap();
    assert_eq!(system, foe_core::harness_text::COMPACTION_INSTRUCTION);
    assert!(user.starts_with("# Transcript\n\n[user]\nfix the parser\n\n[assistant]\nreading\n[call read"));
    assert!(user.contains("[result read error]\ngone"), "the span runs to the cut");
    assert!(!user.contains("yyyy"), "nothing after the cut is summarized");
    assert_eq!(summary.summary, "## Goal\nfix");
    assert_eq!(summary.first_kept_seq, 9);
    assert_eq!(summary.summary_request_seq, 14);
    assert_eq!(summary.step, 4);
    assert_eq!(summary.state.task, "fix the parser");
    assert_eq!(
        summary.state.done_when,
        "a turn with no tool calls or a non-error `check` call, then `check` reports no findings"
    );
    assert_eq!(
        summary.state.files,
        CompactedFiles { read: vec!["a".into()], written: vec![], edited: vec!["c".into()] }
    );
    assert_eq!(summary.state.covered, cut.covered);
    assert_eq!(summary.state.budget_remaining.model_calls, Some(7));
    assert_eq!(usage.input, 800);
    assert!(active_estimate >= tokens(120) && active_estimate < 400, "{active_estimate}");
}

#[tokio::test]
async fn an_empty_or_failed_summary_is_reported_without_a_cut() {
    let events = episode();
    let policy = Policy::new(config(Some(4_000), 500, 34), 4_000, 100, None);
    let cut = Cut {
        first_kept_seq: 9,
        covered: Covered { first_seq: 1, last_seq: 8 },
        projected_tokens: 3252,
        exceeds_window: false,
    };
    let mut empty = Scripted { answer: Some(response("  \n")), asked: None };
    let Summarized::Failed { error, usage } = policy.summarize(&state(&events), &cut, &mut empty).await.unwrap() else {
        panic!("a failure")
    };
    assert_eq!((error.as_str(), usage.input), ("the summary was empty", 800));
    let mut failed = Scripted { answer: Some(Answer::Failed("overloaded".into())), asked: None };
    assert!(matches!(
        policy.summarize(&state(&events), &cut, &mut failed).await.unwrap(),
        Summarized::Failed { error, .. } if error == "overloaded"
    ));
    let mut ended = Scripted { answer: Some(Answer::Ended(Outcome::Failed { error: "stop".into() })), asked: None };
    assert!(matches!(policy.summarize(&state(&events), &cut, &mut ended).await.unwrap(), Summarized::Ended(_)));
}

/// An earlier summary enters the prompt under its heading and its state
/// carries forward.
#[tokio::test]
async fn a_second_compaction_iterates_on_the_first() {
    let mut events = episode();
    let seq = events.len() as u64;
    let prior = CompactionSummary {
        step: 2,
        summary: "earlier narrative".into(),
        state: ContinuationState {
            files: CompactedFiles { read: vec!["z".into()], written: vec![], edited: vec![] },
            ..ContinuationState::default()
        },
        first_kept_seq: 5,
        summary_request_seq: 4,
    };
    events.push(Event { seq, time: 0, data: EventData::CompactionSummary(prior) });
    let policy = Policy::new(config(Some(4_000), 500, 34), 4_000, 100, None);
    let cut = Cut {
        first_kept_seq: 9,
        covered: Covered { first_seq: 5, last_seq: 8 },
        projected_tokens: 3252,
        exceeds_window: false,
    };
    let mut call = Scripted { answer: Some(response("merged")), asked: None };
    let Summarized::Summary { summary, .. } = policy.summarize(&state(&events), &cut, &mut call).await.unwrap() else {
        panic!("a summary")
    };
    let user = call.asked.unwrap().1;
    assert!(user.starts_with("# Earlier summary\n\nearlier narrative\n\n# Transcript\n\n[assistant]\nediting\n"));
    assert!(!user.contains("fix the parser"), "the span before the earlier cut is not repeated");
    assert_eq!(summary.state.files.read, vec!["z"]);
    assert_eq!(summary.state.files.edited, vec!["c"]);
}
