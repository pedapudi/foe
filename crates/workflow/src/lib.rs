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
