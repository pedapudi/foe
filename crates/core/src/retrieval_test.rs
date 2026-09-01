use super::*;
use crate::test_util::{program_with, tmp};
use foe_log::{
    AssistantMessage, EpisodeStart, InboxItem, InboxSource, RuntimeInfo, SandboxInfo, SandboxMode, StopReason,
    ToolCall, ToolResult, Usage,
};

fn start(root: &Path) -> EpisodeStart {
    let program = program_with(root, |_| {}).unwrap();
    EpisodeStart {
        id: "ep_retrieve".into(),
        parent_id: None,
        fork_origin: None,
        team_id: None,
        program: program.to_value(),
        identity: "sha256:test".into(),
        task: "test retrieval".into(),
        runtime: RuntimeInfo { version: "0".into(), build: "unknown".into() },
        sandbox: SandboxInfo { mode: SandboxMode::Off, landlock_abi: 0, effective_access: None },
        effective_budget: None,
    }
}

fn call(id: &str, name: &str) -> ToolCall {
    ToolCall { id: id.into(), name: name.into(), args: json!({}) }
}

fn assistant(step: u32, calls: Vec<ToolCall>) -> AssistantMessage {
    AssistantMessage {
        step,
        request_id: format!("rq_{step}"),
        text: String::new(),
        tool_calls: calls,
        thinking: vec![],
        stop: StopReason::Tool,
        usage: Usage::default(),
        interrupted: false,
    }
}

fn result(step: u32, id: &str, rendered: &str) -> ToolResult {
    ToolResult {
        step,
        call_id: id.into(),
        name: "probe".into(),
        value: json!({ "ok": true }),
        rendered: rendered.into(),
        is_error: false,
        spill: None,
        subject: None,
        duration_ms: 0,
        synthetic: false,
    }
}

fn context(spill_dir: &Path, step: u32) -> CallCtx {
    CallCtx {
        call_id: "tc_retrieve".into(),
        step,
        reader: None,
        writer: None,
        executor: None,
        spawner: None,
        sessions: None,
        composer: None,
        spill_dir: spill_dir.into(),
        deadline: None,
    }
}

fn log_with_result(name: &str, complete: &str, archived: bool) -> (Arc<Log>, String) {
    let root = tmp(name);
    let dir = root.join("episode");
    std::fs::create_dir_all(&dir).unwrap();
    let log = Arc::new(Log::create_or_open(&dir, None).unwrap());
    log.append(EventData::EpisodeStart(start(&root))).unwrap();
    log.append(EventData::InboxItem(InboxItem {
        source: InboxSource::Task,
        content: vec![foe_log::ContentBlock::Text { text: "test retrieval".into() }],
        from: None,
        message_id: None,
    }))
    .unwrap();
    log.append(EventData::AssistantMessage(assistant(1, vec![call("tc_source", "probe")]))).unwrap();
    if archived {
        let retained = ArchivedRendering { complete: complete.into(), digest: digest(complete.as_bytes()) };
        let archive = retain(&dir.join("spill"), 1, "tc_source", &retained).unwrap();
        log.append(EventData::ToolRenderingArchive(archive)).unwrap();
        log.append(EventData::ToolResult(result(1, "tc_source", "shortened"))).unwrap();
    } else {
        log.append(EventData::ToolResult(result(1, "tc_source", complete))).unwrap();
    }
    (log, cursor(1, "tc_source", complete, 0))
}

#[test]
fn a_recorded_digest_produces_the_rendering_cursor() {
    let complete = "complete rendering";
    assert_eq!(
        cursor_for_digest(3, "tc_source", &digest(complete.as_bytes()), 17),
        cursor(3, "tc_source", complete, 17)
    );
}

/// docs/tools.md "Archived result retrieval": bounded calls reconstruct the
/// complete archived rendering byte for byte, including multibyte text.
#[tokio::test]
async fn repeated_retrieval_reconstructs_the_complete_rendering() {
    let complete = "αβγδ\n".repeat(8_000);
    let (log, mut next) = log_with_result("retrieve-archive", &complete, true);
    let tool = tool(log.clone());
    let mut reconstructed = String::new();
    loop {
        let value = tool.call(json!({ "cursor": next }), &context(&log.dir().join("spill"), 2)).await;
        assert!(!value.is_error, "{:?}", value.rendered);
        assert!(value.rendered.as_ref().unwrap().len() <= RENDERED_BYTES);
        reconstructed.push_str(value.value["content"].as_str().unwrap());
        match value.value["next_cursor"].as_str() {
            Some(cursor) => next = cursor.into(),
            None => break,
        }
    }
    assert_eq!(reconstructed, complete);
}

/// docs/tools.md "Archived result retrieval": an uncut rendering is served
/// from its tool/result event and needs no archive file.
#[tokio::test]
async fn an_uncut_rendering_is_retrieved_from_the_log() {
    let (log, cursor) = log_with_result("retrieve-inline", "complete inline result", false);
    let value = tool(log.clone()).call(json!({ "cursor": cursor }), &context(&log.dir().join("spill"), 2)).await;
    assert_eq!(value.value["content"], "complete inline result");
    assert!(!log.dir().join("spill").exists());
}

/// docs/tools.md "Archived result retrieval": cursor resolution uses only
/// an earlier result in the current episode and rejects changed fields.
#[tokio::test]
async fn invalid_changed_and_future_cursors_are_rejected() {
    let (log, cursor_value) = log_with_result("retrieve-cursors", "source", false);
    let mut changed = cursor_value.clone();
    let last = changed.pop().unwrap();
    changed.push(if last == '0' { '1' } else { '0' });
    for (candidate, step) in [("invalid".to_string(), 2), (changed, 2), (cursor_value.clone(), 1)] {
        let value =
            tool(log.clone()).call(json!({ "cursor": candidate }), &context(&log.dir().join("spill"), step)).await;
        assert!(value.is_error, "{:?}", value.value);
    }
    let other = cursor(1, "tc_source", "another episode's bytes", 0);
    let value = tool(log.clone()).call(json!({ "cursor": other }), &context(&log.dir().join("spill"), 2)).await;
    assert!(value.is_error && value.rendered.unwrap().contains("this episode"));

    let same_evidence = cursor(1, "tc_source", "source", 0);
    let value = tool(log.clone()).call(json!({ "cursor": same_evidence }), &context(&log.dir().join("spill"), 2)).await;
    assert_eq!(value.value["content"], "source");
}

/// docs/log-format.md `tool/rendering-archive`: retrieval verifies immutable
/// archive bytes against the recorded digest.
#[tokio::test]
async fn a_changed_archive_is_rejected_with_its_event_and_digest() {
    let (log, cursor) = log_with_result("retrieve-changed", "complete", true);
    let archive = log.events().into_iter().find_map(|event| match event.data {
        EventData::ToolRenderingArchive(archive) => Some((event.seq, archive)),
        _ => None,
    });
    let (seq, archive) = archive.unwrap();
    std::fs::write(log.dir().join("spill").join(&archive.file), "changed").unwrap();
    let value = tool(log.clone()).call(json!({ "cursor": cursor }), &context(&log.dir().join("spill"), 2)).await;
    let error = value.rendered.unwrap();
    assert!(value.is_error && error.contains(&format!("event {seq}")) && error.contains(&archive.digest), "{error}");
}

/// docs/log-format.md "Seeding": retrieval from a copied prefix uses the
/// destination archive after the source episode has been removed.
#[tokio::test]
async fn retrieval_from_a_seed_does_not_open_the_source_episode() {
    let complete = "archived evidence\n".repeat(2_000);
    let (source, cursor) = log_with_result("retrieve-seed", &complete, true);
    let source_dir = source.dir().to_path_buf();
    let dest = source_dir.parent().unwrap().join("seeded");
    std::fs::create_dir_all(&dest).unwrap();
    foe_log::seed::seed(
        &source_dir,
        source.events().len() as u64,
        &dest,
        foe_log::seed::SeedHeader { new_id: "ep_seeded".into(), parent_id: None, team_id: None, program: None },
    )
    .unwrap();
    drop(source);
    std::fs::remove_dir_all(&source_dir).unwrap();
    let seeded = Arc::new(Log::create_or_open(&dest, None).unwrap());
    let value = tool(seeded.clone()).call(json!({ "cursor": cursor }), &context(&dest.join("spill"), 2)).await;
    assert!(!value.is_error, "{:?}", value.rendered);
    assert!(value.value["content"].as_str().unwrap().len() < RENDERED_BYTES);
    assert!(complete.starts_with(value.value["content"].as_str().unwrap()));
}

/// docs/tools.md "Archived result retrieval": a recorded retrieval result
/// replays from `tool/result.rendered` without opening its source archive.
#[tokio::test]
async fn a_recorded_retrieval_replays_without_the_archive() {
    let (log, cursor) = log_with_result("retrieve-replay", &"archived\n".repeat(3_000), true);
    let value = tool(log.clone()).call(json!({ "cursor": cursor }), &context(&log.dir().join("spill"), 2)).await;
    let rendered = value.rendered.unwrap();
    log.append(EventData::AssistantMessage(assistant(2, vec![call("tc_retrieve", NAME)]))).unwrap();
    log.append(EventData::ToolResult(result(2, "tc_retrieve", &rendered))).unwrap();
    let events = log.events();
    let before = foe_log::fold::derive_messages(&events, events.len() as u64, &[]);
    let archive = events.iter().find_map(|event| match &event.data {
        EventData::ToolRenderingArchive(archive) => Some(archive),
        _ => None,
    });
    std::fs::remove_file(log.dir().join("spill").join(&archive.unwrap().file)).unwrap();
    let after = foe_log::fold::derive_messages(&events, events.len() as u64, &[]);
    assert_eq!(after, before);
    assert!(after
        .iter()
        .any(|message| matches!(message, foe_log::Message::Tool { rendered: replayed, .. } if replayed == &rendered)));
}
