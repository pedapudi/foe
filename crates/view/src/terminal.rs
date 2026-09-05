//! An append-only conversation with lanes for spawned episodes.

use foe_log::{ContentBlock, EventData, InboxSource, Outcome};
use serde_json::Value;
use std::collections::BTreeMap;
use std::future::Future;
use std::io::{self, IsTerminal, Write};
use std::path::{Path, PathBuf};

/// Displays recorded messages while `run` executes, followed by its outcome.
/// Output failures disable the display while execution continues to settle.
pub async fn conversation(dir: &Path, run: impl Future<Output = Result<Outcome, String>>) -> Result<Outcome, String> {
    let mut terminal = Terminal::new(io::stdout(), io::stdout().is_terminal());
    tokio::pin!(run);
    let result = loop {
        tokio::select! {
            result = &mut run => break result,
            _ = tokio::time::sleep(std::time::Duration::from_millis(100)) => {
                if let Err(error) = terminal.poll(dir) {
                    eprintln!("foe conversation: {error}; live display stopped");
                    break run.await;
                }
            }
        }
    };
    if let Err(error) = terminal.poll(dir).and(terminal.finish(&result)) {
        eprintln!("foe conversation: {error}");
    }
    result
}

struct Terminal<W> {
    output: W,
    color: bool,
    offsets: BTreeMap<PathBuf, (u64, String)>,
    lanes: Vec<(String, String)>,
}

impl<W: Write> Terminal<W> {
    fn new(output: W, color: bool) -> Self {
        Self { output, color, offsets: BTreeMap::new(), lanes: Vec::new() }
    }

    fn poll(&mut self, dir: &Path) -> io::Result<()> {
        if !dir.join("episode.jsonl").is_file() {
            return Ok(());
        }
        let (offset, mut id) = self.offsets.get(dir).cloned().unwrap_or_default();
        let (events, offset) =
            foe_log::fold::read_from(dir, offset).map_err(|e| io::Error::other(format!("{}: {e}", dir.display())))?;
        for event in events {
            // A returned result follows every available message from its child.
            if let EventData::SpawnEnd { child_id, .. } = &event.data {
                self.poll(&dir.join("children").join(child_id))?;
            }
            if let EventData::EpisodeStart(start) = &event.data {
                id = start.id.clone();
            }
            self.event(&id, &event.data)?;
        }
        self.offsets.insert(dir.into(), (offset, id));
        for child in crate::project::episode_dirs(&dir.join("children")) {
            self.poll(&child)?;
        }
        self.output.flush()
    }

    fn lane(&mut self, id: &str) -> usize {
        if let Some(i) = self.lanes.iter().position(|(key, _)| key == id) {
            return i;
        }
        self.lanes.push((id.into(), id.into()));
        self.lanes.len() - 1
    }

    fn event(&mut self, id: &str, data: &EventData) -> io::Result<()> {
        match data {
            EventData::EpisodeStart(start) => {
                let i = self.lane(id);
                self.lanes[i].1 = start.contract["name"].as_str().unwrap_or(id).into();
            }
            EventData::InboxItem(item)
                if matches!(item.source, InboxSource::Parent | InboxSource::Peer)
                    || (item.source == InboxSource::Task && self.lanes.first().is_some_and(|(key, _)| key == id)) =>
            {
                let i = self.lane(id);
                let body = item
                    .content
                    .iter()
                    .map(|b| match b {
                        ContentBlock::Text { text } => text.as_str(),
                        ContentBlock::Image { .. } => "[image]",
                    })
                    .collect::<Vec<_>>()
                    .join("\n\n");
                self.block(i, item.from.as_deref().unwrap_or("You"), &body)?;
            }
            EventData::AssistantMessage(message) if !message.text.trim().is_empty() => {
                let i = self.lane(id);
                let label = if message.interrupted { "Assistant (interrupted)" } else { "Assistant" };
                self.block(i, label, &display_value(&message.text.clone().into()))?;
            }
            EventData::SpawnStart { child_id, contract, .. } => {
                let parent = self.lane(id);
                let child = self.lane(child_id);
                self.lanes[child].1 = contract.clone();
                self.edge(parent, child, "╮ ", &format!("Branch: {contract}"))?;
            }
            EventData::SpawnEnd { child_id, outcome } => {
                let parent = self.lane(id);
                let child = self.lane(child_id);
                let (status, body) = result_text(outcome);
                let label = format!("{} → {} · {status}", self.lanes[child].1, self.lanes[parent].1);
                self.edge(parent, child, "╯ ", &label)?;
                self.lanes[child] = (String::new(), String::new());
                while self.lanes.last().is_some_and(|(id, _)| id.is_empty()) {
                    self.lanes.pop();
                }
                self.body(&body)?;
            }
            _ => {}
        }
        Ok(())
    }

    fn prefix(&self) -> Vec<&str> {
        self.lanes.iter().map(|(id, _)| if id.is_empty() { "  " } else { "│ " }).collect()
    }

    fn heading(&mut self, prefix: &str, label: &str) -> io::Result<()> {
        let label = clean(label).replace('\n', " ");
        if self.color {
            writeln!(self.output, "\x1b[2m{prefix}\x1b[0m\x1b[1;36m{label}\x1b[0m")
        } else {
            writeln!(self.output, "{prefix}{label}")
        }
    }

    fn block(&mut self, lane: usize, label: &str, body: &str) -> io::Result<()> {
        let mut prefix = self.prefix();
        prefix[lane] = "● ";
        self.heading(&prefix.concat(), &format!("{} · {label}", self.lanes[lane].1))?;
        self.body(body)
    }

    fn edge(&mut self, parent: usize, child: usize, end: &str, label: &str) -> io::Result<()> {
        let mut prefix = self.prefix();
        for segment in &mut prefix[parent.min(child)..parent.max(child)] {
            *segment = if *segment == "│ " { "┼─" } else { "──" };
        }
        prefix[parent] = "├─";
        prefix[child] = end;
        self.heading(&prefix.concat(), label)
    }

    fn body(&mut self, body: &str) -> io::Result<()> {
        let prefix = self.prefix().concat();
        for line in clean(body).lines().chain(std::iter::once("")) {
            writeln!(self.output, "{prefix}{line}")?;
        }
        Ok(())
    }

    fn finish(&mut self, result: &Result<Outcome, String>) -> io::Result<()> {
        let (label, body) = match result {
            Ok(outcome) => result_text(outcome),
            Err(error) => ("Failed", error.clone()),
        };
        self.heading("● ", &format!("Final · {label}"))?;
        self.lanes.clear();
        self.body(&body)?;
        self.output.flush()
    }
}

fn result_text(outcome: &Outcome) -> (&'static str, String) {
    match outcome {
        Outcome::Completed { value } => ("Completed", display_value(value)),
        Outcome::Blocked { code, message } => ("Blocked", format!("{code:?}: {message}")),
        Outcome::Exhausted { limit } => ("Exhausted", format!("Budget exhausted: {limit:?}")),
        Outcome::Failed { error } => ("Failed", error.clone()),
    }
}

fn display_value(value: &Value) -> String {
    if let Value::String(text) = value {
        if let Ok(parsed @ (Value::Object(_) | Value::Array(_))) = serde_json::from_str(text) {
            return display_value(&parsed);
        }
    }
    match value {
        Value::String(text) => text.clone(),
        Value::Object(fields) if !fields.is_empty() => fields
            .iter()
            .map(|(key, value)| format!("{key}:\n  {}", display_value(value).replace('\n', "\n  ")))
            .collect::<Vec<_>>()
            .join("\n\n"),
        Value::Array(items) if !items.is_empty() => items.iter().map(display_value).collect::<Vec<_>>().join("\n\n"),
        _ => value.to_string(),
    }
}

fn clean(text: &str) -> String {
    text.chars().filter(|c| !c.is_control() || matches!(c, '\n' | '\t')).collect()
}

#[cfg(test)]
#[path = "terminal_test.rs"]
mod tests;
