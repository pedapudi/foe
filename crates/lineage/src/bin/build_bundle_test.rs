use super::{run, BINDING_FILE};
use foe_lineage::{digest_of, verify_bundle, CandidateBinding, MANIFEST_FILE};
use std::path::{Path, PathBuf};

fn tmp(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("foe-build-bundle-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// A bundle directory holding a proposal log and a candidate envelope, the
/// files a caller retains before invoking the binary.
fn bundle(name: &str) -> PathBuf {
    let dir = tmp(name);
    std::fs::create_dir_all(dir.join("episode")).unwrap();
    std::fs::write(dir.join("episode/episode.jsonl"), b"{}\n").unwrap();
    std::fs::write(dir.join("candidate-envelope.json"), b"{\"program_identity\":\"sha256:0\"}").unwrap();
    dir
}

fn args(dir: &Path, seq: &str) -> Vec<String> {
    [dir.to_string_lossy().as_ref(), "episode/episode.jsonl", "candidate-envelope.json", "episode/episode.jsonl", seq]
        .map(str::to_string)
        .to_vec()
}

#[test]
fn writes_the_binding_record_and_canonical_manifest_and_prints_the_address() {
    let dir = bundle("valid");
    let address = run(&args(&dir, "7")).unwrap();
    let (manifest, verified_address) = verify_bundle(&dir).unwrap();
    assert_eq!(address, verified_address);
    assert_eq!(manifest.proposal_log, "episode/episode.jsonl");
    assert_eq!(manifest.candidate_envelope, "candidate-envelope.json");
    assert_eq!(manifest.candidate_binding.as_deref(), Some(BINDING_FILE));
    let bytes = std::fs::read(dir.join(MANIFEST_FILE)).unwrap();
    assert_eq!(address, digest_of(&bytes));
    let record: CandidateBinding = serde_json::from_slice(&std::fs::read(dir.join(BINDING_FILE)).unwrap()).unwrap();
    let envelope = std::fs::read(dir.join("candidate-envelope.json")).unwrap();
    assert_eq!(record.candidate_sha256, digest_of(&envelope));
    assert_eq!(record.verification_log, "episode/episode.jsonl");
    assert_eq!(record.verification_seq, 7);
}

#[test]
fn rebuilding_replaces_the_prior_record_and_manifest() {
    let dir = bundle("rebuild");
    let first = run(&args(&dir, "1")).unwrap();
    let second = run(&args(&dir, "2")).unwrap();
    assert_ne!(first, second, "changed coordinates change the content address");
    let (manifest, verified_address) = verify_bundle(&dir).unwrap();
    assert_eq!(second, verified_address);
    assert_eq!(manifest.files.iter().filter(|f| f.path == BINDING_FILE).count(), 1);
}

#[test]
fn refuses_bad_arguments() {
    let dir = bundle("bad-arguments");
    assert!(run(&[]).unwrap_err().starts_with("usage:"));
    assert!(run(&args(&dir, "not-a-number")).unwrap_err().contains("VERIFICATION_SEQ"));
    let mut absolute = args(&dir, "1");
    absolute[2] = "/etc/candidate-envelope.json".into();
    assert!(run(&absolute).unwrap_err().contains("CANDIDATE_ENVELOPE"));
    let mut missing = args(&dir, "1");
    missing[2] = "absent-envelope.json".into();
    assert!(run(&missing).unwrap_err().contains("absent-envelope.json"));
}
