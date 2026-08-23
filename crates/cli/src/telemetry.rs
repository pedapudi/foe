//! Native telemetry: enabled by one machine-level file, emitted after every
//! run, inspected with `foe telemetry`.
//!
//! The switch is `~/.config/foe/telemetry.json` holding `{"capture": PATH}`.
//! It lives beside the credential files rather than in the program
//! configuration because telemetry observes an episode without changing its
//! behavior: the same program with and without telemetry must keep one
//! program identity. Emission runs after the episode from the log the run
//! just wrote — the writer and the reader are one binary, so no line can be
//! unreadable through version skew — and a telemetry failure warns without
//! touching the run's outcome or exit code.

use std::path::{Path, PathBuf};

/// Where telemetry goes, read from the machine configuration.
#[derive(Debug)]
pub struct Settings {
    pub capture: PathBuf,
}

/// The enablement file under an explicit home, or `None` when the file is
/// absent. A file that exists but cannot be read is an error rather than a
/// silent disable: someone turned telemetry on, and it must not go quietly.
pub fn settings_in(home: &Path) -> Result<Option<Settings>, String> {
    let path = foe_transport::paths::config_dir(home).join("telemetry.json");
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("{}: {error}", path.display())),
    };
    let parsed: serde_json::Value =
        serde_json::from_str(&text).map_err(|error| format!("{}: {error}", path.display()))?;
    let capture = parsed
        .get("capture")
        .and_then(|v| v.as_str())
        .ok_or_else(|| format!("{}: `capture` must be a path", path.display()))?;
    let capture = match capture.strip_prefix("~/") {
        Some(rest) => home.join(rest),
        None => PathBuf::from(capture),
    };
    Ok(Some(Settings { capture }))
}

/// The enablement file of the running user.
pub fn settings() -> Result<Option<Settings>, String> {
    settings_in(&foe_transport::paths::home_dir()?)
}

/// Every episode log under `dir`: the root's and, through `children/`, every
/// descendant's, in path order so emission order is stable.
fn logs_under(dir: &Path) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let own = dir.join(foe_log::fold::LOG_FILE);
    if own.is_file() {
        found.push(own);
    }
    if let Ok(entries) = std::fs::read_dir(dir.join("children")) {
        let mut children: Vec<PathBuf> = entries.filter_map(|e| e.ok().map(|e| e.path())).collect();
        children.sort();
        for child in children {
            found.extend(logs_under(&child));
        }
    }
    found
}

/// Emits every episode of one finished run. Warnings go to standard error;
/// nothing here changes the run's outcome. The one summary line is the
/// disclosure that telemetry ran and where it went.
pub fn after_run(settings: &Settings, log_dir: &Path) {
    let mut emitted = 0usize;
    let key = match key_for(settings) {
        Ok(key) => key,
        Err(warning) => return eprintln!("telemetry: {warning}"),
    };
    for log in logs_under(log_dir) {
        match foe_telemetry::emit_into(&log, &settings.capture, key.clone()) {
            Ok(_) => emitted += 1,
            Err(warning) => eprintln!("telemetry: {warning}"),
        }
    }
    eprintln!("telemetry: {emitted} episode(s) → {}", settings.capture.display());
}

/// The local scrubbing key, kept beside the capture.
fn key_for(settings: &Settings) -> Result<Vec<u8>, String> {
    let dir = settings.capture.parent().unwrap_or(Path::new("."));
    foe_telemetry::scrub::local_key(dir).map_err(|error| format!("{}: {error}", dir.display()))
}

/// `foe telemetry LOG… [--json]`: the emission each log receives, printed.
/// With telemetry enabled the pseudonyms are the emitted ones, because the
/// key is the capture's own. Disabled, a zero key stands in and the header
/// says so — the shape is exact, the pseudonyms are not.
pub fn preview(logs: &[String], json: bool) -> Result<(), String> {
    let key = match settings()? {
        Some(settings) => key_for(&settings)?,
        None => {
            eprintln!(
                "telemetry is not enabled: no ~/.config/foe/telemetry.json. Pseudonyms below use a stand-in \
                 key; enable telemetry to see the ones emission would write."
            );
            vec![0u8; 32]
        }
    };
    for log in logs {
        let (events, dir, unparsed) =
            foe_telemetry::read_log(Path::new(log)).map_err(|error| format!("{log}: {error}"))?;
        if unparsed > 0 {
            eprintln!(
                "!! {log}: {unparsed} line(s) this build cannot read; scrubbing coverage cannot be guaranteed. \
                 Emission refuses this log. This is not what would be written."
            );
        }
        let derived = foe_telemetry::emission(&events, &dir.to_string_lossy(), key.clone())
            .map_err(|failure| format!("{log}: {failure}"))?;
        match json {
            true => print!("{}", derived.line()),
            false => print!("{}", foe_telemetry::preview(&derived)),
        }
    }
    Ok(())
}

#[cfg(test)]
#[path = "telemetry_test.rs"]
mod tests;
