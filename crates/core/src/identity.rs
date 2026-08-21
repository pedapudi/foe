//! Canonical serialization and SHA-256 of a program.
//!
//! Implements docs/design.md (Programs and identity). The identity
//! document lists everything that shapes what the model sees and nothing
//! else: resolved paths, the `model` block, `sandbox`, and the task are
//! absent. Computing it reads the executables named in `tool_defs` to hash
//! their content, executes nothing, and opens no socket.

use crate::config::Program;
use crate::{harness_text, registry, ConfigError, ToolSpec};
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

/// The running binary's version and content hash. `build` is `unknown`
/// when the binary cannot be read back, for example off Linux.
pub fn runtime_info() -> RuntimeInfo {
    let build = std::fs::read("/proc/self/exe")
        .map(|bytes| format!("sha256:{}", sha256_hex(&bytes)))
        .unwrap_or_else(|_| "unknown".into());
    RuntimeInfo { version: env!("CARGO_PKG_VERSION").into(), build }
}

/// Computes the identity of `program` and, recursively, of every child
/// program. `extra_builtins` are the specifications of built-in tools
/// implemented outside this crate, so the same list the registry receives.
pub fn compute(program: &Program, extra_builtins: &[ToolSpec], runtime: &RuntimeInfo) -> Result<Identity, ConfigError> {
    let mut tools = Vec::new();
    for spec in registry::resolve_specs(program, extra_builtins)? {
        let mut entry = serde_json::to_value(&spec)?;
        if let Some(def) = program.tool_defs.get(&spec.name) {
            let bytes = std::fs::read(&def.exec).map_err(|e| ConfigError::Invalid {
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
    let texts: serde_json::Map<String, Value> =
        harness_text::all().into_iter().map(|(k, v)| (k.to_string(), Value::String(v.to_string()))).collect();
    let document = json!({
        "name": program.name,
        "instructions": program.instructions,
        "tools": tools,
        "grants": {
            "read": program.grants.read.len(),
            "write": program.grants.write.len(),
            "spawn": program.grants.spawn.len(),
        },
        "budget": program.budget,
        "done_when": program.done_when,
        "programs": programs,
        "harness_text": { "version": harness_text::VERSION, "texts": texts },
        "runtime": runtime,
    });
    let hash = format!("sha256:{}", sha256_hex(canonical(&document).as_bytes()));
    Ok(Identity { hash, document })
}

#[cfg(test)]
#[path = "identity_test.rs"]
mod tests;
