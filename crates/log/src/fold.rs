//! Reading a log back into state. See docs/log-format.md "Derived messages".
//!
//! `derive_messages` is the one rule that the runtime, the viewer, and the
//! Python package all apply identically. A reader that recomputes the
//! message list for a request and finds it differs from the recorded
//! `model/request.messages` has found a runtime defect.

use crate::{Event, EventData, InboxSource, LogError, Message, State};
use std::collections::BTreeMap;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

/// File name of the log inside an episode directory.
pub const LOG_FILE: &str = "episode.jsonl";

/// Parses every line of `episode.jsonl` under `dir`. Succeeds on a log
/// without `episode/end`; structural validation is left to [`fold`].
pub fn read_all(dir: &Path) -> Result<Vec<Event>, LogError> {
    let text = std::fs::read_to_string(dir.join(LOG_FILE))?;
    parse_lines(text.as_bytes()).map(|(events, _)| events)
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
            state.pending_calls.extend(message.tool_calls.iter().map(|c| c.id.clone()));
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
/// rule in the specification. Each earlier `model/request` contributes the
/// items its `consumed` list names, at the request's position; the request
/// being built contributes `consumed_inbox` at the end.
pub fn derive_messages(events: &[Event], upto_seq: u64, consumed_inbox: &[u64]) -> Vec<Message> {
    let items: BTreeMap<u64, &[crate::ContentBlock]> = events
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
    let mut messages: Vec<Message> = Vec::new();
    for event in events.iter().take_while(|e| e.seq < upto_seq) {
        match &event.data {
            EventData::ModelRequest(request) => messages.extend(user(&request.consumed)),
            EventData::AssistantMessage(message) => messages.push(Message::Assistant {
                text: message.text.clone(),
                tool_calls: message.tool_calls.clone(),
                thinking: message.thinking.clone(),
            }),
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
