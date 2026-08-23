//! Context compaction: the policy docs/compaction.md specifies.
//!
//! The log retains complete evidence; the active model context is a
//! bounded projection of it. This crate decides when the projection of the
//! next request outgrows the model's window, where the kept suffix begins,
//! what the summarization request says, and what runtime facts travel
//! across the cut as data. The loop in `foe-core` lends it one recorded,
//! budgeted model call and writes the compaction events.

#![forbid(unsafe_code)]

use foe_config::harness_text as text;
use foe_config::{ContextConfig, DoneWhen};
use foe_core::context::{Answer, ContextPolicy, ContextState, Cut, Summarized, SummaryCall};
use foe_core::RuntimeError;
use foe_log::fold::{derive_span, render_continuation};
use foe_log::{
    ChildSummary, CompactedFiles, CompactionSummary, ContentBlock, ContinuationState, Covered, Event, EventData,
    InboxSource, Message, Usage, SUMMARY_REQUEST_PREFIX,
};
use std::collections::{BTreeMap, BTreeSet};

/// One `context` block resolved against the model: the window in tokens
/// and the per-request output limit, 0 when the configuration sets none.
pub struct Policy {
    cfg: ContextConfig,
    window: u64,
    max_output: u64,
    done_when: String,
}

impl Policy {
    pub fn new(cfg: ContextConfig, window: u64, max_output: u64, done_when: Option<&DoneWhen>) -> Self {
        Policy { cfg, window, max_output, done_when: done_when_line(done_when) }
    }

    /// One line for `foe plan`.
    pub fn describe(&self) -> String {
        format!(
            "compact when the projected request exceeds {} tokens (window {} less reserve {}); keep about {} recent \
             tokens verbatim; margin {}",
            self.window.saturating_sub(self.cfg.reserve_tokens),
            self.window,
            self.cfg.reserve_tokens,
            self.cfg.keep_recent_tokens,
            self.cfg.margin_tokens
        )
    }

    fn continuation(&self, state: &ContextState, cut: &Cut, prior: Option<&CompactionSummary>) -> ContinuationState {
        let task = state.events.iter().find_map(|e| match &e.data {
            EventData::InboxItem(item) if item.source == InboxSource::Task => Some(blocks_text(&item.content)),
            _ => None,
        });
        let carried = prior.map(|p| p.state.clone()).unwrap_or_default();
        ContinuationState {
            task: task.unwrap_or_default(),
            done_when: self.done_when.clone(),
            outstanding_findings: findings(state.events),
            files: files(state.events, cut.covered, &carried.files),
            children: children(state.events, cut.covered, &carried.children),
            covered: cut.covered,
            budget_remaining: state.remaining,
        }
    }
}

#[async_trait::async_trait]
impl ContextPolicy for Policy {
    fn plan(&self, state: &ContextState) -> Option<Cut> {
        let max_output = match (self.max_output, state.remaining.output_tokens) {
            (0, Some(left)) => left.min(u64::from(u32::MAX)),
            (configured, Some(left)) => configured.min(left),
            (configured, None) => configured,
        };
        let projected = projected(state.events, max_output, self.cfg.margin_tokens)?;
        if projected <= self.window.saturating_sub(self.cfg.reserve_tokens) {
            return None;
        }
        let floor = latest_summary(state.events).map_or(1, |s| s.first_kept_seq);
        let steps = steps(state.events, floor);
        let first_kept_seq = steps[first_kept(&steps, self.cfg.keep_recent_tokens)?].0;
        Some(Cut {
            first_kept_seq,
            covered: Covered { first_seq: floor, last_seq: first_kept_seq - 1 },
            projected_tokens: projected,
            exceeds_window: projected > self.window,
        })
    }

    async fn summarize(
        &self,
        state: &ContextState<'_>,
        cut: &Cut,
        call: &mut dyn SummaryCall,
    ) -> Result<Summarized, RuntimeError> {
        let prior = latest_summary(state.events);
        let span = derive_span(state.events, cut.covered.first_seq, cut.first_kept_seq, &[]);
        let user = prompt(prior.map(|p| p.summary.as_str()), &span);
        let continuation = self.continuation(state, cut, prior);
        let failed = |error: &str, usage| Summarized::Failed { error: error.to_string(), usage };
        Ok(match call.call(text::COMPACTION_INSTRUCTION, user).await? {
            Answer::Ended(outcome) => Summarized::Ended(outcome),
            Answer::Failed(error) => failed(&error, Usage::default()),
            Answer::Interrupted => failed("the summarization request was interrupted", Usage::default()),
            Answer::Message { message, .. } if message.text.trim().is_empty() => {
                failed("the summary was empty", message.usage)
            }
            Answer::Message { message, request_seq } => {
                let summary = CompactionSummary {
                    step: message.step,
                    summary: message.text,
                    state: continuation,
                    first_kept_seq: cut.first_kept_seq,
                    summary_request_seq: request_seq,
                };
                let kept: u64 = steps(state.events, cut.first_kept_seq).iter().map(|(_, t)| t).sum();
                let opening = summary.state.task.len() + render_continuation(&summary).len();
                let active_estimate = kept + tokens(opening);
                Summarized::Summary { summary: Box::new(summary), usage: message.usage, active_estimate }
            }
        })
    }
}

/// The token estimate for `bytes` of text: one token per four bytes.
pub fn tokens(bytes: usize) -> u64 {
    bytes.div_ceil(4) as u64
}

fn ordinary(request_id: &str) -> bool {
    !request_id.starts_with(SUMMARY_REQUEST_PREFIX)
}

fn latest_summary(events: &[Event]) -> Option<&CompactionSummary> {
    events.iter().rev().find_map(|e| match &e.data {
        EventData::CompactionSummary(summary) => Some(summary),
        _ => None,
    })
}

fn block_text(block: &ContentBlock) -> String {
    match block {
        ContentBlock::Text { text } => text.clone(),
        ContentBlock::Image { media_type, .. } => format!("(image {media_type})"),
    }
}

fn blocks_text(blocks: &[ContentBlock]) -> String {
    blocks.iter().map(block_text).collect::<Vec<_>>().join("\n")
}

/// The projected size of the next request: what the last ordinary response
/// reported as input and output, the rendered results and inbox items that
/// arrived after it, the output limit, and the margin. `None` when no
/// response reporting usage has arrived since the latest compaction.
pub fn projected(events: &[Event], max_output: u64, margin: u64) -> Option<u64> {
    let mut after = 0usize;
    for event in events.iter().rev() {
        match &event.data {
            EventData::CompactionEnd { ok: true, .. } => return None,
            EventData::AssistantMessage(m) if ordinary(&m.request_id) && m.usage.input > 0 => {
                return Some(m.usage.input + m.usage.output + tokens(after) + max_output + margin);
            }
            EventData::ToolResult(r) => after += r.rendered.len(),
            EventData::InboxItem(item) => after += blocks_text(&item.content).len(),
            _ => {}
        }
    }
    None
}

/// The ordinary steps whose first request lies at or after `floor`: the
/// `seq` of that request and the estimated tokens of the step's assistant
/// text and rendered results. A retried step keeps its first request, so a
/// cut there keeps the items that request consumed.
pub fn steps(events: &[Event], floor: u64) -> Vec<(u64, u64)> {
    let mut by_step: BTreeMap<u32, (u64, usize)> = BTreeMap::new();
    for event in events.iter().filter(|e| e.seq >= floor) {
        match &event.data {
            EventData::ModelRequest(r) if ordinary(&r.request_id) => {
                by_step.entry(r.step).or_insert((event.seq, 0));
            }
            EventData::AssistantMessage(m) if ordinary(&m.request_id) => {
                by_step.entry(m.step).and_modify(|s| s.1 += m.text.len());
            }
            EventData::ToolResult(r) => {
                by_step.entry(r.step).and_modify(|s| s.1 += r.rendered.len());
            }
            _ => {}
        }
    }
    by_step.into_values().map(|(seq, bytes)| (seq, tokens(bytes))).collect()
}

/// The index in `steps` of the first kept step: the longest suffix whose
/// estimate fits `keep_recent`, keeping at least the newest step and
/// summarizing at least the oldest. `None` with fewer than two steps.
pub fn first_kept(steps: &[(u64, u64)], keep_recent: u64) -> Option<usize> {
    if steps.len() < 2 {
        return None;
    }
    let (mut acc, mut first) = (0, steps.len());
    for (i, (_, estimate)) in steps.iter().enumerate().rev() {
        acc += estimate;
        if acc > keep_recent {
            break;
        }
        first = i;
    }
    Some(first.clamp(1, steps.len() - 1))
}

fn covered_by(event: &Event, covered: Covered) -> bool {
    event.seq >= covered.first_seq && event.seq <= covered.last_seq
}

/// Paths named by `read`, `write`, and `edit` calls issued within `covered`
/// whose results were not errors, joined with `carried`, each list sorted
/// and without duplicates.
pub fn files(events: &[Event], covered: Covered, carried: &CompactedFiles) -> CompactedFiles {
    let failed: BTreeSet<&str> = events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::ToolResult(r) if r.is_error => Some(r.call_id.as_str()),
            _ => None,
        })
        .collect();
    let mut lists = [carried.read.clone(), carried.written.clone(), carried.edited.clone()];
    for event in events.iter().filter(|e| covered_by(e, covered)) {
        let EventData::AssistantMessage(m) = &event.data else { continue };
        for call in m.tool_calls.iter().filter(|c| !failed.contains(c.id.as_str())) {
            let list = match call.name.as_str() {
                "read" => 0,
                "write" => 1,
                "edit" => 2,
                _ => continue,
            };
            lists[list].extend(call.args["path"].as_str().map(str::to_string));
        }
    }
    let [read, written, edited] = lists.map(|l| l.into_iter().collect::<BTreeSet<_>>().into_iter().collect());
    CompactedFiles { read, written, edited }
}

/// Children that ended within `covered`, after those `carried` from
/// earlier compactions, each with the program its `spawn/start` named.
pub fn children(events: &[Event], covered: Covered, carried: &[ChildSummary]) -> Vec<ChildSummary> {
    let programs: BTreeMap<&str, &str> = events
        .iter()
        .filter_map(|e| match &e.data {
            EventData::SpawnStart { child_id, program, .. } => Some((child_id.as_str(), program.as_str())),
            _ => None,
        })
        .collect();
    let mut out = carried.to_vec();
    for event in events.iter().filter(|e| covered_by(e, covered)) {
        if let EventData::SpawnEnd { child_id, outcome } = &event.data {
            let program = programs.get(child_id.as_str()).copied().unwrap_or_default().to_string();
            out.push(ChildSummary { id: child_id.clone(), program, outcome: outcome.clone() });
        }
    }
    out
}

/// The text of the latest verifier report. The episode completes only
/// after a verification with no findings, so the latest report is
/// outstanding while the episode runs.
pub fn findings(events: &[Event]) -> Vec<String> {
    events
        .iter()
        .rev()
        .find_map(|e| match &e.data {
            EventData::InboxItem(item) if item.source == InboxSource::Verify => {
                Some(item.content.iter().map(block_text).collect())
            }
            _ => None,
        })
        .unwrap_or_default()
}

/// The completion condition in one line, for the continuation state.
pub fn done_when_line(done_when: Option<&DoneWhen>) -> String {
    let (verify, returns) = done_when.map_or((None, false), |d| (d.verify.as_deref(), d.returns.is_some()));
    let finish = match returns {
        true => "a call to `return` with a value conforming to its schema",
        false => "a turn with no tool calls",
    };
    match verify {
        Some(tool) if !returns => {
            format!("a turn with no tool calls or a non-error `{tool}` call, then `{tool}` reports no findings")
        }
        Some(tool) => format!("{finish}, then `{tool}` reports no findings"),
        None => finish.to_string(),
    }
}

/// The message of a summarization request: the earlier summary under its
/// heading when one exists, then `span` as labeled plain text. Each entry
/// is labeled `user`, `assistant`, or `result <tool>`; a tool call appears
/// within its assistant entry as its name and arguments. Nothing in the
/// text is shaped as a conversation the model could continue.
pub fn prompt(prior: Option<&str>, span: &[Message]) -> String {
    let entries: Vec<String> = span
        .iter()
        .map(|message| {
            let (label, body) = match message {
                Message::User { content } => ("user".to_string(), blocks_text(content)),
                Message::Assistant { text: body, tool_calls, .. } => {
                    let calls = tool_calls.iter().map(|c| {
                        text::fill(text::COMPACTION_CALL, &[("name", &c.name), ("args", &c.args.to_string())])
                    });
                    let lines: Vec<String> =
                        std::iter::once(body.clone()).chain(calls).filter(|l| !l.is_empty()).collect();
                    ("assistant".to_string(), lines.join("\n"))
                }
                Message::Tool { name, rendered, is_error, .. } => {
                    (format!("result {name}{}", if *is_error { " error" } else { "" }), rendered.clone())
                }
            };
            text::fill(text::COMPACTION_TURN, &[("label", &label), ("body", &body)])
        })
        .collect();
    let transcript = text::fill(text::COMPACTION_TRANSCRIPT, &[("transcript", &entries.join("\n\n"))]);
    match prior {
        Some(summary) => format!("{}\n\n{transcript}", text::fill(text::COMPACTION_PRIOR, &[("summary", summary)])),
        None => transcript,
    }
}

#[cfg(test)]
#[path = "lib_test.rs"]
mod tests;
