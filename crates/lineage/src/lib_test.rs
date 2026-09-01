use super::{
    build_manifest, check_ancestry, digest_of, manifest_bytes, record_bytes, state_identity, validate, AdoptionRecord,
    AncestryReport, LineageParent, ProgramLineage, StateDocument, MANIFEST_FILE,
};
use foe_log::append::Writer;
use foe_log::{EpisodeStart, EventData, Outcome, RuntimeInfo, SandboxInfo, SandboxMode, SpawnContext};
use foe_program::document::{resolve, ResolvedProgram};
use foe_program::identity::{canonical, compute, sha256_hex, Identity};
use foe_program::ProgramDocument;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

// ---- fixtures -----------------------------------------------------------

fn runtime() -> RuntimeInfo {
    RuntimeInfo { version: "0.1.0".into(), build: "sha256:test".into() }
}

fn tmp(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("foe-lineage-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// A valid root document granting read and write on `root`, with `block`
/// as its only tool and the host transport.
fn config_value(root: &Path) -> Value {
    json!({
        "version": 3,
        "name": "fixture",
        "instructions": { "10-role": "You are a test agent.", "05-first": "Be brief." },
        "tools": ["block"],
        "grants": { "read": [root], "write": [root] },
        "budget": { "model_calls": 10 },
        "task": "do the thing"
    })
}

fn program_with(root: &Path, edit: impl FnOnce(&mut Value)) -> ResolvedProgram {
    let mut value = config_value(root);
    edit(&mut value);
    let config: ProgramDocument = serde_json::from_value(value).unwrap();
    resolve(&config).unwrap()
}

pub fn digest(fill: char) -> String {
    format!("sha256:{}", fill.to_string().repeat(64))
}

pub fn claim_value() -> Value {
    json!({
        "parent": { "program_identity": digest('a'), "state_identity": digest('b') },
        "evidence": digest('c'),
        "verification_log": "children/ep_1/episode.jsonl",
        "verification_seq": 7
    })
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
fn state_identity_hashes_the_canonical_object() {
    let program_identity = digest('d');
    let root = state_identity(&program_identity, None);
    let text = format!(r#"{{"program_identity":"{program_identity}","program_lineage":null,"schema_version":1}}"#);
    assert_eq!(root, format!("sha256:{}", sha256_hex(text.as_bytes())));
    let claim: ProgramLineage = serde_json::from_value(claim_value()).unwrap();
    let descendant = state_identity(&program_identity, Some(&claim));
    assert_ne!(descendant, root, "an ancestry claim changes the state identity");
}

#[test]
fn one_program_identity_carries_distinct_claims_distinctly() {
    let program_identity = digest('e');
    let first: ProgramLineage = serde_json::from_value(claim_value()).unwrap();
    let mut second = first.clone();
    second.verification_seq = 8;
    assert_ne!(
        state_identity(&program_identity, Some(&first)),
        state_identity(&program_identity, Some(&second)),
        "two claims over one program identity have two state identities"
    );
}

/// The runtime never reads lineage, so this crate's complexity cannot
/// reach the machine: no crate except the command line may depend on
/// `foe-lineage`.
#[test]
fn only_the_command_line_depends_on_this_crate() {
    let crates = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    for entry in std::fs::read_dir(crates).unwrap() {
        let dir = entry.unwrap().path();
        let manifest = dir.join("Cargo.toml");
        if !manifest.is_file() {
            continue;
        }
        let name = dir.file_name().unwrap().to_string_lossy().into_owned();
        let text = std::fs::read_to_string(&manifest).unwrap();
        let depends = text.lines().any(|line| line.trim_start().starts_with("foe-lineage"));
        assert!(!depends || name == "cli", "crates/{name} must not depend on foe-lineage");
    }
}

// ---- ancestry fixtures --------------------------------------------------

/// A parent program declaring a configured verifier `check`, its identity,
/// and the verifier executable's content hash.
struct Fixture {
    root: PathBuf,
    parent_program: ResolvedProgram,
    parent: Identity,
    exec_identity: String,
}

fn verified_config(root: &Path, edit: impl FnOnce(&mut Value)) -> ResolvedProgram {
    let exec = root.join("check.sh");
    program_with(root, |v| {
        v["tools"] = json!(["block", "check"]);
        v["tool_defs"] = json!({ "check": { "exec": exec, "description": "verifies the candidate" } });
        v["done_when"] = json!({ "verify": "check" });
        edit(v);
    })
}

fn fixture(name: &str) -> Fixture {
    let root = tmp(name);
    let exec = root.join("check.sh");
    std::fs::write(&exec, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&exec, std::fs::Permissions::from_mode(0o700)).unwrap();
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
        sandbox: SandboxInfo { mode: SandboxMode::Off, landlock_abi: 0, effective_access: None },
        effective_budget: None,
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

/// Writes the child identity document and artifact manifest and returns
/// the adoption record binding them to the verification at `log`/`seq`.
fn write_candidate_files(dir: &Path, child: &Identity, log: &str, seq: u64) -> AdoptionRecord {
    let document = canonical(&child.document);
    std::fs::write(dir.join("child-identity.json"), &document).unwrap();
    let artifacts = canonical(&json!([{ "path": "out.txt", "sha256": digest('9') }]));
    std::fs::write(dir.join("artifact-manifest.json"), &artifacts).unwrap();
    AdoptionRecord {
        schema_version: 1,
        program_identity: child.hash.clone(),
        identity_document_sha256: digest_of(document.as_bytes()),
        artifact_manifest_sha256: digest_of(artifacts.as_bytes()),
        verification_log: log.into(),
        verification_seq: seq,
    }
}

/// Completes the bundle with the candidate files, the adoption record for
/// the verification at `log`/`seq`, and the canonical manifest, and
/// returns the ancestry claim naming that verification.
fn seal(dir: &Path, proposal: &str, log: &str, seq: u64, f: &Fixture, child: &Identity) -> ProgramLineage {
    let record = write_candidate_files(dir, child, log, seq);
    std::fs::write(dir.join("adoption-record.json"), record_bytes(&record).unwrap()).unwrap();
    let manifest = build_manifest(dir, proposal, "adoption-record.json").unwrap();
    seal_manifest(dir, manifest, log, seq, f)
}

fn seal_manifest(dir: &Path, manifest: super::Manifest, log: &str, seq: u64, f: &Fixture) -> ProgramLineage {
    let bytes = manifest_bytes(&manifest).unwrap();
    std::fs::write(dir.join(MANIFEST_FILE), &bytes).unwrap();
    ProgramLineage {
        parent: LineageParent {
            program_identity: f.parent.hash.clone(),
            state_identity: state_identity(&f.parent.hash, None),
        },
        evidence: digest_of(&bytes),
        verification_log: log.into(),
        verification_seq: seq,
    }
}

/// One proposal bundle under `dir`: the parent episode's log with an
/// accepted verifier result at seq 1, the candidate files for `child`, and
/// the adoption record.
fn bundle(dir: &Path, f: &Fixture, child: &Identity, tool: &str, verifier_identity: &str) -> ProgramLineage {
    write_log(
        &dir.join("episode"),
        vec![
            start("ep_root", None, &f.parent.hash, f.parent_program.to_value()),
            accepted(tool, verifier_identity),
            ended(),
        ],
    );
    seal(dir, "episode/episode.jsonl", "episode/episode.jsonl", 1, f, child)
}

fn check(
    state: &StateDocument,
    states: BTreeMap<String, StateDocument>,
    bundles: BTreeMap<String, PathBuf>,
) -> Result<AncestryReport, super::LineageError> {
    let resolve_state = move |id: &str| states.get(id).cloned().ok_or_else(|| format!("unknown state {id}"));
    let resolve_bundle =
        move |address: &str| bundles.get(address).cloned().ok_or_else(|| format!("unknown bundle {address}"));
    check_ancestry(state, &resolve_state, &resolve_bundle)
}

fn root_state(f: &Fixture) -> (String, StateDocument) {
    let state = StateDocument { identity_document: f.parent.document.clone(), program_lineage: None };
    (state_identity(&f.parent.hash, None), state)
}

// ---- the required implementation tests -----------------------------------

#[test]
fn a_valid_root_and_one_valid_descendant() {
    let f = fixture("valid");
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
    // The adoption record closes exact input binding, and the configured
    // verifier runs in the parent program itself, so every step holds.
    assert!(report.unverifiable.is_empty(), "{:?}", report.unverifiable);
}

#[test]
fn an_adoption_record_that_contradicts_the_claim_or_the_candidate_is_rejected() {
    let f = fixture("wrong-adoption-record");
    let child = child_of(&f, "descendant");
    let (root_id, root) = root_state(&f);
    for (marker, edit) in [
        ("coordinates", (|record: &mut AdoptionRecord| record.verification_seq = 2) as fn(&mut AdoptionRecord)),
        ("digest", |record| record.identity_document_sha256 = digest_of(b"another candidate")),
    ] {
        let dir = f.root.join(format!("bundle-{marker}"));
        std::fs::create_dir_all(&dir).unwrap();
        write_log(
            &dir.join("episode"),
            vec![
                start("ep_root", None, &f.parent.hash, f.parent_program.to_value()),
                accepted("check", &f.exec_identity),
                ended(),
            ],
        );
        let mut record = write_candidate_files(&dir, &child, "episode/episode.jsonl", 1);
        edit(&mut record);
        std::fs::write(dir.join("adoption-record.json"), record_bytes(&record).unwrap()).unwrap();
        let manifest = build_manifest(&dir, "episode/episode.jsonl", "adoption-record.json").unwrap();
        let claim = seal_manifest(&dir, manifest, "episode/episode.jsonl", 1, &f);
        let state = StateDocument { identity_document: child.document.clone(), program_lineage: Some(claim.clone()) };
        let error =
            check(&state, BTreeMap::from([(root_id.clone(), root.clone())]), BTreeMap::from([(claim.evidence, dir)]))
                .unwrap_err()
                .to_string();
        assert!(error.contains("adoption_record"), "{marker}: {error}");
    }
}

#[test]
fn a_missing_or_modified_evidence_file_is_rejected() {
    let f = fixture("tampered");
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
    let f = fixture("wrong-child");
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
    let f = fixture("foreign-verifier");
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
    let f = fixture("swapped-verifier");
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
    let f = fixture("cycle");
    let dir = f.root.join("bundle");
    std::fs::create_dir_all(&dir).unwrap();
    // The proposal admits the parent program itself, so a state can claim
    // its own program as its parent through a resolver that loops.
    let mut claim = bundle(&dir, &f, &f.parent, "check", &f.exec_identity);
    claim.parent.state_identity = digest('f');
    let state = StateDocument { identity_document: f.parent.document.clone(), program_lineage: Some(claim.clone()) };
    let error = check(&state, BTreeMap::from([(digest('f'), state.clone())]), BTreeMap::from([(claim.evidence, dir)]))
        .unwrap_err()
        .to_string();
    assert!(error.contains("cycle"), "{error}");
}

#[test]
fn two_children_of_one_parent_both_verify() {
    let f = fixture("two-children");
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
    let f = fixture("two-claims");
    let child = child_of(&f, "descendant");
    let (root_id, root) = root_state(&f);
    let mut state_ids = Vec::new();
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
        state_ids.push(report.chain[0].state_identity.clone());
    }
    assert_ne!(state_ids[0], state_ids[1], "two claims, two state identities");
}

#[test]
fn verification_survives_moving_the_evidence_directory() {
    let f = fixture("moved");
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
    let root = tmp("child-verifier");
    let exec = root.join("check.sh");
    std::fs::write(&exec, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&exec, std::fs::Permissions::from_mode(0o700)).unwrap();
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
    });
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
    let claim = seal(&dir, "episode/episode.jsonl", "episode/children/ep_kid/episode.jsonl", 1, &f, &child);
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
