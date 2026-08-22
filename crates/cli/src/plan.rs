//! The reports `foe plan` prints below a resolved program: the workflow's
//! nodes, its edges, every cycle with the bound that closes it, every pair
//! of nodes whose write roots overlap, whether a terminal node exists, and
//! every tool definition the program's reachable tree can invoke. See
//! docs/workflow.md "Firing" and "The flow guarantee, stated exactly", and
//! docs/design.md "Subagents and teams".

use crate::run;
use foe_core::config::{resolve_node_program, Program};
use foe_core::registry::{block_spec, resolve_sources, resolve_specs, Source};
use foe_core::workflow::{WorkflowConfig, TASK_SOURCE};
use foe_core::{Effect, ToolSpec};
use serde::Serialize;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write;
use std::path::PathBuf;

/// One tool definition and every program path that may call it.
#[derive(Debug, Serialize)]
pub struct Authority {
    name: String,
    source: &'static str,
    effect: Effect,
    definition: Value,
    programs: Vec<String>,
}

/// A row is keyed by name, source, definition body, and effect together, so
/// that two programs that define one name differently stay apart.
type AuthorityKey = (String, &'static str, String, String);
type AuthorityValue = (Value, Effect, BTreeSet<String>);

/// Every distinct tool definition reachable from `root`, with the program
/// paths that may call it. A child program is reachable only when its
/// parent names it in `grants.spawn`. A workflow model node is reachable
/// because firing it starts that node's episode.
pub fn authority(root: &Program) -> Result<Vec<Authority>, String> {
    let extra = run::extra_builtin_specs();
    let mut found: BTreeMap<AuthorityKey, AuthorityValue> = BTreeMap::new();
    collect_authority(root, "program", &extra, &mut found)?;
    Ok(found
        .into_iter()
        .map(|((name, source, _, _), (definition, effect, programs))| Authority {
            name,
            source,
            effect,
            definition,
            programs: programs.into_iter().collect(),
        })
        .collect())
}

fn collect_authority(
    program: &Program,
    path: &str,
    extra: &[ToolSpec],
    found: &mut BTreeMap<AuthorityKey, AuthorityValue>,
) -> Result<(), String> {
    let specs = resolve_specs(program, extra).map_err(|e| e.to_string())?;
    let block = block_spec();
    let builtins: Vec<&str> =
        std::iter::once(block.name.as_str()).chain(extra.iter().map(|s| s.name.as_str())).collect();
    let configured: Vec<&str> = program.tool_defs.keys().map(String::as_str).collect();
    let host: Vec<&str> = program.host_tools.keys().map(String::as_str).collect();
    let sources = resolve_sources(&program.tools, &builtins, &configured, &host).map_err(|e| e.to_string())?;
    for ((name, source), spec) in program.tools.iter().zip(sources).zip(specs) {
        let (source, definition) = match source {
            Source::Builtin => ("built-in", serde_json::to_value(&spec)),
            Source::Configured => ("configured", serde_json::to_value(&program.tool_defs[name])),
            Source::Host => ("host", serde_json::to_value(&program.host_tools[name])),
        };
        let definition = definition.map_err(|e| e.to_string())?;
        let effect = serde_json::to_value(spec.effect).map_err(|e| e.to_string())?.to_string();
        let key = (name.clone(), source, definition.to_string(), effect);
        found.entry(key).or_insert_with(|| (definition, spec.effect, BTreeSet::new())).2.insert(path.to_string());
    }
    for name in &program.grants.spawn {
        if let Some(child) = program.programs.get(name) {
            collect_authority(child, &format!("{path}.programs.{name}"), extra, found)?;
        }
    }
    if let Some(wf) = &program.workflow {
        for (node, node_program) in foe_workflow::model_nodes(wf, "") {
            let child_path = format!("{path}.workflow.nodes.{}.model", node.replace('/', ".workflow.nodes."));
            let child = resolve_node_program(&child_path, program, &foe_workflow::node_program(node_program))
                .map_err(|e| e.to_string())?;
            collect_authority(&child, &child_path, extra, found)?;
        }
    }
    Ok(())
}

/// The authority report `foe plan` prints below the program. One line per
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
        let name = &row.name;
        let (source, effect) = (row.source, effect.unwrap_or_default());
        let line = format!("  {name:<12} {source:<10} {effect:<7} {body}");
        writeln!(out, "{}", line.trim_end()).ok();
        writeln!(out, "               programs {}", row.programs.join(", ")).ok();
    }
    out
}

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

type Overlap = (String, String, String, String);

/// Pairs of nodes, by path, with a write root of one lying within a write
/// root of the other, and the two roots. A model node contributes its own
/// write roots. A tool node whose effect is not concurrent contributes the
/// write roots of the program declaring the graph, which is every root such
/// a call could reach.
pub fn write_overlaps(program: &Program) -> Result<Vec<Overlap>, String> {
    let mut writers = Vec::new();
    if let Some(wf) = &program.workflow {
        collect_writers(program, wf, "", &mut writers)?;
    }
    let mut pairs = Vec::new();
    for (i, (a, xs)) in writers.iter().enumerate() {
        for (b, ys) in &writers[i + 1..] {
            for x in xs {
                for y in ys {
                    if x.starts_with(y) || y.starts_with(x) {
                        pairs.push((a.clone(), b.clone(), x.display().to_string(), y.display().to_string()));
                    }
                }
            }
        }
    }
    Ok(pairs)
}

fn collect_writers(
    program: &Program,
    wf: &WorkflowConfig,
    prefix: &str,
    out: &mut Vec<(String, Vec<PathBuf>)>,
) -> Result<(), String> {
    let effects: BTreeMap<String, Effect> = resolve_specs(program, &run::extra_builtin_specs())
        .map_err(|e| e.to_string())?
        .into_iter()
        .map(|spec| (spec.name, spec.effect))
        .collect();
    for (name, node) in &wf.nodes {
        let full = format!("{prefix}{name}");
        if let Some(model) = &node.model {
            let resolved = resolve_node_program(&format!("workflow.nodes.{full}.model"), program, model)
                .map_err(|e| e.to_string())?;
            out.push((full, resolved.grants.write));
        } else if node.tool.as_ref().and_then(|tool| effects.get(tool)).is_some_and(|e| !e.concurrent()) {
            out.push((full, program.grants.write.clone()));
        } else if let Some(inner) = &node.workflow {
            collect_writers(program, inner, &format!("{full}/"), out)?;
        }
    }
    Ok(())
}

/// The workflow report `foe plan` prints below the program. The built-in
/// `task` source is listed among the nodes when any node follows it.
pub fn workflow_report(program: &Program) -> Result<String, String> {
    let wf = program.workflow.as_ref().expect("called for a program that declares a workflow");
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
    out.push_str("workflow write roots shared by nodes\n");
    let overlaps = write_overlaps(program)?;
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
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::cycles;
    use foe_core::workflow::WorkflowConfig;
    use serde_json::json;

    /// docs/workflow.md "Firing": the report lists each elementary cycle
    /// once, beginning with the smallest node name in that cycle.
    #[test]
    fn cycle_enumeration_is_stable() {
        let workflow: WorkflowConfig = serde_json::from_value(json!({ "nodes": {
            "start": { "tool": "t", "followed_by": ["a"] },
            "a": { "tool": "t", "followed_by": ["b"], "max_fires": 2 },
            "b": { "tool": "t", "followed_by": ["a", "c"], "max_fires": 2 },
            "c": { "tool": "t", "followed_by": ["b"], "max_fires": 2 }
        } }))
        .unwrap();
        assert_eq!(
            cycles(&workflow),
            vec![vec!["a".to_string(), "b".to_string()], vec!["b".to_string(), "c".to_string()]]
        );
    }
}
