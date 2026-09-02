use super::{learned_findings, parse_tolerant, run, Log, Params, MAX_ATTEMPTS, SPILL_LIMIT};
use crate::budget::Pool;
use crate::context::{ContextPolicy, ContextState, Cut, Summarized, SummaryCall};
use crate::registry::Handles;
use crate::test_util::{
    call, contract_with, done, registry_for, text as text_chunk, tmp, turn, Probe, ScratchDir, ScriptedTransport,
    Verifier,
};
use crate::{Tool, Transport};
use foe_contract::document::ResolvedContract;
use foe_contract::fingerprint::{canonical, sha256_hex};
use foe_contract::harness_text as text;
use foe_contract::Effect;
use foe_log::{
    BlockedCode, Chunk, Covered, EpisodeStart, Event, EventData, ExhaustedLimit, InboxSource, Outcome, RuntimeInfo,
    SandboxInfo, SandboxMode, StopReason, Usage, VerificationStatus,
};
use serde_json::json;
use std::sync::{Arc, Mutex};
use tokio::sync::watch;

fn start(contract: &ResolvedContract) -> EpisodeStart {
    EpisodeStart {
        id: "ep_test".into(),
        parent_id: None,
        fork_origin: None,
        team_id: None,
        contract: contract.to_value(),
        contract_fingerprint: "sha256:test".into(),
        task: "do the thing".into(),
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

struct Fixture {
    scratch: Option<ScratchDir>,
    dir: std::path::PathBuf,
    log: Arc<Log>,
    contract: ResolvedContract,
    tools: Vec<Box<dyn Tool>>,
    transport: Arc<dyn Transport>,
    context: Option<Arc<dyn ContextPolicy>>,
    stop: watch::Sender<Option<String>>,
    stop_rx: watch::Receiver<Option<String>>,
    parent_id: Option<String>,
    sessions: Option<Arc<dyn crate::Sessions>>,
}

impl Fixture {
    fn new(name: &str, edit: impl FnOnce(&mut serde_json::Value), responses: Vec<Vec<Chunk>>) -> Self {
        let root = tmp(name);
        let dir = root.join("episode");
        std::fs::create_dir_all(&dir).unwrap();
        let contract = contract_with(&root, edit).unwrap();
        let log = Arc::new(Log::create_or_open(&dir, None).unwrap());
        let (stop, stop_rx) = watch::channel(None);
        Self {
            scratch: Some(root),
            dir,
            log,
            contract,
            tools: vec![],
            transport: Arc::new(ScriptedTransport::new(responses)),
            context: None,
            stop,
            stop_rx,
            parent_id: None,
            sessions: None,
        }
    }

    fn child(mut self) -> Self {
        self.parent_id = Some("ep_parent".into());
        self
    }

    fn take_scratch(&mut self) -> ScratchDir {
        self.scratch.take().unwrap()
    }

    fn tool(mut self, tool: impl Tool + 'static) -> Self {
        self.tools.push(Box::new(tool));
        self
    }

    fn context(mut self, policy: impl ContextPolicy + 'static) -> Self {
        self.context = Some(Arc::new(policy));
        self
    }

    async fn run(mut self) -> (Outcome, Vec<Event>) {
        if self.contract.tools.iter().any(|name| name == crate::retrieval::NAME) {
            self.tools.push(crate::retrieval::tool(self.log.clone()));
        }
        let registry = Arc::new(registry_for(&self.contract, vec![], self.tools).unwrap());
        let mut episode_start = start(&self.contract);
        episode_start.parent_id = self.parent_id;
        let params = Params {
            log: self.log.clone(),
            start: episode_start,
            contract: self.contract.clone(),
            registry,
            handles: Handles::default(),
            transport: self.transport,
            pool: Arc::new(Mutex::new(Pool::new(self.contract.budget.clone()))),
            stop: self.stop_rx,
            children: None,
            sessions: self.sessions,
            context: self.context,
        };
        let outcome = run(params).await.unwrap();
        let events = foe_log::fold::read_all(&self.dir).unwrap();
        foe_log::fold::fold(&events).expect("the log is well-formed");
        (outcome, events)
    }
}

struct FailedWindowCompaction;

#[async_trait::async_trait]
impl ContextPolicy for FailedWindowCompaction {
    fn plan(&self, _state: &ContextState) -> Option<Cut> {
        Some(Cut {
            first_kept_seq: 2,
            covered: Covered { first_seq: 1, last_seq: 1 },
            projected_tokens: 10_000,
            exceeds_window: true,
        })
    }

    async fn summarize(
        &self,
        _state: &ContextState<'_>,
        _cut: &Cut,
        _call: &mut dyn SummaryCall,
    ) -> Result<Summarized, crate::RuntimeError> {
        Ok(Summarized::Failed { error: "summary unavailable".into(), usage: Usage::default() })
    }
}

fn types(events: &[Event]) -> Vec<String> {
    events.iter().map(|e| e.data.type_name()).collect()
}

fn results(events: &[Event]) -> Vec<&foe_log::ToolResult> {
    events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::ToolResult(r) => Some(r),
            _ => None,
        })
        .collect()
}

#[tokio::test]
async fn a_turn_without_tool_calls_completes_with_its_text() {
    let fx = Fixture::new("loop-complete", |_| {}, vec![turn("all done", vec![])]);
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!("all done") });
    assert_eq!(events.iter().filter(|event| matches!(event.data, EventData::EpisodeStart(_))).count(), 1);
    assert_eq!(
        events
            .iter()
            .filter(|event| matches!(&event.data, EventData::InboxItem(item) if item.source == InboxSource::Task))
            .count(),
        1
    );
    assert_eq!(
        types(&events),
        vec![
            "episode/start",
            "inbox/item",
            "request/header",
            "model/request",
            "assistant/chunk",
            "assistant/chunk",
            "assistant/message",
            "episode/end"
        ]
    );
    let EventData::ModelRequest(request) = &events[3].data else { panic!() };
    assert_eq!((request.step, request.attempt, request.header_seq, request.consumed.as_slice()), (1, 1, 2, &[1][..]));
    assert_eq!(
        request.messages,
        foe_log::fold::derive_messages(&events, 3, &[1]),
        "recorded messages equal the derivation"
    );
    let EventData::RequestHeader(header) = &events[2].data else { panic!() };
    assert_eq!(header.reason, foe_log::HeaderReason::Initial);
    assert_eq!(header.tools.iter().map(|t| t.name.as_str()).collect::<Vec<_>>(), vec!["block"]);
}

/// docs/compaction.md "When it fails": a failed summary for a request that
/// exceeds the model window reports the window, independent of any input
/// allowance in the contract budget.
#[tokio::test]
async fn failed_compaction_beyond_the_model_window_names_the_context_window() {
    let fx = Fixture::new("loop-context-window", |_| {}, vec![]).context(FailedWindowCompaction);
    let (outcome, _) = fx.run().await;
    assert_eq!(outcome, Outcome::Exhausted { limit: ExhaustedLimit::ContextWindow });
}

#[tokio::test]
async fn concurrent_effects_overlap_serial_effects_wait_and_results_keep_issue_order() {
    let fx = Fixture::new(
        "loop-concurrency",
        |v| v["tools"] = json!(["r1", "r2", "w"]),
        vec![
            turn("go", vec![call("a", "r1", "{}"), call("b", "r2", "{}"), call("c", "w", "{}"), call("d", "r1", "{}")]),
            turn("done", vec![]),
        ],
    );
    let r1 = Arc::new(Probe::slow("r1", Effect::Reads, 60));
    let r2 = Arc::new(Probe::slow("r2", Effect::Reads, 60));
    let w = Arc::new(Probe::slow("w", Effect::Writes, 10));
    let (outcome, events) = fx.tool(Shared(r1.clone())).tool(Shared(r2.clone())).tool(Shared(w.clone())).run().await;
    assert!(matches!(outcome, Outcome::Completed { .. }));
    let ids: Vec<_> = results(&events).iter().map(|r| r.call_id.as_str()).collect();
    assert_eq!(ids, vec!["a", "b", "c", "d"]);
    let runs = |p: &Probe| p.runs.lock().unwrap().clone();
    let (a, b, c, d) = (runs(&r1)[0].clone(), runs(&r2)[0].clone(), runs(&w)[0].clone(), runs(&r1)[1].clone());
    assert!(a.1 < b.2 && b.1 < a.2, "a and b overlap");
    assert!(c.1 >= a.2 && c.1 >= b.2, "the write waits for the reads before it");
    assert!(d.1 >= c.2, "the read after the write waits for it");
}

/// Lets a test keep a handle on a tool the registry owns.
struct Shared(Arc<Probe>);

#[async_trait::async_trait]
impl Tool for Shared {
    fn spec(&self) -> &foe_contract::ToolSpec {
        self.0.spec()
    }
    async fn call(&self, args: serde_json::Value, ctx: &crate::CallCtx) -> crate::ToolValue {
        self.0.call(args, ctx).await
    }
}

#[tokio::test]
async fn a_length_stop_rejects_every_call_without_running_any() {
    let mut chunks = vec![text_chunk("partial")];
    chunks.extend(call("a", "p", r#"{"x": 1}"#));
    chunks.extend([
        Chunk::ToolCallStart { id: "b".into(), name: "p".into() },
        Chunk::ToolCallDelta { id: "b".into(), delta: r#"{"x": [1, 2"#.into() },
    ]);
    chunks.push(done(StopReason::Length));
    let fx = Fixture::new("loop-length", |v| v["tools"] = json!(["p"]), vec![chunks, turn("ok", vec![])]);
    let probe = Arc::new(Probe::new("p", Effect::Pure));
    let (_, events) = fx.tool(Shared(probe.clone())).run().await;
    let rs = results(&events);
    assert_eq!(rs.len(), 2);
    assert!(rs.iter().all(|r| r.is_error && r.rendered == text::LENGTH_LIMIT_ERROR && !r.synthetic));
    assert!(probe.runs.lock().unwrap().is_empty(), "no call ran");
    let EventData::AssistantMessage(m) =
        &events.iter().find(|e| matches!(e.data, EventData::AssistantMessage(_))).unwrap().data
    else {
        panic!()
    };
    assert_eq!(m.tool_calls[1].args, json!({ "x": [1, 2] }), "a truncated call parses tolerantly");
}

#[tokio::test]
async fn a_failure_after_a_tool_call_started_is_recorded_as_interrupted_and_the_next_step_continues() {
    let mut chunks = vec![text_chunk("I will")];
    chunks.extend(call("a", "p", "{}"));
    chunks.push(Chunk::Error { message: "connection reset".into(), retryable: true });
    let fx = Fixture::new("loop-interrupted", |v| v["tools"] = json!(["p"]), vec![chunks, turn("recovered", vec![])]);
    let (outcome, events) = fx.tool(Probe::new("p", Effect::Pure)).run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!("recovered") });
    let EventData::AssistantMessage(m) =
        &events.iter().find(|e| matches!(e.data, EventData::AssistantMessage(_))).unwrap().data
    else {
        panic!()
    };
    assert!(m.interrupted && m.stop == StopReason::Interrupted && m.text == "I will");
    let rs = results(&events);
    assert!(rs[0].synthetic && rs[0].is_error && rs[0].rendered == text::INTERRUPTED_RESULT);
    assert!(
        !types(&events).iter().any(|t| t == "request/retry"),
        "a request that started a tool call is never retried"
    );
}

/// The clock is virtual; see `the_last_permitted_attempt_records_no_retry`.
#[tokio::test(start_paused = true)]
async fn failures_before_a_tool_call_are_retried_with_backoff_and_the_retries_consume_budget() {
    let started = tokio::time::Instant::now();
    let fx = Fixture::new(
        "loop-retry",
        |v| {
            v["budget"]["model_calls"] = json!(3);
            v["budget"]["output_tokens"] = json!(30);
        },
        vec![
            vec![Chunk::Error { message: "rate limited (429)".into(), retryable: true }],
            vec![text_chunk("partial text"), Chunk::Error { message: "gone".into(), retryable: true }],
            turn("finally", vec![]),
        ],
    );
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!("finally") });
    let retries: Vec<_> = events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::RequestRetry { attempt, cause, delay_ms, .. } => Some((*attempt, *cause, *delay_ms)),
            _ => None,
        })
        .collect();
    assert_eq!(retries, vec![(1, foe_log::RetryCause::RateLimit, 500), (2, foe_log::RetryCause::Interrupted, 1000)]);
    let announced: u64 = retries.iter().map(|(_, _, delay)| delay).sum();
    assert_eq!(started.elapsed().as_millis() as u64, announced, "each delay the log announces was waited");
    let requests: Vec<_> = events.iter().filter(|e| matches!(e.data, EventData::ModelRequest(_))).collect();
    assert_eq!(requests.len(), 3);
    let output_caps: Vec<Option<u32>> = requests
        .iter()
        .map(|e| match &e.data {
            EventData::ModelRequest(request) => request.max_output_tokens,
            _ => unreachable!(),
        })
        .collect();
    assert_eq!(output_caps, [Some(30), Some(30), Some(30)], "each retry recalculates the remaining allowance");
    let EventData::ModelRequest(third) = &requests[2].data else { panic!() };
    assert_eq!((third.step, third.attempt), (1, 3));
    assert!(third.consumed.is_empty(), "the task was consumed by the first attempt");
    assert_eq!(third.messages.len(), 1, "the discarded text never appears");
}

/// docs/log-format.md "Open obligations": a `request/retry` is written only
/// when the attempt it announces follows it. The last attempt the ceiling
/// allows therefore records no retry, and its failure ends the episode
/// without waiting a delay for a request that is never made.
/// The clock is virtual, so the backoff is measured rather than waited: the
/// scripted transport never touches the world, and tokio advances the clock
/// to each sleep's deadline as soon as the episode is idle.
#[tokio::test(start_paused = true)]
async fn the_last_permitted_attempt_records_no_retry() {
    // Partial text makes each failure an interrupted cause, which the
    // attempt ceiling bounds; a bare provider cause is budget-bounded.
    let failure = || vec![text_chunk("partial"), Chunk::Error { message: "unreachable".into(), retryable: true }];
    let responses: Vec<_> = (0..MAX_ATTEMPTS).map(|_| failure()).collect();
    let fx = Fixture::new("loop-ceiling", |v| v["budget"]["model_calls"] = json!(20), responses);
    let started = tokio::time::Instant::now();
    let (outcome, events) = fx.run().await;
    let expected = format!("{MAX_ATTEMPTS} attempts at step 1 failed");
    assert_eq!(outcome, Outcome::Blocked { code: BlockedCode::RecoveryExhausted, message: expected });
    let kinds = types(&events);
    let count = |name: &str| kinds.iter().filter(|t| *t == name).count();
    assert_eq!(count("model/request"), MAX_ATTEMPTS as usize);
    assert_eq!(count("request/retry"), MAX_ATTEMPTS as usize - 1);
    for (i, kind) in kinds.iter().enumerate() {
        if kind == "request/retry" {
            assert_eq!(kinds[i + 1], "model/request", "the retry at seq {i} announces an attempt that never came");
        }
    }
    let waited: u64 = (0..MAX_ATTEMPTS - 1).map(|n| 500u64 << n).sum();
    let elapsed = started.elapsed().as_millis() as u64;
    assert_eq!(elapsed, waited, "the episode waited every delay it announced and no delay it did not");
}

/// docs/design.md "Failure of a model request": a provider-reported
/// outage is bounded by the budget, not the attempt ceiling. Attempts
/// continue past `MAX_ATTEMPTS` while the seconds budget funds the next
/// delay, and a recovered provider completes the episode.
#[tokio::test(start_paused = true)]
async fn a_provider_outage_is_waited_out_within_the_budget() {
    let failure = || vec![Chunk::Error { message: "overloaded".into(), retryable: true }];
    let mut responses: Vec<_> = (0..MAX_ATTEMPTS + 1).map(|_| failure()).collect();
    responses.push(turn("recovered", vec![]));
    let fx = Fixture::new(
        "loop-outage-recovers",
        |v| {
            v["budget"]["model_calls"] = json!(20);
            v["budget"]["seconds"] = json!(3600);
        },
        responses,
    );
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!("recovered") });
    let retries = types(&events).iter().filter(|t| *t == "request/retry").count();
    assert_eq!(retries, MAX_ATTEMPTS as usize + 1, "the attempt ceiling does not bound a provider outage");
}

/// The outage wait never exceeds the seconds budget: when the remaining
/// budget cannot fund the next delay, the episode ends blocked and the
/// message names the budget.
#[tokio::test(start_paused = true)]
async fn an_outage_beyond_the_seconds_budget_ends_blocked() {
    let failure = || vec![Chunk::Error { message: "overloaded".into(), retryable: true }];
    let responses: Vec<_> = (0..3).map(|_| failure()).collect();
    let fx = Fixture::new(
        "loop-outage-budget",
        |v| {
            v["budget"]["model_calls"] = json!(20);
            v["budget"]["seconds"] = json!(3);
        },
        responses,
    );
    let (outcome, events) = fx.run().await;
    match outcome {
        Outcome::Blocked { code: BlockedCode::RecoveryExhausted, message } => {
            assert!(message.contains("cannot fund another"), "{message}");
            assert!(message.contains("3 attempts"), "{message}");
        }
        other => panic!("expected a blocked outcome, got {other:?}"),
    }
    let retries = types(&events).iter().filter(|t| *t == "request/retry").count();
    assert_eq!(retries, 2, "two funded delays preceded the unfunded third");
}

#[tokio::test]
async fn a_non_retryable_failure_fails_the_episode() {
    let fx =
        Fixture::new("loop-fatal", |_| {}, vec![vec![Chunk::Error { message: "bad key".into(), retryable: false }]]);
    let (outcome, _) = fx.run().await;
    assert!(matches!(outcome, Outcome::Failed { error } if error.contains("bad key")));
}

/// docs/config.md `budget`: the last available ordinary request receives
/// one reconstructable system warning and may complete normally.
#[tokio::test]
async fn the_last_available_request_carries_a_budget_warning() {
    let fx = Fixture::new(
        "loop-final-request-warning",
        |v| {
            v["budget"]["model_calls"] = json!(3);
            v["tools"] = json!(["p"]);
        },
        vec![
            turn("inspect", vec![call("a", "p", "{}")]),
            turn("change", vec![call("b", "p", "{}")]),
            turn("evidence supports completion", vec![]),
        ],
    );
    let (outcome, events) = fx.tool(Probe::new("p", Effect::Pure)).run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!("evidence supports completion") });

    let warnings: Vec<_> = events
        .iter()
        .filter(|event| {
            matches!(&event.data, EventData::InboxItem(item)
                if item.source == InboxSource::System
                    && matches!(&item.content[0], foe_log::ContentBlock::Text { text } if text == text::FINAL_REQUEST))
        })
        .collect();
    assert_eq!(warnings.len(), 1);
    let requests: Vec<_> = events
        .iter()
        .filter_map(|event| match &event.data {
            EventData::ModelRequest(request) => Some(request),
            _ => None,
        })
        .collect();
    assert_eq!(requests.len(), 3);
    assert!(!requests[0].consumed.contains(&warnings[0].seq) && !requests[1].consumed.contains(&warnings[0].seq));
    assert!(requests[2].consumed.contains(&warnings[0].seq));
    assert!(serde_json::to_string(&requests[2].messages).unwrap().contains(text::FINAL_REQUEST));
}

/// docs/config.md `budget`: a child receives the warning before its final
/// call from its bounded pool.
#[tokio::test]
async fn a_bounded_child_receives_the_warning_before_its_final_call() {
    let fx = Fixture::new(
        "loop-child-final-request-warning",
        |v| v["budget"]["model_calls"] = json!(1),
        vec![turn("child evidence", vec![])],
    )
    .child();
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!("child evidence") });
    let EventData::EpisodeStart(start) = &events[0].data else { panic!() };
    assert_eq!(start.parent_id.as_deref(), Some("ep_parent"));
    let EventData::ModelRequest(request) =
        &events.iter().find(|e| matches!(e.data, EventData::ModelRequest(_))).unwrap().data
    else {
        panic!()
    };
    assert!(serde_json::to_string(&request.messages).unwrap().contains(text::FINAL_REQUEST));
}

/// docs/config.md `budget`: the warning changes no completion rule. A last
/// request that leaves work outstanding still exhausts the episode.
#[tokio::test]
async fn the_final_request_warning_does_not_complete_unfinished_work() {
    let fx = Fixture::new(
        "loop-final-request-unfinished",
        |v| {
            v["budget"]["model_calls"] = json!(1);
            v["tools"] = json!(["p"]);
        },
        vec![turn("continue", vec![call("a", "p", "{}")])],
    );
    let (outcome, events) = fx.tool(Probe::new("p", Effect::Pure)).run().await;
    assert_eq!(outcome, Outcome::Exhausted { limit: ExhaustedLimit::ModelCalls });
    assert_eq!(events.iter().filter(|event| matches!(event.data, EventData::InboxItem(_))).count(), 2);
}

#[tokio::test]
async fn the_model_call_budget_ends_the_episode_as_exhausted() {
    let fx = Fixture::new(
        "loop-exhausted",
        |v| {
            v["budget"]["model_calls"] = json!(2);
            v["tools"] = json!(["p"]);
        },
        vec![turn("1", vec![call("a", "p", "{}")]), turn("2", vec![call("b", "p", "{}")]), turn("3", vec![])],
    );
    let (outcome, events) = fx.tool(Probe::new("p", Effect::Pure)).run().await;
    assert_eq!(outcome, Outcome::Exhausted { limit: ExhaustedLimit::ModelCalls });
    assert_eq!(events.iter().filter(|e| matches!(e.data, EventData::ModelRequest(_))).count(), 2);
}

#[tokio::test]
async fn the_same_call_with_the_same_result_in_threshold_consecutive_steps_is_looping() {
    let step = || turn("looking", vec![call("x", "p", r#"{"q": 1}"#)]);
    let fx = Fixture::new(
        "loop-looping-call",
        |v| {
            v["tools"] = json!(["p"]);
            v["budget"]["loop_threshold"] = json!(2);
        },
        vec![step(), step(), step()],
    );
    let (outcome, events) = fx.tool(Probe::new("p", Effect::Pure)).run().await;
    assert!(matches!(outcome, Outcome::Blocked { code: BlockedCode::LoopingToolCall, .. }), "{outcome:?}");
    assert_eq!(events.iter().filter(|e| matches!(e.data, EventData::ModelRequest(_))).count(), 2);
}

#[tokio::test]
async fn identical_assistant_text_in_threshold_consecutive_steps_is_looping() {
    let fx = Fixture::new(
        "loop-looping-text",
        |v| {
            v["tools"] = json!(["p"]);
            v["budget"]["loop_threshold"] = json!(3);
        },
        vec![
            turn("same", vec![call("a", "p", r#"{"n": 1}"#)]),
            turn("same", vec![call("b", "p", r#"{"n": 2}"#)]),
            turn("same", vec![call("c", "p", r#"{"n": 3}"#)]),
        ],
    );
    let (outcome, _) = fx.tool(Probe::new("p", Effect::Pure)).run().await;
    assert!(matches!(outcome, Outcome::Blocked { code: BlockedCode::LoopingReasoning, .. }), "{outcome:?}");
}

#[tokio::test]
async fn block_ends_the_episode_with_the_reported_code() {
    let fx = Fixture::new(
        "loop-block",
        |_| {},
        vec![turn("cannot", vec![call("a", "block", r#"{"code": "missing-capability", "message": "no network"}"#)])],
    );
    let (outcome, _) = fx.run().await;
    assert_eq!(outcome, Outcome::Blocked { code: BlockedCode::MissingCapability, message: "no network".into() });
}

/// docs/log-format.md "Blocked codes": a contract allowed to start children
/// can report that blocked children prevent the parent from proceeding.
#[tokio::test]
async fn a_parent_can_end_with_child_blocked() {
    let fx = Fixture::new(
        "loop-child-blocked",
        |v| {
            let root = v["grants"]["read"][0].clone();
            v["tools"] = json!(["block", "spawn"]);
            v["grants"]["spawn"] = json!(["worker"]);
            v["child_contracts"] = json!({ "worker": {
                "name": "worker", "instructions": { "role": "work" }, "tools": ["block"],
                "grants": { "read": [root] }, "budget": { "model_calls": 1 }
            }});
        },
        vec![turn(
            "all children stopped",
            vec![call("a", "block", r#"{"code":"child-blocked","message":"every child is blocked"}"#)],
        )],
    );
    let (outcome, _) = fx.tool(Probe::new("spawn", Effect::Spawns)).run().await;
    assert_eq!(outcome, Outcome::Blocked { code: BlockedCode::ChildBlocked, message: "every child is blocked".into() });
}

#[tokio::test]
async fn verifier_findings_return_as_inbox_items_until_accepted_or_retries_are_spent() {
    let verifier = || Verifier {
        spec: crate::test_util::spec("check", Effect::Pure),
        findings: Mutex::new(vec![vec!["missing test".into()], vec![]].into()),
    };
    let edit = |v: &mut serde_json::Value| {
        v["tools"] = json!(["block", "check"]);
        v["done_when"] = json!({ "verify": "check", "retries": 1 });
    };
    let fx = Fixture::new("loop-verify", edit, vec![turn("first try", vec![]), turn("second try", vec![])]);
    let (outcome, events) = fx.tool(verifier()).run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!("second try") });
    let item = events.iter().find_map(|e| match &e.data {
        EventData::InboxItem(i) if i.source == InboxSource::Verify => Some(i),
        _ => None,
    });
    let foe_log::ContentBlock::Text { text: framed } = &item.unwrap().content[0] else { panic!() };
    assert_eq!(framed, &text::fill(text::VERIFY_FINDINGS, &[("tool", "check"), ("findings", "missing test")]));
    let EventData::ModelRequest(second) =
        &events.iter().filter(|e| matches!(e.data, EventData::ModelRequest(_))).nth(1).unwrap().data
    else {
        panic!()
    };
    assert!(matches!(second.messages.last(), Some(foe_log::Message::User { .. })), "findings enter the next request");

    let stubborn = Verifier {
        spec: crate::test_util::spec("check", Effect::Pure),
        findings: Mutex::new(vec![vec!["a".into()], vec!["b".into()]].into()),
    };
    let fx = Fixture::new("loop-verify-spent", edit, vec![turn("1", vec![]), turn("2", vec![])]);
    let (outcome, _) = fx.tool(stubborn).run().await;
    assert!(matches!(outcome, Outcome::Blocked { code: BlockedCode::VerificationUnsatisfiable, .. }), "{outcome:?}");
}

fn verifications(events: &[Event]) -> Vec<&foe_log::VerificationResult> {
    events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::VerificationResult(v) => Some(v),
            _ => None,
        })
        .collect()
}

/// docs/log-format.md "Verification": every authoritative invocation is
/// recorded as exactly one `verification/result` — each findings run and
/// the accepted run that completes the episode — with the finding strings
/// the inbox item carries, and the event never enters derived messages.
#[tokio::test]
async fn every_authoritative_verification_is_recorded_as_one_event() {
    let verifier = Verifier {
        spec: crate::test_util::spec("check", Effect::Pure),
        findings: Mutex::new(vec![vec!["missing test".into()], vec![]].into()),
    };
    let fx = Fixture::new(
        "loop-verify-recorded",
        |v| {
            v["tools"] = json!(["block", "check"]);
            v["done_when"] = json!({ "verify": "check", "retries": 1 });
        },
        vec![turn("first try", vec![]), turn("second try", vec![])],
    );
    let (outcome, events) = fx.tool(verifier).run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!("second try") });
    let recorded = verifications(&events);
    assert_eq!(recorded.len(), 2, "one event per invocation, the accepted run included");
    assert_eq!(recorded[0].status, VerificationStatus::Findings);
    assert_eq!(recorded[0].findings, vec!["missing test".to_string()]);
    assert_eq!((recorded[0].tool.as_str(), recorded[0].error.as_deref()), ("check", None));
    assert_eq!(recorded[0].verifier_fingerprint, "unknown", "a built-in verifier carries the runtime build hash");
    assert_eq!(recorded[1].status, VerificationStatus::Accepted);
    assert!(recorded[1].findings.is_empty(), "an accepted run reports no finding");
    let judged = |value: &serde_json::Value| Some(format!("sha256:{}", sha256_hex(canonical(value).as_bytes())));
    assert_eq!(recorded[0].candidate_sha256, judged(&json!("first try")), "the event attests what it judged");
    assert_eq!(recorded[1].candidate_sha256, judged(&json!("second try")), "the event attests what it judged");
    let EventData::ModelRequest(second) =
        &events.iter().filter(|e| matches!(e.data, EventData::ModelRequest(_))).nth(1).unwrap().data
    else {
        panic!()
    };
    let serialized = serde_json::to_string(&second.messages).unwrap();
    assert!(!serialized.contains("verifier_fingerprint"), "the event never enters derived messages");
}

/// A verifier that fails rather than judges is recorded as `failed` with
/// the error, before the episode ends as failed.
#[tokio::test]
async fn a_failed_verifier_records_a_failed_verification() {
    let fx = Fixture::new(
        "loop-verify-failed",
        |v| {
            v["tools"] = json!(["block", "p"]);
            v["done_when"] = json!({ "verify": "p" });
        },
        vec![turn("candidate", vec![])],
    );
    let (outcome, events) = fx.tool(Probe::new("p", Effect::Pure)).run().await;
    assert!(matches!(outcome, Outcome::Failed { error } if error.contains("not a list of strings")));
    let recorded = verifications(&events);
    assert_eq!(recorded.len(), 1);
    assert_eq!(recorded[0].status, VerificationStatus::Failed);
    assert!(recorded[0].findings.is_empty());
    assert!(recorded[0].error.as_deref().unwrap_or_default().contains("not a list of strings"));
}

/// docs/design.md "Termination": calling the declared verifier is a
/// completion signal, so a verified artifact completes on the last call.
#[tokio::test]
async fn a_declared_verifier_call_can_complete_on_the_last_model_call() {
    let verifier = Verifier {
        spec: crate::test_util::spec("check", Effect::Pure),
        findings: Mutex::new(vec![vec![], vec![]].into()),
    };
    let fx = Fixture::new(
        "loop-verify-last-call",
        |v| {
            v["tools"] = json!(["block", "check"]);
            v["budget"]["model_calls"] = json!(1);
            v["done_when"] = json!({ "verify": "check" });
        },
        vec![turn("artifact ready", vec![call("verify", "check", "{}")])],
    );
    let (outcome, events) = fx.tool(verifier).run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!("artifact ready") });
    assert_eq!(events.iter().filter(|e| matches!(e.data, EventData::ModelRequest(_))).count(), 1);
}

/// docs/design.md "Termination": a verifier call does not override its
/// findings when the call budget has no request left for a correction.
#[tokio::test]
async fn verifier_findings_on_the_last_model_call_leave_the_episode_exhausted() {
    let verifier = Verifier {
        spec: crate::test_util::spec("check", Effect::Pure),
        findings: Mutex::new(vec![vec!["missing".into()], vec!["missing".into()]].into()),
    };
    let fx = Fixture::new(
        "loop-verify-findings-last-call",
        |v| {
            v["tools"] = json!(["block", "check"]);
            v["budget"]["model_calls"] = json!(1);
            v["done_when"] = json!({ "verify": "check" });
        },
        vec![turn("still working", vec![call("verify", "check", "{}")])],
    );
    let (outcome, events) = fx.tool(verifier).run().await;
    assert_eq!(outcome, Outcome::Exhausted { limit: ExhaustedLimit::ModelCalls });
    assert!(events.iter().any(|e| matches!(&e.data, EventData::InboxItem(i) if i.source == InboxSource::Verify)));
}

#[tokio::test]
async fn a_returns_contract_completes_only_through_the_return_tool() {
    let edit =
        |v: &mut serde_json::Value| v["done_when"] = json!({ "returns": { "type": "object", "required": ["n"] } });
    let fx = Fixture::new(
        "loop-returns",
        edit,
        vec![turn("I think I am done", vec![]), turn("returning", vec![call("a", "return", r#"{"value": {"n": 1}}"#)])],
    );
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!({ "n": 1 }) });
    let system =
        events.iter().filter(|e| matches!(&e.data, EventData::InboxItem(i) if i.source == InboxSource::System)).count();
    assert_eq!(system, 1, "a finished turn without `return` is answered with the requirement");
}

fn learned_return_schema() -> serde_json::Value {
    json!({
        "type": "object",
        "properties": {
            "learned": {
                "type": "array", "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": { "claim": { "type": "string" }, "seq": { "type": "integer", "minimum": 0 } },
                    "required": ["claim", "seq"], "additionalProperties": false
                }
            }
        },
        "required": ["learned"], "additionalProperties": false
    })
}

struct LargeError(foe_contract::ToolSpec);

#[async_trait::async_trait]
impl Tool for LargeError {
    fn spec(&self) -> &foe_contract::ToolSpec {
        &self.0
    }

    async fn call(&self, _args: serde_json::Value, _ctx: &crate::CallCtx) -> crate::ToolValue {
        crate::ToolValue::error("x".repeat(SPILL_LIMIT + 1))
    }
}

/// docs/config.md `done_when`: requiring `learned` makes completion cite
/// successful results in this episode before the semantic verifier judges it.
#[tokio::test]
async fn learned_completion_rejects_a_foreign_event_then_runs_the_declared_verifier() {
    let verifier =
        Verifier { spec: crate::test_util::spec("check", Effect::Pure), findings: Mutex::new(vec![vec![]].into()) };
    let fx = Fixture::new(
        "loop-learned-completion",
        |v| {
            v["tools"] = json!(["p", "check"]);
            v["done_when"] = json!({ "returns": learned_return_schema(), "verify": "check" });
        },
        vec![
            turn("observe", vec![call("evidence", "p", "{}")]),
            turn(
                "wrong event",
                vec![call("bad-return", "return", r#"{"value":{"learned":[{"claim":"the probe ran","seq":9}]}}"#)],
            ),
            turn(
                "cited result",
                vec![call("good-return", "return", r#"{"value":{"learned":[{"claim":"the probe ran","seq":10}]}}"#)],
            ),
        ],
    );
    let (outcome, events) = fx.tool(Probe::new("p", Effect::Pure)).tool(verifier).run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!({ "learned": [{ "claim": "the probe ran", "seq": 10 }] }) });
    let evidence = &events[10];
    assert!(matches!(&evidence.data, EventData::ToolResult(result) if !result.is_error));
    assert!(
        matches!(&events[9].data, EventData::AssistantMessage(_)),
        "the rejected citation names another event kind"
    );
    let notices = events.iter().filter_map(|event| match &event.data {
        EventData::InboxItem(item) if item.source == InboxSource::System => Some(&item.content),
        _ => None,
    });
    assert!(notices.into_iter().any(|content| format!("{content:?}").contains("does not name a successful")));
    let second_request = events
        .iter()
        .filter_map(|event| match &event.data {
            EventData::ModelRequest(request) => Some(request),
            _ => None,
        })
        .nth(1)
        .unwrap();
    assert!(format!("{:?}", second_request.messages).contains("[seq 10]"), "the model receives a citable sequence");
    let verified = verifications(&events);
    assert_eq!(verified.len(), 1, "the semantic verifier runs only after the evidence contract passes");
    assert_eq!(verified[0].status, VerificationStatus::Accepted);
}

/// docs/config.md `done_when`: a cited spilled result remains evidence only
/// while its canonical JSON can be reconstructed from the episode directory.
#[test]
fn learned_completion_requires_reconstructable_spilled_evidence() {
    let runtime = tokio::runtime::Runtime::new().unwrap();
    let mut fx = Fixture::new(
        "loop-learned-spill",
        |v| v["tools"] = json!(["p"]),
        vec![
            turn("observe", vec![call("evidence", "p", &format!(r#"{{"big":{}}}"#, SPILL_LIMIT + 1))]),
            turn("done", vec![]),
        ],
    );
    let _scratch = fx.take_scratch();
    let dir = fx.dir.clone();
    let (_, events) = runtime.block_on(fx.tool(Probe::new("p", Effect::Pure)).run());
    let evidence = events
        .iter()
        .find(|event| matches!(&event.data, EventData::ToolResult(result) if result.spill.is_some()))
        .unwrap();
    let log = Log::create_or_open(&dir, None).unwrap();
    let candidate = json!({ "learned": [{ "claim": "the probe ran", "seq": evidence.seq }] });
    assert!(learned_findings(&log, &candidate).is_empty());
    let EventData::ToolResult(result) = &evidence.data else { unreachable!() };
    std::fs::remove_file(dir.join("spill").join(result.spill.as_ref().unwrap())).unwrap();
    assert!(learned_findings(&log, &candidate).contains("does not reconstruct"));
}

/// docs/config.md `done_when`: spilling a result preserves whether the
/// tool failed, so a large error cannot become successful cited evidence.
#[tokio::test]
async fn learned_completion_rejects_a_spilled_error() {
    let mut fx = Fixture::new(
        "loop-learned-spilled-error",
        |v| v["tools"] = json!(["bad"]),
        vec![turn("observe", vec![call("bad-evidence", "bad", "{}")]), turn("done", vec![])],
    );
    let _scratch = fx.take_scratch();
    let dir = fx.dir.clone();
    let (_, events) = fx.tool(LargeError(crate::test_util::spec("bad", Effect::Pure))).run().await;
    let evidence = events
        .iter()
        .find(|event| matches!(&event.data, EventData::ToolResult(result) if result.spill.is_some()))
        .unwrap();
    let EventData::ToolResult(result) = &evidence.data else { unreachable!() };
    assert!(result.is_error, "spilling preserves the tool-owned error flag");
    let log = Log::create_or_open(&dir, None).unwrap();
    let candidate = json!({ "learned": [{ "claim": "the call succeeded", "seq": evidence.seq }] });
    assert!(learned_findings(&log, &candidate).contains("does not name a successful tool/result"));
}

#[tokio::test]
async fn a_large_result_is_spilled_and_replaced_by_a_locator() {
    let mut fx = Fixture::new(
        "loop-spill",
        |v| v["tools"] = json!(["p"]),
        vec![turn("big", vec![call("a", "p", &format!(r#"{{"big": {}}}"#, SPILL_LIMIT + 1))]), turn("", vec![])],
    );
    let _scratch = fx.take_scratch();
    let dir = fx.dir.clone();
    let (_, events) = fx.tool(Probe::new("p", Effect::Pure)).run().await;
    let r = results(&events)[0];
    assert_eq!(r.spill.as_deref(), Some("a.json"));
    assert_eq!(r.value["spill"], "a.json");
    assert!(r.rendered.starts_with("The canonical value was") && r.rendered.len() < SPILL_LIMIT);
    let spilled: serde_json::Value = serde_json::from_slice(&std::fs::read(dir.join("spill/a.json")).unwrap()).unwrap();
    assert_eq!(spilled["big"].as_str().unwrap().len(), SPILL_LIMIT + 1);
}

/// docs/log-format.md `tool/rendering-archive`: the complete rendering is
/// synchronized and recorded before the shortened result enters the log.
#[tokio::test]
async fn a_shortened_result_records_its_complete_rendering_first() {
    let mut fx = Fixture::new(
        "loop-rendering-archive",
        |v| v["tools"] = json!(["p", "retrieve"]),
        vec![
            turn("large", vec![call("a", "p", r#"{"big": 60000}"#), call("b", "p", r#"{"big": 60000}"#)]),
            turn("", vec![]),
        ],
    );
    let _scratch = fx.take_scratch();
    let dir = fx.dir.clone();
    let (_, events) = fx.tool(Probe::new("p", Effect::Pure)).run().await;
    let archive_index =
        events.iter().position(|event| matches!(event.data, EventData::ToolRenderingArchive(_))).unwrap();
    let EventData::ToolRenderingArchive(archive) = &events[archive_index].data else { panic!() };
    let EventData::ToolResult(result) = &events[archive_index + 1].data else { panic!() };
    assert_eq!((archive.step, archive.call_id.as_str()), (result.step, result.call_id.as_str()));
    let complete = std::fs::read(dir.join("spill").join(&archive.file)).unwrap();
    assert_eq!(complete.len(), archive.bytes as usize);
    assert_eq!(format!("sha256:{}", foe_log::digest::sha256_hex(&complete)), archive.digest);
    assert!(result.rendered.contains("Use retrieve with cursor \"r1."));
    let headers: Vec<_> = events
        .iter()
        .filter_map(|event| match &event.data {
            EventData::RequestHeader(header) => Some(header),
            _ => None,
        })
        .collect();
    // The header is a property of the contract: one header, present from the
    // first request, carrying every declared tool including retrieve.
    assert_eq!(headers.len(), 1);
    assert!(headers[0].tools.iter().any(|tool| tool.name == "retrieve"));
}

#[tokio::test]
async fn the_stop_signal_ends_the_episode_as_failed_with_its_reason() {
    let fx = Fixture::new("loop-cancel", |_| {}, vec![turn("x", vec![])]);
    fx.stop.send(Some("cancelled".into())).unwrap();
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Failed { error: "cancelled".into() });
    assert_eq!(types(&events), vec!["episode/start", "inbox/item", "episode/end"]);
}

#[tokio::test]
async fn a_seeded_log_continues_from_its_prefix_and_the_header_is_rewritten_only_on_change() {
    let mut first = Fixture::new(
        "loop-seed-src",
        |v| v["tools"] = json!(["p"]),
        vec![turn("one", vec![call("a", "p", "{}")]), turn("two", vec![])],
    );
    let _source_scratch = first.take_scratch();
    let src = first.dir.clone();
    first.tool(Probe::new("p", Effect::Pure)).run().await;
    let dest = tmp("loop-seed-dst");
    // Seq 10 is the first step's tool/result; the boundary excludes the second request.
    foe_log::seed::seed(
        &src,
        11,
        &dest,
        foe_log::seed::SeedHeader { new_id: "ep_fork".into(), parent_id: None, team_id: None, contract: None },
    )
    .unwrap();
    let root = src.parent().unwrap().to_path_buf();
    let contract = contract_with(&root, |v| v["tools"] = json!(["p"])).unwrap();
    let log = Arc::new(Log::create_or_open(&dest, None).unwrap());
    let (_, stop_rx) = watch::channel(None);
    let params = Params {
        log: log.clone(),
        start: start(&contract),
        contract: contract.clone(),
        registry: Arc::new(registry_for(&contract, vec![], vec![Box::new(Probe::new("p", Effect::Pure))]).unwrap()),
        handles: Handles::default(),
        transport: Arc::new(ScriptedTransport::new(vec![turn("forked end", vec![])])),
        pool: Arc::new(Mutex::new(Pool::new(contract.budget.clone()))),
        stop: stop_rx,
        children: None,
        sessions: None,
        context: None,
    };
    let outcome = run(params).await.unwrap();
    assert_eq!(outcome, Outcome::Completed { value: json!("forked end") });
    let events = foe_log::fold::read_all(&dest).unwrap();
    let state = foe_log::fold::fold(&events).unwrap();
    assert_eq!(state.start.unwrap().id, "ep_fork");
    assert_eq!(
        events.iter().filter(|e| matches!(e.data, EventData::RequestHeader(_))).count(),
        1,
        "an unchanged header is not rewritten"
    );
    let EventData::ModelRequest(request) =
        &events.iter().rev().find(|e| matches!(e.data, EventData::ModelRequest(_))).unwrap().data
    else {
        panic!()
    };
    assert_eq!(request.step, 2);
    assert!(request.messages.len() >= 3, "the copied prefix is part of the derived history");
}

/// A `Sessions` fake whose exits become observable at a chosen instant,
/// each reported once, the way `LocalSessions` reports real ones.
#[derive(Default)]
struct FakeSessions {
    exits: Mutex<Vec<(std::time::Instant, crate::SessionStatus)>>,
}

fn exited(id: u64) -> crate::SessionStatus {
    crate::SessionStatus { id, name: "server".into(), alive: false, exit_code: Some(0), seconds: 3 }
}

impl crate::Sessions for FakeSessions {
    fn start(&self, _req: crate::SessionRequest) -> Result<crate::SessionStatus, crate::CapError> {
        Err(crate::CapError::Invalid("unused".into()))
    }
    fn take_output(&self, _id: u64) -> Result<(crate::SessionStatus, crate::SessionOutput), crate::CapError> {
        Err(crate::CapError::Invalid("unused".into()))
    }
    fn write_stdin(&self, _id: u64, _bytes: &[u8]) -> Result<crate::SessionStatus, crate::CapError> {
        Err(crate::CapError::Invalid("unused".into()))
    }
    fn signal(&self, _id: u64, _signal: &str) -> Result<crate::SessionStatus, crate::CapError> {
        Err(crate::CapError::Invalid("unused".into()))
    }
    fn stop(&self, _id: u64) -> Result<crate::SessionStatus, crate::CapError> {
        Err(crate::CapError::Invalid("unused".into()))
    }
    fn settle(&self) -> Vec<crate::SessionSettlement> {
        Vec::new()
    }
    fn take_exited(&self) -> Vec<crate::SessionStatus> {
        let now = std::time::Instant::now();
        let mut exits = self.exits.lock().unwrap();
        let later: Vec<_> = exits.iter().filter(|(at, _)| *at > now).cloned().collect();
        let ready = exits.iter().filter(|(at, _)| *at <= now).map(|(_, s)| s.clone()).collect();
        *exits = later;
        ready
    }
}

/// docs/log-format.md "Inbox": a session exit reaches the log as one
/// `session`-source item, posted before the next request, consumed by it,
/// and derived into that request's messages; the settlement drain finds
/// nothing further to report.
#[tokio::test]
async fn a_session_exit_is_posted_once_and_consumed_by_the_next_request() {
    let fake = FakeSessions::default();
    fake.exits.lock().unwrap().push((std::time::Instant::now(), exited(1)));
    let mut fx = Fixture::new("loop-session-exit", |_| {}, vec![turn("done", vec![])]);
    fx.sessions = Some(Arc::new(fake));
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!("done") });
    let items: Vec<_> = events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::InboxItem(i) if i.source == InboxSource::Session => Some((e.seq, i.clone())),
            _ => None,
        })
        .collect();
    assert_eq!(items.len(), 1, "one item per session lifetime");
    let (seq, item) = &items[0];
    assert_eq!(item.from.as_deref(), Some("1"));
    let foe_log::ContentBlock::Text { text } = &item.content[0] else { panic!() };
    assert_eq!(text, "session 1: exit 0 after 3s");
    let request_event = events.iter().find(|e| matches!(e.data, EventData::ModelRequest(_))).unwrap();
    let EventData::ModelRequest(request) = &request_event.data else { panic!() };
    assert!(request.consumed.contains(seq), "the item entered the next request");
    assert_eq!(
        request.messages,
        foe_log::fold::derive_messages(&events, request_event.seq, &request.consumed),
        "recorded messages equal the derivation"
    );
    assert!(serde_json::to_string(&request.messages).unwrap().contains("session 1: exit 0 after 3s"));
}

/// docs/tools.md "wait": exits are posted while a turn's calls run, so a
/// `wait` on a session condition observes the arrival mid-block, returns
/// naming the condition, and the arrival is consumed by the next request.
#[tokio::test]
async fn wait_until_observes_a_session_exit_while_it_blocks() {
    let fake = FakeSessions::default();
    fake.exits.lock().unwrap().push((std::time::Instant::now() + std::time::Duration::from_millis(100), exited(2)));
    let mut fx = Fixture::new(
        "loop-wait-session",
        |v| v["tools"] = json!(["wait"]),
        vec![turn("waiting", vec![call("w", "wait", r#"{"until": [{"session": "any"}]}"#)]), turn("done", vec![])],
    );
    fx.sessions = Some(Arc::new(fake));
    fx.tools.push(Box::new(SessionWait(fx.log.clone())));
    let (outcome, events) = fx.run().await;
    assert_eq!(outcome, Outcome::Completed { value: json!("done") });
    let waited = results(&events).into_iter().find(|r| r.name == "wait").unwrap();
    assert!(!waited.is_error, "{:?}", waited.rendered);
    assert_eq!(waited.value, json!({ "matched": { "session": "any" } }));
    let item_seq = events
        .iter()
        .find(|e| matches!(&e.data, EventData::InboxItem(i) if i.source == InboxSource::Session))
        .map(|e| e.seq)
        .expect("the exit was posted while the wait blocked");
    assert!(item_seq < waited_result_seq(&events), "the arrival preceded the wait's result");
    let EventData::ModelRequest(second) =
        &events.iter().filter(|e| matches!(e.data, EventData::ModelRequest(_))).nth(1).unwrap().data
    else {
        panic!()
    };
    assert!(second.consumed.contains(&item_seq), "the arrival entered the request after the wait");
}

fn waited_result_seq(events: &[Event]) -> u64 {
    events.iter().find(|e| matches!(&e.data, EventData::ToolResult(r) if r.name == "wait")).map(|e| e.seq).unwrap()
}

/// A stand-in for a blocking wait tool: returns once the log holds a
/// `session`-source inbox item, which is exactly what the loop's mid-turn
/// exit posting must make observable. The team coordinator's wait tool
/// carries the full condition vocabulary and is tested beside it.
struct SessionWait(Arc<super::Log>);

#[async_trait::async_trait]
impl crate::Tool for SessionWait {
    fn spec(&self) -> &foe_contract::ToolSpec {
        static SPEC: std::sync::OnceLock<foe_contract::ToolSpec> = std::sync::OnceLock::new();
        SPEC.get_or_init(|| foe_contract::ToolSpec {
            name: "wait".into(),
            description: "wait for a session exit".into(),
            instruction: None,
            params: json!({ "type": "object" }),
            effect: foe_contract::Effect::Pure,
        })
    }

    async fn call(&self, _args: serde_json::Value, _ctx: &crate::CallCtx) -> crate::ToolValue {
        loop {
            let arrived = self.0.with_events(|e| {
                e.iter().any(|e| matches!(&e.data, EventData::InboxItem(i) if i.source == InboxSource::Session))
            });
            if arrived {
                return crate::ToolValue::ok(json!({ "matched": { "session": "any" } }), "matched");
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    }
}

#[test]
fn tolerant_parsing_closes_what_a_truncated_stream_left_open() {
    assert_eq!(parse_tolerant(r#"{"path": "a/b", "n": [1, 2"#), json!({ "path": "a/b", "n": [1, 2] }));
    assert_eq!(parse_tolerant(r#"{"path": "a/b"#), json!({ "path": "a/b" }));
    assert_eq!(parse_tolerant(r#"{"path": "a/b", "#), json!({ "path": "a/b" }));
    assert_eq!(parse_tolerant(r#"{"path": "a/b", "n":"#), json!({ "path": "a/b" }));
    assert_eq!(parse_tolerant(""), json!({}));
    assert_eq!(parse_tolerant("not json"), json!({}));
}

/// A tool named `python` that drives the composer it receives: one ordinary
/// inner call, one whose arguments are not an object, and one naming an
/// excluded control tool.
struct ComposeProbe {
    spec: foe_contract::ToolSpec,
}

#[async_trait::async_trait]
impl Tool for ComposeProbe {
    fn spec(&self) -> &foe_contract::ToolSpec {
        &self.spec
    }

    async fn call(&self, _args: serde_json::Value, ctx: &crate::CallCtx) -> crate::ToolValue {
        let Some(composer) = ctx.composer.clone() else {
            return crate::ToolValue::error("no composer");
        };
        let (first, first_err) = composer.call("probe", json!({ "n": 1 })).await.unwrap();
        let (_, violation) = composer.call("probe", json!([])).await.unwrap();
        let (refused_value, refused) = composer.call("block", json!({})).await.unwrap();
        crate::ToolValue::ok(
            json!({
                "first": first, "first_err": first_err, "violation": violation,
                "refused": refused, "refused_message": refused_value["error"],
            }),
            "composed",
        )
    }
}

/// docs/code-mode.md and docs/log-format.md: the loop hands a composer to
/// the call named `python` alone; every inner dispatch is recorded as
/// `tool/inner-call` and its ordinary `tool/result`, an inner argument
/// violation is an error result, derived messages carry the outer result
/// alone, and the obligations balance through fold validation.
#[tokio::test]
async fn a_composing_call_records_inner_calls_and_excludes_them_from_derived_messages() {
    let spec = crate::test_util::spec(crate::COMPOSING_TOOL, Effect::Pure);
    let mut probe = crate::test_util::spec("probe", Effect::Pure);
    probe.params = json!({
        "type": "object", "properties": { "n": { "type": "integer" } },
        "required": ["n"], "additionalProperties": false
    });
    let (outcome, events) = Fixture::new(
        "compose",
        |v| v["tools"] = json!([crate::COMPOSING_TOOL, "probe"]),
        vec![turn("", vec![call("tc_p", crate::COMPOSING_TOOL, "{}")]), turn("done", vec![])],
    )
    .tool(ComposeProbe { spec })
    .tool(Probe { spec: probe, ..Probe::new("probe", Effect::Pure) })
    .run()
    .await;
    assert!(matches!(outcome, Outcome::Completed { .. }), "{outcome:?}");
    let inner: Vec<(&str, u32)> = events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::ToolInnerCall(c) => Some((c.call_id.as_str(), c.index)),
            _ => None,
        })
        .collect();
    assert_eq!(inner, [("tc_p_0", 0), ("tc_p_1", 1)], "the refused control tool is never recorded");
    let results: Vec<(&str, bool)> = events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::ToolResult(r) => Some((r.call_id.as_str(), r.is_error)),
            _ => None,
        })
        .collect();
    assert_eq!(
        results,
        [("tc_p_0", false), ("tc_p_1", true), ("tc_p", false)],
        "each inner result precedes the outer result; the argument violation is an error"
    );
    let outer = events
        .iter()
        .find_map(|e| match &e.data {
            EventData::ToolResult(r) if r.call_id == "tc_p" => Some(r.value.clone()),
            _ => None,
        })
        .unwrap();
    assert_eq!(outer["first_err"], json!(false));
    assert_eq!(outer["first"]["args"]["n"], json!(1));
    assert_eq!(outer["violation"], json!(true));
    assert_eq!(outer["refused"], json!(true));
    assert!(outer["refused_message"].as_str().unwrap().contains("block"), "{outer}");
    let EventData::ModelRequest(last) =
        &events.iter().rev().find(|e| matches!(e.data, EventData::ModelRequest(_))).unwrap().data
    else {
        panic!()
    };
    let tools: Vec<&str> = last
        .messages
        .iter()
        .filter_map(|m| match m {
            foe_log::Message::Tool { call_id, .. } => Some(call_id.as_str()),
            _ => None,
        })
        .collect();
    assert_eq!(tools, ["tc_p"], "the outer result alone reaches the model");
}
