//! Properties of a configured contract tree that `foe plan` reports: every
//! distinct tool definition the tree can invoke with the paths that may
//! call it, every elementary workflow cycle, and every pair of nodes whose
//! write roots overlap. Each is read from the document and its resolution
//! alone, which is what makes them reportable before anything runs. They
//! encode one rule the executor in `foe-workflow` realizes as well: a
//! workflow model node is reachable because firing it starts that node's
//! resolved episode contract.

use crate::document::ResolvedContract;
use crate::tools::{resolve_sources, resolve_specs, Source};
use crate::workflow::WorkflowConfig;
use crate::{Effect, ToolSpec};
use serde::Serialize;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

/// One tool definition and every contract path that may call it.
#[derive(Debug, Serialize)]
pub struct Authority {
    pub name: String,
    pub source: &'static str,
    pub effect: Effect,
    pub definition: Value,
    pub child_contracts: BTreeSet<String>,
}

/// A row is keyed by name, source, definition body, and effect together, so
/// that two child contracts that define one name differently stay apart.
type AuthorityKey = (String, &'static str, String, String);

/// Every distinct tool definition reachable from `root`, with the contract
/// paths that may call it. A child contract is reachable only when its
/// parent names it in `grants.spawn`. A workflow model node is reachable
/// because firing it starts that node's episode.
pub fn authority(root: &ResolvedContract, extra: &[ToolSpec]) -> Result<Vec<Authority>, String> {
    let mut found: BTreeMap<AuthorityKey, Authority> = BTreeMap::new();
    collect_authority(root, "contract", extra, &mut found)?;
    Ok(found.into_values().collect())
}

/// Where each name in `contract.tools` resolved, in `tools` order. The
/// built-in names are the blocking tool and the packs the binary links.
pub fn tool_sources(contract: &ResolvedContract, extra: &[ToolSpec]) -> Result<Vec<Source>, String> {
    let mut builtins: Vec<&str> = vec![crate::harness_text::BLOCK_NAME];
    builtins.extend(extra.iter().map(|s| s.name.as_str()));
    let configured: Vec<&str> = contract.tool_defs.keys().map(String::as_str).collect();
    let host: Vec<&str> = contract.host_tools.keys().map(String::as_str).collect();
    resolve_sources(&contract.tools, &builtins, &configured, &host).map_err(|e| e.to_string())
}

fn collect_authority(
    contract: &ResolvedContract,
    path: &str,
    extra: &[ToolSpec],
    found: &mut BTreeMap<AuthorityKey, Authority>,
) -> Result<(), String> {
    let specs = resolve_specs(contract, extra).map_err(|e| e.to_string())?;
    let sources = tool_sources(contract, extra)?;
    for ((name, source), spec) in contract.tools.iter().zip(sources).zip(specs) {
        let (source, definition) = match source {
            Source::Builtin => ("built-in", serde_json::to_value(&spec)),
            Source::Configured => ("configured", serde_json::to_value(&contract.tool_defs[name])),
            Source::Host => ("host", serde_json::to_value(&contract.host_tools[name])),
        };
        let definition = definition.map_err(|e| e.to_string())?;
        let key = (name.clone(), source, definition.to_string(), format!("{:?}", spec.effect));
        let row =
            Authority { name: name.clone(), source, effect: spec.effect, definition, child_contracts: BTreeSet::new() };
        found.entry(key).or_insert(row).child_contracts.insert(path.to_string());
    }
    for name in &contract.grants.spawn {
        if let Some(child) = contract.child_contracts.get(name) {
            collect_authority(child, &format!("{path}.child_contracts.{name}"), extra, found)?;
        }
    }
    if let Some(wf) = &contract.workflow {
        for (node, _) in crate::workflow::model_nodes(wf, "") {
            let child_path = format!("{path}.workflow.nodes.{}.model", node.replace('/', ".workflow.nodes."));
            let child = &contract.workflow_contracts[&node];
            collect_authority(child, &child_path, extra, found)?;
        }
    }
    Ok(())
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
/// write roots of the contract declaring the graph, which is every root such
/// a call could reach.
pub fn write_overlaps(contract: &ResolvedContract, extra: &[ToolSpec]) -> Result<Vec<Overlap>, String> {
    let mut writers = Vec::new();
    if let Some(wf) = &contract.workflow {
        collect_writers(contract, wf, "", extra, &mut writers)?;
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
    contract: &ResolvedContract,
    wf: &WorkflowConfig,
    prefix: &str,
    extra: &[ToolSpec],
    out: &mut Vec<(String, Vec<PathBuf>)>,
) -> Result<(), String> {
    let effects: BTreeMap<String, Effect> = resolve_specs(contract, extra)
        .map_err(|e| e.to_string())?
        .into_iter()
        .map(|spec| (spec.name, spec.effect))
        .collect();
    for (name, node) in &wf.nodes {
        let full = format!("{prefix}{name}");
        if node.model.is_some() {
            out.push((full.clone(), contract.workflow_contracts[&full].grants.write.clone()));
        } else if node.tool.as_ref().and_then(|tool| effects.get(tool)).is_some_and(|e| !e.concurrent()) {
            out.push((full, contract.grants.write.clone()));
        } else if let Some(inner) = &node.workflow {
            collect_writers(contract, inner, &format!("{full}/"), extra, out)?;
        }
    }
    Ok(())
}
