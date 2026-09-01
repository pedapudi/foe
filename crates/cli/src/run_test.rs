use super::*;

#[test]
fn builtin_coding_uses_low_implementation_and_xhigh_assessment_for_gpt_5_6_sol() {
    for provider in ["openai", "openai-codex"] {
        let config =
            builtin_program_document("task".into(), ModelConfig::new(provider, "gpt-5.6-sol"), None, None, None)
                .unwrap();
        assert_eq!(config.model.as_ref().unwrap().option("reasoning_effort"), Some("low"));
        let workflow = config.workflow.as_ref().unwrap();
        for node in ["assess-task", "repair-task"] {
            let program = workflow.nodes[node].model.as_ref().unwrap();
            assert_eq!(program.model.as_ref().unwrap().option("reasoning_effort"), Some("xhigh"));
        }
    }
}

#[test]
fn builtin_coding_preserves_explicit_reasoning_and_other_models() {
    let mut explicit = ModelConfig::new("openai-codex", "gpt-5.6-sol");
    explicit.options.insert("reasoning_effort".into(), "high".into());
    let config = builtin_program_document("task".into(), explicit, None, None, None).unwrap();
    assert_eq!(config.model.as_ref().unwrap().option("reasoning_effort"), Some("high"));
    let workflow = config.workflow.as_ref().unwrap();
    for node in ["assess-task", "repair-task"] {
        let program = workflow.nodes[node].model.as_ref().unwrap();
        assert_eq!(program.model.as_ref().unwrap().option("reasoning_effort"), Some("high"));
    }

    let config =
        builtin_program_document("task".into(), ModelConfig::new("anthropic", "claude-opus-5"), None, None, None)
            .unwrap();
    assert_eq!(config.model.as_ref().unwrap().option("reasoning_effort"), None);
    for node in config.workflow.as_ref().unwrap().nodes.values() {
        let program = node.model.as_ref().unwrap();
        assert_eq!(program.model.as_ref().map(|m| m.option("reasoning_effort")).unwrap_or(None), None);
    }
}

#[test]
fn builtin_key_file_uses_the_providers_credential_option() {
    let dir = crate::tests::scratch("foe-cli-run", "builtin-credential");
    let credential = dir.join("credential.json");
    std::fs::write(&credential, "{}\n").unwrap();
    let canonical = credential.canonicalize().unwrap().to_string_lossy().into_owned();

    let codex = builtin_program_document(
        "task".into(),
        ModelConfig::new("openai-codex", "gpt-5.6-sol"),
        Some(&credential),
        None,
        None,
    )
    .unwrap();
    assert_eq!(codex.model.as_ref().unwrap().option("token_file"), Some(canonical.as_str()));
    assert_eq!(codex.model.as_ref().unwrap().option("api_key_file"), None);

    let openai = builtin_program_document(
        "task".into(),
        ModelConfig::new("openai", "gpt-5.6-sol"),
        Some(&credential),
        None,
        None,
    )
    .unwrap();
    assert_eq!(openai.model.as_ref().unwrap().option("api_key_file"), Some(canonical.as_str()));
}

/// docs/design.md "The command line": a bare task reserves independent
/// implementation, independent assessment, and conditional repair episodes.
#[test]
fn builtin_coding_runs_implementation_then_conditional_repair() {
    assert_eq!(BUILTIN_IMPLEMENTATION_CALLS, 60);
    assert_eq!(BUILTIN_ASSESSMENT_CALLS, 60);
    assert_eq!(BUILTIN_REPAIR_CALLS, 60);
    let config =
        builtin_program_document("task".into(), ModelConfig::new("openai-codex", "gpt-5.6-sol"), None, None, None)
            .unwrap();
    resolve(&config).expect("the built-in workflow resolves before an episode starts");
    assert_eq!(
        config.budget.model_calls,
        BUILTIN_IMPLEMENTATION_CALLS + BUILTIN_ASSESSMENT_CALLS + BUILTIN_REPAIR_CALLS
    );
    assert_eq!(config.budget.max_episodes, 4);
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
        serde_json::json!(["summary", "changed_paths", "validation", "unresolved_risks", "learned"])
    );
    assert!(implementation_program.instructions["environment"].contains("Fixed-path executable probe"));
    let contract = &implementation_program.instructions["contract"];
    assert!(contract.contains("limit mutations to current filesystem state"));
    assert!(contract.contains("leave it operational"));
    assert!(contract.contains("strongest task-authorized interface"));
    assert!(contract.contains("every completion-critical requirement"));
    let assessment = &workflow.nodes["assess-task"];
    assert_eq!(assessment.follows, ["task", "implement-task"]);
    assert!(!assessment.terminal);
    assert_eq!(
        assessment.branches,
        std::collections::BTreeMap::from([("accept".into(), vec![]), ("repair".into(), vec!["repair-task".into()])])
    );
    let assessment_program = assessment.model.as_ref().unwrap();
    assert_eq!(assessment_program.budget.model_calls, BUILTIN_ASSESSMENT_CALLS);
    assert!(!assessment_program.tools.iter().any(|tool| tool == "edit"));
    let assessment_completion = assessment_program.done_when.as_ref().unwrap().returns.as_ref().unwrap();
    assert_eq!(
        assessment_completion["required"],
        serde_json::json!(["summary", "findings", "validation", "unresolved_risks", "learned"])
    );
    assert_eq!(&assessment_program.instructions["contract"], contract);
    assert!(assessment_program.instructions["role"].contains("without editing task artifacts"));
    assert!(assessment_program.instructions["role"].contains("materially different valid inputs"));

    let repair = &workflow.nodes["repair-task"];
    assert_eq!(repair.follows, ["task", "implement-task", "assess-task"]);
    assert!(repair.terminal);
    let repair_program = repair.model.as_ref().unwrap();
    assert_eq!(repair_program.budget.model_calls, BUILTIN_REPAIR_CALLS);
    let repair_completion = repair_program.done_when.as_ref().unwrap().returns.as_ref().unwrap();
    assert_eq!(repair_completion["properties"]["unresolved_risks"]["maxItems"], 0);
    assert_eq!(repair_completion["required"], completion["required"]);
    assert_eq!(completion["properties"]["validation"]["minItems"], 1);
    // The `learned` completion evidence is bounded: one to eight claims,
    // each citing the successful tool result that supports it.
    let learned = &completion["properties"]["learned"];
    assert_eq!(learned["maxItems"], 8);
    assert_eq!(learned["minItems"], 1);
    assert_eq!(learned["items"]["required"], serde_json::json!(["claim", "seq"]));
    assert_eq!(learned["items"]["additionalProperties"], serde_json::json!(false));
    assert!(completion["required"].as_array().unwrap().contains(&serde_json::json!("learned")));
    assert_eq!(&repair_program.instructions["contract"], contract);
    assert!(repair_program.instructions["role"].contains("every changed path"));
    assert!(repair_program.instructions["role"].contains("Treat every finding and unresolved risk as an obligation"));
}

/// docs/design.md "The command line": `--verify` makes `check` available
/// to every built-in episode and gates both completion branches at the root.
#[test]
fn builtin_coding_with_verify_gates_both_assessment_branches() {
    use std::os::unix::fs::PermissionsExt;
    let dir = crate::tests::scratch("foe-cli-verify", "built-in-checker");
    let script = dir.join("check");
    std::fs::write(&script, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
    let model = ModelConfig::new("anthropic", "claude-opus-5");
    let config = builtin_program_document("task".into(), model.clone(), None, Some(&script), None).unwrap();
    resolve(&config).expect("the guarded built-in workflow resolves");
    let canonical = script.canonicalize().unwrap();
    assert_eq!(config.tool_defs["check"].exec, canonical);
    assert!(config.tools.iter().any(|t| t == "check"));
    let workflow = config.workflow.as_ref().unwrap();
    for node in ["implement-task", "assess-task", "repair-task"] {
        let program = workflow.nodes[node].model.as_ref().unwrap();
        assert!(program.tools.iter().any(|t| t == "check"));
        assert_eq!(program.tool_defs["check"].exec, canonical);
        assert!(program.done_when.as_ref().unwrap().verify.is_none());
    }
    let implement = workflow.nodes["implement-task"].model.as_ref().unwrap();
    let gate = config.done_when.as_ref().unwrap();
    assert_eq!(gate.verify.as_deref(), Some("check"));
    assert_eq!(gate.retries, BUILTIN_VERIFIER_RETRIES);
    assert_eq!(config.budget.max_episodes, BUILTIN_VERIFIER_RETRIES + 4);
    assert_eq!(workflow.nodes["assess-task"].max_fires, Some(BUILTIN_VERIFIER_RETRIES + 1));
    assert_eq!(workflow.nodes["repair-task"].max_fires, Some(BUILTIN_VERIFIER_RETRIES + 1));
    let done = implement.done_when.as_ref().unwrap();
    assert!(done.verify.is_none(), "implementation claims are not authoritative");
    assert!(done.returns.is_some(), "the typed handoff remains declared");

    let plain = builtin_program_document("task".into(), model, None, None, None).unwrap();
    assert!(plain.tool_defs.is_empty(), "without --verify the document is unchanged");
}

#[test]
fn builtin_coding_selects_an_explicit_sandbox_mode() {
    let model = ModelConfig::new("openai-codex", "gpt-5.6-sol");
    let config = builtin_program_document("task".into(), model.clone(), None, None, Some("off")).unwrap();
    assert_eq!(serde_json::to_value(config.sandbox.mode).unwrap(), "off");

    let error = builtin_program_document("task".into(), model, None, None, Some("wide-open")).unwrap_err();
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
    let config = load_program_document(&options).unwrap();
    assert_eq!(config.model.as_ref().unwrap().option("service_tier"), Some("priority"));
    let workflow = config.workflow.as_ref().unwrap();
    for node in ["assess-task", "repair-task"] {
        let program = workflow.nodes[node].model.as_ref().unwrap();
        assert_eq!(program.model.as_ref().unwrap().option("service_tier"), Some("priority"));
    }

    let invalid = Options { service_tier: Some("fastest".into()), ..options };
    assert_eq!(load_program_document(&invalid).unwrap_err(), "--service-tier fastest: expected default or priority");
}

#[test]
fn explicit_config_owns_its_sandbox_mode() {
    let options =
        Options { config: Some(PathBuf::from("unused.json")), sandbox: Some("off".into()), ..Options::default() };
    let error = load_program_document(&options).unwrap_err();
    assert_eq!(
        error,
        "--sandbox applies to the built-in coding workflow; a program document declares its own behavior"
    );
}

#[test]
fn explicit_config_owns_its_service_tier() {
    let options = Options {
        config: Some(PathBuf::from("unused.json")),
        service_tier: Some("priority".into()),
        ..Options::default()
    };
    let error = load_program_document(&options).unwrap_err();
    assert_eq!(
        error,
        "--service-tier applies to the built-in coding workflow; a program document declares its own behavior"
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
        builtin_program_document("task".into(), ModelConfig::new("anthropic", "claude-opus-5"), None, None, None)
            .unwrap();
    assert_eq!(config.tools, ["read", "grep", "edit", "bash"]);
    for node in config.workflow.as_ref().unwrap().nodes.values() {
        assert!(node.model.as_ref().unwrap().tools.iter().all(|tool| tool != "retrieve"));
    }
    assert!(extra_builtin_specs().iter().any(|spec| spec.name == foe_core::retrieval::NAME));
}

/// docs/design.md "Program construction": a child resumed without its
/// inherited executable descriptors validates the recorded identity.
#[test]
fn independently_resumed_child_rejects_a_changed_executable() {
    let dir = std::env::temp_dir().join(format!("foe-cli-launch-gap-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let tool = dir.join("tool");
    std::fs::write(&tool, "first").unwrap();
    std::fs::set_permissions(&tool, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    let config: ProgramDocument = serde_json::from_value(serde_json::json!({
        "version": 3,
        "name": "child",
        "instructions": {"role": "test"},
        "tools": ["probe"],
        "tool_defs": {"probe": {"exec": tool, "description": "test"}},
        "grants": {"read": [dir]},
        "budget": {"model_calls": 4},
        "sandbox": {"mode": "off"},
        "task": "test"
    }))
    .unwrap();
    let expected = identity(&resolve(&config).unwrap()).unwrap().hash;
    let config_path = dir.join("config.json");
    std::fs::write(&config_path, serde_json::to_vec(&config).unwrap()).unwrap();
    std::fs::write(
        dir.join("lineage.json"),
        serde_json::to_vec(&serde_json::json!({
            "episode_id": "ep_child",
            "parent_id": "ep_parent",
            "team_id": "ep_parent",
            "expected_program_identity": expected,
            "effective_budget": {"model_calls": 2}
        }))
        .unwrap(),
    )
    .unwrap();
    std::fs::write(&tool, "second").unwrap();
    let error = run(Options {
        config: Some(config_path),
        log_dir: Some(dir.clone()),
        host: true,
        headless: true,
        ..Options::default()
    })
    .unwrap_err();
    assert!(error.contains("expected program identity"), "{error}");
    assert!(!dir.join(foe_log::fold::LOG_FILE).exists(), "identity is checked before episode/start");
}

/// docs/design.md "The command line": a spawned episode resumes under the
/// allowance and identity in its start event when launch metadata is absent
/// or has been changed.
#[test]
fn child_resume_uses_recorded_allowance_and_identity() {
    for (case, metadata) in [
        ("missing", None),
        (
            "changed",
            Some(serde_json::json!({
                "episode_id": "ep_changed",
                "expected_program_identity": "sha256:changed",
                "effective_budget": {"model_calls": 99}
            })),
        ),
    ] {
        let dir = std::env::temp_dir().join(format!("foe-cli-resume-evidence-{case}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let mut writer = foe_log::append::Writer::create(&dir, None).unwrap();
        writer
            .append(EventData::EpisodeStart(EpisodeStart {
                id: "ep_child".into(),
                parent_id: Some("ep_parent".into()),
                fork_origin: Some(foe_log::ForkOrigin { episode_id: "ep_source".into(), seq: 1 }),
                team_id: Some("ep_parent".into()),
                program: serde_json::json!({}),
                identity: "sha256:recorded".into(),
                task: "task".into(),
                runtime: runtime_info(),
                sandbox: foe_log::SandboxInfo { mode: foe_log::SandboxMode::Off, landlock_abi: 0 },
                effective_budget: Some(serde_json::from_value(serde_json::json!({ "model_calls": 2 })).unwrap()),
            }))
            .unwrap();
        writer.append(EventData::SeedEnd {}).unwrap();
        writer.sync().unwrap();
        drop(writer);
        if let Some(metadata) = metadata {
            std::fs::write(dir.join("lineage.json"), serde_json::to_vec(&metadata).unwrap()).unwrap();
        }

        let (_, lineage) = resume(&dir, "sha256:recorded").unwrap();
        assert_eq!(lineage.episode_id, "ep_child");
        assert_eq!(lineage.expected_program_identity.as_deref(), Some("sha256:recorded"));
        assert_eq!(lineage.effective_budget.unwrap().model_calls, 2);
        let error = resume(&dir, "sha256:different").unwrap_err();
        assert!(error.contains("log records identity sha256:recorded"), "{error}");
    }
}

/// docs/design.md "The command line": an ordinary prepared fork keeps its
/// source program in `episode/start`, so resume accepts the fork's program.
#[test]
fn ordinary_prepared_fork_retains_source_identity_exemption() {
    let dir = std::env::temp_dir().join(format!("foe-cli-resume-ordinary-fork-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let mut writer = foe_log::append::Writer::create(&dir, None).unwrap();
    writer
        .append(EventData::EpisodeStart(EpisodeStart {
            id: "ep_fork".into(),
            parent_id: None,
            fork_origin: Some(foe_log::ForkOrigin { episode_id: "ep_source".into(), seq: 1 }),
            team_id: None,
            program: serde_json::json!({}),
            identity: "sha256:source".into(),
            task: "task".into(),
            runtime: runtime_info(),
            sandbox: foe_log::SandboxInfo { mode: foe_log::SandboxMode::Off, landlock_abi: 0 },
            effective_budget: Some(serde_json::from_value(serde_json::json!({ "model_calls": 2 })).unwrap()),
        }))
        .unwrap();
    writer.append(EventData::SeedEnd {}).unwrap();
    writer.sync().unwrap();
    drop(writer);

    let (_, lineage) = resume(&dir, "sha256:fork-program").unwrap();
    assert!(lineage.expected_program_identity.is_none());
    assert_eq!(lineage.effective_budget.unwrap().model_calls, 2);
}
