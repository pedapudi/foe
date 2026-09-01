//! End-to-end tests: the payload against a golden file, emission against
//! real episode logs, and the self-check against a log engineered to slip
//! the known-value layer.

use foe_log::Event;
use std::ops::Deref;
use std::path::{Path, PathBuf};

struct ScratchDir(Option<tempfile::TempDir>);

impl ScratchDir {
    fn path(&self) -> &Path {
        self.0.as_ref().unwrap().path()
    }
}

impl Deref for ScratchDir {
    type Target = Path;

    fn deref(&self) -> &Self::Target {
        self.path()
    }
}

impl AsRef<Path> for ScratchDir {
    fn as_ref(&self) -> &Path {
        self.path()
    }
}

impl Drop for ScratchDir {
    fn drop(&mut self) {
        let Some(mut dir) = self.0.take() else { return };
        if std::thread::panicking() {
            eprintln!("retained failed test directory: {}", dir.path().display());
            dir.disable_cleanup(true);
            return;
        }
        let path = dir.path().to_path_buf();
        dir.close().unwrap_or_else(|error| panic!("failed to remove test directory {}: {error}", path.display()));
    }
}

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

/// The key the golden file was produced under. The scrubbed strings in a
/// payload depend on the local key, so a golden file needs a fixed one.
fn key() -> Vec<u8> {
    (0u8..32).collect()
}

fn events(path: &Path) -> Vec<Event> {
    let text = std::fs::read_to_string(path).unwrap_or_else(|error| panic!("{}: {error}", path.display()));
    text.lines().filter(|line| !line.trim().is_empty()).map(|line| serde_json::from_str(line).unwrap()).collect()
}

fn temp(name: &str) -> ScratchDir {
    ScratchDir(Some(tempfile::Builder::new().prefix(&format!("foe-telemetry-{name}-")).tempdir().unwrap()))
}

/// Byte-for-byte against a committed golden file.
///
/// The mapping from the log to OTLP JSON is written by hand, and hand-
/// written protobuf-JSON drifts in the places no type checker looks: field
/// casing, integers as strings, enum values as numbers, nanosecond
/// timestamps. A golden file pins all four at once.
///
/// This file was validated during development against a stock collector:
/// otelcol-contrib 0.159.0, posted to its OTLP/HTTP receiver at
/// `/v1/traces` with `Content-Type: application/json`. The collector
/// answered 200 with an empty `partialSuccess`, meaning it rejected no
/// span, and its debug exporter rendered all eight spans with the
/// attribute types this encoding intends: `Str`, `Int`, `Slice`, span kind
/// Internal, and status Ok or Error.
#[test]
fn the_payload_matches_the_golden_file() {
    let derived = foe_telemetry::emission(&events(&fixtures().join("clean/episode.jsonl")), "/logs/ep", key())
        .expect("the clean fixture emits");
    let golden = std::fs::read_to_string(fixtures().join("otel-golden.json")).unwrap();
    assert_eq!(derived.line(), golden, "the OTLP encoding changed; update tests/fixtures/otel-golden.json");
}

#[test]
fn the_golden_payload_carries_the_versions_a_consumer_needs_to_compare_two_of_them() {
    let golden = std::fs::read_to_string(fixtures().join("otel-golden.json")).unwrap();
    let payload: serde_json::Value = serde_json::from_str(&golden).unwrap();
    let resource = &payload["resourceSpans"][0]["resource"]["attributes"];
    let named = |key: &str| resource.as_array().unwrap().iter().any(|a| a["key"] == key);
    for key in ["service.name", "foe.schema.version", "foe.taxonomy.version", "foe.ruleset.version"] {
        assert!(named(key), "the golden payload has no {key}");
    }
}

#[test]
fn permission_denial_classification_reaches_the_tool_span() {
    let mut recorded = events(&fixtures().join("clean/episode.jsonl"));
    let result = recorded.iter_mut().find_map(|event| match &mut event.data {
        foe_log::EventData::ToolResult(result) if result.name == "bash" => Some(result),
        _ => None,
    });
    result.unwrap().value = serde_json::json!({"permission_denial": "possible"});
    let derived = foe_telemetry::emission(&recorded, "/logs/ep", key()).unwrap();
    let denial = derived
        .spans
        .iter()
        .flat_map(|span| &span.attributes)
        .find(|attribute| attribute.key == "foe.tool.permission_denial");
    assert!(
        matches!(denial.map(|a| &a.value), Some(foe_telemetry::otlp::AnyValue::StringValue(value)) if value == "possible")
    );
}

#[test]
fn no_free_text_beyond_the_two_permitted_fields_reaches_the_payload() {
    let derived = foe_telemetry::emission(&events(&fixtures().join("clean/episode.jsonl")), "/logs/ep", key())
        .expect("the clean fixture emits");
    let line = derived.line();
    // The task, the system prompt, the model's text, and the completed
    // outcome's value are all in the log and none of them are emitted.
    for text in ["Make the ledger tests pass", "You repair code", "Fixed the rounding", "cargo test --all passes"] {
        assert!(!line.contains(text), "the payload carries {text:?}");
    }
    // Nor does the workspace the episode ran in.
    assert!(!line.contains("rowan") && !line.contains("ledger"));
}

#[test]
fn the_self_check_refuses_a_log_it_cannot_fully_scrub() {
    let seeded = events(&fixtures().join("seeded/episode.jsonl"));
    let error = foe_telemetry::emission(&seeded, "/logs/ep", key()).err().expect("the seeded fixture must fail");
    assert_eq!(error.0, vec!["known user survived scrubbing in tool/result.subject seq 5"]);
    let message = error.to_string();
    assert_eq!(
        message,
        "scrub self-check failed, nothing emitted: known user survived scrubbing in tool/result.subject seq 5"
    );
}

#[test]
fn emission_refuses_the_seeded_log_and_writes_nothing() {
    let dir = temp("seeded");
    let out = dir.join("otel.jsonl");
    let refusal = foe_telemetry::emit_into(&fixtures().join("seeded"), &out, key());
    assert!(refusal.unwrap_err().contains("scrub self-check failed, nothing emitted"));
    assert!(!out.exists(), "a refused emission still wrote a capture file");
}

/// A line this build cannot read is not a tolerable loss. The scrubber
/// learns what to remove from the log, so the unreadable line may be the
/// one carrying the granted roots, and the known-value layer then silently
/// does nothing. This fixture is exactly that shape: an `episode/start`
/// written without a field this build requires, carrying grants naming a
/// user whose name appears in a later tool subject.
#[test]
fn emission_refuses_a_log_it_cannot_fully_read_and_writes_nothing() {
    let dir = temp("skewed");
    let out = dir.join("otel.jsonl");
    let complaint = foe_telemetry::emit_into(&fixtures().join("skewed"), &out, key()).unwrap_err();
    assert!(complaint.contains("1 line(s) this build cannot read"), "{complaint}");
    assert!(complaint.contains("scrubbing coverage cannot be guaranteed"), "{complaint}");
    assert!(complaint.contains("Nothing emitted."), "{complaint}");
    assert!(!out.exists(), "a refused emission still wrote a capture file");
}

/// The leak the refusal exists to prevent is visible when the skewed log is
/// read anyway: the grants never parsed, so the user name they would have
/// taught the scrubber reaches a subject untouched. `read_log` reports the
/// unreadable line for the caller to warn on, and the rendering shows what
/// a person needs to see to understand why emission refuses.
#[test]
fn the_skewed_log_reports_its_unreadable_line_and_shows_the_leak() {
    let (events, dir, unparsed) = foe_telemetry::read_log(&fixtures().join("skewed")).unwrap();
    assert_eq!(unparsed, 1);
    let derived = foe_telemetry::emission(&events, &dir.to_string_lossy(), key()).unwrap();
    assert!(foe_telemetry::preview(&derived).contains("marlow"));
}

#[test]
fn two_emissions_produce_byte_identical_output() {
    let dir = temp("determinism");
    let write = |name: &str| {
        let out = dir.join(name);
        foe_telemetry::emit_into(&fixtures().join("clean"), &out, key()).unwrap();
        std::fs::read(out).unwrap()
    };
    assert_eq!(write("first.jsonl"), write("second.jsonl"));
}

#[test]
fn emission_appends_one_object_per_episode() {
    let dir = temp("append");
    let out = dir.join("otel.jsonl");
    for _ in 0..3 {
        foe_telemetry::emit_into(&fixtures().join("clean"), &out, key()).unwrap();
    }
    let written = std::fs::read_to_string(&out).unwrap();
    assert_eq!(written.lines().count(), 3);
    assert!(written.lines().all(|line| serde_json::from_str::<serde_json::Value>(line).is_ok()));
}

/// What emission writes is `Emission::line`, so the payload a reader is
/// shown and the payload the capture holds cannot differ.
#[test]
fn the_capture_holds_exactly_the_rendered_line() {
    let dir = temp("line");
    let out = dir.join("otel.jsonl");
    foe_telemetry::emit_into(&fixtures().join("clean"), &out, key()).unwrap();
    let (events, dir, _) = foe_telemetry::read_log(&fixtures().join("clean")).unwrap();
    let derived = foe_telemetry::emission(&events, &dir.to_string_lossy(), key()).unwrap();
    assert_eq!(std::fs::read_to_string(&out).unwrap(), derived.line());
}

#[test]
fn preview_reports_the_bucket_its_evidence_the_totals_and_the_scrub_counts() {
    let (events, dir, _) = foe_telemetry::read_log(&fixtures().join("clean")).unwrap();
    let derived = foe_telemetry::emission(&events, &dir.to_string_lossy(), key()).unwrap();
    let printed = foe_telemetry::preview(&derived);
    assert!(printed.contains("ep_7c1a · replay/recorded-1 · completed/none"));
    assert!(printed.contains("category  programming → programming"));
    assert!(printed.contains("cargo test=testing"));
    assert!(printed.contains("3 model calls"));
    assert!(printed.contains("scrubbed  "));
    assert!(printed.contains("tool bash"));
    // The scrub report names types and counts, never a replaced value.
    assert!(!printed.contains("rowan") && !printed.contains("ops@example.org"));
}

/// The logs under `view/fixtures` are real episode logs kept for the
/// viewer. Running against them is the check that the crate reads what the
/// runtime actually writes, rather than what its own fixtures assume.
///
/// One of them holds a `workflow/node-end` of a shape the current event
/// types cannot read, so emission refuses it. That refusal is the correct
/// answer and the test accepts it, while requiring that every other
/// fixture emits and that no fixture fails for any other reason.
#[test]
fn emission_runs_against_every_real_viewer_fixture() {
    let fixture_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../view/fixtures");
    let scratch = temp("viewer");
    let out = scratch.join("otel.jsonl");
    let mut logs: Vec<PathBuf> = std::fs::read_dir(&fixture_dir)
        .unwrap()
        .filter_map(|entry| entry.ok().map(|e| e.path()))
        .filter(|path| path.extension().is_some_and(|e| e == "jsonl"))
        .collect();
    logs.sort();
    assert!(logs.len() >= 10, "the viewer fixtures moved");
    let mut emitted = 0;
    for log in &logs {
        match foe_telemetry::emit_into(log, &out, key()) {
            Ok(_) => emitted += 1,
            Err(complaint) => assert!(
                complaint.contains("this build cannot read"),
                "{} failed for a reason other than an unreadable line: {complaint}",
                log.display()
            ),
        }
    }
    assert!(emitted >= logs.len() - 1, "more than one viewer fixture holds a line this build cannot read");
    let written = std::fs::read_to_string(&out).unwrap();
    assert_eq!(written.lines().count(), emitted);
}
