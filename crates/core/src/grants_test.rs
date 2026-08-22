use super::{canonicalize, contains, resolve, RootReader, RootWriter};
use crate::test_util::tmp;
use crate::{CapError, Reader, Writer};
use std::path::PathBuf;

fn roots(dir: &std::path::Path) -> Vec<PathBuf> {
    vec![std::fs::canonicalize(dir).unwrap()]
}

#[test]
fn containment_compares_components_so_a_sibling_prefix_is_outside() {
    let roots = vec![PathBuf::from("/src")];
    assert!(contains(&roots, std::path::Path::new("/src/a/b")));
    assert!(contains(&roots, std::path::Path::new("/src")));
    assert!(!contains(&roots, std::path::Path::new("/src-other/a")));
    assert!(!contains(&roots, std::path::Path::new("/")));
}

#[test]
fn a_link_that_resolves_outside_every_root_is_denied() {
    let outside = tmp("grants-outside");
    let inside = tmp("grants-inside");
    std::fs::write(outside.join("secret"), "s").unwrap();
    std::os::unix::fs::symlink(outside.join("secret"), inside.join("link")).unwrap();
    let err = resolve(&roots(&inside), &inside.join("link")).unwrap_err();
    assert!(matches!(err, CapError::Denied { .. }), "{err}");
    let reader = RootReader::new(roots(&inside)).unwrap();
    assert!(reader.read(&inside.join("link")).is_err());
}

#[test]
fn canonicalize_resolves_the_parent_of_a_file_that_does_not_exist_yet() {
    let dir = tmp("grants-new");
    let path = canonicalize(&dir.join("fresh.txt")).unwrap();
    assert_eq!(path, std::fs::canonicalize(&dir).unwrap().join("fresh.txt"));
    assert!(canonicalize(&dir.join("missing-dir").join("fresh.txt")).is_err());
}

#[test]
fn relative_paths_resolve_against_the_first_root() {
    let dir = tmp("grants-rel");
    std::fs::write(dir.join("a.txt"), "a").unwrap();
    let reader = RootReader::new(roots(&dir)).unwrap();
    assert_eq!(reader.read(std::path::Path::new("a.txt")).unwrap(), b"a");
    assert!(reader.metadata(std::path::Path::new("../")).is_err());
}

#[test]
fn writer_stages_beside_the_target_and_renames() {
    let dir = tmp("grants-write");
    let writer = RootWriter::new(roots(&dir)).unwrap();
    let target = dir.join("out.txt");
    writer.write(&target, b"one").unwrap();
    writer.write(&target, b"two").unwrap();
    assert_eq!(std::fs::read(&target).unwrap(), b"two");
    let leftovers: Vec<_> = std::fs::read_dir(&dir).unwrap().map(|e| e.unwrap().file_name()).collect();
    assert_eq!(leftovers, vec![std::ffi::OsString::from("out.txt")], "no staging file remains");
}

#[test]
fn writer_refuses_paths_outside_its_roots() {
    let dir = tmp("grants-write-outside");
    let other = tmp("grants-write-other");
    let writer = RootWriter::new(roots(&dir)).unwrap();
    assert!(matches!(writer.write(&other.join("x"), b"x"), Err(CapError::Denied { .. })));
    assert!(!other.join("x").exists());
}

/// docs/config.md `grants`: path resolution at the time of use cannot leave
/// the opened root descriptor.
#[test]
fn replacing_a_checked_directory_with_a_link_cannot_redirect_an_operation() {
    let inside = tmp("grants-race-inside");
    let outside = tmp("grants-race-outside");
    let slot = inside.join("slot");
    std::fs::create_dir(&slot).unwrap();
    std::fs::write(slot.join("value"), "inside").unwrap();
    std::fs::write(outside.join("value"), "outside").unwrap();
    let reader = RootReader::new(roots(&inside)).unwrap();
    let writer = RootWriter::new(roots(&inside)).unwrap();

    std::fs::rename(&slot, inside.join("held")).unwrap();
    std::os::unix::fs::symlink(&outside, &slot).unwrap();

    assert!(reader.read(&slot.join("value")).is_err());
    assert!(reader.files(&slot).is_err());
    assert!(writer.write(&slot.join("value"), b"changed").is_err());
    assert_eq!(std::fs::read(outside.join("value")).unwrap(), b"outside");
}
