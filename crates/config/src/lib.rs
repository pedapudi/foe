//! What a program is: the configuration document, its resolution into a
//! program, the tool specifications a program declares, and the identity
//! that hashes all of them.
//!
//! This crate answers what was to run. It parses and validates the document
//! docs/config.md specifies, resolves it into the [`config::Program`] that
//! `episode/start.program` records, resolves each name in `tools` to the
//! specification the model will see, and computes the identity of the
//! result. Nothing here runs: no process starts, no grant is exercised, no
//! log is written. `foe-core` is the machine that runs what this crate
//! describes.
//!
//! This file holds the vocabulary: the document's types, what a tool
//! declares, and the construction error every rule reports through.

#![forbid(unsafe_code)]

use foe_log::ToolSchema;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

pub mod config;
pub mod harness_text;
pub mod identity;
pub mod inspect;
pub mod schema;
#[cfg(test)]
#[path = "fixtures_test.rs"]
mod test_util;
pub mod tools;
pub mod workflow;

/// The JSON Schema of the configuration document, maintained by hand to
/// mirror docs/config.md. It describes what this crate parses, so it ships
/// with the types it describes; `foe schema` prints it.
pub const SCHEMA: &str = include_str!("schema.json");

// ---- tools --------------------------------------------------------------------

/// A tool's declared interaction with the world. The registry refuses a tool
/// whose effect the grants do not cover. At dispatch, the effect decides
/// which capability handles the tool receives and whether it may run
/// concurrently with its siblings.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Effect {
    /// Touches nothing outside its arguments. May run concurrently.
    Pure,
    /// Reads within the read roots. May run concurrently.
    Reads,
    /// Writes within the write roots. Runs alone, in issue order.
    Writes,
    /// Starts a process. Runs alone, in issue order.
    Execs,
    /// Starts a child episode. Runs alone, in issue order.
    Spawns,
}

impl Effect {
    pub fn concurrent(self) -> bool {
        matches!(self, Effect::Pure | Effect::Reads)
    }
}

/// What identity hashes and what the model sees. See docs/design.md "Tools".
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolSpec {
    pub name: String,
    pub description: String,
    /// Appended to the system prompt after the instructions, in `tools` order.
    pub instruction: Option<String>,
    /// JSON Schema for the arguments, in the subset `crate::schema` implements.
    pub params: serde_json::Value,
    pub effect: Effect,
}

impl ToolSpec {
    pub fn schema(&self) -> ToolSchema {
        ToolSchema { name: self.name.clone(), description: self.description.clone(), parameters: self.params.clone() }
    }
}

// ---- configuration -----------------------------------------------------------

/// The configuration document. Field for field, docs/config.md.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub version: u32,
    pub name: String,
    pub instructions: BTreeMap<String, String>,
    pub tools: Vec<String>,
    #[serde(default)]
    pub tool_defs: BTreeMap<String, ToolDef>,
    /// Tools the host implements over the protocol. The specification lives
    /// here so that identity is computable from the document alone.
    #[serde(default)]
    pub host_tools: BTreeMap<String, HostToolDef>,
    pub grants: Grants,
    pub budget: Budget,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub done_when: Option<DoneWhen>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub context: Option<ContextConfig>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<ModelConfig>,
    #[serde(default)]
    pub sandbox: SandboxConfig,
    #[serde(default)]
    pub programs: BTreeMap<String, ChildProgram>,
    /// A declared graph that replaces the free loop. See docs/workflow.md.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub workflow: Option<workflow::WorkflowConfig>,
    pub task: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ToolDef {
    pub exec: PathBuf,
    pub description: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub instruction: Option<String>,
    #[serde(default)]
    pub network: bool,
    #[serde(default = "u64_default::<120>")]
    pub timeout_seconds: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cwd: Option<PathBuf>,
}

/// A constant default for serde's `default = "..."`, which names a function.
fn u32_default<const N: u32>() -> u32 {
    N
}

fn u64_default<const N: u64>() -> u64 {
    N
}

/// A host-implemented tool as declared in the document. See docs/config.md
/// `host_tools`. The host supplies only the implementation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HostToolDef {
    pub description: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub instruction: Option<String>,
    pub params: serde_json::Value,
    pub effect: Effect,
}

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Grants {
    pub read: Vec<PathBuf>,
    #[serde(default)]
    pub write: Vec<PathBuf>,
    #[serde(default)]
    pub spawn: Vec<String>,
}

/// True when `path` equals one of `roots` or lies below it, compared by
/// components so that `/src-other` is outside `/src`. A child's grants are
/// checked against its parent's this way, before any directory is opened.
pub fn contains(roots: &[PathBuf], path: &Path) -> bool {
    roots.iter().any(|root| path.starts_with(root))
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Budget {
    pub model_calls: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub input_tokens: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_tokens: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seconds: Option<u64>,
    #[serde(default = "u32_default::<1>")]
    pub max_depth: u32,
    #[serde(default = "u32_default::<8>")]
    pub max_episodes: u32,
    #[serde(default = "u32_default::<4>")]
    pub max_concurrent: u32,
    #[serde(default = "u32_default::<3>")]
    pub loop_threshold: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DoneWhen {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub verify: Option<String>,
    #[serde(default = "u32_default::<2>")]
    pub retries: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub returns: Option<serde_json::Value>,
}

/// The `context` block: whether and when the conversation is compacted.
/// See docs/compaction.md. `window_tokens` may be omitted for a model the
/// provider table knows; the binary resolves it before the episode starts.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContextConfig {
    #[serde(default)]
    pub compact: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub window_tokens: Option<u64>,
    #[serde(default = "u64_default::<16384>")]
    pub reserve_tokens: u64,
    #[serde(default = "u64_default::<20000>")]
    pub keep_recent_tokens: u64,
    #[serde(default = "u64_default::<2048>")]
    pub margin_tokens: u64,
}

/// The `model` block. The provider name is opaque to this crate: which
/// names a build knows is decided where the transport is composed, and
/// `foe plan` reports the resolution. Every key other than the three named
/// fields is a provider-specific option, flat and string-valued, such as
/// `api_key_file`, `base_url`, `project`, or `exec`. The block does not
/// participate in identity.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelConfig {
    pub provider: String,
    pub model: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_output_tokens: Option<u32>,
    #[serde(flatten)]
    pub options: BTreeMap<String, String>,
}

impl ModelConfig {
    pub fn new(provider: impl Into<String>, model: impl Into<String>) -> Self {
        ModelConfig {
            provider: provider.into(),
            model: model.into(),
            max_output_tokens: None,
            options: BTreeMap::new(),
        }
    }

    /// One provider-specific option, when present.
    pub fn option(&self, key: &str) -> Option<&str> {
        self.options.get(key).map(String::as_str)
    }
}

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SandboxConfig {
    #[serde(default)]
    pub mode: foe_log::SandboxMode,
}

/// A child program: a configuration without `version`, `task`, or `sandbox`.
/// An omitted `model` inherits the nearest ancestor's model.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChildProgram {
    pub name: String,
    pub instructions: BTreeMap<String, String>,
    pub tools: Vec<String>,
    #[serde(default)]
    pub tool_defs: BTreeMap<String, ToolDef>,
    #[serde(default)]
    pub host_tools: BTreeMap<String, HostToolDef>,
    pub grants: Grants,
    pub budget: Budget,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub done_when: Option<DoneWhen>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub context: Option<ContextConfig>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<ModelConfig>,
    #[serde(default)]
    pub programs: BTreeMap<String, ChildProgram>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub workflow: Option<workflow::WorkflowConfig>,
}

// ---- errors --------------------------------------------------------------------

/// Every construction error names the key and the rule. Construction fails
/// before any process starts and before any log is written.
#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("{key}: {rule}")]
    Invalid { key: String, rule: String },
    #[error("{0}")]
    Parse(#[from] serde_json::Error),
    #[error("{0}")]
    Io(#[from] std::io::Error),
}

#[cfg(test)]
#[path = "lib_test.rs"]
mod tests;
