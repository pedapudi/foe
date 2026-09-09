//! SHA-256 verification for content-addressed log evidence.
//!
//! The hash comes from `sha2`, which the binary that writes a log already
//! links through the contract crate, and which the crate that verifies a
//! log bundle already imports for the same function. A reader that only
//! folds a log takes on one small hashing crate and nothing of the runtime.

use sha2::{Digest, Sha256};

/// Lowercase SHA-256 hex for `bytes`.
pub fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes).iter().map(|byte| format!("{byte:02x}")).collect()
}

/// The only path a rendering archive with `digest` may use below `spill/`.
pub fn rendering_file(digest: &str) -> Option<String> {
    let hex = digest.strip_prefix("sha256:")?;
    (hex.len() == 64 && hex.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
        .then(|| format!("renderings/{hex}.txt"))
}

#[cfg(test)]
#[path = "digest_test.rs"]
mod tests;
