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

/// Rows joined by line feeds, a title in brackets.
fn rendered(value: Value) -> String {
    let rows = display_value(&value).into_iter().map(|row| match row {
        Row::Title(title) => format!("[{title}]"),
        Row::Text(text) => text,
    });
    rows.collect::<Vec<_>>().join("\n")
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
    let mut terminal = Terminal::new(Vec::new(), false, false, 80);
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
    assert!(output.contains("lead · You\n│   Visible task"), "{output}");
    assert!(output.contains("Visible response\n│   with a second line"), "{output}");
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
    let mut terminal = Terminal::new(Vec::new(), false, false, 80);
    terminal.poll(root.path()).unwrap();
    let before = terminal.output.clone();
    terminal.poll(root.path()).unwrap();
    assert_eq!(terminal.output, before);
    append(&grandchild, &[message("Details checked.")]);
    append(&child, &[returned("helper", "Details passed."), message("Review complete.")]);
    append(root.path(), &[returned("reviewer", "Review passed."), returned("tester", "Tests passed.")]);
    terminal.poll(root.path()).unwrap();
    terminal
        .finish(&Ok(Outcome::Completed { value: json!({"summary": "Ready.", "checks": ["Review", "Tests"]}) }), None)
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
    assert!(output.contains("Final · Completed\n  Ready.\n\n  Checks\n  - Review\n  - Tests\n"), "{output}");
    assert_eq!(output.matches("Review complete.").count(), 1);
    let finished = terminal.output.clone();
    terminal.poll(root.path()).unwrap();
    assert_eq!(terminal.output, finished);
}

/// docs/viewer.md: incomplete final lines are displayed after their newline arrives.
#[test]
fn polling_waits_for_complete_lines_and_discovers_children_later() {
    let root = tempfile::tempdir().unwrap();
    let mut terminal = Terminal::new(Vec::new(), false, false, 80);
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

/// docs/viewer.md: wrapped continuations and blank lines carry the lane connectors.
#[test]
fn long_lines_wrap_inside_the_lanes() {
    let mut terminal = Terminal::new(Vec::new(), false, false, 40);
    terminal.event("lead", &start("lead")).unwrap();
    terminal.event("lead", &spawn("worker")).unwrap();
    terminal.event("worker", &start("worker")).unwrap();
    let text = "A sentence long enough to wrap twice at forty columns inside two lanes.\n\
                - A list item whose continuation keeps the item's indentation\n  indented continuation\n\
                Supercalifragilisticexpialidociousantidisestablishmentarianism";
    terminal.event("worker", &message(text)).unwrap();
    let output = String::from_utf8(terminal.output).unwrap();
    let body: Vec<&str> = output.lines().skip_while(|line| !line.contains("worker · Assistant")).skip(1).collect();
    assert_eq!(
        body,
        [
            "│ │   A sentence long enough to wrap",
            "│ │   twice at forty columns inside two",
            "│ │   lanes.",
            "│ │   - A list item whose continuation",
            "│ │     keeps the item's indentation",
            "│ │     indented continuation",
            "│ │   Supercalifragilisticexpialidocious",
            "│ │   antidisestablishmentarianism",
            "│ │",
        ],
        "{output}"
    );
    assert!(body.iter().all(|line| line.chars().count() <= 40), "{output}");
    assert_eq!(wrap("", 10), [""]);
    assert_eq!(wrap("1. one two three", 8), ["1. one", "   two", "   three"]);
    assert_eq!(wrap("• bullet text", 8), ["• bullet", "  text"]);
    assert_eq!(wrap("• bullets", 3), ["•", "  b", "  u", "  l", "  l", "  e", "  t", "  s"]);
}

/// docs/viewer.md: recorded text cannot introduce terminal control sequences.
#[test]
fn terminal_controls_are_removed_and_color_is_optional() {
    let mut terminal = Terminal::new(Vec::new(), true, false, 80);
    terminal.event("lead", &start("lead")).unwrap();
    terminal.event("lead", &message("Safe\x1b[2J\r\x08\u{009b}31mtext\n\tIndented")).unwrap();
    let output = String::from_utf8(terminal.output).unwrap();
    assert!(output.contains("\x1b[1;36mlead · Assistant\x1b[0m"));
    assert!(output.contains("Safe[2J31mtext\n│   \tIndented"));
    assert!(!output.contains("\x1b[2J"));
    assert_eq!(rendered(json!({"empty": [], "count": 0})), "[Count]\n0");
    assert_eq!(rendered(json!("{\"summary\":\"Ready.\"}")), "Ready.");
    assert_eq!(rendered(json!(["First", "Second"])), "- First\n- Second");
}

/// docs/viewer.md: a completed object opens with its summary and gives every other field a titled section.
#[test]
fn completed_values_read_as_titled_sections() {
    let coding = json!({
        "summary": "Added the missing return.",
        "changed_paths": ["src/brackets.py"],
        "validation": ["python3 -m unittest passes", "grep finds the new return"],
        "unresolved_risks": [],
        "learned": [{"claim": "The tests fail before the change.", "seq": 7}],
        "findings": []
    });
    assert_eq!(
        rendered(coding.clone()),
        "Added the missing return.\n\n[Changed paths]\n- src/brackets.py\n\n[Learned]\n\
         - The tests fail before the change. (seq 7)\n\n[Validation]\n- python3 -m unittest passes\n\
         - grep finds the new return"
    );
    let generic = json!({
        "count": 0,
        "name": "",
        "items": [{"kind": "x", "note": "", "tags": ["t1"]}, ["p", "q"], "plain\nsecond line"],
        "nested": {"key": "value", "list": ["a", "b"], "deep": {"n": 1}, "none": {}}
    });
    assert_eq!(
        rendered(generic),
        "[Count]\n0\n\n[Items]\n- kind: x\n  tags:\n    - t1\n- - p\n  - q\n- plain\n  second line\n\n\
         [Nested]\ndeep:\n  n: 1\nkey: value\nlist:\n  - a\n  - b"
    );
    assert_eq!(rendered(json!("Plain text\nwith a second line")), "Plain text\nwith a second line");
    assert_eq!(rendered(json!({"learned": [{"claim": "No sequence."}]})), "[Learned]\n- claim: No sequence.");
    assert_eq!(rendered(json!({})), "");
    let mut terminal = Terminal::new(Vec::new(), true, false, 80);
    terminal.finish(&Ok(Outcome::Completed { value: coding }), Some("http://127.0.0.1:1/?token=a")).unwrap();
    let output = String::from_utf8(terminal.output).unwrap();
    assert!(output.contains("\n  \x1b[1mChanged paths\x1b[0m\n  - src/brackets.py\n"), "{output}");
    assert!(output.contains("\n  Added the missing return.\n\n"), "{output}");
    assert!(output.ends_with("\n\n  Viewer: http://127.0.0.1:1/?token=a\n"), "{output}");
}

/// docs/viewer.md: every outcome has a readable final result.
#[test]
fn unsuccessful_outcomes_and_output_errors_are_reported() {
    for outcome in [
        Outcome::Blocked { code: foe_log::BlockedCode::MissingCapability, message: "A reader is required.".into() },
        Outcome::Exhausted { limit: foe_log::ExhaustedLimit::Seconds },
        Outcome::Failed { error: "Model response failed.".into() },
    ] {
        let mut terminal = Terminal::new(Vec::new(), false, false, 80);
        terminal.finish(&Ok(outcome.clone()), None).unwrap();
        let output = String::from_utf8(terminal.output).unwrap();
        let (label, rows) = result_text(&outcome);
        let [Row::Text(body)] = rows.as_slice() else { panic!("{label} has one line") };
        assert!(output.contains(&format!("Final · {label}\n  {body}\n")), "{output}");
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
    let error = Terminal::new(Closed, false, false, 80).finish(&Err("Run failed.".into()), None).unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::BrokenPipe);
}

/// The progress lines an output holds: the redrawn segments between erasures
/// that carry no appended block.
fn progress(output: &[u8]) -> Vec<String> {
    let text = String::from_utf8(output.to_vec()).unwrap();
    text.split(ERASE).filter(|part| !part.is_empty() && !part.contains('\n')).map(String::from).collect()
}

/// docs/viewer.md: the progress line pulses one frame per tick and reports the
/// active episode, the elapsed seconds, and the tool calls since the last
/// displayed assistant message.
#[tokio::test(start_paused = true)]
async fn the_progress_line_pulses_once_per_tick_and_counts_tool_calls() {
    let mut terminal = Terminal::new(Vec::new(), false, true, 80);
    terminal.event("lead", &start("lead")).unwrap();
    for _ in 0..3 {
        terminal.event("lead", &message("")).unwrap();
    }
    tokio::time::advance(std::time::Duration::from_secs(12)).await;
    for _ in 0..12 {
        terminal.status().unwrap();
    }
    let lines = progress(&terminal.output);
    assert_eq!(lines.len(), 12);
    assert_eq!(lines[0], "·  [lead]  [12 s]  [3 tool calls]");
    assert_eq!(lines.iter().map(|line| line.chars().next().unwrap()).collect::<String>(), "·✶✷✸⊛◎⊛✸✷✶··");
    terminal.event("lead", &message("Done.")).unwrap();
    tokio::time::advance(std::time::Duration::from_secs(1)).await;
    terminal.status().unwrap();
    assert_eq!(progress(&terminal.output).pop().unwrap(), "✶  [lead]  [13 s]  [1 tool call]");
}

/// docs/viewer.md: the progress line carries color on a terminal, is erased
/// before an appended block and when the display stops, and is absent from
/// redirected output.
#[tokio::test(start_paused = true)]
async fn the_progress_line_yields_to_blocks_and_to_redirected_output() {
    let mut terminal = Terminal::new(Vec::new(), true, true, 80);
    terminal.event("lead", &start("lead")).unwrap();
    terminal.status().unwrap();
    let line = String::from_utf8(terminal.output.clone()).unwrap();
    assert_eq!(
        line,
        "\r\x1b[K\x1b[38;2;199;121;26m·\x1b[0m  \x1b[2m[\x1b[0m\x1b[1;36mlead\x1b[0m\x1b[2m]\x1b[0m  \
         \x1b[2m[\x1b[0m\x1b[2m0 s\x1b[0m\x1b[2m]\x1b[0m  \x1b[2m[\x1b[0m\x1b[32m0 tool calls\x1b[0m\x1b[2m]\x1b[0m"
    );
    terminal.event("lead", &message("Recorded.")).unwrap();
    let output = String::from_utf8(terminal.output.clone()).unwrap();
    let block = output.strip_prefix(&line).unwrap();
    assert!(block.starts_with("\r\x1b[K\x1b[2m● \x1b[0m\x1b[1;36mlead · Assistant"), "{block}");
    terminal.status().unwrap();
    terminal.erase().and_then(|()| terminal.erase()).unwrap();
    assert!(String::from_utf8(terminal.output.clone()).unwrap().ends_with("]\x1b[0m\r\x1b[K"));
    let mut piped = Terminal::new(Vec::new(), true, false, 80);
    piped.event("lead", &start("lead")).unwrap();
    piped.status().unwrap();
    piped.event("lead", &message("Recorded.")).unwrap();
    assert!(!String::from_utf8(piped.output).unwrap().contains(ERASE));
}

/// docs/viewer.md: the episode name is shortened so that the redraw stays on one row.
#[tokio::test(start_paused = true)]
async fn the_progress_line_stays_inside_the_terminal_width() {
    let mut terminal = Terminal::new(Vec::new(), false, true, 40);
    terminal.event("lead", &start("a-contract-name-longer-than-the-row")).unwrap();
    terminal.status().unwrap();
    let line = progress(&terminal.output).pop().unwrap();
    assert_eq!(line, "·  [a-contract-n]  [0 s]  [0 tool calls]");
    assert_eq!(line.chars().count(), 40);
}
