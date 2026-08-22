//! The workflow executor. A configuration with a `workflow` key runs a
//! declared graph of nodes in place of the free agent loop; this crate
//! fires the graph by dataflow, enforces its choice points, and routes
//! every failure through recovery. docs/workflow.md specifies it; the
//! configuration types live in `foe_core::workflow`.

#![forbid(unsafe_code)]

pub mod bind;
mod graph;
mod run;

pub use run::{render, run, WorkflowParams};

use foe_core::workflow::{Node, WorkflowConfig};
use foe_core::{ChildProgram, Config, DoneWhen};
use serde_json::{json, Value};

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
pub fn node_program(node: &Node) -> ChildProgram {
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

/// The configuration the episode's spawner is built with: every model
/// node's program added to `programs` under the node's path and to
/// `grants.spawn`, so that firing a model node is an ordinary spawn.
pub fn spawner_config(config: &Config) -> Config {
    let mut out = config.clone();
    for (path, node) in config.workflow.iter().flat_map(|wf| model_nodes(wf, "")) {
        out.programs.insert(path.clone(), node_program(node));
        if !out.grants.spawn.contains(&path) {
            out.grants.spawn.push(path);
        }
    }
    out
}

#[cfg(test)]
#[path = "lib_test.rs"]
mod tests;
