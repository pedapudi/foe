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

// ---- input bounds ---------------------------------------------------------------

/// The repository root, from this crate's manifest directory.
fn repository_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// The name of every crate directory under `crates/`.
fn crate_names() -> Vec<String> {
    let directory = repository_root().join("crates");
    let entries = std::fs::read_dir(&directory).unwrap_or_else(|e| panic!("{}: {e}", directory.display()));
    let mut names: Vec<String> = entries
        .map(|entry| entry.unwrap())
        .filter(|entry| entry.path().is_dir())
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

/// Every Rust source of one crate that is neither a test file nor the file
/// the table itself stands in. The table names every bound, so that file is
/// the one place every name is expected.
fn enforcing_sources(name: &str) -> Vec<(String, String)> {
    let mut found = Vec::new();
    let mut pending = vec![repository_root().join("crates").join(name).join("src")];
    while let Some(directory) = pending.pop() {
        let Ok(entries) = std::fs::read_dir(&directory) else { continue };
        for entry in entries.map(|entry| entry.unwrap().path()) {
            if entry.is_dir() {
                pending.push(entry);
                continue;
            }
            let relative = entry.strip_prefix(repository_root()).unwrap().to_string_lossy().into_owned();
            let is_test = relative.ends_with("_test.rs") || relative.contains("/tests/");
            if !relative.ends_with(".rs") || is_test || relative == "crates/contract/src/lib.rs" {
                continue;
            }
            found.push((relative, std::fs::read_to_string(&entry).unwrap()));
        }
    }
    found.sort();
    found
}

/// One vocabulary of input bounds. The table names each bound once and in
/// order, a name is the crate that enforces the bound and what the bound
/// covers, and the only bound name a crate states is one of its own, so the
/// name in a refusal locates the rule that produced it.
#[test]
fn every_bound_a_crate_names_is_the_one_the_table_gives_that_crate() {
    let crates = crate_names();
    let mut prior = "";
    for bound in super::INPUT_BOUNDS {
        assert!(
            prior < bound.name,
            "INPUT_BOUNDS holds {:?} after {prior:?}; the table is in byte order of name",
            bound.name
        );
        prior = bound.name;
        assert!(bound.maximum > 0, "the bound {} admits nothing", bound.name);
        assert!(!bound.measures.trim().is_empty(), "the bound {} states nothing about what it measures", bound.name);
        let (owner, rest) =
            bound.name.split_once('.').unwrap_or_else(|| panic!("the bound {} is not `<crate>.<what>`", bound.name));
        assert!(
            crates.contains(&owner.to_string()),
            "the bound {} names the crate {owner}, which crates/ lacks",
            bound.name
        );
        assert!(!rest.is_empty(), "the bound {} names no part of that crate's input", bound.name);
    }
    for name in &crates {
        let sources = enforcing_sources(name);
        let enforces = sources.iter().any(|(_, text)| text.contains("within_input_bound("));
        let mut own = false;
        for (path, text) in &sources {
            for bound in super::INPUT_BOUNDS {
                if !text.contains(&format!("{:?}", bound.name)) {
                    continue;
                }
                let owner = bound.name.split_once('.').map(|(owner, _)| owner).unwrap_or_default();
                assert_eq!(
                    owner, name,
                    "{path} states the input bound {}, which the table gives to another crate",
                    bound.name
                );
                own = true;
            }
        }
        assert!(!enforces || own, "crates/{name} enforces an input bound the table does not hold");
    }
}
