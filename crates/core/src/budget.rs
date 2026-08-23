//! Budget pool: debits, reservation on spawn, release, folded from the log.
//!
//! Implements docs/design.md (Subagents and teams). The pool is held by the
//! episode and shared with its spawner. Every limit in `Budget` applies to
//! the whole tree below the episode: a child's reservation is debited from
//! the parent's remainder until the child settles and returns what it did
//! not spend.

use foe_config::Budget;
use foe_log::{BudgetAmount, EventData, ExhaustedLimit, Usage};
use std::collections::BTreeMap;
use std::time::{Duration, Instant};

pub struct Pool {
    limits: Budget,
    started: Instant,
    /// Model requests this episode made, retries included.
    requests: u64,
    /// Provider-reported input tokens this episode consumed.
    input_tokens: u64,
    /// Provider-reported output tokens this episode consumed.
    output_tokens: u64,
    /// Reservations of children that have not settled, by child id.
    active: BTreeMap<String, BudgetAmount>,
    /// Totals reported by children that settled, subtrees included.
    children_spent: BudgetAmount,
}

impl Pool {
    /// A pool for an episode starting now with nothing spent.
    pub fn new(limits: Budget) -> Self {
        Self {
            limits,
            started: Instant::now(),
            requests: 0,
            input_tokens: 0,
            output_tokens: 0,
            active: BTreeMap::new(),
            children_spent: BudgetAmount::default(),
        }
    }

    /// Folds the events that affect the pool: `model/request` counts a
    /// call, `assistant/message` adds usage, `budget/reserve` and
    /// `budget/release` move reservations. Other events change nothing.
    pub fn apply(&mut self, data: &EventData) {
        match data {
            EventData::ModelRequest(_) => self.requests = self.requests.saturating_add(1),
            EventData::AssistantMessage(message) => self.note_usage(message.usage),
            EventData::BudgetReserve { child_id, reserved } => {
                self.active.insert(child_id.clone(), *reserved);
            }
            EventData::BudgetRelease { child_id, spent } => self.release(child_id, *spent),
            _ => {}
        }
    }

    pub fn note_request(&mut self) {
        self.requests = self.requests.saturating_add(1);
    }

    pub fn note_usage(&mut self, usage: Usage) {
        self.input_tokens = self.input_tokens.saturating_add(usage.input);
        self.output_tokens = self.output_tokens.saturating_add(usage.output);
    }

    /// The instant the `seconds` limit elapses, when there is one.
    pub fn deadline(&self) -> Option<Instant> {
        self.limits.seconds.map(|s| self.started + Duration::from_secs(s))
    }

    /// What remains for this episode after its own spend, its settled
    /// children, and its active reservations. `None` means unlimited.
    /// `episodes` counts this episode against the limit, so it is the
    /// number of further episodes the tree below here may still hold.
    pub fn remaining(&self) -> BudgetAmount {
        let reserved =
            |f: fn(&BudgetAmount) -> Option<u64>| self.active.values().filter_map(f).fold(0, u64::saturating_add);
        let used =
            |own: u64, children: Option<u64>, held: u64| own.saturating_add(children.unwrap_or(0)).saturating_add(held);
        let calls_used = used(self.requests, self.children_spent.model_calls, reserved(|a| a.model_calls));
        let input_used = used(self.input_tokens, self.children_spent.input_tokens, reserved(|a| a.input_tokens));
        let output_used = used(self.output_tokens, self.children_spent.output_tokens, reserved(|a| a.output_tokens));
        let episodes_used = used(1, self.children_spent.episodes, reserved(|a| a.episodes));
        BudgetAmount {
            model_calls: Some(self.limits.model_calls.saturating_sub(calls_used)),
            input_tokens: self.limits.input_tokens.map(|t| t.saturating_sub(input_used)),
            output_tokens: self.limits.output_tokens.map(|t| t.saturating_sub(output_used)),
            seconds: self.limits.seconds.map(|s| s.saturating_sub(self.started.elapsed().as_secs())),
            episodes: Some(u64::from(self.limits.max_episodes).saturating_sub(episodes_used)),
        }
    }

    /// The first spend limit that is used up, if any. Checked before each
    /// request and after each step.
    pub fn exhausted(&self) -> Option<ExhaustedLimit> {
        let remaining = self.remaining();
        if remaining.model_calls == Some(0) {
            Some(ExhaustedLimit::ModelCalls)
        } else if remaining.input_tokens == Some(0) {
            Some(ExhaustedLimit::InputTokens)
        } else if remaining.output_tokens == Some(0) {
            Some(ExhaustedLimit::OutputTokens)
        } else if self.deadline().is_some_and(|d| Instant::now() >= d) {
            Some(ExhaustedLimit::Seconds)
        } else {
            None
        }
    }

    /// Clamps the provider's output cap to the output allowance that
    /// remains across this episode tree.
    pub fn request_max_output(&self, configured: Option<u32>) -> Result<Option<u32>, ExhaustedLimit> {
        let remaining = self.remaining();
        match (configured, remaining.output_tokens) {
            (_, Some(0)) => Err(ExhaustedLimit::OutputTokens),
            (Some(cap), Some(left)) => Ok(Some(cap.min(u32::try_from(left).unwrap_or(u32::MAX)))),
            (None, Some(left)) => Ok(Some(u32::try_from(left).unwrap_or(u32::MAX))),
            (cap, None) => Ok(cap),
        }
    }

    /// Reserves budget for a child. A dimension the request leaves unset
    /// receives the whole remainder. Returns the amount granted, which the
    /// caller records as `budget/reserve`, or the limit that refused it.
    /// Seconds are capped by the remainder and never debited, because
    /// children run on the same clock as the parent.
    pub fn reserve(&mut self, child_id: &str, request: BudgetAmount) -> Result<BudgetAmount, ExhaustedLimit> {
        if self.limits.max_depth == 0 {
            return Err(ExhaustedLimit::Depth);
        }
        if self.active.len() as u32 >= self.limits.max_concurrent {
            return Err(ExhaustedLimit::Concurrency);
        }
        let remaining = self.remaining();
        let within = |asked: Option<u64>, left: Option<u64>, limit| match (asked, left) {
            (Some(a), Some(l)) if a > l => Err(limit),
            (Some(a), _) => Ok(Some(a)),
            (None, l) => Ok(l),
        };
        // The episode allowance is clamped rather than refused, because a
        // child program declares its own `max_episodes` without knowing
        // what its parent has left; the child receives what remains.
        let episodes_left = remaining.episodes.unwrap_or(0);
        if episodes_left == 0 {
            return Err(ExhaustedLimit::Episodes);
        }
        let granted = BudgetAmount {
            model_calls: within(request.model_calls, remaining.model_calls, ExhaustedLimit::ModelCalls)?,
            input_tokens: within(request.input_tokens, remaining.input_tokens, ExhaustedLimit::InputTokens)?,
            output_tokens: within(request.output_tokens, remaining.output_tokens, ExhaustedLimit::OutputTokens)?,
            seconds: within(request.seconds, remaining.seconds, ExhaustedLimit::Seconds)?,
            episodes: Some(request.episodes.map_or(episodes_left, |a| a.min(episodes_left))),
        };
        // A grant of zero on any dimension is refused rather than passed
        // down. A configuration whose budget holds a zero is invalid, so a
        // child launched with one refuses it at startup and writes no log,
        // which reaches the parent as a failure rather than as the limit
        // that was actually reached.
        let zero = [
            (granted.model_calls, ExhaustedLimit::ModelCalls),
            (granted.input_tokens, ExhaustedLimit::InputTokens),
            (granted.output_tokens, ExhaustedLimit::OutputTokens),
            (granted.seconds, ExhaustedLimit::Seconds),
        ];
        if let Some((_, limit)) = zero.into_iter().find(|(amount, _)| *amount == Some(0)) {
            return Err(limit);
        }
        self.active.insert(child_id.to_string(), granted);
        Ok(granted)
    }

    /// Settles a child: its reservation ends and what it reports spent is
    /// debited permanently. The caller records `budget/release`. Episodes
    /// are a lifetime count, so a subtree that has ended keeps its share.
    pub fn release(&mut self, child_id: &str, spent: BudgetAmount) {
        self.active.remove(child_id);
        let add = |total: &mut Option<u64>, amount: Option<u64>| {
            if let Some(a) = amount {
                *total = Some(total.unwrap_or(0).saturating_add(a));
            }
        };
        add(&mut self.children_spent.model_calls, spent.model_calls);
        add(&mut self.children_spent.input_tokens, spent.input_tokens);
        add(&mut self.children_spent.output_tokens, spent.output_tokens);
        add(&mut self.children_spent.episodes, spent.episodes);
    }

    /// Children running now.
    pub fn active_children(&self) -> usize {
        self.active.len()
    }
}

#[cfg(test)]
#[path = "budget_test.rs"]
pub(crate) mod tests;
