//! The reports `foe plan` prints below a resolved contract: the workflow's
//! nodes, its edges, every cycle with the bound that closes it, every pair
//! of nodes whose write roots overlap, whether a terminal node exists, and
//! every tool definition the contract's reachable tree can invoke. See
//! docs/workflow.md "Firing" and "The flow guarantee, stated exactly", and
//! docs/design.md "Subagents and teams".

use crate::run;
use foe_contract::document::ResolvedContract;
use foe_contract::workflow::{MAX_EDGE_REFERENCES, MAX_POSSIBLE_FIRINGS, TASK_SOURCE};
use std::fmt::Write;

pub use foe_contract::inspect::{cycles, tool_sources, Authority};

/// Every distinct tool definition reachable from `root`, with the binary's
/// extra built-in packs supplied. See `foe_contract::inspect`.
pub fn authority(root: &ResolvedContract) -> Result<Vec<Authority>, String> {
    foe_contract::inspect::authority(root, &run::extra_builtin_specs())
}

pub fn write_overlaps(contract: &ResolvedContract) -> Result<Vec<(String, String, String, String)>, String> {
    foe_contract::inspect::write_overlaps(contract, &run::extra_builtin_specs())
}

/// The authority report `foe plan` prints below the contract. One line per
/// definition, naming what distinguishes it: the executable a configured
/// tool runs, the description a host tool declares, and nothing for a
/// built-in, whose definition the runtime fixes. `--json` carries the whole
/// definition of each row.
pub fn authority_report(rows: &[Authority]) -> String {
    let mut out = String::from("effective tool authority\n");
    for row in rows {
        let effect = serde_json::to_value(row.effect).ok().and_then(|v| v.as_str().map(str::to_string));
        let body = match row.source {
            "configured" => row.definition["exec"].as_str().unwrap_or_default().to_string(),
            "host" => row.definition["description"].as_str().unwrap_or_default().to_string(),
            _ => String::new(),
        };
        let line = format!("  {:<12} {:<10} {:<7} {body}", row.name, row.source, effect.unwrap_or_default());
        writeln!(out, "{}", line.trim_end()).ok();
        let child_contracts: Vec<&str> = row.child_contracts.iter().map(String::as_str).collect();
        writeln!(out, "               child_contracts {}", child_contracts.join(", ")).ok();
    }
    out
}

/// The workflow report `foe plan` prints below the contract. The built-in
/// `task` source is listed among the nodes when any node follows it.
pub fn workflow_report(contract: &ResolvedContract) -> Result<String, String> {
    let wf = contract.workflow.as_ref().expect("called for a contract that declares a workflow");
    let mut out = String::from("workflow nodes\n");
    let inputs = wf.inputs();
    if inputs.values().flatten().any(|i| i == TASK_SOURCE) {
        writeln!(out, "  {TASK_SOURCE:<12} built-in source: the invocation task").ok();
    }
    for (name, node) in &wf.nodes {
        let kind = match (&node.tool, &node.model) {
            (Some(tool), _) => format!("tool {tool}"),
            (_, Some(contract)) => format!("model {}", contract.name),
            _ => "workflow".to_string(),
        };
        let branches: Vec<String> = node.branches.iter().map(|(l, s)| format!("{l} -> [{}]", s.join(", "))).collect();
        // Each part appears only when the node declares it, in this order.
        let parts = [
            (!inputs[name].is_empty()).then(|| format!("follows {}", inputs[name].join(", "))),
            node.verify.as_ref().map(|verify| format!("verify {verify} (retries {})", node.retries)),
            (!branches.is_empty()).then(|| format!("branches {}", branches.join("; "))),
            node.max_fires.map(|n| format!("max_fires {n}")),
            node.terminal.then(|| "terminal".to_string()),
            node.empty.is_some().then(|| "empty".to_string()),
        ];
        let mut line = format!("  {name:<12} {kind}");
        for part in parts.iter().flatten() {
            write!(line, "  {part}").ok();
        }
        writeln!(out, "{line}").ok();
    }
    out.push_str("workflow edges\n");
    for (target, sources) in wf.predecessors() {
        for source in sources {
            let labels: Vec<&str> = wf.nodes[&source]
                .branches
                .iter()
                .filter(|(_, list)| list.contains(&target))
                .map(|(l, _)| l.as_str())
                .collect();
            let under = if labels.is_empty() { String::new() } else { format!("  ({})", labels.join(", ")) };
            writeln!(out, "  {source} -> {target}{under}").ok();
        }
    }
    out.push_str("workflow cycles\n");
    let found = cycles(wf);
    for cycle in &found {
        let bounds: Vec<String> = cycle.iter().map(|n| format!("{n} {}", wf.nodes[n].max_fires.unwrap_or(1))).collect();
        writeln!(out, "  {} -> {}  bounded by max_fires {}", cycle.join(" -> "), cycle[0], bounds.join(", ")).ok();
    }
    if found.is_empty() {
        out.push_str("  (none)\n");
    }
    out.push_str("workflow write roots shared by nodes\n");
    let overlaps = write_overlaps(contract)?;
    for (a, b, x, y) in &overlaps {
        writeln!(out, "  {a} and {b}: {x} and {y}").ok();
    }
    if overlaps.is_empty() {
        out.push_str("  (none)\n");
    }
    let terminals: Vec<&str> = wf.nodes.iter().filter(|(_, n)| n.terminal).map(|(k, _)| k.as_str()).collect();
    let empty_branch = wf.nodes.values().any(|n| n.branches.values().any(Vec::is_empty));
    let completion = match (terminals.is_empty(), empty_branch) {
        (false, _) => format!("terminal {}", terminals.join(", ")),
        (true, true) => "an empty branch".to_string(),
        (true, false) => "no terminal node and no empty branch: runs until the budget is spent".to_string(),
    };
    writeln!(out, "workflow completion  {completion}").ok();
    writeln!(out, "workflow references  {} declared, at most {MAX_EDGE_REFERENCES}", wf.edge_references()).ok();
    writeln!(out, "workflow firings     {} possible, at most {MAX_POSSIBLE_FIRINGS}", wf.possible_firings()).ok();
    Ok(out)
}

#[cfg(test)]
#[path = "plan_test.rs"]
mod tests;
