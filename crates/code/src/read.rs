//! `read`: a numbered window of a text file.

use crate::{display, parse_args, resolve, OUTPUT_MAX_CHARS, OUTPUT_MAX_LINES};
use foe_core::{fitting, CallCtx, Effect, Tool, ToolSpec, ToolValue};
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
                    "Read a text file with line numbers. offset and limit select a range of lines. \
                     At most {OUTPUT_MAX_LINES} lines or {kib} KiB are collected per call, never \
                     splitting a line; when more remains, the result ends with a notice naming the \
                     offset to continue from. Binary files are reported with their size rather than \
                     shown."
                ),
                instruction: Some(
                    "Use read to look at a file before editing it. Give offset and limit when only \
                     part of a large file matters: the results of one turn share a character \
                     budget. Continue from the offset a truncation notice names rather than \
                     rereading from the start."
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
        let bytes = match reader.read(&path) {
            Ok(b) => b,
            Err(e) => return ToolValue::error(format!("read: {shown}: {e}")),
        };
        if bytes.contains(&0) {
            return ToolValue::error(format!(
                "read: {shown} is a binary file ({} bytes, contains a NUL byte)",
                bytes.len()
            ));
        }
        let Ok(text) = std::str::from_utf8(&bytes) else {
            return ToolValue::error(format!("read: {shown} is a binary file ({} bytes; invalid UTF-8)", bytes.len()));
        };
        let lines: Vec<&str> = text.lines().collect();
        let total = lines.len();
        let offset = a.offset.unwrap_or(1);
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
        if offset == 0 {
            return ToolValue::error("read: offset must be at least 1");
        }
        if total == 0 {
            return ToolValue::ok(value(0, false, ""), format!("[{shown} is empty: 0 lines.]"));
        }
        if offset > total {
            return ToolValue::error(format!(
                "read: offset {offset} is past the end of {shown}, which has {total} lines"
            ));
        }
        let rest = &lines[offset - 1..];
        let max_lines = a.limit.unwrap_or(OUTPUT_MAX_LINES).clamp(1, OUTPUT_MAX_LINES);
        let (kept, _) = fitting(rest.iter(), max_lines, OUTPUT_MAX_CHARS);
        if kept == 0 {
            let notice = format!(
                "[Line {offset} of {shown} is {} characters, over the {OUTPUT_MAX_CHARS}-character limit \
                 for one read. Use bash: sed -n '{offset}p' '{shown}' | head -c 4000]",
                rest[0].chars().count()
            );
            return ToolValue::ok(value(0, true, ""), notice);
        }
        let last = offset - 1 + kept;
        let mut out = String::new();
        for (i, line) in rest[..kept].iter().enumerate() {
            let _ = writeln!(out, "{}\t{line}", offset + i);
        }
        let truncated = last < total;
        if truncated {
            let _ = write!(out, "[Showing lines {offset}-{last} of {total}. Use offset={} to continue.]", last + 1);
        }
        ToolValue::ok(value(kept, truncated, &rest[..kept].join("\n")), out)
    }
}

#[cfg(test)]
#[path = "read_test.rs"]
mod tests;
