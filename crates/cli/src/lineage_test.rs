use super::{check, rendered};
use foe_lineage::{AncestryReport, ChainEntry};
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
    let page = rendered(&report, false);
    assert!(page.starts_with("chain, state first\n"), "{page}");
    assert!(page.contains(&digest('a')) && page.contains(&format!("program {}", digest('d'))), "{page}");
    assert!(page.contains("root reached after 2 states"), "{page}");
    assert!(page.contains("open checks\n  transition x: no candidate_sha256"), "{page}");
    let json = rendered(&report, true);
    let value: serde_json::Value = serde_json::from_str(&json).unwrap();
    assert_eq!(value["chain"][1]["program_identity"], digest('d'));
    assert_eq!(value["unverifiable"][0], "transition x: no candidate_sha256");
}

#[test]
fn a_missing_state_document_names_its_path() {
    let missing = Path::new("/nonexistent/state.json");
    let error = check(missing, Path::new("/tmp"), Path::new("/tmp"), false).unwrap_err();
    assert!(error.contains("/nonexistent/state.json"), "{error}");
}
