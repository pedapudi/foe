use super::cycles;
use foe_config::workflow::WorkflowConfig;
use serde_json::json;

/// docs/workflow.md "Firing": the report lists each elementary cycle
/// once, beginning with the smallest node name in that cycle.
#[test]
fn cycle_enumeration_is_stable() {
    let workflow: WorkflowConfig = serde_json::from_value(json!({ "nodes": {
        "start": { "tool": "t" },
        "a": { "tool": "t", "follows": ["start", "b"], "max_fires": 2 },
        "b": { "tool": "t", "follows": ["a", "c"], "max_fires": 2 },
        "c": { "tool": "t", "follows": ["b"], "max_fires": 2 }
    } }))
    .unwrap();
    assert_eq!(cycles(&workflow), vec![vec!["a".to_string(), "b".to_string()], vec!["b".to_string(), "c".to_string()]]);
}
