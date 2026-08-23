use super::*;

#[test]
fn builtin_coding_uses_low_reasoning_for_gpt_5_6_sol() {
    for provider in ["openai", "openai-codex"] {
        let config = builtin_config("task".into(), ModelConfig::new(provider, "gpt-5.6-sol"), None, None).unwrap();
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
    let config = builtin_config("task".into(), explicit, None, None).unwrap();
    assert_eq!(config.model.as_ref().unwrap().option("reasoning_effort"), Some("high"));
    let audit = config.workflow.as_ref().unwrap().nodes["audit-and-repair-task"].model.as_ref().unwrap();
    assert_eq!(audit.model.as_ref().unwrap().option("reasoning_effort"), Some("high"));

    let config = builtin_config("task".into(), ModelConfig::new("anthropic", "claude-opus-5"), None, None).unwrap();
    assert_eq!(config.model.as_ref().unwrap().option("reasoning_effort"), None);
    let audit = config.workflow.as_ref().unwrap().nodes["audit-and-repair-task"].model.as_ref().unwrap();
    assert_eq!(audit.model.as_ref().unwrap().option("reasoning_effort"), None);
}

/// docs/design.md "The command line": a bare task reserves independent
/// implementation and audit episodes, and the audit receives the task and
/// implementation claim in a fresh context.
#[test]
fn builtin_coding_runs_implementation_then_independent_audit() {
    assert_eq!(BUILTIN_IMPLEMENTATION_CALLS, 60);
    assert_eq!(BUILTIN_AUDIT_CALLS, 60);
    let config = builtin_config("task".into(), ModelConfig::new("openai-codex", "gpt-5.6-sol"), None, None).unwrap();
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

/// docs/design.md "The command line": `--verify` wires the built-in
/// workflow's guard — a `check` tool_defs entry with the canonicalized
/// path, `done_when.verify` on the implementation episode, and
/// `skip_when_verified` on the audit node. Without it, the document is
/// unchanged: always audited.
#[test]
fn builtin_coding_with_verify_wires_the_guard() {
    use std::os::unix::fs::PermissionsExt;
    let dir = std::env::temp_dir().join(format!("foe-cli-verify-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let script = dir.join("check");
    std::fs::write(&script, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
    let model = ModelConfig::new("anthropic", "claude-opus-5");
    let config = builtin_config("task".into(), model.clone(), None, Some(&script)).unwrap();
    resolve(&config).expect("the guarded built-in workflow resolves");
    let canonical = script.canonicalize().unwrap();
    assert_eq!(config.tool_defs["check"].exec, canonical);
    assert!(config.tools.iter().any(|t| t == "check"));
    let workflow = config.workflow.as_ref().unwrap();
    let implement = workflow.nodes["implement-task"].model.as_ref().unwrap();
    assert!(implement.tools.iter().any(|t| t == "check"));
    assert_eq!(implement.tool_defs["check"].exec, canonical);
    let done = implement.done_when.as_ref().unwrap();
    assert_eq!(done.verify.as_deref(), Some("check"));
    assert!(done.returns.is_some(), "the typed handoff remains declared");
    assert_eq!(workflow.nodes["audit-and-repair-task"].skip_when_verified.as_deref(), Some("implement-task"));

    let plain = builtin_config("task".into(), model, None, None).unwrap();
    assert!(plain.tool_defs.is_empty(), "without --verify the document is unchanged");
    assert!(plain.workflow.as_ref().unwrap().nodes["audit-and-repair-task"].skip_when_verified.is_none());
}

#[test]
fn builtin_environment_reports_fixed_path_observations_and_their_scope() {
    let text = builtin_environment(Path::new("/work"), |path| path == Path::new("/usr/bin/git"));
    assert!(text.contains("Working directory: /work"));
    assert!(text.contains("git=/usr/bin/git"));
    assert!(text.contains("python3=not found"));
    assert!(text.contains("not-found result covers only the listed standard locations"));
}

#[test]
fn builtin_coding_can_retrieve_shortened_tool_results() {
    let config = builtin_config("task".into(), ModelConfig::new("anthropic", "claude-opus-5"), None, None).unwrap();
    assert_eq!(config.tools, ["read", "grep", "edit", "bash", "retrieve"]);
    for node in config.workflow.as_ref().unwrap().nodes.values() {
        assert!(node.model.as_ref().unwrap().tools.iter().any(|tool| tool == "retrieve"));
    }
    assert!(extra_builtin_specs().iter().any(|spec| spec.name == foe_core::retrieval::NAME));
}
