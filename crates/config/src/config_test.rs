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
        v["grants"]["execute"] = json!([root.join("link")]);
        v["tools"] = json!(["block", "lint"]);
        v["tool_defs"] = json!({ "lint": { "exec": exec, "description": "lints" } });
    })
    .unwrap();
    let canonical = std::fs::canonicalize(root.join("sub")).unwrap();
    assert_eq!(program.grants.read, vec![canonical.clone()]);
    assert_eq!(program.grants.execute, vec![canonical.clone()]);
    let lint = &program.tool_defs["lint"];
    assert_eq!(lint.cwd.as_deref(), Some(canonical.as_path()), "cwd defaults to the first read root");
    assert_eq!(lint.timeout_seconds, 120);
    assert_eq!(program.budget.loop_threshold, 8);
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
        ("version", Box::new(|v| v["version"] = json!(1))),
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
            "host_tools.h.params",
            Box::new(|v| {
                v["host_tools"] =
                    json!({ "h": { "description": "d", "params": { "format": "email" }, "effect": "pure" } });
            }),
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
        ("grants.execute[0]", Box::new(|v| v["grants"]["execute"] = json!(["relative"]))),
        ("grants.spawn[0]", Box::new(|v| v["grants"]["spawn"] = json!(["ghost"]))),
        ("grants.bind[0]", Box::new(|v| v["grants"]["bind"] = json!([0]))),
        ("budget.model_calls", Box::new(|v| v["budget"]["model_calls"] = json!(0))),
        ("budget.input_tokens", Box::new(|v| v["budget"]["input_tokens"] = json!(0))),
        ("budget.output_tokens", Box::new(|v| v["budget"]["output_tokens"] = json!(0))),
        ("budget.seconds", Box::new(|v| v["budget"]["seconds"] = json!(0))),
        ("budget.max_episodes", Box::new(|v| v["budget"]["max_episodes"] = json!(0))),
        ("budget.loop_threshold", Box::new(|v| v["budget"]["loop_threshold"] = json!(1))),
        ("done_when.verify", Box::new(|v| v["done_when"] = json!({ "verify": "ghost" }))),
        ("done_when.returns", Box::new(|v| v["done_when"] = json!({ "returns": "string" }))),
        ("done_when.returns", Box::new(|v| v["done_when"] = json!({ "returns": { "pattern": "^a" } }))),
        ("model.provider", Box::new(|v| v["model"] = json!({ "provider": " ", "model": "m" }))),
        ("model.model", Box::new(|v| v["model"] = json!({ "provider": "anthropic", "model": "" }))),
        (
            "programs.kid.model.provider",
            Box::new({
                let root = root.clone();
                move |v| {
                    v["programs"] = json!({ "kid": {
                        "name": "kid", "instructions": { "a": "b" }, "tools": ["block"],
                        "grants": { "read": [root] }, "budget": { "model_calls": 1 },
                        "model": { "provider": " ", "model": "m" }
                    } });
                }
            }),
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
fn a_childs_execute_grants_stay_within_its_parent() {
    let root = tmp("config-child-execute");
    let child = root.join("child");
    let outside = tmp("config-child-execute-outside");
    std::fs::create_dir_all(&child).unwrap();
    let error = program_with(&root, |v| {
        v["grants"]["spawn"] = json!(["kid"]);
        v["programs"] = json!({ "kid": {
            "name": "kid", "instructions": {"role": "work"}, "tools": ["block"],
            "grants": {"read": [root], "execute": [outside]}, "budget": {"model_calls": 1}
        }});
    })
    .unwrap_err();
    let ConfigError::Invalid { key, rule } = error else { unreachable!() };
    assert_eq!(key, "programs.kid.grants.execute[0]");
    assert!(rule.contains("parent program"));
}

/// docs/config.md `programs`: a child's bind ports are a subset of its
/// parent's, like every other grant kind.
#[test]
fn a_childs_bind_ports_stay_within_its_parent() {
    let root = tmp("config-child-bind");
    let kid = |bind: Value, root: &std::path::Path| {
        json!({ "kid": {
            "name": "kid", "instructions": {"role": "work"}, "tools": ["block"],
            "grants": {"read": [root], "bind": bind}, "budget": {"model_calls": 1}
        }})
    };
    let subset = program_with(&root, |v| {
        v["grants"]["bind"] = json!([8080, 8081]);
        v["grants"]["spawn"] = json!(["kid"]);
        v["programs"] = kid(json!([8081]), &root);
    })
    .unwrap();
    assert_eq!(subset.programs["kid"].grants.bind, vec![8081]);
    let error = program_with(&root, |v| {
        v["grants"]["bind"] = json!([8080]);
        v["grants"]["spawn"] = json!(["kid"]);
        v["programs"] = kid(json!([8080, 9090]), &root);
    })
    .unwrap_err();
    let ConfigError::Invalid { key, rule } = error else { unreachable!() };
    assert_eq!(key, "programs.kid.grants.bind[1]");
    assert!(rule.contains("parent program"));
}

/// docs/config.md `grants`: a child or workflow model node may hold task-
/// session authority only when its containing program holds it.
#[test]
fn task_session_authority_only_narrows_downward() {
    let root = tmp("config-task-session-authority");
    let child = |root: &std::path::Path| {
        json!({ "kid": {
            "name": "kid", "instructions": {"role": "work"}, "tools": ["block"],
            "grants": {"read": [root], "task_session": true}, "budget": {"model_calls": 1}
        }})
    };
    let key = rejected(&root, |v| {
        v["grants"]["spawn"] = json!(["kid"]);
        v["programs"] = child(&root);
    });
    assert_eq!(key, "programs.kid.grants.task_session");

    let key = rejected(&root, |v| {
        v["workflow"] = json!({ "nodes": { "serve": {
            "model": {
                "name": "serve", "instructions": {"role": "work"}, "tools": ["block"],
                "grants": {"read": [root], "task_session": true}, "budget": {"model_calls": 1}
            },
            "terminal": true
        } } });
    });
    assert_eq!(key, "workflow.nodes.serve.model.grants.task_session");

    let program = program_with(&root, |v| {
        v["grants"]["task_session"] = json!(true);
        v["grants"]["spawn"] = json!(["kid"]);
        v["programs"] = child(&root);
    })
    .unwrap();
    assert!(program.programs["kid"].grants.task_session);
}

/// docs/workflow.md "Model nodes": a model node's program is validated like
/// any other, so its `done_when.returns` is checked at its own dotted key.
#[test]
fn a_workflow_model_node_schema_is_checked_at_its_dotted_key() {
    let root = tmp("config-workflow-schema");
    let key = rejected(&root, |v| {
        v["workflow"] = json!({ "nodes": { "draft": {
            "model": {
                "name": "draft", "instructions": { "role": "draft" }, "tools": ["block"],
                "grants": { "read": [root] }, "budget": { "model_calls": 1 },
                "done_when": { "returns": { "type": "string", "format": "uri" } }
            },
            "terminal": true
        } } });
    });
    assert_eq!(key, "workflow.nodes.draft.model.done_when.returns");
}

#[test]
fn children_override_or_inherit_models_and_inherit_sandbox() {
    let root = tmp("config-children");
    std::fs::create_dir_all(root.join("child")).unwrap();
    std::fs::write(root.join("k.key"), "k").unwrap();
    let program = program_with(&root, |v| {
        v["tools"] = json!(["block", "spawn"]);
        v["grants"]["spawn"] = json!(["kid"]);
        v["model"] = json!({ "provider": "anthropic", "model": "m", "api_key_file": root.join("k.key") });
        v["programs"] = json!({ "kid": {
            "name": "kid", "instructions": { "a": "b" }, "tools": ["block"],
            "grants": { "read": [root.join("child")], "write": [root.join("child")] }, "budget": { "model_calls": 1 },
            "model": { "provider": "openai-codex", "model": "gpt-5.6-luna", "reasoning_effort": "high" },
            "programs": { "grandchild": {
                "name": "grandchild", "instructions": { "a": "b" }, "tools": ["block"],
                "grants": { "read": [root.join("child")] }, "budget": { "model_calls": 1 }
            } }
        } });
    })
    .unwrap();
    let kid = &program.programs["kid"];
    assert_eq!(kid.model.as_ref().unwrap().model, "gpt-5.6-luna");
    assert_eq!(kid.programs["grandchild"].model, kid.model, "an omitted model inherits the nearest declaration");
    assert_eq!(kid.sandbox, program.sandbox);
    assert_eq!(kid.grants.read, vec![std::fs::canonicalize(root.join("child")).unwrap()]);
    assert!(validate(&config(&root)).is_ok());
    assert!(resolve(&config(&root)).is_ok());
}

/// docs/workflow.md "Model nodes": a model node may select a model instead
/// of inheriting the containing program's selection.
#[test]
fn a_workflow_model_node_can_select_its_model() {
    let root = tmp("config-workflow-model");
    let parent = program_with(&root, |v| {
        v["model"] = json!({ "provider": "openai-codex", "model": "gpt-5.6-sol" });
    })
    .unwrap();
    let child = serde_json::from_value(json!({
        "name": "diagnose", "instructions": { "role": "diagnose" }, "tools": ["block"],
        "grants": { "read": [root] }, "budget": { "model_calls": 1 },
        "model": { "provider": "openai-codex", "model": "gpt-5.6-luna", "reasoning_effort": "high" }
    }))
    .unwrap();
    let resolved = super::resolve_node_program("workflow.nodes.diagnose.model", &parent, &child).unwrap();
    assert_eq!(resolved.model.unwrap().model, "gpt-5.6-luna");
}

fn node(name: &str, root: &std::path::Path) -> Value {
    json!({ "name": name, "instructions": { "role": "work" }, "tools": ["block"],
        "grants": { "read": [root] }, "budget": { "model_calls": 1 } })
}

/// docs/workflow.md "Model nodes": every authority-bearing field of a model
/// node lies within the program that contains the workflow, at every depth.
#[test]
fn a_workflow_model_node_stays_within_its_ceiling() {
    let root = tmp("config-workflow-ceiling");
    let exec = root.join("tool");
    let other = root.join("other");
    let tool_home = root.join("tool-home");
    std::fs::write(&exec, "").unwrap();
    std::fs::write(&other, "").unwrap();
    std::fs::create_dir(&tool_home).unwrap();
    let base = || {
        let mut value = config_value(&root);
        value["tools"] = json!(["block", "bounded", "observe"]);
        value["tool_defs"] = json!({ "bounded": { "exec": exec, "cwd": tool_home,
            "description": "bounded", "timeout_seconds": 5 } });
        value["host_tools"] = json!({ "observe": { "description": "observe", "params": {}, "effect": "reads" } });
        value["budget"] = json!({ "model_calls": 4, "input_tokens": 100, "output_tokens": 50, "seconds": 30,
            "max_depth": 2, "loop_threshold": 4 });
        value["programs"] = json!({ "helper": node("helper", &root) });
        value["grants"]["spawn"] = json!(["helper"]);
        value
    };
    type Case<'a> = (&'a str, Box<dyn FnOnce(&mut Value) + 'a>);
    let cases: Vec<Case> = vec![
        ("workflow.nodes.work.model.tools[1]", Box::new(|p| p["tools"] = json!(["block", "ghost"]))),
        (
            "workflow.nodes.work.model.tool_defs.bounded.exec",
            Box::new({
                let other = other.clone();
                move |p| p["tool_defs"] = json!({ "bounded": { "exec": other, "description": "other" } })
            }),
        ),
        (
            "workflow.nodes.work.model.tool_defs.bounded.cwd",
            Box::new(|p| {
                p["tool_defs"]["bounded"].as_object_mut().unwrap().remove("cwd");
            }),
        ),
        (
            "workflow.nodes.work.model.tool_defs.bounded.timeout_seconds",
            Box::new(|p| p["tool_defs"]["bounded"]["timeout_seconds"] = json!(6)),
        ),
        (
            "workflow.nodes.work.model.host_tools.observe",
            Box::new(|p| {
                p["host_tools"] = json!({ "observe": { "description": "changed", "params": {}, "effect": "reads" } })
            }),
        ),
        (
            "workflow.nodes.work.model.grants.spawn[0]",
            Box::new(|p| {
                p["programs"] = json!({ "other": node("other", &root) });
                p["grants"]["spawn"] = json!(["other"]);
            }),
        ),
        (
            "workflow.nodes.work.model.programs.helper.tools[0]",
            Box::new(|p| {
                let mut helper = node("helper", &root);
                helper["tools"] = json!(["ghost"]);
                p["programs"] = json!({ "helper": helper });
                p["grants"]["spawn"] = json!(["helper"]);
            }),
        ),
        ("workflow.nodes.work.model.budget.model_calls", Box::new(|p| p["budget"]["model_calls"] = json!(5))),
        ("workflow.nodes.work.model.budget.input_tokens", Box::new(|p| p["budget"]["input_tokens"] = json!(101))),
        ("workflow.nodes.work.model.budget.output_tokens", Box::new(|p| p["budget"]["output_tokens"] = json!(51))),
        ("workflow.nodes.work.model.budget.seconds", Box::new(|p| p["budget"]["seconds"] = json!(31))),
        ("workflow.nodes.work.model.budget.max_depth", Box::new(|p| p["budget"]["max_depth"] = json!(3))),
        ("workflow.nodes.work.model.budget.loop_threshold", Box::new(|p| p["budget"]["loop_threshold"] = json!(5))),
    ];
    for (expected, edit) in cases {
        let mut value = base();
        let mut child = node("work", &root);
        child["tools"] = json!(["block", "bounded", "observe"]);
        child["tool_defs"] = value["tool_defs"].clone();
        child["host_tools"] = value["host_tools"].clone();
        child["budget"] = value["budget"].clone();
        edit(&mut child);
        value["workflow"] = json!({ "nodes": { "work": { "model": child, "terminal": true } } });
        let config = parse(&value.to_string()).unwrap();
        let ConfigError::Invalid { key, rule } = resolve(&config).unwrap_err() else { unreachable!() };
        assert_eq!(key, expected);
        assert!(rule.contains("workflow ceiling"), "{rule}");
    }

    // A node may reword a configured tool and omit an optional spend limit.
    let mut value = base();
    let mut child = node("work", &root);
    child["tools"] = json!(["block", "bounded"]);
    child["tool_defs"] = value["tool_defs"].clone();
    child["tool_defs"]["bounded"]["description"] = json!("A node-specific description");
    child["tool_defs"]["bounded"]["instruction"] = json!("Use this tool once.");
    child["budget"] = value["budget"].clone();
    child["budget"].as_object_mut().unwrap().remove("input_tokens");
    child["budget"].as_object_mut().unwrap().remove("output_tokens");
    child["budget"].as_object_mut().unwrap().remove("seconds");
    value["workflow"] = json!({ "nodes": { "work": { "model": child, "terminal": true } } });
    assert!(resolve(&parse(&value.to_string()).unwrap()).is_ok());
}

/// docs/config.md `budget`: a model node's `max_episodes` and
/// `max_concurrent` carry no construction ceiling. The pool clamps the
/// episode share when it reserves, and `max_concurrent` counts one
/// episode's own direct children rather than the whole tree's.
#[test]
fn a_model_node_may_declare_its_own_episode_and_concurrency_counts() {
    let root = tmp("config-node-episode-counts");
    let mut value = config_value(&root);
    value["budget"] = json!({ "model_calls": 4, "max_episodes": 2, "max_concurrent": 1 });
    let mut child = node("work", &root);
    child["budget"] = json!({ "model_calls": 1, "max_episodes": 8, "max_concurrent": 4 });
    value["workflow"] = json!({ "nodes": { "work": { "model": child, "terminal": true } } });
    assert!(resolve(&parse(&value.to_string()).unwrap()).is_ok());
}

/// docs/design.md "Delegation": an ordinary child program may name tools its
/// parent does not, because only a workflow model node carries a ceiling.
#[test]
fn an_ordinary_child_may_hold_tools_the_parent_does_not_use() {
    let root = tmp("config-specialist-tools");
    let program = program_with(&root, |v| {
        v["tools"] = json!(["spawn"]);
        v["grants"]["spawn"] = json!(["kid"]);
        v["programs"] = json!({ "kid": node("kid", &root) });
    })
    .unwrap();
    assert_eq!(program.tools, vec!["spawn"]);
    assert_eq!(program.programs["kid"].tools, vec!["block"]);
}

// ---- program_lineage ------------------------------------------------------

fn lineage_claim() -> serde_json::Value {
    serde_json::json!({
        "parent": {
            "program_identity": format!("sha256:{}", "a".repeat(64)),
            "state_identity": format!("sha256:{}", "b".repeat(64)),
        },
        "evidence": format!("sha256:{}", "c".repeat(64)),
        "verification_log": "children/ep_1/episode.jsonl",
        "verification_seq": 7
    })
}

#[test]
fn program_lineage_is_recorded_and_outside_identity() {
    let root = crate::test_util::tmp("config-lineage-omitted");
    let runtime = foe_log::RuntimeInfo { version: "0.1.0".into(), build: "sha256:test".into() };
    let bare = crate::test_util::program(&root);
    let mut value = crate::test_util::config_value(&root);
    value["program_lineage"] = lineage_claim();
    let config: crate::Config = serde_json::from_value(value).unwrap();
    let claimed = crate::config::resolve(&config).unwrap();
    assert_eq!(claimed.to_value()["program_lineage"], lineage_claim());
    assert_eq!(
        crate::identity::compute(&claimed, &[], &runtime).unwrap().hash,
        crate::identity::compute(&bare, &[], &runtime).unwrap().hash,
        "the claim does not participate in identity"
    );
}

#[test]
fn program_lineage_is_root_only() {
    let root = crate::test_util::tmp("config-lineage-root-only");
    let mut value = crate::test_util::config_value(&root);
    value["grants"]["spawn"] = serde_json::json!(["kid"]);
    value["programs"] = serde_json::json!({ "kid": {
        "name": "kid", "instructions": { "a": "b" }, "tools": ["block"],
        "grants": { "read": [root] }, "budget": { "model_calls": 1 },
        "program_lineage": lineage_claim(),
    }});
    let parsed: Result<crate::Config, _> = serde_json::from_value(value);
    assert!(parsed.is_err(), "a nested program does not carry program_lineage");
}
