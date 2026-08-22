//! Workflow configuration: the graph of nodes, the edge set derived from
//! it, and the construction rules of docs/workflow.md "The graph". The
//! executor lives in the `foe-workflow` crate; this module holds what the
//! document, validation, and identity need.

use crate::{ChildProgram, ConfigError};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::collections::{BTreeMap, BTreeSet};

/// The reserved name of the built-in source any node may follow: the
/// invocation task, available from the start and produced exactly once.
pub const TASK_SOURCE: &str = "task";
pub const WORKFLOW_FIRING_RULE: &str = "Each node contributes its effective max_fires. A nested workflow contributes its possible firings multiplied by the effective max_fires of its containing node.";

/// Static graph size and the maximum number of node firings it permits.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize)]
pub struct WorkflowStructure {
    pub nodes: u64,
    pub edges: u64,
    pub nested_depth: u64,
    pub possible_firings: u64,
}

pub const WORKFLOW_CEILINGS: WorkflowStructure =
    WorkflowStructure { nodes: 256, edges: 1024, nested_depth: 8, possible_firings: 4096 };

/// A declared graph. Model node programs are kept as written; the spawner
/// resolves each when the node fires, as it does for `programs`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowConfig {
    pub nodes: BTreeMap<String, Node>,
    #[serde(default)]
    pub recovery: RecoveryConfig,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct RecoveryConfig {
    pub enabled: bool,
    pub max_interventions: u32,
}

impl Default for RecoveryConfig {
    fn default() -> Self {
        Self { enabled: true, max_interventions: 3 }
    }
}

/// Widens what a node's recovery decision sees.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NodeRecovery {
    #[serde(default)]
    pub follows: Vec<String>,
}

/// One node. Exactly one of `tool`, `model`, and `workflow` is set, and
/// `args` only with `tool`. The other fields are the table in
/// docs/workflow.md "Nodes".
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Node {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub args: Option<Map<String, Value>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<ChildProgram>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub workflow: Option<WorkflowConfig>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub follows: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub followed_by: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub verify: Option<String>,
    #[serde(default = "crate::u32_default::<2>")]
    pub retries: u32,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub branches: BTreeMap<String, Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_fires: Option<u32>,
    #[serde(default)]
    pub terminal: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub empty: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recovery: Option<NodeRecovery>,
}

impl WorkflowConfig {
    /// Whether this graph or a nested graph contains a model node, each of
    /// which runs as a child episode when it fires.
    pub(crate) fn contains_model_node(&self) -> bool {
        self.nodes
            .values()
            .any(|node| node.model.is_some() || node.workflow.as_ref().is_some_and(Self::contains_model_node))
    }

    /// Counts the graph at every nested workflow node. Edges are
    /// distinct source-target pairs, including edges from `task`.
    pub fn structure(&self) -> WorkflowStructure {
        fn walk(wf: &WorkflowConfig, depth: u64) -> WorkflowStructure {
            let mut out = WorkflowStructure {
                nodes: wf.nodes.len() as u64,
                edges: edge_count(wf),
                nested_depth: depth,
                possible_firings: 0,
            };
            if depth > WORKFLOW_CEILINGS.nested_depth {
                return out;
            }
            for node in wf.nodes.values() {
                let fires = u64::from(node.max_fires.unwrap_or(1));
                let inner = node.workflow.as_ref().map(|w| walk(w, depth.saturating_add(1))).unwrap_or_default();
                out.possible_firings =
                    out.possible_firings.saturating_add(fires.saturating_mul(inner.possible_firings.saturating_add(1)));
                out.nodes = out.nodes.saturating_add(inner.nodes);
                out.edges = out.edges.saturating_add(inner.edges);
                out.nested_depth = out.nested_depth.max(inner.nested_depth);
            }
            out
        }
        walk(self, 0)
    }

    /// The data inputs of every node: the `task` source first when the node
    /// follows it, then its other `follows`, then every node whose
    /// `followed_by` names it, in name order, each listed once.
    pub fn inputs(&self) -> BTreeMap<String, Vec<String>> {
        let mut inputs: BTreeMap<String, Vec<String>> =
            self.nodes.iter().map(|(name, node)| (name.clone(), node.follows.clone())).collect();
        for (source, node) in &self.nodes {
            for target in &node.followed_by {
                let list = inputs.entry(target.clone()).or_default();
                list.extend((!list.contains(source)).then(|| source.clone()));
            }
        }
        inputs.values_mut().for_each(|list| list.sort_by_key(|name| name != TASK_SOURCE));
        inputs
    }

    /// Every edge source of every node: data inputs and branch sources
    /// alike. The `task` source is absent, because it imposes no ordering.
    pub fn predecessors(&self) -> BTreeMap<String, BTreeSet<String>> {
        let mut preds: BTreeMap<String, BTreeSet<String>> = self
            .inputs()
            .into_iter()
            .map(|(name, list)| (name, list.into_iter().filter(|i| i != TASK_SOURCE).collect()))
            .collect();
        for (source, node) in &self.nodes {
            for target in node.branches.values().flatten() {
                preds.entry(target.clone()).or_default().insert(source.clone());
            }
        }
        preds
    }
}

fn edge_count(wf: &WorkflowConfig) -> u64 {
    let graph = wf.predecessors().values().map(BTreeSet::len).sum::<usize>();
    let task = wf.nodes.values().filter(|node| node.follows.iter().any(|source| source == TASK_SOURCE)).count();
    (graph + task) as u64
}

/// Every node reachable from `node` by walking `preds` backwards. Contains
/// `node` itself only when it lies on a cycle.
pub fn ancestors(preds: &BTreeMap<String, BTreeSet<String>>, node: &str) -> BTreeSet<String> {
    let mut seen = BTreeSet::new();
    let mut stack: Vec<&str> = preds.get(node).into_iter().flatten().map(String::as_str).collect();
    while let Some(name) = stack.pop() {
        if seen.insert(name.to_string()) {
            stack.extend(preds.get(name).into_iter().flatten().map(String::as_str));
        }
    }
    seen
}

/// The node name of every `$node` binding in `args`, at any depth.
pub fn bindings(value: &Value) -> Vec<String> {
    match value.get("$node").and_then(Value::as_str) {
        Some(name) => vec![name.to_string()],
        None => value.as_object().into_iter().flat_map(|object| object.values()).flat_map(bindings).collect(),
    }
}

fn valid(condition: bool, key: String, rule: String) -> Result<(), ConfigError> {
    condition.then_some(()).ok_or(ConfigError::Invalid { key, rule })
}

/// The rules of docs/workflow.md "The graph" that the document alone can
/// check, at every depth. `tools` is the ceiling the tool and verify names
/// resolve in. `program` is applied to every model node's program with its
/// dotted key; the caller validates or resolves it there.
pub fn check(
    prefix: &str,
    wf: &WorkflowConfig,
    tools: &[String],
    program: &mut dyn FnMut(&str, &ChildProgram) -> Result<(), ConfigError>,
) -> Result<(), ConfigError> {
    let structure = wf.structure();
    let limits = [
        ("nodes", structure.nodes, WORKFLOW_CEILINGS.nodes),
        ("distinct edges", structure.edges, WORKFLOW_CEILINGS.edges),
        ("nested workflow levels", structure.nested_depth, WORKFLOW_CEILINGS.nested_depth),
        ("possible firings", structure.possible_firings, WORKFLOW_CEILINGS.possible_firings),
    ];
    if let Some((name, count, limit)) = limits.into_iter().find(|(_, count, limit)| count > limit) {
        let rule = format!("contains {count} {name}; the foe runtime permits at most {limit}");
        return Err(ConfigError::Invalid { key: prefix.into(), rule });
    }
    let (inputs, preds) = (wf.inputs(), wf.predecessors());
    let cyclic: BTreeSet<&String> = wf.nodes.keys().filter(|name| ancestors(&preds, name).contains(*name)).collect();
    for (name, node) in &wf.nodes {
        let key = |field: &str| format!("{prefix}.nodes.{name}.{field}");
        valid(name != TASK_SOURCE, key(""), "has a name other than `task`, which names the built-in source".into())?;
        let kinds = [node.tool.is_some(), node.model.is_some(), node.workflow.is_some()];
        let one_kind = kinds.iter().filter(|set| **set).count() == 1 && (node.args.is_none() || node.tool.is_some());
        valid(one_kind, key(""), "has exactly one of tool, model, and workflow, and args only with tool".into())?;
        let names = |field: &str, target: &String| {
            valid(wf.nodes.contains_key(target), key(field), format!("names a node; `{target}` is absent"))
        };
        let tool_in = |field: &str, tool: &String| {
            valid(tools.contains(tool), key(field), format!("names a tool in tools; `{tool}` is absent"))
        };
        node.follows.iter().filter(|t| *t != TASK_SOURCE).try_for_each(|t| names("follows", t))?;
        node.followed_by.iter().try_for_each(|t| names("followed_by", t))?;
        for (label, list) in &node.branches {
            list.iter().try_for_each(|t| names(&format!("branches.{label}"), t))?;
        }
        node.recovery.iter().flat_map(|r| &r.follows).try_for_each(|t| names("recovery.follows", t))?;
        node.verify.iter().try_for_each(|v| tool_in("verify", v))?;
        node.tool.iter().try_for_each(|t| tool_in("tool", t))?;
        let bound =
            node.args.iter().flat_map(|args| args.values()).flat_map(bindings).find(|b| !inputs[name].contains(b));
        if let Some(bound) = bound {
            return valid(false, key("args"), format!("binds `{bound}`, which is not an input of this node"));
        }
        node.model.iter().try_for_each(|p| program(&key("model"), p))?;
        node.workflow.iter().try_for_each(|inner| check(&key("workflow"), inner, tools, program))?;
        valid(node.max_fires != Some(0), key("max_fires"), "is greater than 0".into())?;
        valid(!cyclic.contains(name) || node.max_fires.is_some(), key("max_fires"), "is declared on cycles".into())?;
    }
    Ok(())
}

#[cfg(test)]
#[path = "workflow_test.rs"]
mod tests;
