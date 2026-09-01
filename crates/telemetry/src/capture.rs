//! Telemetry on one machine: the file that enables it, where emission
//! goes, and the two operations performed against a finished run — emitting
//! every episode it wrote, and printing what emission would write.
//!
//! The switch is a JSON file holding `{"capture": PATH}`. It is a
//! machine-level file rather than part of a contract's configuration because
//! telemetry observes an episode without changing it: the same contract
//! observed and unobserved must keep one contract fingerprint. A `~/`-relative
//! capture resolves against the home the file was found under.
//!
//! Where that file lives is a path convention this crate does not own — a
//! caller supplies the path and the home, so nothing here depends on the
//! rest of the runtime.

use std::path::{Path, PathBuf};

/// Where telemetry goes, read from the machine configuration.
#[derive(Debug)]
pub struct Settings {
    pub capture: PathBuf,
}

/// The enablement file at `path`, or `None` when it is absent. A file that
/// exists but cannot be read is an error rather than a silent disable:
/// someone turned telemetry on, and it must not go quietly. A capture
/// beginning `~/` resolves against `home`.
pub fn settings_at(path: &Path, home: &Path) -> Result<Option<Settings>, String> {
    let text = match std::fs::read_to_string(path) {
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
        match crate::emit_into(&log, &settings.capture, key.clone()) {
            Ok(_) => emitted += 1,
            Err(warning) => eprintln!("telemetry: {warning}"),
        }
    }
    eprintln!("telemetry: {emitted} episode(s) → {}", settings.capture.display());
}

/// The local scrubbing key, kept beside the capture.
fn key_for(settings: &Settings) -> Result<Vec<u8>, String> {
    let dir = settings.capture.parent().unwrap_or(Path::new("."));
    crate::scrub::local_key(dir).map_err(|error| format!("{}: {error}", dir.display()))
}

/// The emission each log receives, printed. With telemetry enabled the
/// pseudonyms are the emitted ones, because the key is the capture's own.
/// Disabled, a zero key stands in and the header says so — the shape is
/// exact, the pseudonyms are not.
pub fn preview_logs(logs: &[String], settings: Option<&Settings>, json: bool) -> Result<(), String> {
    let key = match settings {
        Some(settings) => key_for(settings)?,
        None => {
            eprintln!(
                "telemetry is not enabled: no ~/.config/foe/telemetry.json. Pseudonyms below use a stand-in \
                 key; enable telemetry to see the ones emission would write."
            );
            vec![0u8; 32]
        }
    };
    for log in logs {
        let (events, dir, unparsed) = crate::read_log(Path::new(log)).map_err(|error| format!("{log}: {error}"))?;
        if unparsed > 0 {
            eprintln!(
                "!! {log}: {unparsed} line(s) this build cannot read; scrubbing coverage cannot be guaranteed. \
                 Emission refuses this log. This is not what would be written."
            );
        }
        let derived = crate::emission(&events, &dir.to_string_lossy(), key.clone())
            .map_err(|failure| format!("{log}: {failure}"))?;
        match json {
            true => print!("{}", derived.line()),
            false => print!("{}", crate::preview(&derived)),
        }
    }
    Ok(())
}
