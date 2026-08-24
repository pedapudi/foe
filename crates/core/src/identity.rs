//! What the running binary reports about itself for a program's identity.
//!
//! The identity document of `foe_config::identity` names the runtime that
//! will run the program. Producing that name reads `/proc/self/exe`, which
//! is the runtime probing itself and belongs here rather than in the
//! configuration.

use foe_config::identity::sha256_hex;
use foe_log::RuntimeInfo;

/// The running binary's version and content hash. `build` is `unknown`
/// when the binary cannot be read back, for example off Linux.
pub fn runtime_info() -> RuntimeInfo {
    let build = std::fs::read("/proc/self/exe")
        .map(|bytes| format!("sha256:{}", sha256_hex(&bytes)))
        .unwrap_or_else(|_| "unknown".into());
    RuntimeInfo { version: env!("CARGO_PKG_VERSION").into(), build }
}

#[cfg(test)]
#[path = "identity_test.rs"]
mod tests;
