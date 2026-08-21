use super::*;

fn span<'a>(original: &str, old: &str, new: &'a str) -> Span<'a> {
    let start = original.find(old).unwrap();
    Span {
        start,
        end: start + old.len(),
        new_text: new,
    }
}

#[test]
fn one_line_replacement_with_context() {
    let orig = "a\nb\nc\nd\ne\nf\ng\nh\n";
    let d = unified("f", orig, &[span(orig, "d", "D")]);
    assert_eq!(
        d.text,
        "--- a/f\n+++ b/f\n@@ -1,7 +1,7 @@\n a\n b\n c\n-d\n+D\n e\n f\n g\n"
    );
    assert_eq!((d.added, d.removed), (1, 1));
}

#[test]
fn two_spans_on_one_line_form_one_change() {
    let orig = "foo bar\n";
    let d = unified(
        "f",
        orig,
        &[span(orig, "foo", "FOO"), span(orig, "bar", "BAR")],
    );
    assert_eq!(
        d.text,
        "--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n-foo bar\n+FOO BAR\n"
    );
}

#[test]
fn distant_changes_become_separate_hunks_with_shifted_new_offsets() {
    let orig: String = (1..=20).map(|i| format!("l{i}\n")).collect();
    let d = unified(
        "f",
        &orig,
        &[span(&orig, "l2\n", "x\ny\n"), span(&orig, "l18\n", "")],
    );
    assert!(
        d.text.contains("@@ -1,5 +1,6 @@\n l1\n-l2\n+x\n+y\n l3\n"),
        "{}",
        d.text
    );
    assert!(
        d.text
            .contains("@@ -15,6 +16,5 @@\n l15\n l16\n l17\n-l18\n l19\n l20\n"),
        "{}",
        d.text
    );
    assert_eq!((d.added, d.removed), (2, 2));
}

#[test]
fn common_lines_are_trimmed_from_a_multiline_replacement() {
    let orig = "a\nb\nc\n";
    let d = unified("f", orig, &[span(orig, "a\nb\nc\n", "a\nB\nc\n")]);
    assert_eq!(
        d.text,
        "--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n"
    );
}

#[test]
fn deleting_every_line_reports_an_empty_new_side() {
    let d = unified("f", "only\n", &[span("only\n", "only\n", "")]);
    assert_eq!(d.text, "--- a/f\n+++ b/f\n@@ -1,1 +0,0 @@\n-only\n");
}
