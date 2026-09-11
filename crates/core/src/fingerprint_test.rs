use super::runtime_info;

#[test]
fn runtime_info_hashes_the_binary_or_says_unknown() {
    let info = runtime_info();
    assert_eq!(info.version, env!("CARGO_PKG_VERSION"));
    assert!(info.build == "unknown" || info.build.starts_with("sha256:"));
    if let Ok(bytes) = std::fs::read("/proc/self/exe") {
        assert_eq!(info.build, format!("sha256:{}", foe_contract::fingerprint::sha256_hex(&bytes)));
    }
    std::thread::scope(|scope| {
        for _ in 0..8 {
            let expected = &info;
            scope.spawn(move || assert_eq!(&runtime_info(), expected));
        }
    });
}
