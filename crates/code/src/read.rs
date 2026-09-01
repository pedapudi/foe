//! `read`: a numbered window of a text file, consumed as a stream.
//!
//! The file is read through the `Reader`'s descriptor-bound stream in
//! fixed-size buffers. The tool retains that buffer and the kept window,
//! so peak memory is independent of the file's size and of any one line's
//! length, while NUL detection and UTF-8 validation still cover every byte
//! of the file, including bytes after the window and sequences cut by a
//! buffer boundary.

use crate::{display, parse_args, resolve, OUTPUT_MAX_CHARS, OUTPUT_MAX_LINES, READ_BUFFER_BYTES};
use foe_core::{CallCtx, Reader, Tool, ToolValue};
use foe_program::{Effect, ToolSpec};
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};
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
                    "Read a text file with line numbers or list a directory's immediate entries. \
                     Shows at most {OUTPUT_MAX_LINES} lines or {kib} KiB per call; when more remains, \
                     the result names the offset to continue from. File reads return a sha256 version \
                     of the complete bytes. Binary files are reported with their size rather than shown."
                ),
                instruction: Some(
                    "Use read on a directory to inspect its sorted immediate entries. Read a file before \
                     editing it, then pass its version to edit as expected_version. Continue from the \
                     offset in a truncation notice rather than rereading from the start."
                        .into(),
                ),
                params: json!({
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File or directory path, absolute or relative to the first read root."},
                        "offset": {"type": "integer", "minimum": 1, "description": "First line or directory entry to show, 1-indexed. Default 1."},
                        "limit": {"type": "integer", "minimum": 1, "description": format!("Maximum lines or entries to show. Default and cap {OUTPUT_MAX_LINES}.")}
                    },
                    "required": ["path"],
                    "additionalProperties": false
                }),
                effect: Effect::Reads,
            },
        }
    }
}

fn list(reader: &dyn Reader, path: &std::path::Path, shown: &str, offset: usize, limit: usize) -> ToolValue {
    let mut entries = match reader.read_dir(path) {
        Ok(entries) => entries,
        Err(e) => return ToolValue::error(format!("read: {shown}: {e}")),
    };
    entries.sort_by(|left, right| left.path.cmp(&right.path));
    let total = entries.len();
    if offset > total.max(1) {
        return ToolValue::error(format!(
            "read: offset {offset} is past the end of {shown}, which has {total} entries"
        ));
    }
    let mut rendered = String::new();
    let mut items = Vec::new();
    let notice = format!("[Showing entries {offset}-{total} of {total}. Use offset={} to continue.]", total + 1);
    let row_chars = OUTPUT_MAX_CHARS.saturating_sub(notice.chars().count());
    let mut used = 0;
    for (index, entry) in entries.iter().enumerate().skip(offset - 1).take(limit) {
        let kind = if entry.is_file {
            "file"
        } else if entry.is_dir {
            "directory"
        } else {
            "other"
        };
        let name = display(reader.roots(), &entry.path);
        let row = format!("{}\t{kind}\t{name}\n", index + 1);
        let width = row.chars().count();
        if used + width > row_chars {
            break;
        }
        used += width;
        rendered.push_str(&row);
        items.push(json!({"path": entry.path.display().to_string(), "type": kind}));
    }
    let count = items.len();
    let last = offset.saturating_sub(1) + count;
    let truncated = last < total;
    if total == 0 {
        rendered = format!("[{shown} is empty: 0 entries.]");
    } else if truncated {
        let _ = write!(rendered, "[Showing entries {offset}-{last} of {total}. Use offset={} to continue.]", last + 1);
    }
    ToolValue::ok(
        json!({"path": path.display().to_string(), "offset": offset, "total_entries": total,
               "shown": count, "truncated": truncated, "entries": items}),
        rendered,
    )
    .subject(if total == 0 {
        format!("{shown} is empty")
    } else {
        format!("{shown} entries {offset}\u{2013}{last} of {total}")
    })
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
    version: String,
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
    let mut scan = Scan {
        total: 0,
        kept: Vec::new(),
        first_chars: 0,
        nul: false,
        invalid_utf8: false,
        bytes: 0,
        version: String::new(),
    };
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
    let mut digest = Sha256::new();
    loop {
        let n = stream.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        scan.bytes += n as u64;
        let chunk = &buffer[..n];
        digest.update(chunk);
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
    scan.version = format!("sha256:{}", hex::encode(digest.finalize()));
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
        let max_lines = a.limit.unwrap_or(OUTPUT_MAX_LINES).clamp(1, OUTPUT_MAX_LINES);
        let metadata = match reader.metadata(&path) {
            Ok(metadata) => metadata,
            Err(e) => return ToolValue::error(format!("read: {shown}: {e}")),
        };
        if metadata.is_dir() {
            return list(reader.as_ref(), &path, &shown, offset, max_lines);
        }
        let mut stream = match reader.open(&path) {
            Ok(s) => s,
            Err(e) => return ToolValue::error(format!("read: {shown}: {e}")),
        };
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
                "version": &s.version,
            })
        };
        if total == 0 {
            return ToolValue::ok(
                value(0, false, ""),
                format!("[version {}]\n[{shown} is empty: 0 lines.]", s.version),
            )
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
                "[version {}]\n[Line {offset} of {shown} is {} characters, over the {OUTPUT_MAX_CHARS}-character limit \
                 for one read. Use bash: sed -n '{offset}p' '{shown}' | head -c 4000]",
                s.version,
                s.first_chars
            );
            return ToolValue::ok(value(0, true, ""), notice)
                .subject(format!("{shown} line {offset} is too long to show"));
        }
        let last = offset - 1 + kept;
        let mut out = format!("[version {}]\n", s.version);
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
