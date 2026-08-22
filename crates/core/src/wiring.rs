//! Adapters that compose the runtime's parts without glue in the binary.
//!
//! The protocol layer, the spawner, the team coordinator, and the loop each
//! name what they need as a trait. This module implements those traits over
//! the concrete types the sibling modules provide, and wraps the process
//! spawner in the budget accounting and the spawn events that the log owes
//! around every child.

use crate::budget::Pool;
use crate::loop_::{lock, Log};
use crate::protocol::{self, Downlink};
use crate::spawn::{ProcessSpawner, Router, Uplink};
use crate::team::LeadLog;
use crate::{CapError, SpawnHandle, SpawnRequest, Spawner};
use foe_log::{BudgetAmount, Event, EventData};
use std::sync::{Arc, Mutex};

/// Forwards a descendant's tagged lines to the host on standard output, for
/// a process running under `--host`.
pub struct StdoutUplink;

impl Uplink for StdoutUplink {
    fn forward(&self, line: &str) {
        if let Err(e) = protocol::forward_line(line) {
            eprintln!("foe: forwarding to the host: {e}");
        }
    }
}

/// Drops forwarded lines, for a process with no host. A descendant's
/// `model/request` then records a call the descendant makes itself, and
/// nothing above this process could answer a `host/tool-call`.
pub struct NoHostUplink;

impl Uplink for NoHostUplink {
    fn forward(&self, _line: &str) {}
}

impl Downlink for Router {
    fn route(&self, episode_id: &str, line: &str) {
        if let Err(e) = Router::route(self, episode_id, line) {
            eprintln!("foe: routing an answer to {episode_id}: {e}");
        }
    }

    fn cancel_all(&self) {
        Router::cancel_all(self)
    }
}

impl LeadLog for Log {
    fn append(&self, event: EventData) {
        if let Err(e) = Log::append(self, event) {
            eprintln!("foe: appending a team event: {e}");
        }
    }

    fn events(&self) -> Vec<Event> {
        Log::events(self)
    }
}

/// Reserves a child's budget from the pool before the child starts and
/// records `budget/reserve`, `spawn/start`, `spawn/end`, and
/// `budget/release` around its life. A reservation the pool refuses is a
/// tool error naming the limit, which the model receives as a result.
pub struct BudgetedSpawner {
    inner: Arc<ProcessSpawner>,
    log: Arc<Log>,
    pool: Arc<Mutex<Pool>>,
}

impl BudgetedSpawner {
    pub fn new(inner: Arc<ProcessSpawner>, log: Arc<Log>, pool: Arc<Mutex<Pool>>) -> Self {
        Self { inner, log, pool }
    }

    fn record(&self, event: EventData) -> Result<(), CapError> {
        self.log.append(event).map(|_| ()).map_err(|e| CapError::Invalid(format!("log: {e}")))
    }
}

impl Spawner for BudgetedSpawner {
    fn spawn(&self, req: SpawnRequest) -> Result<SpawnHandle, CapError> {
        let child_id = self.inner.child_id();
        let reserved = lock(&self.pool).reserve(&child_id, self.inner.reserve_for(&req)).map_err(|limit| {
            let limit = serde_json::to_value(limit).ok().and_then(|v| v.as_str().map(str::to_string));
            CapError::Invalid(format!("budget: the {} limit leaves no room for a child", limit.unwrap_or_default()))
        })?;
        self.record(EventData::BudgetReserve { child_id: child_id.clone(), reserved })?;
        let started = SpawnRequest { reserve: reserved, ..req.clone() };
        let handle = match self.inner.spawn_as(child_id.clone(), started) {
            Ok(handle) => handle,
            Err(e) => {
                lock(&self.pool).release(&child_id, BudgetAmount::default());
                self.record(EventData::BudgetRelease { child_id, spent: BudgetAmount::default() })?;
                return Err(e);
            }
        };
        self.record(EventData::SpawnStart {
            child_id: child_id.clone(),
            program: req.program,
            context: req.context,
            call_id: req.call_id,
        })?;
        let (log, pool, run) = (self.log.clone(), self.pool.clone(), handle.run.clone());
        tokio::spawn(async move {
            let settled = run.settle().await;
            lock(&pool).release(&child_id, settled.spent);
            let end = EventData::SpawnEnd { child_id: child_id.clone(), outcome: settled.outcome };
            let release = EventData::BudgetRelease { child_id, spent: settled.spent };
            for event in [end, release] {
                if let Err(e) = log.append(event) {
                    eprintln!("foe: recording a child's end: {e}");
                }
            }
        });
        Ok(handle)
    }
}

#[cfg(test)]
#[path = "wiring_test.rs"]
mod tests;
