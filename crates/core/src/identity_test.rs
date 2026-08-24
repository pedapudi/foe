use super::runtime_info;

#[test]
fn runtime_info_hashes_the_binary_or_says_unknown() {
    let info = runtime_info();
    assert_eq!(info.version, env!("CARGO_PKG_VERSION"));
    assert!(info.build == "unknown" || info.build.starts_with("sha256:"));
}
