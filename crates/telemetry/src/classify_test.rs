use super::*;
use crate::extract::Evidence;

/// Evidence built from the tokens the extractor would have produced.
fn evidence(extensions: &[&str], heads: &[&str], tools: &[&str]) -> Evidence {
    let mut out = Evidence::default();
    for extension in extensions {
        *out.extensions.entry(extension.to_string()).or_default() += 1;
    }
    for head in heads {
        *out.heads.entry(head.to_string()).or_default() += 1;
    }
    for tool in tools {
        *out.tools.entry(tool.to_string()).or_default() += 1;
    }
    out
}

fn bucket(extensions: &[&str], heads: &[&str], tools: &[&str]) -> String {
    classify(&evidence(extensions, heads, tools)).bucket
}

#[test]
fn debugging() {
    assert_eq!(bucket(&[], &["gdb", "strace"], &[]), "debugging");
}

#[test]
fn testing() {
    assert_eq!(bucket(&[], &["pytest", "cargo test"], &[]), "testing");
}

#[test]
fn refactoring() {
    assert_eq!(bucket(&[], &["rustfmt", "cargo clippy", "git mv"], &[]), "refactoring");
}

#[test]
fn data_analysis() {
    assert_eq!(bucket(&["csv", "parquet"], &["jq", "duckdb"], &[]), "data-analysis");
}

#[test]
fn infrastructure() {
    assert_eq!(bucket(&["tf", "yaml"], &["terraform", "kubectl"], &[]), "infrastructure");
}

#[test]
fn documentation() {
    assert_eq!(bucket(&["md", "rst"], &["pandoc"], &[]), "documentation");
}

#[test]
fn build() {
    assert_eq!(bucket(&["toml"], &["cargo build", "make"], &[]), "build");
}

#[test]
fn a_mixed_episode_takes_the_bucket_with_the_most_evidence() {
    // Four testing votes against one build vote and one programming vote.
    let mut mixed = evidence(&["toml"], &["pytest", "cargo test", "tox"], &["edit"]);
    *mixed.heads.get_mut("pytest").unwrap() = 2;
    assert_eq!(mixed.heads.len(), 3);
    let result = classify(&mixed);
    assert_eq!(result.bucket, "testing");
    assert_eq!(result.top_level, "programming");
    assert_eq!(result.counts.get("testing"), Some(&4));
    assert_eq!(result.counts.get("build"), Some(&1));
    // Programming carries its own votes plus everything that rolls into it.
    assert_eq!(result.counts.get("programming"), Some(&6));
}

#[test]
fn an_episode_with_no_matching_evidence_stays_unclassified() {
    let result = classify(&evidence(&["png", "bin"], &["ls", "cat", "echo"], &["read", "grep"]));
    assert_eq!(result.bucket, UNCLASSIFIED);
    assert_eq!(result.top_level, UNCLASSIFIED);
    assert!(result.votes.is_empty());
    assert!(result.counts.is_empty());
}

#[test]
fn a_tie_is_broken_by_declaration_order_and_not_by_arrival_order() {
    // One debugging vote and one testing vote; debugging is declared first.
    assert_eq!(bucket(&[], &["gdb", "pytest"], &[]), "debugging");
    assert_eq!(bucket(&[], &["pytest", "gdb"], &[]), "debugging");
}

#[test]
fn evidence_is_the_whole_explanation_of_the_choice() {
    let result = classify(&evidence(&["rs"], &["cargo test"], &["edit"]));
    let named: Vec<(&str, &str, u32)> = result.votes.iter().map(|v| (v.token.as_str(), v.bucket, v.count)).collect();
    assert_eq!(named, vec![("rs", "programming", 1), ("cargo test", "testing", 1), ("edit", "programming", 1)]);
}

#[test]
fn structure_alone_leaves_an_episode_unclassified() {
    let structural = Evidence { spawns: 3, workflow_nodes: 2, ..Evidence::default() };
    let result = classify(&structural);
    assert_eq!(result.bucket, UNCLASSIFIED);
    assert!(result.votes.is_empty());
}

#[test]
fn generic_source_work_wins_only_when_it_outweighs_every_specific_kind() {
    // Two source extensions and one test run: no specific kind of work
    // dominates, so the answer is the general one.
    let general = classify(&evidence(&["rs", "py"], &["pytest"], &[]));
    assert_eq!(general.bucket, "programming");
    // The multi-label view still credits programming with the test run,
    // because testing rolls up into it.
    assert_eq!(general.counts.get("programming"), Some(&3));
    assert_eq!(general.counts.get("testing"), Some(&1));
    // One more test run and the specific kind is the answer.
    let specific = classify(&evidence(&["rs", "py"], &["pytest", "tox", "jest"], &[]));
    assert_eq!(specific.bucket, "testing");
}

#[test]
fn a_subcategory_wins_a_tie_against_the_category_it_rolls_up_into() {
    let result = classify(&evidence(&["rs"], &["pytest"], &[]));
    assert_eq!(result.bucket, "testing");
    assert_eq!(result.top_level, "programming");
}

#[test]
fn every_subcategory_rolls_up_into_a_declared_top_level_category() {
    for (top, subs) in SUBCATEGORIES {
        assert!(TOP_LEVEL.split('|').any(|t| t == *top), "{subs} roll up into {top}, not a top-level category");
    }
}

#[test]
fn every_rule_names_a_declared_bucket() {
    let declared = |bucket: &str| subcategories().contains(&bucket) || TOP_LEVEL.split('|').any(|top| top == bucket);
    for (bucket, tokens) in EXTENSION_RULES.iter().chain(COMMAND_RULES).chain(TOOL_RULES) {
        assert!(declared(bucket), "{tokens} vote for {bucket}, which is not a bucket");
    }
}

#[test]
fn no_rule_table_holds_the_same_token_twice() {
    for table in [EXTENSION_RULES, COMMAND_RULES, TOOL_RULES] {
        let mut tokens: Vec<&str> = table.iter().flat_map(|(_, tokens)| tokens.split('|')).collect();
        let count = tokens.len();
        tokens.sort_unstable();
        tokens.dedup();
        assert_eq!(tokens.len(), count, "a rule table holds a duplicate token");
    }
}
