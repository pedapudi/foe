use super::{Downlink, Host, InboxSink, HOST_ROUTE};
use crate::loop_::{initialize, Log};
use crate::test_util::{program_with, tmp};
use crate::{CallCtx, ModelRequestBody};
use foe_log::{Chunk, EventData, InboxSource, StopReason, Usage};
use serde_json::json;
use std::sync::{Arc, Mutex};
use tokio::io::AsyncWriteExt;

/// Polls `probe` between yields to the runtime until it answers. The fast
/// tier waits on no real clock, so the wait is over as soon as the task
/// under test has run rather than after a span guessed in advance.
async fn yield_until<T>(mut probe: impl FnMut() -> Option<T>) -> T {
    for _ in 0..10_000 {
        if let Some(value) = probe() {
            return value;
        }
        tokio::task::yield_now().await;
    }
    panic!("the condition was never met");
}

fn log(name: &str) -> (Arc<Log>, std::path::PathBuf) {
    let root = tmp(name);
    let dir = root.join("episode");
    std::fs::create_dir_all(&dir).unwrap();
    let log = Arc::new(Log::create_or_open(&dir, None).unwrap());
    log.append(EventData::EpisodeStart(foe_log::EpisodeStart {
        id: "ep_self".into(),
        parent_id: None,
        fork_origin: None,
        team_id: None,
        program: json!({}),
        identity: "sha256:x".into(),
        task: "t".into(),
        runtime: foe_log::RuntimeInfo { version: "0".into(), build: "unknown".into() },
        sandbox: foe_log::SandboxInfo {
            mode: foe_log::SandboxMode::Off,
            landlock_abi: 0,
            effective_access: None,
            process_boundary: None,
        },
        effective_budget: None,
    }))
    .unwrap();
    (log, root)
}

/// docs/protocol.md "Children" and docs/log-format.md "Inbox": a queued
/// peer message follows the episode start and its task item.
#[tokio::test]
async fn protocol_input_starts_after_the_episode_prefix() {
    let root = tmp("protocol-prefix-before-input");
    let dir = root.join("episode");
    std::fs::create_dir_all(&dir).unwrap();
    let log = Arc::new(Log::create_or_open(&dir, None).unwrap());
    let start = foe_log::EpisodeStart {
        id: "ep_self".into(),
        parent_id: Some("ep_parent".into()),
        fork_origin: None,
        team_id: Some("ep_parent".into()),
        program: json!({}),
        identity: "sha256:x".into(),
        task: "do the work".into(),
        runtime: foe_log::RuntimeInfo { version: "0".into(), build: "unknown".into() },
        sandbox: foe_log::SandboxInfo {
            mode: foe_log::SandboxMode::Off,
            landlock_abi: 0,
            effective_access: None,
            process_boundary: None,
        },
        effective_budget: None,
    };
    initialize(&log, &start).unwrap();
    let (host, stop) = Host::new(start.id.clone(), log.clone(), None);
    let peer = concat!(
        r#"{"type":"inbox/item","source":"peer","content":[{"type":"text","text":"ready"}],"from":"ep_peer","message_id":"tm_1"}"#,
        "\n",
    );

    host.read_lines(std::io::Cursor::new(peer.as_bytes().to_vec())).await;

    assert!(stop.borrow().is_none());
    let events = log.events();
    assert_eq!(events.iter().filter(|event| matches!(event.data, EventData::EpisodeStart(_))).count(), 1);
    assert_eq!(
        events
            .iter()
            .filter(|event| matches!(&event.data, EventData::InboxItem(item) if item.source == InboxSource::Task))
            .count(),
        1
    );
    assert!(matches!(events[0].data, EventData::EpisodeStart(_)));
    assert!(matches!(&events[1].data, EventData::InboxItem(item) if item.source == InboxSource::Task));
    assert!(matches!(&events[2].data, EventData::InboxItem(item) if item.source == InboxSource::Peer));
    foe_log::fold::fold(&events).expect("the queued peer message follows a valid episode prefix");
}

fn request(log: &Log, id: &str) -> ModelRequestBody {
    log.append(EventData::InboxItem(foe_log::InboxItem {
        source: InboxSource::Task,
        content: vec![],
        from: None,
        message_id: None,
    }))
    .unwrap();
    log.append(EventData::RequestHeader(foe_log::RequestHeader {
        reason: foe_log::HeaderReason::Initial,
        system: String::new(),
        tools: vec![],
        model: foe_log::ModelRoute { provider: "h".into(), model: "h".into() },
    }))
    .unwrap();
    log.append(EventData::ModelRequest(foe_log::ModelRequest {
        step: 1,
        attempt: 1,
        request_id: id.into(),
        header_seq: 2,
        consumed: vec![1],
        messages: vec![],
        max_output_tokens: None,
    }))
    .unwrap();
    ModelRequestBody {
        request_id: id.into(),
        system: String::new(),
        tools: vec![],
        messages: vec![],
        max_output_tokens: None,
    }
}

fn ctx(call_id: &str, dir: &std::path::Path) -> CallCtx {
    CallCtx {
        call_id: call_id.into(),
        step: 1,
        reader: None,
        writer: None,
        executor: None,
        spawner: None,
        sessions: None,
        composer: None,
        spill_dir: dir.to_path_buf(),
        deadline: None,
    }
}

#[tokio::test]
async fn model_chunks_reach_the_transport_even_when_they_arrive_before_it_listens() {
    let (log, _) = log("protocol-chunks");
    let (host, _stop) = Host::new("ep_self".into(), log.clone(), None);
    let req = request(&log, "rq_0001");
    let lines = concat!(
        r#"{"type":"model/chunk","request_id":"rq_0001","chunk":{"kind":"text","delta":"hi"}}"#,
        "\n",
        r#"{"type":"model/chunk","request_id":"rq_0001","episode_id":"ep_self","chunk":{"kind":"done","stop":"end","usage":{"input":1,"output":2,"cache_read":0}}}"#,
        "\n",
    );
    host.read_lines(std::io::Cursor::new(lines.as_bytes().to_vec())).await;
    let transport = host.transport();
    assert_eq!(transport.route().provider, HOST_ROUTE);
    let mut chunks: Vec<Chunk> = Vec::new();
    transport.stream(req, &mut chunks).await;
    assert_eq!(
        chunks,
        vec![
            Chunk::Text { delta: "hi".into() },
            Chunk::Done { stop: StopReason::End, usage: Usage { input: 1, output: 2, cache_read: 0 } }
        ]
    );
}

#[tokio::test]
async fn a_chunk_for_an_unknown_or_settled_request_is_a_protocol_error() {
    let (log, _) = log("protocol-unknown");
    let (host, stop) = Host::new("ep_self".into(), log.clone(), None);
    let line = r#"{"type":"model/chunk","request_id":"rq_9999","chunk":{"kind":"text","delta":"hi"}}"#;
    host.read_lines(std::io::Cursor::new(format!("{line}\n").into_bytes())).await;
    assert!(stop.borrow().as_deref().unwrap().starts_with("protocol:"));
}

#[tokio::test]
async fn host_tool_calls_emit_an_event_and_wait_for_the_answer() {
    let (log, root) = log("protocol-tool");
    let program = program_with(&root, |v| {
        v["tools"] = json!(["h"]);
        v["host_tools"] = json!({ "h": { "description": "hosted", "params": { "type": "object" }, "effect": "pure" } });
    })
    .unwrap();
    let (host, _stop) = Host::new("ep_self".into(), log.clone(), None);
    let tools = host.tools(&program);
    assert_eq!(tools.len(), 1);
    let tool = tools.into_iter().next().unwrap();
    let (mut writer, reader) = tokio::io::duplex(1024);
    let reader_task = {
        let host = host.clone();
        tokio::spawn(async move { host.read_lines(reader).await })
    };
    let call = tokio::spawn({
        let dir = root.clone();
        async move { tool.call(json!({ "q": 1 }), &ctx("tc_1", &dir)).await }
    });
    let emitted = yield_until(|| log.events().pop().filter(|e| matches!(e.data, EventData::HostToolCall { .. }))).await;
    assert!(
        matches!(&emitted.data, EventData::HostToolCall { call_id, name, args, .. } if call_id == "tc_1" && name == "h" && args == &json!({ "q": 1 })),
        "the call is announced before the answer is awaited: {:?}",
        emitted.data
    );
    writer
        .write_all(br#"{"type":"tool/result","call_id":"tc_1","value":{"count":3},"rendered":"3 refs"}"#)
        .await
        .unwrap();
    writer.write_all(b"\n").await.unwrap();
    let value = call.await.unwrap();
    assert_eq!(
        (value.value, value.rendered.as_deref(), value.is_error),
        (json!({ "count": 3 }), Some("3 refs"), false)
    );
    drop(writer);
    reader_task.await.unwrap();
}

#[tokio::test]
async fn end_of_input_fails_outstanding_waits_instead_of_hanging() {
    let (log, root) = log("protocol-eof");
    let (host, _stop) = Host::new("ep_self".into(), log.clone(), None);
    let req = request(&log, "rq_0001");
    host.read_lines(std::io::Cursor::new(Vec::new())).await;
    let mut chunks: Vec<Chunk> = Vec::new();
    host.transport().stream(req, &mut chunks).await;
    assert!(matches!(chunks.as_slice(), [Chunk::Error { retryable: false, .. }]));
    let tool = host.tool(crate::test_util::spec("h", foe_program::Effect::Pure));
    assert!(tool.call(json!({}), &ctx("tc_9", &root)).await.is_error);
}

#[tokio::test]
async fn inbox_items_are_appended_on_receipt_and_cancel_stops_the_episode() {
    let (log, _) = log("protocol-inbox");
    let (host, stop) = Host::new("ep_self".into(), log.clone(), None);
    let lines = concat!(
        r#"{"type":"inbox/item","source":"parent","content":[{"type":"text","text":"stop early"}],"from":"ep_root","message_id":null}"#,
        "\n",
        r#"{"type":"inbox/item","source":"peer","content":[{"type":"text","text":"p"}],"from":"ep_a","message_id":"tm_1"}"#,
        "\n",
        r#"{"type":"inbox/item","source":"peer","content":[{"type":"text","text":"p"}],"from":"ep_a","message_id":"tm_1"}"#,
        "\n",
        r#"{"type":"cancel"}"#,
        "\n",
    );
    host.read_lines(std::io::Cursor::new(lines.as_bytes().to_vec())).await;
    let items: Vec<_> = log
        .events()
        .into_iter()
        .filter_map(|e| match e.data {
            EventData::InboxItem(i) => Some(i),
            _ => None,
        })
        .collect();
    assert_eq!(items.len(), 2, "the duplicate peer message is dropped");
    assert_eq!((items[0].source, items[0].from.as_deref()), (InboxSource::Parent, Some("ep_root")));
    assert_eq!(stop.borrow().as_deref(), Some("cancelled"));
    InboxSink::append(&host, items[0].clone());
    assert_eq!(log.events().len(), 4);
}

#[tokio::test]
async fn a_task_source_from_the_host_and_unknown_line_types_are_protocol_errors() {
    let (log, _) = log("protocol-bad");
    let (host, stop) = Host::new("ep_self".into(), log.clone(), None);
    let line = r#"{"type":"inbox/item","source":"task","content":[],"from":null,"message_id":null}"#;
    host.read_lines(std::io::Cursor::new(format!("{line}\n").into_bytes())).await;
    assert!(stop.borrow().as_deref().unwrap().contains("source"));
    let (host, stop) = Host::new("ep_self".into(), log.clone(), None);
    host.read_lines(std::io::Cursor::new(b"{\"type\":\"surprise\"}\n".to_vec())).await;
    assert!(stop.borrow().as_deref().unwrap().contains("surprise"));
    let (host, stop) = Host::new("ep_self".into(), log, None);
    host.read_lines(std::io::Cursor::new(b"not json\n".to_vec())).await;
    assert!(stop.borrow().as_deref().unwrap().contains("JSON"));
}

#[derive(Default)]
struct Recording {
    routed: Mutex<Vec<(String, String)>>,
    cancelled: Mutex<bool>,
}

impl Downlink for Recording {
    fn route(&self, episode_id: &str, line: &str) {
        self.routed.lock().unwrap().push((episode_id.into(), line.into()));
    }
    fn cancel_all(&self) {
        *self.cancelled.lock().unwrap() = true;
    }
}

#[tokio::test]
async fn lines_tagged_for_a_descendant_go_down_unchanged() {
    let (log, _) = log("protocol-downlink");
    let down = Arc::new(Recording::default());
    let (host, stop) = Host::new("ep_self".into(), log.clone(), Some(down.clone()));
    let tagged =
        r#"{"type":"model/chunk","request_id":"rq_0001","episode_id":"ep_child","chunk":{"kind":"text","delta":"x"}}"#;
    host.read_lines(std::io::Cursor::new(format!("{tagged}\n{{\"type\":\"cancel\"}}\n").into_bytes())).await;
    assert_eq!(*down.routed.lock().unwrap(), vec![("ep_child".to_string(), tagged.to_string())]);
    assert!(*down.cancelled.lock().unwrap());
    assert_eq!(stop.borrow().as_deref(), Some("cancelled"));
    let (host, stop) = Host::new("ep_self".into(), log, None);
    host.read_lines(std::io::Cursor::new(format!("{tagged}\n").into_bytes())).await;
    assert!(stop.borrow().as_deref().unwrap().contains("ep_child"), "without a downlink a tagged line is an error");
}
