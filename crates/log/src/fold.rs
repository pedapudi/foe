//! Reading a log back into state. See docs/log-format.md "Derived messages".
//!
//! `derive_messages` is the one rule that the runtime, the viewer, and the
//! Python package all apply identically. A reader that recomputes the
//! message list for a request and finds it differs from the recorded
//! `model/request.messages` has found a runtime defect.

use crate::{
    CompactionSummary, ContentBlock, Event, EventData, InboxSource, LogError, Message, Outcome, State,
    SUMMARY_REQUEST_PREFIX,
};
use std::collections::BTreeMap;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

/// File name of the log inside an episode directory.
pub const LOG_FILE: &str = "episode.jsonl";

/// Parses every line of `episode.jsonl` under `dir`. Succeeds on a log
/// without `episode/end`; structural validation is left to [`fold`].
pub fn read_all(dir: &Path) -> Result<Vec<Event>, LogError> {
    read_from(dir, 0).map(|(events, _)| events)
}

/// Parses events appended after byte offset `from`, returning them and the
/// new offset. Only newline-terminated lines are parsed; a trailing partial
/// line is left for the next call. A parse error names the line counted
/// from `from`. A missing log surfaces as an `Io` error with `NotFound`.
pub fn read_from(dir: &Path, from: u64) -> Result<(Vec<Event>, u64), LogError> {
    let mut file = std::fs::File::open(dir.join(LOG_FILE))?;
    file.seek(SeekFrom::Start(from))?;
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)?;
    let (events, consumed) = parse_lines(&bytes)?;
    Ok((events, from + consumed))
}

/// Parses complete lines of `bytes`; returns the events and the byte count
/// of the lines parsed.
fn parse_lines(bytes: &[u8]) -> Result<(Vec<Event>, u64), LogError> {
    let mut events = Vec::new();
    let mut consumed = 0usize;
    for (index, line) in bytes.split_inclusive(|b| *b == b'\n').enumerate() {
        if line.last() != Some(&b'\n') {
            break;
        }
        consumed += line.len();
        if line.iter().all(|b| b.is_ascii_whitespace()) {
            continue;
        }
        let event = serde_json::from_slice(line).map_err(|source| LogError::Parse { line: index as u64, source })?;
        events.push(event);
    }
    Ok((events, consumed as u64))
}

/// Folds events into [`State`]. Validates the structural rules: `seq`
/// contiguous from 0, `episode/start` first, at most one `episode/end` and
/// it is last, at most one result per tool call and none without a call.
/// A log that has ended must also give every tool call a result; a log
/// still in progress may have calls awaiting their results.
pub fn fold(events: &[Event]) -> Result<State, LogError> {
    let mut state = State::default();
    for (index, event) in events.iter().enumerate() {
        if event.seq != index as u64 {
            return Err(LogError::Invalid { seq: event.seq, rule: "seq is contiguous from 0" });
        }
        validate_next(&state, event)?;
        apply(&mut state, event);
    }
    if state.outcome.is_some() && !state.pending_calls.is_empty() {
        return Err(LogError::Invalid {
            seq: events.len() as u64 - 1,
            rule: "every tool call has a result before episode/end",
        });
    }
    Ok(state)
}

/// Advances `state` by one event. Performs no validation.
pub fn apply(state: &mut State, event: &Event) {
    match &event.data {
        EventData::EpisodeStart(start) => state.start = Some(start.clone()),
        EventData::EpisodeEnd { outcome } => state.outcome = Some(outcome.clone()),
        EventData::SeedEnd {} => state.seeded_through = Some(event.seq),
        EventData::RequestHeader(header) => {
            state.header_seq = Some(event.seq);
            state.header = Some(header.clone());
        }
        EventData::ModelRequest(request) => {
            state.model_calls += 1;
            for seq in &request.consumed {
                if let Some(entry) = state.inbox.get_mut(seq) {
                    entry.1 = true;
                }
            }
        }
        EventData::AssistantMessage(message) => {
            state.usage.input += message.usage.input;
            state.usage.output += message.usage.output;
            state.usage.cache_read += message.usage.cache_read;
            for call in &message.tool_calls {
                state.settled_calls.remove(&call.id);
                state.pending_calls.insert(call.id.clone());
            }
        }
        EventData::ToolResult(result) => {
            state.pending_calls.remove(&result.call_id);
            state.settled_calls.insert(result.call_id.clone());
        }
        EventData::InboxItem(item) => {
            state.inbox.insert(event.seq, (item.clone(), false));
        }
        EventData::SpawnStart { child_id, .. } => {
            state.children.insert(child_id.clone(), None);
        }
        EventData::SpawnEnd { child_id, outcome } => {
            state.children.insert(child_id.clone(), Some(outcome.clone()));
        }
        _ => {}
    }
}

/// Derives the message list a request at `upto_seq` would carry, by the
/// rule in the specification. After the latest `compaction/summary`, the
/// list opens with the task and the continuation message and continues
/// from the summary's `first_kept_seq`; the request being built
/// contributes `consumed_inbox` at the end.
pub fn derive_messages(events: &[Event], upto_seq: u64, consumed_inbox: &[u64]) -> Vec<Message> {
    let summary = events.iter().rev().skip_while(|e| e.seq >= upto_seq).find_map(|e| match &e.data {
        EventData::CompactionSummary(summary) => Some(summary),
        _ => None,
    });
    let mut messages: Vec<Message> =
        summary.into_iter().flat_map(|s| [user_text(&s.state.task), user_text(&render_continuation(s))]).collect();
    messages.extend(derive_span(events, summary.map_or(0, |s| s.first_kept_seq), upto_seq, consumed_inbox));
    messages
}

/// Rules 3 to 7 over the events with `seq` in `[from_seq, upto_seq)`, then
/// the items `consumed_inbox` names. Each `model/request` contributes the
/// items its `consumed` list names, at the request's position, wherever
/// the items themselves lie. A summarization request and its response
/// contribute nothing.
pub fn derive_span(events: &[Event], from_seq: u64, upto_seq: u64, consumed_inbox: &[u64]) -> Vec<Message> {
    let items: BTreeMap<u64, &[ContentBlock]> = events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::InboxItem(item) => Some((e.seq, item.content.as_slice())),
            _ => None,
        })
        .collect();
    let user = |consumed: &[u64]| {
        let content: Vec<_> = consumed.iter().filter_map(|s| items.get(s)).flat_map(|c| c.iter().cloned()).collect();
        (!content.is_empty()).then_some(Message::User { content })
    };
    let ordinary = |request_id: &str| !request_id.starts_with(SUMMARY_REQUEST_PREFIX);
    let mut messages: Vec<Message> = Vec::new();
    for event in events.iter().filter(|e| e.seq >= from_seq && e.seq < upto_seq) {
        match &event.data {
            EventData::ModelRequest(request) if ordinary(&request.request_id) => {
                messages.extend(user(&request.consumed))
            }
            EventData::AssistantMessage(message) if ordinary(&message.request_id) => {
                messages.push(Message::Assistant {
                    text: message.text.clone(),
                    tool_calls: message.tool_calls.clone(),
                    thinking: message.thinking.clone(),
                })
            }
            EventData::ToolResult(result) => messages.push(Message::Tool {
                call_id: result.call_id.clone(),
                name: result.name.clone(),
                rendered: result.rendered.clone(),
                is_error: result.is_error,
            }),
            _ => {}
        }
    }
    messages.extend(user(consumed_inbox));
    messages
}

fn user_text(text: &str) -> Message {
    Message::User { content: vec![ContentBlock::Text { text: text.to_string() }] }
}

/// The continuation message a compaction contributes: the state as
/// labeled lines, then the model's summary. Identity hashes the three
/// templates; `STATE_LABELS` is the schema the lines follow.
pub const CONTINUATION_MESSAGE: &str = "## Continuation state\n\n{state}\n\n## Summary\n\n{summary}";
pub const STATE_ITEM: &str = "\n- {item}";
pub const STATE_NONE: &str = "(none)";
pub const STATE_LABELS: [&str; 8] = [
    "covered",
    "done_when",
    "outstanding_findings",
    "files_read",
    "files_written",
    "files_edited",
    "children",
    "budget_remaining",
];

pub fn render_continuation(summary: &CompactionSummary) -> String {
    let s = &summary.state;
    // A value follows its label's colon: a scalar after one space, a list
    // as one item per line below it.
    let list = |items: &[String]| match items.is_empty() {
        true => format!(" {STATE_NONE}"),
        false => items.iter().map(|item| STATE_ITEM.replace("{item}", item)).collect(),
    };
    let amount = |n: Option<u64>| n.map_or("unlimited".to_string(), |n| n.to_string());
    let (b, c) = (s.budget_remaining, s.covered);
    let children: Vec<String> =
        s.children.iter().map(|k| format!("{} ({}): {}", k.id, k.program, kind(&k.outcome))).collect();
    let values = [
        format!(" seq {} to {}", c.first_seq, c.last_seq),
        format!(" {}", s.done_when),
        list(&s.outstanding_findings),
        list(&s.files.read),
        list(&s.files.written),
        list(&s.files.edited),
        list(&children),
        format!(" model_calls {}, tokens {}, seconds {}", amount(b.model_calls), amount(b.tokens), amount(b.seconds)),
    ];
    let lines: Vec<String> = STATE_LABELS.iter().zip(values).map(|(label, value)| format!("{label}:{value}")).collect();
    CONTINUATION_MESSAGE.replace("{state}", &lines.join("\n")).replace("{summary}", &summary.summary)
}

/// `completed`, `blocked <code>`, `exhausted <limit>`, or `failed`.
fn kind(outcome: &Outcome) -> String {
    let value = serde_json::to_value(outcome).unwrap_or_default();
    let detail = value["code"].as_str().or(value["limit"].as_str()).map(|d| format!(" {d}")).unwrap_or_default();
    format!("{}{detail}", value["kind"].as_str().unwrap_or_default())
}

/// Validates one event against the preceding ones. Returns the rule
/// violated, if any. Used by the writer before appending.
pub fn validate_next(prior: &State, event: &Event) -> Result<(), LogError> {
    let seq = event.seq;
    let invalid = |rule| Err(LogError::Invalid { seq, rule });
    if prior.outcome.is_some() {
        return invalid("episode/end is the last event");
    }
    match (&prior.start, &event.data) {
        (None, EventData::EpisodeStart(_)) if seq == 0 => return Ok(()),
        (None, _) => return invalid("episode/start is the first event, at seq 0"),
        (Some(_), EventData::EpisodeStart(_)) => return invalid("exactly one episode/start per log"),
        _ => {}
    }
    match &event.data {
        EventData::InboxItem(item)
            if item.source == InboxSource::Task
                && (seq != 1 || prior.inbox.values().any(|(i, _)| i.source == InboxSource::Task)) =>
        {
            return invalid("exactly one task item per log, at seq 1");
        }
        EventData::ModelRequest(request) => {
            if prior.header_seq != Some(request.header_seq) {
                return invalid("header_seq names the request/header in effect");
            }
            if request.consumed.iter().any(|s| *s >= seq || !matches!(prior.inbox.get(s), Some((_, false)))) {
                return invalid("consumed names earlier inbox items that no earlier request consumed");
            }
        }
        EventData::ToolResult(result) if prior.settled_calls.contains(&result.call_id) => {
            return invalid("exactly one tool/result per tool call");
        }
        EventData::ToolResult(result) if !prior.pending_calls.contains(&result.call_id) => {
            return invalid("tool/result names a tool call in an earlier assistant/message");
        }
        EventData::SeedEnd {} if prior.seeded_through.is_some() => return invalid("at most one seed/end per log"),
        _ => {}
    }
    Ok(())
}

#[cfg(test)]
#[path = "fold_test.rs"]
pub(crate) mod tests;
