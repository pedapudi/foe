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
