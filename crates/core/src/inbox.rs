//! Typed inbox items and their consumption by requests.
//!
//! Implements docs/log-format.md (Inbox). Items live in the log, written
//! the moment they arrive; this module tracks which of them a request has
//! consumed and recognizes peer messages delivered twice.

use foe_log::{Event, EventData, InboxItem, InboxSource, State};
use std::collections::BTreeMap;

/// True for a peer message whose `message_id` an item in `events` already
/// carries. The team lead redelivers messages it cannot confirm, so a
/// member sees duplicates and must drop them before appending.
pub fn is_duplicate(events: &[Event], item: &InboxItem) -> bool {
    let Some(id) = item.message_id.as_deref().filter(|_| item.source == InboxSource::Peer) else { return false };
    events.iter().any(|e| matches!(&e.data, EventData::InboxItem(i) if i.message_id.as_deref() == Some(id)))
}

#[derive(Debug, Default)]
pub struct Inbox {
    /// Items by `seq`, with whether a request consumed them.
    items: BTreeMap<u64, (InboxItem, bool)>,
    /// Events before this `seq` have been scanned for items.
    scanned: u64,
}

impl Inbox {
    /// Restores the inbox from a fold of the log.
    pub fn from_state(state: &State) -> Self {
        let scanned = state.inbox.keys().next_back().map_or(0, |s| s + 1);
        Self { items: state.inbox.clone(), scanned }
    }

    /// Notes every `inbox/item` among `events` that arrived since the last
    /// scan. Other writers append items while a request is in flight.
    pub fn absorb(&mut self, events: &[Event]) {
        let scanned = self.scanned;
        for event in events.iter().filter(|e| e.seq >= scanned) {
            if let EventData::InboxItem(item) = &event.data {
                self.items.insert(event.seq, (item.clone(), false));
            }
            self.scanned = event.seq + 1;
        }
    }

    /// `seq` of every item no request has consumed, in arrival order.
    pub fn pending(&self) -> Vec<u64> {
        self.items.iter().filter(|(_, (_, consumed))| !consumed).map(|(seq, _)| *seq).collect()
    }

    /// Marks items as consumed by the request that listed them.
    pub fn consume(&mut self, seqs: &[u64]) {
        for seq in seqs {
            if let Some(entry) = self.items.get_mut(seq) {
                entry.1 = true;
            }
        }
    }

    pub fn get(&self, seq: u64) -> Option<&InboxItem> {
        self.items.get(&seq).map(|(item, _)| item)
    }
}

#[cfg(test)]
#[path = "inbox_test.rs"]
mod tests;
