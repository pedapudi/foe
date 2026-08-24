//! Starting a new log from a prefix of an existing one, and closing what a
//! log left open. See docs/log-format.md "Seeding" and "Open obligations".
//! Forking, replay, and the end of every episode use this.

use crate::append::Writer;
use crate::{
    Event, EventData, ForkOrigin, LogError, Obligation, Outcome, RenderingArchive, ToolCall, ToolResult, Usage,
};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Write;
use std::path::Path;

/// Rendered text of a synthetic result for a tool call whose real result
/// never reached the log. The model sees this text.
pub const ORPHAN_RENDERED: &str =
    "The result of this tool call was not recorded; the episode was interrupted before the call finished.";

/// Recorded as the outcome of a child, and as the error of a compaction,
/// that the log opened and the episode ended without.
pub const ABANDONED: &str = "the episode ended before this was settled";

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

    // A `request/retry` the boundary separated from the attempt it
    // announces is dropped along with the outcome and the seed boundary of
    // the source: the copy holds no attempt that could close it, and no
    // event states that an attempt was abandoned.
    let cut_off = crate::fold::open_obligations(&events[..until_seq as usize]);
    let copied_span = &events[..until_seq as usize];
    let complete_archives: BTreeSet<(u32, String)> = copied_span
        .windows(2)
        .filter_map(|pair| match (&pair[0].data, &pair[1].data) {
            (EventData::ToolRenderingArchive(a), EventData::ToolResult(r))
                if a.step == r.step && a.call_id == r.call_id =>
            {
                Some((a.step, a.call_id.clone()))
            }
            _ => None,
        })
        .collect();
    let mut renumber: BTreeMap<u64, u64> = BTreeMap::new();
    for event in events.iter().take(until_seq as usize).skip(1) {
        let opens_retry = crate::obligations(&event.data)
            .into_iter()
            .any(|(kind, key, opens)| opens && kind == Obligation::Retry && cut_off.contains(&(kind, key)));
        let mut data = match &event.data {
            EventData::EpisodeEnd { .. } | EventData::SeedEnd {} => continue,
            EventData::ToolRenderingArchive(a) if !complete_archives.contains(&(a.step, a.call_id.clone())) => continue,
            _ if opens_retry => continue,
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
    for data in closing_events(&written) {
        written.push(writer.append(data)?);
    }
    copy_rendering_archives(source, dest, &written)?;
    written.push(writer.append(EventData::SeedEnd {})?);
    writer.sync()?;
    Ok(written)
}

fn copy_rendering_archives(source: &Path, dest: &Path, events: &[Event]) -> Result<(), LogError> {
    let archives = events.iter().filter_map(|event| match &event.data {
        EventData::ToolRenderingArchive(archive) => Some((event.seq, archive)),
        _ => None,
    });
    for (seq, archive) in archives {
        let bytes = verify_archive(source, seq, archive)?;
        let target = dest.join("spill").join(&archive.file);
        if !target.exists() {
            let parent = target.parent().expect("an archive file has a parent");
            std::fs::create_dir_all(parent).map_err(|error| archive_io(seq, archive, "create directory", error))?;
            let temporary = parent.join(format!(".{}.tmp", archive.digest.trim_start_matches("sha256:")));
            if temporary.exists() {
                std::fs::remove_file(&temporary)
                    .map_err(|error| archive_io(seq, archive, "remove incomplete temporary file", error))?;
            }
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temporary)
                .map_err(|error| archive_io(seq, archive, "create temporary file", error))?;
            file.write_all(&bytes).map_err(|error| archive_io(seq, archive, "write temporary file", error))?;
            file.sync_all().map_err(|error| archive_io(seq, archive, "synchronize temporary file", error))?;
            std::fs::rename(temporary, &target)
                .map_err(|error| archive_io(seq, archive, "install verified file", error))?;
        }
        verify_archive(dest, seq, archive)?;
    }
    Ok(())
}

fn verify_archive(dir: &Path, seq: u64, archive: &RenderingArchive) -> Result<Vec<u8>, LogError> {
    let expected = crate::digest::rendering_file(&archive.digest);
    if expected.as_deref() != Some(&archive.file) {
        return Err(archive_error(seq, archive, "file is not derived from digest"));
    }
    let path = dir.join("spill").join(&archive.file);
    let bytes = std::fs::read(&path).map_err(|error| LogError::Archive {
        seq,
        path: format!("spill/{}", archive.file),
        rule: format!("cannot read: {error}"),
    })?;
    if bytes.len() as u64 != archive.bytes {
        return Err(archive_error(seq, archive, &format!("has {} bytes; expected {}", bytes.len(), archive.bytes)));
    }
    let actual = format!("sha256:{}", crate::digest::sha256_hex(&bytes));
    if actual != archive.digest {
        return Err(archive_error(seq, archive, &format!("has digest {actual}; expected {}", archive.digest)));
    }
    Ok(bytes)
}

fn archive_error(seq: u64, archive: &RenderingArchive, rule: &str) -> LogError {
    LogError::Archive {
        seq,
        path: format!("spill/{}", archive.file),
        rule: format!("{rule}; expected digest {}", archive.digest),
    }
}

fn archive_io(seq: u64, archive: &RenderingArchive, operation: &str, error: std::io::Error) -> LogError {
    archive_error(seq, archive, &format!("cannot {operation}: {error}"))
}

/// The events that close every obligation `events` left open, in the order
/// to append them. Seeding writes them into a copied prefix and the runtime
/// writes them before `episode/end`, so that one rule repairs every pairing
/// the format defines rather than one rule per pairing.
///
/// A tool call receives a synthetic error result. A child that never
/// reported is recorded as failed and its whole reservation as spent, which
/// is what the parent can say about a child it stopped hearing from. A
/// compaction that never ended is recorded as failed, leaving the
/// projection as it was.
///
/// A `request/retry` has no closing event, because nothing records an
/// attempt that was never made. A writer therefore appends a retry only
/// when the attempt it announces follows it.
pub fn closing_events(events: &[Event]) -> Vec<EventData> {
    let open = crate::fold::open_obligations(events);
    let held = |kind, key: &str| open.contains(&(kind, key.to_string()));
    let mut closing = Vec::new();
    for event in events {
        match &event.data {
            EventData::AssistantMessage(m) => closing.extend(
                m.tool_calls
                    .iter()
                    .filter(|c| held(Obligation::ToolCall, &c.id))
                    .map(|c| EventData::ToolResult(orphan_result(m.step, c))),
            ),
            EventData::CompactionStart(s) if held(Obligation::Compaction, &s.step.to_string()) => {
                let (usage, error) = (Usage::default(), Some(ABANDONED.to_string()));
                closing.push(EventData::CompactionEnd { step: s.step, ok: false, usage, active_estimate: 0, error })
            }
            EventData::SpawnStart { child_id, .. } if held(Obligation::Child, child_id) => {
                let outcome = Outcome::Failed { error: ABANDONED.to_string() };
                closing.push(EventData::SpawnEnd { child_id: child_id.clone(), outcome })
            }
            EventData::BudgetReserve { child_id, reserved } if held(Obligation::Reservation, child_id) => {
                closing.push(EventData::BudgetRelease { child_id: child_id.clone(), spent: *reserved })
            }
            _ => {}
        }
    }
    closing
}

/// The synthetic result of a tool call whose real result never reached the
/// log. The model sees [`ORPHAN_RENDERED`].
fn orphan_result(step: u32, call: &ToolCall) -> ToolResult {
    ToolResult {
        step,
        call_id: call.id.clone(),
        name: call.name.clone(),
        value: serde_json::json!({ "error": ORPHAN_RENDERED }),
        rendered: ORPHAN_RENDERED.to_string(),
        is_error: true,
        spill: None,
        subject: Some(ORPHAN_RENDERED.to_string()),
        duration_ms: 0,
        synthetic: true,
    }
}

#[cfg(test)]
#[path = "seed_test.rs"]
mod tests;
