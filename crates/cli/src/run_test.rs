use super::*;

#[test]
fn builtin_coding_uses_low_reasoning_for_gpt_5_6_sol() {
    for provider in ["openai", "openai-codex"] {
        let config = builtin_config("task".into(), ModelConfig::new(provider, "gpt-5.6-sol"), None).unwrap();
        assert_eq!(config.model.as_ref().unwrap().option("reasoning_effort"), Some("low"));
        let workflow = config.workflow.as_ref().unwrap();
        let audit = workflow.nodes["audit-and-repair-task"].model.as_ref().unwrap();
        assert_eq!(audit.model.as_ref().unwrap().option("reasoning_effort"), Some("high"));
    }
}

#[test]
fn builtin_coding_preserves_explicit_reasoning_and_other_models() {
    let mut explicit = ModelConfig::new("openai-codex", "gpt-5.6-sol");
    explicit.options.insert("reasoning_effort".into(), "high".into());
    let config = builtin_config("task".into(), explicit, None).unwrap();
    assert_eq!(config.model.as_ref().unwrap().option("reasoning_effort"), Some("high"));
    let audit = config.workflow.as_ref().unwrap().nodes["audit-and-repair-task"].model.as_ref().unwrap();
    assert_eq!(audit.model.as_ref().unwrap().option("reasoning_effort"), Some("high"));

    let config = builtin_config("task".into(), ModelConfig::new("anthropic", "claude-opus-5"), None).unwrap();
    assert_eq!(config.model.as_ref().unwrap().option("reasoning_effort"), None);
    let audit = config.workflow.as_ref().unwrap().nodes["audit-and-repair-task"].model.as_ref().unwrap();
    assert_eq!(audit.model.as_ref().unwrap().option("reasoning_effort"), None);
}

/// docs/design.md "The command line": a bare task reserves independent
/// implementation and audit episodes, and the audit receives the task and
/// implementation claim in a fresh context.
#[test]
fn builtin_coding_runs_implementation_then_independent_audit() {
    let config = builtin_config("task".into(), ModelConfig::new("openai-codex", "gpt-5.6-sol"), None).unwrap();
    resolve(&config).expect("the built-in workflow resolves before an episode starts");
    assert_eq!(config.budget.model_calls, BUILTIN_IMPLEMENTATION_CALLS + BUILTIN_AUDIT_CALLS);
    assert_eq!(config.budget.max_concurrent, 1);
    let workflow = config.workflow.unwrap();
    let implementation = &workflow.nodes["implement-task"];
    assert_eq!(implementation.follows, ["task"]);
    assert!(!implementation.terminal);
    let implementation_program = implementation.model.as_ref().unwrap();
    assert_eq!(implementation_program.budget.model_calls, BUILTIN_IMPLEMENTATION_CALLS);
    let completion = implementation_program.done_when.as_ref().unwrap().returns.as_ref().unwrap();
    assert_eq!(
        completion["required"],
        serde_json::json!(["summary", "changed_paths", "validation", "unresolved_risks"])
    );
    assert!(implementation_program.instructions["environment"].contains("Fixed-path executable probe"));
    let audit = &workflow.nodes["audit-and-repair-task"];
    assert_eq!(audit.follows, ["task", "implement-task"]);
    assert!(audit.terminal);
    let audit_program = audit.model.as_ref().unwrap();
    assert_eq!(audit_program.budget.model_calls, BUILTIN_AUDIT_CALLS);
    assert_eq!(audit_program.done_when.as_ref().unwrap().returns.as_ref().unwrap(), completion);
    assert_eq!(completion["properties"]["validation"]["minItems"], 1);
    assert!(audit_program.instructions["role"].contains("every path changed by either episode"));
}

#[test]
fn builtin_environment_reports_fixed_path_observations_and_their_scope() {
    let text = builtin_environment(Path::new("/work"), |path| path == Path::new("/usr/bin/git"));
    assert!(text.contains("Working directory: /work"));
    assert!(text.contains("git=/usr/bin/git"));
    assert!(text.contains("python3=not found"));
    assert!(text.contains("not-found result covers only the listed standard locations"));
}
