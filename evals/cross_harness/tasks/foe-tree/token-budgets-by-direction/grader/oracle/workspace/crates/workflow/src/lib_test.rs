use super::{model_nodes, node_program, spawner_config};
use foe_core::Config;
use serde_json::json;

fn config() -> Config {
    serde_json::from_value(json!({
        "version": 2, "name": "wf", "instructions": { "r": "x" }, "tools": ["block"],
        "grants": { "read": ["/p"], "spawn": ["helper"] }, "budget": { "model_calls": 10 }, "task": "t",
        "programs": { "helper": { "name": "helper", "instructions": { "r": "h" }, "tools": ["block"],
                                  "grants": { "read": ["/p"] }, "budget": { "model_calls": 1 } } },
        "workflow": { "nodes": {
            "plan": { "model": { "name": "plan", "instructions": { "r": "p" }, "tools": ["block"],
                                 "grants": { "read": ["/p"] }, "budget": { "model_calls": 2 },
                                 "done_when": { "returns": { "type": "object", "properties": { "n": { "type": "integer" } }, "required": ["n"] } } },
                      "branches": { "go": [], "stop": [] } },
            "inner": { "workflow": { "nodes": {
                "draft": { "model": { "name": "draft", "instructions": { "r": "d" }, "tools": ["block"],
                                      "grants": { "read": ["/p"] }, "budget": { "model_calls": 2 } },
                           "branches": { "ok": [] } }
            } } }
        } }
    }))
    .unwrap()
}

/// docs/workflow.md "Choice points": the runtime adds `branch` to the
/// returns schema as a required enum over the labels, creating the schema
/// when the node declared none.
#[test]
fn branch_is_added_to_the_returns_schema() {
    let config = config();
    let nodes = model_nodes(config.workflow.as_ref().unwrap(), "");
    let paths: Vec<&str> = nodes.iter().map(|(p, _)| p.as_str()).collect();
    assert_eq!(paths, ["inner/draft", "plan"]);
    let plan = node_program(nodes[1].1);
    let returns = plan.done_when.unwrap().returns.unwrap();
    assert_eq!(returns["properties"]["branch"], json!({ "type": "string", "enum": ["go", "stop"] }));
    let required = returns["required"].as_array().unwrap();
    assert!(required.contains(&json!("branch")), "`branch` is required: {required:?}");
    assert!(required.contains(&json!("n")), "the declared requirement is kept: {required:?}");
    assert_eq!(returns["properties"]["n"], json!({ "type": "integer" }), "the declared schema is kept");
    let draft = node_program(nodes[0].1);
    let returns = draft.done_when.unwrap().returns.unwrap();
    assert_eq!(
        returns,
        json!({ "type": "object", "properties": { "branch": { "type": "string", "enum": ["ok"] } }, "required": ["branch"] })
    );
}

/// docs/workflow.md "Model nodes": a model node is spawned as a child
/// program, so the spawner's configuration lists every model node under
/// its path beside the declared programs.
#[test]
fn the_spawner_configuration_lists_model_nodes_as_programs() {
    let spawner = spawner_config(&config());
    let programs: Vec<&String> = spawner.programs.keys().collect();
    assert_eq!(programs, ["helper", "inner/draft", "plan"]);
    assert_eq!(spawner.grants.spawn, ["helper", "inner/draft", "plan"]);
    assert_eq!(
        spawner.programs["plan"].done_when.as_ref().unwrap().returns.as_ref().unwrap()["required"],
        json!(["n", "branch"])
    );
}
