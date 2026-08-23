//! The OTLP JSON trace encoding, written by hand.
//!
//! No OpenTelemetry SDK: an SDK brings a transport stack, a batching
//! processor, and a shutdown protocol, none of which an add-on that writes
//! one file may take on. What is left after removing those is the wire
//! shape, which is small enough to state here and pinned by a golden file.
//!
//! The encoding is protobuf-JSON: field names in camel case, 64-bit
//! integers as decimal strings, byte fields as lower-case hex, enums as
//! their numeric values. Traces only; numbers ride as span attributes and a
//! collector derives metrics from them.

use serde::Serialize;
use sha2::{Digest, Sha256};

/// Instrumentation scope name of every span this crate writes.
pub const SCOPE: &str = "foe.telemetry";

/// The whole document for one episode: one resource, one instrumentation
/// scope, and every span under it.
///
/// The envelope is a format string rather than five wrapper structs
/// because it has exactly one shape, and because it must keep the field
/// order written here — assembling it as a JSON value would sort the keys
/// and the golden file would stop describing what a reader receives. The
/// parts that vary are serialized by serde and inserted whole. Nothing
/// interpolated needs escaping: the scope name and `version` are
/// compile-time constants, and the two lists arrive already encoded.
pub fn payload(version: &str, resource: &[Attribute], spans: &[Span]) -> String {
    let attributes = serde_json::to_string(resource).expect("an attribute holds no unserializable value");
    let spans = serde_json::to_string(spans).expect("a span holds no unserializable value");
    let scope = format!(r#"{{"name":"{SCOPE}","version":"{version}"}}"#);
    let scope_spans = format!(r#"[{{"scope":{scope},"spans":{spans}}}]"#);
    format!(r#"{{"resourceSpans":[{{"resource":{{"attributes":{attributes}}},"scopeSpans":{scope_spans}}}]}}"#)
}

#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Span {
    pub trace_id: String,
    pub span_id: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub parent_span_id: String,
    pub name: String,
    pub kind: u32,
    pub start_time_unix_nano: String,
    pub end_time_unix_nano: String,
    pub attributes: Vec<Attribute>,
    pub status: Status,
}

#[derive(Debug, Serialize, PartialEq)]
pub struct Status {
    pub code: u32,
}

impl Span {
    /// Finishes a span with what it reports and whether it reports success.
    pub fn with(mut self, attributes: Vec<Attribute>, ok: bool) -> Span {
        (self.attributes, self.status) = (attributes, status(ok));
        self
    }
}

#[derive(Debug, Serialize, PartialEq)]
pub struct Attribute {
    pub key: String,
    pub value: AnyValue,
}

#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub enum AnyValue {
    StringValue(String),
    IntValue(String),
    BoolValue(bool),
    ArrayValue(ArrayValue),
}

#[derive(Debug, Serialize, PartialEq)]
pub struct ArrayValue {
    pub values: Vec<AnyValue>,
}

pub fn text(key: &str, value: impl Into<String>) -> Attribute {
    Attribute { key: key.into(), value: AnyValue::StringValue(value.into()) }
}

pub fn number(key: &str, value: u64) -> Attribute {
    Attribute { key: key.into(), value: AnyValue::IntValue(value.to_string()) }
}

pub fn flag(key: &str, value: bool) -> Attribute {
    Attribute { key: key.into(), value: AnyValue::BoolValue(value) }
}

pub fn list(key: &str, values: impl IntoIterator<Item = String>) -> Attribute {
    let values = values.into_iter().map(AnyValue::StringValue).collect();
    Attribute { key: key.into(), value: AnyValue::ArrayValue(ArrayValue { values }) }
}

/// `STATUS_CODE_OK` is 1 in the encoding and `STATUS_CODE_ERROR` is 2.
pub fn status(ok: bool) -> Status {
    Status { code: if ok { 1 } else { 2 } }
}

/// Trace id of an episode: the first sixteen bytes of SHA-256 over its id.
///
/// Ids are derived rather than drawn from a random source because the whole
/// add-on is a pure function of the log: two runs over the same log must
/// produce the same bytes, and a random id would break that before any
/// other difference could.
pub fn trace_id(episode_id: &str) -> String {
    hex::encode(&Sha256::digest(episode_id.as_bytes())[..16])
}

/// Span id: the first eight bytes of SHA-256 over the episode id, the span
/// kind, and the sequence number of the event the span was built from.
/// Distinct spans within an episode differ in kind or in seq, so the three
/// together are unique inside the trace.
pub fn span_id(episode_id: &str, kind: &str, seq: u64) -> String {
    let material = format!("{episode_id}\u{0}{kind}\u{0}{seq}");
    hex::encode(&Sha256::digest(material.as_bytes())[..8])
}

/// Milliseconds since the epoch, as the log records them, in the
/// nanoseconds-as-string form the encoding requires. Negative times cannot
/// be represented and clamp to zero.
pub fn nanos(millis: i64) -> String {
    (millis.max(0) as u64 * 1_000_000).to_string()
}

/// A span with no attributes yet. `kind` is `SPAN_KIND_INTERNAL`, which is
/// 1: every span here describes work inside one process.
pub fn span(trace: &str, id: String, parent: String, name: String, start: i64, end: i64) -> Span {
    Span {
        trace_id: trace.to_string(),
        span_id: id,
        parent_span_id: parent,
        name,
        kind: 1,
        start_time_unix_nano: nanos(start),
        end_time_unix_nano: nanos(end.max(start)),
        attributes: Vec::new(),
        status: status(true),
    }
}

#[cfg(test)]
#[path = "otlp_test.rs"]
mod tests;
