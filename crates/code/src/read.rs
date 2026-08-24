//! `read`: a numbered window of a text file, consumed as a stream.
//!
//! The file is read through the `Reader`'s descriptor-bound stream in
//! fixed-size buffers. The tool retains that buffer and the kept window,
//! so peak memory is independent of the file's size and of any one line's
//! length, while NUL detection and UTF-8 validation still cover every byte
//! of the file, including bytes after the window and sequences cut by a
//! buffer boundary.

use crate::{display, parse_args, resolve, OUTPUT_MAX_CHARS, OUTPUT_MAX_LINES, READ_BUFFER_BYTES};
use foe_config::{Effect, ToolSpec};
use foe_core::{CallCtx, Tool, ToolValue};
use serde::Deserialize;
use serde_json::json;
use std::fmt::Write;

pub struct Read {
    spec: ToolSpec,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Args {
    path: String,
    offset: Option<usize>,
    limit: Option<usize>,
}

impl Read {
    pub(crate) fn new() -> Self {
        let kib = OUTPUT_MAX_CHARS / 1024;
        Self {
            spec: ToolSpec {
                name: "read".into(),
                description: format!(
                    "Read a text file with line numbers. Shows at most {OUTPUT_MAX_LINES} lines or \
                     {kib} KiB per call, never splitting a line; when more remains, the result ends \
                     with a notice naming the offset to continue from. Binary files are reported \
                     with their size rather than shown."
                ),
                instruction: Some(
                    "Use read to look at a file before editing it. For a long file, continue from the \
                     offset the truncation notice names rather than rereading from the start."
                        .into(),
                ),
                params: json!({
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path, absolute or relative to the first read root."},
                        "offset": {"type": "integer", "minimum": 1, "description": "First line to show, 1-indexed. Default 1."},
                        "limit": {"type": "integer", "minimum": 1, "description": format!("Maximum lines to show. Default and cap {OUTPUT_MAX_LINES}.")}
                    },
                    "required": ["path"],
                    "additionalProperties": false
                }),
                effect: Effect::Reads,
            },
        }
    }
}

/// What one pass over the stream established.
struct Scan {
    /// Lines in the file, counted as `str::lines` counts them.
    total: usize,
    /// The window's lines, carriage returns before line breaks removed.
    kept: Vec<String>,
    /// Characters of the window's first line when nothing fit.
    first_chars: usize,
    /// The file holds a NUL byte somewhere.
    nul: bool,
    /// The file holds bytes that are not UTF-8.
    invalid_utf8: bool,
    /// Bytes in the file.
    bytes: u64,
}

/// The window under construction while the stream is scanned. A line
/// arrives in pieces when it crosses a buffer boundary, so the window keeps
/// per-line state between pieces rather than a whole line.
struct Window {
    /// 0-based index of the window's first line.
    first: usize,
    max_lines: usize,
    max_chars: usize,
    kept: Vec<String>,
    /// Characters the kept lines take, one line break counted per line.
    used: usize,
    cur: String,
    cur_chars: usize,
    /// False once the current line can no longer fit, so its bytes are
    /// dropped while its characters are still counted.
    retaining: bool,
    /// False once the window closed: the first line that does not fit ends
    /// collection, the rule `foe_core::fitting` applies to buffered text.
    collecting: bool,
    /// True while the current line's last character is a carriage return,
    /// which a following line break makes part of a CRLF ending.
    ends_cr: bool,
    /// Characters of the window's first line when nothing fit.
    first_chars: usize,
}

impl Window {
    /// One piece of the line at `line`, without its line break.
    fn piece(&mut self, line: usize, content: &str) {
        if !self.collecting || line < self.first || content.is_empty() {
            return;
        }
        self.cur_chars += content.chars().count();
        self.ends_cr = content.ends_with('\r');
        if self.retaining {
            if self.used + self.cur_chars <= self.max_chars {
                self.cur.push_str(content);
            } else {
                self.retaining = false;
                self.cur = String::new();
            }
        }
    }

    /// The line at `line` ended, at a line break or at the end of the file.
    fn end_line(&mut self, line: usize) {
        if self.collecting && line >= self.first {
            if self.ends_cr {
                self.cur_chars -= 1;
                if self.retaining {
                    self.cur.pop();
                }
            }
            let width = self.cur_chars + 1;
            if self.retaining && self.used + width <= self.max_chars {
                self.used += width;
                self.kept.push(std::mem::take(&mut self.cur));
                if self.kept.len() == self.max_lines {
                    self.collecting = false;
                }
            } else {
                if self.kept.is_empty() {
                    self.first_chars = self.cur_chars;
                }
                self.collecting = false;
            }
        }
        self.cur.clear();
        self.cur_chars = 0;
        self.retaining = true;
        self.ends_cr = false;
    }
}

/// Reads the stream once: counts every line, keeps the window starting at
/// 0-based line `first`, and checks every byte for NUL and UTF-8 validity.
/// A multibyte sequence cut by the buffer boundary is carried into the next
/// buffer before validation, so a boundary can never make a text file
/// binary.
fn scan(stream: &mut dyn std::io::Read, first: usize, max_lines: usize, max_chars: usize) -> std::io::Result<Scan> {
    let mut scan = Scan { total: 0, kept: Vec::new(), first_chars: 0, nul: false, invalid_utf8: false, bytes: 0 };
    let mut window = Window {
        first,
        max_lines,
        max_chars,
        kept: Vec::new(),
        used: 0,
        cur: String::new(),
        cur_chars: 0,
        retaining: true,
        collecting: true,
        ends_cr: false,
        first_chars: 0,
    };
    let mut buffer = vec![0u8; READ_BUFFER_BYTES];
    let mut carry: Vec<u8> = Vec::new();
    let mut joined: Vec<u8> = Vec::new();
    let mut line = 0usize;
    let mut line_open = false;
    loop {
        let n = stream.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        scan.bytes += n as u64;
        let chunk = &buffer[..n];
        if scan.nul {
            continue;
        }
        if chunk.contains(&0) {
            scan.nul = true;
            continue;
        }
        if scan.invalid_utf8 {
            continue;
        }
        let data: &[u8] = if carry.is_empty() {
            chunk
        } else {
            joined.clear();
            joined.extend_from_slice(&carry);
            joined.extend_from_slice(chunk);
            carry.clear();
            &joined
        };
        let (text, tail) = match std::str::from_utf8(data) {
            Ok(text) => (text, &data[data.len()..]),
            // An error with no length is a sequence the buffer cut short;
            // its head waits for the bytes that complete it.
            Err(e) if e.error_len().is_none() => {
                let (valid, rest) = data.split_at(e.valid_up_to());
                (std::str::from_utf8(valid).expect("the prefix up to the error is valid"), rest)
            }
            Err(_) => {
                scan.invalid_utf8 = true;
                continue;
            }
        };
        for segment in text.split_inclusive('\n') {
            match segment.strip_suffix('\n') {
                Some(content) => {
                    window.piece(line, content);
                    window.end_line(line);
                    line += 1;
                    line_open = false;
                }
                None => {
                    line_open = true;
                    window.piece(line, segment);
                }
            }
        }
        carry.extend_from_slice(tail);
    }
    // Bytes still carried at the end of the file complete no sequence.
    if !carry.is_empty() {
        scan.invalid_utf8 = true;
    }
    if line_open {
        window.end_line(line);
        line += 1;
    }
    scan.total = line;
    scan.kept = window.kept;
    scan.first_chars = window.first_chars;
    Ok(scan)
}

#[async_trait::async_trait]
impl Tool for Read {
    fn spec(&self) -> &ToolSpec {
        &self.spec
    }

    async fn call(&self, args: serde_json::Value, ctx: &CallCtx) -> ToolValue {
        let a: Args = match parse_args("read", args) {
            Ok(a) => a,
            Err(e) => return e,
        };
        let Some(reader) = ctx.reader.as_ref() else {
            return ToolValue::error("read: dispatched without a reader handle");
        };
        let path = resolve(reader.roots(), &a.path);
        let shown = display(reader.roots(), &path);
        let offset = a.offset.unwrap_or(1);
        if offset == 0 {
            return ToolValue::error("read: offset must be at least 1");
        }
        let mut stream = match reader.open(&path) {
            Ok(s) => s,
            Err(e) => return ToolValue::error(format!("read: {shown}: {e}")),
        };
        let max_lines = a.limit.unwrap_or(OUTPUT_MAX_LINES).clamp(1, OUTPUT_MAX_LINES);
        let s = match scan(stream.as_mut(), offset - 1, max_lines, OUTPUT_MAX_CHARS) {
            Ok(s) => s,
            Err(e) => return ToolValue::error(format!("read: {shown}: {e}")),
        };
        if s.nul {
            return ToolValue::error(format!(
                "read: {shown} is a binary file ({} bytes, contains a NUL byte)",
                s.bytes
            ));
        }
        if s.invalid_utf8 {
            return ToolValue::error(format!("read: {shown} is a binary file ({} bytes; invalid UTF-8)", s.bytes));
        }
        let total = s.total;
        let value = |shown_n: usize, truncated: bool, content: &str| {
            json!({
                "path": path.display().to_string(),
                "offset": offset,
                "total_lines": total,
                "shown": shown_n,
                "truncated": truncated,
                "content": content,
            })
        };
        if total == 0 {
            return ToolValue::ok(value(0, false, ""), format!("[{shown} is empty: 0 lines.]"))
                .subject(format!("{shown} is empty"));
        }
        if offset > total {
            return ToolValue::error(format!(
                "read: offset {offset} is past the end of {shown}, which has {total} lines"
            ));
        }
        let kept = s.kept.len();
        if kept == 0 {
            let notice = format!(
                "[Line {offset} of {shown} is {} characters, over the {OUTPUT_MAX_CHARS}-character limit \
                 for one read. Use bash: sed -n '{offset}p' '{shown}' | head -c 4000]",
                s.first_chars
            );
            return ToolValue::ok(value(0, true, ""), notice)
                .subject(format!("{shown} line {offset} is too long to show"));
        }
        let last = offset - 1 + kept;
        let mut out = String::new();
        for (i, line) in s.kept.iter().enumerate() {
            let _ = writeln!(out, "{}\t{line}", offset + i);
        }
        let truncated = last < total;
        if truncated {
            let _ = write!(out, "[Showing lines {offset}-{last} of {total}. Use offset={} to continue.]", last + 1);
        }
        ToolValue::ok(value(kept, truncated, &s.kept.join("\n")), out)
            .subject(format!("{shown} lines {offset}\u{2013}{last} of {total}"))
    }
}

#[cfg(test)]
#[path = "read_test.rs"]
mod tests;
