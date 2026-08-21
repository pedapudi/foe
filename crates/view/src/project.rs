//! Projection of an episode directory tree into the summary the viewer's
//! episode list shows, and the store that keeps that summary and the raw
//! log lines current while the logs are still being written.

use crate::Error;
use foe_log::{EpisodeStart, Event, EventData, ForkOrigin, Outcome, Usage};
use serde::Serialize;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// One episode as the episode list shows it.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Node {
    pub id: String,
    pub parent_id: Option<String>,
    pub fork_origin: Option<ForkOrigin>,
    pub team_id: Option<String>,
    /// `program.name` from `episode/start`, when the program has one.
    pub name: Option<String>,
    /// Set once `episode/end` is in the log.
    pub outcome: Option<Outcome>,
    /// Sum of `usage` over every `assistant/message`.
    pub usage: Usage,
    /// Episodes whose directory is under this one's `children/`.
    pub children: Vec<Node>,
}

/// The episodes under one directory. A directory holds one root log, so
/// `roots` has one element once that log exists and none before.
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Tree {
    pub roots: Vec<Node>,
}

impl Node {
    fn new(start: &EpisodeStart) -> Node {
        Node {
            id: start.id.clone(),
            parent_id: start.parent_id.clone(),
            fork_origin: start.fork_origin.clone(),
            team_id: start.team_id.clone(),
            name: start.program["name"].as_str().map(str::to_string),
            outcome: None,
            usage: Usage::default(),
            children: Vec::new(),
        }
    }

    fn apply(&mut self, event: &Event) {
        match &event.data {
            EventData::EpisodeEnd { outcome } => self.outcome = Some(outcome.clone()),
            EventData::AssistantMessage(m) => {
                self.usage.input += m.usage.input;
                self.usage.output += m.usage.output;
                self.usage.cache_read += m.usage.cache_read;
            }
            _ => {}
        }
    }
}

/// One log being followed: how far it has been read, its lines serialized
/// for the wire, and its summary. `node` is absent until `episode/start`
/// has been read.
#[derive(Default)]
struct Episode {
    offset: u64,
    lines: Vec<Arc<str>>,
    node: Option<Node>,
}

/// Every log under a root directory, keyed by episode directory. Child
/// directories sort after their parent, so iteration is tree order.
pub(crate) struct Store {
    root: PathBuf,
    episodes: BTreeMap<PathBuf, Episode>,
}

impl Store {
    pub(crate) fn new(root: &Path) -> Store {
        Store {
            root: root.to_path_buf(),
            episodes: BTreeMap::new(),
        }
    }

    /// Reads whatever each log has appended since the last call and
    /// discovers new child directories. Returns whether any log grew. When
    /// some log cannot be read, the others are still advanced and the first
    /// error is returned; a tailing caller retries on its next tick.
    pub(crate) fn poll(&mut self) -> Result<bool, Error> {
        let (mut changed, mut first_err) = (false, None);
        let mut pending = vec![self.root.clone()];
        while let Some(dir) = pending.pop() {
            let episode = self.episodes.entry(dir.clone()).or_default();
            match foe_log::fold::read_from(&dir, episode.offset) {
                Ok((events, offset)) => {
                    changed |= !events.is_empty();
                    episode.offset = offset;
                    for event in &events {
                        if let EventData::EpisodeStart(start) = &event.data {
                            episode.node = Some(Node::new(start));
                        }
                        if let Some(node) = &mut episode.node {
                            node.apply(event);
                        }
                        let line = serde_json::to_string(event).expect("event serializes");
                        episode.lines.push(line.into());
                    }
                }
                Err(e) => {
                    first_err.get_or_insert(Error::Log(dir.join("episode.jsonl"), e));
                }
            }
            pending.extend(child_dirs(&dir));
        }
        first_err.map_or(Ok(changed), Err)
    }

    pub(crate) fn tree(&self) -> Tree {
        Tree {
            roots: self.node(&self.root).into_iter().collect(),
        }
    }

    fn node(&self, dir: &Path) -> Option<Node> {
        let mut node = self.episodes.get(dir)?.node.clone()?;
        let children = dir.join("children");
        node.children = self
            .episodes
            .keys()
            .filter(|d| d.parent() == Some(children.as_path()))
            .filter_map(|d| self.node(d))
            .collect();
        Some(node)
    }

    /// Log lines of episode `id` with `seq` greater than `after`. `seq` is
    /// contiguous from 0, so it equals the line's index.
    pub(crate) fn lines(&self, id: &str, after: Option<u64>) -> Vec<Arc<str>> {
        let from = after.map_or(0, |s| s as usize + 1);
        let found = self
            .episodes
            .values()
            .find(|e| e.node.as_ref().is_some_and(|n| n.id == id));
        found
            .map_or(&[][..], |e| e.lines.get(from..).unwrap_or_default())
            .to_vec()
    }

    /// Every log that has a start event, with its lines, in tree order.
    pub(crate) fn logs(&self) -> impl Iterator<Item = (&str, &[Arc<str>])> {
        self.episodes
            .values()
            .filter_map(|e| Some((e.node.as_ref()?.id.as_str(), e.lines.as_slice())))
    }
}

/// Subdirectories of `dir/children`, in no particular order. Empty when
/// there are none.
fn child_dirs(dir: &Path) -> impl Iterator<Item = PathBuf> {
    let entries = std::fs::read_dir(dir.join("children"))
        .into_iter()
        .flatten()
        .flatten();
    entries.map(|e| e.path()).filter(|p| p.is_dir())
}

/// Reads every log under `dir` once and returns the episode tree. Fails
/// when any log under `dir` cannot be read or parsed, naming the file.
pub fn project(dir: &Path) -> Result<Tree, Error> {
    let mut store = Store::new(dir);
    store.poll()?;
    Ok(store.tree())
}
