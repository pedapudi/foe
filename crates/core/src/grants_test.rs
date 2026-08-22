use super::{contains, RootReader, RootWriter};
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

/// docs/config.md `grants`: a read through a link that leaves every granted
/// root is denied, and a write replaces the named entry inside the root
/// rather than following the link to what it points at.
#[test]
fn a_link_that_leaves_every_root_reads_nothing_and_receives_no_write() {
    let outside = tmp("grants-outside");
    let inside = tmp("grants-inside");
    std::fs::write(outside.join("secret"), "s").unwrap();
    std::os::unix::fs::symlink(outside.join("secret"), inside.join("link")).unwrap();
    let reader = RootReader::new(roots(&inside)).unwrap();
    assert!(reader.read(&inside.join("link")).is_err());
    let writer = RootWriter::new(roots(&inside)).unwrap();
    writer.write(&inside.join("link"), b"x").unwrap();
    assert_eq!(std::fs::read(outside.join("secret")).unwrap(), b"s", "the link's target is untouched");
    assert_eq!(std::fs::read(inside.join("link")).unwrap(), b"x", "the entry inside the root now holds the bytes");
    assert!(!std::fs::symlink_metadata(inside.join("link")).unwrap().file_type().is_symlink());
}

#[test]
fn relative_paths_resolve_against_the_first_root() {
    let dir = tmp("grants-rel");
    std::fs::write(dir.join("a.txt"), "a").unwrap();
    let reader = RootReader::new(roots(&dir)).unwrap();
    assert_eq!(reader.read(std::path::Path::new("a.txt")).unwrap(), b"a");
    assert!(reader.metadata(std::path::Path::new("../")).is_err());
}

/// docs/config.md `grants`: a file that does not exist yet is created under
/// the root, and one named through `..` is refused.
#[test]
fn a_new_file_is_created_under_the_root_and_a_climbing_path_is_refused() {
    let dir = tmp("grants-new");
    let writer = RootWriter::new(roots(&dir)).unwrap();
    writer.write(std::path::Path::new("fresh.txt"), b"new").unwrap();
    assert_eq!(std::fs::read(dir.join("fresh.txt")).unwrap(), b"new");
    assert!(writer.write(std::path::Path::new("../escape.txt"), b"x").is_err());
    assert!(!dir.parent().unwrap().join("escape.txt").exists());
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

/// docs/config.md `grants`: containment holds at the time of use. A
/// directory component replaced by a link after the handle was opened
/// redirects nothing, because the operation resolves under the descriptor
/// the runtime holds rather than under the pathname as it now stands.
#[test]
fn replacing_a_directory_component_with_a_link_redirects_nothing() {
    let inside = tmp("grants-race-inside");
    let outside = tmp("grants-race-outside");
    let slot = inside.join("slot");
    std::fs::create_dir(&slot).unwrap();
    std::fs::write(slot.join("value"), "inside").unwrap();
    std::fs::write(outside.join("value"), "outside").unwrap();
    let reader = RootReader::new(roots(&inside)).unwrap();
    let writer = RootWriter::new(roots(&inside)).unwrap();
    assert_eq!(reader.read(&slot.join("value")).unwrap(), b"inside");

    std::fs::rename(&slot, inside.join("held")).unwrap();
    std::os::unix::fs::symlink(&outside, &slot).unwrap();

    assert!(reader.read(&slot.join("value")).is_err(), "a read must not follow the link out of the root");
    assert!(reader.metadata(&slot).is_err());
    assert!(writer.write(&slot.join("value"), b"changed").is_err());
    assert_eq!(std::fs::read(outside.join("value")).unwrap(), b"outside", "nothing outside the root was written");
}

/// docs/tools.md `grep`: the reader answers for a directory as well as a
/// file, which is how `grep` reports an unreachable search root.
#[test]
fn metadata_answers_for_a_directory_inside_the_roots() {
    let dir = tmp("grants-dir-metadata");
    std::fs::create_dir(dir.join("sub")).unwrap();
    let reader = RootReader::new(roots(&dir)).unwrap();
    assert!(reader.metadata(&dir.join("sub")).unwrap().is_dir());
    assert!(reader.metadata(&dir).unwrap().is_dir());
    assert!(reader.metadata(&dir.join("absent")).is_err());
}
