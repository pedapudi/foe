use super::*;
use crate::testing::{ctx, Fixture};

async fn read(fx: &Fixture, args: serde_json::Value) -> ToolValue {
    Read::new().call(args, &ctx(fx)).await
}

#[tokio::test]
async fn numbers_lines_and_reports_totals() {
    let fx = Fixture::new();
    fx.write("a.txt", "one\ntwo\nthree\n");
    let v = read(&fx, json!({"path": "a.txt"})).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(v.rendered.as_deref(), Some("1\tone\n2\ttwo\n3\tthree\n"));
    assert_eq!(v.value["total_lines"], 3);
    assert_eq!(v.value["shown"], 3);
    assert_eq!(v.value["truncated"], false);
}

#[tokio::test]
async fn truncates_at_the_line_limit_and_names_the_next_offset() {
    let fx = Fixture::new();
    let text: String = (1..=8431).map(|i| format!("line {i}\n")).collect();
    fx.write("big.txt", &text);
    let v = read(&fx, json!({"path": "big.txt"})).await;
    let r = v.rendered.unwrap();
    assert!(r.ends_with("[Showing lines 1-2000 of 8431. Use offset=2001 to continue.]"), "{r}");
    assert!(r.starts_with("1\tline 1\n"));
    assert_eq!(v.value["shown"], 2000);
    assert_eq!(v.value["truncated"], true);

    let v = read(&fx, json!({"path": "big.txt", "offset": 2001, "limit": 10})).await;
    let r = v.rendered.unwrap();
    assert!(r.starts_with("2001\tline 2001\n"));
    assert!(r.ends_with("[Showing lines 2001-2010 of 8431. Use offset=2011 to continue.]"), "{r}");

    let v = read(&fx, json!({"path": "big.txt", "offset": 8431})).await;
    assert_eq!(v.rendered.as_deref(), Some("8431\tline 8431\n"));
    assert_eq!(v.value["truncated"], false);
}

#[tokio::test]
async fn truncates_at_the_character_limit_without_splitting_a_line() {
    let fx = Fixture::new();
    let line = format!("{}\n", "é".repeat(1000));
    fx.write("wide.txt", &line.repeat(100));
    let v = read(&fx, json!({"path": "wide.txt"})).await;
    let shown = v.value["shown"].as_u64().unwrap() as usize;
    let width = line.chars().count();
    assert!(shown * width <= OUTPUT_MAX_CHARS);
    assert!((shown + 1) * width > OUTPUT_MAX_CHARS);
    assert!(v.rendered.unwrap().contains(&format!("Use offset={} to continue", shown + 1)));
}

#[tokio::test]
async fn a_single_line_over_the_character_limit_suggests_bash() {
    let fx = Fixture::new();
    fx.write("one.txt", &"x".repeat(OUTPUT_MAX_CHARS + 10));
    let v = read(&fx, json!({"path": "one.txt"})).await;
    assert!(!v.is_error);
    assert_eq!(v.value["shown"], 0);
    let r = v.rendered.unwrap();
    assert!(r.contains(&format!("is {} characters", OUTPUT_MAX_CHARS + 10)), "{r}");
    assert!(r.contains("sed -n '1p' 'one.txt' | head -c"), "{r}");
}

#[tokio::test]
async fn binary_files_and_bad_offsets_are_errors() {
    let fx = Fixture::new();
    fx.write_bytes("bin", b"abc\0def");
    let v = read(&fx, json!({"path": "bin"})).await;
    assert!(v.is_error);
    assert!(v.rendered.unwrap().contains("binary file (7 bytes"));
    fx.write_bytes("latin1", b"caf\xe9\n");
    let v = read(&fx, json!({"path": "latin1"})).await;
    assert!(v.is_error);
    assert!(v.rendered.unwrap().contains("invalid UTF-8"));
    fx.write("short.txt", "a\n");
    let v = read(&fx, json!({"path": "short.txt", "offset": 5})).await;
    assert!(v.is_error);
    assert!(v.rendered.unwrap().contains("offset 5 is past the end"));
    let v = read(&fx, json!({"path": "missing.txt"})).await;
    assert!(v.is_error);
}

#[tokio::test]
async fn empty_files_and_crlf_files_render() {
    let fx = Fixture::new();
    fx.write("empty", "");
    let v = read(&fx, json!({"path": "empty"})).await;
    assert!(!v.is_error);
    assert_eq!(v.value["total_lines"], 0);
    fx.write("crlf", "a\r\nb\r\n");
    let v = read(&fx, json!({"path": "crlf"})).await;
    assert_eq!(v.rendered.as_deref(), Some("1\ta\n2\tb\n"));
}

#[tokio::test]
async fn paths_outside_the_roots_are_denied() {
    let fx = Fixture::new();
    let v = read(&fx, json!({"path": "/etc/hostname"})).await;
    assert!(v.is_error);
    assert!(v.rendered.unwrap().contains("outside every granted root"));
}

/// docs/tools.md "read": the file is consumed as a stream, so the tool
/// never asks the reader for a whole-file buffer, and the count of lines
/// past the window is still exact.
#[tokio::test]
async fn streams_the_file_without_a_whole_file_buffer() {
    let fx = Fixture::new();
    let text: String = (1..=20_000).map(|i| format!("line {i}\n")).collect();
    assert!(text.len() > 2 * READ_BUFFER_BYTES, "the fixture must span several stream buffers");
    fx.write("big.txt", &text);
    let v = read(&fx, json!({"path": "big.txt", "offset": 9000, "limit": 3})).await;
    assert_eq!(fx.whole_reads(), 0, "read consumed the stream rather than a whole-file buffer");
    let r = v.rendered.unwrap();
    assert!(r.starts_with("9000\tline 9000\n9001\tline 9001\n9002\tline 9002\n"), "{r}");
    assert!(r.ends_with("[Showing lines 9000-9002 of 20000. Use offset=9003 to continue.]"), "{r}");
    assert_eq!(v.value["total_lines"], 20_000);
    let v = read(&fx, json!({"path": "big.txt", "offset": 20_001})).await;
    assert!(v.is_error);
    assert!(v.rendered.unwrap().contains("offset 20001 is past the end of big.txt, which has 20000 lines"));
}

/// A multibyte character cut by the stream buffer boundary is reassembled
/// before validation, so a boundary can never make a text file binary, and
/// a line longer than the character limit reports its complete character
/// count without being retained.
#[tokio::test]
async fn utf8_sequences_split_across_the_buffer_boundary_survive() {
    let fx = Fixture::new();
    // The two bytes of é fall on either side of the buffer boundary.
    let mut text = "x".repeat(READ_BUFFER_BYTES - 1);
    text.push_str("é\ntail é line\n");
    fx.write("wide.txt", &text);
    let v = read(&fx, json!({"path": "wide.txt", "offset": 2})).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(v.rendered.as_deref(), Some("2\ttail é line\n"));
    assert_eq!(v.value["total_lines"], 2);
    let v = read(&fx, json!({"path": "wide.txt", "offset": 1, "limit": 1})).await;
    assert!(!v.is_error, "{v:?}");
    let r = v.rendered.unwrap();
    assert!(r.contains(&format!("is {} characters", READ_BUFFER_BYTES)), "{r}");
}

/// A NUL byte after the displayed window still marks the file binary,
/// because the whole stream is scanned rather than the window alone.
#[tokio::test]
async fn a_nul_byte_after_the_window_is_still_binary() {
    let fx = Fixture::new();
    let mut bytes: Vec<u8> = (1..=12_000).flat_map(|i| format!("line {i}\n").into_bytes()).collect();
    assert!(bytes.len() > READ_BUFFER_BYTES, "the NUL must sit beyond the first stream buffer");
    bytes.push(0);
    let size = bytes.len();
    fx.write_bytes("late-nul.txt", &bytes);
    let v = read(&fx, json!({"path": "late-nul.txt", "limit": 2})).await;
    assert!(v.is_error);
    assert!(v.rendered.unwrap().contains(&format!("binary file ({size} bytes")));
}

/// CRLF endings render without the carriage return even when the buffer
/// boundary falls between the carriage return and the line break.
#[tokio::test]
async fn crlf_split_across_the_buffer_boundary_renders_clean() {
    let fx = Fixture::new();
    // The carriage return is the last byte of the first buffer and the line
    // break the first byte of the next.
    let mut text = "x".repeat(READ_BUFFER_BYTES - 1);
    text.push_str("\r\nok\r\n");
    fx.write("split.txt", &text);
    let v = read(&fx, json!({"path": "split.txt", "offset": 2})).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(v.rendered.as_deref(), Some("2\tok\n"));
    assert_eq!(v.value["total_lines"], 2);
    // The same boundary split on a line too long to show: the reported
    // character count excludes the carriage return.
    let mut text = "y".repeat(10);
    text.push('\n');
    text.push_str(&"z".repeat(READ_BUFFER_BYTES - 12));
    text.push_str("\r\nend\n");
    fx.write("split2.txt", &text);
    let v = read(&fx, json!({"path": "split2.txt", "offset": 2, "limit": 1})).await;
    assert!(!v.is_error, "{v:?}");
    let r = v.rendered.unwrap();
    assert!(r.contains(&format!("is {} characters", READ_BUFFER_BYTES - 12)), "{r}");
    let v = read(&fx, json!({"path": "split2.txt", "offset": 3})).await;
    assert_eq!(v.rendered.as_deref(), Some("3\tend\n"));
}

/// What a person reads in a list: the file and the span actually shown,
/// which the arguments alone do not give, because a limit and the end of
/// the file both cut it.
#[tokio::test]
async fn states_the_file_and_the_span_it_showed() {
    let fx = Fixture::new();
    fx.write("a.txt", "one\ntwo\nthree\nfour\n");
    let v = read(&fx, json!({"path": "a.txt", "offset": 2, "limit": 2})).await;
    assert_eq!(v.subject.as_deref(), Some("a.txt lines 2\u{2013}3 of 4"));

    let v = read(&fx, json!({"path": "empty.txt"})).await;
    assert_eq!(v.subject.as_deref(), Some("read: empty.txt: No such file or directory (os error 2)"));
}
