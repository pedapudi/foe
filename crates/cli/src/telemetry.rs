//! Native telemetry: enabled by one machine-level file, emitted after every
//! run, inspected with `foe telemetry`.
//!
//! The switch is `~/.config/foe/telemetry.json` holding `{"capture": PATH}`.
//! It lives beside the credential files rather than in the contract
//! configuration because telemetry observes an episode without changing its
//! behavior: the same contract with and without telemetry must keep one
//! contract fingerprint. Emission runs after the episode from the log the run
//! just wrote — the writer and the reader are one binary, so no line can be
//! unreadable through version skew — and a telemetry failure warns without
//! touching the run's outcome or exit code.
//!
//! What that file means, what emission does with it, and what a preview
//! prints are `foe_telemetry::capture`. What is here is the one path
//! convention: telemetry's switch sits in the same directory as the
//! credentials, which `foe_transport::paths` owns and this crate composes.

use foe_transport::paths;
use std::path::Path;

pub use foe_telemetry::capture::{after_run, preview_logs, Settings};

/// The enablement file under an explicit home.
pub fn settings_in(home: &Path) -> Result<Option<Settings>, String> {
    foe_telemetry::capture::settings_at(&paths::config_dir(home).join("telemetry.json"), home)
}

/// The enablement file of the running user.
pub fn settings() -> Result<Option<Settings>, String> {
    settings_in(&paths::home_dir()?)
}

/// `foe telemetry LOG… [--json]`: the emission each log receives, printed.
pub fn preview(logs: &[String], json: bool) -> Result<(), String> {
    preview_logs(logs, settings()?.as_ref(), json)
}

#[cfg(test)]
#[path = "telemetry_test.rs"]
mod tests;
