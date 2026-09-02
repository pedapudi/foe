//! Completes an evidence bundle directory in place. The caller has already
//! retained every file the bundle carries — the proposal episode tree, the
//! candidate fingerprint document, and the artifact manifest. This binary
//! writes the two files whose form the crate owns: the adoption record
//! associating the candidate fingerprint with the accepting
//! verification's coordinates, and the canonical manifest. It prints the
//! bundle's content address. Builders outside the runtime — the
//! self-improvement runner among them — invoke it so the canonical form
//! has one implementation.

use foe_contract::fingerprint::canonical;
use foe_evidence::{
    build_manifest, digest_of, manifest_bytes, record_bytes, require_digest, require_manifest_path, AdoptionRecord,
    MANIFEST_FILE,
};
use std::path::Path;
use std::process::ExitCode;

/// File name of the adoption record this binary writes.
pub const RECORD_FILE: &str = "adoption-record.json";

const USAGE: &str = "usage: build-evidence-bundle DIR PROPOSAL_LOG FINGERPRINT_DOCUMENT ARTIFACT_MANIFEST \
                     VERIFICATION_LOG VERIFICATION_SEQ [PREDECESSOR_CONTRACT_FINGERPRINT]";

/// Writes the adoption record and manifest into the bundle at `args[0]`
/// and returns the bundle's content address. The path arguments after the
/// directory are relative, in manifest path form; the fingerprint-document
/// and artifact-manifest digests and the candidate's contract fingerprint are
/// computed from the retained files.
fn run(args: &[String]) -> Result<String, String> {
    let (dir, proposal_log, fingerprint_document, artifact_manifest, verification_log, seq, predecessor) = match args {
        [dir, proposal_log, fingerprint_document, artifact_manifest, verification_log, seq] => {
            (dir, proposal_log, fingerprint_document, artifact_manifest, verification_log, seq, None)
        }
        [dir, proposal_log, fingerprint_document, artifact_manifest, verification_log, seq, predecessor] => {
            (dir, proposal_log, fingerprint_document, artifact_manifest, verification_log, seq, Some(predecessor))
        }
        _ => return Err(USAGE.into()),
    };
    let seq: u64 = seq.parse().map_err(|e| format!("VERIFICATION_SEQ: {e}"))?;
    if let Some(predecessor) = predecessor {
        require_digest("PREDECESSOR_CONTRACT_FINGERPRINT", predecessor).map_err(|e| e.to_string())?;
    }
    for (key, path) in [
        ("PROPOSAL_LOG", proposal_log),
        ("FINGERPRINT_DOCUMENT", fingerprint_document),
        ("ARTIFACT_MANIFEST", artifact_manifest),
        ("VERIFICATION_LOG", verification_log),
    ] {
        require_manifest_path(key, path).map_err(|e| e.to_string())?;
    }
    let dir = Path::new(dir);
    let read = |name: &str| std::fs::read(dir.join(name)).map_err(|e| format!("{name}: {e}"));
    let document_bytes = read(fingerprint_document)?;
    let document: serde_json::Value =
        serde_json::from_slice(&document_bytes).map_err(|e| format!("{fingerprint_document}: {e}"))?;
    if canonical(&document).as_bytes() != document_bytes {
        return Err(format!("{fingerprint_document}: is canonical JSON"));
    }
    let record = AdoptionRecord {
        schema_version: 2,
        contract_fingerprint: digest_of(&document_bytes),
        fingerprint_document_sha256: digest_of(&document_bytes),
        artifact_manifest_sha256: digest_of(&read(artifact_manifest)?),
        verification_log: verification_log.clone(),
        verification_seq: seq,
        predecessor_contract_fingerprint: predecessor.cloned(),
    };
    std::fs::write(dir.join(RECORD_FILE), record_bytes(&record).map_err(|e| e.to_string())?)
        .map_err(|e| format!("{RECORD_FILE}: {e}"))?;
    let manifest = build_manifest(dir, proposal_log, RECORD_FILE).map_err(|e| e.to_string())?;
    let bytes = manifest_bytes(&manifest).map_err(|e| e.to_string())?;
    std::fs::write(dir.join(MANIFEST_FILE), &bytes).map_err(|e| format!("{MANIFEST_FILE}: {e}"))?;
    Ok(digest_of(&bytes))
}

fn main() -> ExitCode {
    match run(&std::env::args().skip(1).collect::<Vec<_>>()) {
        Ok(address) => {
            println!("{address}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("build-evidence-bundle: {error}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
#[path = "build_bundle_test.rs"]
mod tests;
