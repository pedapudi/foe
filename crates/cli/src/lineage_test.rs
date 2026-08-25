use super::{rendered, verify};
use foe_config::{LineageParent, ProgramLineage};
use foe_lineage::{AncestryReport, ChainEntry};
use serde_json::json;
use std::path::Path;

fn digest(fill: char) -> String {
    format!("sha256:{}", fill.to_string().repeat(64))
}

#[test]
fn the_page_lists_the_chain_and_the_open_checks() {
    let report = AncestryReport {
        chain: vec![
            ChainEntry { lineage_identity: digest('a'), program_identity: digest('b') },
            ChainEntry { lineage_identity: digest('c'), program_identity: digest('d') },
        ],
        unverifiable: vec!["transition x: no candidate_sha256".into()],
    };
    let page = rendered(&report);
    assert!(page.starts_with("chain, state first\n"), "{page}");
    assert!(page.contains(&digest('a')) && page.contains(&format!("program {}", digest('d'))), "{page}");
    assert!(page.contains("root reached after 2 states"), "{page}");
    assert!(page.contains("open checks\n  transition x: no candidate_sha256"), "{page}");
    let value = serde_json::to_value(&report).unwrap();
    assert_eq!(value["chain"][1]["program_identity"], digest('d'));
    assert_eq!(value["unverifiable"][0], "transition x: no candidate_sha256");
}

#[test]
fn an_unresolvable_parent_state_names_the_path_it_was_sought_at() {
    let claim = ProgramLineage {
        parent: LineageParent { program_identity: digest('a'), lineage_identity: digest('b') },
        evidence: digest('c'),
        verification_log: "episode.jsonl".into(),
        verification_seq: 0,
    };
    let states = Path::new("/nonexistent/states");
    let error = verify(&json!({ "name": "p" }), Some(claim), states, Path::new("/tmp")).unwrap_err();
    assert!(error.contains("/nonexistent/states"), "{error}");
}
