//! Appending events to a log. See docs/log-format.md "Writers".
//!
//! One writer per log, by construction: the process running the episode.
//! Each event is written with one write call, flushed, then echoed to the
//! optional mirror (standard output, for the host protocol).

use crate::{Event, EventData, LogError};
use std::io::Write;
use std::path::{Path, PathBuf};

/// Owns the open log file and the sequence counter.
pub struct Writer {
    path: PathBuf,
    file: std::fs::File,
    mirror: Option<Box<dyn Write + Send>>,
    next_seq: u64,
}

impl Writer {
    /// Creates `episode.jsonl` under `dir`, which must exist and be empty of
    /// a prior log. Fails if a log is already present.
    pub fn create(dir: &Path, mirror: Option<Box<dyn Write + Send>>) -> Result<Self, LogError> {
        let _ = (dir, mirror);
        todo!("owner: runtime agent")
    }

    /// Opens an existing log for continued appending, for example after
    /// seeding. Reads the current length to resume the sequence.
    pub fn open(dir: &Path, mirror: Option<Box<dyn Write + Send>>) -> Result<Self, LogError> {
        let _ = (dir, mirror);
        todo!("owner: runtime agent")
    }

    /// Appends one event, assigning the next `seq` and the current time.
    /// Returns the event as written. Validates that `data` round-trips as JSON.
    pub fn append(&mut self, data: EventData) -> Result<Event, LogError> {
        let _ = data;
        todo!("owner: runtime agent")
    }

    /// Forces the file to disk. Called at the points the specification names.
    pub fn sync(&mut self) -> Result<(), LogError> {
        todo!("owner: runtime agent")
    }

    pub fn next_seq(&self) -> u64 {
        self.next_seq
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}
