use crate::config::resolve;
use crate::identity::compute;
use crate::test_util::{config_value, program_with, tmp};
use crate::workflow::MAX_POSSIBLE_FIRINGS;
use crate::{Config, ConfigError};
use foe_log::RuntimeInfo;
use serde_json::{json, Value};
use std::collections::BTreeSet;

/// The graph of docs/workflow.md "The graph", over the fixture's tools.
fn graph() -> Value {
    json!({
        "nodes": {
            "manifest": { "tool": "list" },
            "survey": { "tool": "grep", "args": { "pattern": { "$node": "manifest", "pointer": "/top" } },
                        "follows": ["manifest"], "max_fires": 3 },
            "propose": {
                "model": { "name": "propose", "instructions": { "r": "Propose." }, "tools": ["block"],
                           "grants": { "read": ["ROOT"] }, "budget": { "model_calls": 2 } },
                "follows": ["task", "manifest", "survey"],
                "branches": { "accept": ["derive"], "widen": ["survey"] },
                "max_fires": 3
            },
            "derive": { "tool": "derive", "args": { "experiment": { "$node": "propose" } }, "follows": ["propose"], "terminal": true }
        }
    })
}

fn with_graph(root: &std::path::Path, edit: impl FnOnce(&mut Value)) -> Value {
    let mut value = config_value(root);
    value["tools"] = json!(["block", "list", "grep", "derive"]);
    value["host_tools"] = json!({
        "list": { "description": "d", "params": {}, "effect": "pure" },
        "grep": { "description": "d", "params": {}, "effect": "pure" },
        "derive": { "description": "d", "params": {}, "effect": "pure" }
    });
    let mut graph = graph();
    graph["nodes"]["propose"]["model"]["grants"]["read"] = json!([root]);
    value["workflow"] = graph;
    edit(&mut value);
    value
}

fn rejected(value: Value) -> String {
    rejection(value).0
}

fn rejection(value: Value) -> (String, String) {
    let config: Config = serde_json::from_value(value).expect("the document parses");
    match resolve(&config) {
        Err(ConfigError::Invalid { key, rule }) => {
            assert!(!rule.is_empty());
            (key, rule)
        }
        other => panic!("expected an Invalid error, got {other:?}"),
    }
}

/// docs/workflow.md "Nodes" and "Firing": the edge set is the union of both
/// forms, a cycle closes through a branch, and survey lies on it.
#[test]
fn the_edge_set_unions_both_forms_and_finds_cycles() {
    let root = tmp("workflow-edges");
    let config: Config = serde_json::from_value(with_graph(&root, |v| {
        v["workflow"]["nodes"]["manifest"]["followed_by"] = json!(["survey", "derive"]);
    }))
    .unwrap();
    let wf = config.workflow.as_ref().unwrap();
    let inputs = wf.inputs();
    assert_eq!(inputs["survey"], vec!["manifest"], "a duplicate edge is one edge");
    assert_eq!(inputs["derive"], vec!["propose", "manifest"], "follows first, then followed_by sources");
    assert_eq!(inputs["propose"], vec!["task", "manifest", "survey"], "the task source is an input, listed first");
    let preds = wf.predecessors();
    assert!(preds["survey"].contains("propose"), "a branch target is a successor");
    assert!(!preds["propose"].contains("task"), "the task source orders nothing");
    let cyclic: BTreeSet<String> = ["propose", "survey"].into_iter().map(String::from).collect();
    assert_eq!(wf.on_cycles(), cyclic);
    assert!(resolve(&config).is_ok());
    let program = resolve(&config).unwrap();
    assert!(program.to_value()["workflow"]["nodes"]["propose"]["branches"]["widen"].is_array());
}

/// docs/workflow.md "The graph" and docs/config.md "Errors": every rule
/// names the node and the rule it breaks.
#[test]
fn every_workflow_rule_names_its_node() {
    let root = tmp("workflow-rules");
    std::fs::create_dir_all(root.join("child")).unwrap();
    type Case<'a> = (&'a str, Box<dyn FnOnce(&mut Value)>);
    let cases: Vec<Case> = vec![
        ("workflow.nodes.survey.follows", Box::new(|v| v["workflow"]["nodes"]["survey"]["follows"] = json!(["ghost"]))),
        (
            "workflow.nodes.survey.followed_by",
            Box::new(|v| v["workflow"]["nodes"]["survey"]["followed_by"] = json!(["x"])),
        ),
        (
            "workflow.nodes.propose.branches.widen",
            Box::new(|v| v["workflow"]["nodes"]["propose"]["branches"]["widen"] = json!(["ghost"])),
        ),
        (
            "workflow.nodes.propose.recovery.follows",
            Box::new(|v| v["workflow"]["nodes"]["propose"]["recovery"] = json!({ "follows": ["ghost"] })),
        ),
        ("workflow.nodes.manifest.tool", Box::new(|v| v["workflow"]["nodes"]["manifest"]["tool"] = json!("ghost"))),
        ("workflow.nodes.manifest.verify", Box::new(|v| v["workflow"]["nodes"]["manifest"]["verify"] = json!("ghost"))),
        (
            "workflow.nodes.derive.args",
            Box::new(|v| v["workflow"]["nodes"]["derive"]["args"] = json!({ "e": { "$node": "survey" } })),
        ),
        (
            "workflow.nodes.manifest.",
            Box::new(|v| v["workflow"]["nodes"]["manifest"]["workflow"] = json!({ "nodes": {} })),
        ),
        ("workflow.nodes.manifest.", Box::new(|v| v["workflow"]["nodes"]["manifest"] = json!({ "follows": [] }))),
        ("workflow.nodes.task.", Box::new(|v| v["workflow"]["nodes"]["task"] = json!({ "tool": "list" }))),
        (
            "workflow.nodes.manifest.followed_by",
            Box::new(|v| v["workflow"]["nodes"]["manifest"]["followed_by"] = json!(["task"])),
        ),
        ("workflow.nodes.propose.", Box::new(|v| v["workflow"]["nodes"]["propose"]["args"] = json!({}))),
        ("workflow.nodes.propose.max_fires", Box::new(|v| v["workflow"]["nodes"]["propose"]["max_fires"] = json!(0))),
        ("workflow.nodes.survey.max_fires", Box::new(|v| v["workflow"]["nodes"]["survey"]["max_fires"] = json!(null))),
        (
            "workflow.nodes.propose.model.instructions",
            Box::new(|v| v["workflow"]["nodes"]["propose"]["model"]["instructions"] = json!({})),
        ),
        (
            "workflow.nodes.propose.model.grants.read[0]",
            Box::new({
                let root = root.clone();
                move |v| v["grants"]["read"] = json!([root.join("child")])
            }),
        ),
        (
            "workflow.nodes.inner.workflow.nodes.a.tool",
            Box::new(|v| {
                v["workflow"]["nodes"]["inner"] = json!({ "workflow": { "nodes": { "a": { "tool": "ghost" } } } })
            }),
        ),
    ];
    for (key, edit) in cases {
        assert_eq!(rejected(with_graph(&root, edit)), key);
    }
    let unknown = with_graph(&root, |v| v["workflow"]["recovery"] = json!({ "surprise": 1 }));
    assert!(serde_json::from_value::<Config>(unknown).is_err(), "unknown keys are refused");
}

/// docs/workflow.md "Identity": the labels, the edges, the bindings, the
/// model programs, and the runtime's recovery instruction participate.
#[test]
fn identity_hashes_the_graph_and_the_recovery_texts() {
    let root = tmp("workflow-identity");
    let runtime = RuntimeInfo { version: "0".into(), build: "unknown".into() };
    let hash = |edit: &dyn Fn(&mut Value)| {
        let mut value = with_graph(&root, |_| {});
        edit(&mut value);
        let config: Config = serde_json::from_value(value).unwrap();
        compute(&resolve(&config).unwrap(), &[], &runtime).unwrap()
    };
    let base = hash(&|_| {});
    let plain = compute(&program_with(&root, |_| {}).unwrap(), &[], &runtime).unwrap();
    assert_ne!(base.hash, plain.hash, "a workflow participates");
    let texts = &base.document["workflow"]["texts"];
    for (key, text) in crate::harness_text::workflow_texts() {
        assert_eq!(texts[key], json!(text));
    }
    assert!(base.document["workflow"]["nodes"]["propose"]["model"].as_str().unwrap().starts_with("sha256:"));
    assert_eq!(base.document["workflow"]["nodes"]["derive"]["max_fires"], json!(1));
    let relabeled =
        hash(&|v| v["workflow"]["nodes"]["propose"]["branches"] = json!({ "ok": ["derive"], "widen": ["survey"] }));
    assert_ne!(relabeled.hash, base.hash, "labels participate");
    let rebound = hash(&|v| v["workflow"]["nodes"]["survey"]["args"]["pattern"]["pointer"] = json!("/other"));
    assert_ne!(rebound.hash, base.hash, "bindings participate");
    let widened = hash(&|v| v["workflow"]["nodes"]["propose"]["recovery"] = json!({ "follows": ["manifest"] }));
    assert_ne!(widened.hash, base.hash, "recovery widening participates");
    let capped = hash(&|v| v["workflow"]["recovery"] = json!({ "max_interventions": 1 }));
    assert_ne!(capped.hash, base.hash, "max_interventions participates");
    let reprogrammed = hash(&|v| v["workflow"]["nodes"]["propose"]["model"]["instructions"]["r"] = json!("Other."));
    assert_ne!(reprogrammed.hash, base.hash, "a model node's program participates");
}

/// docs/workflow.md "Firing": the possible-firing count sums each node's
/// effective `max_fires`, and a nested workflow node multiplies its own
/// count by one plus the count of the graph it holds. A graph above the
/// runtime bound is refused before anything runs.
#[test]
fn a_graph_declares_how_many_firings_it_can_perform() {
    let plain: crate::workflow::WorkflowConfig = serde_json::from_value(json!({ "nodes": {
        "a": { "tool": "t" },
        "b": { "tool": "t", "follows": ["a"], "max_fires": 3 }
    } }))
    .unwrap();
    assert_eq!(plain.possible_firings(), 4, "one firing of `a` and three of `b`");

    let nested: crate::workflow::WorkflowConfig = serde_json::from_value(json!({ "nodes": {
        "outer": { "max_fires": 3, "workflow": { "nodes": {
            "inner": { "tool": "t", "max_fires": 2, "terminal": true }
        } } }
    } }))
    .unwrap();
    assert_eq!(nested.possible_firings(), 9, "three firings of `outer`, each running two of `inner`");

    let root = tmp("workflow-firing-bound");
    let over = with_graph(&root, |value| {
        value["workflow"] = json!({ "nodes": {
            "loop": { "tool": "list", "followed_by": ["back"], "max_fires": 4096 },
            "back": { "tool": "grep", "followed_by": ["loop"], "max_fires": 4096, "terminal": true }
        } });
    });
    let (key, rule) = rejection(over);
    assert_eq!(key, "workflow");
    assert!(rule.contains("8192 possible firings"), "{rule}");
    assert!(rule.contains(&MAX_POSSIBLE_FIRINGS.to_string()), "{rule}");
}
