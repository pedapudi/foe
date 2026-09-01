use super::resolve_specs;
use crate::harness_text as text;
use crate::test_util::{contract_with, spec, tmp};
use crate::Effect;
use serde_json::json;

/// AGENTS.md: a specification rule that can be tested has a test. Every
/// parameter schema the runtime itself writes stays inside the subset
/// `crate::schema` implements, so no built-in tool advertises a constraint
/// dispatch would ignore.
#[test]
fn every_runtime_written_parameter_schema_stays_inside_the_implemented_subset() {
    let root = tmp("tools-own-schemas");
    let exec = root.join("t.sh");
    std::fs::write(&exec, "").unwrap();
    std::fs::set_permissions(&exec, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    let contract = contract_with(&root, |v| {
        v["tools"] = json!(["t", "block"]);
        v["tool_defs"] = json!({ "t": { "exec": exec, "description": "configured" } });
        v["done_when"] = json!({ "returns": { "type": "object", "properties": { "n": { "type": "integer" } } } });
    })
    .unwrap();
    let specs = resolve_specs(&contract, &[spec("p", Effect::Pure)]).unwrap();
    assert!(specs.iter().any(|s| s.name == text::RETURN_NAME), "the synthesized return tool is present");
    for s in specs {
        crate::schema::check(format!("tools.{}.params", s.name), &s.params).unwrap();
    }
}

/// `return` and `block` belong to the harness: a configured or host
/// definition would shadow the synthesized specification, so resolution
/// refuses one even when `tools` omits the name. The configured case once
/// reached a fingerprint panic through the synthesized `return` spec.
#[test]
fn harness_tool_names_cannot_be_defined() {
    let root = tmp("tools-harness-names");
    let exec = root.join("t.sh");
    std::fs::write(&exec, "").unwrap();
    std::fs::set_permissions(&exec, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    let configured = contract_with(&root, |v| {
        v["tool_defs"] = json!({ "return": { "exec": exec, "description": "shadow" } });
        v["done_when"] = json!({ "returns": { "type": "object" } });
    })
    .unwrap();
    let host = contract_with(&root, |v| {
        v["host_tools"] = json!({ "block": { "description": "shadow", "params": {}, "effect": "pure" } });
    })
    .unwrap();
    for contract in [&configured, &host] {
        match resolve_specs(contract, &[]) {
            Err(crate::ContractError::Invalid { rule, .. }) => assert!(rule.contains("harness tool name"), "{rule}"),
            other => panic!("expected an Invalid error, got {other:?}"),
        }
    }
}

/// docs/log-format.md "Blocked codes": reporting blocked children requires
/// both child-contract permission and the `spawn` tool. Permission that no
/// listed tool can exercise does not widen the model's blocking vocabulary.
#[test]
fn block_codes_require_child_contract_permission_and_spawn_tool() {
    let root = tmp("tools-block-codes");
    let leaf = contract_with(&root, |_| {}).unwrap();
    let unused = contract_with(&root, |v| {
        v["grants"]["spawn"] = json!(["worker"]);
        v["child_contracts"] = json!({ "worker": {
            "name": "worker", "instructions": { "role": "work" }, "tools": ["block"],
            "grants": { "read": [root] }, "budget": { "model_calls": 1 }
        }});
    })
    .unwrap();
    let parent = contract_with(&root, |v| {
        v["tools"] = json!(["block", "spawn"]);
        v["grants"]["spawn"] = json!(["worker"]);
        v["child_contracts"] = json!({ "worker": {
            "name": "worker", "instructions": { "role": "work" }, "tools": ["block"],
            "grants": { "read": [root] }, "budget": { "model_calls": 1 }
        }});
    })
    .unwrap();
    let codes = |contract| resolve_specs(contract, &[]).unwrap()[0].params["properties"]["code"]["enum"].clone();
    let parent_codes = resolve_specs(&parent, &[spec("spawn", Effect::Spawns)]).unwrap()[0].params["properties"]
        ["code"]["enum"]
        .clone();
    assert_eq!(codes(&leaf), json!(["goal-unreachable", "ambiguous-task", "missing-capability"]));
    assert_eq!(codes(&unused), json!(["goal-unreachable", "ambiguous-task", "missing-capability"]));
    assert_eq!(parent_codes, json!(["goal-unreachable", "ambiguous-task", "missing-capability", "child-blocked"]));
}
