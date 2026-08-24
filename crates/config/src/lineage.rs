//! Program lineage: the ancestry claim a root configuration carries and the
//! identity derived from it. Implements docs/lineage-identity.md.
//!
//! A resolved program's identity names its content. The lineage identity
//! names that content together with one ancestry claim: which parent state
//! this one descends from, and which content-addressed evidence bundle
//! records the transition. Everything here is a pure function over values
//! and files already on disk: nothing runs, no grant is exercised, no log
//! is written.

use crate::identity::{canonical, sha256_hex};
use crate::ConfigError;
use serde::{Deserialize, Serialize};
use serde_json::json;

/// The immediate predecessor named by an ancestry claim.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LineageParent {
    /// The parent state's program identity.
    pub program_identity: String,
    /// The parent state's own ancestry claim, selecting one among the
    /// claims that can accompany a single program identity.
    pub lineage_identity: String,
}

/// The `program_lineage` object of a root configuration. The identity
/// computation omits it; the resolved program records it, so the claim
/// reaches `episode/start.program`. See docs/config.md `program_lineage`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProgramLineage {
    pub parent: LineageParent,
    /// Content address of the proposal episode's evidence bundle: the
    /// SHA-256 digest of the bundle's canonical manifest.
    pub evidence: String,
    /// Path of the episode log holding the authoritative verifier result,
    /// relative to the bundle root, in manifest path form.
    pub verification_log: String,
    /// `seq` of that `verification/result` event inside `verification_log`.
    pub verification_seq: u64,
}

fn invalid(key: &str, rule: impl Into<String>) -> ConfigError {
    ConfigError::Invalid { key: key.into(), rule: rule.into() }
}

/// `Ok` when `text` is `sha256:` followed by 64 lowercase hex digits.
pub fn require_digest(key: &str, text: &str) -> Result<(), ConfigError> {
    let hex = text.strip_prefix("sha256:").unwrap_or("");
    match hex.len() == 64 && hex.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')) {
        true => Ok(()),
        false => Err(invalid(key, "is `sha256:` followed by 64 lowercase hex digits")),
    }
}

/// `Ok` when `path` is in manifest path form: relative, forward slashes,
/// and no empty, `.`, or `..` component. See docs/lineage-identity.md
/// "Evidence bundle".
pub fn require_manifest_path(key: &str, path: &str) -> Result<(), ConfigError> {
    match !path.contains('\\') && path.split('/').all(|c| !matches!(c, "" | "." | "..")) {
        true => Ok(()),
        false => Err(invalid(key, "is a relative path with no empty, `.`, or `..` component")),
    }
}

/// Checks the shape rules of the `program_lineage` object.
pub fn validate(lineage: &ProgramLineage) -> Result<(), ConfigError> {
    require_digest("program_lineage.parent.program_identity", &lineage.parent.program_identity)?;
    require_digest("program_lineage.parent.lineage_identity", &lineage.parent.lineage_identity)?;
    require_digest("program_lineage.evidence", &lineage.evidence)?;
    require_manifest_path("program_lineage.verification_log", &lineage.verification_log)
}

/// The lineage identity of a state: a SHA-256 digest over the canonical
/// object docs/lineage-identity.md "Configuration representation"
/// specifies. The claim of a root state is `null`. The identity is derived
/// and appears nowhere inside the object it hashes.
pub fn lineage_identity(program_identity: &str, lineage: Option<&ProgramLineage>) -> String {
    let document = json!({
        "schema_version": 1,
        "program_identity": program_identity,
        "program_lineage": lineage,
    });
    format!("sha256:{}", sha256_hex(canonical(&document).as_bytes()))
}

#[cfg(test)]
#[path = "lineage_test.rs"]
mod tests;
