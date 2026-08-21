//! Starting a new log from a prefix of an existing one. See
//! docs/log-format.md "Seeding". Forking and replay both use this.

use crate::{Event, LogError};
use std::path::Path;

/// What the new episode supplies for its own `episode/start`.
pub struct SeedHeader {
    pub new_id: String,
    pub parent_id: Option<String>,
    pub team_id: Option<String>,
}

/// Copies events `[1, until_seq)` of the source log into a new log under
/// `dest`, preceded by a fresh `episode/start` carrying `fork_origin`, with
/// orphaned tool calls repaired by synthetic results, followed by `seed/end`.
/// Returns the events written, renumbered.
pub fn seed(source: &Path, until_seq: u64, dest: &Path, header: SeedHeader) -> Result<Vec<Event>, LogError> {
    let _ = (source, until_seq, dest, header);
    todo!("owner: runtime agent")
}

/// For every tool call among `events` that has no result, produces the
/// synthetic `tool/result` that seeding appends. Exposed so that the loop
/// can apply the same repair after an interrupted request.
pub fn orphan_results(events: &[Event]) -> Vec<crate::ToolResult> {
    let _ = events;
    todo!("owner: runtime agent")
}
