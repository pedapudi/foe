use super::{closing_events, seed, SeedHeader, ORPHAN_RENDERED};
use crate::append::Writer;
use crate::fold::tests::tmp;
use crate::fold::{fold, read_all, tests as fx};
use crate::*;

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
    SeedHeader { new_id: "ep_new".into(), parent_id: Some("ep_parent".into()), team_id: None, contract: None }
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
    assert_eq!(start.task, "do it", "fields other than id and episode relations are copied");
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

/// docs/log-format.md "Seeding": an open inner call closes before the
/// outer composing call it nests under.
#[test]
fn closing_events_close_open_inner_calls_before_their_outer_call() {
    let events = fx::number(vec![
        fx::assistant(2, "x", vec![fx::call("tc_p")], true),
        fx::inner_call("tc_p", 0),
        fx::result(2, "tc_p_0", "settled"),
        fx::inner_call("tc_p", 1),
    ]);
    let closing = closing_events(&events);
    let ids: Vec<(u32, &str)> = closing
        .iter()
        .map(|data| match data {
            EventData::ToolResult(r) => (r.step, r.call_id.as_str()),
            other => panic!("{other:?}"),
        })
        .collect();
    assert_eq!(ids, [(2, "tc_p_1"), (2, "tc_p")], "the open inner call closes first, at the outer call's step");
}

fn archived(dir: &std::path::Path, step: u32, call_id: &str, content: &str) -> RenderingArchive {
    let hex = digest::sha256_hex(content.as_bytes());
    let file = format!("renderings/{hex}.txt");
    let path = dir.join("spill").join(&file);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
    RenderingArchive {
        step,
        call_id: call_id.into(),
        file,
        digest: format!("sha256:{hex}"),
        bytes: content.len() as u64,
    }
}

fn source_with_archives(dir: &std::path::Path) -> Vec<Event> {
    let mut w = Writer::create(dir, None).unwrap();
    let first = archived(dir, 1, "tc_a", "first complete rendering");
    let second = archived(dir, 1, "tc_b", "second complete rendering");
    [
        EventData::EpisodeStart(fx::start("ep_src")),
        fx::inbox(InboxSource::Task, "task"),
        fx::header(),
        fx::request(1, 2, vec![1], vec![]),
        fx::assistant(1, "go", vec![fx::call("tc_a"), fx::call("tc_b")], false),
        EventData::ToolRenderingArchive(first),
        fx::result(1, "tc_a", "short first"),
        EventData::ToolRenderingArchive(second),
        fx::result(1, "tc_b", "short second"),
    ]
    .into_iter()
    .map(|data| w.append(data).unwrap())
    .collect()
}

/// docs/log-format.md "Seeding": a seed copies and verifies archives
/// referenced by complete result pairs before its boundary.
#[test]
fn seed_copies_only_referenced_rendering_archives() {
    let src = tmp("archive-src");
    let dst = tmp("archive-dst");
    let events = source_with_archives(&src);
    let EventData::ToolRenderingArchive(first) = &events[5].data else { panic!() };
    let EventData::ToolRenderingArchive(second) = &events[7].data else { panic!() };
    seed(&src, 7, &dst, header()).unwrap();
    assert_eq!(std::fs::read_to_string(dst.join("spill").join(&first.file)).unwrap(), "first complete rendering");
    assert!(!dst.join("spill").join(&second.file).exists());
    std::fs::remove_dir_all(&src).unwrap();
    assert_eq!(std::fs::read_to_string(dst.join("spill").join(&first.file)).unwrap(), "first complete rendering");
}

/// docs/log-format.md "Seeding": a changed source archive makes seeding
/// fail with its archive event and expected digest named.
#[test]
fn seed_rejects_a_changed_rendering_archive() {
    let src = tmp("archive-changed-src");
    let dst = tmp("archive-changed-dst");
    let events = source_with_archives(&src);
    let EventData::ToolRenderingArchive(first) = &events[5].data else { panic!() };
    std::fs::write(src.join("spill").join(&first.file), "changed").unwrap();
    let error = seed(&src, 7, &dst, header()).unwrap_err().to_string();
    assert!(error.contains("event 5") && error.contains(&first.digest), "{error}");
}

/// docs/log-format.md "Seeding": a seeded log states the format version of
/// the writer that seeded it, on the fresh episode/start it writes first.
#[test]
fn a_seeded_log_states_the_format_version_on_its_first_event() {
    let src = tmp("version-src");
    let dst = tmp("version-dst");
    let events = source(&src);
    let written = seed(&src, events.len() as u64, &dst, header()).unwrap();
    assert_eq!(written[0].version, Some(LOG_VERSION));
    assert!(written[1..].iter().all(|event| event.version.is_none()));
    assert_eq!(written, read_all(&dst).unwrap());
}

fn source_with_canonical(
    dir: &std::path::Path,
    file: &str,
    content: Option<&[u8]>,
    value: serde_json::Value,
) -> Vec<Event> {
    if let Some(bytes) = content {
        artifact::retain(&dir.join("spill").join(file), bytes).unwrap();
    }
    let mut writer = Writer::create(dir, None).unwrap();
    let EventData::ToolResult(mut result) = fx::result(1, "tc_canonical", "result") else { unreachable!() };
    result.spill = Some(file.into());
    result.value = value;
    [
        EventData::EpisodeStart(fx::start("ep_source")),
        fx::inbox(InboxSource::Task, "task"),
        fx::header(),
        fx::request(1, 2, vec![1], vec![]),
        fx::assistant(1, "", vec![fx::call("tc_canonical")], false),
        EventData::ToolResult(result),
    ]
    .into_iter()
    .map(|data| writer.append(data).unwrap())
    .collect()
}

/// docs/log-format.md "Seeding": copied canonical spills remain readable independently of their source.
#[test]
fn seed_copies_canonical_spills_with_and_without_digest() {
    for has_digest in [false, true] {
        let src = tmp("canonical-source");
        let dst = tmp("canonical-destination");
        let bytes = br#"{"count":3}"#;
        let mut value = serde_json::json!({"spill":"result.json", "bytes":bytes.len(), "is_error":false});
        if has_digest {
            value["digest"] = format!("sha256:{}", digest::sha256_hex(bytes)).into();
        }
        let events = source_with_canonical(&src, "result.json", Some(bytes), value);
        let written = seed(&src, events.len() as u64, &dst, header()).unwrap();
        assert_eq!(std::fs::read(dst.join("spill/result.json")).unwrap(), bytes);
        let moved = src.with_extension("retained");
        assert!(!moved.exists());
        std::fs::rename(&src, moved).unwrap();
        let EventData::ToolResult(result) = &written[5].data else { unreachable!() };
        assert_eq!(artifact::read_canonical(&dst.join("spill"), 5, result).unwrap().unwrap(), bytes);
    }
}

/// docs/log-format.md "Seeding": unavailable or inconsistent canonical evidence prevents seed/end.
#[test]
fn seed_rejects_invalid_canonical_evidence_and_ignores_results_outside_the_prefix() {
    let bytes = br#"{"count":3}"#;
    for fault in ["missing", "length", "digest", "digest-type", "json", "path", "locator", "error", "symlink"] {
        let src = tmp("canonical-invalid");
        let dst = tmp("canonical-rejected");
        let file = if fault == "path" { "../result.json" } else { "result.json" };
        let mut value = serde_json::json!({"spill":file, "bytes":bytes.len(), "is_error":false});
        match fault {
            "length" => value["bytes"] = 1.into(),
            "digest" => value["digest"] = format!("sha256:{}", "0".repeat(64)).into(),
            "digest-type" => value["digest"] = false.into(),
            "locator" => value["spill"] = "different.json".into(),
            "error" => value["is_error"] = true.into(),
            _ => {}
        }
        let content = match fault {
            "missing" | "path" | "symlink" => None,
            "json" => Some(b"invalid json".as_slice()),
            _ => Some(bytes.as_slice()),
        };
        if fault == "json" {
            value["bytes"] = content.unwrap().len().into();
        }
        let events = source_with_canonical(&src, file, content, value);
        #[cfg(unix)]
        if fault == "symlink" {
            std::fs::create_dir_all(src.join("spill")).unwrap();
            artifact::retain(&src.join("outside.json"), bytes).unwrap();
            std::os::unix::fs::symlink(src.join("outside.json"), src.join("spill/result.json")).unwrap();
        }
        let error = seed(&src, events.len() as u64, &dst, header()).unwrap_err().to_string();
        assert!(error.contains("event 5") && error.contains(file), "{fault}: {error}");
        assert!(!read_all(&dst).unwrap().iter().any(|e| matches!(e.data, EventData::SeedEnd {})));
        let before = std::fs::read(dst.join(fold::LOG_FILE)).unwrap();
        let error = Writer::open(&dst, None).err().expect("incomplete seeding must refuse reopening");
        assert!(error.to_string().contains("seed/end"), "{fault}: {error}");
        assert_eq!(std::fs::read(dst.join(fold::LOG_FILE)).unwrap(), before);
        let earlier = tmp("canonical-outside-prefix");
        seed(&src, 5, &earlier, header()).unwrap();
        assert!(!earlier.join("spill/result.json").exists());
    }
}

/// docs/log-format.md `tool/result`: immutable installation cannot replace conflicting bytes.
#[test]
fn concurrent_archive_installations_preserve_content_and_remove_owned_temporary_files() {
    let dir = tmp("archive-install");
    let target = dir.join("result.json");
    std::thread::scope(|scope| {
        for _ in 0..16 {
            scope.spawn(|| artifact::retain(&target, b"recorded").unwrap());
        }
    });
    assert_eq!(std::fs::read(&target).unwrap(), b"recorded");
    assert!(artifact::retain(&target, b"different").is_err());
    assert_eq!(std::fs::read(&target).unwrap(), b"recorded");
    assert_eq!(std::fs::read_dir(&dir).unwrap().count(), 1);
}

/// Writes a log whose ordinary requests record the message list that the
/// events before them derive, as the runtime records them.
struct Recorder {
    writer: Writer,
    events: Vec<Event>,
    header_seq: u64,
}

impl Recorder {
    fn new(dir: &std::path::Path) -> Self {
        let mut recorder = Recorder { writer: Writer::create(dir, None).unwrap(), events: vec![], header_seq: 0 };
        recorder.push(EventData::EpisodeStart(fx::start("ep_src")));
        recorder.push(fx::inbox(InboxSource::Task, "task"));
        recorder.header();
        recorder
    }

    fn push(&mut self, data: EventData) -> u64 {
        self.events.push(self.writer.append(data).unwrap());
        self.events.len() as u64 - 1
    }

    fn header(&mut self) {
        self.header_seq = self.push(fx::header());
    }

    /// One step: a request, a response issuing one call, and its result.
    /// Returns the `seq` of the request.
    fn step(&mut self, step: u32) -> u64 {
        let consumed = if step == 1 { vec![1] } else { vec![] };
        let messages = fold::derive_messages(&self.events, self.events.len() as u64, &consumed);
        let request = self.push(fx::request(step, self.header_seq, consumed, messages));
        let call = format!("tc_{step}");
        self.push(fx::assistant(step, "go", vec![fx::call(&call)], false));
        self.push(fx::result(step, &call, "done"));
        request
    }

    /// A compaction at `step` that summarizes `[first_seq, first_kept_seq)`.
    fn compact(&mut self, step: u32, first_seq: u64, first_kept_seq: u64) {
        let covered = Covered { first_seq, last_seq: first_kept_seq - 1 };
        self.push(EventData::CompactionStart(CompactionStart {
            step,
            covered,
            trigger: CompactionTrigger::Threshold,
            projected_tokens: 190_000,
            reserved: BudgetAmount::default(),
        }));
        self.header();
        let summary_request_seq = self.push(fx::summary_request(step, self.header_seq));
        self.push(fx::summary_response(step, "summary"));
        let EventData::CompactionSummary(mut summary) =
            fx::compaction_summary(step, first_kept_seq, summary_request_seq, "summary", &["a"])
        else {
            unreachable!()
        };
        summary.state.covered = covered;
        self.push(EventData::CompactionSummary(summary));
        self.push(EventData::CompactionEnd {
            step,
            ok: true,
            usage: Usage::default(),
            active_estimate: 50,
            error: None,
        });
        self.header();
    }

    fn node_start(&mut self, inputs: Vec<u64>) -> u64 {
        let start = WorkflowNodeStart { node: "survey".into(), fire: 1, inputs, child_id: None };
        self.push(EventData::WorkflowNodeStart(start))
    }
}

/// A log of six steps and two compactions that holds a `seed/end` after
/// `seeded` of its first three steps. The first compaction keeps from step
/// `kept`. The second keeps from step 5 and covers from the boundary of the
/// first. A workflow node firing names the task item and the last tool
/// result as its inputs.
fn compacted_source(dir: &std::path::Path, seeded: u32, kept: u32) -> Vec<Event> {
    let mut log = Recorder::new(dir);
    let mut requests = vec![0];
    for step in 1..=3 {
        if seeded == step - 1 {
            log.push(EventData::SeedEnd {});
        }
        requests.push(log.step(step));
    }
    if seeded == 3 {
        log.push(EventData::SeedEnd {});
    }
    log.compact(4, 1, requests[kept as usize]);
    requests.push(log.step(4));
    requests.push(log.step(5));
    log.compact(6, requests[kept as usize], requests[5]);
    let last_result = log.step(6) + 2;
    log.node_start(vec![1, last_result]);
    log.events
}

/// Every ordinary request in `events` records the message list that the
/// events before it derive.
fn assert_rederivable(events: &[Event], context: &str) {
    for event in events {
        let EventData::ModelRequest(request) = &event.data else { continue };
        if request.request_id.starts_with(SUMMARY_REQUEST_PREFIX) {
            continue;
        }
        let derived = fold::derive_messages(events, event.seq, &request.consumed);
        assert_eq!(request.messages, derived, "{context}: the request at seq {} rederives", event.seq);
    }
}

/// The sequence-number fields of `event`, with the two ends of a `covered`
/// range last.
fn references(event: &Event) -> Vec<u64> {
    match &event.data {
        EventData::ModelRequest(r) => std::iter::once(r.header_seq).chain(r.consumed.clone()).collect(),
        EventData::CompactionStart(s) => vec![s.covered.first_seq, s.covered.last_seq],
        EventData::CompactionSummary(s) => {
            vec![s.first_kept_seq, s.summary_request_seq, s.state.covered.first_seq, s.state.covered.last_seq]
        }
        EventData::WorkflowNodeStart(n) => n.inputs.clone(),
        _ => vec![],
    }
}

/// docs/log-format.md "Seeding" rule 2 and "Derived messages": a seed of a
/// source that holds a `seed/end` and two compactions folds, every copied
/// request records the list that the events before it derive, and every
/// sequence-number field names the event it named in the source. An end of
/// a `covered` range that named the source `seed/end` names the last copied
/// event before it. Every placement of the `seed/end` and of the first
/// compaction is seeded whole, and the placement that ends a range on the
/// `seed/end` is seeded at every boundary.
#[test]
fn a_seeded_log_rederives_every_copied_request() {
    for (seeded, kept) in [(0, 2), (0, 3), (1, 2), (1, 3), (2, 2), (2, 3), (3, 2), (3, 3)] {
        let src = tmp("rederive-src");
        let events = compacted_source(&src, seeded, kept);
        fold(&events).unwrap();
        assert_rederivable(&events, "the source");
        let seed_end = events.iter().position(|e| e.data == EventData::SeedEnd {}).unwrap() as u64;
        let whole = events.len() as u64;
        for boundary in if (seeded, kept) == (1, 2) { 2..=whole } else { whole..=whole } {
            let context = format!("seed/end after step {seeded}, kept from step {kept}, boundary {boundary}");
            let dst = tmp("rederive-dst");
            let written = seed(&src, boundary, &dst, header()).unwrap_or_else(|e| panic!("{context}: {e}"));
            fold(&written).unwrap_or_else(|e| panic!("{context}: {e}"));
            assert_rederivable(&written, &context);
            let copied = events[1..boundary as usize].iter().filter(|e| e.data != EventData::SeedEnd {});
            let renumbered: std::collections::BTreeMap<u64, u64> =
                copied.clone().zip(&written[1..]).map(|(source, copy)| (source.seq, copy.seq)).collect();
            for (source, copy) in copied.zip(&written[1..]) {
                // The source `seed/end` is not copied. Only the last end of
                // a `covered` range names it, and that end moves to the
                // last copied event before it.
                let inward = |seq: u64| if seq == seed_end { seq - 1 } else { seq };
                let expected: Vec<u64> = references(source).into_iter().map(|seq| renumbered[&inward(seq)]).collect();
                assert_eq!(references(copy), expected, "{context}: the fields of the event copied to seq {}", copy.seq);
            }
        }
    }
}

/// docs/log-format.md "Seeding" rule 2: a copied request records the
/// continuation message rendered from the renumbered `covered` range.
#[test]
fn a_copied_request_records_the_continuation_of_the_renumbered_state() {
    let (src, dst) = (tmp("continuation-src"), tmp("continuation-dst"));
    let events = compacted_source(&src, 1, 3);
    let written = seed(&src, events.len() as u64, &dst, header()).unwrap();
    let covered_line = |events: &[Event]| {
        let request = events.iter().find_map(|e| match &e.data {
            EventData::ModelRequest(r) if r.request_id == "rq_4" => Some(r.clone()),
            _ => None,
        });
        let Message::User { content } = &request.unwrap().messages[1] else { panic!() };
        let ContentBlock::Text { text } = &content[0] else { panic!() };
        text.lines().nth(2).unwrap().to_string()
    };
    assert_eq!(covered_line(&events), "covered: seq 1 to 9");
    assert_eq!(covered_line(&written), "covered: seq 1 to 8");
}

/// docs/log-format.md "Seeding" rule 2: `workflow/node-start.inputs` is
/// rewritten to the `seq` that the events it names take in the new log.
#[test]
fn seed_renumbers_workflow_node_inputs() {
    let (src, dst) = (tmp("inputs-src"), tmp("inputs-dst"));
    let mut log = Recorder::new(&src);
    log.push(EventData::SeedEnd {});
    log.step(1);
    let start = log.node_start(vec![1, 6]);
    let written = seed(&src, log.events.len() as u64, &dst, header()).unwrap();
    let EventData::WorkflowNodeStart(copy) = &written[start as usize - 1].data else { panic!() };
    assert_eq!(copy.inputs, [1, 5], "the tool result at seq 6 is copied to seq 5");
    assert_eq!(written[5].data, log.events[6].data);
}

/// docs/log-format.md "Seeding" rule 2: a sequence-number field that names
/// an event the seed does not copy makes seeding fail before `seed/end`,
/// and the error names the event and the `seq` it names.
#[test]
fn seed_refuses_a_reference_to_an_event_it_does_not_copy() {
    let (src, dst) = (tmp("uncopied-src"), tmp("uncopied-dst"));
    let mut log = Recorder::new(&src);
    let seed_end = log.push(EventData::SeedEnd {});
    log.step(1);
    let start = log.node_start(vec![seed_end]);
    let error = seed(&src, log.events.len() as u64, &dst, header()).unwrap_err().to_string();
    assert!(error.contains(&format!("event {start}")) && error.contains(&format!("event {seed_end}")), "{error}");
    assert!(!read_all(&dst).unwrap().iter().any(|e| matches!(e.data, EventData::SeedEnd {})));
}

/// docs/log-format.md "Seeding" rule 2: an end of a `covered` range that
/// names an event the seed does not copy moves inward to the nearest
/// copied event.
#[test]
fn seed_moves_a_range_end_on_an_uncopied_event_inward() {
    let (src, dst) = (tmp("inward-src"), tmp("inward-dst"));
    let events = compacted_source(&src, 1, 2);
    let written = seed(&src, events.len() as u64, &dst, header()).unwrap();
    let first_range = |events: &[Event]| {
        events.iter().find_map(|e| match &e.data {
            EventData::CompactionStart(start) => Some(start.covered),
            _ => None,
        })
    };
    assert_eq!(events[6].data, EventData::SeedEnd {});
    assert_eq!(first_range(&events), Some(Covered { first_seq: 1, last_seq: 6 }));
    assert_eq!(first_range(&written), Some(Covered { first_seq: 1, last_seq: 5 }));
}
