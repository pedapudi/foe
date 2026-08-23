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
