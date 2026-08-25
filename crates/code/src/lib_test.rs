#[test]
fn all_lists_each_tool_once_and_readonly_lists_the_reads_tools() {
    let names: Vec<String> = super::all().iter().map(|t| t.spec().name.clone()).collect();
    let mut expected = vec!["read", "grep", "edit"];
    if cfg!(feature = "exec") {
        expected.push("bash");
        expected.push("session");
        expected.push("python");
    }
    assert_eq!(names, expected);
    let ro: Vec<String> = super::readonly().iter().map(|t| t.spec().name.clone()).collect();
    assert_eq!(ro, ["read", "grep"]);
    for t in super::all() {
        let words = t.spec().description.split_whitespace().count();
        assert!(words < 80, "{} description has {words} words", t.spec().name);
        assert!(t.spec().instruction.is_some(), "{} lacks an instruction", t.spec().name);
    }
}

/// docs/config.md "JSON Schema subset": dispatch checks a call against the
/// tool's parameter schema, so a schema this crate writes stays inside the
/// subset the runtime evaluates.
#[test]
fn every_coding_tool_schema_stays_inside_the_implemented_subset() {
    for tool in super::all() {
        let spec = tool.spec();
        foe_config::schema::check(format!("tools.{}.params", spec.name), &spec.params).unwrap();
    }
}

/// The subject a tool writes is for a person reading a list, and the model
/// must never be asked to produce it: a weaker model would drop it or fill
/// it with noise, and correct tool use would come to depend on extra prose.
/// Nothing about it may reach the model, so it may appear in no tool's
/// parameters, description or instruction, and therefore in no schema and
/// nowhere in the system prompt.
#[test]
fn nothing_about_the_subject_reaches_the_model() {
    for tool in super::all() {
        let spec = tool.spec();
        let whole = serde_json::to_string(&spec).unwrap();
        assert!(!whole.contains("subject"), "{} mentions the subject in its specification", spec.name);
        // The schema is the whole of what the model is told about a tool,
        // and it carries exactly a name, a description and parameters.
        let schema = serde_json::to_value(spec.schema()).unwrap();
        let mut keys: Vec<&String> = schema.as_object().unwrap().keys().collect();
        keys.sort();
        assert_eq!(keys, ["description", "name", "parameters"], "{} schema carries another field", spec.name);
        assert_eq!(schema["name"], serde_json::json!(spec.name));
        assert_eq!(schema["description"], serde_json::json!(spec.description));
        assert_eq!(schema["parameters"], spec.params);
    }
}

/// The same prohibition over the prompt the runtime actually assembles,
/// which is the instruction sections followed by every tool's instruction.
#[test]
fn the_assembled_system_prompt_never_mentions_the_subject() {
    let root = std::env::temp_dir().join("foe-subject-prompt");
    std::fs::create_dir_all(&root).unwrap();
    let config: foe_config::Config = serde_json::from_value(serde_json::json!({
        "version": 2,
        "name": "subject-prohibition",
        "instructions": { "10-role": "You fix failing tests." },
        "tools": super::all().iter().map(|t| t.spec().name.clone()).collect::<Vec<_>>(),
        "grants": { "read": [&root], "write": [&root] },
        "budget": { "model_calls": 1 },
        "task": "do the thing"
    }))
    .unwrap();
    let program = foe_config::config::resolve(&config).unwrap();
    let registry = foe_core::registry::Registry::new(&program, vec![], super::all()).unwrap();
    let prompt = registry.system_prompt(&program.instructions);
    assert!(!prompt.contains("subject"), "the system prompt mentions the subject:\n{prompt}");
    for schema in registry.schemas() {
        let whole = serde_json::to_string(&schema).unwrap();
        assert!(!whole.contains("subject"), "{} schema mentions the subject", schema.name);
    }
}

/// A subject past the cap ends in an ellipsis where it was cut, so a
/// shortened line never passes for a complete one.
#[test]
fn a_cut_subject_is_marked_where_it_was_cut() {
    let long = "x".repeat(foe_core::SUBJECT_MAX + 40);
    let v = foe_core::ToolValue::ok(serde_json::json!({}), "").subject(&long);
    let subject = v.subject.unwrap();
    assert_eq!(subject.chars().count(), foe_core::SUBJECT_MAX);
    assert!(subject.ends_with('\u{2026}'), "{subject}");
    let short = foe_core::ToolValue::ok(serde_json::json!({}), "").subject("read a.txt");
    assert_eq!(short.subject.as_deref(), Some("read a.txt"));
}
