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
use crate::spawn::{ChildRun, ProcessSpawner, Router, Uplink};
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

    fn answers(&self) -> bool {
        true
    }
}

/// Drops forwarded lines, for a process with no host. A descendant's
/// `model/request` then records a call the descendant makes itself, and no
/// process above this one can answer a `host/tool-call`, which the spawner
/// refuses in place rather than forwarding.
pub struct NoHostUplink;

impl Uplink for NoHostUplink {
    fn forward(&self, _line: &str) {}

    fn answers(&self) -> bool {
        false
    }
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
        self.log.append(event)?;
        Ok(())
    }
}

impl Spawner for BudgetedSpawner {
    fn allocate_id(&self) -> String {
        self.inner.allocate_id()
    }

    fn launch(&self, child_id: String, req: SpawnRequest) -> Result<SpawnHandle, CapError> {
        let reserved =
            lock(&self.pool).reserve(&child_id, self.inner.reserve_for(&req)).map_err(|limit| CapError::Budget {
                name: serde_json::to_value(limit)
                    .ok()
                    .and_then(|value| value.as_str().map(str::to_string))
                    .unwrap_or_default(),
                limit,
            })?;
        if let Err(e) = self.record(EventData::BudgetReserve { child_id: child_id.clone(), reserved }) {
            lock(&self.pool).release(&child_id, BudgetAmount::default());
            return Err(e);
        }
        let start = EventData::SpawnStart {
            child_id: child_id.clone(),
            program: req.program.clone(),
            context: req.context,
            call_id: req.call_id.clone(),
        };
        if let Err(e) = self.record(start) {
            lock(&self.pool).release(&child_id, BudgetAmount::default());
            return Err(e);
        }
        let started = SpawnRequest { reserve: reserved, ..req.clone() };
        let handle = match self.inner.launch(child_id.clone(), started) {
            Ok(handle) => handle,
            Err(e) => {
                let end = EventData::SpawnEnd {
                    child_id: child_id.clone(),
                    outcome: foe_log::Outcome::Failed { error: e.to_string() },
                };
                let recorded = self.record(end).and_then(|_| {
                    self.record(EventData::BudgetRelease { child_id: child_id.clone(), spent: BudgetAmount::default() })
                });
                lock(&self.pool).release(&child_id, BudgetAmount::default());
                recorded?;
                return Err(e);
            }
        };
        // The handle this returns settles only after the task below has
        // recorded the child's end and returned its reservation, so no
        // holder of the handle can observe a settled child while the
        // parent's account of it is still open.
        let (settled_tx, settled_run) = ChildRun::pending();
        let returned = SpawnHandle { child_id: handle.child_id.clone(), dir: handle.dir.clone(), run: settled_run };
        let (log, pool, run) = (self.log.clone(), self.pool.clone(), handle.run);
        tokio::spawn(async move {
            let settled = run.settle().await;
            let end = EventData::SpawnEnd { child_id: child_id.clone(), outcome: settled.outcome.clone() };
            let release = EventData::BudgetRelease { child_id: child_id.clone(), spent: settled.spent };
            for event in [end, release] {
                if let Err(e) = log.append(event) {
                    eprintln!("foe: recording a child's end: {e}");
                }
            }
            // The pool is released after the log and before the handle. A
            // waiter watches the pool, so releasing first would let it see
            // a settled child whose `spawn/end` and `budget/release` are
            // not yet in the log, and the teardown would then write a
            // second pair of its own.
            lock(&pool).release(&child_id, settled.spent);
            let _ = settled_tx.send(Some(settled));
        });
        Ok(returned)
    }
}

#[cfg(test)]
#[path = "wiring_test.rs"]
mod tests;
