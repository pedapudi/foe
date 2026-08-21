use super::{cycles, plan_report, write_overlaps};
use foe_core::workflow::WorkflowConfig;
use serde_json::json;

fn program(name: &str, write: &str) -> serde_json::Value {
    json!({ "name": name, "instructions": { "r": "x" }, "tools": ["block"],
            "grants": { "read": ["/p"], "write": [write] }, "budget": { "model_calls": 1 } })
}

/// docs/workflow.md "Firing" and "The flow guarantee, stated exactly":
/// `foe plan` reports every cycle with its bound, every pair of model
/// nodes whose write roots overlap, and the absence of a terminal node.
#[test]
fn the_report_names_cycles_bounds_overlaps_and_completion() {
    let wf: WorkflowConfig = serde_json::from_value(json!({ "nodes": {
        "manifest": { "tool": "list" },
        "survey": { "tool": "grep", "follows": ["manifest"], "max_fires": 3 },
        "propose": { "model": program("propose", "/p/src"), "follows": ["manifest", "survey", "task"],
                     "branches": { "accept": ["derive"], "widen": ["survey"] }, "max_fires": 3 },
        "review": { "model": program("review", "/p/src/lib"), "follows": ["propose"] },
        "derive": { "tool": "derive", "follows": ["propose"], "verify": "check", "terminal": true, "empty": null }
    } }))
    .unwrap();
    assert_eq!(cycles(&wf), vec![vec!["propose".to_string(), "survey".to_string()]]);
    let overlaps = write_overlaps(&wf);
    assert_eq!(overlaps, vec![("propose".into(), "review".into(), "/p/src".into(), "/p/src/lib".into())]);
    let report = plan_report(&wf);
    assert!(report.contains("propose -> survey -> propose  bounded by max_fires propose 3, survey 3"), "{report}");
    assert!(report.contains("propose and review: /p/src and /p/src/lib"), "{report}");
    assert!(report.contains("propose -> survey  (widen)"), "{report}");
    assert!(report.contains("verify check (retries 2)"), "{report}");
    assert!(report.contains("  task         built-in source: the invocation task\n"), "{report}");
    assert!(report.contains("follows task, manifest, survey"), "{report}");
    assert!(report.contains("workflow completion  terminal derive"), "{report}");

    let open: WorkflowConfig = serde_json::from_value(json!({ "nodes": {
        "watch": { "tool": "poll", "followed_by": ["act"], "max_fires": 9 },
        "act": { "tool": "do", "followed_by": ["watch"], "max_fires": 9 }
    } }))
    .unwrap();
    let report = plan_report(&open);
    assert!(report.contains("no terminal node and no empty branch"), "{report}");
    assert!(report.contains("act -> watch -> act  bounded by max_fires act 9, watch 9"), "{report}");
    assert!(report.contains("(none)"), "no overlaps: {report}");
    assert!(!report.contains("built-in source"), "no node follows task: {report}");
}
