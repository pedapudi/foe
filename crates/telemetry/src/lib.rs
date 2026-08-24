//! Categorized, scrubbed, OTLP-encoded telemetry derived from episode logs.
//!
//! Telemetry is a pure function of an episode log, the rules compiled into
//! this crate, and a local key. It does not instrument the runtime: it
//! reads `episode.jsonl` after the episode ended. Three consequences carry
//! the design.
//!
//! The loop pays nothing, and the kernel needs no telemetry code at all.
//! Nothing here depends on `foe-core`.
//!
//! The same inputs produce the same bytes. Nothing draws on a random source
//! or on the wall clock: span and trace ids are derived from the episode id
//! and times come from the log's own event times. Determinism is what makes
//! the preview command trustworthy — it runs the emission and prints it,
//! rather than simulating what an emission would do.
//!
//! Running the binary is the opt-in. There is no configuration key, no
//! identity change, and no environment variable, and this crate opens no
//! network connection and calls no model.

#![forbid(unsafe_code)]

pub mod capture;
pub mod classify;
pub mod extract;
pub mod otlp;
pub mod scrub;

use classify::Classification;
use extract::Facts;
use foe_log::{Event, LogError};
use otlp::{list, number, span, text, AnyValue, Attribute};
use scrub::{Report, Scrubber};
use std::path::{Path, PathBuf};

/// Changes whenever an emitted field is added, removed, renamed, or given a
/// different meaning. Every payload carries it as a resource attribute.
pub const SCHEMA_VERSION: &str = "2";

/// The one way deriving telemetry fails: the scrubber found something in
/// its own output. Reading and writing errors belong to the caller that
/// does the reading and writing.
#[derive(Debug, PartialEq, Eq)]
pub struct ScrubFailure(pub Vec<String>);

impl std::fmt::Display for ScrubFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "scrub self-check failed, nothing emitted: {}", self.0.join("; "))
    }
}

/// One episode's telemetry: the facts it was derived from, what the
/// classifier concluded, what the scrubber replaced, and the spans and
/// resource attributes the payload is assembled from.
pub struct Emission {
    pub facts: Facts,
    pub classification: Classification,
    pub report: Report,
    pub resource: Vec<Attribute>,
    pub spans: Vec<otlp::Span>,
}

impl Emission {
    /// The payload as the one line `emit` appends, newline included.
    pub fn line(&self) -> String {
        format!("{}\n", otlp::payload(SCHEMA_VERSION, &self.resource, &self.spans))
    }
}

/// Derives one episode's telemetry, or fails without producing a payload
/// when the scrub self-check still sees something in its own output.
pub fn emission(events: &[Event], log_dir: &str, key: Vec<u8>) -> Result<Emission, ScrubFailure> {
    let facts = extract::extract(events, log_dir);
    let classification = classify::classify(&facts.evidence);
    let scrubber = Scrubber::new(key, &facts.known);
    let mut report = Report::default();
    let mut findings = Vec::new();
    let mut clean = |field: String, text: &str| -> String {
        let scrubbed = scrubber.scrub(text, &mut report);
        findings.extend(scrubber.findings(&field, &scrubbed));
        scrubbed
    };

    let trace = otlp::trace_id(&facts.id);
    let root = otlp::span_id(&facts.id, "episode", 0);
    let mut spans = Vec::new();
    for step in &facts.steps {
        let id = otlp::span_id(&facts.id, "step", step.seq);
        let name = format!("step {}", step.step);
        spans.push(span(&trace, id, root.clone(), name, step.start_ms, step.end_ms).with(
            vec![
                number("foe.step", step.step as u64),
                number("foe.tokens.input", step.usage.input),
                number("foe.tokens.output", step.usage.output),
                number("foe.tokens.cache_read", step.usage.cache_read),
                text("foe.stop_reason", step.stop.map(|s| extract::term(&s)).unwrap_or("unfinished".into())),
            ],
            true,
        ));
    }
    for call in &facts.calls {
        let id = otlp::span_id(&facts.id, "tool", call.seq);
        let name = format!("tool {}", call.name);
        spans.push(span(&trace, id, root.clone(), name, call.start_ms, call.end_ms).with(
            vec![
                text("foe.tool.name", call.name.clone()),
                number("foe.tool.seq", call.seq),
                number("foe.tool.duration_ms", call.duration_ms),
                Attribute { key: "foe.tool.is_error".into(), value: AnyValue::BoolValue(call.is_error) },
                text("foe.tool.subject", clean(format!("tool/result.subject seq {}", call.seq), &call.subject)),
            ],
            !call.is_error,
        ));
    }

    let (kind, exit_class, detail) = extract::outcome_terms(facts.outcome.as_ref());
    let mut episode_attributes = vec![
        text("foe.episode.id", facts.id.clone()),
        text("foe.outcome.kind", kind),
        text("foe.outcome.exit_class", exit_class),
        text("foe.outcome.detail", clean("outcome.detail".into(), &detail)),
    ];
    // Provenance exists only for a completed episode; an absent attribute
    // is an outcome that established no completion, never a zero.
    episode_attributes.extend(facts.provenance.map(|p| text("foe.completion.provenance", p)));
    episode_attributes.extend(vec![
        number("foe.verification.runs", facts.verification_runs),
        number("foe.verification.findings", facts.verification_findings),
        text("foe.model.provider", facts.provider.clone()),
        text("foe.model.model", facts.model.clone()),
        text("foe.category", classification.bucket.clone()),
        text("foe.category.top_level", classification.top_level.clone()),
        list("foe.evidence", classification.votes.iter().map(|v| format!("{}={}", v.token, v.bucket))),
        list("foe.category.counts", classification.counts.iter().map(|(name, n)| format!("{name}={n}"))),
        number("foe.tokens.input", facts.usage.input),
        number("foe.tokens.output", facts.usage.output),
        number("foe.tokens.cache_read", facts.usage.cache_read),
        number("foe.model_calls", facts.model_calls),
        number("foe.tool_calls", facts.calls.len() as u64),
        number("foe.tool_errors", facts.calls.iter().filter(|c| c.is_error).count() as u64),
        number("foe.duration_ms", (facts.end_ms - facts.start_ms).max(0) as u64),
    ]);
    let episode = span(&trace, root, String::new(), "episode".into(), facts.start_ms, facts.end_ms)
        .with(episode_attributes, kind == "completed");
    spans.insert(0, episode);

    if !findings.is_empty() {
        return Err(ScrubFailure(findings));
    }
    let resource = resource_attributes(&facts, &report);
    Ok(Emission { facts, classification, report, resource, spans })
}

/// Attributes describing the installation the episode ran in and the rules
/// that produced the payload. A consumer needs all of them to know whether
/// two payloads may be compared.
fn resource_attributes(facts: &Facts, report: &Report) -> Vec<Attribute> {
    let mut attributes = vec![
        text("service.name", "foe"),
        text("foe.program.identity", facts.identity.clone()),
        text("foe.runtime.version", facts.runtime_version.clone()),
        text("foe.runtime.build", facts.runtime_build.clone()),
        text("foe.schema.version", SCHEMA_VERSION),
        text("foe.taxonomy.version", classify::TAXONOMY_VERSION),
        text("foe.ruleset.version", classify::RULESET_VERSION),
    ];
    attributes.extend(report.0.iter().map(|(name, count)| number(&format!("foe.scrub.{name}"), *count as u64)));
    attributes
}

/// Emits one log into `capture`, fail-closed: a line this build cannot
/// read, or a self-check finding, refuses the whole episode, because the
/// scrubber learns the values it must remove from the log itself.
/// Returns the episode id on success.
pub fn emit_into(log: &Path, capture: &Path, key: Vec<u8>) -> Result<String, String> {
    let (events, dir, unparsed) = read_log(log).map_err(|error| format!("{}: {error}", log.display()))?;
    if unparsed > 0 {
        return Err(format!(
            "{}: {unparsed} line(s) this build cannot read; scrubbing coverage cannot be guaranteed. \
             Nothing emitted.",
            log.display()
        ));
    }
    let derived = emission(&events, &dir.to_string_lossy(), key).map_err(|f| format!("{}: {f}", log.display()))?;
    append(capture, &derived.line()).map_err(|error| format!("{}: {error}", capture.display()))?;
    Ok(derived.facts.id.clone())
}

/// Appends one line to `capture`, creating its directory.
pub fn append(capture: &Path, line: &str) -> std::io::Result<()> {
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
pub fn read_log(path: &Path) -> Result<(Vec<Event>, PathBuf, usize), LogError> {
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
pub fn preview(derived: &Emission) -> String {
    let list = |items: &[String], sep: &str| if items.is_empty() { "none".to_string() } else { items.join(sep) };
    let (facts, class) = (&derived.facts, &derived.classification);
    let (kind, exit_class, _) = crate::extract::outcome_terms(facts.outcome.as_ref());
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
