//! Dataflow scheduling over the declared graph. Implements docs/workflow.md
//! "Firing" and "Choice points".
//!
//! Every node keeps a firing count and the set of edges that became fresh
//! since it last fired. An edge from a node without `branches` is fresh
//! after every firing of its source. An edge from a node with `branches` is
//! fresh only when the chosen label lists the target, or when no label
//! lists it. A node is ready when it is not running, it has a fresh edge or
//! a forced re-fire, every data input has a value, and no ancestor is
//! running or ready ahead of it; the last condition makes a node with two
//! inputs wait for both when a re-fire upstream refreshes them in turn. The
//! built-in `task` source holds its value before the first firing and is
//! nobody's ancestor, so a node that follows only `task` fires at the start.

use foe_config::workflow::{ancestors, Node, WorkflowConfig, TASK_SOURCE};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::time::Instant;

/// A value a node produced, with the `seq` of the event that produced it.
#[derive(Debug, Clone, PartialEq)]
pub struct Produced {
    pub value: Value,
    pub rendered: String,
    pub seq: u64,
}

#[derive(Debug, Default)]
pub struct NodeState {
    pub fires: u32,
    pub value: Option<Produced>,
    /// Sources of edges that became fresh since the last firing.
    pub fresh: BTreeSet<String>,
    /// Set by recovery and by verification: fire regardless of freshness.
    pub forced: bool,
    pub running: bool,
    /// When the firing now running began, for the `workflow/node-end` its
    /// executor owes even when it abandons the firing.
    pub started: Option<Instant>,
    /// Attached to the next firing as a `findings` section.
    pub findings: Vec<String>,
    /// Attached to the next firing as a `recovery` section.
    pub note: Option<String>,
    pub verify_attempts: u32,
    /// `seq` of the `verification/result` that accepted this node's latest
    /// value, which a successor's `skip_when_verified` guard reads: in the
    /// workflow's own log for a node-level `verify`, in the child episode's
    /// log for a model node whose program declares `done_when.verify`.
    /// Cleared when the node fires again, because a re-fire invalidates
    /// the accepted value.
    pub accepted: Option<u64>,
    /// The child episode of this node's latest firing, whose log holds a
    /// model node's own verification events.
    pub child_id: Option<String>,
}

pub struct Scheduler {
    pub nodes: BTreeMap<String, Node>,
    /// Data inputs in section order.
    pub inputs: BTreeMap<String, Vec<String>>,
    /// Every edge source, data and branch edges alike.
    pub preds: BTreeMap<String, BTreeSet<String>>,
    pub succs: BTreeMap<String, BTreeSet<String>>,
    pub state: BTreeMap<String, NodeState>,
}

impl Scheduler {
    /// A scheduler with every node that has no predecessor marked to fire
    /// and `task` holding the invocation task.
    pub fn new(wf: &WorkflowConfig, task: Produced) -> Self {
        let preds = wf.predecessors();
        let mut succs: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
        for (target, sources) in &preds {
            for source in sources {
                succs.entry(source.clone()).or_default().insert(target.clone());
            }
        }
        let source =
            |name: &String| NodeState { forced: preds.get(name).is_none_or(BTreeSet::is_empty), ..Default::default() };
        let mut state: BTreeMap<String, NodeState> = wf.nodes.keys().map(|name| (name.clone(), source(name))).collect();
        state.insert(TASK_SOURCE.into(), NodeState { value: Some(task), ..Default::default() });
        Self { nodes: wf.nodes.clone(), inputs: wf.inputs(), preds, succs, state }
    }

    /// The nodes that may fire now, in name order.
    pub fn ready(&self) -> Vec<String> {
        let candidate = |name: &String| {
            let s = &self.state[name];
            let inputs_present = self.inputs[name].iter().all(|i| self.state[i].value.is_some());
            !s.running && (s.forced || (!s.fresh.is_empty() && inputs_present))
        };
        let candidates: BTreeSet<&String> = self.nodes.keys().filter(|n| candidate(n)).collect();
        candidates
            .iter()
            .filter(|name| {
                let blocks = |a: &String| a != **name && (self.state[a].running || candidates.contains(a));
                !ancestors(&self.preds, name).iter().any(blocks)
            })
            .map(|name| (*name).clone())
            .collect()
    }

    /// Starts a firing: counts it, clears freshness, and takes the findings
    /// and note attached for it.
    pub fn begin(&mut self, name: &str) -> (u32, Vec<String>, Option<String>) {
        let s = self.state.get_mut(name).expect("a known node");
        s.fires += 1;
        s.fresh.clear();
        s.forced = false;
        s.running = true;
        s.started = Some(Instant::now());
        s.accepted = None;
        (s.fires, std::mem::take(&mut s.findings), s.note.take())
    }

    /// Clears readiness without counting a firing, for a node whose
    /// `skip_when_verified` guard fired in its place.
    pub fn skip(&mut self, name: &str) {
        let s = self.state.get_mut(name).expect("a known node");
        s.fresh.clear();
        s.forced = false;
    }

    pub fn finish(&mut self, name: &str) {
        self.state.get_mut(name).expect("a known node").running = false;
    }

    pub fn force(&mut self, name: &str) {
        self.state.get_mut(name).expect("a known node").forced = true;
    }

    /// Records a produced value and refreshes the edges the label admits.
    /// Returns the successors refreshed, in name order.
    pub fn produced(&mut self, name: &str, produced: Produced, label: Option<&str>) -> Vec<String> {
        self.state.get_mut(name).expect("a known node").value = Some(produced);
        let node = &self.nodes[name];
        let admitted: Vec<String> = self
            .succs
            .get(name)
            .into_iter()
            .flatten()
            .filter(|target| {
                let listed = node.branches.values().any(|list| list.contains(target));
                let chosen = label.is_some_and(|l| node.branches.get(l).is_some_and(|list| list.contains(target)));
                node.branches.is_empty() || !listed || chosen
            })
            .cloned()
            .collect();
        for target in &admitted {
            self.state.get_mut(target).expect("a known node").fresh.insert(name.to_string());
        }
        admitted
    }

    /// The nearest model node at or above `name`, by breadth-first walk
    /// over the predecessors.
    pub fn nearest_model(&self, name: &str) -> Option<String> {
        let mut queue = std::collections::VecDeque::from([name.to_string()]);
        let mut seen = BTreeSet::new();
        while let Some(current) = queue.pop_front() {
            if !seen.insert(current.clone()) {
                continue;
            }
            if self.nodes[&current].model.is_some() {
                return Some(current);
            }
            queue.extend(self.preds.get(&current).into_iter().flatten().cloned());
        }
        None
    }
}

#[cfg(test)]
#[path = "graph_test.rs"]
mod tests;
