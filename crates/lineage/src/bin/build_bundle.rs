//! Completes an evidence bundle directory in place. The caller has already
//! retained every file the bundle carries — the proposal episode tree, the
//! candidate identity document, the artifact manifest, and the candidate
//! envelope. This binary writes the two files whose form the crate owns:
//! the candidate binding record pairing the retained envelope's digest with
//! the accepted verification's coordinates, and the canonical manifest. It
//! prints the bundle's content address. Builders outside the runtime — the
//! self-improvement runner among them — invoke it so the canonical form has
//! one implementation.

use foe_lineage::{
    binding_bytes, build_manifest, digest_of, manifest_bytes, require_manifest_path, CandidateBinding, MANIFEST_FILE,
};
use std::path::Path;
use std::process::ExitCode;

/// File name of the candidate binding record this binary writes.
pub const BINDING_FILE: &str = "candidate-binding.json";

const USAGE: &str = "usage: build-bundle DIR PROPOSAL_LOG CANDIDATE_ENVELOPE VERIFICATION_LOG VERIFICATION_SEQ";

/// Writes the binding record and manifest into the bundle at `args[0]` and
/// returns the bundle's content address. The path arguments after the
/// directory are relative, in manifest path form.
fn run(args: &[String]) -> Result<String, String> {
    let [dir, proposal_log, envelope, verification_log, seq] = args else {
        return Err(USAGE.into());
    };
    let seq: u64 = seq.parse().map_err(|e| format!("VERIFICATION_SEQ: {e}"))?;
    for (key, path) in
        [("PROPOSAL_LOG", proposal_log), ("CANDIDATE_ENVELOPE", envelope), ("VERIFICATION_LOG", verification_log)]
    {
        require_manifest_path(key, path).map_err(|e| e.to_string())?;
    }
    let dir = Path::new(dir);
    let envelope_bytes = std::fs::read(dir.join(envelope)).map_err(|e| format!("{envelope}: {e}"))?;
    let record = CandidateBinding {
        schema_version: 1,
        candidate_sha256: digest_of(&envelope_bytes),
        verification_log: verification_log.clone(),
        verification_seq: seq,
    };
    std::fs::write(dir.join(BINDING_FILE), binding_bytes(&record).map_err(|e| e.to_string())?)
        .map_err(|e| format!("{BINDING_FILE}: {e}"))?;
    let manifest = build_manifest(dir, proposal_log, envelope, Some(BINDING_FILE)).map_err(|e| e.to_string())?;
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
            eprintln!("build-bundle: {error}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
#[path = "build_bundle_test.rs"]
mod tests;
