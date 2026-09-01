use super::{resolve_specs, Handles, Registry, Source};
use crate::grants::{RootReader, RootWriter};
use crate::test_util::{program_with, spec, tmp, FakeExecutor, Probe, Verifier};
use crate::{CapError, ExecRequest, ExecResult, Executor, Tool, ToolCall, ToolFailureCode};
use foe_program::harness_text as text;
use foe_program::{Effect, ProgramError};
use serde_json::{json, Value};
use std::sync::Arc;

struct StartFailure;

impl Executor for StartFailure {
    fn run(&self, _req: ExecRequest) -> Result<ExecResult, CapError> {
        Err(CapError::ProcessStart("interpreter missing".into()))
    }
}

fn probe(name: &str, effect: Effect) -> Box<dyn Tool> {
    Box::new(Probe::new(name, effect))
}

fn call(name: &str, args: Value) -> ToolCall {
    ToolCall { id: format!("tc_{name}"), name: name.into(), args }
}

fn rule_of(result: Result<Registry, ProgramError>) -> String {
    match result {
        Err(ProgramError::Invalid { key, rule }) => {
            assert_eq!(key, "tools");
            rule
        }
        Ok(_) => panic!("expected a construction error"),
        Err(e) => panic!("{e}"),
    }
}

#[test]
fn names_resolve_in_source_order_and_schemas_follow_tools_order() {
    let root = tmp("registry-order");
    let exec = root.join("t.sh");
    std::fs::write(&exec, "").unwrap();
    let program = program_with(&root, |v| {
        v["tools"] = json!(["h", "t", "block", "p"]);
        v["tool_defs"] = json!({ "t": { "exec": exec, "description": "configured", "instruction": "use t" } });
        v["host_tools"] = json!({ "h": { "description": "hosted", "params": { "type": "object" }, "effect": "pure" } });
    })
    .unwrap();
    let registry = Registry::new(&program, vec![probe("h", Effect::Pure)], vec![probe("p", Effect::Pure)]).unwrap();
    let names: Vec<_> = registry.schemas().into_iter().map(|s| s.name).collect();
    assert_eq!(names, vec!["h", "t", "block", "p"]);
    assert_eq!(registry.source("h"), Some(Source::Host));
    assert_eq!(registry.source("t"), Some(Source::Configured));
    assert_eq!(registry.source("block"), Some(Source::Builtin));
    assert_eq!(registry.source("p"), Some(Source::Builtin));
    assert!(!registry.has_return());
    let prompt = registry.system_prompt(&program.instructions);
    assert_eq!(
        prompt,
        format!("Be brief.\n\nYou are a test agent.\n\n{}\n\n## t\n\nuse t", text::TOOL_INSTRUCTIONS_HEADING)
    );
    let specs = resolve_specs(&program, &[spec("p", Effect::Pure)]).unwrap();
    assert_eq!(specs.iter().map(|s| s.name.as_str()).collect::<Vec<_>>(), names);
}

#[test]
fn duplicate_and_unresolved_names_are_errors_naming_the_tool() {
    let root = tmp("registry-dupes");
    let program = program_with(&root, |v| {
        v["tools"] = json!(["block", "x"]);
        v["host_tools"] = json!({ "x": { "description": "d", "params": {}, "effect": "pure" } });
    })
    .unwrap();
    let rule = rule_of(Registry::new(&program, vec![probe("x", Effect::Pure)], vec![probe("x", Effect::Pure)]));
    assert!(rule.contains("`x`") && rule.contains("more than one source"), "{rule}");
    let program = program_with(&root, |v| v["tools"] = json!(["block", "ghost"])).unwrap();
    let rule = rule_of(Registry::new(&program, vec![], vec![]));
    assert!(rule.contains("`ghost`") && rule.contains("no source"), "{rule}");
    let program = program_with(&root, |v| {
        v["tools"] = json!(["h"]);
        v["host_tools"] = json!({ "h": { "description": "d", "params": {}, "effect": "pure" } });
    })
    .unwrap();
    let rule = rule_of(Registry::new(&program, vec![], vec![]));
    assert!(rule.contains("`h`") && rule.contains("no implementation"), "{rule}");
}

#[test]
fn an_effect_the_grants_do_not_cover_is_refused_at_construction() {
    let root = tmp("registry-effect");
    let program = program_with(&root, |v| {
        v["tools"] = json!(["w"]);
        v["grants"]["write"] = json!([]);
    })
    .unwrap();
    let rule = rule_of(Registry::new(&program, vec![], vec![probe("w", Effect::Writes)]));
    assert!(rule.contains("`w`") && rule.contains("grants.write"), "{rule}");
    let program = program_with(&root, |v| v["tools"] = json!(["s"])).unwrap();
    let rule = rule_of(Registry::new(&program, vec![], vec![probe("s", Effect::Spawns)]));
    assert!(rule.contains("grants.spawn"), "{rule}");
    let program = program_with(&root, |v| v["tools"] = json!(["x"])).unwrap();
    assert!(Registry::new(&program, vec![], vec![probe("x", Effect::Execs)]).is_ok(), "execution needs no grant");
}

#[tokio::test]
async fn dispatch_passes_only_the_handles_the_effect_entitles() {
    let root = tmp("registry-handles");
    let program = program_with(&root, |v| v["tools"] = json!(["pure", "reads", "writes", "execs"])).unwrap();
    let tools = vec![
        probe("pure", Effect::Pure),
        probe("reads", Effect::Reads),
        probe("writes", Effect::Writes),
        probe("execs", Effect::Execs),
    ];
    let registry = Registry::new(&program, vec![], tools).unwrap();
    let handles = Handles {
        reader: Some(Arc::new(RootReader::new(program.grants.read.clone()).unwrap())),
        writer: Some(Arc::new(RootWriter::new(program.grants.write.clone()).unwrap())),
        executor: Some(Arc::new(FakeExecutor::default())),
        spawner: None,
        sessions: None,
    };
    let received = |name: &str| {
        let (registry, handles, root) = (&registry, &handles, &root);
        let call = call(name, json!({}));
        async move {
            let v = registry.dispatch(handles, &call, 1, root.to_path_buf(), None, None).await.value;
            (v["reader"].as_bool().unwrap(), v["writer"].as_bool().unwrap(), v["executor"].as_bool().unwrap())
        }
    };
    assert_eq!(received("pure").await, (false, false, false));
    assert_eq!(received("reads").await, (true, false, false));
    assert_eq!(received("writes").await, (true, true, false));
    assert_eq!(received("execs").await, (true, false, true));
    let unknown = registry.dispatch(&handles, &call("ghost", json!({})), 1, root.to_path_buf(), None, None).await;
    assert!(unknown.is_error && unknown.rendered.unwrap().contains("`ghost`"));
    let bad = registry.dispatch(&handles, &call("pure", json!([1])), 1, root.to_path_buf(), None, None).await;
    assert!(bad.is_error);
}

#[tokio::test]
async fn block_validates_its_code_and_return_validates_against_the_schema() {
    let root = tmp("registry-block");
    let program = program_with(&root, |v| {
        v["done_when"] =
            json!({ "returns": { "type": "object", "properties": { "n": { "type": "integer" } }, "required": ["n"] } });
    })
    .unwrap();
    let registry = Registry::new(&program, vec![], vec![]).unwrap();
    assert!(registry.has_return());
    assert_eq!(registry.schemas().last().unwrap().name, text::RETURN_NAME, "`return` follows the named tools");
    let handles = Handles::default();
    let ok = registry
        .dispatch(
            &handles,
            &call("block", json!({ "code": "ambiguous-task", "message": "which test?" })),
            1,
            root.to_path_buf(),
            None,
            None,
        )
        .await;
    assert!(!ok.is_error && ok.value["code"] == "ambiguous-task");
    let bad = registry
        .dispatch(
            &handles,
            &call("block", json!({ "code": "looping-tool-call", "message": "m" })),
            1,
            root.to_path_buf(),
            None,
            None,
        )
        .await;
    assert_eq!(bad.failure.unwrap().code, ToolFailureCode::InvalidCall, "other runtime codes are not reportable");

    let parent = program_with(&root, |v| {
        v["tools"] = json!(["block", "spawn"]);
        v["grants"]["spawn"] = json!(["worker"]);
        v["programs"] = json!({ "worker": {
            "name": "worker", "instructions": { "role": "work" }, "tools": ["block"],
            "grants": { "read": [root] }, "budget": { "model_calls": 1 }
        }});
    })
    .unwrap();
    let parent_registry = Registry::new(&parent, vec![], vec![probe("spawn", Effect::Spawns)]).unwrap();
    let child_blocked = parent_registry
        .dispatch(
            &handles,
            &call("block", json!({ "code": "child-blocked", "message": "every child is blocked" })),
            1,
            root.to_path_buf(),
            None,
            None,
        )
        .await;
    assert!(!child_blocked.is_error && child_blocked.value["code"] == "child-blocked");
    let returned = registry
        .dispatch(&handles, &call("return", json!({ "value": { "n": 3 } })), 1, root.to_path_buf(), None, None)
        .await;
    assert!(!returned.is_error && returned.value["value"]["n"] == 3);
    let rejected = registry
        .dispatch(&handles, &call("return", json!({ "value": { "n": "three" } })), 1, root.to_path_buf(), None, None)
        .await;
    assert!(rejected.is_error && rejected.rendered.unwrap().contains("value.n"));
}

/// docs/config.md "JSON Schema subset": dispatch is the one place a call's
/// arguments are checked, so a host tool's declared `params` binds the model
/// before the host process sees the call.
#[tokio::test]
async fn dispatch_checks_host_tool_arguments_against_the_declared_schema() {
    let root = tmp("registry-host-args");
    let program = program_with(&root, |v| {
        v["tools"] = json!(["lookup"]);
        v["host_tools"] = json!({ "lookup": {
            "description": "lookup", "effect": "pure",
            "params": {
                "type": "object", "additionalProperties": false, "required": ["key", "limit"],
                "properties": { "key": { "type": "string", "minLength": 1 }, "limit": { "type": "integer", "minimum": 1 } }
            }
        }});
    })
    .unwrap();
    let registry = Registry::new(&program, vec![probe("lookup", Effect::Pure)], vec![]).unwrap();
    let handles = Handles::default();
    let ok = registry
        .dispatch(&handles, &call("lookup", json!({ "key": "k", "limit": 2 })), 1, root.to_path_buf(), None, None)
        .await;
    assert!(!ok.is_error, "{:?}", ok.rendered);
    let bad = registry
        .dispatch(&handles, &call("lookup", json!({ "key": "k", "limit": 0 })), 1, root.to_path_buf(), None, None)
        .await;
    assert!(bad.is_error);
    assert!(bad.rendered.unwrap().contains("limit"));
    let extra = registry
        .dispatch(
            &handles,
            &call("lookup", json!({ "key": "k", "limit": 1, "x": 1 })),
            1,
            root.to_path_buf(),
            None,
            None,
        )
        .await;
    assert!(extra.is_error && extra.rendered.unwrap().contains("`x`"));
}

#[tokio::test]
async fn configured_executables_receive_args_as_argv_and_report_exit_as_data() {
    let root = tmp("registry-exec");
    let exec = root.join("t.sh");
    std::fs::write(&exec, "").unwrap();
    let program = program_with(&root, |v| {
        v["tools"] = json!(["t"]);
        v["tool_defs"] = json!({ "t": { "exec": exec, "description": "d", "timeout_seconds": 7, "network": true } });
    })
    .unwrap();
    let registry = Registry::new(&program, vec![], vec![]).unwrap();
    let executor = Arc::new(FakeExecutor::default());
    let handles = Handles { executor: Some(executor.clone()), ..Default::default() };
    let value = registry
        .dispatch(&handles, &call("t", json!({ "args": ["check", "."] })), 2, root.to_path_buf(), None, None)
        .await;
    assert!(!value.is_error);
    assert_eq!(value.value["exit_code"], 0);
    assert_eq!(value.value["stderr"], "check .");
    assert!(value.rendered.unwrap().contains("[exit code 0]"));
    let req = executor.requests.lock().unwrap().pop().unwrap();
    assert_eq!(req.args, vec!["check", "."]);
    assert_eq!((req.timeout.as_secs(), req.network, req.stdin), (7, true, None));
    assert_eq!(req.cwd, program.grants.read[0]);
    let bad =
        registry.dispatch(&handles, &call("t", json!({ "args": "check" })), 2, root.to_path_buf(), None, None).await;
    assert!(bad.is_error);
    assert_eq!(bad.failure.unwrap().code, ToolFailureCode::InvalidCall);
}

/// docs/tools.md "Failures": a configured executable reports process
/// creation failure where its executor refuses the start.
#[tokio::test]
async fn configured_executable_start_failure_is_typed() {
    let root = tmp("registry-exec-start");
    let exec = root.join("t.sh");
    std::fs::write(&exec, "").unwrap();
    let program = program_with(&root, |value| {
        value["tools"] = json!(["t"]);
        value["tool_defs"] = json!({ "t": { "exec": exec, "description": "d" } });
    })
    .unwrap();
    let registry = Registry::new(&program, vec![], vec![]).unwrap();
    let handles = Handles { executor: Some(Arc::new(StartFailure)), ..Default::default() };
    let result =
        registry.dispatch(&handles, &call("t", json!({ "args": [] })), 1, root.to_path_buf(), None, None).await;
    let failure = result.failure.expect("the failed start has a typed failure");
    assert_eq!(failure.code, ToolFailureCode::ProcessStartFailed);
    assert!(!failure.retryable);
}

/// docs/config.md `done_when`: an executable verifier accepts by exiting 0
/// with empty standard output, reports findings as lines with exit 0, and
/// fails the verification with any other exit status.
#[tokio::test]
async fn verify_feeds_the_candidate_on_stdin_to_an_executable_and_as_the_argument_to_a_tool() {
    let root = tmp("registry-verify");
    let exec = root.join("v.sh");
    std::fs::write(&exec, "").unwrap();
    let program = program_with(&root, |v| {
        v["tools"] = json!(["block", "v"]);
        v["tool_defs"] = json!({ "v": { "exec": exec, "description": "d" } });
        v["done_when"] = json!({ "verify": "v" });
    })
    .unwrap();
    let registry = Registry::new(&program, vec![], vec![]).unwrap();
    let executor = Arc::new(FakeExecutor::default());
    let handles = Handles { executor: Some(executor.clone()), ..Default::default() };
    let findings = registry.verify_with("v", &handles, &json!("candidate"), 1, root.to_path_buf(), None).await.unwrap();
    assert_eq!(findings, vec![r#""candidate""#], "stdout is a finding; this executor echoed stdin");
    let req = executor.requests.lock().unwrap().pop().unwrap();
    assert!(req.args.is_empty(), "a verifier receives an empty argument vector");
    assert_eq!(req.stdin.as_deref(), Some(br#""candidate""#.as_slice()));

    // Each line of standard output is one finding, and a blank line is not
    // a finding, so a verifier may separate what it reports.
    let reporting =
        Arc::new(FakeExecutor { stdout: Some("first finding\n\n  \nsecond finding\n".into()), ..Default::default() });
    let handles = Handles { executor: Some(reporting), ..Default::default() };
    let findings = registry.verify_with("v", &handles, &json!("candidate"), 1, root.to_path_buf(), None).await.unwrap();
    assert_eq!(findings, vec!["first finding", "second finding"]);

    let accepting = Arc::new(FakeExecutor { stdout: Some(String::new()), ..Default::default() });
    let handles = Handles { executor: Some(accepting), ..Default::default() };
    let accepted = registry.verify_with("v", &handles, &json!("candidate"), 1, root.to_path_buf(), None).await.unwrap();
    assert!(accepted.is_empty(), "empty standard output with exit 0 accepts the candidate");

    let crashing = Arc::new(FakeExecutor { exit_code: 1, ..Default::default() });
    let handles = Handles { executor: Some(crashing), ..Default::default() };
    let error = registry.verify_with("v", &handles, &json!(""), 1, root.to_path_buf(), None).await.unwrap_err();
    assert!(error.contains("verifier `v` failed") && error.contains("[exit code 1]"), "{error}");
    let error = registry.verify_with("v", &handles, &json!("out"), 1, root.to_path_buf(), None).await.unwrap_err();
    assert!(error.contains(r#""out""#), "the diagnostic carries standard output: {error}");

    let program = program_with(&root, |v| {
        v["tools"] = json!(["block", "check"]);
        v["done_when"] = json!({ "verify": "check" });
    })
    .unwrap();
    let verifier = Verifier {
        spec: spec("check", Effect::Pure),
        findings: std::sync::Mutex::new(vec![vec!["f1".into()], vec![]].into()),
    };
    let registry = Registry::new(&program, vec![], vec![Box::new(verifier)]).unwrap();
    assert_eq!(
        registry.verify_with("check", &handles, &json!("c"), 1, root.to_path_buf(), None).await.unwrap(),
        vec!["f1"]
    );
    assert!(registry
        .verify_with("check", &handles, &json!("c"), 1, root.to_path_buf(), None)
        .await
        .unwrap()
        .is_empty());
}
