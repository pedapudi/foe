//! Canonical serialization and SHA-256 of a program.
//!
//! Implements docs/design.md (Programs and identity). The identity
//! document lists everything that shapes what the model sees and nothing
//! else: resolved paths, the `model` block, `sandbox`, and the task are
//! absent. Construction reads the executables named in `tool_defs`. Identity
//! uses the retained digests, executes nothing, and opens no socket. The
//! caller supplies the runtime named by the document. Reading the running
//! binary belongs to `foe_core::identity::runtime_info`.

use crate::document::ResolvedProgram;
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
        if program.tool_defs.contains_key(&spec.name) {
            let digest = program.executable_sha256.get(&spec.name).expect("a resolved executable has a digest");
            entry["exec_sha256"] = Value::String(digest.clone());
        }
        tools.push(entry);
    }
    let mut programs = serde_json::Map::new();
    for (name, child) in &program.programs {
        programs.insert(name.clone(), Value::String(compute(child, extra_builtins, runtime)?.hash));
    }
    let texts = texts(harness_text::all());
    let workflow =
        program.workflow.as_ref().map(|wf| workflow_document("workflow", "", wf, program, extra_builtins, runtime));
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
    path: &str,
    wf: &WorkflowConfig,
    parent: &ResolvedProgram,
    extra_builtins: &[ToolSpec],
    runtime: &RuntimeInfo,
) -> Result<Value, ProgramError> {
    let inputs = wf.inputs();
    let mut nodes = serde_json::Map::new();
    for (name, node) in &wf.nodes {
        let key = format!("{prefix}.nodes.{name}");
        let child_path = format!("{path}{name}");
        let mut entry = serde_json::to_value(node)?;
        let fields = entry.as_object_mut().expect("a node serializes to an object");
        fields.insert("follows".into(), json!(inputs[name]));
        fields.insert("max_fires".into(), json!(node.max_fires.unwrap_or(1)));
        if node.model.is_some() {
            let program = parent.workflow_programs.get(&child_path).expect("a model node is resolved");
            fields.insert("model".into(), json!(compute(program, extra_builtins, runtime)?.hash));
        }
        if let Some(inner) = &node.workflow {
            let inner = workflow_document(
                &format!("{key}.workflow"),
                &format!("{child_path}/"),
                inner,
                parent,
                extra_builtins,
                runtime,
            )?;
            fields.insert("workflow".into(), inner);
        }
        nodes.insert(name.clone(), entry);
    }
    let texts = texts(harness_text::workflow_texts());
    Ok(json!({ "nodes": nodes, "max_interventions": wf.recovery.max_interventions, "texts": texts }))
}

/// Confirms that configured executable files still have the content read
/// during construction. A mismatch prevents a child launch until execution
/// can use committed file descriptors directly.
pub fn verify_executables(program: &ResolvedProgram) -> Result<(), ProgramError> {
    for (name, expected) in &program.executable_sha256 {
        let def = &program.tool_defs[name];
        let bytes = std::fs::read(&def.exec).map_err(|e| ProgramError::Invalid {
            key: format!("tool_defs.{name}.exec"),
            rule: format!("still names the executable read during construction: {e}"),
        })?;
        if sha256_hex(&bytes) != *expected {
            return Err(ProgramError::Invalid {
                key: format!("tool_defs.{name}.exec"),
                rule: "still has the content read during construction".into(),
            });
        }
    }
    program.spawned_programs().try_for_each(verify_executables)
}

fn texts(list: Vec<(&str, &str)>) -> serde_json::Map<String, Value> {
    list.into_iter().map(|(k, v)| (k.to_string(), Value::String(v.to_string()))).collect()
}

#[cfg(test)]
#[path = "identity_test.rs"]
mod tests;
