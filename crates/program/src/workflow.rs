//! Workflow configuration: the graph of nodes, the edge set derived from
//! it, and the construction rules of docs/workflow.md "The graph". The
//! executor lives in the `foe-workflow` crate; this module holds what the
//! document, validation, and identity need.

use crate::{ChildProgramDocument, DoneWhen, ProgramError};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};

/// The reserved name of the built-in source any node may follow: the
/// invocation task, available from the start and produced exactly once.
pub const TASK_SOURCE: &str = "task";

/// The most firings a declared graph may describe. This is a runtime
/// constant rather than a configuration key, because a graph beyond it is
/// a declaration mistake rather than a choice: an episode that runs
/// unattended needs a bound on its work that a reader can check before it
/// starts, and `max_fires` multiplies through nested workflows. The bound
/// covers the node count and the nesting depth as well, since every node
/// contributes at least one firing.
pub const MAX_POSSIBLE_FIRINGS: u64 = 4096;

/// The most data-flow, control-flow, and recovery edge references that a
/// declared workflow tree may contain.
pub const MAX_EDGE_REFERENCES: u64 = 4096;

fn count_len(len: usize) -> u64 {
    u64::try_from(len).unwrap_or(u64::MAX)
}

/// A declared graph. Construction resolves every model node into the
/// enclosing program's immutable child-program tree.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowConfig {
    pub nodes: BTreeMap<String, Node>,
    #[serde(default)]
    pub recovery: RecoveryConfig,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RecoveryConfig {
    #[serde(default = "default_enabled")]
    pub enabled: bool,
    #[serde(default = "default_max_interventions")]
    pub max_interventions: u32,
}

impl Default for RecoveryConfig {
    fn default() -> Self {
        Self { enabled: true, max_interventions: default_max_interventions() }
    }
}

fn default_enabled() -> bool {
    true
}
fn default_max_interventions() -> u32 {
    3
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
    pub model: Option<ChildProgramDocument>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub workflow: Option<WorkflowConfig>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub follows: Vec<String>,
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
    /// Whether this graph or a graph nested inside it holds a model node.
    /// Each such node runs as a child episode when it fires, so a program
    /// carrying one starts descendants even with no `grants.spawn` entry.
    pub fn contains_model_node(&self) -> bool {
        self.nodes
            .values()
            .any(|node| node.model.is_some() || node.workflow.as_ref().is_some_and(Self::contains_model_node))
    }

    /// The most node firings this graph can perform in one episode. A node
    /// contributes its effective `max_fires`. A nested workflow node
    /// contributes that count multiplied by one plus the firings of the
    /// graph it holds, because each firing of the node runs that graph once
    /// from the start. The count saturates rather than overflowing.
    pub fn possible_firings(&self) -> u64 {
        self.nodes.values().fold(0, |total, node| {
            let fires = u64::from(node.max_fires.unwrap_or(1));
            let inner = node.workflow.as_ref().map_or(0, Self::possible_firings);
            total.saturating_add(fires.saturating_mul(inner.saturating_add(1)))
        })
    }

    /// The node-name entries that declare data flow, control flow, or
    /// recovery context, summed across this graph and nested workflows.
    pub fn edge_references(&self) -> u64 {
        let mut total = 0u64;
        let mut pending = vec![self];
        while let Some(workflow) = pending.pop() {
            for node in workflow.nodes.values() {
                total = total.saturating_add(count_len(node.follows.len()));
                total =
                    node.branches.values().fold(total, |count, targets| count.saturating_add(count_len(targets.len())));
                total = node
                    .recovery
                    .iter()
                    .fold(total, |count, recovery| count.saturating_add(count_len(recovery.follows.len())));
                pending.extend(node.workflow.iter());
            }
        }
        total
    }

    /// The data inputs of every node, with the `task` source first.
    pub fn inputs(&self) -> BTreeMap<String, Vec<String>> {
        let mut inputs: BTreeMap<String, Vec<String>> =
            self.nodes.iter().map(|(name, node)| (name.clone(), node.follows.clone())).collect();
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

    /// Every node from which a path of edges leads back to itself.
    pub fn on_cycles(&self) -> BTreeSet<String> {
        let preds = self.predecessors();
        self.nodes.keys().filter(|name| ancestors(&preds, name).contains(*name)).cloned().collect()
    }
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
    match value.as_object() {
        Some(o) => match o.get("$node").and_then(Value::as_str) {
            Some(name) => vec![name.to_string()],
            None => o.values().flat_map(bindings).collect(),
        },
        None => Vec::new(),
    }
}

/// The rules of docs/workflow.md "The graph" that the document alone can
/// check, at every depth. `tools` is the ceiling the tool and verify names
/// resolve in. `program` is applied to every model node's program with its
/// dotted key; the caller validates or resolves it there.
pub fn check(
    prefix: &str,
    wf: &WorkflowConfig,
    tools: &[String],
    program: &mut dyn FnMut(&str, &ChildProgramDocument) -> Result<(), ProgramError>,
) -> Result<(), ProgramError> {
    let invalid = |key: String, rule: String| ProgramError::Invalid { key, rule };
    let edges = wf.edge_references();
    if edges > MAX_EDGE_REFERENCES {
        let rule =
            format!("describes {edges} workflow edge references; the runtime permits at most {MAX_EDGE_REFERENCES}");
        return Err(invalid(prefix.into(), rule));
    }
    let firings = wf.possible_firings();
    if firings > MAX_POSSIBLE_FIRINGS {
        let rule = format!("describes {firings} possible firings; the runtime permits at most {MAX_POSSIBLE_FIRINGS}");
        return Err(invalid(prefix.into(), rule));
    }
    check_graph(prefix, wf, tools, program)
}

/// Checks a workflow tree after its aggregate construction bounds hold.
fn check_graph(
    prefix: &str,
    wf: &WorkflowConfig,
    tools: &[String],
    program: &mut dyn FnMut(&str, &ChildProgramDocument) -> Result<(), ProgramError>,
) -> Result<(), ProgramError> {
    let invalid = |key: String, rule: String| ProgramError::Invalid { key, rule };
    let (inputs, cyclic) = (wf.inputs(), wf.on_cycles());
    for (name, node) in &wf.nodes {
        let key = |field: &str| format!("{prefix}.nodes.{name}.{field}");
        if name == TASK_SOURCE {
            return Err(invalid(key(""), "has a name other than `task`, which names the built-in source".into()));
        }
        let kinds = [node.tool.is_some(), node.model.is_some(), node.workflow.is_some()];
        if kinds.iter().filter(|set| **set).count() != 1 || (node.args.is_some() && node.tool.is_none()) {
            let rule = "has exactly one of tool, model, and workflow, and args only with tool";
            return Err(invalid(key(""), rule.into()));
        }
        let names = |field: &str, target: &String| match wf.nodes.contains_key(target) {
            true => Ok(()),
            false => Err(invalid(key(field), format!("names a node; `{target}` is absent"))),
        };
        let tool_in = |field: &str, tool: &String| match tools.contains(tool) {
            true => Ok(()),
            false => Err(invalid(key(field), format!("names a tool in tools; `{tool}` is absent"))),
        };
        node.follows.iter().filter(|t| *t != TASK_SOURCE).try_for_each(|t| names("follows", t))?;
        for (label, list) in &node.branches {
            list.iter().try_for_each(|t| names(&format!("branches.{label}"), t))?;
        }
        node.recovery.iter().flat_map(|r| &r.follows).try_for_each(|t| names("recovery.follows", t))?;
        node.verify.iter().try_for_each(|v| tool_in("verify", v))?;
        node.tool.iter().try_for_each(|t| tool_in("tool", t))?;
        let args = node.args.clone().map_or(Value::Null, Value::Object);
        if let Some(bound) = bindings(&args).iter().find(|b| !inputs[name].contains(b)) {
            return Err(invalid(key("args"), format!("binds `{bound}`, which is not an input of this node")));
        }
        node.model.iter().try_for_each(|p| program(&key("model"), p))?;
        node.workflow.iter().try_for_each(|inner| check_graph(&key("workflow"), inner, tools, program))?;
        if node.max_fires == Some(0) {
            return Err(invalid(key("max_fires"), "is greater than 0".into()));
        }
        if cyclic.contains(name) && node.max_fires.is_none() {
            return Err(invalid(key("max_fires"), "is declared, because this node lies on a cycle".into()));
        }
    }
    Ok(())
}

/// Every model node at any depth with its path: the node name at the top,
/// `outer/inner` inside a nested workflow node.
pub fn model_nodes<'a>(wf: &'a WorkflowConfig, prefix: &str) -> Vec<(String, &'a Node)> {
    let mut found = Vec::new();
    for (name, node) in &wf.nodes {
        if node.model.is_some() {
            found.push((format!("{prefix}{name}"), node));
        }
        if let Some(inner) = &node.workflow {
            found.extend(model_nodes(inner, &format!("{prefix}{name}/")));
        }
    }
    found
}

/// The program a model node's child episode runs: the node's program with
/// `branch` added to its `done_when.returns` as a required enum over the
/// labels when the node declares `branches`. See docs/workflow.md "Choice
/// points".
pub fn node_program(node: &Node) -> ChildProgramDocument {
    let mut program = node.model.clone().expect("a model node");
    if node.branches.is_empty() {
        return program;
    }
    let done = program.done_when.get_or_insert(DoneWhen { verify: None, retries: 2, returns: None });
    let returns = done.returns.get_or_insert_with(|| json!({ "type": "object", "properties": {} }));
    returns["properties"]["branch"] = json!({ "type": "string", "enum": node.branches.keys().collect::<Vec<_>>() });
    let mut required = returns["required"].as_array().cloned().unwrap_or_default();
    if !required.contains(&json!("branch")) {
        required.push(json!("branch"));
    }
    returns["required"] = Value::Array(required);
    program
}

#[cfg(test)]
#[path = "workflow_test.rs"]
mod tests;
