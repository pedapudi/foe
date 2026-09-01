use super::{settings_in, Settings};
use std::path::Path;

fn scratch(name: &str) -> crate::tests::ScratchDir {
    crate::tests::scratch("foe-cli-telemetry", name)
}

fn enable(home: &Path, body: &str) {
    let dir = home.join(".config/foe");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("telemetry.json"), body).unwrap();
}

#[test]
fn an_absent_enablement_file_disables_telemetry() {
    assert!(settings_in(&scratch("absent")).unwrap().is_none());
}

/// Someone turned telemetry on; a file that cannot be read must not turn it
/// off quietly.
#[test]
fn a_broken_enablement_file_is_an_error_rather_than_a_silent_disable() {
    let home = scratch("broken");
    enable(&home, "{");
    assert!(settings_in(&home).is_err());
    enable(&home, r#"{"capture": 3}"#);
    assert!(settings_in(&home).unwrap_err().contains("`capture` must be a path"));
}

#[test]
fn a_tilde_capture_resolves_against_the_home_the_settings_came_from() {
    let home = scratch("tilde");
    enable(&home, r#"{"capture": "~/state/otel.jsonl"}"#);
    let settings = settings_in(&home).unwrap().unwrap();
    assert_eq!(settings.capture, home.join("state/otel.jsonl"));
}

/// A run's telemetry covers the whole episode tree it wrote: the root log
/// and every descendant under `children/`, each as its own payload line.
#[test]
fn after_run_emits_the_root_and_every_descendant_episode() {
    let dir = scratch("tree");
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR")).join("../telemetry/tests/fixtures/clean/episode.jsonl");
    for episode in ["", "children/a", "children/a/children/b"] {
        let target = dir.join("run").join(episode);
        std::fs::create_dir_all(&target).unwrap();
        std::fs::copy(&fixture, target.join("episode.jsonl")).unwrap();
    }
    let settings = Settings { capture: dir.join("out/otel.jsonl") };
    super::after_run(&settings, &dir.join("run"));
    let written = std::fs::read_to_string(&settings.capture).unwrap();
    assert_eq!(written.lines().count(), 3);
}
