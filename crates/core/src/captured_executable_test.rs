//! Where the store for configured executables is placed.

use super::Store;
use std::path::{Path, PathBuf};

fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("foe-core-store-{name}-{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("the scratch directory is created");
    dir
}

/// A store is placed outside every write root, which is what stops an
/// episode overwriting the executable it will later run.
#[test]
fn a_store_is_placed_outside_every_declared_write_root() {
    let dir = scratch("outside");
    let store = Store::create(&dir.join("episode"), std::slice::from_ref(&dir)).expect("a store is created");
    assert!(!store.root.0.starts_with(&dir), "the store sits under a write root: {}", store.root.0.display());
}

/// `--dangerously-permit-everything-no-sandbox` grants the whole filesystem,
/// so no directory lies outside a write root. The rule above can then name
/// no candidate, and it protects nothing either: an episode that may write
/// everywhere reaches the store wherever it is put. Without this the run
/// cannot start, and says only that each candidate lies under a write root.
#[test]
fn a_whole_filesystem_write_grant_still_places_a_store() {
    let dir = scratch("everywhere");
    let store = Store::create(&dir.join("episode"), &[PathBuf::from("/")]);
    let store = store.unwrap_or_else(|e| panic!("a store is created under a whole-filesystem grant: {e}"));
    assert!(store.root.0.starts_with(Path::new("/")), "the store has a path");
}
