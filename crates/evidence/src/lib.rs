//! Portable evidence for adopting a proposed execution contract.
//!
//! An evidence bundle retains a proposal episode tree, the proposed
//! contract's canonical fingerprint document, an artifact manifest, and a
//! canonical record that associates those files with one accepted verifier result.
//! Verification reads only the supplied directory. It runs no command,
//! exercises no grant, and writes no log.

#![forbid(unsafe_code)]

use foe_contract::fingerprint::{canonical, sha256_hex};
use foe_log::{fold, Event, EventData, State, VerificationStatus};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::Path;

#[derive(Debug, thiserror::Error)]
pub enum EvidenceError {
    #[error("{key}: {rule}")]
    Invalid { key: String, rule: String },
    #[error("{0}")]
    Parse(#[from] serde_json::Error),
    #[error("{0}")]
    Io(#[from] std::io::Error),
}

fn invalid(key: impl Into<String>, rule: impl Into<String>) -> EvidenceError {
    EvidenceError::Invalid { key: key.into(), rule: rule.into() }
}

pub fn require_digest(key: &str, text: &str) -> Result<(), EvidenceError> {
    let hex = text.strip_prefix("sha256:").unwrap_or("");
    match hex.len() == 64 && hex.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')) {
        true => Ok(()),
        false => Err(invalid(key, "is `sha256:` followed by 64 lowercase hex digits")),
    }
}

pub fn require_manifest_path(key: &str, path: &str) -> Result<(), EvidenceError> {
    match !path.contains('\\') && path.split('/').all(|component| !matches!(component, "" | "." | "..")) {
        true => Ok(()),
        false => Err(invalid(key, "is a relative path with no empty, `.`, or `..` component")),
    }
}

pub fn digest_of(bytes: &[u8]) -> String {
    format!("sha256:{}", sha256_hex(bytes))
}

pub const MANIFEST_FILE: &str = "manifest.json";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManifestFile {
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub schema_version: u32,
    /// In byte order of `path`, without duplicates.
    pub files: Vec<ManifestFile>,
    pub proposal_log: String,
    pub adoption_record: String,
}

pub fn check_manifest(bytes: &[u8]) -> Result<Manifest, EvidenceError> {
    let manifest: Manifest = serde_json::from_slice(bytes)?;
    let key = "evidence.manifest";
    if canonical(&serde_json::to_value(&manifest)?).as_bytes() != bytes {
        return Err(invalid(key, "is the canonical serialization of its object"));
    }
    if manifest.schema_version != 1 {
        return Err(invalid(format!("{key}.schema_version"), "is 1"));
    }
    for (index, file) in manifest.files.iter().enumerate() {
        require_manifest_path(&format!("{key}.files[{index}].path"), &file.path)?;
        require_digest(&format!("{key}.files[{index}].sha256"), &file.sha256)?;
        if index > 0 && manifest.files[index - 1].path >= file.path {
            return Err(invalid(format!("{key}.files[{index}].path"), "follows the prior path in byte order"));
        }
    }
    for (name, path) in [("proposal_log", &manifest.proposal_log), ("adoption_record", &manifest.adoption_record)] {
        if !manifest.files.iter().any(|file| &file.path == path) {
            return Err(invalid(format!("{key}.{name}"), "names a listed file"));
        }
    }
    Ok(manifest)
}

/// A bundle whose files all matched the manifest. `files` holds the
/// digest-verified bytes of every listed file, keyed by manifest path.
/// Facts are established from these bytes, never from a re-read, so the
/// directory changing after the digest pass cannot alter them.
#[derive(Debug)]
pub struct VerifiedBundle {
    pub manifest: Manifest,
    /// Digest of the canonical manifest bytes: the bundle address.
    pub address: String,
    pub files: BTreeMap<String, Vec<u8>>,
}

pub fn verify_bundle(dir: &Path) -> Result<VerifiedBundle, EvidenceError> {
    let bytes = std::fs::read(dir.join(MANIFEST_FILE))
        .map_err(|error| invalid("evidence.manifest", format!("is readable: {}: {error}", dir.display())))?;
    let manifest = check_manifest(&bytes)?;
    let mut files = BTreeMap::new();
    for file in &manifest.files {
        let content = std::fs::read(dir.join(&file.path))
            .map_err(|error| invalid(format!("evidence file {}", file.path), format!("is readable: {error}")))?;
        if content.len() as u64 != file.bytes || digest_of(&content) != file.sha256 {
            return Err(invalid(format!("evidence file {}", file.path), "matches its manifest length and digest"));
        }
        files.insert(file.path.clone(), content);
    }
    Ok(VerifiedBundle { manifest, address: digest_of(&bytes), files })
}

pub fn build_manifest(dir: &Path, proposal_log: &str, adoption_record: &str) -> Result<Manifest, EvidenceError> {
    require_manifest_path("proposal_log", proposal_log)?;
    require_manifest_path("adoption_record", adoption_record)?;
    let mut files = Vec::new();
    let mut pending = vec![dir.to_path_buf()];
    while let Some(next) = pending.pop() {
        for entry in std::fs::read_dir(&next)? {
            let path = entry?.path();
            if path.is_dir() {
                pending.push(path);
            } else if path != dir.join(MANIFEST_FILE) {
                let content = std::fs::read(&path)?;
                let parts: Vec<String> = path
                    .strip_prefix(dir)
                    .expect("the walk stays below the bundle")
                    .components()
                    .map(|component| component.as_os_str().to_string_lossy().into_owned())
                    .collect();
                files.push(ManifestFile {
                    path: parts.join("/"),
                    bytes: content.len() as u64,
                    sha256: digest_of(&content),
                });
            }
        }
    }
    files.sort_by(|left, right| left.path.cmp(&right.path));
    let manifest = Manifest {
        schema_version: 1,
        files,
        proposal_log: proposal_log.into(),
        adoption_record: adoption_record.into(),
    };
    check_manifest(&manifest_bytes(&manifest)?)
}

pub fn manifest_bytes(manifest: &Manifest) -> Result<Vec<u8>, EvidenceError> {
    Ok(canonical(&serde_json::to_value(manifest)?).into_bytes())
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AdoptionRecord {
    pub schema_version: u32,
    pub contract_fingerprint: String,
    pub fingerprint_document_sha256: String,
    pub artifact_manifest_sha256: String,
    pub verification_log: String,
    pub verification_seq: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub predecessor_contract_fingerprint: Option<String>,
}

pub fn record_bytes(record: &AdoptionRecord) -> Result<Vec<u8>, EvidenceError> {
    Ok(canonical(&serde_json::to_value(record)?).into_bytes())
}

/// Facts established by standalone adoption verification. The caller's
/// adoption policy decides whether `verifier_fingerprint` is permitted.
/// `contract_fingerprint` is checked against the retained fingerprint
/// document alone. When the accepted event attests `candidate_sha256`,
/// `candidate_file` names the retained canonical-JSON file with that
/// digest, so the bytes the verifier judged are established; whether that
/// value corresponds to the candidate contract remains the record
/// author's claim. When the event lacks the field, `candidate_file` is
/// `None` and the whole candidate-to-verification association is the
/// record author's claim, left to that policy.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct VerifiedAdoption {
    pub bundle_address: String,
    pub contract_fingerprint: String,
    pub predecessor_contract_fingerprint: Option<String>,
    pub verifier_fingerprint: String,
    pub verification_tool: String,
    pub verification_log: String,
    pub verification_seq: u64,
    pub candidate_file: Option<String>,
}

/// Verifies a portable evidence bundle. When `expected_predecessor` is
/// present, both the record and proposal root must name that fingerprint.
pub fn verify_adoption(dir: &Path, expected_predecessor: Option<&str>) -> Result<VerifiedAdoption, EvidenceError> {
    if let Some(expected) = expected_predecessor {
        require_digest("expected_predecessor", expected)?;
    }
    let bundle = verify_bundle(dir)?;
    let manifest = &bundle.manifest;
    let record_content = &bundle.files[&manifest.adoption_record];
    let record: AdoptionRecord = serde_json::from_slice(record_content)?;
    if &record_bytes(&record)? != record_content {
        return Err(invalid("adoption_record", "is the canonical serialization of its object"));
    }
    if record.schema_version != 2 {
        return Err(invalid("adoption_record.schema_version", "is 2"));
    }
    require_digest("adoption_record.contract_fingerprint", &record.contract_fingerprint)?;
    require_digest("adoption_record.fingerprint_document_sha256", &record.fingerprint_document_sha256)?;
    require_digest("adoption_record.artifact_manifest_sha256", &record.artifact_manifest_sha256)?;
    require_manifest_path("adoption_record.verification_log", &record.verification_log)?;
    if let Some(predecessor) = &record.predecessor_contract_fingerprint {
        require_digest("adoption_record.predecessor_contract_fingerprint", predecessor)?;
    }
    if let Some(expected) = expected_predecessor {
        if record.predecessor_contract_fingerprint.as_deref() != Some(expected) {
            return Err(invalid(
                "adoption_record.predecessor_contract_fingerprint",
                "equals the predecessor required by adoption policy",
            ));
        }
    }

    let listed = |digest: &str| manifest.files.iter().find(|file| file.sha256 == digest);
    let fingerprint_file = listed(&record.fingerprint_document_sha256).ok_or_else(|| {
        invalid("adoption_record.fingerprint_document_sha256", "equals the digest of a retained file")
    })?;
    if listed(&record.artifact_manifest_sha256).is_none() {
        return Err(invalid("adoption_record.artifact_manifest_sha256", "equals the digest of a retained file"));
    }
    let fingerprint_content = &bundle.files[&fingerprint_file.path];
    let fingerprint_document: Value = serde_json::from_slice(fingerprint_content)?;
    if canonical(&fingerprint_document).as_bytes() != fingerprint_content.as_slice() {
        return Err(invalid("fingerprint_document", "is canonical JSON"));
    }
    if digest_of(fingerprint_content) != record.contract_fingerprint {
        return Err(invalid(
            "adoption_record.contract_fingerprint",
            "equals the digest of the canonical fingerprint document",
        ));
    }

    let logs = read_logs(&bundle)?;
    let (verification_events, _) = logs
        .get(&record.verification_log)
        .ok_or_else(|| invalid("adoption_record.verification_log", "names an episode log in the bundle"))?;
    let event = verification_events
        .iter()
        .find(|event| event.seq == record.verification_seq)
        .ok_or_else(|| invalid("adoption_record.verification_seq", "names an event in verification_log"))?;
    let EventData::VerificationResult(result) = &event.data else {
        return Err(invalid("adoption_record.verification_seq", "names a verification/result event"));
    };
    if result.status != VerificationStatus::Accepted {
        return Err(invalid("adoption_record.verification_seq", "names an accepted verification/result"));
    }
    require_digest("verification/result.verifier_fingerprint", &result.verifier_fingerprint)?;
    let candidate_file = match &result.candidate_sha256 {
        None => None,
        Some(attested) => {
            require_digest("verification/result.candidate_sha256", attested)?;
            let file = listed(attested).ok_or_else(|| {
                invalid("verification/result.candidate_sha256", "equals the digest of a retained candidate file")
            })?;
            let content = &bundle.files[&file.path];
            let value: Value = serde_json::from_slice(content)
                .map_err(|error| invalid(format!("evidence file {}", file.path), format!("is JSON: {error}")))?;
            if canonical(&value).as_bytes() != content.as_slice() {
                return Err(invalid(format!("evidence file {}", file.path), "is canonical JSON"));
            }
            Some(file.path.clone())
        }
    };

    let (_, root_state) = logs
        .get(&manifest.proposal_log)
        .ok_or_else(|| invalid("evidence.manifest.proposal_log", "names an episode log"))?;
    let root_start =
        root_state.start.as_ref().ok_or_else(|| invalid("evidence.manifest.proposal_log", "has episode/start"))?;
    if let Some(predecessor) = &record.predecessor_contract_fingerprint {
        if &root_start.contract_fingerprint != predecessor {
            return Err(invalid(
                "adoption_record.predecessor_contract_fingerprint",
                "equals the proposal root's contract fingerprint",
            ));
        }
    }
    verify_provenance(&manifest.proposal_log, &record.verification_log, &logs)?;

    Ok(VerifiedAdoption {
        bundle_address: bundle.address,
        contract_fingerprint: record.contract_fingerprint,
        predecessor_contract_fingerprint: record.predecessor_contract_fingerprint,
        verifier_fingerprint: result.verifier_fingerprint.clone(),
        verification_tool: result.tool.clone(),
        verification_log: record.verification_log,
        verification_seq: record.verification_seq,
        candidate_file,
    })
}

fn read_logs(bundle: &VerifiedBundle) -> Result<BTreeMap<String, (Vec<Event>, State)>, EvidenceError> {
    let mut logs = BTreeMap::new();
    for file in bundle
        .manifest
        .files
        .iter()
        .filter(|file| file.path == "episode.jsonl" || file.path.ends_with("/episode.jsonl"))
    {
        fold::version_check(0, &bundle.files[&file.path])
            .map_err(|error| invalid(format!("evidence log {}", file.path), format!("is valid: {error}")))?;
        let (events, _) = fold::parse_lines(&bundle.files[&file.path])
            .map_err(|error| invalid(format!("evidence log {}", file.path), format!("is valid: {error}")))?;
        let state = fold::fold(&events)
            .map_err(|error| invalid(format!("evidence log {}", file.path), format!("is valid: {error}")))?;
        logs.insert(file.path.clone(), (events, state));
    }
    Ok(logs)
}

fn verify_provenance(root: &str, log: &str, logs: &BTreeMap<String, (Vec<Event>, State)>) -> Result<(), EvidenceError> {
    if log == root {
        return Ok(());
    }
    let key = "adoption_record.verification_log";
    let base = root.strip_suffix("episode.jsonl").unwrap_or_default();
    let Some(rest) = log.strip_prefix(base).and_then(|value| value.strip_suffix("/episode.jsonl")) else {
        return Err(invalid(key, "lies in the proposal episode tree"));
    };
    let parts: Vec<&str> = rest.split('/').collect();
    if !parts.len().is_multiple_of(2) || parts.iter().step_by(2).any(|component| *component != "children") {
        return Err(invalid(key, "descends through children/<id> directories"));
    }
    let mut above = root.to_string();
    let mut prefix = base.to_string();
    for id in parts.iter().skip(1).step_by(2) {
        let (parent_events, parent_state) =
            logs.get(&above).ok_or_else(|| invalid(key, "every log on its spawn path is retained"))?;
        let spawned = parent_events
            .iter()
            .any(|event| matches!(&event.data, EventData::SpawnStart { child_id, .. } if child_id == id));
        if !spawned {
            return Err(invalid(key, format!("{id} is spawned by the log above it")));
        }
        prefix = format!("{prefix}children/{id}/");
        let child_path = format!("{prefix}episode.jsonl");
        let (_, child_state) =
            logs.get(&child_path).ok_or_else(|| invalid(key, "every log on its spawn path is retained"))?;
        let child_start = child_state.start.as_ref().ok_or_else(|| invalid(key, "has episode/start"))?;
        if child_start.parent_id.as_deref() != parent_state.start.as_ref().map(|start| start.id.as_str()) {
            return Err(invalid(key, format!("{id} names the episode above it as its parent")));
        }
        above = child_path;
    }
    Ok(())
}

#[cfg(test)]
#[path = "lib_test.rs"]
mod tests;
