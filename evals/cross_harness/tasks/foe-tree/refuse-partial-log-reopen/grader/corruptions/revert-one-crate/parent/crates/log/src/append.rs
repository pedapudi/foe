//! Appending events to a log. See docs/log-format.md "Writers".
//!
//! One writer per log, by construction: the process running the episode.
//! Each event is written with one write call, flushed, then echoed to the
//! optional mirror (standard output, for the host protocol).

use crate::fold::{self, LOG_FILE};
use crate::{Event, EventData, LogError, State, LOG_VERSION};
use std::io::Write;
use std::path::Path;

/// Owns the open log file and the sequence counter.
pub struct Writer {
    file: std::fs::File,
    mirror: Option<Box<dyn Write + Send>>,
    next_seq: u64,
    /// Fold of everything written so far, used to validate the next event.
    state: State,
    failure: Option<String>,
}

impl Writer {
    /// Creates `episode.jsonl` under `dir`, which must exist and be empty of
    /// a prior log. Fails if a log is already present.
    pub fn create(dir: &Path, mirror: Option<Box<dyn Write + Send>>) -> Result<Self, LogError> {
        let file = std::fs::OpenOptions::new().create_new(true).append(true).open(dir.join(LOG_FILE))?;
        Ok(Self { file, mirror, next_seq: 0, state: State::default(), failure: None })
    }

    /// Opens an existing log for continued appending, for example after
    /// seeding. Reads the current length to resume the sequence.
    pub fn open(dir: &Path, mirror: Option<Box<dyn Write + Send>>) -> Result<Self, LogError> {
        let events = fold::read_all(dir)?;
        if events.is_empty() {
            return Err(LogError::Empty);
        }
        let state = fold::fold(&events)?;
        let file = std::fs::OpenOptions::new().append(true).open(dir.join(LOG_FILE))?;
        Ok(Self { file, mirror, next_seq: events.len() as u64, state, failure: None })
    }

    /// Appends one event, assigning the next `seq` and the current time.
    /// Returns the event as written. Validates that `data` round-trips as JSON.
    pub fn append(&mut self, data: EventData) -> Result<Event, LogError> {
        self.append_at(data, now_millis())
    }

    /// Appends one event carrying a caller-supplied time. Seeding uses this
    /// to keep the timestamps of copied events.
    pub fn append_at(&mut self, data: EventData, time: i64) -> Result<Event, LogError> {
        self.check()?;
        let event = Event { seq: self.next_seq, time, version: (self.next_seq == 0).then_some(LOG_VERSION), data };
        let mut line = serde_json::to_vec(&event)
            .map_err(|e| LogError::Invalid { seq: event.seq, rule: leak(format!("data serializes: {e}")) })?;
        let back: Event = serde_json::from_slice(&line)
            .map_err(|e| LogError::Invalid { seq: event.seq, rule: leak(format!("data parses: {e}")) })?;
        if back != event {
            return Err(LogError::Invalid { seq: event.seq, rule: "data round-trips byte-for-byte" });
        }
        fold::validate_next(&self.state, &event)?;
        line.push(b'\n');
        let written = (|| {
            self.file.write_all(&line)?;
            self.file.flush()?;
            if let Some(mirror) = &mut self.mirror {
                mirror.write_all(&line)?;
                mirror.flush()?;
            }
            Ok(())
        })();
        self.record_io(written, &event.data.type_name())?;
        fold::apply(&mut self.state, &event);
        self.next_seq += 1;
        Ok(event)
    }

    /// Forces the file to disk. Called at the points the specification names.
    pub fn sync(&mut self) -> Result<(), LogError> {
        self.check()?;
        self.record_io(self.file.sync_all(), "sync")
    }

    fn check(&self) -> Result<(), LogError> {
        self.failure.clone().map_or(Ok(()), |error| Err(LogError::Recording(error)))
    }

    fn record_io(&mut self, result: std::io::Result<()>, operation: &str) -> Result<(), LogError> {
        if let Err(error) = result {
            self.failure = Some(format!("event {} {operation}: recording interrupted: {error}", self.next_seq));
        }
        self.check()
    }

    /// The fold of everything written so far.
    pub fn state(&self) -> &State {
        &self.state
    }

    pub fn next_seq(&self) -> u64 {
        self.next_seq
    }
}

/// Milliseconds since the Unix epoch.
pub fn now_millis() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or(0)
}

/// `LogError::Invalid` carries a static rule; a rule that embeds a parser
/// message is leaked once. Such failures are defects, so they are rare.
fn leak(text: String) -> &'static str {
    Box::leak(text.into_boxed_str())
}

#[cfg(test)]
#[path = "append_test.rs"]
mod tests;
