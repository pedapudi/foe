use super::*;

#[test]
fn builtin_coding_uses_low_reasoning_for_gpt_5_6_sol() {
    for provider in ["openai", "openai-codex"] {
        let config =
            builtin_config("task".into(), ModelConfig::new(provider, "gpt-5.6-sol"), None, None, None).unwrap();
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
    let config = builtin_config("task".into(), explicit, None, None, None).unwrap();
    assert_eq!(config.model.as_ref().unwrap().option("reasoning_effort"), Some("high"));
    let audit = config.workflow.as_ref().unwrap().nodes["audit-and-repair-task"].model.as_ref().unwrap();
    assert_eq!(audit.model.as_ref().unwrap().option("reasoning_effort"), Some("high"));

    let config =
        builtin_config("task".into(), ModelConfig::new("anthropic", "claude-opus-5"), None, None, None).unwrap();
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
    let config =
        builtin_config("task".into(), ModelConfig::new("openai-codex", "gpt-5.6-sol"), None, None, None).unwrap();
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

/// docs/design.md "The command line": `--verify` makes `check` available
/// to both built-in episodes and assigns `done_when.verify` to the terminal
/// audit. The audit remains unconditional and owns checked completion.
#[test]
fn builtin_coding_with_verify_makes_terminal_audit_authoritative() {
    use std::os::unix::fs::PermissionsExt;
    let dir = std::env::temp_dir().join(format!("foe-cli-verify-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let script = dir.join("check");
    std::fs::write(&script, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
    let model = ModelConfig::new("anthropic", "claude-opus-5");
    let config = builtin_config("task".into(), model.clone(), None, Some(&script), None).unwrap();
    resolve(&config).expect("the guarded built-in workflow resolves");
    let canonical = script.canonicalize().unwrap();
    assert_eq!(config.tool_defs["check"].exec, canonical);
    assert!(config.tools.iter().any(|t| t == "check"));
    let workflow = config.workflow.as_ref().unwrap();
    let implement = workflow.nodes["implement-task"].model.as_ref().unwrap();
    assert!(implement.tools.iter().any(|t| t == "check"));
    assert_eq!(implement.tool_defs["check"].exec, canonical);
    let audit_node = &workflow.nodes["audit-and-repair-task"];
    assert!(audit_node.skip_when_verified.is_none(), "verified implementation cannot bypass the audit");
    let audit = audit_node.model.as_ref().unwrap();
    assert!(audit.tools.iter().any(|t| t == "check"));
    assert_eq!(audit.tool_defs["check"].exec, canonical);
    assert_eq!(audit.done_when.as_ref().unwrap().verify.as_deref(), Some("check"));
    let done = implement.done_when.as_ref().unwrap();
    assert!(done.verify.is_none(), "implementation claims are not authoritative");
    assert!(done.returns.is_some(), "the typed handoff remains declared");

    let plain = builtin_config("task".into(), model, None, None, None).unwrap();
    assert!(plain.tool_defs.is_empty(), "without --verify the document is unchanged");
    assert!(plain.workflow.as_ref().unwrap().nodes["audit-and-repair-task"].skip_when_verified.is_none());
}

#[test]
fn builtin_coding_selects_an_explicit_sandbox_mode() {
    let model = ModelConfig::new("openai-codex", "gpt-5.6-sol");
    let config = builtin_config("task".into(), model.clone(), None, None, Some("off")).unwrap();
    assert_eq!(serde_json::to_value(config.sandbox.mode).unwrap(), "off");

    let error = builtin_config("task".into(), model, None, None, Some("wide-open")).unwrap_err();
    assert_eq!(error, "--sandbox wide-open: expected best-effort, required, or off");
}

#[test]
fn builtin_coding_selects_an_explicit_service_tier() {
    let options = Options {
        task: Some("task".into()),
        model: Some("openai-codex/gpt-5.6-sol".into()),
        service_tier: Some("priority".into()),
        ..Options::default()
    };
    let config = load_config(&options).unwrap();
    assert_eq!(config.model.as_ref().unwrap().option("service_tier"), Some("priority"));
    let audit = config.workflow.as_ref().unwrap().nodes["audit-and-repair-task"].model.as_ref().unwrap();
    assert_eq!(audit.model.as_ref().unwrap().option("service_tier"), Some("priority"));

    let invalid = Options { service_tier: Some("fastest".into()), ..options };
    assert_eq!(load_config(&invalid).unwrap_err(), "--service-tier fastest: expected default or priority");
}

#[test]
fn explicit_config_owns_its_sandbox_mode() {
    let options =
        Options { config: Some(PathBuf::from("unused.json")), sandbox: Some("off".into()), ..Options::default() };
    let error = load_config(&options).unwrap_err();
    assert_eq!(
        error,
        "--sandbox applies to the built-in coding workflow; a configuration document declares its own behavior"
    );
}

#[test]
fn explicit_config_owns_its_service_tier() {
    let options = Options {
        config: Some(PathBuf::from("unused.json")),
        service_tier: Some("priority".into()),
        ..Options::default()
    };
    let error = load_config(&options).unwrap_err();
    assert_eq!(
        error,
        "--service-tier applies to the built-in coding workflow; a configuration document declares its own behavior"
    );
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
    let config =
        builtin_config("task".into(), ModelConfig::new("anthropic", "claude-opus-5"), None, None, None).unwrap();
    assert_eq!(config.tools, ["read", "grep", "edit", "bash"]);
    for node in config.workflow.as_ref().unwrap().nodes.values() {
        assert!(node.model.as_ref().unwrap().tools.iter().all(|tool| tool != "retrieve"));
    }
    assert!(extra_builtin_specs().iter().any(|spec| spec.name == foe_core::retrieval::NAME));
}
