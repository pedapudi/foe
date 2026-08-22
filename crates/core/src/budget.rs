//! Budget pool: debits, reservation on spawn, release, folded from the log.
//!
//! Implements docs/design.md (Subagents and teams). The pool is held by the
//! episode and shared with its spawner. Every limit in `Budget` applies to
//! the whole tree below the episode: a child's reservation is debited from
//! the parent's remainder until the child settles and returns what it did
//! not spend.

use crate::Budget;
use foe_log::{BudgetAmount, EventData, ExhaustedLimit, Usage};
use std::collections::BTreeMap;
use std::time::{Duration, Instant};

pub struct Pool {
    limits: Budget,
    started: Instant,
    /// Model requests this episode made, retries included.
    requests: u64,
    /// Input plus output tokens this episode consumed.
    tokens: u64,
    /// Reservations of children that have not settled, by child id.
    active: BTreeMap<String, BudgetAmount>,
    /// Totals reported by children that settled.
    children_spent: BudgetAmount,
    /// Episodes in the tree so far, this one included.
    episodes: u32,
}

impl Pool {
    /// A pool for an episode starting now with nothing spent.
    pub fn new(limits: Budget) -> Self {
        Self {
            limits,
            started: Instant::now(),
            requests: 0,
            tokens: 0,
            active: BTreeMap::new(),
            children_spent: BudgetAmount::default(),
            episodes: 1,
        }
    }

    /// Folds the events that affect the pool: `model/request` counts a
    /// call, `assistant/message` adds usage, `budget/reserve` and
    /// `budget/release` move reservations. Other events change nothing.
    pub fn apply(&mut self, data: &EventData) {
        match data {
            EventData::ModelRequest(_) => self.requests += 1,
            EventData::AssistantMessage(message) => self.note_usage(message.usage),
            EventData::BudgetReserve { child_id, reserved } => {
                self.episodes += 1;
                self.active.insert(child_id.clone(), *reserved);
            }
            EventData::BudgetRelease { child_id, spent } => self.release(child_id, *spent),
            _ => {}
        }
    }

    pub fn note_request(&mut self) {
        self.requests += 1;
    }

    pub fn note_usage(&mut self, usage: Usage) {
        self.tokens += usage.input + usage.output;
    }

    /// The instant the `seconds` limit elapses, when there is one.
    pub fn deadline(&self) -> Option<Instant> {
        self.limits.seconds.map(|s| self.started + Duration::from_secs(s))
    }

    /// What remains for this episode after its own spend, its settled
    /// children, and its active reservations. `None` means unlimited.
    pub fn remaining(&self) -> BudgetAmount {
        let reserved = |f: fn(&BudgetAmount) -> Option<u64>| self.active.values().filter_map(f).sum::<u64>();
        let calls_used = self.requests + self.children_spent.model_calls.unwrap_or(0) + reserved(|a| a.model_calls);
        let tokens_used = self.tokens + self.children_spent.tokens.unwrap_or(0) + reserved(|a| a.tokens);
        BudgetAmount {
            model_calls: Some(self.limits.model_calls.saturating_sub(calls_used)),
            tokens: self.limits.tokens.map(|t| t.saturating_sub(tokens_used)),
            seconds: self.limits.seconds.map(|s| s.saturating_sub(self.started.elapsed().as_secs())),
        }
    }

    /// The first spend limit that is used up, if any. Checked before each
    /// request and after each step.
    pub fn exhausted(&self) -> Option<ExhaustedLimit> {
        let remaining = self.remaining();
        if remaining.model_calls == Some(0) {
            Some(ExhaustedLimit::ModelCalls)
        } else if remaining.tokens == Some(0) {
            Some(ExhaustedLimit::Tokens)
        } else if self.deadline().is_some_and(|d| Instant::now() >= d) {
            Some(ExhaustedLimit::Seconds)
        } else {
            None
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
        if self.episodes >= self.limits.max_episodes {
            return Err(ExhaustedLimit::Episodes);
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
        let granted = BudgetAmount {
            model_calls: within(request.model_calls, remaining.model_calls, ExhaustedLimit::ModelCalls)?,
            tokens: within(request.tokens, remaining.tokens, ExhaustedLimit::Tokens)?,
            seconds: within(request.seconds, remaining.seconds, ExhaustedLimit::Seconds)?,
        };
        if granted.model_calls == Some(0) {
            return Err(ExhaustedLimit::ModelCalls);
        }
        self.episodes += 1;
        self.active.insert(child_id.to_string(), granted);
        Ok(granted)
    }

    /// Settles a child: its reservation ends and what it reports spent is
    /// debited permanently. The caller records `budget/release`.
    pub fn release(&mut self, child_id: &str, spent: BudgetAmount) {
        self.active.remove(child_id);
        let add = |total: &mut Option<u64>, amount: Option<u64>| {
            if let Some(a) = amount {
                *total = Some(total.unwrap_or(0) + a);
            }
        };
        add(&mut self.children_spent.model_calls, spent.model_calls);
        add(&mut self.children_spent.tokens, spent.tokens);
    }

    /// Children running now.
    pub fn active_children(&self) -> usize {
        self.active.len()
    }
}

#[cfg(test)]
#[path = "budget_test.rs"]
pub(crate) mod tests;
