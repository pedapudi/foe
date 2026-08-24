//! Program lineage: the ancestry claim a root configuration carries, the
//! identity derived from it, the evidence bundle that makes a transition
//! verifiable, and the checker that verifies a claim. Implements
//! docs/lineage-identity.md.
//!
//! A resolved program's identity names its content. The lineage identity
//! names that content together with one ancestry claim: which parent state
//! this one descends from, and which content-addressed evidence bundle
//! records the transition. Everything here is a pure function over values
//! and files already on disk: nothing runs, no grant is exercised, no log
//! is written.

use crate::identity::{canonical, sha256_hex};
use crate::ConfigError;
use foe_log::{fold, Event, EventData, State, VerificationStatus};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

/// The immediate predecessor named by an ancestry claim.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LineageParent {
    /// The parent state's program identity.
    pub program_identity: String,
    /// The parent state's own ancestry claim, selecting one among the
    /// claims that can accompany a single program identity.
    pub lineage_identity: String,
}

/// The `program_lineage` object of a root configuration. The identity
/// computation omits it; the resolved program records it, so the claim
/// reaches `episode/start.program`. See docs/config.md `program_lineage`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProgramLineage {
    pub parent: LineageParent,
    /// Content address of the proposal episode's evidence bundle: the
    /// SHA-256 digest of the bundle's canonical manifest.
    pub evidence: String,
    /// Path of the episode log holding the authoritative verifier result,
    /// relative to the bundle root, in manifest path form.
    pub verification_log: String,
    /// `seq` of that `verification/result` event inside `verification_log`.
    pub verification_seq: u64,
}

fn invalid(key: impl Into<String>, rule: impl Into<String>) -> ConfigError {
    ConfigError::Invalid { key: key.into(), rule: rule.into() }
}

/// `Ok` when `text` is `sha256:` followed by 64 lowercase hex digits.
pub fn require_digest(key: &str, text: &str) -> Result<(), ConfigError> {
    let hex = text.strip_prefix("sha256:").unwrap_or("");
    match hex.len() == 64 && hex.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')) {
        true => Ok(()),
        false => Err(invalid(key, "is `sha256:` followed by 64 lowercase hex digits")),
    }
}

/// `Ok` when `path` is in manifest path form: relative, forward slashes,
/// and no empty, `.`, or `..` component. See docs/lineage-identity.md
/// "Evidence bundle".
pub fn require_manifest_path(key: &str, path: &str) -> Result<(), ConfigError> {
    match !path.contains('\\') && path.split('/').all(|c| !matches!(c, "" | "." | "..")) {
        true => Ok(()),
        false => Err(invalid(key, "is a relative path with no empty, `.`, or `..` component")),
    }
}

/// Checks the shape rules of the `program_lineage` object.
pub fn validate(lineage: &ProgramLineage) -> Result<(), ConfigError> {
    require_digest("program_lineage.parent.program_identity", &lineage.parent.program_identity)?;
    require_digest("program_lineage.parent.lineage_identity", &lineage.parent.lineage_identity)?;
    require_digest("program_lineage.evidence", &lineage.evidence)?;
    require_manifest_path("program_lineage.verification_log", &lineage.verification_log)
}

/// The lineage identity of a state: a SHA-256 digest over the canonical
/// object docs/lineage-identity.md "Configuration representation"
/// specifies. The claim of a root state is `null`. The identity is derived
/// and appears nowhere inside the object it hashes.
pub fn lineage_identity(program_identity: &str, lineage: Option<&ProgramLineage>) -> String {
    let document = json!({
        "schema_version": 1,
        "program_identity": program_identity,
        "program_lineage": lineage,
    });
    digest_of(canonical(&document).as_bytes())
}

/// `sha256:<hex>` over `bytes`.
pub fn digest_of(bytes: &[u8]) -> String {
    format!("sha256:{}", sha256_hex(bytes))
}

// ---- evidence bundle --------------------------------------------------------

/// File name of the canonical manifest inside a bundle directory. The
/// bundle's content address is the SHA-256 digest of this file, whose
/// bytes are the canonical serialization of [`Manifest`].
pub const MANIFEST_FILE: &str = "manifest.json";

/// One retained file of an evidence bundle.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManifestFile {
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
}

/// The canonical manifest of an evidence bundle: every retained file by
/// relative path, byte length, and SHA-256 digest. See
/// docs/lineage-identity.md "Evidence bundle".
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub schema_version: u32,
    /// In byte order of `path`, without duplicates.
    pub files: Vec<ManifestFile>,
    /// The proposal episode tree's root log, listed in `files`.
    pub proposal_log: String,
    /// The transition candidate envelope, listed in `files`.
    pub candidate_envelope: String,
}

/// Parses and checks a manifest without opening any listed file: canonical
/// serialization, path form, byte order without duplicates, and that the
/// proposal log and the candidate envelope are listed.
pub fn check_manifest(bytes: &[u8]) -> Result<Manifest, ConfigError> {
    let manifest: Manifest = serde_json::from_slice(bytes)?;
    let key = "evidence.manifest";
    if canonical(&serde_json::to_value(&manifest)?).as_bytes() != bytes {
        return Err(invalid(key, "is the canonical serialization of its object"));
    }
    if manifest.schema_version != 1 {
        return Err(invalid(format!("{key}.schema_version"), "is 1"));
    }
    for (i, file) in manifest.files.iter().enumerate() {
        require_manifest_path(&format!("{key}.files[{i}].path"), &file.path)?;
        require_digest(&format!("{key}.files[{i}].sha256"), &file.sha256)?;
        if i > 0 && manifest.files[i - 1].path >= file.path {
            return Err(invalid(format!("{key}.files[{i}].path"), "follows the prior path in byte order"));
        }
    }
    for (name, path) in [("proposal_log", &manifest.proposal_log), ("candidate_envelope", &manifest.candidate_envelope)]
    {
        if !manifest.files.iter().any(|f| &f.path == path) {
            return Err(invalid(format!("{key}.{name}"), "names a listed file"));
        }
    }
    Ok(manifest)
}

/// Verifies a bundle directory: the manifest parses and is canonical, and
/// every listed file has the recorded length and digest. Returns the
/// manifest and the bundle's content address.
pub fn verify_bundle(dir: &Path) -> Result<(Manifest, String), ConfigError> {
    let bytes = std::fs::read(dir.join(MANIFEST_FILE))
        .map_err(|e| invalid("evidence.manifest", format!("is readable: {}: {e}", dir.display())))?;
    let manifest = check_manifest(&bytes)?;
    for file in &manifest.files {
        let content = std::fs::read(dir.join(&file.path))
            .map_err(|e| invalid(format!("evidence file {}", file.path), format!("is readable: {e}")))?;
        if content.len() as u64 != file.bytes || digest_of(&content) != file.sha256 {
            return Err(invalid(format!("evidence file {}", file.path), "matches its manifest length and digest"));
        }
    }
    Ok((manifest, digest_of(&bytes)))
}

/// Builds the manifest of `dir`: every file below it except the manifest
/// itself, in byte order. The caller writes [`manifest_bytes`] to
/// [`MANIFEST_FILE`]; the digest of those bytes is the bundle's address.
pub fn build_manifest(dir: &Path, proposal_log: &str, candidate_envelope: &str) -> Result<Manifest, ConfigError> {
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
                    .expect("the walk stays below dir")
                    .components()
                    .map(|c| c.as_os_str().to_string_lossy().into_owned())
                    .collect();
                files.push(ManifestFile {
                    path: parts.join("/"),
                    bytes: content.len() as u64,
                    sha256: digest_of(&content),
                });
            }
        }
    }
    files.sort_by(|a, b| a.path.cmp(&b.path));
    let manifest = Manifest {
        schema_version: 1,
        files,
        proposal_log: proposal_log.into(),
        candidate_envelope: candidate_envelope.into(),
    };
    check_manifest(&manifest_bytes(&manifest)?)
}

/// The canonical bytes of a manifest: what [`MANIFEST_FILE`] holds.
pub fn manifest_bytes(manifest: &Manifest) -> Result<Vec<u8>, ConfigError> {
    Ok(canonical(&serde_json::to_value(manifest)?).into_bytes())
}

/// The transition candidate envelope: the complete verifier input, binding
/// the proposed child by digest to the identity document and the artifact
/// manifest retained in the bundle.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CandidateEnvelope {
    pub program_identity: String,
    pub identity_document_sha256: String,
    pub artifact_manifest_sha256: String,
}

// ---- ancestry ---------------------------------------------------------------

/// A state document: the canonical identity document paired with the
/// optional ancestry claim. What a state resolver returns.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StateDocument {
    pub identity_document: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub program_lineage: Option<ProgramLineage>,
}

/// Retrieves a state document by lineage identity.
pub type StateResolver<'a> = &'a dyn Fn(&str) -> Result<StateDocument, String>;
/// Retrieves an evidence bundle directory by content address.
pub type EvidenceResolver<'a> = &'a dyn Fn(&str) -> Result<PathBuf, String>;

/// One state of a verified chain.
#[derive(Debug, Clone, Serialize)]
pub struct ChainEntry {
    pub lineage_identity: String,
    pub program_identity: String,
}

/// What [`check_ancestry`] establishes: the chain from the requested state
/// to its root, first entry the requested state, and every check the
/// retained evidence leaves open. Open checks exist because the implemented
/// `verification/result` event carries no candidate digest and because the
/// identity document reduces a child program to its hash; see
/// docs/lineage-identity.md "The candidate binding gap".
#[derive(Debug, Clone, Serialize)]
pub struct AncestryReport {
    pub chain: Vec<ChainEntry>,
    pub unverifiable: Vec<String>,
}

/// Verifies an ancestry claim per docs/lineage-identity.md "Verifying an
/// ancestry claim", repeating toward the root and rejecting a repeated
/// lineage identity as a cycle. A check the retained evidence cannot decide
/// is reported in `unverifiable` rather than failed.
pub fn check_ancestry(
    state: &StateDocument,
    states: StateResolver,
    evidence: EvidenceResolver,
) -> Result<AncestryReport, ConfigError> {
    let mut current = state.clone();
    let mut seen = BTreeSet::new();
    let mut report = AncestryReport { chain: Vec::new(), unverifiable: Vec::new() };
    loop {
        let program_identity = digest_of(canonical(&current.identity_document).as_bytes());
        let lineage = lineage_identity(&program_identity, current.program_lineage.as_ref());
        if !seen.insert(lineage.clone()) {
            return Err(invalid("ancestry", format!("{lineage} repeats: the claim chain is a cycle")));
        }
        report.chain.push(ChainEntry { lineage_identity: lineage, program_identity: program_identity.clone() });
        let Some(claim) = current.program_lineage.clone() else { return Ok(report) };
        validate(&claim)?;
        let parent = states(&claim.parent.lineage_identity)
            .map_err(|e| invalid("program_lineage.parent.lineage_identity", format!("resolves to a state: {e}")))?;
        let parent_identity = digest_of(canonical(&parent.identity_document).as_bytes());
        if parent_identity != claim.parent.program_identity {
            return Err(invalid(
                "program_lineage.parent.program_identity",
                "equals the resolved parent state's program identity",
            ));
        }
        check_transition(&claim, &program_identity, &parent.identity_document, evidence, &mut report)?;
        current = parent;
    }
}

/// Verifies one transition: the bundle and its files, every retained
/// episode log, the accepted verifier result, the envelope bindings, the
/// descent of the proposal tree from the named parent, and the verifier's
/// place in the parent program.
fn check_transition(
    claim: &ProgramLineage,
    child_identity: &str,
    parent_document: &Value,
    evidence: EvidenceResolver,
    report: &mut AncestryReport,
) -> Result<(), ConfigError> {
    let dir = evidence(&claim.evidence)
        .map_err(|e| invalid("program_lineage.evidence", format!("resolves to a bundle: {e}")))?;
    let (manifest, address) = verify_bundle(&dir)?;
    if address != claim.evidence {
        return Err(invalid("program_lineage.evidence", "equals the digest of the bundle's canonical manifest"));
    }
    // Every episode log retained in the bundle is a valid log.
    let mut logs = BTreeMap::new();
    for file in manifest.files.iter().filter(|f| f.path == "episode.jsonl" || f.path.ends_with("/episode.jsonl")) {
        let log_dir = dir.join(&file.path);
        let log_dir = log_dir.parent().expect("a log path has a parent");
        let events = fold::read_all(log_dir)
            .map_err(|e| invalid(format!("evidence log {}", file.path), format!("is readable: {e}")))?;
        let state = fold::fold(&events)
            .map_err(|e| invalid(format!("evidence log {}", file.path), format!("is a valid log: {e}")))?;
        logs.insert(file.path.clone(), (events, state));
    }
    // The accepted verifier result the claim names.
    let (events, _) = logs
        .get(&claim.verification_log)
        .ok_or_else(|| invalid("program_lineage.verification_log", "names an episode log in the bundle"))?;
    let event = events
        .iter()
        .find(|e| e.seq == claim.verification_seq)
        .ok_or_else(|| invalid("program_lineage.verification_seq", "names an event in verification_log"))?;
    let EventData::VerificationResult(result) = &event.data else {
        return Err(invalid("program_lineage.verification_seq", "names a verification/result event"));
    };
    if result.status != VerificationStatus::Accepted {
        return Err(invalid("program_lineage.verification_seq", "names an accepted verification/result"));
    }
    // The implemented event carries no candidate digest, so the equality of
    // the event's candidate and the retained envelope is not checkable.
    report.unverifiable.push(format!(
        "transition {}: verification/result at seq {} carries no candidate_sha256, so the retained \
         envelope is not bound to the verifier's input",
        claim.evidence, claim.verification_seq
    ));
    // The envelope names the retained child identity document and artifact
    // manifest by digest, and the child by its recomputed identity.
    let envelope_bytes = std::fs::read(dir.join(&manifest.candidate_envelope))?;
    let envelope: CandidateEnvelope = serde_json::from_slice(&envelope_bytes)?;
    let listed = |digest: &str| manifest.files.iter().find(|f| f.sha256 == digest);
    let identity_file = listed(&envelope.identity_document_sha256).ok_or_else(|| {
        invalid("candidate_envelope.identity_document_sha256", "equals the digest of a retained file")
    })?;
    if listed(&envelope.artifact_manifest_sha256).is_none() {
        return Err(invalid("candidate_envelope.artifact_manifest_sha256", "equals the digest of a retained file"));
    }
    let document: Value = serde_json::from_slice(&std::fs::read(dir.join(&identity_file.path))?)?;
    if digest_of(canonical(&document).as_bytes()) != envelope.program_identity {
        return Err(invalid(
            "candidate_envelope.program_identity",
            "equals the hash of the canonical child identity document",
        ));
    }
    if envelope.program_identity != child_identity {
        return Err(invalid("candidate_envelope.program_identity", "equals the descendant state's program identity"));
    }
    // The proposal tree descends from the named parent.
    let (_, root_state) = logs
        .get(&manifest.proposal_log)
        .ok_or_else(|| invalid("evidence.manifest.proposal_log", "names an episode log"))?;
    let root_start =
        root_state.start.as_ref().ok_or_else(|| invalid("evidence.manifest.proposal_log", "has episode/start"))?;
    if root_start.identity != claim.parent.program_identity {
        return Err(invalid(
            "program_lineage.parent.program_identity",
            "equals the proposal root log's program identity",
        ));
    }
    verify_provenance(&manifest.proposal_log, &claim.verification_log, &logs)?;
    let (_, verifier_state) = &logs[&claim.verification_log];
    let verifier_start = verifier_state.start.as_ref().expect("a checked log has episode/start");
    check_verifier(result, verifier_start, parent_document, report)
}

/// Requires `log` to be `root` or a descendant reached through recorded
/// spawn provenance: each `children/<id>/episode.jsonl` hop is announced by
/// a `spawn/start` naming `<id>` in the log above it, and the child's
/// `episode/start` names that episode as its parent.
fn verify_provenance(root: &str, log: &str, logs: &BTreeMap<String, (Vec<Event>, State)>) -> Result<(), ConfigError> {
    if log == root {
        return Ok(());
    }
    let key = "program_lineage.verification_log";
    let base = root.strip_suffix("episode.jsonl").unwrap_or_default();
    let Some(rest) = log.strip_prefix(base).and_then(|r| r.strip_suffix("/episode.jsonl")) else {
        return Err(invalid(key, "lies in the proposal episode tree"));
    };
    let parts: Vec<&str> = rest.split('/').collect();
    if !parts.len().is_multiple_of(2) || parts.iter().step_by(2).any(|c| *c != "children") {
        return Err(invalid(key, "descends through children/<id> directories"));
    }
    let mut above = root.to_string();
    let mut prefix = base.to_string();
    for id in parts.iter().skip(1).step_by(2) {
        let (parent_events, parent_state) = &logs[&above];
        let spawned =
            parent_events.iter().any(|e| matches!(&e.data, EventData::SpawnStart { child_id, .. } if child_id == id));
        if !spawned {
            return Err(invalid(key, format!("{id} is spawned by the log above it")));
        }
        prefix = format!("{prefix}children/{id}/");
        let child_path = format!("{prefix}episode.jsonl");
        let (_, child_state) =
            logs.get(&child_path).ok_or_else(|| invalid(key, "every log on its spawn path is retained"))?;
        let child_start = child_state.start.as_ref().ok_or_else(|| invalid(key, "has episode/start"))?;
        if child_start.parent_id.as_deref() != parent_state.start.as_ref().map(|s| s.id.as_str()) {
            return Err(invalid(key, format!("{id} names the episode above it as its parent")));
        }
        above = child_path;
    }
    Ok(())
}

/// The verifier episode runs a program of the parent state's tree, and the
/// identity the event records is the one that program declares. The
/// identity document reduces a child program to its hash, so a configured
/// verifier is checkable against its declared executable hash only when
/// the verifier episode runs the parent program itself; the child case is
/// reported open.
fn check_verifier(
    result: &foe_log::VerificationResult,
    start: &foe_log::EpisodeStart,
    parent_document: &Value,
    report: &mut AncestryReport,
) -> Result<(), ConfigError> {
    let parent_identity = digest_of(canonical(parent_document).as_bytes());
    let mut reachable = BTreeSet::new();
    reachable.insert(parent_identity.clone());
    program_hashes(parent_document, &mut reachable);
    if !reachable.contains(&start.identity) {
        return Err(invalid(
            "verification_log episode/start.identity",
            "is the parent program or a program its identity document reaches",
        ));
    }
    let program = &start.program;
    let mut declared = BTreeSet::new();
    if let Some(name) = program["done_when"]["verify"].as_str() {
        declared.insert(name);
    }
    workflow_verifiers(&program["workflow"], &mut declared);
    if !declared.contains(result.tool.as_str()) {
        return Err(invalid("verification/result.tool", "is a verifier the episode's program declares"));
    }
    if !program["tool_defs"][result.tool.as_str()].is_object() {
        // A built-in or host verifier records the runtime build.
        if result.verifier_identity != start.runtime.build {
            return Err(invalid(
                "verification/result.verifier_identity",
                "equals the runtime build recorded for a built-in verifier",
            ));
        }
        return Ok(());
    }
    if start.identity == parent_identity {
        let tools = parent_document["tools"].as_array().cloned().unwrap_or_default();
        let expected = tools
            .iter()
            .find(|t| t["name"] == result.tool.as_str())
            .and_then(|t| t["exec_sha256"].as_str().map(|h| format!("sha256:{h}")));
        if expected.as_deref() != Some(result.verifier_identity.as_str()) {
            return Err(invalid(
                "verification/result.verifier_identity",
                "equals the executable hash the parent program declares",
            ));
        }
    } else {
        report.unverifiable.push(format!(
            "verifier episode {}: a configured verifier of a child program; the parent identity document \
             reduces the child to its hash, so the declared executable hash is not retained",
            start.id
        ));
    }
    Ok(())
}

/// Every program hash the identity document names: child programs and
/// workflow model nodes, at every depth the document itself carries.
fn program_hashes(document: &Value, out: &mut BTreeSet<String>) {
    if let Some(children) = document["programs"].as_object() {
        out.extend(children.values().filter_map(Value::as_str).map(str::to_string));
    }
    workflow_hashes(&document["workflow"], out);
}

fn workflow_hashes(workflow: &Value, out: &mut BTreeSet<String>) {
    if let Some(nodes) = workflow["nodes"].as_object() {
        for node in nodes.values() {
            if let Some(hash) = node["model"].as_str() {
                out.insert(hash.to_string());
            }
            workflow_hashes(&node["workflow"], out);
        }
    }
}

fn workflow_verifiers<'a>(workflow: &'a Value, out: &mut BTreeSet<&'a str>) {
    if let Some(nodes) = workflow["nodes"].as_object() {
        for node in nodes.values() {
            if let Some(name) = node["verify"].as_str() {
                out.insert(name);
            }
            workflow_verifiers(&node["workflow"], out);
        }
    }
}

#[cfg(test)]
#[path = "lineage_test.rs"]
mod tests;
