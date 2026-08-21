use super::{parse, resolve, validate};
use crate::test_util::{config, config_value, program_with, tmp};
use crate::ConfigError;
use serde_json::{json, Value};

/// Applies `edit` to a valid document and returns the offending key.
fn rejected(root: &std::path::Path, edit: impl FnOnce(&mut Value)) -> String {
    match program_with(root, edit) {
        Err(ConfigError::Invalid { key, rule }) => {
            assert!(!rule.is_empty());
            key
        }
        other => panic!("expected an Invalid error, got {other:?}"),
    }
}

#[test]
fn a_valid_document_resolves_with_canonical_paths_and_defaults() {
    let root = tmp("config-valid");
    std::fs::create_dir_all(root.join("sub")).unwrap();
    std::os::unix::fs::symlink(root.join("sub"), root.join("link")).unwrap();
    let exec = root.join("tool.sh");
    std::fs::write(&exec, "#!/bin/sh\n").unwrap();
    let program = program_with(&root, |v| {
        v["grants"]["read"] = json!([root.join("link")]);
        v["tools"] = json!(["block", "lint"]);
        v["tool_defs"] = json!({ "lint": { "exec": exec, "description": "lints" } });
    })
    .unwrap();
    let canonical = std::fs::canonicalize(root.join("sub")).unwrap();
    assert_eq!(program.grants.read, vec![canonical.clone()]);
    let lint = &program.tool_defs["lint"];
    assert_eq!(lint.cwd.as_deref(), Some(canonical.as_path()), "cwd defaults to the first read root");
    assert_eq!(lint.timeout_seconds, 120);
    assert_eq!(program.budget.loop_threshold, 3);
    assert!(program.to_value().get("task").is_none(), "the program omits the task");
}

#[test]
fn unknown_keys_and_wrong_types_are_parse_errors() {
    let root = tmp("config-parse");
    let mut value = config_value(&root);
    value["surprise"] = json!(1);
    assert!(matches!(parse(&value.to_string()), Err(ConfigError::Parse(_))));
    let mut value = config_value(&root);
    value["budget"]["model_calls"] = json!("ten");
    assert!(matches!(parse(&value.to_string()), Err(ConfigError::Parse(_))));
}

#[test]
fn every_rule_names_its_key() {
    let root = tmp("config-rules");
    let exec = root.join("tool.sh");
    std::fs::write(&exec, "").unwrap();
    std::fs::create_dir_all(root.join("child")).unwrap();
    type Case<'a> = (&'a str, Box<dyn FnOnce(&mut Value)>);
    let cases: Vec<Case> = vec![
        ("version", Box::new(|v| v["version"] = json!(2))),
        ("name", Box::new(|v| v["name"] = json!(" "))),
        ("task", Box::new(|v| v["task"] = json!(""))),
        ("instructions", Box::new(|v| v["instructions"] = json!({}))),
        ("instructions.10-role", Box::new(|v| v["instructions"]["10-role"] = json!(""))),
        ("tools", Box::new(|v| v["tools"] = json!([]))),
        ("tools[1]", Box::new(|v| v["tools"] = json!(["block", "block"]))),
        (
            "tools",
            Box::new(|v| {
                v["tools"] = json!(["edit"]);
                v["grants"]["write"] = json!([]);
            }),
        ),
        ("tools", Box::new(|v| v["tools"] = json!(["spawn"]))),
        (
            "tool_defs.t.exec",
            Box::new(|v| {
                v["tool_defs"] = json!({ "t": { "exec": "relative/t", "description": "d" } });
            }),
        ),
        (
            "tool_defs.t.exec",
            Box::new({
                let root = root.clone();
                move |v| {
                    v["tools"] = json!(["t"]);
                    v["tool_defs"] = json!({ "t": { "exec": root.join("absent"), "description": "d" } });
                }
            }),
        ),
        (
            "tool_defs.t.description",
            Box::new({
                let exec = exec.clone();
                move |v| v["tool_defs"] = json!({ "t": { "exec": exec, "description": "" } })
            }),
        ),
        (
            "tool_defs.t.timeout_seconds",
            Box::new({
                let exec = exec.clone();
                move |v| v["tool_defs"] = json!({ "t": { "exec": exec, "description": "d", "timeout_seconds": 0 } })
            }),
        ),
        (
            "tool_defs.t.cwd",
            Box::new({
                let exec = exec.clone();
                move |v| v["tool_defs"] = json!({ "t": { "exec": exec, "description": "d", "cwd": "rel" } })
            }),
        ),
        (
            "host_tools.h.params",
            Box::new(|v| v["host_tools"] = json!({ "h": { "description": "d", "params": [], "effect": "pure" } })),
        ),
        (
            "host_tools.h.description",
            Box::new(|v| v["host_tools"] = json!({ "h": { "description": "", "params": {}, "effect": "pure" } })),
        ),
        ("grants.read", Box::new(|v| v["grants"]["read"] = json!([]))),
        ("grants.read[0]", Box::new(|v| v["grants"]["read"] = json!(["relative"]))),
        (
            "grants.read[0]",
            Box::new({
                let root = root.clone();
                move |v| v["grants"]["read"] = json!([root.join("nope")])
            }),
        ),
        ("grants.write[0]", Box::new(|v| v["grants"]["write"] = json!(["relative"]))),
        ("grants.spawn[0]", Box::new(|v| v["grants"]["spawn"] = json!(["ghost"]))),
        ("budget.model_calls", Box::new(|v| v["budget"]["model_calls"] = json!(0))),
        ("budget.tokens", Box::new(|v| v["budget"]["tokens"] = json!(0))),
        ("budget.seconds", Box::new(|v| v["budget"]["seconds"] = json!(0))),
        ("budget.max_episodes", Box::new(|v| v["budget"]["max_episodes"] = json!(0))),
        ("budget.loop_threshold", Box::new(|v| v["budget"]["loop_threshold"] = json!(1))),
        ("done_when.verify", Box::new(|v| v["done_when"] = json!({ "verify": "ghost" }))),
        ("done_when.returns", Box::new(|v| v["done_when"] = json!({ "returns": "string" }))),
        ("model.provider", Box::new(|v| v["model"] = json!({ "provider": " ", "model": "m" }))),
        ("model.model", Box::new(|v| v["model"] = json!({ "provider": "anthropic", "model": "" }))),
        (
            "model.max_output_tokens",
            Box::new(|v| v["model"] = json!({ "provider": "anthropic", "model": "m", "max_output_tokens": 0 })),
        ),
        (
            "programs.kid.instructions",
            Box::new({
                let root = root.clone();
                move |v| v["programs"] = json!({ "kid": { "name": "kid", "instructions": {}, "tools": ["block"], "grants": { "read": [root] }, "budget": { "model_calls": 1 } } })
            }),
        ),
        (
            "programs.kid.grants.read[0]",
            Box::new({
                let root = root.clone();
                move |v| {
                    v["grants"]["read"] = json!([root.join("child")]);
                    v["programs"] = json!({ "kid": { "name": "kid", "instructions": { "a": "b" }, "tools": ["block"], "grants": { "read": [root] }, "budget": { "model_calls": 1 } } });
                }
            }),
        ),
    ];
    for (key, edit) in cases {
        assert_eq!(rejected(&root, edit), key);
    }
}

#[test]
fn children_inherit_model_and_sandbox_and_validate_recursively() {
    let root = tmp("config-children");
    std::fs::create_dir_all(root.join("child")).unwrap();
    std::fs::write(root.join("k.key"), "k").unwrap();
    let program = program_with(&root, |v| {
        v["tools"] = json!(["block", "spawn"]);
        v["grants"]["spawn"] = json!(["kid"]);
        v["model"] = json!({ "provider": "anthropic", "model": "m", "api_key_file": root.join("k.key") });
        v["programs"] = json!({ "kid": {
            "name": "kid", "instructions": { "a": "b" }, "tools": ["block"],
            "grants": { "read": [root.join("child")], "write": [root.join("child")] }, "budget": { "model_calls": 1 }
        } });
    })
    .unwrap();
    let kid = &program.programs["kid"];
    assert_eq!(kid.model, program.model);
    assert_eq!(kid.sandbox, program.sandbox);
    assert_eq!(kid.grants.read, vec![std::fs::canonicalize(root.join("child")).unwrap()]);
    assert!(validate(&config(&root)).is_ok());
    assert!(resolve(&config(&root)).is_ok());
}
