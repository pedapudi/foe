use super::*;
use foe_log::{
    AssistantMessage, EpisodeStart, ModelRoute, Outcome, RequestHeader, RuntimeInfo, SandboxInfo, StopReason, ToolCall,
    ToolResult, Usage, WorkflowNodeEnd, WorkflowNodeStart, WorkflowRecovery,
};

fn event(seq: u64, time: i64, data: EventData) -> Event {
    Event { seq, time, data }
}

fn start(program: serde_json::Value) -> EventData {
    EventData::EpisodeStart(EpisodeStart {
        id: "ep_1".into(),
        parent_id: None,
        fork_origin: None,
        team_id: None,
        program,
        identity: "sha256:aa".into(),
        task: "do it".into(),
        runtime: RuntimeInfo { version: "0.1.0".into(), build: "sha256:bb".into() },
        sandbox: SandboxInfo { mode: foe_log::SandboxMode::Off, landlock_abi: 0 },
    })
}

fn call(name: &str, args: serde_json::Value) -> EventData {
    EventData::AssistantMessage(AssistantMessage {
        step: 1,
        request_id: "rq_1".into(),
        text: String::new(),
        tool_calls: vec![ToolCall { id: "tc_1".into(), name: name.into(), args }],
        thinking: Vec::new(),
        stop: StopReason::Tool,
        usage: Usage { input: 10, output: 2, cache_read: 1 },
        interrupted: false,
    })
}

fn bash(command: &str) -> EventData {
    call("bash", serde_json::json!({ "command": command }))
}

fn heads(command: &str) -> Vec<String> {
    command_heads(command)
}

#[test]
fn head_of_a_plain_command() {
    assert_eq!(heads("pytest -q tests/"), vec!["pytest"]);
}

#[test]
fn every_segment_of_a_compound_command_votes() {
    assert_eq!(heads("make build && ctest --output-on-failure"), vec!["make", "ctest"]);
}

#[test]
fn a_leading_directory_change_is_not_the_command() {
    assert_eq!(heads("cd /app/repo && cargo test --all"), vec!["cargo test"]);
}

#[test]
fn a_pipeline_votes_once_per_stage() {
    assert_eq!(heads("cat data.csv | jq -r .name | sort"), vec!["cat", "jq", "sort"]);
}

#[test]
fn a_double_pipe_is_one_separator_and_leaves_no_stray_stage() {
    assert_eq!(heads("cargo build || make"), vec!["cargo build", "make"]);
}

#[test]
fn assignments_before_the_command_are_not_the_command() {
    assert_eq!(heads("RUST_LOG=debug CARGO_TERM_COLOR=never cargo test"), vec!["cargo test"]);
}

#[test]
fn newlines_and_semicolons_separate_segments() {
    assert_eq!(heads("git status;\ndocker build .\nmake"), vec!["git status", "docker", "make"]);
}

#[test]
fn a_path_qualified_command_votes_as_its_name() {
    assert_eq!(heads("/usr/bin/pytest -x"), vec!["pytest"]);
}

#[test]
fn a_dispatcher_without_a_subcommand_votes_as_its_head() {
    assert_eq!(heads("cargo --version"), vec!["cargo"]);
}

#[test]
fn an_empty_command_yields_no_head() {
    assert!(heads("   ").is_empty());
    assert!(heads("cd /tmp").is_empty());
}

#[test]
fn extensions_come_from_read_edit_and_grep_arguments() {
    let events = vec![
        event(0, 1000, start(serde_json::json!({}))),
        event(1, 1010, call("read", serde_json::json!({ "path": "src/main.RS" }))),
        event(2, 1020, call("edit", serde_json::json!({ "path": "/app/notes.md" }))),
        event(3, 1030, call("grep", serde_json::json!({ "pattern": "x", "glob": "**/*.py" }))),
    ];
    let facts = extract(&events, "/logs");
    assert_eq!(facts.evidence.extensions.get("rs"), Some(&1));
    assert_eq!(facts.evidence.extensions.get("md"), Some(&1));
    assert_eq!(facts.evidence.extensions.get("py"), Some(&1));
    assert_eq!(facts.evidence.tools.get("read"), Some(&1));
}

#[test]
fn a_name_without_an_extension_contributes_none() {
    let events = vec![
        event(0, 1000, start(serde_json::json!({}))),
        event(1, 1010, call("read", serde_json::json!({ "path": "/app/Makefile" }))),
        event(2, 1020, call("read", serde_json::json!({ "path": "/app/.gitignore" }))),
    ];
    assert!(extract(&events, "/logs").evidence.extensions.is_empty());
}

#[test]
fn steps_and_calls_carry_their_own_times_and_usage() {
    let events = vec![
        event(0, 1000, start(serde_json::json!({}))),
        event(
            1,
            1010,
            EventData::RequestHeader(RequestHeader {
                reason: foe_log::HeaderReason::Initial,
                system: "s".into(),
                tools: Vec::new(),
                model: ModelRoute { provider: "replay".into(), model: "recorded-1".into() },
            }),
        ),
        event(
            2,
            1020,
            EventData::ModelRequest(foe_log::ModelRequest {
                step: 1,
                attempt: 1,
                request_id: "rq_1".into(),
                header_seq: 1,
                consumed: Vec::new(),
                messages: Vec::new(),
                max_output_tokens: None,
            }),
        ),
        event(3, 1080, bash("pytest")),
        event(
            4,
            1100,
            EventData::ToolResult(ToolResult {
                step: 1,
                call_id: "tc_1".into(),
                name: "bash".into(),
                value: serde_json::json!({}),
                rendered: String::new(),
                is_error: true,
                spill: None,
                subject: Some("pytest · exit 1".into()),
                duration_ms: 40,
                synthetic: false,
            }),
        ),
        event(5, 1200, EventData::EpisodeEnd { outcome: Outcome::Failed { error: "no".into() } }),
    ];
    let facts = extract(&events, "/logs");
    assert_eq!(facts.provider, "replay");
    assert_eq!(facts.model_calls, 1);
    assert_eq!(facts.usage, Usage { input: 10, output: 2, cache_read: 1 });
    assert_eq!(facts.steps.len(), 1);
    assert_eq!((facts.steps[0].start_ms, facts.steps[0].end_ms), (1020, 1080));
    assert_eq!(facts.steps[0].stop, Some(StopReason::Tool));
    assert_eq!(facts.calls.len(), 1);
    assert_eq!((facts.calls[0].start_ms, facts.calls[0].end_ms), (1060, 1100));
    assert!(facts.calls[0].is_error);
    assert_eq!(outcome_terms(facts.outcome.as_ref()), ("failed", "none".into(), "no".into()));
}

#[test]
fn workflow_correction_evidence_is_derived_from_typed_events() {
    let events = vec![
        event(0, 1000, start(serde_json::json!({}))),
        event(
            1,
            1010,
            EventData::WorkflowRecovery(WorkflowRecovery {
                node: "repair".into(),
                fire: 1,
                cause: "verify-findings".into(),
                action: "retry".into(),
                target: Some("repair".into()),
                note: None,
                intervention: 1,
            }),
        ),
        event(
            2,
            1020,
            EventData::ModelRequest(foe_log::ModelRequest {
                step: 2,
                attempt: 1,
                request_id: "rq_2".into(),
                header_seq: 0,
                consumed: Vec::new(),
                messages: Vec::new(),
                max_output_tokens: None,
            }),
        ),
        event(
            3,
            1030,
            EventData::WorkflowNodeStart(WorkflowNodeStart {
                node: "optional-diagnosis".into(),
                fire: 1,
                inputs: vec![1],
                child_id: Some("ep_child".into()),
            }),
        ),
        event(
            4,
            1040,
            EventData::SpawnEnd {
                child_id: "ep_child".into(),
                outcome: Outcome::Exhausted { limit: foe_log::ExhaustedLimit::ModelCalls },
            },
        ),
        event(
            5,
            1050,
            EventData::WorkflowNodeEnd(WorkflowNodeEnd {
                node: "optional-diagnosis".into(),
                fire: 1,
                value: serde_json::json!({ "branch": "continue" }),
                rendered: r#"{"branch":"continue"}"#.into(),
                error: Some("exhausted: model_calls: the child spent its budget".into()),
                duration_ms: 20,
            }),
        ),
        event(6, 1060, EventData::EpisodeEnd { outcome: Outcome::Completed { value: serde_json::json!({}) } }),
    ];
    let facts = extract(&events, "/logs");
    assert_eq!(facts.recovery.interventions, 1);
    assert_eq!(facts.recovery.actions.get("retry"), Some(&1));
    assert_eq!(facts.recovery.empty_substitutions, 1);
}

#[test]
fn an_unknown_recovery_action_emits_only_the_closed_unknown_term() {
    let events = vec![
        event(0, 1000, start(serde_json::json!({}))),
        event(
            1,
            1010,
            EventData::WorkflowRecovery(WorkflowRecovery {
                node: "repair".into(),
                fire: 1,
                cause: "private cause text".into(),
                action: "private action text".into(),
                target: None,
                note: None,
                intervention: 1,
            }),
        ),
    ];
    let facts = extract(&events, "/logs");
    assert_eq!(facts.recovery.actions.len(), 1);
    assert_eq!(facts.recovery.actions.get("unknown"), Some(&1));
}

#[test]
fn known_values_are_the_configured_paths_the_log_directory_and_the_home_user() {
    let program = serde_json::json!({
        "grants": { "read": ["/app/repo", "/"], "write": [] },
        "model": { "token_file": "/home/rowan/.config/token.json" },
    });
    let events = vec![event(0, 1000, start(program))];
    let facts = extract(&events, "/var/log/foe/ep_1");
    let values: Vec<&str> = facts.known.iter().map(|k| k.value.as_str()).collect();
    assert!(values.contains(&"/app/repo"));
    assert!(values.contains(&"/home/rowan/.config/token.json"));
    assert!(values.contains(&"/var/log/foe/ep_1"));
    assert!(values.contains(&"rowan"));
    // A one-character root would match inside every path and is not a
    // value substitution can protect.
    assert!(!values.contains(&"/"));
    // Longest first, so a longer path is substituted before a prefix of it.
    let lengths: Vec<usize> = facts.known.iter().map(|k| k.value.len()).collect();
    assert!(lengths.windows(2).all(|pair| pair[0] >= pair[1]));
}

#[test]
fn a_completed_outcome_contributes_no_text() {
    let outcome = Outcome::Completed { value: serde_json::json!("the whole report the model wrote") };
    assert_eq!(outcome_terms(Some(&outcome)), ("completed", "none".into(), String::new()));
}

#[test]
fn an_exhausted_outcome_names_its_limit_as_the_exit_class() {
    let outcome = Outcome::Exhausted { limit: foe_log::ExhaustedLimit::ModelCalls };
    assert_eq!(outcome_terms(Some(&outcome)), ("exhausted", "model_calls".into(), String::new()));
}

#[test]
fn a_blocked_outcome_names_its_code_and_carries_its_message() {
    let outcome = Outcome::Blocked { code: foe_log::BlockedCode::AmbiguousTask, message: "which one".into() };
    assert_eq!(outcome_terms(Some(&outcome)), ("blocked", "ambiguous-task".into(), "which one".into()));
}

// ---- completion provenance ---------------------------------------------------

fn verification(seq: u64, status: foe_log::VerificationStatus, findings: &[&str]) -> Event {
    event(
        seq,
        1000 + seq as i64,
        EventData::VerificationResult(foe_log::VerificationResult {
            step: 1,
            tool: "check".into(),
            verifier_identity: "sha256:cc".into(),
            status,
            findings: findings.iter().map(|f| f.to_string()).collect(),
            error: None,
            duration_ms: 5,
        }),
    )
}

fn completed(seq: u64) -> Event {
    event(
        seq,
        1000 + seq as i64,
        EventData::EpisodeEnd { outcome: Outcome::Completed { value: serde_json::json!("done") } },
    )
}

fn node_start(seq: u64, node: &str, inputs: Vec<u64>) -> Event {
    event(
        seq,
        1000 + seq as i64,
        EventData::WorkflowNodeStart(foe_log::WorkflowNodeStart {
            node: node.into(),
            fire: 1,
            inputs,
            child_id: Some(format!("ep_{node}")),
        }),
    )
}

fn node_end(seq: u64, node: &str) -> Event {
    event(
        seq,
        1000 + seq as i64,
        EventData::WorkflowNodeEnd(foe_log::WorkflowNodeEnd {
            node: node.into(),
            fire: 1,
            value: serde_json::json!({}),
            rendered: String::new(),
            error: None,
            duration_ms: 2,
        }),
    )
}

/// The workflow of the built-in coding configuration in shape: a model
/// node feeding a terminal model node.
fn review_program() -> serde_json::Value {
    serde_json::json!({ "workflow": { "nodes": {
        "implement-task": { "model": { "name": "implement-task" }, "follows": ["task"] },
        "audit-and-repair-task": { "model": { "name": "audit-and-repair-task" },
                                   "follows": ["task", "implement-task"], "terminal": true }
    } } })
}

#[test]
fn provenance_is_verifier_when_the_last_verification_accepted() {
    let events = vec![
        event(0, 1000, start(serde_json::json!({}))),
        verification(1, foe_log::VerificationStatus::Findings, &["a", "b"]),
        verification(2, foe_log::VerificationStatus::Accepted, &[]),
        completed(3),
    ];
    let facts = extract(&events, "/logs");
    assert_eq!(facts.provenance, Some("verifier"));
    assert_eq!((facts.verification_runs, facts.verification_findings), (2, 2));
}

#[test]
fn provenance_is_reviewed_for_a_terminal_model_node_fed_by_a_model_node() {
    let events = vec![
        event(0, 1000, start(review_program())),
        node_start(1, "implement-task", vec![]),
        node_end(2, "implement-task"),
        node_start(3, "audit-and-repair-task", vec![2]),
        node_end(4, "audit-and-repair-task"),
        completed(5),
    ];
    let facts = extract(&events, "/logs");
    assert_eq!(facts.provenance, Some("reviewed"));
    assert_eq!(facts.verification_runs, 0);
}

#[test]
fn provenance_is_model_report_when_nothing_verified_or_reviewed() {
    let plain = vec![event(0, 1000, start(serde_json::json!({}))), completed(1)];
    assert_eq!(extract(&plain, "/logs").provenance, Some("model-report"));
    // A terminal model node fed by a tool node is no independent review.
    let program = serde_json::json!({ "workflow": { "nodes": {
        "survey": { "tool": "grep" },
        "write-up": { "model": { "name": "write-up" }, "follows": ["survey"], "terminal": true }
    } } });
    let events = vec![
        event(0, 1000, start(program)),
        node_start(1, "survey", vec![]),
        node_end(2, "survey"),
        node_start(3, "write-up", vec![2]),
        node_end(4, "write-up"),
        completed(5),
    ];
    assert_eq!(extract(&events, "/logs").provenance, Some("model-report"));
}

#[test]
fn provenance_is_absent_for_an_episode_that_did_not_complete() {
    let events = vec![
        event(0, 1000, start(serde_json::json!({}))),
        verification(1, foe_log::VerificationStatus::Accepted, &[]),
        event(2, 1002, EventData::EpisodeEnd { outcome: Outcome::Failed { error: "no".into() } }),
    ];
    assert_eq!(extract(&events, "/logs").provenance, None);
}
