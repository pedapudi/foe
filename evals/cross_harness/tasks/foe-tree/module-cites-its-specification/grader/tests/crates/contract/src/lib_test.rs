use super::contains;
use std::path::PathBuf;

#[test]
fn containment_compares_components_so_a_sibling_prefix_is_outside() {
    let roots = vec![PathBuf::from("/src")];
    assert!(contains(&roots, std::path::Path::new("/src/a/b")));
    assert!(contains(&roots, std::path::Path::new("/src")));
    assert!(!contains(&roots, std::path::Path::new("/src-other/a")));
    assert!(!contains(&roots, std::path::Path::new("/")));
}

// ---- specification citations ---------------------------------------------------

/// The documents AGENTS.md "Read first" names, the design and the five
/// specifications, which are the only documents a module may cite.
const SPECIFICATION_DOCUMENTS: [&str; 6] = [
    "docs/compaction.md",
    "docs/config.md",
    "docs/log-format.md",
    "docs/protocol.md",
    "docs/design.md",
    "docs/workflow.md",
];

fn source_directory() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src")
}

fn repository_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// The modules of this crate: every `.rs` file under `src` that is neither
/// the crate root nor a test file.
fn crate_modules() -> Vec<String> {
    let directory = source_directory();
    let entries = std::fs::read_dir(&directory).unwrap_or_else(|e| panic!("{}: {e}", directory.display()));
    let mut names: Vec<String> = entries
        .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
        .filter(|name| name.ends_with(".rs") && name != "lib.rs" && !name.ends_with("_test.rs"))
        .map(|name| name.trim_end_matches(".rs").to_owned())
        .collect();
    names.sort();
    names
}

/// The document and section a module's documentation cites, or None when it
/// cites none. A module states at most one citation.
fn stated_citation(module: &str) -> Option<(String, String)> {
    let path = source_directory().join(format!("{module}.rs"));
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let mut found: Vec<(String, String)> = Vec::new();
    for line in text.lines() {
        let Some(rest) = line.trim().strip_prefix("//! Specification: ") else { continue };
        let cited = rest
            .trim()
            .strip_suffix('.')
            .unwrap_or_else(|| panic!("{}: {line:?} does not end with a period", path.display()));
        let (document, quoted) = cited
            .split_once(' ')
            .unwrap_or_else(|| panic!("{}: {line:?} names a document and no section", path.display()));
        let section = quoted
            .strip_prefix('"')
            .and_then(|rest| rest.strip_suffix('"'))
            .unwrap_or_else(|| panic!("{}: {line:?} does not quote its section", path.display()));
        found.push((document.to_owned(), section.to_owned()));
    }
    assert!(found.len() <= 1, "crates/contract/src/{module}.rs states {} citations; a module states one", found.len());
    found.pop()
}

/// Whether the document holds the section as a heading of any level.
fn heading_stands(document: &str, section: &str) -> bool {
    let path = repository_root().join(document);
    let Ok(text) = std::fs::read_to_string(&path) else { return false };
    text.lines().any(|line| line.starts_with('#') && line.trim_start_matches('#').trim() == section)
}

/// One module states a citation, to a specification document, whose section stands there.
fn cites_a_standing_section(module: &str) {
    let (document, section) = stated_citation(module)
        .unwrap_or_else(|| panic!("crates/contract/src/{module}.rs states no `Specification:` line"));
    assert!(
        SPECIFICATION_DOCUMENTS.contains(&document.as_str()),
        "crates/contract/src/{module}.rs cites {document}, which AGENTS.md \"Read first\" does not name"
    );
    assert!(
        heading_stands(&document, &section),
        "crates/contract/src/{module}.rs cites {document} \"{section}\", and that document holds no such heading"
    );
}

/// AGENTS.md "Read first": the module states the specification it implements,
/// and the section it names stands in that document.
#[test]
fn the_fingerprint_module_cites_a_standing_section() {
    cites_a_standing_section("fingerprint");
}

/// AGENTS.md "Read first": the module states the specification it implements,
/// and the section it names stands in that document.
#[test]
fn the_harness_text_module_cites_a_standing_section() {
    cites_a_standing_section("harness_text");
}

/// AGENTS.md "Read first": the module states the specification it implements,
/// and the section it names stands in that document.
#[test]
fn the_inspect_module_cites_a_standing_section() {
    cites_a_standing_section("inspect");
}

/// AGENTS.md "Read first": the module states the specification it implements,
/// and the section it names stands in that document.
#[test]
fn the_schema_module_cites_a_standing_section() {
    cites_a_standing_section("schema");
}

/// AGENTS.md "Read first": the module states the specification it implements,
/// and the section it names stands in that document.
#[test]
fn the_tools_module_cites_a_standing_section() {
    cites_a_standing_section("tools");
}

/// AGENTS.md "Read first": the module states the specification it implements,
/// and the section it names stands in that document.
#[test]
fn the_workflow_module_cites_a_standing_section() {
    cites_a_standing_section("workflow");
}

/// One table of citations. `SPECIFICATIONS` names every module of the crate,
/// every module that states a citation states the one the table gives it, and
/// every section the table names stands in its document.
#[test]
fn every_module_agrees_with_the_specification_table() {
    let modules = crate_modules();
    let mut listed: Vec<String> = super::SPECIFICATIONS.iter().map(|(name, _, _)| (*name).to_owned()).collect();
    listed.sort();
    assert_eq!(
        listed, modules,
        "SPECIFICATIONS in crates/contract/src/lib.rs names {listed:?}; the crate's modules are {modules:?}"
    );
    for module in &modules {
        let Some((document, section)) = stated_citation(module) else { continue };
        let (given_document, given_section) = super::specification(module)
            .unwrap_or_else(|| panic!("SPECIFICATIONS holds no entry for the module {module}"));
        assert_eq!(
            (document.as_str(), section.as_str()),
            (given_document, given_section),
            "crates/contract/src/{module}.rs cites a specification other than the one SPECIFICATIONS gives it"
        );
    }
    for (module, document, section) in super::SPECIFICATIONS {
        assert!(
            heading_stands(document, section),
            "SPECIFICATIONS gives {module} the section {document} \"{section}\", and that document holds no such heading"
        );
    }
}
