//! Reading a log back into state. See docs/log-format.md "Derived messages".
//!
//! `derive_messages` is the one rule that the runtime, the viewer, and the
//! Python package all apply identically. A reader that recomputes the
//! message list for a request and finds it differs from the recorded
//! `model/request.messages` has found a runtime defect.

use crate::{Event, LogError, Message, State};
use std::path::Path;

/// Parses every line of `episode.jsonl` under `dir`.
pub fn read_all(dir: &Path) -> Result<Vec<Event>, LogError> {
    let _ = dir;
    todo!("owner: runtime agent")
}

/// Parses events appended after byte offset `from`, returning them and the
/// new offset. Used by tailing readers.
pub fn read_from(dir: &Path, from: u64) -> Result<(Vec<Event>, u64), LogError> {
    let _ = (dir, from);
    todo!("owner: runtime agent")
}

/// Folds events into [`State`]. Validates the structural rules: `seq`
/// contiguous from 0, `episode/start` first, at most one `episode/end` and
/// it is last, every tool call has exactly one result.
pub fn fold(events: &[Event]) -> Result<State, LogError> {
    let _ = events;
    todo!("owner: runtime agent")
}

/// Derives the message list a request at `upto_seq` would carry, by the
/// rule in the specification. Inbox items are included when their `seq` is
/// in `consumed_inbox` or was consumed by an earlier request.
pub fn derive_messages(events: &[Event], upto_seq: u64, consumed_inbox: &[u64]) -> Vec<Message> {
    let _ = (events, upto_seq, consumed_inbox);
    todo!("owner: runtime agent")
}

/// Validates one event against the preceding ones. Returns the rule
/// violated, if any. Used by the writer before appending.
pub fn validate_next(prior: &State, event: &Event) -> Result<(), LogError> {
    let _ = (prior, event);
    todo!("owner: runtime agent")
}
