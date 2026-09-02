use super::{
    build_manifest, digest_of, manifest_bytes, record_bytes, verify_adoption, verify_bundle, AdoptionRecord,
    MANIFEST_FILE,
};
use foe_contract::fingerprint::canonical;
use foe_log::append::Writer;
use foe_log::{
    EpisodeStart, EventData, Outcome, RuntimeInfo, SandboxInfo, SandboxMode, VerificationResult, VerificationStatus,
};
use serde_json::json;
use std::path::Path;

fn digest(fill: char) -> String {
    format!("sha256:{}", fill.to_string().repeat(64))
}

fn write_log(dir: &Path, contract_fingerprint: &str, verifier_fingerprint: &str, candidate_sha256: Option<String>) {
    std::fs::create_dir_all(dir).unwrap();
    let mut writer = Writer::create(dir, None).unwrap();
    writer
        .append(EventData::EpisodeStart(EpisodeStart {
            id: "ep_proposal".into(),
            parent_id: None,
            fork_origin: None,
            team_id: None,
            contract: json!({"name": "proposal"}),
            contract_fingerprint: contract_fingerprint.into(),
            task: "propose an improvement".into(),
            runtime: RuntimeInfo { version: "0.2.0".into(), build: digest('9') },
            sandbox: SandboxInfo {
                mode: SandboxMode::Off,
                landlock_abi: 0,
                resolved_permissions: Default::default(),
                process_boundary: Default::default(),
            },
            effective_budget: None,
        }))
        .unwrap();
    writer
        .append(EventData::VerificationResult(VerificationResult {
            step: 1,
            tool: "check".into(),
            verifier_fingerprint: verifier_fingerprint.into(),
            status: VerificationStatus::Accepted,
            findings: Vec::new(),
            error: None,
            candidate_sha256,
            duration_ms: 1,
        }))
        .unwrap();
    writer.append(EventData::EpisodeEnd { outcome: Outcome::Completed { value: json!({}) } }).unwrap();
}

fn bundle(root: &Path, predecessor: Option<String>, candidate_sha256: Option<String>) -> (String, String) {
    let proposal_log = "episode/episode.jsonl";
    let predecessor_for_log = predecessor.clone().unwrap_or_else(|| digest('a'));
    let verifier = digest('b');
    write_log(&root.join("episode"), &predecessor_for_log, &verifier, candidate_sha256);
    let fingerprint_document = canonical(&json!({"name": "candidate", "runtime": {"version": "0.2.0"}}));
    std::fs::write(root.join("fingerprint-document.json"), &fingerprint_document).unwrap();
    let artifact_manifest = canonical(&json!([{"path": "candidate.patch", "sha256": digest('c')} ]));
    std::fs::write(root.join("artifact-manifest.json"), &artifact_manifest).unwrap();
    let record = AdoptionRecord {
        schema_version: 2,
        contract_fingerprint: digest_of(fingerprint_document.as_bytes()),
        fingerprint_document_sha256: digest_of(fingerprint_document.as_bytes()),
        artifact_manifest_sha256: digest_of(artifact_manifest.as_bytes()),
        verification_log: proposal_log.into(),
        verification_seq: 1,
        predecessor_contract_fingerprint: predecessor,
    };
    std::fs::write(root.join("adoption-record.json"), record_bytes(&record).unwrap()).unwrap();
    let manifest = build_manifest(root, proposal_log, "adoption-record.json").unwrap();
    std::fs::write(root.join(MANIFEST_FILE), manifest_bytes(&manifest).unwrap()).unwrap();
    (record.contract_fingerprint, verifier)
}

#[test]
fn standalone_verification_returns_the_verifier_fingerprint() {
    let root = tempfile::tempdir().unwrap();
    let predecessor = digest('a');
    let (contract, verifier) = bundle(root.path(), Some(predecessor.clone()), None);
    let verified = verify_adoption(root.path(), Some(&predecessor)).unwrap();
    assert_eq!(verified.contract_fingerprint, contract);
    assert_eq!(verified.predecessor_contract_fingerprint, Some(predecessor.clone()));
    assert_eq!(verified.verifier_fingerprint, verifier);
    assert_eq!(verified.verification_tool, "check");
    assert_eq!(verified.candidate_file, None, "an event without candidate_sha256 verifies with no association");

    let standalone = verify_adoption(root.path(), None).unwrap();
    assert_eq!(standalone.predecessor_contract_fingerprint, Some(predecessor));
}

/// docs/adoption.md "Verification result": when the accepted event attests
/// `candidate_sha256`, verification requires a retained canonical-JSON
/// file with that digest and names it in `candidate_file`.
#[test]
fn an_attested_candidate_must_be_retained_as_canonical_json() {
    let judged = canonical(&json!({"branch": "configure-workflow"}));

    let root = tempfile::tempdir().unwrap();
    std::fs::write(root.path().join("candidate.json"), &judged).unwrap();
    bundle(root.path(), None, Some(digest_of(judged.as_bytes())));
    let verified = verify_adoption(root.path(), None).unwrap();
    assert_eq!(verified.candidate_file.as_deref(), Some("candidate.json"));

    let root = tempfile::tempdir().unwrap();
    bundle(root.path(), None, Some(digest_of(judged.as_bytes())));
    let error = verify_adoption(root.path(), None).unwrap_err().to_string();
    assert!(error.contains("candidate_sha256"), "an absent candidate file fails: {error}");

    let root = tempfile::tempdir().unwrap();
    std::fs::write(root.path().join("candidate.json"), canonical(&json!({"branch": "define-tool"}))).unwrap();
    bundle(root.path(), None, Some(digest_of(judged.as_bytes())));
    let error = verify_adoption(root.path(), None).unwrap_err().to_string();
    assert!(error.contains("candidate_sha256"), "a differing digest fails: {error}");

    let pretty = serde_json::to_string_pretty(&json!({"branch": "configure-workflow"})).unwrap();
    let root = tempfile::tempdir().unwrap();
    std::fs::write(root.path().join("candidate.json"), &pretty).unwrap();
    bundle(root.path(), None, Some(digest_of(pretty.as_bytes())));
    let error = verify_adoption(root.path(), None).unwrap_err().to_string();
    assert!(error.contains("canonical JSON"), "a non-canonical candidate fails: {error}");
}

/// A retained log stating an unsupported format version is refused with
/// the typed error naming both versions, before event parsing.
#[test]
fn a_retained_log_with_an_unsupported_version_is_refused() {
    let root = tempfile::tempdir().unwrap();
    bundle(root.path(), None, None);
    let log_path = root.path().join("episode/episode.jsonl");
    let log = std::fs::read_to_string(&log_path).unwrap();
    let (first, rest) = log.split_once('\n').unwrap();
    let mut event: serde_json::Value = serde_json::from_str(first).unwrap();
    event["version"] = json!(99);
    std::fs::write(&log_path, format!("{event}\n{rest}")).unwrap();
    let manifest = build_manifest(root.path(), "episode/episode.jsonl", "adoption-record.json").unwrap();
    std::fs::write(root.path().join(MANIFEST_FILE), manifest_bytes(&manifest).unwrap()).unwrap();
    let error = verify_adoption(root.path(), None).unwrap_err().to_string();
    assert!(error.contains("version 99"), "{error}");
    assert!(error.contains("version 3"), "{error}");
}

#[test]
fn policy_predecessor_must_match_the_record_and_proposal_root() {
    let root = tempfile::tempdir().unwrap();
    bundle(root.path(), Some(digest('a')), None);
    let error = verify_adoption(root.path(), Some(&digest('d'))).unwrap_err().to_string();
    assert!(error.contains("predecessor_contract_fingerprint"), "{error}");

    let root = tempfile::tempdir().unwrap();
    bundle(root.path(), None, None);
    let error = verify_adoption(root.path(), Some(&digest('a'))).unwrap_err().to_string();
    assert!(error.contains("predecessor_contract_fingerprint"), "{error}");
}

/// Facts are established from the bytes the digest pass verified:
/// `verify_bundle` retains every listed file's bytes, and the later
/// phases of `verify_adoption` parse only the retained map, never the
/// directory, so a file rewritten after the digest pass cannot reach
/// fact establishment.
#[test]
fn facts_come_from_retained_bytes_not_from_re_reads() {
    let root = tempfile::tempdir().unwrap();
    bundle(root.path(), None, None);
    let verified = verify_bundle(root.path()).unwrap();
    std::fs::write(root.path().join("adoption-record.json"), "rewritten after the digest pass").unwrap();
    std::fs::write(root.path().join("episode/episode.jsonl"), "rewritten after the digest pass").unwrap();
    for file in &verified.manifest.files {
        assert_eq!(digest_of(&verified.files[&file.path]), file.sha256, "{} holds its verified bytes", file.path);
    }
}

#[test]
fn modified_files_are_rejected_before_their_contents_are_trusted() {
    let root = tempfile::tempdir().unwrap();
    bundle(root.path(), None, None);
    std::fs::write(root.path().join("artifact-manifest.json"), "tampered").unwrap();
    let error = verify_adoption(root.path(), None).unwrap_err().to_string();
    assert!(error.contains("artifact-manifest.json"), "{error}");
}

#[test]
fn canonical_record_and_fingerprint_document_are_required() {
    let root = tempfile::tempdir().unwrap();
    bundle(root.path(), None, None);
    let record_path = root.path().join("adoption-record.json");
    let record: serde_json::Value = serde_json::from_slice(&std::fs::read(&record_path).unwrap()).unwrap();
    std::fs::write(&record_path, serde_json::to_string_pretty(&record).unwrap()).unwrap();
    let manifest = build_manifest(root.path(), "episode/episode.jsonl", "adoption-record.json").unwrap();
    std::fs::write(root.path().join(MANIFEST_FILE), manifest_bytes(&manifest).unwrap()).unwrap();
    let error = verify_adoption(root.path(), None).unwrap_err().to_string();
    assert!(error.contains("canonical serialization"), "{error}");
}
