//! The ancestry verification `foe plan --states DIR --evidence DIR`
//! performs on a resolved program. docs/lineage-identity.md "Verifying an
//! ancestry claim" specifies the checker, which lives in `foe_lineage`;
//! this file supplies the two directory-backed resolvers and the page a
//! person reads.

use foe_config::ProgramLineage;
use foe_lineage::{check_ancestry, AncestryReport, StateDocument};
use serde_json::Value;
use std::path::{Path, PathBuf};

/// The identity without its `sha256:` prefix, usable as a file name.
fn hex_of(identity: &str) -> &str {
    identity.strip_prefix("sha256:").unwrap_or(identity)
}

/// Checks the program's ancestry claim against its identity document:
/// resolves states from `<states>/<hex>.json` by lineage identity and
/// bundles from `<evidence>/<hex>` by content address, and returns the
/// chain with every check the retained evidence leaves open.
pub fn verify(
    identity_document: &Value,
    claim: Option<ProgramLineage>,
    states: &Path,
    evidence: &Path,
) -> Result<AncestryReport, String> {
    let document = StateDocument { identity_document: identity_document.clone(), program_lineage: claim };
    let resolve_state = |id: &str| -> Result<StateDocument, String> {
        let path = states.join(format!("{}.json", hex_of(id)));
        let text = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))
    };
    let resolve_evidence = |address: &str| -> Result<PathBuf, String> {
        let dir = evidence.join(hex_of(address));
        match dir.is_dir() {
            true => Ok(dir),
            false => Err(format!("{} is not a directory", dir.display())),
        }
    };
    check_ancestry(&document, &resolve_state, &resolve_evidence).map_err(|e| e.to_string())
}

/// The report as a page: the chain from the program's own state to its
/// root, then every open check.
pub fn rendered(report: &AncestryReport) -> String {
    let chain: String =
        report.chain.iter().map(|e| format!("  {}  program {}\n", e.lineage_identity, e.program_identity)).collect();
    let open: String = report.unverifiable.iter().map(|note| format!("  {note}\n")).collect();
    let open = if open.is_empty() { open } else { format!("open checks\n{open}") };
    format!("chain, state first\n{chain}root reached after {} states\n{open}", report.chain.len())
}

#[cfg(test)]
#[path = "lineage_test.rs"]
mod tests;
