//! The workflow executor. A configuration with a `workflow` key runs a
//! declared graph of nodes in place of the free agent loop; this crate
//! fires the graph by dataflow, enforces its choice points, and routes
//! every failure through recovery. docs/workflow.md specifies it; the
//! configuration types, and the analysis of a configured graph that
//! `foe plan` reports, live in `foe_program::workflow` and
//! `foe_program::inspect`.

#![forbid(unsafe_code)]

pub mod bind;
mod graph;
mod run;

pub use run::{render, run, WorkflowParams};

use foe_program::workflow::{model_nodes, node_program};
use foe_program::ProgramDocument;

/// The configuration the episode's spawner is built with: every model
/// node's program added to `programs` under the node's path and to
/// `grants.spawn`, so that firing a model node is an ordinary spawn.
pub fn spawner_document(config: &ProgramDocument) -> ProgramDocument {
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
