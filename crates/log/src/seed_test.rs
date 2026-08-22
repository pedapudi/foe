use super::{closing_events, seed, SeedHeader, ORPHAN_RENDERED};
use crate::append::Writer;
use crate::fold::{fold, read_all, tests as fx};
use crate::*;
use std::path::PathBuf;

fn tmp(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("foe-log-seed-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// A source log interrupted after a step issued two calls and only the
/// first received its result.
fn source(dir: &std::path::Path) -> Vec<Event> {
    let mut w = Writer::create(dir, None).unwrap();
    let datas = vec![
        EventData::EpisodeStart(fx::start("ep_src")),
        fx::inbox(InboxSource::Task, "task"),
        fx::header(),
        fx::request(1, 2, vec![1], vec![]),
        fx::assistant(1, "go", vec![fx::call("tc_1"), fx::call("tc_2")], false),
        fx::result(1, "tc_1", "done"),
    ];
    datas.into_iter().map(|d| w.append(d).unwrap()).collect()
}

fn header() -> SeedHeader {
    SeedHeader { new_id: "ep_new".into(), parent_id: Some("ep_parent".into()), team_id: None }
}

#[test]
fn seed_repairs_orphan_tool_calls_and_marks_the_boundary() {
    let src = tmp("src");
    let dst = tmp("dst");
    let events = source(&src);
    let written = seed(&src, events.len() as u64, &dst, header()).unwrap();
    assert_eq!(written, read_all(&dst).unwrap());
    let EventData::EpisodeStart(start) = &written[0].data else { panic!() };
    assert_eq!(start.id, "ep_new");
    assert_eq!(start.parent_id.as_deref(), Some("ep_parent"));
    assert_eq!(start.fork_origin, Some(ForkOrigin { episode_id: "ep_src".into(), seq: 6 }));
    assert_eq!(start.task, "do it", "fields other than id, lineage, and origin are copied");
    let EventData::ToolResult(repair) = &written[6].data else { panic!("{:?}", written[6]) };
    assert!(repair.synthetic && repair.is_error);
    assert_eq!((repair.call_id.as_str(), repair.rendered.as_str()), ("tc_2", ORPHAN_RENDERED));
    assert_eq!(written[7].data, EventData::SeedEnd {});
    let state = fold(&written).unwrap();
    assert_eq!(state.seeded_through, Some(7));
    assert_eq!(written[1].time, events[1].time, "copied events keep their time");
}

#[test]
fn seed_at_a_prefix_copies_only_events_before_the_boundary() {
    let src = tmp("prefix-src");
    let dst = tmp("prefix-dst");
    source(&src);
    let written = seed(&src, 4, &dst, header()).unwrap();
    assert_eq!(
        written.iter().map(|e| e.data.type_name()).collect::<Vec<_>>(),
        vec!["episode/start", "inbox/item", "request/header", "model/request", "seed/end"]
    );
    let mut w = Writer::open(&dst, None).unwrap();
    assert_eq!(w.append(fx::assistant(1, "live", vec![], false)).unwrap().seq, 5);
}

#[test]
fn seed_rejects_a_boundary_beyond_the_source() {
    let src = tmp("beyond-src");
    let dst = tmp("beyond-dst");
    source(&src);
    assert!(matches!(seed(&src, 99, &dst, header()), Err(LogError::Invalid { seq: 99, .. })));
}

#[test]
fn seed_drops_a_copied_episode_end_and_renumbers_references() {
    let src = tmp("end-src");
    let dst = tmp("end-dst");
    let mut w = Writer::create(&src, None).unwrap();
    w.append(EventData::EpisodeStart(fx::start("ep_src"))).unwrap();
    w.append(fx::inbox(InboxSource::Task, "task")).unwrap();
    w.append(EventData::SeedEnd {}).unwrap();
    w.append(fx::header()).unwrap();
    w.append(fx::request(1, 3, vec![1], vec![])).unwrap();
    w.append(fx::assistant(1, "bye", vec![], false)).unwrap();
    w.append(EventData::EpisodeEnd { outcome: Outcome::Completed { value: serde_json::Value::Null } }).unwrap();
    let written = seed(&src, 7, &dst, header()).unwrap();
    assert_eq!(written.iter().filter(|e| matches!(e.data, EventData::SeedEnd {})).count(), 1);
    assert!(!written.iter().any(|e| matches!(e.data, EventData::EpisodeEnd { .. })));
    let EventData::ModelRequest(request) = &written[3].data else { panic!() };
    assert_eq!(request.header_seq, 2, "header_seq follows the renumbered header");
    fold(&written).unwrap();
}

#[test]
fn closing_events_names_every_unsettled_call() {
    let events =
        fx::number(vec![fx::assistant(3, "x", vec![fx::call("a"), fx::call("b")], true), fx::result(3, "b", "ok")]);
    let closing = closing_events(&events);
    let [EventData::ToolResult(orphan)] = closing.as_slice() else { panic!("one closing event: {closing:?}") };
    assert_eq!((orphan.step, orphan.call_id.as_str()), (3, "a"));
}
