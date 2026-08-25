//! The ancestry verification `foe plan --states DIR --evidence DIR`
//! performs on a resolved program. docs/lineage-identity.md "Verifying an
//! ancestry claim" specifies the checker, which lives in `foe_lineage`;
//! this file supplies the two directory-backed resolvers and the page a
//! person reads.

use foe_config::ProgramLineage;
use foe_lineage::{check_ancestry, AncestryReport, StateDocument};
use std::path::{Path, PathBuf};

/// Checks the program's ancestry claim against its identity document:
/// resolves states from `<states>/<hex>.json` and bundles from
/// `<evidence>/<hex>`, each named by its digest without the `sha256:`
/// prefix, and returns the chain with every check the retained evidence
/// leaves open.
pub fn verify(
    identity_document: &serde_json::Value,
    claim: Option<ProgramLineage>,
    states: &Path,
    evidence: &Path,
) -> Result<AncestryReport, String> {
    let document = StateDocument { identity_document: identity_document.clone(), program_lineage: claim };
    let hex_of = |digest: &str| digest.strip_prefix("sha256:").unwrap_or(digest).to_string();
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

/// The report as a page: the program identities of the verified chain,
/// this program first and each row the previous row's parent, then every
/// check the retained evidence leaves open.
pub fn rendered(report: &AncestryReport) -> String {
    let name = |i: usize| if i == 0 { "program" } else { "parent" };
    let chain: String =
        report.chain.iter().enumerate().map(|(i, e)| format!("  {:<8} {}\n", name(i), e.program_identity)).collect();
    let open: String = report.unverifiable.iter().map(|note| format!("  {note}\n")).collect();
    let open = if open.is_empty() { open } else { format!("open checks\n{open}") };
    format!("ancestry, this program first\n{chain}root reached after {} states\n{open}", report.chain.len())
}

/// The report as the `lineage` member of `foe plan --json`: the same chain
/// of program identities, and the open checks.
pub fn value(report: &AncestryReport) -> serde_json::Value {
    let chain: Vec<&str> = report.chain.iter().map(|e| e.program_identity.as_str()).collect();
    serde_json::json!({ "chain": chain, "unverifiable": report.unverifiable })
}

#[cfg(test)]
#[path = "lineage_test.rs"]
mod tests;
