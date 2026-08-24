use super::{
    build_manifest, check_ancestry, digest_of, lineage_identity, manifest_bytes, validate, CandidateEnvelope,
    LineageParent, ProgramLineage, StateDocument, MANIFEST_FILE,
};
use crate::config::{resolve, Program};
use crate::identity::{canonical, compute, sha256_hex, Identity};
use crate::test_util::{config_value, program, program_with, tmp};
use foe_log::append::Writer;
use foe_log::{EpisodeStart, EventData, Outcome, RuntimeInfo, SandboxInfo, SandboxMode, SpawnContext};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

fn runtime() -> RuntimeInfo {
    RuntimeInfo { version: "0.1.0".into(), build: "sha256:test".into() }
}

pub fn digest(fill: char) -> String {
    format!("sha256:{}", fill.to_string().repeat(64))
}

pub fn claim_value() -> Value {
    json!({
        "parent": { "program_identity": digest('a'), "lineage_identity": digest('b') },
        "evidence": digest('c'),
        "verification_log": "children/ep_1/episode.jsonl",
        "verification_seq": 7
    })
}

#[test]
fn identity_omits_the_claim_and_the_resolved_program_records_it() {
    let root = tmp("lineage-omitted");
    let bare = program(&root);
    let mut value = config_value(&root);
    value["program_lineage"] = claim_value();
    let config = serde_json::from_value(value).unwrap();
    let claimed = resolve(&config).unwrap();
    assert!(claimed.program_lineage.is_some());
    assert_eq!(claimed.to_value()["program_lineage"], claim_value());
    assert_eq!(
        compute(&claimed, &[], &runtime()).unwrap().hash,
        compute(&bare, &[], &runtime()).unwrap().hash,
        "the claim does not participate in identity"
    );
}

#[test]
fn the_claim_is_root_only() {
    let root = tmp("lineage-root-only");
    let mut value = config_value(&root);
    value["grants"]["spawn"] = json!(["kid"]);
    value["programs"] = json!({ "kid": {
        "name": "kid", "instructions": { "a": "b" }, "tools": ["block"],
        "grants": { "read": [root] }, "budget": { "model_calls": 1 },
        "program_lineage": claim_value(),
    }});
    let parsed: Result<crate::Config, _> = serde_json::from_value(value);
    assert!(parsed.is_err(), "a nested program does not carry program_lineage");
}

#[test]
fn validation_names_the_key_and_the_rule() {
    let mut claim: ProgramLineage = serde_json::from_value(claim_value()).unwrap();
    claim.evidence = "sha256:short".into();
    let error = validate(&claim).unwrap_err().to_string();
    assert!(error.contains("program_lineage.evidence"), "{error}");
    let mut claim: ProgramLineage = serde_json::from_value(claim_value()).unwrap();
    claim.verification_log = "../episode.jsonl".into();
    let error = validate(&claim).unwrap_err().to_string();
    assert!(error.contains("program_lineage.verification_log"), "{error}");
    let mut claim: ProgramLineage = serde_json::from_value(claim_value()).unwrap();
    claim.verification_log = "/episode.jsonl".into();
    assert!(validate(&claim).is_err(), "an absolute path is refused");
}

#[test]
fn lineage_identity_hashes_the_canonical_object() {
    let program_identity = digest('d');
    let root = lineage_identity(&program_identity, None);
    let text = format!(r#"{{"program_identity":"{program_identity}","program_lineage":null,"schema_version":1}}"#);
    assert_eq!(root, format!("sha256:{}", sha256_hex(text.as_bytes())));
    let claim: ProgramLineage = serde_json::from_value(claim_value()).unwrap();
    let descendant = lineage_identity(&program_identity, Some(&claim));
    assert_ne!(descendant, root, "an ancestry claim changes the lineage identity");
}

#[test]
fn one_program_identity_carries_distinct_claims_distinctly() {
    let program_identity = digest('e');
    let first: ProgramLineage = serde_json::from_value(claim_value()).unwrap();
    let mut second = first.clone();
    second.verification_seq = 8;
    assert_ne!(
        lineage_identity(&program_identity, Some(&first)),
        lineage_identity(&program_identity, Some(&second)),
        "two claims over one program identity have two lineage identities"
    );
}

// ---- ancestry fixtures --------------------------------------------------

/// A parent program declaring a configured verifier `check`, its identity,
/// and the verifier executable's content hash.
struct Fixture {
    root: PathBuf,
    parent_program: Program,
    parent: Identity,
    exec_identity: String,
}

fn verified_config(root: &Path, edit: impl FnOnce(&mut Value)) -> Program {
    let exec = root.join("check.sh");
    program_with(root, |v| {
        v["tools"] = json!(["block", "check"]);
        v["tool_defs"] = json!({ "check": { "exec": exec, "description": "verifies the candidate" } });
        v["done_when"] = json!({ "verify": "check" });
        edit(v);
    })
    .unwrap()
}

fn fixture(name: &str) -> Fixture {
    let root = tmp(name);
    let exec = root.join("check.sh");
    std::fs::write(&exec, "#!/bin/sh\nexit 0\n").unwrap();
    let parent_program = verified_config(&root, |_| {});
    let parent = compute(&parent_program, &[], &runtime()).unwrap();
    let exec_identity = digest_of(&std::fs::read(&exec).unwrap());
    Fixture { root, parent_program, parent, exec_identity }
}

/// A distinct program sharing the fixture's verifier, named by `marker`.
fn child_of(f: &Fixture, marker: &str) -> Identity {
    let program = verified_config(&f.root, |v| v["instructions"]["20-extra"] = json!(marker));
    compute(&program, &[], &runtime()).unwrap()
}

fn start(id: &str, parent: Option<&str>, identity: &str, program: Value) -> EventData {
    EventData::EpisodeStart(EpisodeStart {
        id: id.into(),
        parent_id: parent.map(str::to_string),
        fork_origin: None,
        team_id: None,
        program,
        identity: identity.into(),
        task: "propose a descendant".into(),
        runtime: runtime(),
        sandbox: SandboxInfo { mode: SandboxMode::Off, landlock_abi: 0 },
    })
}

fn accepted(tool: &str, verifier_identity: &str) -> EventData {
    EventData::VerificationResult(foe_log::VerificationResult {
        step: 1,
        tool: tool.into(),
        verifier_identity: verifier_identity.into(),
        status: foe_log::VerificationStatus::Accepted,
        findings: vec![],
        error: None,
        duration_ms: 1,
    })
}

fn ended() -> EventData {
    EventData::EpisodeEnd { outcome: Outcome::Completed { value: json!({}) } }
}

fn write_log(dir: &Path, events: Vec<EventData>) {
    std::fs::create_dir_all(dir).unwrap();
    let mut log = Writer::create(dir, None).unwrap();
    for event in events {
        log.append(event).unwrap();
    }
}

/// Writes the candidate files of a bundle and returns nothing; `seal`
/// completes the bundle with its manifest.
fn write_candidate_files(dir: &Path, child: &Identity) {
    let document = canonical(&child.document);
    std::fs::write(dir.join("child-identity.json"), &document).unwrap();
    let artifacts = canonical(&json!([{ "path": "out.txt", "sha256": digest('9') }]));
    std::fs::write(dir.join("artifact-manifest.json"), &artifacts).unwrap();
    let envelope = CandidateEnvelope {
        program_identity: child.hash.clone(),
        identity_document_sha256: digest_of(document.as_bytes()),
        artifact_manifest_sha256: digest_of(artifacts.as_bytes()),
    };
    let envelope = canonical(&serde_json::to_value(envelope).unwrap());
    std::fs::write(dir.join("candidate-envelope.json"), envelope).unwrap();
}

fn seal(dir: &Path, log: &str, seq: u64, f: &Fixture) -> ProgramLineage {
    let manifest = build_manifest(dir, log, "candidate-envelope.json").unwrap();
    let bytes = manifest_bytes(&manifest).unwrap();
    std::fs::write(dir.join(MANIFEST_FILE), &bytes).unwrap();
    ProgramLineage {
        parent: LineageParent {
            program_identity: f.parent.hash.clone(),
            lineage_identity: lineage_identity(&f.parent.hash, None),
        },
        evidence: digest_of(&bytes),
        verification_log: log.into(),
        verification_seq: seq,
    }
}

/// One proposal bundle under `dir`: the parent episode's log with an
/// accepted verifier result at seq 1, and the candidate files for `child`.
fn bundle(dir: &Path, f: &Fixture, child: &Identity, tool: &str, verifier_identity: &str) -> ProgramLineage {
    write_log(
        &dir.join("episode"),
        vec![
            start("ep_root", None, &f.parent.hash, f.parent_program.to_value()),
            accepted(tool, verifier_identity),
            ended(),
        ],
    );
    write_candidate_files(dir, child);
    seal(dir, "episode/episode.jsonl", 1, f)
}

fn check(
    state: &StateDocument,
    states: BTreeMap<String, StateDocument>,
    bundles: BTreeMap<String, PathBuf>,
) -> Result<super::AncestryReport, crate::ConfigError> {
    let resolve_state = move |id: &str| states.get(id).cloned().ok_or_else(|| format!("unknown state {id}"));
    let resolve_bundle =
        move |address: &str| bundles.get(address).cloned().ok_or_else(|| format!("unknown bundle {address}"));
    check_ancestry(state, &resolve_state, &resolve_bundle)
}

fn root_state(f: &Fixture) -> (String, StateDocument) {
    let state = StateDocument { identity_document: f.parent.document.clone(), program_lineage: None };
    (lineage_identity(&f.parent.hash, None), state)
}

// ---- the required implementation tests -----------------------------------

#[test]
fn a_valid_root_and_one_valid_descendant() {
    let f = fixture("lineage-valid");
    let child = child_of(&f, "descendant");
    let dir = f.root.join("bundle");
    std::fs::create_dir_all(&dir).unwrap();
    let claim = bundle(&dir, &f, &child, "check", &f.exec_identity);
    let state = StateDocument { identity_document: child.document.clone(), program_lineage: Some(claim.clone()) };
    let (root_id, root) = root_state(&f);
    let report = check(&state, BTreeMap::from([(root_id, root)]), BTreeMap::from([(claim.evidence, dir)])).unwrap();
    assert_eq!(report.chain.len(), 2);
    assert_eq!(report.chain[0].program_identity, child.hash);
    assert_eq!(report.chain[1].program_identity, f.parent.hash);
    assert!(report.unverifiable.iter().any(|n| n.contains("candidate_sha256")), "{:?}", report.unverifiable);
}

#[test]
fn a_missing_or_modified_evidence_file_is_rejected() {
    let f = fixture("lineage-tampered");
    let child = child_of(&f, "descendant");
    let dir = f.root.join("bundle");
    std::fs::create_dir_all(&dir).unwrap();
    let claim = bundle(&dir, &f, &child, "check", &f.exec_identity);
    let state = StateDocument { identity_document: child.document.clone(), program_lineage: Some(claim.clone()) };
    let (root_id, root) = root_state(&f);
    std::fs::write(dir.join("artifact-manifest.json"), "tampered").unwrap();
    let error = check(
        &state,
        BTreeMap::from([(root_id.clone(), root.clone())]),
        BTreeMap::from([(claim.evidence.clone(), dir.clone())]),
    )
    .unwrap_err()
    .to_string();
    assert!(error.contains("artifact-manifest.json"), "{error}");
    std::fs::remove_file(dir.join("artifact-manifest.json")).unwrap();
    let error = check(&state, BTreeMap::from([(root_id, root)]), BTreeMap::from([(claim.evidence, dir)]))
        .unwrap_err()
        .to_string();
    assert!(error.contains("is readable"), "{error}");
}

#[test]
fn a_child_that_differs_from_the_accepted_candidate_is_rejected() {
    let f = fixture("lineage-wrong-child");
    let child = child_of(&f, "proposed");
    let other = child_of(&f, "different");
    let dir = f.root.join("bundle");
    std::fs::create_dir_all(&dir).unwrap();
    let claim = bundle(&dir, &f, &child, "check", &f.exec_identity);
    let state = StateDocument { identity_document: other.document.clone(), program_lineage: Some(claim.clone()) };
    let (root_id, root) = root_state(&f);
    let error = check(&state, BTreeMap::from([(root_id, root)]), BTreeMap::from([(claim.evidence, dir)]))
        .unwrap_err()
        .to_string();
    assert!(error.contains("descendant state's program identity"), "{error}");
}

#[test]
fn a_verifier_absent_from_the_parent_is_rejected() {
    let f = fixture("lineage-foreign-verifier");
    let child = child_of(&f, "descendant");
    let dir = f.root.join("bundle");
    std::fs::create_dir_all(&dir).unwrap();
    let claim = bundle(&dir, &f, &child, "ghost", &f.exec_identity);
    let state = StateDocument { identity_document: child.document.clone(), program_lineage: Some(claim.clone()) };
    let (root_id, root) = root_state(&f);
    let error = check(&state, BTreeMap::from([(root_id, root)]), BTreeMap::from([(claim.evidence, dir)]))
        .unwrap_err()
        .to_string();
    assert!(error.contains("a verifier the episode's program declares"), "{error}");
}

#[test]
fn a_verifier_executable_changed_before_invocation_is_rejected() {
    let f = fixture("lineage-swapped-verifier");
    let child = child_of(&f, "descendant");
    let dir = f.root.join("bundle");
    std::fs::create_dir_all(&dir).unwrap();
    let claim = bundle(&dir, &f, &child, "check", &digest_of(b"a replaced executable"));
    let state = StateDocument { identity_document: child.document.clone(), program_lineage: Some(claim.clone()) };
    let (root_id, root) = root_state(&f);
    let error = check(&state, BTreeMap::from([(root_id, root)]), BTreeMap::from([(claim.evidence, dir)]))
        .unwrap_err()
        .to_string();
    assert!(error.contains("executable hash the parent program declares"), "{error}");
}

#[test]
fn an_ancestry_cycle_is_rejected() {
    let f = fixture("lineage-cycle");
    let dir = f.root.join("bundle");
    std::fs::create_dir_all(&dir).unwrap();
    // The proposal admits the parent program itself, so a state can claim
    // its own program as its parent through a resolver that loops.
    let mut claim = bundle(&dir, &f, &f.parent, "check", &f.exec_identity);
    claim.parent.lineage_identity = digest('f');
    let state = StateDocument { identity_document: f.parent.document.clone(), program_lineage: Some(claim.clone()) };
    let error = check(&state, BTreeMap::from([(digest('f'), state.clone())]), BTreeMap::from([(claim.evidence, dir)]))
        .unwrap_err()
        .to_string();
    assert!(error.contains("cycle"), "{error}");
}

#[test]
fn two_children_of_one_parent_both_verify() {
    let f = fixture("lineage-two-children");
    let (root_id, root) = root_state(&f);
    for marker in ["first", "second"] {
        let child = child_of(&f, marker);
        let dir = f.root.join(format!("bundle-{marker}"));
        std::fs::create_dir_all(&dir).unwrap();
        let claim = bundle(&dir, &f, &child, "check", &f.exec_identity);
        let state = StateDocument { identity_document: child.document.clone(), program_lineage: Some(claim.clone()) };
        let report =
            check(&state, BTreeMap::from([(root_id.clone(), root.clone())]), BTreeMap::from([(claim.evidence, dir)]))
                .unwrap();
        assert_eq!(report.chain.len(), 2, "{marker}");
    }
}

#[test]
fn one_program_identity_with_two_valid_ancestry_claims() {
    let f = fixture("lineage-two-claims");
    let child = child_of(&f, "descendant");
    let (root_id, root) = root_state(&f);
    let mut lineage_ids = Vec::new();
    for marker in ["first", "second"] {
        let dir = f.root.join(format!("bundle-{marker}"));
        std::fs::create_dir_all(&dir).unwrap();
        // A retained note distinguishes the two proposal episodes, so the
        // two bundles have two content addresses.
        std::fs::write(dir.join("note.txt"), marker).unwrap();
        let claim = bundle(&dir, &f, &child, "check", &f.exec_identity);
        let state = StateDocument { identity_document: child.document.clone(), program_lineage: Some(claim.clone()) };
        let report =
            check(&state, BTreeMap::from([(root_id.clone(), root.clone())]), BTreeMap::from([(claim.evidence, dir)]))
                .unwrap();
        assert_eq!(report.chain[0].program_identity, child.hash);
        lineage_ids.push(report.chain[0].lineage_identity.clone());
    }
    assert_ne!(lineage_ids[0], lineage_ids[1], "two claims, two lineage identities");
}

#[test]
fn verification_survives_moving_the_evidence_directory() {
    let f = fixture("lineage-moved");
    let child = child_of(&f, "descendant");
    let built = f.root.join("bundle-built-here");
    std::fs::create_dir_all(&built).unwrap();
    let claim = bundle(&built, &f, &child, "check", &f.exec_identity);
    let moved = f.root.join("bundle-retrieved-elsewhere");
    std::fs::rename(&built, &moved).unwrap();
    let state = StateDocument { identity_document: child.document.clone(), program_lineage: Some(claim.clone()) };
    let (root_id, root) = root_state(&f);
    let report = check(&state, BTreeMap::from([(root_id, root)]), BTreeMap::from([(claim.evidence, moved)])).unwrap();
    assert_eq!(report.chain.len(), 2);
}

#[test]
fn a_verifier_result_in_a_spawned_child_log_is_reached_by_provenance() {
    let root = tmp("lineage-child-verifier");
    let exec = root.join("check.sh");
    std::fs::write(&exec, "#!/bin/sh\nexit 0\n").unwrap();
    let kid = json!({
        "name": "kid", "instructions": { "a": "verify the candidate" },
        "tools": ["block", "check"],
        "tool_defs": { "check": { "exec": exec, "description": "verifies the candidate" } },
        "done_when": { "verify": "check" },
        "grants": { "read": [root] }, "budget": { "model_calls": 4 },
    });
    let parent_program = program_with(&root, |v| {
        v["grants"]["spawn"] = json!(["kid"]);
        v["programs"] = json!({ "kid": kid });
    })
    .unwrap();
    let parent = compute(&parent_program, &[], &runtime()).unwrap();
    let f = Fixture {
        root: root.clone(),
        parent_program: parent_program.clone(),
        parent: parent.clone(),
        exec_identity: digest_of(&std::fs::read(&exec).unwrap()),
    };
    let child = child_of(&f, "descendant");
    let dir = root.join("bundle");
    std::fs::create_dir_all(&dir).unwrap();
    let kid_identity = parent.document["programs"]["kid"].as_str().unwrap().to_string();
    write_log(
        &dir.join("episode"),
        vec![
            start("ep_root", None, &parent.hash, parent_program.to_value()),
            EventData::SpawnStart {
                child_id: "ep_kid".into(),
                program: "kid".into(),
                context: SpawnContext::Fresh,
                call_id: "tc_1".into(),
            },
            EventData::SpawnEnd { child_id: "ep_kid".into(), outcome: Outcome::Completed { value: json!({}) } },
            ended(),
        ],
    );
    write_log(
        &dir.join("episode/children/ep_kid"),
        vec![
            start("ep_kid", Some("ep_root"), &kid_identity, parent_program.programs["kid"].to_value()),
            accepted("check", &f.exec_identity),
            ended(),
        ],
    );
    write_candidate_files(&dir, &child);
    let claim = seal(&dir, "episode/episode.jsonl", 1, &f);
    let claim = ProgramLineage {
        verification_log: "episode/children/ep_kid/episode.jsonl".into(),
        verification_seq: 1,
        ..claim
    };
    let state = StateDocument { identity_document: child.document.clone(), program_lineage: Some(claim.clone()) };
    let (root_id, root_doc) = root_state(&f);
    let report = check(&state, BTreeMap::from([(root_id, root_doc)]), BTreeMap::from([(claim.evidence, dir)])).unwrap();
    assert_eq!(report.chain.len(), 2);
    assert!(
        report.unverifiable.iter().any(|n| n.contains("configured verifier of a child program")),
        "{:?}",
        report.unverifiable
    );
}
