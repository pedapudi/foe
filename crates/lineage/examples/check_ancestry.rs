//! Verifies a state document's ancestry claim through directory-backed
//! resolvers: `check_ancestry STATE STATES_DIR EVIDENCE_DIR` reads the
//! state document from STATE, resolves states from `STATES_DIR/<hex>.json`
//! and bundles from `EVIDENCE_DIR/<hex>`, each named by its digest without
//! the `sha256:` prefix, and prints one JSON object: the chain of program
//! identities, this state's program first and each entry the previous
//! entry's parent, and every check the retained evidence leaves open.
//!
//! `foe plan --states --evidence` performs this verification for a
//! configuration it resolves; this example is the same checker over a
//! state document that is not a configuration, as the self-improvement
//! runner's harness adoptions record.

use foe_lineage::{check_ancestry, StateDocument};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

fn run(args: &[String]) -> Result<String, String> {
    let [state, states, evidence] = args else {
        return Err("usage: check_ancestry STATE STATES_DIR EVIDENCE_DIR".into());
    };
    let text = std::fs::read_to_string(state).map_err(|e| format!("{state}: {e}"))?;
    let document: StateDocument = serde_json::from_str(&text).map_err(|e| format!("{state}: {e}"))?;
    let hex_of = |digest: &str| digest.strip_prefix("sha256:").unwrap_or(digest).to_string();
    let resolve_state = |id: &str| -> Result<StateDocument, String> {
        let path = Path::new(states).join(format!("{}.json", hex_of(id)));
        let text = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))
    };
    let resolve_evidence = |address: &str| -> Result<PathBuf, String> {
        let dir = Path::new(evidence).join(hex_of(address));
        match dir.is_dir() {
            true => Ok(dir),
            false => Err(format!("{} is not a directory", dir.display())),
        }
    };
    let report = check_ancestry(&document, &resolve_state, &resolve_evidence).map_err(|e| e.to_string())?;
    let chain: Vec<&str> = report.chain.iter().map(|e| e.program_identity.as_str()).collect();
    Ok(serde_json::json!({ "chain": chain, "unverifiable": report.unverifiable }).to_string())
}

fn main() -> ExitCode {
    match run(&std::env::args().skip(1).collect::<Vec<_>>()) {
        Ok(report) => {
            println!("{report}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("check_ancestry: {error}");
            ExitCode::FAILURE
        }
    }
}
