use super::{lineage_identity, validate, ProgramLineage};
use crate::config::resolve;
use crate::identity::{compute, sha256_hex};
use crate::test_util::{config_value, program, tmp};
use foe_log::RuntimeInfo;
use serde_json::json;

fn runtime() -> RuntimeInfo {
    RuntimeInfo { version: "0.1.0".into(), build: "sha256:test".into() }
}

pub fn digest(fill: char) -> String {
    format!("sha256:{}", fill.to_string().repeat(64))
}

pub fn claim_value() -> serde_json::Value {
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
