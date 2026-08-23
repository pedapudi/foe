//! End-to-end tests: the payload against a golden file, the binary against
//! real episode logs, and the self-check against a log engineered to slip
//! the known-value layer.

use foe_log::Event;
use std::path::{Path, PathBuf};
use std::process::Command;

const BINARY: &str = env!("CARGO_BIN_EXE_foe-telemetry");

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

fn temp(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("foe-telemetry-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
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
fn the_binary_refuses_the_seeded_log_and_writes_nothing() {
    let out = temp("seeded").join("otel.jsonl");
    let run = Command::new(BINARY)
        .args(["emit", fixtures().join("seeded").to_str().unwrap(), "--out", out.to_str().unwrap()])
        .output()
        .unwrap();
    assert!(!run.status.success());
    assert!(String::from_utf8_lossy(&run.stderr).contains("scrub self-check failed, nothing emitted"));
    assert!(!out.exists(), "a refused emission still wrote a capture file");
}

/// A line this build cannot read is not a tolerable loss. The scrubber
/// learns what to remove from the log, so the unreadable line may be the
/// one carrying the granted roots, and the known-value layer then silently
/// does nothing. This fixture is exactly that shape: an `episode/start`
/// written without a field this build requires, carrying grants naming a
/// user whose name appears in a later tool subject.
#[test]
fn emit_refuses_a_log_it_cannot_fully_read_and_writes_nothing() {
    let out = temp("skewed").join("otel.jsonl");
    let run = Command::new(BINARY)
        .args(["emit", fixtures().join("skewed").to_str().unwrap(), "--out", out.to_str().unwrap()])
        .output()
        .unwrap();
    assert!(!run.status.success());
    let complaint = String::from_utf8_lossy(&run.stderr);
    assert!(complaint.contains("1 line(s) this build cannot read"), "{complaint}");
    assert!(complaint.contains("scrubbing coverage cannot be guaranteed"), "{complaint}");
    assert!(complaint.contains("Nothing emitted."), "{complaint}");
    assert!(!out.exists(), "a refused emission still wrote a capture file");
}

/// Preview still runs, because seeing what the log holds is how a person
/// finds out why it cannot be read. It says up front that emission refuses
/// it, so nothing here is mistaken for what would be written.
#[test]
fn preview_of_a_log_it_cannot_fully_read_leads_with_a_warning() {
    let out = temp("skewed-preview").join("otel.jsonl");
    let run = Command::new(BINARY)
        .args(["preview", fixtures().join("skewed").to_str().unwrap(), "--out", out.to_str().unwrap()])
        .output()
        .unwrap();
    assert!(run.status.success());
    let warning = String::from_utf8_lossy(&run.stderr);
    assert!(warning.starts_with("!! "), "{warning}");
    assert!(warning.contains("scrubbing coverage cannot be guaranteed"), "{warning}");
    assert!(warning.contains("refuses this log"), "{warning}");
    // The leak the refusal exists to prevent is visible in the preview: the
    // user name reaches the subject because the grants never parsed.
    assert!(String::from_utf8_lossy(&run.stdout).contains("marlow"));
}

#[test]
fn two_runs_of_emit_produce_byte_identical_output() {
    let dir = temp("determinism");
    let log = fixtures().join("clean");
    let run = |name: &str| {
        let out = dir.join(name);
        let status = Command::new(BINARY)
            .args(["emit", log.to_str().unwrap(), "--out", out.to_str().unwrap()])
            .status()
            .unwrap();
        assert!(status.success());
        std::fs::read(out).unwrap()
    };
    assert_eq!(run("first.jsonl"), run("second.jsonl"));
}

#[test]
fn emit_appends_one_object_per_episode() {
    let out = temp("append").join("otel.jsonl");
    let log = fixtures().join("clean");
    for _ in 0..3 {
        let status = Command::new(BINARY)
            .args(["emit", log.to_str().unwrap(), "--out", out.to_str().unwrap()])
            .status()
            .unwrap();
        assert!(status.success());
    }
    let written = std::fs::read_to_string(&out).unwrap();
    assert_eq!(written.lines().count(), 3);
    assert!(written.lines().all(|line| serde_json::from_str::<serde_json::Value>(line).is_ok()));
}

#[test]
fn preview_prints_what_emit_would_write() {
    let out = temp("preview").join("otel.jsonl");
    let log = fixtures().join("clean");
    let capture = |command: &str, json: bool| {
        let mut args = vec![command, log.to_str().unwrap(), "--out", out.to_str().unwrap()];
        if json {
            args.push("--json");
        }
        Command::new(BINARY).args(&args).output().unwrap()
    };
    let previewed = capture("preview", true);
    assert!(previewed.status.success());
    assert!(capture("emit", false).status.success());
    assert_eq!(String::from_utf8_lossy(&previewed.stdout), std::fs::read_to_string(&out).unwrap());
}

#[test]
fn preview_reports_the_bucket_its_evidence_the_totals_and_the_scrub_counts() {
    let out = temp("readable").join("otel.jsonl");
    let run = Command::new(BINARY)
        .args(["preview", fixtures().join("clean").to_str().unwrap(), "--out", out.to_str().unwrap()])
        .output()
        .unwrap();
    assert!(run.status.success());
    let printed = String::from_utf8_lossy(&run.stdout);
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
fn the_binary_runs_against_every_real_viewer_fixture() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../view/fixtures");
    let out = temp("viewer").join("otel.jsonl");
    let mut logs: Vec<PathBuf> = std::fs::read_dir(&dir)
        .unwrap()
        .filter_map(|entry| entry.ok().map(|e| e.path()))
        .filter(|path| path.extension().is_some_and(|e| e == "jsonl"))
        .collect();
    logs.sort();
    assert!(logs.len() >= 10, "the viewer fixtures moved");
    let mut emitted = 0;
    for log in &logs {
        let run = Command::new(BINARY)
            .args(["emit", log.to_str().unwrap(), "--out", out.to_str().unwrap()])
            .output()
            .unwrap();
        let complaint = String::from_utf8_lossy(&run.stderr);
        match run.status.success() {
            true => emitted += 1,
            false => assert!(
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
