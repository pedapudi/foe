use super::*;
use crate::testing::{ctx, Fixture};

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
    assert!(!r.contains("build/out.rs"));
}

#[tokio::test]
async fn glob_path_literal_and_ignore_case_narrow_the_search() {
    let fx = tree();
    let v = grep(&fx, json!({"pattern": "alpha", "glob": "*.md"})).await;
    assert_eq!(v.value["matches"], 1);
    let v = grep(
        &fx,
        json!({"pattern": "ALPHA", "path": "src", "ignore_case": true}),
    )
    .await;
    assert_eq!(v.value["matches"], 3);
    let v = grep(
        &fx,
        json!({"pattern": "alpha() {}", "literal": true, "path": "src/b.rs"}),
    )
    .await;
    assert_eq!(v.value["matches"], 1);
    let v = Grep::new()
        .call(
            json!({"pattern": "alpha() {}", "path": "src/b.rs"}),
            &ctx(&fx),
        )
        .await;
    assert!(
        v.is_error,
        "without literal the same pattern is an invalid regex"
    );
}

#[tokio::test]
async fn limit_caps_the_rendering_while_the_value_keeps_every_match() {
    let fx = tree();
    let v = grep(&fx, json!({"pattern": "alpha", "limit": 2})).await;
    let r = v.rendered.unwrap();
    assert!(
        r.starts_with("4 matches in 3 files under .; showing the first 2."),
        "{r}"
    );
    assert_eq!(r.lines().count(), 3);
    assert_eq!(v.value["hits"].as_array().unwrap().len(), 4);
}

#[tokio::test]
async fn context_lines_are_marked_and_long_lines_are_clamped() {
    let fx = Fixture::new();
    fx.write(
        "c.txt",
        &format!("before\nneedle {}\nafter\n", "x".repeat(600)),
    );
    let v = grep(&fx, json!({"pattern": "needle", "context": 1})).await;
    let r = v.rendered.unwrap();
    assert!(r.contains("c.txt:1-before\n"), "{r}");
    assert!(r.contains("c.txt:3-after"), "{r}");
    assert!(r.contains(" [clamped at 500 chars; 107 more]"), "{r}");
}

#[tokio::test]
async fn bad_patterns_and_unreadable_roots_are_errors() {
    let fx = tree();
    let v = Grep::new().call(json!({"pattern": "("}), &ctx(&fx)).await;
    assert!(v.is_error);
    assert!(v.rendered.unwrap().contains("invalid pattern"));
    let v = Grep::new()
        .call(json!({"pattern": "a", "path": "/etc"}), &ctx(&fx))
        .await;
    assert!(v.is_error);
}
