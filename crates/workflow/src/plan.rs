//! The workflow part of `foe plan`: the nodes, the edges, every cycle with
//! the bound that closes it, every pair of model nodes whose write roots
//! overlap, and whether a terminal node exists. See docs/workflow.md
//! "Firing" and "The flow guarantee, stated exactly".

use foe_core::workflow::{Node, WorkflowConfig, TASK_SOURCE};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write;

/// Every elementary cycle, each listed once starting from its smallest
/// node name, as the sequence of nodes along it.
pub fn cycles(wf: &WorkflowConfig) -> Vec<Vec<String>> {
    let mut succs: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for (target, sources) in wf.predecessors() {
        for source in sources {
            succs.entry(source).or_default().insert(target.clone());
        }
    }
    fn walk(
        start: &str,
        path: &mut Vec<String>,
        succs: &BTreeMap<String, BTreeSet<String>>,
        out: &mut Vec<Vec<String>>,
    ) {
        let last = path.last().cloned().expect("a path has a start");
        for next in succs.get(&last).into_iter().flatten() {
            if next == start {
                out.push(path.clone());
            } else if next.as_str() > start && !path.contains(next) {
                path.push(next.clone());
                walk(start, path, succs, out);
                path.pop();
            }
        }
    }
    let mut found = Vec::new();
    for start in wf.nodes.keys() {
        walk(start, &mut vec![start.clone()], &succs, &mut found);
    }
    found
}

/// Pairs of model nodes, by path, with a write root of one lying within a
/// write root of the other, and the two roots.
pub fn write_overlaps(wf: &WorkflowConfig) -> Vec<(String, String, String, String)> {
    let nodes = crate::model_nodes(wf, "");
    let mut pairs = Vec::new();
    let roots = |n: &Node| n.model.as_ref().map(|p| p.grants.write.clone()).unwrap_or_default();
    for (i, (a, a_node)) in nodes.iter().enumerate() {
        for (b, b_node) in &nodes[i + 1..] {
            for x in roots(a_node) {
                for y in roots(b_node) {
                    if x.starts_with(&y) || y.starts_with(&x) {
                        pairs.push((a.clone(), b.clone(), x.display().to_string(), y.display().to_string()));
                    }
                }
            }
        }
    }
    pairs
}

/// The report `foe plan` prints below the program. The built-in `task`
/// source is listed among the nodes when any node follows it.
pub fn plan_report(wf: &WorkflowConfig) -> String {
    let mut out = String::from("workflow nodes\n");
    let inputs = wf.inputs();
    if inputs.values().flatten().any(|i| i == TASK_SOURCE) {
        writeln!(out, "  {TASK_SOURCE:<12} built-in source: the invocation task").ok();
    }
    for (name, node) in &wf.nodes {
        let kind = match (&node.tool, &node.model) {
            (Some(tool), _) => format!("tool {tool}"),
            (_, Some(program)) => format!("model {}", program.name),
            _ => "workflow".to_string(),
        };
        let mut line = format!("  {name:<12} {kind}");
        if !inputs[name].is_empty() {
            write!(line, "  follows {}", inputs[name].join(", ")).ok();
        }
        if let Some(verify) = &node.verify {
            write!(line, "  verify {verify} (retries {})", node.retries).ok();
        }
        if !node.branches.is_empty() {
            let labels: Vec<String> = node.branches.iter().map(|(l, s)| format!("{l} -> [{}]", s.join(", "))).collect();
            write!(line, "  branches {}", labels.join("; ")).ok();
        }
        if let Some(n) = node.max_fires {
            write!(line, "  max_fires {n}").ok();
        }
        let flags = [(node.terminal, "  terminal"), (node.empty.is_some(), "  empty")];
        line.extend(flags.iter().filter(|(set, _)| *set).map(|(_, flag)| *flag));
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
    out.push_str("workflow write roots shared by model nodes\n");
    let overlaps = write_overlaps(wf);
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
    out
}

#[cfg(test)]
#[path = "plan_test.rs"]
mod tests;
