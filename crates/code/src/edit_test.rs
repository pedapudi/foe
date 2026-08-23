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
