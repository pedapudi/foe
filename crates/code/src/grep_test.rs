use super::*;
use crate::testing::{ctx, Fixture};
use foe_core::{CapError, ReadEntry, Reader};
use std::path::Path;
use std::sync::Arc;

async fn grep(fx: &Fixture, args: serde_json::Value) -> ToolValue {
    let v = Grep::new().call(args, &ctx(fx)).await;
    assert!(!v.is_error, "{v:?}");
    v
}

fn tree() -> Fixture {
    let fx = Fixture::new();
    fx.write("src/b.rs", "fn beta() {}\nfn alpha() {}\n");
    fx.write("src/a.rs", "fn alpha() {}\n// alpha again\n");
    fx.write("notes.md", "alpha in prose\n");
    fx.write("build/out.rs", "fn alpha() {}\n");
    fx.write(".gitignore", "build/\n");
    fx
}

struct FailingReader {
    inner: Arc<dyn Reader>,
    fail_open: Option<PathBuf>,
    fail_dir: Option<PathBuf>,
}

impl Reader for FailingReader {
    fn open(&self, path: &Path) -> Result<Box<dyn std::io::Read + Send>, CapError> {
        if self.fail_open.as_deref() == Some(path) {
            return Err(CapError::Invalid(format!("{}: planted open failure", path.display())));
        }
        self.inner.open(path)
    }
    fn read(&self, path: &Path) -> Result<Vec<u8>, CapError> {
        self.inner.read(path)
    }
    fn metadata(&self, path: &Path) -> Result<std::fs::Metadata, CapError> {
        self.inner.metadata(path)
    }
    fn read_dir(&self, path: &Path) -> Result<Vec<ReadEntry>, CapError> {
        if self.fail_dir.as_deref() == Some(path) {
            return Err(CapError::Invalid(format!("{}: planted traversal failure", path.display())));
        }
        self.inner.read_dir(path)
    }
    fn roots(&self) -> &[PathBuf] {
        self.inner.roots()
    }
}

#[tokio::test]
async fn matches_are_sorted_by_path_then_line_and_honor_gitignore() {
    let fx = tree();
    let v = grep(&fx, json!({"pattern": "alpha"})).await;
    let r = v.rendered.unwrap();
    let lines: Vec<&str> = r.lines().collect();
    assert_eq!(lines[0], "4 matches in 3 files under .");
    assert_eq!(
        &lines[1..],
        [
            "notes.md:1:alpha in prose",
            "src/a.rs:1:fn alpha() {}",
            "src/a.rs:2:// alpha again",
            "src/b.rs:2:fn alpha() {}"
        ]
    );
    assert_eq!(v.value["matches"], 4);
    assert_eq!(v.value["failed_files"], 0);
    assert_eq!(v.value["complete"], true, "a search under the collection limit is complete");
    assert!(v.value["searched_files"].as_u64().unwrap() >= 3, "{}", v.value["searched_files"]);
    assert!(!r.contains("build/out.rs"));
}

/// docs/tools.md `grep`: failures in descriptor-backed enumeration and file
/// opens make the result incomplete and remain distinguishable in its value.
#[tokio::test]
async fn traversal_and_open_failures_are_never_reported_as_complete() {
    let fx = Fixture::new();
    fx.write("good.txt", "needle\n");
    fx.write("bad.txt", "needle\n");
    let mut c = ctx(&fx);
    let inner = c.reader.take().unwrap();
    c.reader = Some(Arc::new(FailingReader { inner, fail_open: Some(fx.root().join("bad.txt")), fail_dir: None }));
    let value = Grep::new().call(json!({"pattern": "needle"}), &c).await;
    assert_eq!(value.value["matches"], 1);
    assert_eq!(value.value["failed_files"], 1);
    assert_eq!(value.value["traversal_failures"], 0);
    assert_eq!(value.value["complete"], false);
    assert!(value.value["first_failure"].as_str().unwrap().contains("planted open failure"));

    let mut c = ctx(&fx);
    let inner = c.reader.take().unwrap();
    c.reader = Some(Arc::new(FailingReader { inner, fail_open: None, fail_dir: Some(fx.root()) }));
    let value = Grep::new().call(json!({"pattern": "needle"}), &c).await;
    assert_eq!(value.value["failed_files"], 0);
    assert_eq!(value.value["traversal_failures"], 1);
    assert_eq!(value.value["complete"], false);
    assert!(value.rendered.unwrap().contains("planted traversal failure"));
}

#[tokio::test]
async fn nested_ignore_files_apply_from_the_directory_that_contains_them() {
    let fx = Fixture::new();
    fx.write("src/.gitignore", "generated/\n");
    fx.write("src/kept.txt", "needle\n");
    fx.write("src/generated/ignored.txt", "needle\n");
    fx.write("other/generated/kept.txt", "needle\n");
    let value = grep(&fx, json!({"pattern": "needle"})).await;
    let rendered = value.rendered.unwrap();
    assert!(rendered.contains("src/kept.txt"), "{rendered}");
    assert!(rendered.contains("other/generated/kept.txt"), "{rendered}");
    assert!(!rendered.contains("src/generated/ignored.txt"), "{rendered}");
}

/// docs/tools.md `grep`: file content is streamed through the reader, so a
/// search does not allocate a complete file before matching it.
#[tokio::test]
async fn streams_files_without_requesting_whole_file_buffers() {
    let fx = tree();
    let v = grep(&fx, json!({"pattern": "alpha"})).await;
    assert_eq!(v.value["matches"], 4);
    assert_eq!(fx.whole_reads(), 0);
}

/// docs/tools.md `grep`: the search stops after `GREP_COLLECT_MAX` matches.
/// The result then says so, both in the value and in the rendering, so the
/// model does not read a partial answer as the whole one.
#[tokio::test]
async fn a_search_that_reaches_the_collection_limit_says_it_stopped() {
    let fx = Fixture::new();
    let body: String = (0..GREP_COLLECT_MAX + 100).map(|n| format!("needle {n}\n")).collect();
    fx.write("many.txt", &body);
    let v = grep(&fx, json!({"pattern": "needle", "limit": 1})).await;
    assert_eq!(v.value["complete"], false);
    assert_eq!(v.value["matches"], GREP_COLLECT_MAX);
    assert!(v.rendered.unwrap().contains(&format!("search stopped at {GREP_COLLECT_MAX} matches")));
}

/// docs/tools.md `grep`: the line-search buffer has a fixed ceiling. A line
/// beyond it makes the partial result explicit rather than exhausting memory.
#[tokio::test]
async fn a_line_beyond_the_search_buffer_limit_is_reported_as_unsearched() {
    let fx = Fixture::new();
    fx.write("huge.txt", &"x".repeat(GREP_SEARCH_BUFFER_MAX_BYTES + 1));
    let v = grep(&fx, json!({"pattern": "needle"})).await;
    assert_eq!(v.value["complete"], false);
    assert_eq!(v.value["failed_files"], 1);
    assert!(v.subject.unwrap().ends_with("; incomplete"));
    assert!(v.rendered.unwrap().contains("configured allocation limit"));
}

#[tokio::test]
async fn context_lines_have_a_collection_limit() {
    let fx = Fixture::new();
    let mut body = "context\n".repeat(GREP_HIT_COLLECT_MAX);
    body.push_str("needle\n");
    fx.write("context.txt", &body);
    let v = grep(&fx, json!({"pattern": "needle", "context": GREP_HIT_COLLECT_MAX})).await;
    assert_eq!(v.value["complete"], false);
    assert_eq!(v.value["hits"].as_array().unwrap().len(), GREP_HIT_COLLECT_MAX);
    assert!(v.rendered.unwrap().contains(&format!("search stopped at {GREP_HIT_COLLECT_MAX} result lines")));
}

#[tokio::test]
async fn glob_path_literal_and_ignore_case_narrow_the_search() {
    let fx = tree();
    let v = grep(&fx, json!({"pattern": "alpha", "glob": "*.md"})).await;
    assert_eq!(v.value["matches"], 1);
    let v = grep(&fx, json!({"pattern": "ALPHA", "path": "src", "ignore_case": true})).await;
    assert_eq!(v.value["matches"], 3);
    let v = grep(&fx, json!({"pattern": "alpha() {}", "literal": true, "path": "src/b.rs"})).await;
    assert_eq!(v.value["matches"], 1);
    let v = Grep::new().call(json!({"pattern": "alpha() {}", "path": "src/b.rs"}), &ctx(&fx)).await;
    assert!(v.is_error, "without literal the same pattern is an invalid regex");
}

#[tokio::test]
async fn limit_caps_the_rendering_while_the_value_keeps_every_match() {
    let fx = tree();
    let v = grep(&fx, json!({"pattern": "alpha", "limit": 2})).await;
    let r = v.rendered.unwrap();
    assert!(r.starts_with("4 matches in 3 files under .; showing the first 2."), "{r}");
    assert_eq!(r.lines().count(), 3);
    assert_eq!(v.value["hits"].as_array().unwrap().len(), 4);
}

#[tokio::test]
async fn context_lines_are_marked_and_long_lines_are_clamped() {
    let fx = Fixture::new();
    let long = format!("needle {}", "x".repeat(600));
    fx.write("c.txt", &format!("before\n{long}\nafter\n"));
    let v = grep(&fx, json!({"pattern": "needle", "context": 1})).await;
    let r = v.rendered.unwrap();
    assert!(r.contains("c.txt:1-before\n"), "{r}");
    assert!(r.contains("c.txt:3-after"), "{r}");
    let dropped = long.chars().count() - GREP_LINE_MAX_CHARS;
    assert!(r.contains(&format!(" [clamped at {GREP_LINE_MAX_CHARS} chars; {dropped} more]")), "{r}");
}

#[tokio::test]
async fn bad_patterns_and_unreadable_roots_are_errors() {
    let fx = tree();
    let v = Grep::new().call(json!({"pattern": "("}), &ctx(&fx)).await;
    assert!(v.is_error);
    assert!(v.rendered.unwrap().contains("invalid pattern"));
    let v = Grep::new().call(json!({"pattern": "a", "path": "/etc"}), &ctx(&fx)).await;
    assert!(v.is_error);
}

/// What the call found rather than what it was asked for: the pattern is
/// in the arguments already, and only the tool knows the count.
#[tokio::test]
async fn states_how_many_matches_it_found() {
    let fx = Fixture::new();
    fx.write("a.txt", "alpha\nbeta\n");
    fx.write("b.txt", "alpha\n");
    let v = grep(&fx, json!({"pattern": "alpha"})).await;
    assert_eq!(v.subject.as_deref(), Some("2 match(es) in 2 file(s) under ."));
}
