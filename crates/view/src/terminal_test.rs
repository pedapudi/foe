use super::*;
use foe_log::{AssistantMessage, Event, InboxItem, SpawnContext, StopReason, ToolCall, Usage};
use serde_json::json;

fn start(id: &str) -> EventData {
    let fixture = include_str!("../tests/fixtures/run/episode.jsonl");
    let event: Event = serde_json::from_str(fixture.lines().next().unwrap()).unwrap();
    let EventData::EpisodeStart(mut start) = event.data else { panic!() };
    start.id = id.into();
    start.contract = json!({"name": id});
    EventData::EpisodeStart(start)
}

fn message(text: &str) -> EventData {
    EventData::AssistantMessage(AssistantMessage {
        step: 1,
        request_id: "request".into(),
        text: text.into(),
        tool_calls: vec![ToolCall {
            id: "call".into(),
            name: "hidden-tool".into(),
            args: json!({"secret": "hidden-args"}),
        }],
        thinking: Vec::new(),
        stop: StopReason::Tool,
        usage: Usage::default(),
        interrupted: false,
    })
}

fn spawn(child: &str) -> EventData {
    EventData::SpawnStart {
        child_id: child.into(),
        contract: child.into(),
        context: SpawnContext::Fresh,
        call_id: child.into(),
    }
}

fn returned(child: &str, text: &str) -> EventData {
    EventData::SpawnEnd { child_id: child.into(), outcome: Outcome::Completed { value: json!(text) } }
}

fn append(dir: &Path, events: &[EventData]) {
    std::fs::create_dir_all(dir).unwrap();
    let mut file = std::fs::OpenOptions::new().create(true).append(true).open(dir.join("episode.jsonl")).unwrap();
    for data in events {
        let event = Event { seq: 0, time: 0, version: None, data: data.clone() };
        writeln!(file, "{}", serde_json::to_string(&event).unwrap()).unwrap();
    }
}

/// docs/viewer.md: the terminal conversation hides tool traffic and internal input.
#[test]
fn conversation_includes_only_visible_messages() {
    let mut terminal = Terminal::new(Vec::new(), false);
    terminal.event("lead", &start("lead")).unwrap();
    let mut inbox = InboxItem {
        source: InboxSource::Task,
        content: vec![ContentBlock::Text { text: "Visible task".into() }],
        from: None,
        message_id: None,
    };
    terminal.event("lead", &EventData::InboxItem(inbox.clone())).unwrap();
    terminal.event("lead", &spawn("worker")).unwrap();
    terminal.event("worker", &start("worker")).unwrap();
    inbox.content = vec![ContentBlock::Text { text: "hidden-tool-input-in-child-task".into() }];
    terminal.event("worker", &EventData::InboxItem(inbox.clone())).unwrap();
    terminal.event("lead", &returned("worker", "Work complete.")).unwrap();
    inbox.source = InboxSource::System;
    inbox.content = vec![ContentBlock::Text { text: "hidden-system".into() }];
    terminal.event("lead", &EventData::InboxItem(inbox)).unwrap();
    terminal.event("lead", &message("Visible response\nwith a second line")).unwrap();
    terminal.event("lead", &message("")).unwrap();
    let fixture = include_str!("../tests/fixtures/run/episode.jsonl");
    for line in fixture.lines() {
        let event: Event = serde_json::from_str(line).unwrap();
        if matches!(
            event.data,
            EventData::ToolResult(_) | EventData::RequestHeader(_) | EventData::AssistantChunk { .. }
        ) {
            terminal.event("lead", &event.data).unwrap();
        }
    }
    let output = String::from_utf8(terminal.output).unwrap();
    assert!(output.contains("lead · You\n│ Visible task"), "{output}");
    assert!(output.contains("Visible response\n│ with a second line"), "{output}");
    assert_eq!(output.matches("· Assistant").count(), 1);
    for hidden in ["hidden-", "import pytest", "tool_calls", "<first>", "\x1b"] {
        assert!(!output.contains(hidden), "{output}");
    }
}

/// docs/viewer.md: messages from concurrent and nested branches precede their returned results.
#[test]
fn polling_preserves_branch_returns_and_does_not_repeat_messages() {
    let root = tempfile::tempdir().unwrap();
    let child = root.path().join("children/reviewer");
    let sibling = root.path().join("children/tester");
    let grandchild = child.join("children/helper");
    append(root.path(), &[start("lead"), spawn("reviewer"), spawn("tester")]);
    append(&child, &[start("reviewer"), message("Reviewing changes."), spawn("helper")]);
    append(&grandchild, &[start("helper"), message("Checking details.")]);
    append(&sibling, &[start("tester"), message("Running checks.")]);
    let mut terminal = Terminal::new(Vec::new(), false);
    terminal.poll(root.path()).unwrap();
    let before = terminal.output.clone();
    terminal.poll(root.path()).unwrap();
    assert_eq!(terminal.output, before);
    append(&grandchild, &[message("Details checked.")]);
    append(&child, &[returned("helper", "Details passed."), message("Review complete.")]);
    append(root.path(), &[returned("reviewer", "Review passed."), returned("tester", "Tests passed.")]);
    terminal.poll(root.path()).unwrap();
    terminal
        .finish(&Ok(Outcome::Completed { value: json!({"summary": "Ready.", "checks": ["Review", "Tests"]}) }))
        .unwrap();
    let output = String::from_utf8(terminal.output.clone()).unwrap();
    assert!(output.contains("├─╮ Branch: reviewer"), "{output}");
    for (first, second) in [
        ("Details checked.", "Details passed."),
        ("Details passed.", "Review complete."),
        ("Review complete.", "Review passed."),
        ("Running checks.", "Tests passed."),
        ("Tests passed.", "Final · Completed"),
    ] {
        assert!(output.find(first).unwrap() < output.find(second).unwrap(), "{first} before {second}: {output}");
    }
    assert!(output.contains("reviewer → lead · Completed"), "{output}");
    assert!(output.contains("summary:\n  Ready."), "{output}");
    assert_eq!(output.matches("Review complete.").count(), 1);
    let finished = terminal.output.clone();
    terminal.poll(root.path()).unwrap();
    assert_eq!(terminal.output, finished);
}

/// docs/viewer.md: incomplete final lines are displayed after their newline arrives.
#[test]
fn polling_waits_for_complete_lines_and_discovers_children_later() {
    let root = tempfile::tempdir().unwrap();
    let mut terminal = Terminal::new(Vec::new(), false);
    terminal.poll(root.path()).unwrap();
    append(root.path(), &[start("lead"), spawn("worker")]);
    terminal.poll(root.path()).unwrap();
    let child = root.path().join("children/worker");
    append(&child, &[start("worker")]);
    let event = Event { seq: 1, time: 0, version: None, data: message("Arrived later.") };
    let line = serde_json::to_string(&event).unwrap();
    let mut file = std::fs::OpenOptions::new().append(true).open(child.join("episode.jsonl")).unwrap();
    file.write_all(line.as_bytes()).unwrap();
    terminal.poll(root.path()).unwrap();
    assert!(!String::from_utf8_lossy(&terminal.output).contains("Arrived later."));
    file.write_all(b"\n").unwrap();
    terminal.poll(root.path()).unwrap();
    assert_eq!(String::from_utf8_lossy(&terminal.output).matches("Arrived later.").count(), 1);
}

/// docs/viewer.md: recorded text cannot introduce terminal control sequences.
#[test]
fn terminal_controls_are_removed_and_color_is_optional() {
    let mut terminal = Terminal::new(Vec::new(), true);
    terminal.event("lead", &start("lead")).unwrap();
    terminal.event("lead", &message("Safe\x1b[2J\r\x08\u{009b}31mtext\n\tIndented")).unwrap();
    let output = String::from_utf8(terminal.output).unwrap();
    assert!(output.contains("\x1b[1;36mlead · Assistant\x1b[0m"));
    assert!(output.contains("Safe[2J31mtext\n│ \tIndented"));
    assert!(!output.contains("\x1b[2J"));
    assert_eq!(display_value(&json!({"empty": [], "count": 0})), "count:\n  0\n\nempty:\n  []");
    assert_eq!(display_value(&json!("{\"summary\":\"Ready.\"}")), "summary:\n  Ready.");
    assert_eq!(display_value(&json!(["First", "Second"])), "First\n\nSecond");
}

/// docs/viewer.md: every outcome has a readable final result.
#[test]
fn unsuccessful_outcomes_and_output_errors_are_reported() {
    for outcome in [
        Outcome::Blocked { code: foe_log::BlockedCode::MissingCapability, message: "A reader is required.".into() },
        Outcome::Exhausted { limit: foe_log::ExhaustedLimit::Seconds },
        Outcome::Failed { error: "Model response failed.".into() },
    ] {
        let mut terminal = Terminal::new(Vec::new(), false);
        terminal.finish(&Ok(outcome.clone())).unwrap();
        let output = String::from_utf8(terminal.output).unwrap();
        let (label, body) = result_text(&outcome);
        assert!(output.contains(&format!("Final · {label}")));
        assert!(output.contains(&body));
    }
    struct Closed;
    impl Write for Closed {
        fn write(&mut self, _: &[u8]) -> io::Result<usize> {
            Err(io::ErrorKind::BrokenPipe.into())
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }
    let error = Terminal::new(Closed, false).finish(&Err("Run failed.".into())).unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::BrokenPipe);
}
