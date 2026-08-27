use super::*;
use crate::testing::{ctx, Fixture};

async fn edit(fx: &Fixture, args: serde_json::Value) -> ToolValue {
    Edit::new().call(args, &ctx(fx)).await
}

fn replace(old: &str, new: &str) -> serde_json::Value {
    json!({"old_text": old, "new_text": new})
}

#[tokio::test]
async fn applies_multiple_edits_against_the_original_in_one_write() {
    let fx = Fixture::new();
    fx.write("f.txt", "alpha\nbeta\ngamma\n");
    let v = edit(&fx, json!({"path": "f.txt", "edits": [replace("gamma", "GAMMA"), replace("alpha", "beta")]})).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(fx.read("f.txt"), "beta\nbeta\nGAMMA\n");
    assert_eq!(v.value["edits"], 2);
    assert_eq!(v.value["added"], 2);
    assert_eq!(v.value["removed"], 2);
    let r = v.rendered.unwrap();
    assert!(r.starts_with("edited f.txt: 2 edit(s), +2 -2 lines\n--- a/f.txt\n+++ b/f.txt\n"), "{r}");
    assert!(r.contains("-alpha\n+beta\n beta\n-gamma\n+GAMMA\n"), "{r}");
    assert_eq!(fx.writes(), 1);
}

#[tokio::test]
async fn creates_a_missing_or_empty_file_with_one_empty_match() {
    let fx = Fixture::new();
    let v = edit(&fx, json!({"path": "new.txt", "edits": [replace("", "one\ntwo\n")]})).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(fx.read("new.txt"), "one\ntwo\n");
    assert_eq!(v.value["added"], 2);
    assert!(v.rendered.as_deref().unwrap().contains("@@ -0,0 +1,2 @@\n+one\n+two\n"));

    fx.write("empty.txt", "");
    let v = edit(&fx, json!({"path": "empty.txt", "edits": [replace("", "content\n")]})).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(fx.read("empty.txt"), "content\n");
    assert_eq!(fx.writes(), 2);
}

#[tokio::test]
async fn empty_matches_cannot_overwrite_text_or_join_other_edits() {
    let fx = Fixture::new();
    fx.write("f.txt", "existing\n");
    let v = edit(&fx, json!({"path": "f.txt", "edits": [replace("", "replacement\n")]})).await;
    assert!(v.is_error);
    assert!(v.rendered.as_deref().unwrap().contains("requires a missing or empty file"));

    fx.write("empty.txt", "");
    let v = edit(&fx, json!({"path": "empty.txt", "edits": [replace("", "one\n"), replace("missing", "two\n")]})).await;
    assert!(v.is_error);
    assert!(v.rendered.as_deref().unwrap().contains("requires exactly one edit"));
    assert_eq!(fx.read("f.txt"), "existing\n");
    assert_eq!(fx.read("empty.txt"), "");
    assert_eq!(fx.writes(), 0);
}

#[tokio::test]
async fn duplicate_and_missing_matches_are_rejected_by_index_and_count() {
    let fx = Fixture::new();
    fx.write("f.txt", "x = 1\nx = 1\n");
    let v = edit(&fx, json!({"path": "f.txt", "edits": [replace("x = 1", "x = 2")]})).await;
    assert!(v.is_error);
    assert!(v.rendered.as_deref().unwrap().starts_with("edits[0]: old_text occurs 2 times"));
    let v = edit(&fx, json!({"path": "f.txt", "edits": [replace("x = 1\nx = 1", "ok"), replace("zzz", "y")]})).await;
    assert!(v.is_error);
    assert!(v.rendered.as_deref().unwrap().starts_with("edits[1]: old_text occurs 0 times"));
    assert_eq!(fx.read("f.txt"), "x = 1\nx = 1\n");
    assert_eq!(fx.writes(), 0);
}

#[tokio::test]
async fn overlapping_edits_are_rejected() {
    let fx = Fixture::new();
    fx.write("f.txt", "one two three\n");
    let v =
        edit(&fx, json!({"path": "f.txt", "edits": [replace("one two", "1 2"), replace("two three", "2 3")]})).await;
    assert!(v.is_error);
    // The rejection names both edits and the file, so the model can tell
    // which pair to merge. Its wording is not the subject.
    let rendered = v.rendered.unwrap();
    for part in ["edits[0]", "edits[1]", "f.txt"] {
        assert!(rendered.contains(part), "the rejection does not name {part}: {rendered}");
    }
    assert_eq!(fx.read("f.txt"), "one two three\n", "a rejected edit leaves the file alone");
    assert_eq!(fx.writes(), 0);
    let v = edit(&fx, json!({"path": "f.txt", "edits": [replace("one ", "1 "), replace("two", "2")]})).await;
    assert!(!v.is_error, "touching spans are allowed");
    assert_eq!(fx.read("f.txt"), "1 2 three\n");
}

#[tokio::test]
async fn noop_edits_are_an_error() {
    let fx = Fixture::new();
    fx.write("f.txt", "same\n");
    let v = edit(&fx, json!({"path": "f.txt", "edits": [replace("same", "same")]})).await;
    assert!(v.is_error);
    assert!(v.rendered.unwrap().contains("unchanged"));
    assert_eq!(fx.writes(), 0);
}

#[tokio::test]
async fn bom_round_trips() {
    let fx = Fixture::new();
    fx.write("f.txt", "\u{feff}hello\n");
    let v = edit(&fx, json!({"path": "f.txt", "edits": [replace("hello", "bye")]})).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(fx.read("f.txt"), "\u{feff}bye\n");
}

#[tokio::test]
async fn crlf_round_trips_whether_or_not_old_text_uses_crlf() {
    let fx = Fixture::new();
    fx.write("f.txt", "a\r\nb\r\nc\r\n");
    let v = edit(&fx, json!({"path": "f.txt", "edits": [replace("a\nb\n", "A\nB\n")]})).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(fx.read("f.txt"), "A\r\nB\r\nc\r\n");
    let v = edit(&fx, json!({"path": "f.txt", "edits": [replace("B\r\nc", "x\r\ny")]})).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(fx.read("f.txt"), "A\r\nx\r\ny\r\n");
    fx.write("mixed.txt", "a\r\nb\nc\n");
    let v = edit(&fx, json!({"path": "mixed.txt", "edits": [replace("b\nc", "B\nC")]})).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(fx.read("mixed.txt"), "a\r\nB\nC\n", "mixed endings are left as they are");
}

#[tokio::test]
async fn missing_files_binary_files_and_denied_paths_are_errors() {
    let fx = Fixture::new();
    let v = edit(&fx, json!({"path": "nope.txt", "edits": [replace("a", "b")]})).await;
    assert!(v.is_error);
    fx.write_bytes("bin", b"\xff\xfe");
    let v = edit(&fx, json!({"path": "bin", "edits": [replace("a", "b")]})).await;
    assert!(v.is_error);
    assert!(v.rendered.unwrap().contains("not valid UTF-8"));
    let v = edit(&fx, json!({"path": "/etc/hostname", "edits": [replace("a", "b")]})).await;
    assert!(v.is_error);
    let v = edit(&fx, json!({"path": "f.txt", "edits": []})).await;
    assert!(v.is_error);
}

/// docs/tools.md "edit": the rendering shows the diff up to
/// `EDIT_DIFF_MAX_LINES` lines and one elision line beyond it; the
/// canonical value keeps the complete diff, so the bound costs the log
/// nothing.
#[tokio::test]
async fn a_large_diff_is_rendered_up_to_the_window_and_elided_beyond_it() {
    let fx = Fixture::new();
    let body: String = (1..=300).map(|i| format!("line {i}\n")).collect();
    let v = edit(&fx, json!({"path": "big.txt", "edits": [replace("", &body)]})).await;
    assert!(!v.is_error, "{v:?}");
    let canonical = v.value["diff"].as_str().unwrap();
    assert_eq!(canonical.lines().count(), 303, "two headers, one hunk line, and every added line");
    assert!(canonical.contains("+line 300\n"), "the canonical value keeps the complete diff");
    let r = v.rendered.unwrap();
    assert_eq!(r.lines().count(), 1 + EDIT_DIFF_MAX_LINES + 1, "the summary, the window, the elision line");
    assert!(r.contains("+line 197\n"), "the window holds the head of the diff");
    assert!(!r.contains("+line 198\n"));
    assert!(
        r.ends_with(
            "[Diff cut at 200 lines: 103 added and 0 removed lines omitted. \
             Every edit was applied; read the file to see the result.]\n"
        ),
        "{r}"
    );

    let v = edit(&fx, json!({"path": "big.txt", "edits": [replace(body.as_str(), "solo\n")]})).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(fx.read("big.txt"), "solo\n", "the write applied in full despite the elided rendering");
    let r = v.rendered.unwrap();
    assert!(r.contains("1 added and 103 removed lines omitted"), "{r}");
}

/// The line the rendering already leads with, hoisted so a reader of a
/// list gets it without the diff under it.
#[tokio::test]
async fn states_the_file_and_what_it_changed() {
    let fx = Fixture::new();
    fx.write("a.txt", "one\ntwo\n");
    let v = edit(&fx, json!({"path": "a.txt", "edits": [{"old_text": "one", "new_text": "ONE"}]})).await;
    assert_eq!(v.subject.as_deref(), Some("a.txt: 1 edit(s), +1 -1 lines"));
    assert!(v.rendered.as_deref().unwrap().starts_with("edited a.txt: 1 edit(s), +1 -1 lines\n"));
}

/// docs/tools.md `edit`: expected_version compares the complete byte
/// snapshot before any mutation, and successful edits report both versions.
#[tokio::test]
async fn expected_version_refuses_a_stale_edit() {
    let fx = Fixture::new();
    fx.write("a.txt", "one\n");
    let observed = crate::file_version(b"one\n");
    let value =
        edit(&fx, json!({"path": "a.txt", "expected_version": observed, "edits": [replace("one", "two")]})).await;
    assert!(!value.is_error, "{value:?}");
    assert_eq!(value.value["previous_version"], crate::file_version(b"one\n"));
    assert_eq!(value.value["version"], crate::file_version(b"two\n"));

    let value =
        edit(&fx, json!({"path": "a.txt", "expected_version": observed, "edits": [replace("two", "three")]})).await;
    assert!(value.is_error);
    assert!(value.rendered.unwrap().contains("differs from expected_version"));
    assert_eq!(fx.read("a.txt"), "two\n");
    assert_eq!(fx.writes(), 1);
}

/// docs/tools.md `edit`: the canonical prefix and the bare digest identify
/// the same observed file version, including for stale-version rejection.
#[tokio::test]
async fn expected_version_accepts_a_matching_bare_digest_and_refuses_it_when_stale() {
    let fx = Fixture::new();
    fx.write("a.txt", "one\n");
    let observed = crate::file_version(b"one\n");
    let bare = observed.trim_start_matches("sha256:");
    let value = edit(&fx, json!({"path": "a.txt", "expected_version": bare, "edits": [replace("one", "two")]})).await;
    assert!(!value.is_error, "{value:?}");
    assert_eq!(value.value["previous_version"], observed);

    let value = edit(&fx, json!({"path": "a.txt", "expected_version": bare, "edits": [replace("two", "three")]})).await;
    assert!(value.is_error);
    assert!(value.rendered.unwrap().contains("differs from expected_version"));
    assert_eq!(fx.read("a.txt"), "two\n");
    assert_eq!(fx.writes(), 1);
}

/// docs/tools.md `edit`: omitting the algorithm prefix does not make a
/// partial digest authoritative.
#[tokio::test]
async fn expected_version_refuses_a_truncated_bare_digest_without_writing() {
    let fx = Fixture::new();
    fx.write("a.txt", "one\n");
    let observed = crate::file_version(b"one\n");
    let truncated = &observed.trim_start_matches("sha256:")[..32];
    let value =
        edit(&fx, json!({"path": "a.txt", "expected_version": truncated, "edits": [replace("one", "two")]})).await;
    assert!(value.is_error);
    assert!(value.rendered.unwrap().contains("differs from expected_version"));
    assert_eq!(fx.read("a.txt"), "one\n");
    assert_eq!(fx.writes(), 0);
}
