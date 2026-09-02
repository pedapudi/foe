use super::{run, RECORD_FILE};
use foe_contract::fingerprint::canonical;
use foe_evidence::{digest_of, verify_bundle, AdoptionRecord, MANIFEST_FILE};
use std::ops::Deref;
use std::path::Path;

struct ScratchDir(Option<tempfile::TempDir>);

impl ScratchDir {
    fn path(&self) -> &Path {
        self.0.as_ref().unwrap().path()
    }
}

impl Deref for ScratchDir {
    type Target = Path;

    fn deref(&self) -> &Self::Target {
        self.path()
    }
}

impl Drop for ScratchDir {
    fn drop(&mut self) {
        let Some(mut dir) = self.0.take() else { return };
        if std::thread::panicking() {
            eprintln!("retained failed test directory: {}", dir.path().display());
            dir.disable_cleanup(true);
            return;
        }
        let path = dir.path().to_path_buf();
        dir.close().unwrap_or_else(|error| panic!("failed to remove test directory {}: {error}", path.display()));
    }
}

fn tmp(name: &str) -> ScratchDir {
    assert_eq!(Path::new(name).file_name(), Some(name.as_ref()), "scratch name must be one path component");
    ScratchDir(Some(tempfile::Builder::new().prefix(&format!("foe-build-evidence-bundle-{name}-")).tempdir().unwrap()))
}

/// A bundle directory holding a proposal log, a candidate fingerprint
/// document, and an artifact manifest, the files a caller retains before
/// invoking the binary.
fn bundle(name: &str) -> ScratchDir {
    let dir = tmp(name);
    std::fs::create_dir_all(dir.join("episode")).unwrap();
    std::fs::write(dir.join("episode/episode.jsonl"), b"{}\n").unwrap();
    std::fs::write(dir.join("child-fingerprint.json"), b"{\"a\":1,\"b\":2}").unwrap();
    std::fs::write(dir.join("artifact-manifest.json"), b"[]").unwrap();
    dir
}

fn args(dir: &Path, seq: &str) -> Vec<String> {
    [
        dir.to_string_lossy().as_ref(),
        "episode/episode.jsonl",
        "child-fingerprint.json",
        "artifact-manifest.json",
        "episode/episode.jsonl",
        seq,
    ]
    .map(str::to_string)
    .to_vec()
}

#[test]
fn writes_the_adoption_record_and_canonical_manifest_and_prints_the_address() {
    let dir = bundle("valid");
    let address = run(&args(&dir, "7")).unwrap();
    let bundle = verify_bundle(&dir).unwrap();
    assert_eq!(address, bundle.address);
    assert_eq!(bundle.manifest.proposal_log, "episode/episode.jsonl");
    assert_eq!(bundle.manifest.adoption_record, RECORD_FILE);
    let bytes = std::fs::read(dir.join(MANIFEST_FILE)).unwrap();
    assert_eq!(address, digest_of(&bytes));
    let record: AdoptionRecord = serde_json::from_slice(&std::fs::read(dir.join(RECORD_FILE)).unwrap()).unwrap();
    let document_bytes = std::fs::read(dir.join("child-fingerprint.json")).unwrap();
    let document: serde_json::Value = serde_json::from_slice(&document_bytes).unwrap();
    assert_eq!(record.schema_version, 2);
    assert_eq!(record.contract_fingerprint, digest_of(canonical(&document).as_bytes()));
    assert_eq!(record.fingerprint_document_sha256, digest_of(&document_bytes));
    assert_eq!(record.artifact_manifest_sha256, digest_of(b"[]"));
    assert_eq!(record.verification_log, "episode/episode.jsonl");
    assert_eq!(record.verification_seq, 7);
}

#[test]
fn rebuilding_replaces_the_prior_record_and_manifest() {
    let dir = bundle("rebuild");
    let first = run(&args(&dir, "1")).unwrap();
    let second = run(&args(&dir, "2")).unwrap();
    assert_ne!(first, second, "changed coordinates change the content address");
    let bundle = verify_bundle(&dir).unwrap();
    assert_eq!(second, bundle.address);
    assert_eq!(bundle.manifest.files.iter().filter(|f| f.path == RECORD_FILE).count(), 1);
}

#[test]
fn refuses_bad_arguments() {
    let dir = bundle("bad-arguments");
    assert!(run(&[]).unwrap_err().starts_with("usage:"));
    assert!(run(&args(&dir, "not-a-number")).unwrap_err().contains("VERIFICATION_SEQ"));
    let mut absolute = args(&dir, "1");
    absolute[2] = "/etc/child-fingerprint.json".into();
    assert!(run(&absolute).unwrap_err().contains("FINGERPRINT_DOCUMENT"));
    let mut missing = args(&dir, "1");
    missing[3] = "absent-manifest.json".into();
    assert!(run(&missing).unwrap_err().contains("absent-manifest.json"));
    let mut bad_predecessor = args(&dir, "1");
    bad_predecessor.push("sha256:short".into());
    assert!(run(&bad_predecessor).unwrap_err().contains("PREDECESSOR_CONTRACT_FINGERPRINT"));
}

#[test]
fn records_an_optional_predecessor_fingerprint() {
    let dir = bundle("predecessor");
    let predecessor = format!("sha256:{}", "a".repeat(64));
    let mut args = args(&dir, "1");
    args.push(predecessor.clone());
    run(&args).unwrap();
    let record: AdoptionRecord = serde_json::from_slice(&std::fs::read(dir.join(RECORD_FILE)).unwrap()).unwrap();
    assert_eq!(record.predecessor_contract_fingerprint, Some(predecessor));
}
