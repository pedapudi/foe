//! The structural classifier.
//!
//! Evidence is read from typed log fields alone — file extensions, shell
//! command heads, tool names, the presence of spawning and workflows. No
//! lexicon, no model, and no scan of task text or tool output, so the
//! classifier cannot be steered by anything the model wrote and cannot leak
//! what it read. Its cost is that it sees only the shape of the work.
//!
//! There are no confidence scores. A rule vote is a count of matches, not a
//! calibrated probability, and a number formatted like a probability would
//! be treated as one by everything downstream.

use crate::extract::Evidence;
use std::collections::BTreeMap;

/// Changes whenever a bucket is added, removed, or renamed, or a rule moves
/// evidence from one bucket to another. Consumers group by it; a series that
/// crosses a version boundary is two series.
pub const TAXONOMY_VERSION: &str = "1";

/// Top-level categories, seeded on 2026-08-23 from the task categories
/// OpenRouter ranks model usage by. The list is a best-known seed rather
/// than a fetched one: that page renders its categories in the browser, so
/// it is re-pinned by hand against the live page if and when a topical
/// layer is added. Until then the structural rules below reach only
/// `programming`, `data analysis`, and `technology`, and the topical tail
/// is inert.
pub const TOP_LEVEL: &str =
    "programming|data analysis|technology|science|translation|legal|finance|health|academia|marketing|trivia|roleplay";

/// Every subcategory, grouped under the top-level category it rolls up
/// into. Two groups sit outside programming: work over tabular data is data
/// analysis whatever language it is written in, and provisioning and
/// operating machines is technology rather than programming.
///
/// Reading order is the tie-break order: on an equal count the subcategory
/// named earlier wins, so the ranking is a property of this list and not of
/// the order evidence happened to arrive in.
pub const SUBCATEGORIES: &[(&str, &str)] = &[
    ("programming", "debugging|testing|build|refactoring|documentation"),
    ("data analysis", "data-analysis"),
    ("technology", "infrastructure"),
];

/// The label used when no rule matched anything in the episode.
pub const UNCLASSIFIED: &str = "unclassified";

/// Identifies the rules below. Bumped when a rule's matching set changes
/// without the taxonomy changing, so that a shift in counts can be
/// attributed to the rules rather than to the population.
pub const RULESET_VERSION: &str = "1";

/// File extension seen in a `read` or `edit` path or a `grep` glob.
///
/// Each entry pairs a bucket with the tokens that vote for it, separated by
/// `|` because a command token may itself contain a space. A bucket may
/// appear in more than one entry; the first entry holding a token decides.
const EXTENSION_RULES: &[(&str, &str)] = &[
    ("programming", "c|cc|cpp|cs|go|h|hpp|java|js|jsx|kt|php|py|rb|rs|scala|swift|ts|tsx"),
    ("documentation", "adoc|md|rst|txt"),
    ("data-analysis", "arrow|csv|ipynb|parquet|sql|tsv|xlsx"),
    ("infrastructure", "conf|dockerfile|ini|nix|service|tf|tfvars|yaml|yml"),
    ("build", "bazel|bzl|cmake|gradle|mk|toml"),
];

/// Head of a shell command segment, or head and subcommand for a
/// dispatcher whose subcommand carries the meaning, grouped by bucket.
const COMMAND_RULES: &[(&str, &str)] = &[
    ("debugging", "dmesg|gdb|journalctl|lldb|ltrace|perf|strace|valgrind"),
    ("testing", "bazel test|cargo test|ctest|dotnet test|go test|gradle test|jest|mocha|mvn test|npm test|phpunit"),
    ("testing", "pytest|rspec|tox|vitest"),
    ("build", "bazel build|cargo build|cargo check|cmake|go build|go run|gradle|gradle assemble|gradle build|make"),
    ("build", "meson|mvn|mvn compile|mvn install|mvn package|ninja|npm ci|npm install|npm run|pip|tsc|webpack"),
    ("refactoring", "black|cargo clippy|cargo fmt|eslint|git mv|gofmt|prettier|ruff|rustfmt"),
    ("documentation", "doxygen|mkdocs|pandoc|sphinx-build"),
    ("data-analysis", "awk|csvsql|datamash|duckdb|jq|mysql|psql|rscript|sqlite3"),
    ("infrastructure", "ansible|apt|apt-get|brew|dnf|docker|docker-compose|helm|iptables|kubectl|mount|nginx|rsync"),
    ("infrastructure", "ssh|systemctl|terraform|yum"),
];

/// Tool the episode called. Only tools that change source vote: reading
/// and searching happen in every kind of episode and separate none of them.
const TOOL_RULES: &[(&str, &str)] = &[("programming", "edit|write")];

/// Delegation and workflow structure vote for infrastructure: an episode
/// that spawns children or runs a graph is orchestrating work rather than
/// doing one piece of it.
const STRUCTURE_BUCKET: &str = "infrastructure";

/// One matched rule: the evidence token, the bucket it voted for, and how
/// many times it was seen.
pub struct Vote {
    pub token: String,
    pub bucket: &'static str,
    pub count: u32,
}

/// What the classifier concluded about one episode.
pub struct Classification {
    /// The top bucket, or [`UNCLASSIFIED`] when nothing matched.
    pub bucket: String,
    /// The top-level category the bucket rolls up into, or [`UNCLASSIFIED`].
    pub top_level: String,
    /// Counts by bucket, subcategories and their roll-ups together.
    pub counts: BTreeMap<String, u32>,
    /// Every matched rule, which is the whole explanation of the choice.
    pub votes: Vec<Vote>,
}

/// The top-level category a subcategory rolls up into, or the label itself
/// when it is already top level.
fn roll_up(bucket: &str) -> &str {
    let holds = |(_, subs): &&(&str, &str)| subs.split('|').any(|sub| sub == bucket);
    SUBCATEGORIES.iter().find(holds).map(|(top, _)| *top).unwrap_or(bucket)
}

/// Every subcategory, in reading order. That order is the tie-break order.
pub fn subcategories() -> Vec<&'static str> {
    SUBCATEGORIES.iter().flat_map(|(_, subs)| subs.split('|')).collect()
}

/// Rank of a bucket for tie-breaking: subcategories in declaration order
/// first, then top-level categories in theirs.
fn rank(bucket: &str) -> usize {
    let subs = subcategories();
    let top = TOP_LEVEL.split('|').position(|name| name == bucket).map(|index| subs.len() + index);
    subs.iter().position(|sub| *sub == bucket).or(top).unwrap_or(usize::MAX / 2)
}

pub fn classify(evidence: &Evidence) -> Classification {
    let mut votes = Vec::new();
    let mut vote = |table: &'static [(&'static str, &'static str)], token: &str, count: u32| {
        if let Some((bucket, _)) = table.iter().find(|(_, tokens)| tokens.split('|').any(|t| t == token)) {
            votes.push(Vote { token: token.to_string(), bucket, count });
        }
    };
    let histograms =
        [(EXTENSION_RULES, &evidence.extensions), (COMMAND_RULES, &evidence.heads), (TOOL_RULES, &evidence.tools)];
    for (table, counted) in histograms {
        counted.iter().for_each(|(token, count)| vote(table, token, *count));
    }
    for (token, count) in [("spawn", evidence.spawns), ("workflow", evidence.workflow_nodes)] {
        if count > 0 {
            votes.push(Vote { token: token.into(), bucket: STRUCTURE_BUCKET, count });
        }
    }

    // Two views of the same votes. `direct` counts each bucket's own votes
    // and decides the top bucket; `counts` adds every subcategory's votes to
    // the category it rolls up into and is the multi-label view.
    //
    // The top bucket is chosen on direct votes because the two levels are
    // not comparable once roll-ups are added: a top-level category would
    // then hold the sum of its children and no subcategory could ever win.
    // On direct votes the levels are peers — `programming` means source
    // work with no more specific signal, and it wins only when the generic
    // evidence outweighs every specific kind.
    let mut direct: BTreeMap<&str, u32> = BTreeMap::new();
    let mut counts: BTreeMap<String, u32> = BTreeMap::new();
    for cast in &votes {
        *direct.entry(cast.bucket).or_default() += cast.count;
        *counts.entry(cast.bucket.to_string()).or_default() += cast.count;
        let top = roll_up(cast.bucket);
        if top != cast.bucket {
            *counts.entry(top.to_string()).or_default() += cast.count;
        }
    }
    // On an equal count the more specific label wins, subcategories being
    // ranked before top-level categories.
    let bucket = direct
        .iter()
        .max_by_key(|(label, count)| (**count, std::cmp::Reverse(rank(label))))
        .map(|(label, _)| label.to_string())
        .unwrap_or_else(|| UNCLASSIFIED.to_string());
    let top_level = roll_up(&bucket).to_string();
    Classification { bucket, top_level, counts, votes }
}

#[cfg(test)]
#[path = "classify_test.rs"]
mod tests;
