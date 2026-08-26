//! Canonical serialization and SHA-256 of a program.
//!
//! Implements docs/design.md (Programs and identity). The identity
//! document lists everything that shapes what the model sees and nothing
//! else: resolved paths, the `model` block, `sandbox`, and the task are
//! absent. Computing it reads the executables named in `tool_defs` to hash
//! their content, executes nothing, and opens no socket. The runtime the
//! document names is supplied by the caller, because reading back the
//! running binary is the runtime's business rather than the
//! configuration's; `foe_core::identity::runtime_info` does it.

use crate::document::{resolve_node_program, ResolvedProgram};
use crate::workflow::WorkflowConfig;
use crate::{harness_text, tools, ProgramError, ToolSpec};
use foe_log::RuntimeInfo;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// A computed identity with the document it hashes, for `foe plan`.
#[derive(Debug, Clone, PartialEq)]
pub struct Identity {
    /// `sha256:<hex>`
    pub hash: String,
    pub document: Value,
}

/// Compact JSON with object keys in sorted order. `serde_json` sorts keys
/// because the `preserve_order` feature is off; this function is the one
/// place that relies on it.
pub fn canonical(value: &Value) -> String {
    serde_json::to_string(value).expect("a JSON value serializes")
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

/// Computes the identity of `program` and, recursively, of every child
/// program. `extra_builtins` are the specifications of built-in tools
/// implemented outside this crate, so the same list the registry receives.
pub fn compute(
    program: &ResolvedProgram,
    extra_builtins: &[ToolSpec],
    runtime: &RuntimeInfo,
) -> Result<Identity, ProgramError> {
    let mut tools = Vec::new();
    for spec in tools::resolve_specs(program, extra_builtins)? {
        let mut entry = serde_json::to_value(&spec)?;
        if let Some(def) = program.tool_defs.get(&spec.name) {
            let bytes = std::fs::read(&def.exec).map_err(|e| ProgramError::Invalid {
                key: format!("tool_defs.{}.exec", spec.name),
                rule: format!("is readable for hashing: {}: {e}", def.exec.display()),
            })?;
            entry["exec_sha256"] = Value::String(sha256_hex(&bytes));
        }
        tools.push(entry);
    }
    let mut programs = serde_json::Map::new();
    for (name, child) in &program.programs {
        programs.insert(name.clone(), Value::String(compute(child, extra_builtins, runtime)?.hash));
    }
    let texts = texts(harness_text::all());
    let workflow =
        program.workflow.as_ref().map(|wf| workflow_document("workflow", wf, program, extra_builtins, runtime));
    let mut grants = json!({
        "read": program.grants.read.len(), "write": program.grants.write.len(),
        "execute": program.grants.execute.len(), "spawn": program.grants.spawn.len(), "bind": program.grants.bind.len(),
    });
    if program.grants.task_session {
        grants["task_session"] = Value::Bool(true);
    }
    let document = json!({
        "name": program.name,
        "instructions": program.instructions,
        "tools": tools,
        "grants": grants,
        "budget": program.budget,
        "done_when": program.done_when,
        "context": program.context,
        "compaction": {
            "policy_version": harness_text::COMPACTION_POLICY_VERSION,
            "state": foe_log::ContinuationState::default(),
            "labels": foe_log::fold::STATE_LABELS,
        },
        "programs": programs,
        "workflow": workflow.transpose()?,
        "harness_text": { "version": harness_text::VERSION, "texts": texts },
        "runtime": runtime,
    });
    let hash = format!("sha256:{}", sha256_hex(canonical(&document).as_bytes()));
    Ok(Identity { hash, document })
}

/// The workflow part of the identity document: everything docs/workflow.md
/// "Identity" lists, with each model node's program reduced to its hash.
fn workflow_document(
    prefix: &str,
    wf: &WorkflowConfig,
    parent: &ResolvedProgram,
    extra_builtins: &[ToolSpec],
    runtime: &RuntimeInfo,
) -> Result<Value, ProgramError> {
    let inputs = wf.inputs();
    let mut nodes = serde_json::Map::new();
    for (name, node) in &wf.nodes {
        let key = format!("{prefix}.nodes.{name}");
        let mut entry = serde_json::to_value(node)?;
        let fields = entry.as_object_mut().expect("a node serializes to an object");
        fields.insert("follows".into(), json!(inputs[name]));
        fields.insert("max_fires".into(), json!(node.max_fires.unwrap_or(1)));
        if let Some(child) = &node.model {
            let program = resolve_node_program(&format!("{key}.model"), parent, child)?;
            fields.insert("model".into(), json!(compute(&program, extra_builtins, runtime)?.hash));
        }
        if let Some(inner) = &node.workflow {
            let inner = workflow_document(&format!("{key}.workflow"), inner, parent, extra_builtins, runtime)?;
            fields.insert("workflow".into(), inner);
        }
        nodes.insert(name.clone(), entry);
    }
    let texts = texts(harness_text::workflow_texts());
    Ok(json!({ "nodes": nodes, "max_interventions": wf.recovery.max_interventions, "texts": texts }))
}

fn texts(list: Vec<(&str, &str)>) -> serde_json::Map<String, Value> {
    list.into_iter().map(|(k, v)| (k.to_string(), Value::String(v.to_string()))).collect()
}

#[cfg(test)]
#[path = "identity_test.rs"]
mod tests;
