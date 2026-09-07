use super::{configuration_warnings, cycles, execution, root_agent_report, summary_report};
use foe_contract::workflow::WorkflowConfig;
use serde_json::json;

/// Every readiness-summary line projects the resolved objects the
/// detailed report prints, so the summary cannot disagree with the
/// detail: grants, budget, completion, sandbox mode, and the warning
/// count all come from the one resolved contract.
#[test]
fn summary_lines_project_the_resolved_contract() {
    let dir = crate::tests::scratch("foe-cli-plan", "summary");
    let root = dir.as_ref().to_path_buf();
    let document: foe_contract::ContractDocument = serde_json::from_value(json!({
        "version": 4,
        "name": "summary",
        "instructions": { "role": "work" },
        "task": "count",
        "tools": ["bash"],
        "grants": { "read": [root], "write": [root] },
        "budget": { "model_calls": 80, "seconds": 3600 },
        "done_when": { "returns": { "type": "object" } },
    }))
    .unwrap();
    let contract = foe_contract::document::resolve(&document).unwrap();
    let warnings = configuration_warnings(&contract);
    let out = summary_report(&contract, None, &warnings);
    let line = |label: &str| out.lines().find(|l| l.starts_with(label)).map(str::to_string).unwrap_or_default();
    let canonical_root = contract.grants.read[0].display().to_string();
    assert!(line("model").contains("answered by the host over the protocol"));
    assert!(line("read").contains(&canonical_root));
    assert!(line("write").contains(&canonical_root));
    assert!(line("execute").contains("(none: shell built-ins only)"));
    assert!(line("completion").contains("typed return"));
    assert!(line("limits").contains("80 model calls"));
    assert!(line("limits").contains("3600s"));
    assert!(line("limits").contains(&format!("loop threshold {}", contract.budget.loop_threshold)));
    assert!(line("sandbox").contains("best-effort"));
    assert_eq!(warnings.len(), 1, "the empty execute grant beside a shell tool is statically known");
    assert!(line("warnings").contains("1: external-commands-unavailable"));
    assert!(line("warnings").contains("grants.execute"));
    assert!(line("execution").contains("one root-bound agent node"));
    assert_eq!(execution(&contract)["kind"], "root-agent");
    assert!(root_agent_report(&contract).contains("root-agent   model summary  follows task  terminal  root episode"));
}

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
