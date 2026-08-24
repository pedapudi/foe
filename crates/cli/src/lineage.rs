//! The `foe lineage` form: verify a state document's ancestry claim.
//! docs/lineage-identity.md "Verifying an ancestry claim" specifies the
//! checker; this file supplies the two directory-backed resolvers and the
//! page a person reads.

use foe_lineage::{check_ancestry, AncestryReport, StateDocument};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

/// The identity without its `sha256:` prefix, usable as a file name.
fn hex_of(identity: &str) -> &str {
    identity.strip_prefix("sha256:").unwrap_or(identity)
}

/// Reads the state document, resolves states from `<states>/<hex>.json` by
/// lineage identity and bundles from `<evidence>/<hex>` by content address,
/// checks the claim, and prints the chain with every check the retained
/// evidence leaves open.
pub fn check(state: &Path, states: &Path, evidence: &Path, json: bool) -> Result<ExitCode, String> {
    let text = std::fs::read_to_string(state).map_err(|e| format!("{}: {e}", state.display()))?;
    let document: StateDocument = serde_json::from_str(&text).map_err(|e| format!("{}: {e}", state.display()))?;
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
    let report = check_ancestry(&document, &resolve_state, &resolve_evidence).map_err(|e| e.to_string())?;
    print!("{}", rendered(&report, json));
    Ok(ExitCode::SUCCESS)
}

/// The report as a page: the chain from the state to its root, then every
/// open check. `--json` prints the same report as one object.
fn rendered(report: &AncestryReport, json: bool) -> String {
    if json {
        return format!("{}\n", serde_json::to_value(report).expect("a report serializes"));
    }
    let mut out = String::from("chain, state first\n");
    for entry in &report.chain {
        out.push_str(&format!("  {}  program {}\n", entry.lineage_identity, entry.program_identity));
    }
    out.push_str(&format!("root reached after {} states\n", report.chain.len()));
    if !report.unverifiable.is_empty() {
        out.push_str("open checks\n");
        for note in &report.unverifiable {
            out.push_str(&format!("  {note}\n"));
        }
    }
    out
}

#[cfg(test)]
#[path = "lineage_test.rs"]
mod tests;
