//! Tests for the projection and the static export.

use foe_log::{Event, ExhaustedLimit, Outcome};
use std::path::{Path, PathBuf};

fn fixture() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/run")
}

const FONTS: [&str; 4] = [
    "iAWriterMonoS-Regular.woff2",
    "iAWriterMonoS-Bold.woff2",
    "JetBrainsMono-Regular.woff2",
    "JetBrainsMono-Bold.woff2",
];

/// The font files present in the checkout, with their bytes.
fn present_fonts() -> Vec<(&'static str, Vec<u8>)> {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../view/fonts");
    FONTS
        .iter()
        .filter_map(|name| Some((*name, std::fs::read(dir.join(name)).ok()?)))
        .collect()
}

#[test]
fn projects_tree_with_spawned_and_forked_children() {
    let tree = foe_view::project(&fixture()).unwrap();
    assert_eq!(tree.roots.len(), 1);
    let root = &tree.roots[0];
    assert_eq!(root.id, "ep_root");
    assert_eq!(root.name.as_deref(), Some("fixer"));
    assert_eq!(root.parent_id, None);
    assert!(matches!(root.outcome, Some(Outcome::Completed { .. })));
    assert_eq!(
        (root.usage.input, root.usage.output, root.usage.cache_read),
        (9120, 100, 8000)
    );
    let ids: Vec<&str> = root.children.iter().map(|c| c.id.as_str()).collect();
    assert_eq!(ids, ["ep_child", "ep_fork"]);
    let child = &root.children[0];
    assert_eq!(child.parent_id.as_deref(), Some("ep_root"));
    assert_eq!(child.fork_origin, None);
    assert!(matches!(child.outcome, Some(Outcome::Blocked { .. })));
    let fork = &root.children[1];
    let origin = fork.fork_origin.as_ref().unwrap();
    assert_eq!((origin.episode_id.as_str(), origin.seq), ("ep_root", 7));
    assert_eq!(
        fork.outcome,
        Some(Outcome::Exhausted {
            limit: ExhaustedLimit::ModelCalls
        })
    );
    assert!(fork.children.is_empty());
}

#[test]
fn project_names_the_unreadable_log() {
    let missing = fixture().join("children/ep_child/children/none");
    let err = foe_view::project(&missing).unwrap_err().to_string();
    assert!(err.contains("none/episode.jsonl"), "{err}");
}

#[test]
fn export_contains_every_event() {
    let html = foe_view::export(&fixture()).unwrap();
    assert!(html.contains("\"mode\":\"static\""));
    assert!(html.contains("<div id=\"app\"></div>"));
    let boot = html
        .split("window.__FOE__=")
        .nth(1)
        .unwrap()
        .split(";</script>")
        .next()
        .unwrap();
    assert!(
        !boot.contains('<') && boot.contains("\\u003cfirst>"),
        "event text must not close the script"
    );
    for rel in [
        "episode.jsonl",
        "children/ep_child/episode.jsonl",
        "children/ep_fork/episode.jsonl",
    ] {
        for line in std::fs::read_to_string(fixture().join(rel))
            .unwrap()
            .lines()
        {
            let event: Event = serde_json::from_str(line).unwrap();
            let wire = serde_json::to_string(&event)
                .unwrap()
                .replace('<', "\\u003c");
            assert!(html.contains(&wire), "missing from export: {wire}");
        }
    }
    assert_eq!(html.matches("\"type\":\"episode/start\"").count(), 3);
    assert!(html.contains("\"tree\":{\"roots\":[{\"id\":\"ep_root\""));
}

#[test]
fn export_inlines_every_present_font() {
    // The CSS embedded at build time decides how many references exist.
    let css = include_str!(concat!(env!("OUT_DIR"), "/viewer.css"));
    let html = foe_view::export(&fixture()).unwrap();
    let mut expected = 0;
    for (name, _) in present_fonts() {
        let path = format!("/fonts/{name}");
        expected += css.matches(&path).count();
        assert!(!html.contains(&path), "{path} left unresolved in export");
    }
    assert_eq!(html.matches("data:font/woff2;base64,").count(), expected);
    if present_fonts().len() == FONTS.len()
        && FONTS.iter().all(|n| css.contains(&format!("/fonts/{n}")))
    {
        assert!(expected >= 4, "every font inlined at least once");
    }
}
