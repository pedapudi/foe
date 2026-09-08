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
/// Wrapped lines a spawned episode's task may take before a count stands
/// for the rest. A team lead writes a unit of a few sentences, which this
/// shows whole; a workflow node's input can be a whole tool result.
const TASK_LINES: usize = 6;
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
/// The cyan of a block heading.
const CYAN: &str = "\x1b[1;36m";
/// What separates the fields of a heading. The landing page's port of this
/// display reads the value out of this line rather than restating it, so the
/// two cannot drift; site/build/term.py names the line it reads.
const FIELD: &str = " – ";
/// The eight colors that name an episode, one per hue of the identity palette
/// in `view/src/tokens.css` and in the same order, so a name that reads blue
/// in the browser reads blue here. A block heading, a branch line, and the
/// progress line write an episode's name in the color its name hashes to, so
/// two episodes writing into one transcript are told apart by eye. These are
/// the terminal's own palette entries rather than 24-bit values, because the
/// terminal's theme has already tuned them for its background, which no
/// program can read. Yellow is left out: it is where the brand accent sits.
/// docs/design-language.md states what the channel means.
const IDENTITY: [&str; 8] =
    ["\x1b[1;34m", "\x1b[1;32m", "\x1b[1;95m", "\x1b[1;36m", "\x1b[1;92m", "\x1b[1;31m", "\x1b[1;96m", "\x1b[1;35m"];
/// Green, for the tool-call count on the progress line.
const GREEN: &str = "\x1b[32m";
/// Dim, for the elapsed seconds and every bracket on the progress line.
const DIM: &str = "\x1b[2m";

/// Displays recorded messages while `run` executes, followed by its outcome
/// and the `foe view` command that renders the finished episode. Output
/// failures disable the display while execution continues to settle.
pub async fn conversation(dir: &Path, run: impl Future<Output = Result<Outcome, String>>) -> Result<Outcome, String> {
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
    if let Err(error) = terminal.poll(dir).and(terminal.finish(&result, dir)) {
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
    /// Per log directory: how far it has been read, whose episode it is,
    /// and whether its seeded prefix is still being skipped.
    offsets: BTreeMap<PathBuf, Reader>,
    /// One entry per open lane: the episode id, its name, and the index of
    /// the identity color the name holds.
    lanes: Vec<(String, String, usize)>,
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

/// How far one log directory has been read and whose episode it holds.
#[derive(Clone, Default)]
struct Reader {
    offset: u64,
    id: String,
    /// Inside a forked log's copied prefix, which ends at `seed/end`.
    seeding: bool,
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

    /// Redraws the progress line in place: one pulsing mark per open lane,
    /// the episode that acted most recently, the seconds since the display
    /// started, and the tool calls requested since the last displayed
    /// assistant message. The episode name is shortened to whatever the
    /// width leaves, so the redraw stays on one row and `ERASE` reaches all
    /// of it.
    ///
    /// The marks occupy the same cells as the connector prefix of the lines
    /// above, so each one stands at the foot of its own lane's vertical
    /// line. A team runs several episodes at once and every one of them
    /// gets a mark; a lane whose column is held open by a lane to its right
    /// gets the blank the transcript gives it.
    fn status(&mut self) -> io::Result<()> {
        if !self.interactive {
            return Ok(());
        }
        let frame = FRAMES[self.ticks % FRAMES.len()];
        self.ticks += 1;
        let marks = match self.lanes.is_empty() {
            true => self.glyph(frame),
            false => self
                .lanes
                .iter()
                .map(|(id, ..)| if id.is_empty() { "  ".to_string() } else { self.glyph(frame) })
                .collect(),
        };
        let cells = if self.lanes.is_empty() { 2 } else { self.lanes.len() * 2 };
        let seconds = format!("{} s", tokio::time::Instant::now().duration_since(self.started).as_secs());
        let calls = format!("{} tool call{}", self.calls, if self.calls == 1 { "" } else { "s" });
        let spent = seconds.chars().count() + calls.chars().count() + cells + 12;
        let room = self.width.saturating_sub(spent);
        let lane: String = self.active.chars().take(room).collect();
        let held = self.lanes.iter().find(|(_, name, _)| *name == self.active);
        let code = held.map_or(IDENTITY[identity(&self.active)], |(_, _, slot)| IDENTITY[*slot]);
        let fields = [self.bracket(code, &lane), self.bracket(DIM, &seconds), self.bracket(GREEN, &calls)];
        self.drawn = true;
        write!(self.output, "{ERASE}{marks}{GUTTER}{}", fields.join("  "))?;
        self.output.flush()
    }

    /// One cell of the progress line: the pulse frame in the brand accent,
    /// padded to the two columns a connector cell occupies.
    fn glyph(&self, frame: &str) -> String {
        match self.color {
            true => format!("{ACCENT}{frame}\x1b[0m "),
            false => format!("{frame} "),
        }
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
        let mut reader = self.offsets.get(dir).cloned().unwrap_or_default();
        let (events, offset) = foe_log::fold::read_from(dir, reader.offset)
            .map_err(|e| io::Error::other(format!("{}: {e}", dir.display())))?;
        for event in events {
            // Everything between a forked episode's own start and its
            // `seed/end` was copied from the origin's log. It records what
            // that episode did, so displaying it here would draw this
            // episode repeating its origin's work and opening its origin's
            // children. docs/log-format.md "Seeding" fixes the boundary.
            if let EventData::EpisodeStart(start) = &event.data {
                reader.id = start.id.clone();
                reader.seeding = start.fork_origin.is_some();
            } else if reader.seeding {
                reader.seeding = !matches!(&event.data, EventData::SeedEnd {});
                continue;
            }
            // A returned result follows every available message from its child.
            if let EventData::SpawnEnd { child_id, .. } = &event.data {
                self.poll(&dir.join("children").join(child_id))?;
            }
            self.event(&reader.id.clone(), &event.data)?;
        }
        reader.offset = offset;
        self.offsets.insert(dir.into(), reader);
        for child in crate::project::episode_dirs(&dir.join("children")) {
            self.poll(&child)?;
        }
        self.output.flush()
    }

    fn lane(&mut self, id: &str) -> usize {
        if let Some(i) = self.lanes.iter().position(|(key, ..)| key == id) {
            return i;
        }
        self.lanes.push((id.into(), id.into(), 0));
        let last = self.lanes.len() - 1;
        self.rename(last, id.into());
        last
    }

    /// Names lane `i` and gives the name a color: the one it hashes to, or
    /// the next free one when another open lane already holds that. Eight
    /// colors over four names collide more often than not, and two lanes on
    /// screen together in one color is what this channel exists to prevent.
    fn rename(&mut self, i: usize, name: String) {
        let taken: Vec<usize> = self.lanes.iter().enumerate().filter(|(k, _)| *k != i).map(|(_, l)| l.2).collect();
        let first = identity(&name);
        let free = |n: &usize| !taken.contains(n);
        self.lanes[i].2 = (0..IDENTITY.len()).map(|n| (first + n) % IDENTITY.len()).find(free).unwrap_or(first);
        self.lanes[i].1 = name;
    }

    fn event(&mut self, id: &str, data: &EventData) -> io::Result<()> {
        if let Some((_, label, _)) = self.lanes.iter().find(|(key, ..)| key == id) {
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
                self.rename(i, start.contract["name"].as_str().unwrap_or(id).into());
                self.active = self.lanes[i].1.clone();
            }
            EventData::InboxItem(item)
                if matches!(
                    item.source,
                    InboxSource::Parent
                        | InboxSource::Peer
                        | InboxSource::Request
                        | InboxSource::Response
                        | InboxSource::Task
                ) =>
            {
                let i = self.lane(id);
                fn text(block: &ContentBlock) -> &str {
                    match block {
                        ContentBlock::Text { text } => text.as_str(),
                        ContentBlock::Image { .. } => "[image]",
                    }
                }
                let body = item.content.iter().map(text).collect::<Vec<_>>().join("\n\n");
                // The run's own task came from the person running it; a
                // spawned episode's came from the episode that opened it,
                // and is the one thing that tells two children of one
                // contract apart.
                let root = self.lanes.first().is_some_and(|(key, ..)| key == id);
                let spawned = item.source == InboxSource::Task && !root;
                let label = if spawned { "Task" } else { item.from.as_deref().unwrap_or("You") };
                self.block(i, label, &display_task(&body), spawned.then_some(TASK_LINES))?;
            }
            EventData::AssistantMessage(message) if !message.text.trim().is_empty() => {
                let i = self.lane(id);
                let label = if message.interrupted { "Assistant (interrupted)" } else { "Assistant" };
                self.block(i, label, &display_value(&message.text.clone().into()), None)?;
            }
            EventData::SpawnStart { child_id, contract, .. } => {
                let parent = self.lane(id);
                let child = self.lane(child_id);
                self.rename(child, contract.clone());
                let code = IDENTITY[self.lanes[child].2];
                self.edge(parent, child, "╮ ", &[(CYAN, "Branch: ".into()), (code, contract.clone())])?;
            }
            EventData::SpawnEnd { child_id, outcome } => {
                let parent = self.lane(id);
                let child = self.lane(child_id);
                let (status, body) = result_text(outcome);
                // The elbow closing the child's column into its parent's tee
                // already says which parent took the result, and the columns
                // name both ends, so the label names the child alone. It
                // matches the `Branch:` line that opened the same column.
                let name = self.lanes[child].1.clone();
                let code = IDENTITY[self.lanes[child].2];
                self.edge(parent, child, "╯ ", &[(code, name), (CYAN, format!("{FIELD}{status}"))])?;
                self.lanes[child] = (String::new(), String::new(), 0);
                while self.lanes.last().is_some_and(|(id, ..)| id.is_empty()) {
                    self.lanes.pop();
                }
                self.body(&body, None)?;
            }
            _ => {}
        }
        Ok(())
    }

    fn prefix(&self) -> Vec<&str> {
        self.lanes.iter().map(|(id, ..)| if id.is_empty() { "  " } else { "│ " }).collect()
    }

    /// One heading line: the connector prefix in dim, then the fields, each
    /// in the color given for it. Without color the codes are dropped and the
    /// texts are concatenated, so a redirected transcript reads the same.
    fn heading(&mut self, prefix: &str, fields: &[(&'static str, String)]) -> io::Result<()> {
        self.erase()?;
        let sgr = |code: &'static str| if self.color { code } else { "" };
        let field =
            |(c, t): &(&'static str, String)| format!("{}{}{}", sgr(c), clean(t).replace('\n', " "), sgr("\x1b[0m"));
        writeln!(self.output, "{}{prefix}{}{}", sgr(DIM), sgr("\x1b[0m"), fields.iter().map(field).collect::<String>())
    }

    fn block(&mut self, lane: usize, label: &str, body: &[Row], limit: Option<usize>) -> io::Result<()> {
        let mut prefix = self.prefix();
        prefix[lane] = "● ";
        let (name, code) = (self.lanes[lane].1.clone(), IDENTITY[self.lanes[lane].2]);
        self.heading(&prefix.concat(), &[(code, name), (CYAN, format!("{FIELD}{label}"))])?;
        self.body(body, limit)
    }

    fn edge(&mut self, parent: usize, child: usize, end: &str, fields: &[(&'static str, String)]) -> io::Result<()> {
        let mut prefix = self.prefix();
        for segment in &mut prefix[parent.min(child)..parent.max(child)] {
            *segment = if *segment == "│ " { "┼─" } else { "──" };
        }
        prefix[parent] = "├─";
        prefix[child] = end;
        self.heading(&prefix.concat(), fields)
    }

    /// Writes the rows under the current lanes, then one blank line. Every
    /// emitted line starts with the connector cells and the gutter, so a
    /// wrapped continuation or a blank line never breaks a vertical line.
    ///
    /// `limit` bounds the wrapped lines the body may take, and a count
    /// stands for the rest. Only a spawned episode's task is bounded: a
    /// team lead writes a unit of a few sentences, and a workflow node's
    /// input can be a whole tool result.
    fn body(&mut self, rows: &[Row], limit: Option<usize>) -> io::Result<()> {
        let prefix = self.prefix().concat();
        let room = self.width.saturating_sub(prefix.chars().count() + GUTTER.len()).max(MIN_TEXT_WIDTH);
        let mut lines: Vec<(String, bool)> = Vec::new();
        for row in rows {
            let (text, title) = match row {
                Row::Title(text) => (text, true),
                Row::Text(text) => (text, false),
            };
            let cleaned = clean(text);
            let wrapped = cleaned.trim_end_matches('\n').split('\n').flat_map(|line| wrap(line, room));
            lines.extend(wrapped.map(|line| (line, title)));
        }
        // The bound counts lines that carry text: a structured task is
        // mostly headings and blank lines, and a bound that counted those
        // would keep almost none of what a reader came for.
        let mut text = 0;
        let carries = |(line, _): &(String, bool)| !line.trim().is_empty();
        if let Some(cut) = limit.and_then(|max| {
            lines.iter().position(|l| {
                carries(l) && {
                    text += 1;
                    text > max
                }
            })
        }) {
            let over = lines.len() - cut;
            lines.truncate(cut);
            lines.push((format!("… {over} more line{}", if over == 1 { "" } else { "s" }), false));
        }
        for (line, title) in lines.into_iter().chain(once((String::new(), false))) {
            let line = if title && self.color { format!("\x1b[1m{line}\x1b[0m") } else { line };
            writeln!(self.output, "{}", format!("{prefix}{GUTTER}{line}").trim_end())?;
        }
        Ok(())
    }

    /// Writes the final block: the outcome, then the `foe view` command over
    /// the episode directory on one unwrapped line so that it can be copied
    /// whole. The live viewer leaves with the process, so the command is the
    /// reference that outlives the run.
    fn finish(&mut self, result: &Result<Outcome, String>, dir: &Path) -> io::Result<()> {
        let failed = |error: &String| ("Failed", vec![Row::Text(error.clone())]);
        let (label, body) = result.as_ref().map_or_else(failed, result_text);
        self.heading("● ", &[(CYAN, format!("Final{FIELD}{label}"))])?;
        self.lanes.clear();
        self.body(&body, None)?;
        writeln!(self.output, "{GUTTER}Viewer: foe view {}", dir.display())?;
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

/// Rows for a task. A task a person wrote is prose and stays prose. A task
/// a workflow node was given is sections named by a `## heading`, and a
/// section carrying what an earlier node returned holds that value as JSON;
/// it is displayed as the value it is, which puts the answer's own summary
/// first and gives every other field a titled section of its own.
fn display_task(text: &str) -> Vec<Row> {
    fn close(name: Option<&str>, body: &[&str], rows: &mut Vec<Row>) {
        let text = body.join("\n");
        let text = text.trim();
        if text.is_empty() {
            return;
        }
        // A section keeps the heading's own text: these are workflow node
        // names, which a reader matches against the graph. The section
        // holding the run's own task is titled by the block heading above
        // it and needs no title of its own.
        if let Some(name) = name.filter(|name| *name != "task") {
            if !rows.is_empty() {
                rows.push(Row::Text(String::new()));
            }
            rows.push(Row::Title(name.to_string()));
        }
        rows.extend(display_value(&Value::String(text.into())));
    }
    let (mut rows, mut name, mut body) = (Vec::new(), None, Vec::new());
    for line in text.lines() {
        match line.strip_prefix("## ") {
            Some(heading) => {
                close(name, &body, &mut rows);
                (name, body) = (Some(heading), Vec::new());
            }
            None => body.push(line),
        }
    }
    close(name, &body, &mut rows);
    rows
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

/// The color that names `name`: FNV-1a over its UTF-16 code units, reduced to
/// one of `IDENTITY`. The same function is written in `view/src/identity.ts`
/// and in the landing page's scripts, so one name resolves to one color
/// wherever it is written.
fn identity(name: &str) -> usize {
    let step = |hash: u32, unit| (hash ^ u32::from(unit)).wrapping_mul(0x0100_0193);
    name.encode_utf16().fold(0x811c_9dc5_u32, step) as usize % IDENTITY.len()
}

fn clean(text: &str) -> String {
    text.chars().filter(|c| !c.is_control() || matches!(c, '\n' | '\t')).collect()
}

#[cfg(test)]
#[path = "terminal_test.rs"]
mod tests;
