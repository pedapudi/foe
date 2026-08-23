//! The `foe-telemetry` binary. docs/telemetry.md states what it produces.
//!
//! Two commands over the same computation: `emit` writes the payload,
//! `preview` prints it. Preview is not a simulation — it runs the emission
//! and shows the result — which is only meaningful because the computation
//! is deterministic.

#![forbid(unsafe_code)]

use foe_log::{Event, LogError};
use foe_telemetry::otlp::AnyValue;
use foe_telemetry::{emission, scrub, Emission};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const USAGE: &str = "usage:
  foe-telemetry emit LOG... [--out FILE]
  foe-telemetry preview LOG... [--out FILE] [--json]

LOG is an episode directory holding episode.jsonl, or a log file itself.
--out names the capture file, by default LOG/telemetry/otel.jsonl; its
directory holds the local scrubbing key too, which is why preview takes
--out: the pseudonyms it prints are the ones emitting there would write.";

fn main() -> ExitCode {
    match run(&std::env::args().skip(1).collect::<Vec<String>>()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("{message}");
            ExitCode::FAILURE
        }
    }
}

fn run(args: &[String]) -> Result<(), String> {
    let mut logs: Vec<String> = Vec::new();
    let mut out: Option<PathBuf> = None;
    let mut json = false;
    let mut rest = args.iter().skip(1);
    while let Some(arg) = rest.next() {
        match arg.as_str() {
            "--out" => out = Some(PathBuf::from(rest.next().ok_or_else(|| format!("--out takes a value\n{USAGE}"))?)),
            "--json" => json = true,
            _ if arg.starts_with("--") => return Err(format!("unknown option {arg}\n{USAGE}")),
            _ => logs.push(arg.clone()),
        }
    }
    let command = args.first().map(String::as_str).unwrap_or_default();
    if logs.is_empty() || !["emit", "preview"].contains(&command) {
        return Err(USAGE.to_string());
    }
    if json && command == "emit" {
        return Err(format!("--json does not apply to `foe-telemetry emit`\n{USAGE}"));
    }
    for log in &logs {
        let (events, dir, unparsed) = read_log(Path::new(log)).map_err(|error| format!("{log}: {error}"))?;
        if unparsed > 0 {
            let doubt = format!(
                "{log}: {unparsed} line(s) this build cannot read. The known-value layer learns what to remove \
                 from the log, so a value it needed may be on one of them and scrubbing coverage cannot be \
                 guaranteed."
            );
            if command == "emit" {
                return Err(format!("{doubt} Nothing emitted."));
            }
            eprintln!("!! {doubt}\n!! `foe-telemetry emit` refuses this log. This is not what emit would write.");
        }
        let capture = out.clone().unwrap_or_else(|| dir.join("telemetry").join("otel.jsonl"));
        let key_dir = capture.parent().unwrap_or(Path::new(".")).to_path_buf();
        let key = scrub::local_key(&key_dir).map_err(|error| format!("{}: {error}", key_dir.display()))?;
        let derived = emission(&events, &dir.to_string_lossy(), key).map_err(|failure| format!("{log}: {failure}"))?;
        match command {
            "emit" => {
                append(&capture, &derived.line()).map_err(|error| format!("{}: {error}", capture.display()))?;
                println!("{} → {}", derived.facts.id, capture.display());
            }
            _ if json => print!("{}", derived.line()),
            _ => print!("{}", preview(&derived)),
        }
    }
    Ok(())
}

/// Appends one line to `capture`, creating its directory.
fn append(capture: &Path, line: &str) -> std::io::Result<()> {
    capture.parent().map(std::fs::create_dir_all).transpose()?;
    let mut file = std::fs::OpenOptions::new().create(true).append(true).open(capture)?;
    std::io::Write::write_all(&mut file, line.as_bytes())
}

/// Reads a log given either its directory or the file itself, and returns
/// the events, the directory the log lives in, and how many lines did not
/// parse.
///
/// Structural validation is not applied: a log cut short by a crash still
/// describes everything that happened before the cut, and that is worth
/// emitting. A line whose event shape this build cannot read is a different
/// matter, because the scrubber learns the values it must remove from the
/// log itself. Losing the line that carries the granted roots loses the
/// known-value layer, and nothing downstream can tell that it happened.
/// Skipped lines are therefore counted for the caller to refuse on.
fn read_log(path: &Path) -> Result<(Vec<Event>, PathBuf, usize), LogError> {
    let file = if path.is_dir() { path.join(foe_log::fold::LOG_FILE) } else { path.to_path_buf() };
    let dir = if path.is_dir() { path.to_path_buf() } else { path.parent().unwrap_or(Path::new(".")).to_path_buf() };
    let text = std::fs::read_to_string(&file)?;
    let lines: Vec<&str> = text.lines().filter(|line| !line.trim().is_empty()).collect();
    let events: Vec<Event> = lines.iter().filter_map(|line| serde_json::from_str(line).ok()).collect();
    let unparsed = lines.len() - events.len();
    Ok((events, dir, unparsed))
}

/// What one episode's emission carries, for a person. Every number and
/// label here is in the payload; nothing is computed only for display.
fn preview(derived: &Emission) -> String {
    let list = |items: &[String], sep: &str| if items.is_empty() { "none".to_string() } else { items.join(sep) };
    let (facts, class) = (&derived.facts, &derived.classification);
    let (kind, exit_class, _) = foe_telemetry::extract::outcome_terms(facts.outcome.as_ref());
    let (used, failed) = (facts.usage, facts.calls.iter().filter(|c| c.is_error).count());
    let spend = format!("{} in · {} out · {} cache-read", used.input, used.output, used.cache_read);
    let votes: Vec<String> = class.votes.iter().map(|v| format!("{}={} ×{}", v.token, v.bucket, v.count)).collect();
    let counts: Vec<String> = derived.report.0.iter().map(|(name, count)| format!("{name} {count}")).collect();
    let mut out = format!("{} · {}/{} · {kind}/{exit_class}\n", facts.id, facts.provider, facts.model);
    out += &format!("  category  {} → {}\n", class.bucket, class.top_level);
    out += &format!("  evidence  {}\n", list(&votes, ", "));
    out += &format!("  totals    {} model calls · {spend}", facts.model_calls);
    out += &format!(" · {} tool calls, {failed} failed\n", facts.calls.len());
    out += &format!("  scrubbed  {}\n  spans\n", list(&counts, " · "));
    for span in &derived.spans {
        let nanos = |text: &str| text.parse::<u64>().unwrap_or_default();
        let millis = (nanos(&span.end_time_unix_nano) - nanos(&span.start_time_unix_nano)) / 1_000_000;
        // The one attribute worth a line of its own: what a tool acted on,
        // or why a step stopped.
        let of = |key: &str| match span.attributes.iter().find(|a| a.key == key).map(|a| &a.value) {
            Some(AnyValue::StringValue(text)) if !text.is_empty() => Some(text.as_str()),
            _ => None,
        };
        let detail = of("foe.tool.subject").or_else(|| of("foe.stop_reason")).unwrap_or_default();
        out += &format!("    {:<28} {millis:>9} ms  {detail}\n", span.name);
    }
    out
}
