//! Starting a new log from a prefix of an existing one. See
//! docs/log-format.md "Seeding". Forking and replay both use this.

use crate::append::Writer;
use crate::{Event, EventData, ForkOrigin, LogError, ToolResult};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

/// Rendered text of a synthetic result for a tool call whose real result
/// never reached the log. The model sees this text.
pub const ORPHAN_RENDERED: &str =
    "The result of this tool call was not recorded; the episode was interrupted before the call finished.";

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
///
/// A copied `episode/end` or `seed/end` is dropped: the new log has its own
/// outcome and its own single seed boundary. `seq` references inside copied
/// `model/request` events are renumbered with the events they name.
pub fn seed(source: &Path, until_seq: u64, dest: &Path, header: SeedHeader) -> Result<Vec<Event>, LogError> {
    let events = crate::fold::read_all(source)?;
    let Some(EventData::EpisodeStart(start)) = events.first().map(|e| &e.data) else {
        return Err(events
            .first()
            .map_or(LogError::Empty, |e| LogError::Invalid { seq: e.seq, rule: "episode/start is the first event" }));
    };
    if until_seq > events.len() as u64 {
        return Err(LogError::Invalid { seq: until_seq, rule: "seed boundary lies within the source log" });
    }
    let mut writer = Writer::create(dest, None)?;
    let mut written = Vec::new();
    let mut fresh = start.clone();
    fresh.id = header.new_id;
    fresh.parent_id = header.parent_id;
    fresh.team_id = header.team_id;
    fresh.fork_origin = Some(ForkOrigin { episode_id: start.id.clone(), seq: until_seq });
    written.push(writer.append(EventData::EpisodeStart(fresh))?);

    let mut renumber: BTreeMap<u64, u64> = BTreeMap::new();
    for event in events.iter().take(until_seq as usize).skip(1) {
        let mut data = match &event.data {
            EventData::EpisodeEnd { .. } | EventData::SeedEnd {} => continue,
            data => data.clone(),
        };
        let map = |s: &u64| renumber.get(s).copied().unwrap_or(*s);
        match &mut data {
            EventData::ModelRequest(request) => {
                request.header_seq = map(&request.header_seq);
                request.consumed = request.consumed.iter().map(map).collect();
            }
            EventData::CompactionStart(start) => {
                start.covered =
                    crate::Covered { first_seq: map(&start.covered.first_seq), last_seq: map(&start.covered.last_seq) };
            }
            EventData::CompactionSummary(summary) => {
                summary.first_kept_seq = map(&summary.first_kept_seq);
                summary.summary_request_seq = map(&summary.summary_request_seq);
                let c = summary.state.covered;
                summary.state.covered = crate::Covered { first_seq: map(&c.first_seq), last_seq: map(&c.last_seq) };
            }
            _ => {}
        }
        let copied = writer.append_at(data, event.time)?;
        renumber.insert(event.seq, copied.seq);
        written.push(copied);
    }
    for result in orphan_results(&written) {
        written.push(writer.append(EventData::ToolResult(result))?);
    }
    written.push(writer.append(EventData::SeedEnd {})?);
    writer.sync()?;
    Ok(written)
}

/// For every tool call among `events` that has no result, produces the
/// synthetic `tool/result` that seeding appends. Exposed so that the loop
/// can apply the same repair after an interrupted request.
pub fn orphan_results(events: &[Event]) -> Vec<ToolResult> {
    let settled: BTreeSet<&str> = events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::ToolResult(r) => Some(r.call_id.as_str()),
            _ => None,
        })
        .collect();
    events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::AssistantMessage(m) => Some(m),
            _ => None,
        })
        .flat_map(|m| m.tool_calls.iter().map(move |c| (m.step, c)))
        .filter(|(_, c)| !settled.contains(c.id.as_str()))
        .map(|(step, c)| ToolResult {
            step,
            call_id: c.id.clone(),
            name: c.name.clone(),
            value: serde_json::json!({ "error": ORPHAN_RENDERED }),
            rendered: ORPHAN_RENDERED.to_string(),
            is_error: true,
            spill: None,
            duration_ms: 0,
            synthetic: true,
        })
        .collect()
}

#[cfg(test)]
#[path = "seed_test.rs"]
mod tests;
