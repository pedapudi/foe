use super::spawner_config;
use foe_config::Config;
use serde_json::json;

fn config() -> Config {
    serde_json::from_value(json!({
        "version": 3, "name": "wf", "instructions": { "r": "x" }, "tools": ["block"],
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
