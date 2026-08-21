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
    let reader = RootReader::new(roots(&inside));
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
    let reader = RootReader::new(roots(&dir));
    assert_eq!(reader.read(std::path::Path::new("a.txt")).unwrap(), b"a");
    assert!(reader.metadata(std::path::Path::new("../")).is_err());
}

#[test]
fn writer_stages_beside_the_target_and_renames() {
    let dir = tmp("grants-write");
    let writer = RootWriter::new(roots(&dir));
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
    let writer = RootWriter::new(roots(&dir));
    assert!(matches!(writer.write(&other.join("x"), b"x"), Err(CapError::Denied { .. })));
    assert!(matches!(writer.create_dir_all(&other.join("deep/er")), Err(CapError::Denied { .. })));
    assert!(!other.join("deep").exists());
}

#[test]
fn writer_creates_nested_directories_inside_a_root() {
    let dir = tmp("grants-mkdir");
    let writer = RootWriter::new(roots(&dir));
    writer.create_dir_all(&dir.join("a/b/c")).unwrap();
    assert!(dir.join("a/b/c").is_dir());
    writer.create_dir_all(std::path::Path::new("rel/x")).unwrap();
    assert!(dir.join("rel/x").is_dir());
}

#[test]
fn walk_honors_ignore_files_and_stays_within_roots() {
    let dir = tmp("grants-walk");
    let outside = tmp("grants-walk-outside");
    std::fs::write(dir.join(".gitignore"), "ignored.txt\n").unwrap();
    std::fs::write(dir.join("kept.txt"), "k").unwrap();
    std::fs::write(dir.join("ignored.txt"), "i").unwrap();
    std::fs::write(outside.join("far.txt"), "f").unwrap();
    std::os::unix::fs::symlink(&outside, dir.join("escape")).unwrap();
    let reader = RootReader::new(roots(&dir));
    let names: Vec<String> =
        reader.walk(&dir).unwrap().filter_map(|p| p.file_name().map(|n| n.to_string_lossy().into_owned())).collect();
    assert!(names.contains(&"kept.txt".to_string()), "{names:?}");
    assert!(!names.contains(&"ignored.txt".to_string()), "{names:?}");
    assert!(!names.contains(&"far.txt".to_string()) && !names.contains(&"escape".to_string()), "{names:?}");
}
