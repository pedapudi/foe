//! Canonical serialization and SHA-256 of a contract.
//!
//! Implements docs/design.md (Execution contracts and fingerprints). The fingerprint
//! document lists everything that shapes what the model sees and nothing
//! else: resolved paths, model selection, `sandbox`, and the task are absent.
//! Construction reads declared configured tools.
//! Fingerprint uses their retained digests, executes nothing, and opens no
//! socket. The caller supplies the runtime named by the document. Reading the
//! running binary belongs to `foe_core::fingerprint::runtime_info`.

use crate::document::ResolvedContract;
use crate::workflow::{WorkflowConfig, MAX_EDGE_REFERENCES};
use crate::{harness_text, tools, ContractError, ToolSpec};
use foe_log::RuntimeInfo;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// A computed fingerprint with the document it hashes, for `foe plan`.
#[derive(Debug, Clone, PartialEq)]
pub struct Fingerprint {
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

/// Computes the fingerprint of `contract` and, recursively, of every child
/// contract. `extra_builtins` are the specifications of built-in tools
/// implemented outside this crate, so the same list the registry receives.
pub fn compute(
    contract: &ResolvedContract,
    extra_builtins: &[ToolSpec],
    runtime: &RuntimeInfo,
) -> Result<Fingerprint, ContractError> {
    let mut tools = Vec::new();
    for spec in tools::resolve_specs(contract, extra_builtins)? {
        let mut entry = serde_json::to_value(&spec)?;
        if contract.tool_defs.contains_key(&spec.name) {
            let captured = contract.captured_executables.get(&spec.name).expect("a resolved executable is captured");
            entry["exec_sha256"] = Value::String(captured.sha256.clone());
            entry["exec_name"] = Value::String(captured.invocation_name.clone());
        }
        tools.push(entry);
    }
    let mut child_contracts = serde_json::Map::new();
    for (name, child) in &contract.child_contracts {
        child_contracts.insert(name.clone(), Value::String(compute(child, extra_builtins, runtime)?.hash));
    }
    let texts = texts(harness_text::all());
    let workflow =
        contract.workflow.as_ref().map(|wf| workflow_document("workflow", "", wf, contract, extra_builtins, runtime));
    let mut grants = json!({
        "read": contract.grants.read.len(), "write": contract.grants.write.len(),
        "execute": contract.grants.execute.len(), "spawn": contract.grants.spawn.len(), "bind": contract.grants.bind.len(),
    });
    if contract.grants.task_session {
        grants["task_session"] = Value::Bool(true);
    }
    let runtime = serde_json::to_value(runtime)?;
    let document = json!({
        "name": contract.name,
        "instructions": contract.instructions,
        "tools": tools,
        "grants": grants,
        "budget": contract.budget,
        "done_when": contract.done_when,
        "context": contract.context,
        "compaction": {
            "policy_version": harness_text::COMPACTION_POLICY_VERSION,
            "state": foe_log::ContinuationState::default(),
            "labels": foe_log::fold::STATE_LABELS,
        },
        "child_contracts": child_contracts,
        "workflow": workflow.transpose()?,
        "harness_text": { "version": harness_text::VERSION, "texts": texts },
        "runtime": runtime,
    });
    let hash = format!("sha256:{}", sha256_hex(canonical(&document).as_bytes()));
    Ok(Fingerprint { hash, document })
}

/// The workflow part of the fingerprint document: everything docs/workflow.md
/// "Fingerprint" lists, with each model node's contract reduced to its hash.
fn workflow_document(
    prefix: &str,
    path: &str,
    wf: &WorkflowConfig,
    parent: &ResolvedContract,
    extra_builtins: &[ToolSpec],
    runtime: &RuntimeInfo,
) -> Result<Value, ContractError> {
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
            let contract = parent.workflow_contracts.get(&child_path).expect("a model node is resolved");
            fields.insert("model".into(), json!(compute(contract, extra_builtins, runtime)?.hash));
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
    Ok(json!({
        "nodes": nodes,
        "max_interventions": wf.recovery.max_interventions,
        "edge_reference_limit": {
            "maximum": MAX_EDGE_REFERENCES,
            "fields": ["follows", "branches", "recovery.follows"],
            "nested": true,
        },
        "texts": texts,
    }))
}

fn texts(list: Vec<(&str, &str)>) -> serde_json::Map<String, Value> {
    list.into_iter().map(|(k, v)| (k.to_string(), Value::String(v.to_string()))).collect()
}

#[cfg(test)]
#[path = "fingerprint_test.rs"]
mod tests;
