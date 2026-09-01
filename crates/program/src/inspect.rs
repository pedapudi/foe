//! Properties of a configured program tree that `foe plan` reports: every
//! distinct tool definition the tree can invoke with the paths that may
//! call it, every elementary workflow cycle, and every pair of nodes whose
//! write roots overlap. Each is read from the document and its resolution
//! alone, which is what makes them reportable before anything runs. They
//! encode one rule the executor in `foe-workflow` realizes as well: a
//! workflow model node is reachable because firing it starts that node's
//! resolved episode program.

use crate::document::ResolvedProgram;
use crate::tools::{resolve_sources, resolve_specs, Source};
use crate::workflow::WorkflowConfig;
use crate::{Effect, ToolSpec};
use serde::Serialize;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

/// One tool definition and every program path that may call it.
#[derive(Debug, Serialize)]
pub struct Authority {
    pub name: String,
    pub source: &'static str,
    pub effect: Effect,
    pub definition: Value,
    pub programs: BTreeSet<String>,
}

/// A row is keyed by name, source, definition body, and effect together, so
/// that two programs that define one name differently stay apart.
type AuthorityKey = (String, &'static str, String, String);

/// Every distinct tool definition reachable from `root`, with the program
/// paths that may call it. A child program is reachable only when its
/// parent names it in `grants.spawn`. A workflow model node is reachable
/// because firing it starts that node's episode.
pub fn authority(root: &ResolvedProgram, extra: &[ToolSpec]) -> Result<Vec<Authority>, String> {
    let mut found: BTreeMap<AuthorityKey, Authority> = BTreeMap::new();
    for (path, program) in reachable_programs(root) {
        collect_authority(program, &path, extra, &mut found)?;
    }
    Ok(found.into_values().collect())
}

/// The root and every program reachable through a declared spawn edge or a
/// workflow model node. Each row carries the path printed by `foe plan`.
pub fn reachable_programs(root: &ResolvedProgram) -> Vec<(String, &ResolvedProgram)> {
    fn collect<'a>(program: &'a ResolvedProgram, path: String, found: &mut Vec<(String, &'a ResolvedProgram)>) {
        found.push((path.clone(), program));
        for name in &program.grants.spawn {
            if let Some(child) = program.programs.get(name) {
                collect(child, format!("{path}.programs.{name}"), found);
            }
        }
        if let Some(workflow) = &program.workflow {
            for (node, _) in crate::workflow::model_nodes(workflow, "") {
                let child_path = format!("{path}.workflow.nodes.{}.model", node.replace('/', ".workflow.nodes."));
                collect(&program.workflow_programs[&node], child_path, found);
            }
        }
    }
    let mut found = Vec::new();
    collect(root, "program".into(), &mut found);
    found
}

/// Where each name in `program.tools` resolved, in `tools` order. The
/// built-in names are the blocking tool and the packs the binary links.
pub fn tool_sources(program: &ResolvedProgram, extra: &[ToolSpec]) -> Result<Vec<Source>, String> {
    let mut builtins: Vec<&str> = vec![crate::harness_text::BLOCK_NAME];
    builtins.extend(extra.iter().map(|s| s.name.as_str()));
    let configured: Vec<&str> = program.tool_defs.keys().map(String::as_str).collect();
    let host: Vec<&str> = program.host_tools.keys().map(String::as_str).collect();
    resolve_sources(&program.tools, &builtins, &configured, &host).map_err(|e| e.to_string())
}

fn collect_authority(
    program: &ResolvedProgram,
    path: &str,
    extra: &[ToolSpec],
    found: &mut BTreeMap<AuthorityKey, Authority>,
) -> Result<(), String> {
    let specs = resolve_specs(program, extra).map_err(|e| e.to_string())?;
    let sources = tool_sources(program, extra)?;
    for ((name, source), spec) in program.tools.iter().zip(sources).zip(specs) {
        let (source, definition) = match source {
            Source::Builtin => ("built-in", serde_json::to_value(&spec)),
            Source::Configured => ("configured", serde_json::to_value(&program.tool_defs[name])),
            Source::Host => ("host", serde_json::to_value(&program.host_tools[name])),
        };
        let definition = definition.map_err(|e| e.to_string())?;
        let key = (name.clone(), source, definition.to_string(), format!("{:?}", spec.effect));
        let row = Authority { name: name.clone(), source, effect: spec.effect, definition, programs: BTreeSet::new() };
        found.entry(key).or_insert(row).programs.insert(path.to_string());
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
/// write roots of the program declaring the graph, which is every root such
/// a call could reach.
pub fn write_overlaps(program: &ResolvedProgram, extra: &[ToolSpec]) -> Result<Vec<Overlap>, String> {
    let mut writers = Vec::new();
    if let Some(wf) = &program.workflow {
        collect_writers(program, wf, "", extra, &mut writers)?;
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
    program: &ResolvedProgram,
    wf: &WorkflowConfig,
    prefix: &str,
    extra: &[ToolSpec],
    out: &mut Vec<(String, Vec<PathBuf>)>,
) -> Result<(), String> {
    let effects: BTreeMap<String, Effect> = resolve_specs(program, extra)
        .map_err(|e| e.to_string())?
        .into_iter()
        .map(|spec| (spec.name, spec.effect))
        .collect();
    for (name, node) in &wf.nodes {
        let full = format!("{prefix}{name}");
        if node.model.is_some() {
            out.push((full.clone(), program.workflow_programs[&full].grants.write.clone()));
        } else if node.tool.as_ref().and_then(|tool| effects.get(tool)).is_some_and(|e| !e.concurrent()) {
            out.push((full, program.grants.write.clone()));
        } else if let Some(inner) = &node.workflow {
            collect_writers(program, inner, &format!("{full}/"), extra, out)?;
        }
    }
    Ok(())
}
