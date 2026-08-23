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

pub mod classify;
pub mod extract;
pub mod otlp;
pub mod scrub;

use classify::Classification;
use extract::Facts;
use foe_log::Event;
use otlp::{list, number, span, text, AnyValue, Attribute};
use scrub::{Report, Scrubber};

/// Changes whenever an emitted field is added, removed, renamed, or given a
/// different meaning. Every payload carries it as a resource attribute.
pub const SCHEMA_VERSION: &str = "1";

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
    let episode = span(&trace, root, String::new(), "episode".into(), facts.start_ms, facts.end_ms).with(
        vec![
            text("foe.episode.id", facts.id.clone()),
            text("foe.outcome.kind", kind),
            text("foe.outcome.exit_class", exit_class),
            text("foe.outcome.detail", clean("outcome.detail".into(), &detail)),
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
        ],
        kind == "completed",
    );
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
