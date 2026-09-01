use super::Writer;
use crate::fold::tests::tmp;
use crate::fold::{read_all, tests as fx};
use crate::*;
use std::sync::{Arc, Mutex};

#[derive(Clone, Default)]
struct Capture(Arc<Mutex<Vec<u8>>>);

impl std::io::Write for Capture {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[test]
fn writer_assigns_contiguous_seq_and_mirrors_identical_bytes() {
    let dir = tmp("seq");
    let mirror = Capture::default();
    let mut writer = Writer::create(&dir, Some(Box::new(mirror.clone()))).unwrap();
    let a = writer.append(EventData::EpisodeStart(fx::start("ep"))).unwrap();
    let b = writer.append(fx::inbox(InboxSource::Task, "t")).unwrap();
    assert_eq!((a.seq, b.seq, writer.next_seq()), (0, 1, 2));
    let file = std::fs::read(dir.join("episode.jsonl")).unwrap();
    assert_eq!(file, *mirror.0.lock().unwrap());
    assert_eq!(read_all(&dir).unwrap(), vec![a, b]);
}

#[test]
fn create_refuses_an_existing_log_and_open_resumes_it() {
    let dir = tmp("reopen");
    let mut writer = Writer::create(&dir, None).unwrap();
    writer.append(EventData::EpisodeStart(fx::start("ep"))).unwrap();
    drop(writer);
    assert!(matches!(Writer::create(&dir, None), Err(LogError::Io(_))));
    let mut reopened = Writer::open(&dir, None).unwrap();
    assert_eq!(reopened.next_seq(), 1);
    let event = reopened.append(fx::inbox(InboxSource::Task, "t")).unwrap();
    assert_eq!(event.seq, 1);
    assert_eq!(read_all(&dir).unwrap().len(), 2);
}

#[test]
fn writer_rejects_events_that_break_the_structural_rules() {
    let dir = tmp("rules");
    let mut writer = Writer::create(&dir, None).unwrap();
    assert!(matches!(writer.append(fx::inbox(InboxSource::Task, "t")), Err(LogError::Invalid { seq: 0, .. })));
    writer.append(EventData::EpisodeStart(fx::start("ep"))).unwrap();
    writer.append(EventData::EpisodeEnd { outcome: Outcome::Failed { error: "e".into() } }).unwrap();
    assert!(matches!(writer.append(fx::header()), Err(LogError::Invalid { seq: 2, .. })));
    assert_eq!(read_all(&dir).unwrap().len(), 2, "a rejected event is not written");
}

/// docs/log-format.md `tool/result`: exactly one per tool call, matched by
/// `call_id`. The writer refuses a second result and a result for a call no
/// assistant message issued, so neither can reach the file.
#[test]
fn writer_rejects_a_duplicate_or_orphan_tool_result() {
    let dir = tmp("results");
    let mut writer = Writer::create(&dir, None).unwrap();
    writer.append(EventData::EpisodeStart(fx::start("ep"))).unwrap();
    writer.append(fx::inbox(InboxSource::Task, "t")).unwrap();
    writer.append(fx::header()).unwrap();
    writer.append(fx::request(1, 2, vec![1], vec![])).unwrap();
    writer.append(fx::assistant(1, "", vec![fx::call("tc_1")], false)).unwrap();
    assert_eq!(writer.state().open.len(), 1);
    assert!(matches!(writer.append(fx::result(1, "tc_9", "x")), Err(LogError::Invalid { seq: 5, .. })));
    writer.append(fx::result(1, "tc_1", "x")).unwrap();
    assert!(writer.state().open.is_empty());
    assert!(matches!(writer.append(fx::result(1, "tc_1", "again")), Err(LogError::Invalid { seq: 6, .. })));
    assert_eq!(read_all(&dir).unwrap().len(), 6);
}

/// docs/log-format.md `tool/rendering-archive`: an archive names an open
/// call, uses its digest-derived path, and immediately precedes its result.
#[test]
fn writer_enforces_the_rendering_archive_pair() {
    let dir = tmp("rendering-archive");
    let mut writer = Writer::create(&dir, None).unwrap();
    writer.append(EventData::EpisodeStart(fx::start("ep"))).unwrap();
    writer.append(fx::inbox(InboxSource::Task, "t")).unwrap();
    writer.append(fx::assistant(1, "", vec![fx::call("tc_1")], false)).unwrap();
    let hex = "a".repeat(64);
    let archive = RenderingArchive {
        step: 1,
        call_id: "tc_1".into(),
        file: format!("renderings/{hex}.txt"),
        digest: format!("sha256:{hex}"),
        bytes: 3,
    };
    writer.append(EventData::ToolRenderingArchive(archive)).unwrap();
    assert!(matches!(writer.append(fx::header()), Err(LogError::Invalid { seq: 4, .. })));
    writer.append(fx::result(1, "tc_1", "cut")).unwrap();

    let wrong_dir = tmp("rendering-archive-path");
    let mut wrong = Writer::create(&wrong_dir, None).unwrap();
    wrong.append(EventData::EpisodeStart(fx::start("ep"))).unwrap();
    wrong.append(fx::inbox(InboxSource::Task, "t")).unwrap();
    wrong.append(fx::assistant(1, "", vec![fx::call("tc_2")], false)).unwrap();
    let invalid = RenderingArchive {
        step: 1,
        call_id: "tc_2".into(),
        file: "renderings/other.txt".into(),
        digest: format!("sha256:{}", "b".repeat(64)),
        bytes: 3,
    };
    assert!(matches!(wrong.append(EventData::ToolRenderingArchive(invalid)), Err(LogError::Invalid { seq: 3, .. })));
}
