//! An append-only conversation with lanes for spawned episodes.

use foe_log::{ContentBlock, EventData, InboxSource, Outcome};
use serde_json::Value;
use std::collections::BTreeMap;
use std::future::Future;
use std::io::{self, IsTerminal, Write};
use std::iter::once;
use std::path::{Path, PathBuf};

/// Columns between the last connector cell and the text of a body line.
const GUTTER: &str = "  ";
/// The narrowest text column wrapping produces, however deep the lanes.
const MIN_TEXT_WIDTH: usize = 20;
/// The width assumed when standard output has no window size.
const DEFAULT_WIDTH: usize = 80;
/// The frames of the progress glyph, one per poll tick: the core dot, the
/// spikes growing, the shell closing around them, and the whole mark at the
/// peak. docs/brand/README.md defines the sequence. `◎` has ambiguous East
/// Asian width, so a terminal set to a CJK locale draws it two cells wide and
/// shifts the rest of the line one cell on that frame. The progress line is
/// redrawn whole on every tick and never enters scrollback, so the shift
/// leaves the transcript unchanged.
const FRAMES: [&str; 11] = ["·", "✶", "✷", "✸", "⊛", "◎", "⊛", "✸", "✷", "✶", "·"];
/// Returns to column one and clears to the end of the line.
const ERASE: &str = "\r\x1b[K";
/// The brand accent `#C7791A` as a 24-bit foreground color, for the progress
/// glyph. Which color depths a terminal supports is readable only from an
/// environment variable, and no environment variable is read anywhere, so the
/// accent is always 24-bit and a terminal limited to 256 colors approximates
/// it with the nearest index it holds.
const ACCENT: &str = "\x1b[38;2;199;121;26m";
/// The cyan of a block heading, also for the episode name on the progress line.
const CYAN: &str = "\x1b[1;36m";
/// Green, for the tool-call count on the progress line.
const GREEN: &str = "\x1b[32m";
/// Dim, for the elapsed seconds and every bracket on the progress line.
const DIM: &str = "\x1b[2m";

/// Displays recorded messages while `run` executes, followed by its outcome
/// and, when `viewer` names a served page, that address. Output failures
/// disable the display while execution continues to settle.
pub async fn conversation(
    dir: &Path,
    viewer: Option<String>,
    run: impl Future<Output = Result<Outcome, String>>,
) -> Result<Outcome, String> {
    let width = rustix::termios::tcgetwinsize(io::stdout()).map_or(0, |size| usize::from(size.ws_col));
    let width = if width == 0 { DEFAULT_WIDTH } else { width };
    let interactive = io::stdout().is_terminal();
    let mut terminal = Terminal::new(io::stdout(), interactive, interactive, width);
    tokio::pin!(run);
    let result = loop {
        tokio::select! {
            result = &mut run => break result,
            _ = tokio::time::sleep(std::time::Duration::from_millis(100)) => {
                if let Err(error) = terminal.poll(dir).and_then(|()| terminal.status()) {
                    let _ = terminal.erase();
                    eprintln!("foe conversation: {error}; live display stopped");
                    break run.await;
                }
            }
        }
    };
    if let Err(error) = terminal.poll(dir).and(terminal.finish(&result, viewer.as_deref())) {
        eprintln!("foe conversation: {error}");
    }
    result
}

struct Terminal<W> {
    output: W,
    color: bool,
    /// Whether standard output is a terminal. The progress line is drawn only
    /// then, so redirected output holds appended blocks alone.
    interactive: bool,
    width: usize,
    offsets: BTreeMap<PathBuf, (u64, String)>,
    lanes: Vec<(String, String)>,
    /// When the display started, on the runtime clock.
    started: tokio::time::Instant,
    /// Poll ticks since the display started. The progress frame is this count
    /// modulo the number of frames.
    ticks: usize,
    /// Tool calls the assistant has requested since the last displayed
    /// assistant message.
    calls: usize,
    /// The label of the episode whose event arrived most recently.
    active: String,
    /// Whether the progress line stands on the last line written.
    drawn: bool,
}

/// One line of a body: a section title, bold in color mode, or text.
enum Row {
    Title(String),
    Text(String),
}

impl<W: Write> Terminal<W> {
    /// `color` decides whether escape sequences are emitted and `interactive`
    /// decides whether the progress line is drawn. A run gives both the same
    /// value; a test sets them apart to render one without the other.
    fn new(output: W, color: bool, interactive: bool, width: usize) -> Self {
        let (offsets, lanes, active) = Default::default();
        let started = tokio::time::Instant::now();
        Self { output, color, interactive, width, offsets, lanes, started, ticks: 0, calls: 0, active, drawn: false }
    }

    /// Redraws the progress line in place: the pulsing mark, the episode that
    /// acted most recently, the seconds since the display started, and the
    /// tool calls requested since the last displayed assistant message. The
    /// episode name is shortened to whatever the width leaves, so the redraw
    /// stays on one row and `ERASE` reaches all of it.
    fn status(&mut self) -> io::Result<()> {
        if !self.interactive {
            return Ok(());
        }
        let mark = FRAMES[self.ticks % FRAMES.len()];
        self.ticks += 1;
        let mark = if self.color { format!("{ACCENT}{mark}\x1b[0m") } else { mark.into() };
        let seconds = format!("{} s", tokio::time::Instant::now().duration_since(self.started).as_secs());
        let calls = format!("{} tool call{}", self.calls, if self.calls == 1 { "" } else { "s" });
        let room = self.width.saturating_sub(seconds.chars().count() + calls.chars().count() + 13);
        let lane: String = self.active.chars().take(room).collect();
        let fields = [self.bracket(CYAN, &lane), self.bracket(DIM, &seconds), self.bracket(GREEN, &calls)];
        self.drawn = true;
        write!(self.output, "{ERASE}{mark}  {}", fields.join("  "))?;
        self.output.flush()
    }

    /// One bracketed field of the progress line, its text in `code` and its
    /// brackets dim.
    fn bracket(&self, code: &str, text: &str) -> String {
        match self.color {
            true => format!("{DIM}[\x1b[0m{code}{text}\x1b[0m{DIM}]\x1b[0m"),
            false => format!("[{text}]"),
        }
    }

    /// Removes the progress line, so that what follows owns the scrollback.
    /// The erasure is flushed, so a message written to standard error next
    /// starts on a clear row.
    fn erase(&mut self) -> io::Result<()> {
        match std::mem::take(&mut self.drawn) {
            true => write!(self.output, "{ERASE}").and_then(|()| self.output.flush()),
            false => Ok(()),
        }
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
        if let Some((_, label)) = self.lanes.iter().find(|(key, _)| key == id) {
            self.active = label.clone();
        }
        // A tool call is carried by the assistant message that requests it
        // rather than logged on its own, and the count restarts whenever an
        // assistant message reaches the display.
        if let EventData::AssistantMessage(message) = data {
            if !message.text.trim().is_empty() {
                self.calls = 0;
            }
            self.calls += message.tool_calls.len();
        }
        match data {
            EventData::EpisodeStart(start) => {
                let i = self.lane(id);
                self.lanes[i].1 = start.contract["name"].as_str().unwrap_or(id).into();
                self.active = self.lanes[i].1.clone();
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
                self.block(i, item.from.as_deref().unwrap_or("You"), &[Row::Text(body)])?;
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
        self.erase()?;
        let label = clean(label).replace('\n', " ");
        if self.color {
            writeln!(self.output, "{DIM}{prefix}\x1b[0m{CYAN}{label}\x1b[0m")
        } else {
            writeln!(self.output, "{prefix}{label}")
        }
    }

    fn block(&mut self, lane: usize, label: &str, body: &[Row]) -> io::Result<()> {
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

    /// Writes the rows under the current lanes, then one blank line. Every
    /// emitted line starts with the connector cells and the gutter, so a
    /// wrapped continuation or a blank line never breaks a vertical line.
    fn body(&mut self, rows: &[Row]) -> io::Result<()> {
        let prefix = self.prefix().concat();
        let room = self.width.saturating_sub(prefix.chars().count() + GUTTER.len()).max(MIN_TEXT_WIDTH);
        for row in rows.iter().chain(once(&Row::Text(String::new()))) {
            let (text, title) = match row {
                Row::Title(text) => (text, true),
                Row::Text(text) => (text, false),
            };
            for line in clean(text).trim_end_matches('\n').split('\n').flat_map(|line| wrap(line, room)) {
                let line = if title && self.color { format!("\x1b[1m{line}\x1b[0m") } else { line };
                writeln!(self.output, "{}", format!("{prefix}{GUTTER}{line}").trim_end())?;
            }
        }
        Ok(())
    }

    /// Writes the final block: the outcome, then the viewer address on one
    /// unwrapped line so that it can be copied whole.
    fn finish(&mut self, result: &Result<Outcome, String>, viewer: Option<&str>) -> io::Result<()> {
        let (label, body) = match result {
            Ok(outcome) => result_text(outcome),
            Err(error) => ("Failed", vec![Row::Text(error.clone())]),
        };
        self.heading("● ", &format!("Final · {label}"))?;
        self.lanes.clear();
        self.body(&body)?;
        if let Some(url) = viewer {
            writeln!(self.output, "{GUTTER}Viewer: {url}")?;
        }
        self.output.flush()
    }
}

/// Breaks one line into lines of at most `width` characters at spaces,
/// splitting a word only when the word alone exceeds the width. Each
/// continuation repeats the leading whitespace, plus the width of a list
/// marker (`- `, `* `, `• `, `1. `, or `1) `) so that it aligns with the
/// item's text.
fn wrap(line: &str, width: usize) -> Vec<String> {
    let indent: String = line.chars().take_while(|c| c.is_whitespace()).collect();
    let mut rest = &line[indent.len()..];
    let hang = " ".repeat(indent.chars().count() + marker_width(rest));
    let (mut head, mut out) = (indent, Vec::new());
    loop {
        let room = width.saturating_sub(head.chars().count()).max(1);
        let Some((limit, _)) = rest.char_indices().nth(room) else {
            out.push(head + rest);
            return out;
        };
        let cut = match rest[limit..].starts_with(' ') {
            true => limit,
            false => rest[..limit].rfind(' ').filter(|&cut| cut > 0).unwrap_or(limit),
        };
        out.push(format!("{head}{}", rest[..cut].trim_end()));
        rest = rest[cut..].trim_start_matches(' ');
        head = hang.clone();
    }
}

/// The width of the list marker that opens `text`, or 0 without one.
fn marker_width(text: &str) -> usize {
    let digits = text.chars().take_while(char::is_ascii_digit).count();
    let tail = &text[digits..];
    match digits {
        0 if ["- ", "* ", "• "].iter().any(|marker| tail.starts_with(marker)) => 2,
        1.. if tail.starts_with(". ") || tail.starts_with(") ") => digits + 2,
        _ => 0,
    }
}

fn result_text(outcome: &Outcome) -> (&'static str, Vec<Row>) {
    match outcome {
        Outcome::Completed { value } => ("Completed", display_value(value)),
        Outcome::Blocked { code, message } => ("Blocked", vec![Row::Text(format!("{}: {message}", wire(code)))]),
        Outcome::Exhausted { limit } => ("Exhausted", vec![Row::Text(format!("Budget exhausted: {}", wire(limit)))]),
        Outcome::Failed { error } => ("Failed", vec![Row::Text(error.clone())]),
    }
}

/// Rows for a value. A string holding a JSON object or array is displayed
/// as that object or array. An object opens with its `summary` paragraph
/// and gives every other field a titled section; a field holding an empty
/// string, array, or object is omitted.
fn display_value(value: &Value) -> Vec<Row> {
    if let Value::String(text) = value {
        if let Ok(parsed @ (Value::Object(_) | Value::Array(_))) = serde_json::from_str(text) {
            return display_value(&parsed);
        }
    }
    let Value::Object(fields) = value else {
        return lines("", value, 0).into_iter().map(Row::Text).collect();
    };
    let summary = fields.get("summary").and_then(Value::as_str);
    let mut rows: Vec<Row> =
        summary.filter(|text| !text.is_empty()).map(|text| Row::Text(text.into())).into_iter().collect();
    for (key, value) in fields.iter().filter(|(key, value)| (*key != "summary" || summary.is_none()) && !empty(value)) {
        if !rows.is_empty() {
            rows.push(Row::Text(String::new()));
        }
        rows.push(Row::Title(title(key)));
        rows.extend(lines(key, value, 0).into_iter().map(Row::Text));
    }
    rows
}

/// Lines for `value`, indented by `indent` columns: an array as one bullet
/// per item, an object as `key: value` lines with a nested array or object
/// indented under its key, and a scalar as its text. `key` names the field
/// the value belongs to.
fn lines(key: &str, value: &Value, indent: usize) -> Vec<String> {
    let pad = " ".repeat(indent);
    match value {
        Value::Array(items) => items.iter().flat_map(|item| bullet(key, item, indent)).collect(),
        Value::Object(fields) => fields
            .iter()
            .filter(|(_, value)| !empty(value))
            .flat_map(|(key, value)| match value {
                Value::Array(_) | Value::Object(_) => {
                    once(format!("{pad}{key}:")).chain(lines(key, value, indent + 2)).collect::<Vec<_>>()
                }
                _ => vec![format!("{pad}{key}: {}", scalar(value))],
            })
            .collect(),
        _ => scalar(value).lines().map(|line| format!("{pad}{line}")).collect(),
    }
}

/// One bullet for an item of the array field `key`. An item of `learned`
/// holding `claim` and `seq` is the claim with the log sequence it cites.
/// Another object item puts its scalar fields on the bullet line and nests
/// the rest beneath.
fn bullet(key: &str, item: &Value, indent: usize) -> Vec<String> {
    let pad = " ".repeat(indent);
    let body = match item {
        Value::Object(fields) if key == "learned" && fields.contains_key("claim") && fields.contains_key("seq") => {
            vec![format!("{} (seq {})", scalar(&fields["claim"]), fields["seq"])]
        }
        Value::Object(fields) => {
            let (scalars, nested): (Vec<_>, Vec<_>) = fields
                .iter()
                .filter(|(_, value)| !empty(value))
                .partition(|(_, value)| !matches!(value, Value::Array(_) | Value::Object(_)));
            let head = scalars.iter().map(|(key, value)| format!("{key}: {}", scalar(value))).collect::<Vec<_>>();
            once(head.join(", "))
                .chain(nested.iter().flat_map(|(key, value)| once(format!("{key}:")).chain(lines(key, value, 2))))
                .collect()
        }
        _ => lines(key, item, 0),
    };
    body.iter().enumerate().map(|(i, line)| format!("{pad}{} {line}", if i == 0 { "-" } else { " " })).collect()
}

/// A blocked code or limit as the log spells it.
fn wire(value: &impl serde::Serialize) -> String {
    match serde_json::to_value(value) {
        Ok(Value::String(name)) => name,
        Ok(other) => other.to_string(),
        Err(_) => String::from("unknown"),
    }
}

fn empty(value: &Value) -> bool {
    match value {
        Value::String(text) => text.is_empty(),
        Value::Array(items) => items.is_empty(),
        Value::Object(fields) => fields.is_empty(),
        Value::Null => true,
        _ => false,
    }
}

fn scalar(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

/// The section title for a field: underscores become spaces and the first
/// letter is capitalized.
fn title(key: &str) -> String {
    let spaced = key.replace('_', " ");
    let mut chars = spaced.chars();
    chars.next().map(|first| first.to_uppercase().chain(chars).collect()).unwrap_or_default()
}

fn clean(text: &str) -> String {
    text.chars().filter(|c| !c.is_control() || matches!(c, '\n' | '\t')).collect()
}

#[cfg(test)]
#[path = "terminal_test.rs"]
mod tests;
