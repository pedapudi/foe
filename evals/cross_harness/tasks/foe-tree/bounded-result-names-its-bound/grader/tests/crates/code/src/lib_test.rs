use crate::testing::ScratchDir;

#[test]
fn all_lists_each_tool_once_and_readonly_lists_the_reads_tools() {
    let names: Vec<String> = super::all().iter().map(|t| t.spec().name.clone()).collect();
    let mut expected = vec!["read", "grep", "edit"];
    if cfg!(feature = "exec") {
        expected.push("bash");
        expected.push("session");
        expected.push(foe_core::COMPOSING_TOOL);
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
        foe_contract::schema::check(format!("tools.{}.params", spec.name), &spec.params).unwrap();
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
        // The schema is all the model is told about a tool, and it carries
        // exactly a name, a description and parameters.
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
    let root = ScratchDir::new("subject-prompt");
    let config: foe_contract::ContractDocument = serde_json::from_value(serde_json::json!({
        "version": 4,
        "name": "subject-prohibition",
        "instructions": { "10-role": "You fix failing tests." },
        "tools": super::all().iter().map(|t| t.spec().name.clone()).collect::<Vec<_>>(),
        "grants": { "read": [&root], "write": [&root] },
        "budget": { "model_calls": 1 },
        "task": "do the thing"
    }))
    .unwrap();
    let contract = foe_contract::document::resolve(&config).unwrap();
    let executables = foe_core::captured_executable::CapturedExecutableTree::materialize(&contract, &root).unwrap();
    let registry = foe_core::registry::Registry::new(&contract, &executables, vec![], super::all()).unwrap();
    let prompt = registry.system_prompt(&contract.instructions);
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

/// One vocabulary of bounds: every name a cut result carries stands in
/// [`super::BOUNDS`], so a tool that names a bound the table does not hold
/// is a tool no caller can interpret.
#[tokio::test]
async fn every_bound_a_cut_result_names_stands_in_the_table() {
    use crate::testing::{ctx, ctx_with_executor, ctx_with_sessions, FakeExecutor};
    use foe_core::{CallCtx, ExecResult, SessionOutput, SessionRequest, SessionStatus, Tool, ToolValue};
    use serde_json::json;
    use std::sync::Arc;

    struct OneSession(std::sync::Mutex<SessionOutput>);
    impl foe_core::Sessions for OneSession {
        fn start(&self, _: SessionRequest) -> Result<SessionStatus, foe_core::CapError> {
            Ok(status())
        }
        fn take_output(&self, _: u64) -> Result<(SessionStatus, SessionOutput), foe_core::CapError> {
            Ok((status(), std::mem::take(&mut *self.0.lock().unwrap())))
        }
        fn write_stdin(&self, _: u64, _: &[u8]) -> Result<SessionStatus, foe_core::CapError> {
            Ok(status())
        }
        fn signal(&self, _: u64, _: &str) -> Result<SessionStatus, foe_core::CapError> {
            Ok(status())
        }
        fn stop(&self, _: u64) -> Result<SessionStatus, foe_core::CapError> {
            Ok(status())
        }
        fn settle(&self) -> Vec<foe_core::SessionSettlement> {
            Vec::new()
        }
    }
    fn status() -> SessionStatus {
        SessionStatus { id: 1, name: "server".into(), alive: true, exit_code: None, seconds: 3 }
    }

    #[derive(Default)]
    struct NoComposer;
    #[async_trait::async_trait]
    impl foe_core::Composer for NoComposer {
        async fn call(
            &self,
            _: &str,
            _: serde_json::Value,
        ) -> Result<(serde_json::Value, bool), foe_core::RuntimeError> {
            Ok((json!({}), false))
        }
    }

    fn executor(stdout: &str) -> Arc<FakeExecutor> {
        Arc::new(FakeExecutor::new(ExecResult {
            exit_code: Some(0),
            stdout: stdout.into(),
            stderr: Vec::new(),
            timed_out: false,
            duration: std::time::Duration::from_millis(10),
        }))
    }

    let long: String = (1..=2500).map(|i| format!("line {i}\n")).collect();
    let fx = crate::testing::Fixture::new();
    fx.write("big.txt", &(1..=2500).map(|i| format!("line {i}\n")).collect::<String>());
    fx.write("many.txt", &(1..=10_001).map(|i| format!("alpha {i}\n")).collect::<String>());
    let before: String = (1..=300).map(|i| format!("line {i}\n")).collect();
    let after: String = (1..=300).map(|i| format!("row {i}\n")).collect();
    fx.write("wide.txt", &before);

    let python_ctx = |handle: Arc<FakeExecutor>| -> CallCtx {
        let mut c = ctx_with_executor(&fx, handle);
        c.composer = Some(Arc::new(NoComposer));
        c
    };
    let sessions = Arc::new(OneSession(std::sync::Mutex::new(SessionOutput {
        stdout: long.clone().into_bytes(),
        stderr: Vec::new(),
    })));

    let mut seen: Vec<(String, ToolValue)> = Vec::new();
    seen.push(("read".into(), crate::read::Read::new().call(json!({"path": "big.txt"}), &ctx(&fx)).await));
    seen.push(("grep".into(), crate::grep::Grep::new().call(json!({"pattern": "alpha", "limit": 1}), &ctx(&fx)).await));
    seen.push((
        "edit".into(),
        crate::edit::Edit::new()
            .call(json!({"path": "wide.txt", "edits": [{"old_text": before, "new_text": after}]}), &ctx(&fx))
            .await,
    ));
    seen.push((
        "bash".into(),
        crate::bash::Bash::new().call(json!({"command": "seq 2500"}), &ctx_with_executor(&fx, executor(&long))).await,
    ));
    seen.push((
        "session".into(),
        crate::session::Session::new()
            .call(json!({"action": "poll", "session": 1}), &ctx_with_sessions(&fx, sessions))
            .await,
    ));
    seen.push((
        foe_core::COMPOSING_TOOL.into(),
        crate::python::Python::new()
            .call(json!({"source": "#".repeat(super::PYTHON_SOURCE_MAX_BYTES + 1)}), &python_ctx(executor("")))
            .await,
    ));

    let mut named = Vec::new();
    for (tool, value) in &seen {
        let Some(bound) = value.value.get("bound").and_then(|b| b.as_str()) else { continue };
        assert!(super::is_bound(bound), "{tool} names the bound {bound:?}, which crates/code/src/lib.rs BOUNDS lacks");
        named.push(bound.to_owned());
    }
    assert!(!named.is_empty(), "no tool named a bound, so the table was never exercised");
    let mut sorted: Vec<&(&str, &str)> = super::BOUNDS.iter().collect();
    sorted.sort();
    let mut unique = sorted.clone();
    unique.dedup_by_key(|entry| entry.0);
    assert_eq!(sorted.len(), unique.len(), "BOUNDS names one bound twice");
}
