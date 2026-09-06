use super::*;

/// The built-in coding document under a model, which is what a run of
/// `--config builtin:coding` and a run with no `--config` both build.
fn coding(
    task: String,
    model: ModelConfig,
    verify: Option<&Path>,
    sandbox: Option<&str>,
) -> Result<ContractDocument, String> {
    builtin_contract_document(BUILTIN_CODING, task, Some(model), verify, sandbox)
}

#[test]
fn builtin_coding_uses_low_implementation_and_xhigh_assessment_for_gpt_5_6_sol() {
    for provider in ["openai", "openai-codex"] {
        let config = coding("task".into(), ModelConfig::new(provider, "gpt-5.6-sol"), None, None).unwrap();
        assert_eq!(config.model.as_ref().unwrap().option("reasoning_effort"), Some("low"));
        let workflow = config.workflow.as_ref().unwrap();
        for node in ["assess-task", "repair-task"] {
            let contract = workflow.nodes[node].model.as_ref().unwrap();
            assert_eq!(contract.model.as_ref().unwrap().option("reasoning_effort"), Some("xhigh"));
        }
    }
}

#[test]
fn builtin_coding_preserves_explicit_reasoning_and_other_models() {
    let mut explicit = ModelConfig::new("openai-codex", "gpt-5.6-sol");
    explicit.options.insert("reasoning_effort".into(), "high".into());
    let config = coding("task".into(), explicit, None, None).unwrap();
    assert_eq!(config.model.as_ref().unwrap().option("reasoning_effort"), Some("high"));
    let workflow = config.workflow.as_ref().unwrap();
    for node in ["assess-task", "repair-task"] {
        let contract = workflow.nodes[node].model.as_ref().unwrap();
        assert_eq!(contract.model.as_ref().unwrap().option("reasoning_effort"), Some("high"));
    }

    let config = coding("task".into(), ModelConfig::new("anthropic", "claude-opus-5"), None, None).unwrap();
    assert_eq!(config.model.as_ref().unwrap().option("reasoning_effort"), None);
    for node in config.workflow.as_ref().unwrap().nodes.values() {
        let contract = node.model.as_ref().unwrap();
        assert_eq!(contract.model.as_ref().map(|m| m.option("reasoning_effort")).unwrap_or(None), None);
    }
}

/// docs/design.md "The command line": the built-in document carries the
/// credential options of the model block it is given, so a credential file
/// recorded by `foe login` reaches the run through that block.
#[test]
fn builtin_coding_carries_the_credential_options_of_its_model_block() {
    let mut model = ModelConfig::new("openai", "gpt-5.6-sol");
    model.options.insert("api_key_file".into(), "/keys/openai.json".into());
    let document = coding("task".into(), model, None, None).unwrap();
    assert_eq!(document.model.as_ref().unwrap().option("api_key_file"), Some("/keys/openai.json"));
}

/// docs/design.md "The command line": a bare task reserves independent
/// implementation, independent assessment, and conditional repair episodes.
#[test]
fn builtin_coding_runs_implementation_then_conditional_repair() {
    assert_eq!(BUILTIN_IMPLEMENTATION_CALLS, 60);
    assert_eq!(BUILTIN_ASSESSMENT_CALLS, 60);
    assert_eq!(BUILTIN_REPAIR_CALLS, 60);
    let config = coding("task".into(), ModelConfig::new("openai-codex", "gpt-5.6-sol"), None, None).unwrap();
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
    let implementation_contract = implementation.model.as_ref().unwrap();
    assert_eq!(implementation_contract.budget.model_calls, BUILTIN_IMPLEMENTATION_CALLS);
    let completion = implementation_contract.done_when.as_ref().unwrap().returns.as_ref().unwrap();
    assert_eq!(
        completion["required"],
        serde_json::json!(["summary", "changed_paths", "validation", "unresolved_risks", "learned"])
    );
    assert!(implementation_contract.instructions["environment"].contains("Fixed-path executable probe"));
    let contract = &implementation_contract.instructions["contract"];
    assert!(contract.contains("limit mutations to current filesystem state"));
    assert!(contract.contains("leave it operational"));
    assert!(contract.contains("strongest interface the task permits"));
    assert!(contract.contains("every completion-critical requirement"));
    let assessment = &workflow.nodes["assess-task"];
    assert_eq!(assessment.follows, ["task", "implement-task"]);
    assert!(!assessment.terminal);
    assert_eq!(
        assessment.branches,
        std::collections::BTreeMap::from([("accept".into(), vec![]), ("repair".into(), vec!["repair-task".into()])])
    );
    let assessment_contract = assessment.model.as_ref().unwrap();
    assert_eq!(assessment_contract.budget.model_calls, BUILTIN_ASSESSMENT_CALLS);
    assert!(!assessment_contract.tools.iter().any(|tool| tool == "edit"));
    let assessment_completion = assessment_contract.done_when.as_ref().unwrap().returns.as_ref().unwrap();
    assert_eq!(
        assessment_completion["required"],
        serde_json::json!(["summary", "findings", "validation", "unresolved_risks", "learned"])
    );
    assert_eq!(&assessment_contract.instructions["contract"], contract);
    assert!(assessment_contract.instructions["role"].contains("without editing task artifacts"));
    assert!(assessment_contract.instructions["role"].contains("materially different valid inputs"));

    let repair = &workflow.nodes["repair-task"];
    assert_eq!(repair.follows, ["task", "implement-task", "assess-task"]);
    assert!(repair.terminal);
    let repair_contract = repair.model.as_ref().unwrap();
    assert_eq!(repair_contract.budget.model_calls, BUILTIN_REPAIR_CALLS);
    let repair_completion = repair_contract.done_when.as_ref().unwrap().returns.as_ref().unwrap();
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
    assert_eq!(&repair_contract.instructions["contract"], contract);
    assert!(repair_contract.instructions["role"].contains("every changed path"));
    assert!(repair_contract.instructions["role"].contains("Treat every finding and unresolved risk as an obligation"));
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
    let config = coding("task".into(), model.clone(), Some(&script), None).unwrap();
    resolve(&config).expect("the guarded built-in workflow resolves");
    let canonical = script.canonicalize().unwrap();
    assert_eq!(config.tool_defs["check"].exec, canonical);
    assert!(config.tools.iter().any(|t| t == "check"));
    let workflow = config.workflow.as_ref().unwrap();
    for node in ["implement-task", "assess-task", "repair-task"] {
        let contract = workflow.nodes[node].model.as_ref().unwrap();
        assert!(contract.tools.iter().any(|t| t == "check"));
        assert_eq!(contract.tool_defs["check"].exec, canonical);
        assert!(contract.done_when.as_ref().unwrap().verify.is_none());
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

    let plain = coding("task".into(), model, None, None).unwrap();
    assert!(plain.tool_defs.is_empty(), "without --verify the document is unchanged");
}

#[test]
fn builtin_coding_selects_an_explicit_sandbox_mode() {
    let model = ModelConfig::new("openai-codex", "gpt-5.6-sol");
    let config = coding("task".into(), model.clone(), None, Some("off")).unwrap();
    assert_eq!(serde_json::to_value(config.sandbox.mode).unwrap(), "off");

    let error = coding("task".into(), model, None, Some("wide-open")).unwrap_err();
    assert_eq!(error, "--sandbox wide-open: expected best-effort, required, or off");
}

/// The command line carries the tier the caller typed into every episode
/// of the built-in workflow. The provider table judges the value, so the
/// command line refuses none of them.
#[test]
fn builtin_coding_selects_an_explicit_service_tier() {
    let options = Options {
        task: Some("task".into()),
        model: Some("openai-codex/gpt-5.6-sol".into()),
        service_tier: Some("priority".into()),
        ..Options::default()
    };
    let (config, _) = load_contract_document(&options).unwrap();
    assert_eq!(config.model.as_ref().unwrap().option("service_tier"), Some("priority"));
    let workflow = config.workflow.as_ref().unwrap();
    for node in ["assess-task", "repair-task"] {
        let contract = workflow.nodes[node].model.as_ref().unwrap();
        assert_eq!(contract.model.as_ref().unwrap().option("service_tier"), Some("priority"));
    }

    let other = Options { service_tier: Some("flex".into()), ..options };
    let (config, _) = load_contract_document(&other).unwrap();
    assert_eq!(config.model.as_ref().unwrap().option("service_tier"), Some("flex"));
}

#[test]
fn explicit_config_owns_its_sandbox_mode() {
    let options = Options { config: Some("unused.json".into()), sandbox: Some("off".into()), ..Options::default() };
    let error = load_contract_document(&options).unwrap_err();
    assert_eq!(error, "--sandbox applies to the built-in coding workflow; unused.json declares its own behavior");
}

/// A contract document, with the `model` block given when there is one.
/// The return value is the `--config` value naming it.
fn contract_document_file(dir: &Path, model: Option<serde_json::Value>) -> String {
    let mut value = serde_json::json!({
        "version": 4,
        "name": "document",
        "instructions": {"role": "test"},
        "tools": [],
        "grants": {"read": [dir]},
        "budget": {"model_calls": 1},
        "sandbox": {"mode": "off"},
        "task": "test"
    });
    if let Some(model) = model {
        value["model"] = model;
    }
    let path = dir.join("config.json");
    std::fs::write(&path, serde_json::to_vec(&value).unwrap()).unwrap();
    path.to_string_lossy().into_owned()
}

/// docs/design.md "The command line": a document that declares no `model`
/// block takes one from the model options, exactly as the built-in
/// document does, and stays without one when they are absent.
#[test]
fn a_document_without_a_model_block_takes_the_command_line_model() {
    let dir = crate::tests::scratch("foe-cli-run", "model-less-document");
    let credential = dir.join("credential.json");
    std::fs::write(&credential, "{}\n").unwrap();
    let path = contract_document_file(dir.as_ref(), None);

    let options = Options {
        config: Some(path.clone()),
        model: Some("openai/gpt-5.6-sol".into()),
        service_tier: Some("flex".into()),
        ..Options::default()
    };
    let (config, _) = load_contract_document(&options).unwrap();
    let model = config.model.as_ref().unwrap();
    assert_eq!((model.provider.as_str(), model.model.as_str()), ("openai", "gpt-5.6-sol"));
    assert_eq!(model.option("service_tier"), Some("flex"));

    let none_given = Options { config: Some(path), ..Options::default() };
    assert!(load_contract_document(&none_given).unwrap().0.model.is_none(), "the document still names no model");
}

/// A document that declares a `model` block owns the model, so each of the
/// two options that would supply one is refused.
#[test]
fn explicit_config_owns_its_model_options() {
    let dir = crate::tests::scratch("foe-cli-run", "document-model-block");
    let path = contract_document_file(dir.as_ref(), Some(serde_json::json!({"provider": "openai", "model": "m"})));
    let given = [
        ("--model", Options { model: Some("anthropic/claude-opus-5".into()), ..Options::default() }),
        ("--service-tier", Options { service_tier: Some("priority".into()), ..Options::default() }),
    ];
    for (option, options) in given {
        let options = Options { config: Some(path.clone()), ..options };
        let error = load_contract_document(&options).unwrap_err();
        assert_eq!(error, format!("{option}: the contract document declares its own `model` block"));
    }
}

/// docs/design.md "The command line": `--config` takes the name of a
/// document the binary carries beside a file path, and a name the binary
/// does not carry is refused with the names it carries.
#[test]
fn config_takes_a_built_in_name_beside_a_file_path() {
    let named = contract_source("builtin:coding").unwrap();
    assert!(matches!(named, ContractSource::Builtin("coding")));
    assert_eq!(named.describe(), "builtin:coding");
    let file = contract_source("/tmp/contract.json").unwrap();
    assert!(matches!(&file, ContractSource::File(path) if path == Path::new("/tmp/contract.json")));
    assert_eq!(file.describe(), "/tmp/contract.json");
    assert_eq!(
        contract_source("builtin:parser").unwrap_err(),
        "--config builtin:parser: no built-in document has that name; the built-in documents are builtin:coding"
    );
}

/// A built-in document has no task of its own, so a command line naming one
/// without a task is refused, and the refusal states where the task comes
/// from.
#[test]
fn a_built_in_name_without_a_task_is_refused() {
    let options = Options { config: Some("builtin:coding".into()), ..Options::default() };
    assert_eq!(load_contract_document(&options).unwrap_err(), USAGE_BUILTIN);
    assert_eq!(USAGE_BUILTIN, "a task is required: a built-in document takes the task from the command line");
    assert_eq!(load_contract_document(&Options::default()).unwrap_err(), USAGE_BARE);
}

/// docs/design.md "The command line": `--config builtin:coding` runs the
/// document that a command line omitting `--config` runs, so the two
/// documents and their fingerprints are one.
#[test]
fn the_built_in_name_and_the_omitted_option_select_one_document() {
    let options = |config: Option<&str>| Options {
        task: Some("task".into()),
        config: config.map(str::to_string),
        model: Some("anthropic/claude-opus-5".into()),
        ..Options::default()
    };
    let (named, _) = load_contract_document(&options(Some("builtin:coding"))).unwrap();
    let (omitted, _) = load_contract_document(&options(None)).unwrap();
    assert_eq!(serde_json::to_value(&named).unwrap(), serde_json::to_value(&omitted).unwrap());
    let hash = |document: &ContractDocument| fingerprint(&resolve(document).unwrap()).unwrap().hash;
    assert_eq!(hash(&named), hash(&omitted));
}

/// docs/design.md "The command line": `--verify` and `--sandbox` configure
/// a built-in document under its name exactly as they configure it when
/// `--config` is absent, and a document in a file states that behavior in
/// its own keys. `--service-tier` reaches the `model` block of the named
/// document as it reaches the block of the omitted one.
#[test]
fn the_run_options_of_the_built_in_workflow_apply_under_its_name() {
    use std::os::unix::fs::PermissionsExt;
    let dir = crate::tests::scratch("foe-cli-run", "named-run-options");
    let script = dir.join("check");
    std::fs::write(&script, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
    let options = |verify, sandbox, service_tier: Option<&str>| Options {
        task: Some("task".into()),
        config: Some("builtin:coding".into()),
        model: Some("openai-codex/gpt-5.6-sol".into()),
        verify,
        sandbox,
        service_tier: service_tier.map(str::to_string),
        ..Options::default()
    };
    let document =
        load_contract_document(&options(Some(script.clone()), Some("off".into()), Some("priority"))).unwrap().0;
    assert_eq!(document.done_when.as_ref().unwrap().verify.as_deref(), Some("check"));
    assert_eq!(document.tool_defs["check"].exec, script.canonicalize().unwrap());
    assert_eq!(serde_json::to_value(document.sandbox.mode).unwrap(), "off");
    assert_eq!(document.model.as_ref().unwrap().option("service_tier"), Some("priority"));

    let refused = Options { config: Some("unused.json".into()), ..options(Some(script), None, None) };
    assert_eq!(
        load_contract_document(&refused).unwrap_err(),
        "--verify applies to the built-in coding workflow; unused.json declares its own behavior"
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
fn builtin_coding_declares_its_general_shell_command_surface() {
    let config = coding("task".into(), ModelConfig::new("anthropic", "claude-opus-5"), None, None).unwrap();
    let expected: Vec<PathBuf> = BUILTIN_EXECUTE_ROOTS.iter().map(PathBuf::from).collect();
    assert!(expected.iter().any(|root| Path::new("/usr/bin/python3").starts_with(root)));
    assert_eq!(config.grants.execute, expected);
    for node in config.workflow.as_ref().unwrap().nodes.values() {
        assert_eq!(node.model.as_ref().unwrap().grants.execute, expected);
        assert!(node.model.as_ref().unwrap().tools.iter().any(|tool| tool == "bash"));
        assert!(node.model.as_ref().unwrap().tools.iter().all(|tool| tool != foe_core::COMPOSING_TOOL));
    }
}

#[test]
fn builtin_coding_can_retrieve_shortened_tool_results() {
    let config = coding("task".into(), ModelConfig::new("anthropic", "claude-opus-5"), None, None).unwrap();
    assert_eq!(config.tools, ["read", "grep", "edit", "bash"]);
    for node in config.workflow.as_ref().unwrap().nodes.values() {
        assert!(node.model.as_ref().unwrap().tools.iter().all(|tool| tool != "retrieve"));
    }
    assert!(extra_builtin_specs().iter().any(|spec| spec.name == foe_core::retrieval::NAME));
}

/// docs/config.md `done_when`: an invalid host verifier is rejected before
/// the episode records its start event.
#[test]
fn invalid_host_verifier_schema_starts_no_episode() {
    let dir = crate::tests::scratch("foe-cli-run", "invalid-verifier");
    let config_path = dir.join("config.json");
    std::fs::write(
        &config_path,
        serde_json::to_vec(&serde_json::json!({
            "version": 4,
            "name": "invalid-verifier",
            "instructions": {"role": "test"},
            "tools": ["check"],
            "host_tools": {"check": {
                "description": "check", "effect": "pure",
                "params": {"type": "object", "properties": {}}
            }},
            "grants": {"read": [dir.to_path_buf()]},
            "budget": {"model_calls": 1},
            "done_when": {"verify": "check"},
            "sandbox": {"mode": "off"},
            "task": "test"
        }))
        .unwrap(),
    )
    .unwrap();
    let error = run(Options {
        config: Some(config_path.to_string_lossy().into_owned()),
        log_dir: Some(dir.to_path_buf()),
        host: true,
        viewer: Viewer::Off,
        ..Options::default()
    })
    .unwrap_err();
    assert!(error.contains("done_when.verify") && error.contains("found 0"), "{error}");
    let created =
        std::fs::read_dir(&dir).unwrap().flatten().map(|entry| entry.path()).find(|path| path.is_dir()).unwrap();
    assert!(std::fs::read(created.join(foe_log::fold::LOG_FILE)).unwrap().is_empty());
}

/// docs/viewer.md "Terminal conversation": the terminal display chooses what
/// standard output shows and composes with every `--viewer` value; `off` and
/// `--host` are the running forms without a browser viewer.
#[test]
fn conversation_composes_with_every_viewer_value() {
    for viewer in [Viewer::Open, Viewer::Serve] {
        assert!(serves_viewer(&Options { conversation: true, viewer, ..Options::default() }));
    }
    assert!(!serves_viewer(&Options { conversation: true, viewer: Viewer::Off, ..Options::default() }));
    assert!(!serves_viewer(&Options { host: true, ..Options::default() }));
    assert!(serves_viewer(&Options::default()), "a run opens the viewer without being asked");
    assert_eq!(Viewer::parse("serve"), Ok(Viewer::Serve));
    assert_eq!(Viewer::parse("watch"), Err("--viewer watch: expected open, serve, or off".into()));
}

/// docs/design.md "The command line": a `--from DIR@SEQ` run whose task the
/// source log recorded reruns the copied conversation from the boundary, so
/// the seeded log carries no directive of its own; every other task is
/// appended after `seed/end` as a live `system` item.
#[test]
fn a_fork_appends_every_task_except_the_one_the_source_recorded() {
    let dir = crate::tests::scratch("foe-cli-run", "fork-directive");
    let source = dir.join("source");
    std::fs::create_dir(&source).unwrap();
    let start = serde_json::json!({ "seq": 0, "time": 1, "type": "episode/start", "data": {
        "id": "ep_source", "parent_id": null, "fork_origin": null, "team_id": null,
        "contract": {}, "contract_fingerprint": "sha256:source", "task": "the recorded task",
        "runtime": { "version": "0", "build": "unknown" },
        "sandbox": { "mode": "off", "landlock_abi": 0, "resolved_permissions": {},
            "process_boundary": { "kind": "process-group", "subtree_cleanup": "observational" } } } });
    let item = serde_json::json!({ "seq": 1, "time": 1, "type": "inbox/item", "data": {
        "source": "task", "content": [{ "type": "text", "text": "the recorded task" }],
        "from": null, "message_id": null } });
    std::fs::write(source.join(foe_log::fold::LOG_FILE), format!("{start}\n{item}\n")).unwrap();

    let recorded = Task { text: "the recorded task".into(), recorded: true };
    assert_eq!(recorded.directive(), None);
    let (reran, _, note) = fork(&source, 2, Some(&dir.join("rerun")), recorded.directive()).unwrap();
    assert!(note.unwrap().contains("fork of"), "the mode is announced");
    let events = foe_log::fold::read_all(&reran).unwrap();
    assert!(matches!(events.last().unwrap().data, EventData::SeedEnd {}), "no directive follows the copied prefix");

    let given = Task { text: "a new task".into(), recorded: false };
    let (directed, _, _) = fork(&source, 2, Some(&dir.join("directed")), given.directive()).unwrap();
    let events = foe_log::fold::read_all(&directed).unwrap();
    let EventData::InboxItem(item) = &events.last().unwrap().data else { panic!("the task is appended") };
    assert_eq!(item.source, InboxSource::System);
    assert_eq!(item.content, vec![ContentBlock::Text { text: "a new task".into() }]);

    // The parent of episode directories holds no log of its own, and is
    // refused by the name of the file it lacks, before anything is created.
    let parent = fork(&dir, 2, Some(&dir.join("misnamed")), given.directive()).unwrap_err();
    assert!(parent.contains(&format!("{} does not exist", dir.join(foe_log::fold::LOG_FILE).display())), "{parent}");
    assert!(parent.contains("`foe: log PATH`"), "the refusal names the line a run prints: {parent}");
    assert_eq!(source_state(&dir).unwrap_err(), parent, "the same rule applies without a boundary");
    assert!(!dir.join("misnamed").exists());
}

/// docs/design.md "Contract construction": a child resumed without its
/// inherited executable descriptors validates the recorded fingerprint.
#[test]
fn independently_resumed_child_rejects_a_changed_executable() {
    let dir = std::env::temp_dir().join(format!("foe-cli-launch-gap-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let tool = dir.join("tool");
    std::fs::write(&tool, "first").unwrap();
    std::fs::set_permissions(&tool, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    let config: ContractDocument = serde_json::from_value(serde_json::json!({
        "version": 4,
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
    let expected = fingerprint(&resolve(&config).unwrap()).unwrap().hash;
    let config_path = dir.join("config.json");
    std::fs::write(&config_path, serde_json::to_vec(&config).unwrap()).unwrap();
    std::fs::write(
        dir.join("child-launch.json"),
        serde_json::to_vec(&serde_json::json!({
            "episode_id": "ep_child",
            "parent_id": "ep_parent",
            "team_id": "ep_parent",
            "expected_contract_fingerprint": expected,
            "effective_budget": {"model_calls": 2}
        }))
        .unwrap(),
    )
    .unwrap();
    std::fs::write(&tool, "second").unwrap();
    let error = run(Options {
        config: Some(config_path.to_string_lossy().into_owned()),
        log_dir: Some(dir.clone()),
        host: true,
        viewer: Viewer::Off,
        ..Options::default()
    })
    .unwrap_err();
    assert!(error.contains("expected contract fingerprint"), "{error}");
    assert!(!dir.join(foe_log::fold::LOG_FILE).exists(), "fingerprint is checked before episode/start");
}

/// docs/design.md "The command line": a spawned episode resumes under the
/// allowance and fingerprint in its start event when launch metadata is absent
/// or has been changed.
#[test]
fn child_resume_uses_recorded_allowance_and_fingerprint() {
    for (case, metadata) in [
        ("missing", None),
        (
            "changed",
            Some(serde_json::json!({
                "episode_id": "ep_changed",
                "expected_contract_fingerprint": "sha256:changed",
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
                contract: serde_json::json!({}),
                contract_fingerprint: "sha256:recorded".into(),
                task: "task".into(),
                runtime: runtime_info(),
                sandbox: foe_log::SandboxInfo {
                    mode: foe_log::SandboxMode::Off,
                    landlock_abi: 0,
                    resolved_permissions: Default::default(),
                    process_boundary: Default::default(),
                },
                effective_budget: Some(serde_json::from_value(serde_json::json!({ "model_calls": 2 })).unwrap()),
            }))
            .unwrap();
        writer.append(EventData::SeedEnd {}).unwrap();
        writer.sync().unwrap();
        drop(writer);
        if let Some(metadata) = metadata {
            std::fs::write(dir.join("child-launch.json"), serde_json::to_vec(&metadata).unwrap()).unwrap();
        }

        let (_, launch, _) = resume(&dir, "sha256:recorded").unwrap();
        assert_eq!(launch.episode_id, "ep_child");
        assert_eq!(launch.expected_contract_fingerprint.as_deref(), Some("sha256:recorded"));
        assert_eq!(launch.effective_budget.unwrap().model_calls, 2);
        let error = resume(&dir, "sha256:different").unwrap_err();
        assert!(error.contains("log records fingerprint sha256:recorded"), "{error}");
    }
}

/// docs/design.md "The command line": an ordinary prepared fork keeps its
/// source contract in `episode/start`, so resume accepts the fork's contract.
#[test]
fn ordinary_prepared_fork_retains_source_fingerprint_exemption() {
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
            contract: serde_json::json!({}),
            contract_fingerprint: "sha256:source".into(),
            task: "task".into(),
            runtime: runtime_info(),
            sandbox: foe_log::SandboxInfo {
                mode: foe_log::SandboxMode::Off,
                landlock_abi: 0,
                resolved_permissions: Default::default(),
                process_boundary: Default::default(),
            },
            effective_budget: Some(serde_json::from_value(serde_json::json!({ "model_calls": 2 })).unwrap()),
        }))
        .unwrap();
    writer.append(EventData::SeedEnd {}).unwrap();
    writer.sync().unwrap();
    drop(writer);

    let (_, launch, _) = resume(&dir, "sha256:fork-contract").unwrap();
    assert!(launch.expected_contract_fingerprint.is_none());
    assert_eq!(launch.effective_budget.unwrap().model_calls, 2);
}

/// docs/log-format.md "Seeding": a destination without seed/end cannot resume.
#[test]
fn resume_refuses_an_incomplete_seed_without_changing_its_log() {
    let dir = crate::tests::scratch("foe-cli-resume", "incomplete-seed");
    let mut writer = foe_log::append::Writer::create(&dir, None).unwrap();
    writer
        .append(EventData::EpisodeStart(EpisodeStart {
            id: "ep_fork".into(),
            parent_id: None,
            fork_origin: Some(foe_log::ForkOrigin { episode_id: "ep_source".into(), seq: 1 }),
            team_id: None,
            contract: serde_json::json!({}),
            contract_fingerprint: "sha256:source".into(),
            task: "task".into(),
            runtime: runtime_info(),
            sandbox: foe_log::SandboxInfo {
                mode: foe_log::SandboxMode::Off,
                landlock_abi: 0,
                resolved_permissions: Default::default(),
                process_boundary: Default::default(),
            },
            effective_budget: Some(serde_json::from_value(serde_json::json!({ "model_calls": 2 })).unwrap()),
        }))
        .unwrap();
    writer.sync().unwrap();
    drop(writer);
    let before = std::fs::read(dir.join(foe_log::fold::LOG_FILE)).unwrap();
    let error = resume(&dir, "sha256:source").unwrap_err();
    assert!(error.contains("seed/end"), "{error}");
    assert_eq!(std::fs::read(dir.join(foe_log::fold::LOG_FILE)).unwrap(), before);
}
