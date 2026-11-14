//! Starting a new log from a prefix of an existing one, and closing what a
//! log left open. See docs/log-format.md "Seeding" and "Open obligations".
//! Forking, replay, and the end of every episode use this.

use crate::append::Writer;
use crate::{Event, EventData, ForkOrigin, LogError, Obligation, Outcome, ToolCall, ToolResult, Usage};
use std::collections::BTreeMap;
use std::path::Path;

/// Rendered text of a synthetic result for a tool call whose real result
/// never reached the log. The model sees this text.
pub const ORPHAN_RENDERED: &str =
    "The result of this tool call was not recorded. Its external effects are unknown; repeating it may repeat those effects.";

/// Recorded as the outcome of a child, and as the error of a compaction,
/// that the log opened and the episode ended without.
pub const ABANDONED: &str = "the episode ended before this was settled";

/// What the new episode supplies for its own `episode/start`.
pub struct SeedHeader {
    pub new_id: String,
    pub parent_id: Option<String>,
    pub team_id: Option<String>,
    /// Child contract evidence that replaces the source contract evidence.
    /// Ordinary forks preserve the source by leaving this absent.
    pub contract: Option<SeedContract>,
}

/// Contract evidence supplied by a spawned child that forks source context.
pub struct SeedContract {
    pub contract: serde_json::Value,
    pub contract_fingerprint: String,
    pub effective_budget: crate::Budget,
}

/// Copies events `[1, until_seq)` of the source log into a new log under
/// `dest`, preceded by a fresh `episode/start` carrying `fork_origin`, with
/// orphaned tool calls repaired by synthetic results, followed by `seed/end`.
/// Returns the events written, renumbered.
///
/// A copied `episode/end` or `seed/end` is dropped: the new log has its own
/// outcome and its own single seed boundary. Every field of a copied event
/// that holds a `seq` is renumbered with the event it names, and seeding
/// fails when that event is not copied.
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
    if let Some(contract) = header.contract {
        fresh.contract = contract.contract;
        fresh.contract_fingerprint = contract.contract_fingerprint;
        fresh.effective_budget = Some(contract.effective_budget);
    }
    written.push(writer.append(EventData::EpisodeStart(fresh))?);

    // A `request/retry` the boundary separated from the attempt it
    // announces is dropped along with the outcome and the seed boundary of
    // the source: the copy holds no attempt that could close it, and no
    // event states that an attempt was abandoned.
    let copied_span = &events[..until_seq as usize];
    let cut_off = crate::fold::open_obligations(copied_span);
    let mut renumber: BTreeMap<u64, u64> = BTreeMap::new();
    // The continuation message of the latest copied summary: as the source
    // rendered it, then as the renumbered state renders it.
    let mut continuation: Option<[crate::Message; 2]> = None;
    for (index, event) in copied_span.iter().enumerate().skip(1) {
        let opens_retry = crate::obligations(&event.data)
            .into_iter()
            .any(|(kind, key, opens)| opens && kind == Obligation::Retry && cut_off.contains(&(kind, key)));
        // An archive is copied when the copied event after it is its result.
        let result = match copied_span.get(index + 1).map(|next| &next.data) {
            Some(EventData::ToolResult(r)) => Some((r.step, r.call_id.as_str())),
            _ => None,
        };
        let mut data = match &event.data {
            EventData::EpisodeEnd { .. } | EventData::SeedEnd {} => continue,
            EventData::ToolRenderingArchive(a) if result != Some((a.step, a.call_id.as_str())) => continue,
            _ if opens_retry => continue,
            data => data.clone(),
        };
        let uncopied = |reference: u64| LogError::Uncopied { seq: event.seq, reference };
        // A reference names one event, which the copy must hold. An end of
        // a `covered` range is a position, which moves inward to the
        // nearest copied event.
        let (references, covered): (Vec<&mut u64>, _) = match &mut data {
            EventData::ModelRequest(r) => {
                // The continuation message states `covered`. A request that
                // recorded the source rendering records the renumbered one,
                // so that it equals the list the copied events derive.
                if let Some([_, copy]) = continuation.as_ref().filter(|c| r.messages.get(1) == Some(&c[0])) {
                    r.messages[1] = copy.clone();
                }
                (std::iter::once(&mut r.header_seq).chain(&mut r.consumed).collect(), None)
            }
            EventData::CompactionStart(s) => (vec![], Some(&mut s.covered)),
            EventData::CompactionSummary(s) => {
                (vec![&mut s.first_kept_seq, &mut s.summary_request_seq], Some(&mut s.state.covered))
            }
            EventData::WorkflowNodeStart(n) => (n.inputs.iter_mut().collect(), None),
            _ => (vec![], None),
        };
        for reference in references {
            *reference = *renumber.get(reference).ok_or_else(|| uncopied(*reference))?;
        }
        if let Some(c) = covered {
            c.first_seq = *renumber.range(c.first_seq..).next().ok_or_else(|| uncopied(c.first_seq))?.1;
            c.last_seq = *renumber.range(..=c.last_seq).next_back().ok_or_else(|| uncopied(c.last_seq))?.1;
        }
        if let (EventData::CompactionSummary(source), EventData::CompactionSummary(copy)) = (&event.data, &data) {
            continuation = Some([source, copy].map(|s| crate::fold::user_text(&crate::fold::render_continuation(s))));
        }
        let copied = writer.append_at(data, event.time)?;
        renumber.insert(event.seq, copied.seq);
        written.push(copied);
    }
    for data in closing_events(&written) {
        written.push(writer.append(data)?);
    }
    copy_artifacts(source, dest, &written)?;
    written.push(writer.append(EventData::SeedEnd {})?);
    writer.sync()?;
    Ok(written)
}

fn copy_artifacts(source: &Path, dest: &Path, events: &[Event]) -> Result<(), LogError> {
    for event in events {
        let spill = source.join("spill");
        let (file, bytes) = match &event.data {
            EventData::ToolRenderingArchive(a) => (&a.file, crate::artifact::read_rendering(&spill, event.seq, a)?),
            EventData::ToolResult(r) if r.spill.is_some() => {
                (r.spill.as_ref().unwrap(), crate::artifact::read_canonical(&spill, event.seq, r)?.unwrap())
            }
            _ => continue,
        };
        crate::artifact::retain(&dest.join("spill").join(file), &bytes).map_err(|e| LogError::Archive {
            seq: event.seq,
            path: file.clone(),
            rule: e.to_string(),
        })?;
    }
    Ok(())
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
    // Open inner calls of a composing tool, by the outer call they nest
    // under. Each closes before its outer call, so the nested account
    // balances before the outer synthetic result is read.
    let mut inner: BTreeMap<&str, Vec<ToolCall>> = BTreeMap::new();
    for event in events {
        if let EventData::ToolInnerCall(c) = &event.data {
            if held(Obligation::ToolCall, &c.call_id) {
                let call = ToolCall { id: c.call_id.clone(), name: c.name.clone(), args: c.args.clone() };
                inner.entry(&c.outer_call_id).or_default().push(call);
            }
        }
    }
    let mut closing = Vec::new();
    for event in events {
        match &event.data {
            EventData::AssistantMessage(m) => {
                for call in m.tool_calls.iter().filter(|c| held(Obligation::ToolCall, &c.id)) {
                    let nested = inner.remove(call.id.as_str()).unwrap_or_default();
                    closing.extend(nested.iter().map(|c| EventData::ToolResult(orphan_result(m.step, c))));
                    closing.push(EventData::ToolResult(orphan_result(m.step, call)));
                }
            }
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
    // An open inner call whose outer call is not open can arise only when
    // appending the inner result itself failed; a closing result is still
    // owed. No `assistant/message` carries such a call, so no step is
    // known for it.
    for calls in inner.into_values() {
        closing.extend(calls.iter().map(|c| EventData::ToolResult(orphan_result(0, c))));
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
        failure: Some(crate::ToolFailure {
            code: crate::ToolFailureCode::Interrupted,
            message: ORPHAN_RENDERED.to_string(),
            retryable: false,
            details: serde_json::json!({}),
        }),
        spill: None,
        subject: Some(ORPHAN_RENDERED.to_string()),
        duration_ms: 0,
        synthetic: true,
    }
}

#[cfg(test)]
#[path = "seed_test.rs"]
mod tests;
