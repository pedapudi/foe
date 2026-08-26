//! Evidence about how program states relate. Implements
//! docs/lineage-identity.md: the state identity derived from a program
//! identity and its ancestry claim, the evidence bundle that makes a
//! transition verifiable after files move between machines, and the
//! checker that verifies a claim through two resolvers.
//!
//! The claim's shape is the configuration document's business and lives in
//! `foe-program` as [`ProgramLineage`]. This crate consumes both contracts
//! — the document from `foe-program` and the episode record from `foe-log`
//! — and is part of neither: nothing in the runtime depends on it, and
//! everything here is a pure function over values and files already on
//! disk. Nothing runs, no grant is exercised, no log is written.

#![forbid(unsafe_code)]

use foe_log::{fold, Event, EventData, State, VerificationStatus};
use foe_program::identity::{canonical, sha256_hex};
pub use foe_program::{LineageParent, ProgramLineage};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

/// Every check names the key, file, or event it judged and the rule it
/// applied.
#[derive(Debug, thiserror::Error)]
pub enum LineageError {
    #[error("{key}: {rule}")]
    Invalid { key: String, rule: String },
    #[error("{0}")]
    Parse(#[from] serde_json::Error),
    #[error("{0}")]
    Io(#[from] std::io::Error),
}

/// A configured verifier whose executable bytes are retained outside the
/// parent identity document. The caller establishes the byte digest before
/// asking proposal validation to close the child-program authorization check.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetainedVerifier {
    pub tool: String,
    pub executable_sha256: String,
}

/// Source-capture evidence that a trusted caller authenticates before the
/// generic proposal check uses runtime-effective child identities. Source
/// adoption retains this value so ancestry checking can repeat that check.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProposalEvidence {
    pub verifier: RetainedVerifier,
    pub effective_children: BTreeMap<String, String>,
}

fn invalid(key: impl Into<String>, rule: impl Into<String>) -> LineageError {
    LineageError::Invalid { key: key.into(), rule: rule.into() }
}

/// `Ok` when `text` is `sha256:` followed by 64 lowercase hex digits.
pub fn require_digest(key: &str, text: &str) -> Result<(), LineageError> {
    let hex = text.strip_prefix("sha256:").unwrap_or("");
    match hex.len() == 64 && hex.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')) {
        true => Ok(()),
        false => Err(invalid(key, "is `sha256:` followed by 64 lowercase hex digits")),
    }
}

/// `Ok` when `path` is in manifest path form: relative, forward slashes,
/// and no empty, `.`, or `..` component. See docs/lineage-identity.md
/// "Evidence bundle".
pub fn require_manifest_path(key: &str, path: &str) -> Result<(), LineageError> {
    match !path.contains('\\') && path.split('/').all(|c| !matches!(c, "" | "." | "..")) {
        true => Ok(()),
        false => Err(invalid(key, "is a relative path with no empty, `.`, or `..` component")),
    }
}

/// Checks the shape rules of an ancestry claim, as the configuration
/// parser does at construction.
pub fn validate(lineage: &ProgramLineage) -> Result<(), LineageError> {
    require_digest("program_lineage.parent.program_identity", &lineage.parent.program_identity)?;
    require_digest("program_lineage.parent.state_identity", &lineage.parent.state_identity)?;
    require_digest("program_lineage.evidence", &lineage.evidence)?;
    require_manifest_path("program_lineage.verification_log", &lineage.verification_log)
}

/// The state identity: a SHA-256 digest over the canonical object
/// docs/lineage-identity.md "Configuration representation" specifies. The
/// claim of a root state is `null`. The identity is derived and appears
/// nowhere inside the object it hashes.
pub fn state_identity(program_identity: &str, lineage: Option<&ProgramLineage>) -> String {
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
    /// The adoption record, listed in `files`.
    pub adoption_record: String,
}

/// Parses and checks a manifest without opening any listed file: canonical
/// serialization, path form, byte order without duplicates, and that the
/// proposal log and the adoption record are listed.
pub fn check_manifest(bytes: &[u8]) -> Result<Manifest, LineageError> {
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
    let named = [("proposal_log", &manifest.proposal_log), ("adoption_record", &manifest.adoption_record)];
    for (name, path) in named {
        if !manifest.files.iter().any(|f| &f.path == path) {
            return Err(invalid(format!("{key}.{name}"), "names a listed file"));
        }
    }
    Ok(manifest)
}

/// Verifies a bundle directory: the manifest parses and is canonical, and
/// every listed file has the recorded length and digest. Returns the
/// manifest and the bundle's content address.
pub fn verify_bundle(dir: &Path) -> Result<(Manifest, String), LineageError> {
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
/// itself, in byte order. The caller writes [`canonical_bytes`] to
/// [`MANIFEST_FILE`]; the digest of those bytes is the bundle's address.
pub fn build_manifest(dir: &Path, proposal_log: &str, adoption_record: &str) -> Result<Manifest, LineageError> {
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
        adoption_record: adoption_record.into(),
    };
    check_manifest(&canonical_bytes(&manifest)?)
}

/// The canonical bytes of a lineage record.
pub fn canonical_bytes(value: &impl Serialize) -> Result<Vec<u8>, LineageError> {
    Ok(canonical(&serde_json::to_value(value)?).into_bytes())
}

/// The adoption record: written into the bundle by its builder, binding
/// the proposed child — by its program identity and the digests of the
/// retained identity document and artifact manifest — to the coordinates
/// of the verification that accepted it. The record closes the
/// exact-input-binding step, and attests the pairing only as strongly as
/// the bundle does, because the frozen `verification/result` event carries
/// no digest of its input. See docs/lineage-identity.md "Exact input
/// binding".
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AdoptionRecord {
    pub schema_version: u32,
    /// The hash of the canonical child identity document.
    pub program_identity: String,
    /// The digests of the retained identity document and artifact
    /// manifest, as files of the bundle.
    pub identity_document_sha256: String,
    pub artifact_manifest_sha256: String,
    /// The coordinates of the accepted `verification/result`, as the
    /// ancestry claim names them.
    pub verification_log: String,
    pub verification_seq: u64,
    /// Runtime-effective workflow identities and their retained verifier.
    /// Source adoption records them after authenticating the parent plan.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub proposal_evidence: Option<ProposalEvidence>,
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

/// One state of a verified chain.
#[derive(Debug, Clone, Serialize)]
pub struct ChainEntry {
    pub state_identity: String,
    pub program_identity: String,
}

/// What [`check_ancestry`] establishes: the chain from the requested state
/// to its root, first entry the requested state, and every check the
/// retained evidence leaves open. Open checks exist because the identity
/// document reduces a child program to its hash, so a configured verifier
/// run by a child program is not comparable with a declared executable
/// hash; see docs/lineage-identity.md "Exact input binding".
#[derive(Debug, Clone, Serialize)]
pub struct AncestryReport {
    pub chain: Vec<ChainEntry>,
    pub unverifiable: Vec<String>,
}

/// Verifies an ancestry claim per docs/lineage-identity.md "Verifying an
/// ancestry claim", repeating toward the root and rejecting a repeated
/// state identity as a cycle. A check the retained evidence cannot decide
/// is reported in `unverifiable` rather than failed.
pub fn check_ancestry(
    state: &StateDocument,
    states: &dyn Fn(&str) -> Result<StateDocument, String>,
    evidence: &dyn Fn(&str) -> Result<PathBuf, String>,
) -> Result<AncestryReport, LineageError> {
    let mut current = state.clone();
    let mut seen = BTreeSet::new();
    let mut report = AncestryReport { chain: Vec::new(), unverifiable: Vec::new() };
    loop {
        let program_identity = digest_of(canonical(&current.identity_document).as_bytes());
        let state = state_identity(&program_identity, current.program_lineage.as_ref());
        if !seen.insert(state.clone()) {
            return Err(invalid("ancestry", format!("{state} repeats: the claim chain is a cycle")));
        }
        report.chain.push(ChainEntry { state_identity: state, program_identity: program_identity.clone() });
        let Some(claim) = current.program_lineage.clone() else { return Ok(report) };
        validate(&claim)?;
        let parent = states(&claim.parent.state_identity)
            .map_err(|e| invalid("program_lineage.parent.state_identity", format!("resolves to a state: {e}")))?;
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
/// episode log, the accepted verifier result, the adoption record, the
/// descent of the proposal tree from the named parent, and the verifier's
/// place in the parent program.
fn check_transition(
    claim: &ProgramLineage,
    child_identity: &str,
    parent_document: &Value,
    evidence: &dyn Fn(&str) -> Result<PathBuf, String>,
    report: &mut AncestryReport,
) -> Result<(), LineageError> {
    let dir = evidence(&claim.evidence)
        .map_err(|e| invalid("program_lineage.evidence", format!("resolves to a bundle: {e}")))?;
    let (manifest, address) = verify_bundle(&dir)?;
    if address != claim.evidence {
        return Err(invalid("program_lineage.evidence", "equals the digest of the bundle's canonical manifest"));
    }
    // Exact input binding: the adoption record, written by the bundle
    // builder, pairs the candidate's identity members with the accepted
    // event's coordinates. The frozen event carries no digest of its
    // input, so the pairing is attested as strongly as the bundle itself.
    let bytes = std::fs::read(dir.join(&manifest.adoption_record))?;
    let record: AdoptionRecord = serde_json::from_slice(&bytes)?;
    if canonical_bytes(&record)? != bytes {
        return Err(invalid("adoption_record", "is the canonical serialization of its object"));
    }
    if record.schema_version != 1 + u32::from(record.proposal_evidence.is_some()) {
        return Err(invalid("adoption_record.schema_version", "matches the presence of proposal_evidence"));
    }
    if record.verification_log != claim.verification_log || record.verification_seq != claim.verification_seq {
        return Err(invalid("adoption_record", "names the verification the ancestry claim names"));
    }
    // The record names the retained child identity document and artifact
    // manifest by digest, and the child by its recomputed identity.
    let listed = |digest: &str| manifest.files.iter().find(|f| f.sha256 == digest);
    let identity_file = listed(&record.identity_document_sha256)
        .ok_or_else(|| invalid("adoption_record.identity_document_sha256", "equals the digest of a retained file"))?;
    if listed(&record.artifact_manifest_sha256).is_none() {
        return Err(invalid("adoption_record.artifact_manifest_sha256", "equals the digest of a retained file"));
    }
    if let Some(proposal) = &record.proposal_evidence {
        if listed(&proposal.verifier.executable_sha256).is_none() {
            return Err(invalid(
                "adoption_record.proposal_evidence.verifier.executable_sha256",
                "equals the digest of a retained file",
            ));
        }
    }
    let document: Value = serde_json::from_slice(&std::fs::read(dir.join(&identity_file.path))?)?;
    if digest_of(canonical(&document).as_bytes()) != record.program_identity {
        return Err(invalid(
            "adoption_record.program_identity",
            "equals the hash of the canonical child identity document",
        ));
    }
    if record.program_identity != child_identity {
        return Err(invalid("adoption_record.program_identity", "equals the descendant state's program identity"));
    }
    report.unverifiable.extend(check_proposal(
        &dir,
        manifest.files.iter().map(|file| file.path.as_str()),
        &manifest.proposal_log,
        &claim.verification_log,
        claim.verification_seq,
        parent_document,
        record.proposal_evidence.as_ref(),
    )?);
    Ok(())
}

/// Verifies that an accepted result belongs to the retained proposal tree
/// and was produced by a verifier authorized by the parent program. The
/// caller supplies every retained file name so missing spawn-path logs
/// cannot be recovered from ambient filesystem state. Effective child
/// identities are accepted only through source evidence whose caller has
/// authenticated the runtime transformation for every retained child.
pub fn check_proposal<'a>(
    dir: &Path,
    files: impl Iterator<Item = &'a str>,
    proposal_log: &str,
    verification_log: &str,
    verification_seq: u64,
    parent_document: &Value,
    evidence: Option<&ProposalEvidence>,
) -> Result<Vec<String>, LineageError> {
    let mut logs = BTreeMap::new();
    for path in files.filter(|path| *path == "episode.jsonl" || path.ends_with("/episode.jsonl")) {
        let log_dir = dir.join(path);
        let events = fold::read_all(log_dir.parent().expect("a log path has a parent"))
            .map_err(|e| invalid(format!("evidence log {path}"), format!("is readable: {e}")))?;
        let state =
            fold::fold(&events).map_err(|e| invalid(format!("evidence log {path}"), format!("is a valid log: {e}")))?;
        logs.insert(path.to_string(), (events, state));
    }
    let (events, verifier_state) = logs
        .get(verification_log)
        .ok_or_else(|| invalid("program_lineage.verification_log", "names an episode log in the bundle"))?;
    let event = events
        .iter()
        .find(|event| event.seq == verification_seq)
        .ok_or_else(|| invalid("program_lineage.verification_seq", "names an event in verification_log"))?;
    let EventData::VerificationResult(result) = &event.data else {
        return Err(invalid("program_lineage.verification_seq", "names a verification/result event"));
    };
    if result.status != VerificationStatus::Accepted {
        return Err(invalid("program_lineage.verification_seq", "names an accepted verification/result"));
    }
    let (_, root_state) =
        logs.get(proposal_log).ok_or_else(|| invalid("evidence.manifest.proposal_log", "names an episode log"))?;
    let root =
        root_state.start.as_ref().ok_or_else(|| invalid("evidence.manifest.proposal_log", "has episode/start"))?;
    if root.identity != digest_of(canonical(parent_document).as_bytes()) {
        return Err(invalid(
            "program_lineage.parent.program_identity",
            "equals the proposal root log's program identity",
        ));
    }
    verify_provenance(proposal_log, &logs, parent_document, evidence.map(|evidence| &evidence.effective_children))?;
    let start = verifier_state.start.as_ref().expect("a checked log has episode/start");
    let mut report = AncestryReport { chain: Vec::new(), unverifiable: Vec::new() };
    check_verifier(result, start, parent_document, evidence, &mut report)?;
    Ok(report.unverifiable)
}

/// Requires one retained root and one identity-bound spawn edge for every
/// retained child. Each child log names the spawning episode as its parent
/// and team and occupies that episode's `children/<id>` directory.
fn verify_provenance(
    root: &str,
    logs: &BTreeMap<String, (Vec<Event>, State)>,
    parent_document: &Value,
    effective_children: Option<&BTreeMap<String, String>>,
) -> Result<(), LineageError> {
    let key = "program_lineage.verification_log";
    if logs[root].1.start.as_ref().expect("a checked log has episode/start").parent_id.is_some()
        || logs[root].1.start.as_ref().expect("a checked log has episode/start").team_id.is_some()
    {
        return Err(invalid("evidence.manifest.proposal_log", "is the sole retained root episode"));
    }
    let mut children = BTreeMap::new();
    for (path, (_, state)) in logs {
        let start = state.start.as_ref().expect("a checked log has episode/start");
        if children.insert(start.id.as_str(), path.as_str()).is_some() {
            return Err(invalid(key, format!("episode id {} is unique in the retained tree", start.id)));
        }
    }
    children.remove(logs[root].1.start.as_ref().expect("a checked log has episode/start").id.as_str());
    if effective_children.is_some_and(|effective| {
        effective.len() != children.len() || effective.keys().any(|id| !children.contains_key(id.as_str()))
    }) {
        return Err(invalid(key, "effective child identities name every retained child and no other episode"));
    }
    for (parent_path, (events, state)) in logs {
        let parent_id = &state.start.as_ref().expect("a checked log has episode/start").id;
        for (child_id, program) in events.iter().filter_map(|event| match &event.data {
            EventData::SpawnStart { child_id, program, .. } => Some((child_id, program)),
            _ => None,
        }) {
            let child_path = children
                .remove(child_id.as_str())
                .ok_or_else(|| invalid(key, format!("{parent_id} has no unique retained child {child_id}")))?;
            let start = logs[child_path].1.start.as_ref().expect("a checked log has episode/start");
            let expected =
                format!("{}children/{child_id}/episode.jsonl", parent_path.strip_suffix("episode.jsonl").unwrap());
            if *child_path != expected
                || start.parent_id.as_ref() != Some(parent_id)
                || start.team_id.as_ref() != Some(parent_id)
            {
                return Err(invalid(key, format!("{child_path} descends from its recorded parent and team")));
            }
            if *parent_path != root {
                return Err(invalid(key, format!("{child_path} lacks its spawning parent's identity document")));
            }
            let expected = effective_children
                .and_then(|children| children.get(child_id).map(String::as_str))
                .or_else(|| workflow_child_identity(parent_document, program));
            if expected != Some(start.identity.as_str()) {
                return Err(invalid(key, format!("{} has the identity of workflow node {program}", start.id)));
            }
        }
    }
    children
        .is_empty()
        .then_some(())
        .ok_or_else(|| invalid(key, "reaches every retained episode through one spawn edge"))
}

fn workflow_child_identity<'a>(document: &'a Value, program: &str) -> Option<&'a str> {
    if let Some(identity) = document["programs"][program].as_str() {
        return Some(identity);
    }
    program.split('/').try_fold(document, |parent, name| parent["workflow"]["nodes"].get(name))?["model"].as_str()
}

/// The verifier episode runs a program of the parent state's tree, and the
/// identity the event records is the one that program declares. The
/// identity document reduces a child program to its hash. A configured
/// verifier in a child program therefore needs a retained executable
/// binding from the evidence bundle. A child verifier without that exact
/// binding remains an open authorization check.
fn check_verifier(
    result: &foe_log::VerificationResult,
    start: &foe_log::EpisodeStart,
    parent_document: &Value,
    evidence: Option<&ProposalEvidence>,
    report: &mut AncestryReport,
) -> Result<(), LineageError> {
    let parent_identity = digest_of(canonical(parent_document).as_bytes());
    let mut reachable = BTreeSet::new();
    reachable.insert(parent_identity.clone());
    program_hashes(parent_document, &mut reachable);
    reachable.extend(evidence.into_iter().flat_map(|evidence| evidence.effective_children.values()).cloned());
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
    } else if !evidence
        .map(|evidence| &evidence.verifier)
        .is_some_and(|binding| binding.tool == result.tool && binding.executable_sha256 == result.verifier_identity)
    {
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
#[path = "lib_test.rs"]
mod tests;
